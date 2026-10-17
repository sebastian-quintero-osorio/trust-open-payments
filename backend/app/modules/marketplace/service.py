"""Solicitudes, ofertas y matches (10-marketplace, R1–R9). Nunca llama a op-service."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, exists, false, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import PageParams, next_offset, parse_id
from app.core.errors import AppError
from app.core.events import record_event
from app.core.money import BIGINT_MAX, Money, MoneySchema, format_es, market_money, require_market_asset
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, Loan
from app.modules.marketplace.models import LoanOffer, LoanRequest, OfferStatus, RequestStatus
from app.modules.marketplace.schemas import (
    CreateLoanOfferIn,
    CreateLoanRequestIn,
    CreditSummary,
    LoanOfferOut,
    LoanOfferPage,
    LoanRequestOut,
    LoanRequestPage,
    MatchList,
    MatchOffer,
    MatchOut,
    MatchRequest,
    ProposalOut,
)
from app.modules.scoring.service import CreditProfile, get_credit_profile
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import lender_ref, merchant_ref
from app.policies.matching import Proposal, fits

Scope = Literal["mine", "market"]

FEE_BPS_MAX = 10_000
TERM_MONTHS_MIN, TERM_MONTHS_MAX = 1, 24
MATCHES_MAX = 200

ACTIVE_LOAN_EXISTS = "Ya tienes un crédito en curso. Termínalo antes de pedir otro."
OPEN_REQUEST_EXISTS = "Ya tienes una solicitud abierta. Cancélala si quieres publicar otra."
NOT_ELIGIBLE = "Todavía no cumples los requisitos para pedir un crédito."
EXCEEDS_CUPO = "El monto supera tu cupo disponible."
WALLET_REQUIRED = "Vincula tu wallet de cobro antes de publicar una oferta."
REQUEST_NOT_OPEN = "Esta solicitud ya no está abierta."
OFFER_NOT_OPEN = "Esta oferta ya no está abierta."
FIT_PARAMS = "Envía fit_min, fit_max y pct_bps juntos, o ninguno."


@dataclass(frozen=True)
class AmountRange:
    """Solicitud hipotética (`fit_*`) o virtual (13 R4) para `fits()`."""

    min_amount: int
    max_amount: int
    pct_bps: int


# ── Validaciones (R1, R3) ─────────────────────────────────────────────────────


def _field_error(fields: list[dict[str, str]]) -> AppError:
    return AppError(422, "validation_error", details={"fields": fields})


def _pct_text(bps: int) -> str:
    whole, frac = divmod(bps, 100)
    return f"{whole},{frac:02d}".rstrip("0") + " %" if frac else f"{whole} %"


def _validate_range(min_amount: MoneySchema, max_amount: MoneySchema) -> tuple[int, int]:
    """R1: asset de mercado (422 `asset_mismatch`); que quepa en `bigint`, múltiplos de `AMOUNT_STEP`,
    mínimo ≥ `MIN_LOAN_AMOUNT` y mínimo ≤ máximo (422 `validation_error`)."""
    cfg = get_settings()
    low = require_market_asset(min_amount.to_money()).value
    high = require_market_asset(max_amount.to_money()).value
    step = format_es(market_money(cfg.amount_step))
    errors: list[dict[str, str]] = []
    for field, value in (("min_amount", low), ("max_amount", high)):
        if value > BIGINT_MAX:  # cabe en el patrón del YAML pero no en la columna `bigint`
            errors.append({"field": field, "message": "El monto es demasiado grande."})
    for field, value in (("min_amount", low), ("max_amount", high)):
        if value % cfg.amount_step:
            errors.append({"field": field, "message": f"El monto debe ser múltiplo de {step}."})
    if low < cfg.min_loan_amount:
        minimum = format_es(market_money(cfg.min_loan_amount))
        errors.append({"field": "min_amount", "message": f"El mínimo debe ser de al menos {minimum}."})
    if low > high:
        errors.append({"field": "min_amount", "message": "El mínimo no puede superar el máximo."})
    if errors:
        raise _field_error(errors)
    return low, high


def _validate_pct(pct_bps: int) -> None:
    """R1: `pct_bps` ∈ `PCT_OPTIONS_BPS` (422 `pct_not_allowed`)."""
    options = get_settings().pct_options_bps
    if pct_bps not in options:
        allowed = ", ".join(_pct_text(bps) for bps in options[:-1])
        allowed = f"{allowed} o {_pct_text(options[-1])}" if len(options) > 1 else _pct_text(options[0])
        raise AppError(
            422, "pct_not_allowed", f"Elige uno de los porcentajes permitidos: {allowed}.", {"allowed": options}
        )


# ── Armado de respuestas ──────────────────────────────────────────────────────


def _money(value: int, asset_code: str, asset_scale: int) -> MoneySchema:
    return MoneySchema.of(Money(value, asset_code, asset_scale))


def credit_summary(profile: CreditProfile) -> CreditSummary:
    return CreditSummary(
        score=profile.assessment.score,
        grade=profile.assessment.grade,
        cupo_total=MoneySchema.of(profile.cupo_total),
        cupo_disponible=MoneySchema.of(profile.cupo_disponible),
    )


def request_out(req: LoanRequest, merchant: User | None = None, profile: CreditProfile | None = None) -> LoanRequestOut:
    """`merchant` y `credit` solo en `market` y en el detalle; en `mine`, al crear y al cancelar van en `null`."""
    return LoanRequestOut(
        id=req.id,
        status=req.status,
        pct_bps=req.pct_bps,
        credit_assessment_id=req.credit_assessment_id,
        created_at=req.created_at,
        min_amount=_money(req.min_amount, req.asset_code, req.asset_scale),
        max_amount=_money(req.max_amount, req.asset_code, req.asset_scale),
        merchant=merchant_ref(merchant) if merchant is not None else None,
        credit=credit_summary(profile) if profile is not None else None,
    )


def offer_out(offer: LoanOffer, lender: User, proposal: Proposal | None = None) -> LoanOfferOut:
    return LoanOfferOut(
        id=offer.id,
        status=offer.status,
        lender=lender_ref(lender),
        min_amount=_money(offer.min_amount, offer.asset_code, offer.asset_scale),
        max_amount=_money(offer.max_amount, offer.asset_code, offer.asset_scale),
        pct_bps=offer.pct_bps,
        fee_bps=offer.fee_bps,
        term_months=offer.term_months,
        created_at=offer.created_at,
        proposal=ProposalOut.of(proposal, offer.asset_code, offer.asset_scale) if proposal is not None else None,
    )


class _Profiles:
    """`get_credit_profile` una sola vez por negocio dentro de una respuesta."""

    def __init__(self, db: Session):
        self.db = db
        self._cache: dict[uuid.UUID, CreditProfile] = {}

    def __call__(self, merchant_id: uuid.UUID) -> CreditProfile:
        if merchant_id not in self._cache:
            self._cache[merchant_id] = get_credit_profile(self.db, merchant_id)
        return self._cache[merchant_id]


def can_fit(profile: CreditProfile) -> bool:
    """R8: no elegible o `cupo_disponible < MIN_LOAN_AMOUNT` → ninguna oferta encaja."""
    return profile.assessment.eligible and profile.cupo_disponible.value >= get_settings().min_loan_amount


def _require_scope_role(user: User, scope: Scope, mine_role: UserRole, market_role: UserRole) -> None:
    """R4: el rol tiene que corresponder al scope (si no, 403)."""
    if user.role != (mine_role if scope == "mine" else market_role):
        raise AppError(403, "forbidden")


def _open_request(db: Session, merchant_id: uuid.UUID) -> LoanRequest | None:
    return db.scalar(
        select(LoanRequest).where(LoanRequest.merchant_id == merchant_id, LoanRequest.status == RequestStatus.OPEN)
    )


def _page(query: Select, page: PageParams) -> Select:
    return query.limit(page.limit).offset(page.offset)


def _iso(profile: CreditProfile) -> str | None:
    unlocks_at = profile.assessment.unlocks_at
    return unlocks_at.isoformat().replace("+00:00", "Z") if unlocks_at is not None else None


# ── Solicitudes ───────────────────────────────────────────────────────────────


def create_request(db: Session, merchant: User, body: CreateLoanRequestIn) -> LoanRequestOut:
    """R1, R2 (en ese orden). Guarda el `credit_assessment_id` del perfil usado."""
    cfg = get_settings()
    low, high = _validate_range(body.min_amount, body.max_amount)
    _validate_pct(body.pct_bps)

    active_loan = select(Loan.id).where(Loan.merchant_id == merchant.id, Loan.status.in_(NON_TERMINAL_LOAN_STATUSES))
    if db.scalar(select(exists(active_loan))):
        raise AppError(409, "active_loan_exists", ACTIVE_LOAN_EXISTS)
    if _open_request(db, merchant.id) is not None:
        raise AppError(409, "open_request_exists", OPEN_REQUEST_EXISTS)
    profile = get_credit_profile(db, merchant.id)
    if not profile.assessment.eligible:
        raise AppError(422, "not_eligible", NOT_ELIGIBLE, {"unlocks_at": _iso(profile)})
    if high > profile.cupo_disponible.value:
        raise AppError(422, "exceeds_cupo", EXCEEDS_CUPO, {"cupo_disponible": profile.cupo_disponible.to_api()})

    req = LoanRequest(
        merchant_id=merchant.id,
        min_amount=low,
        max_amount=high,
        asset_code=cfg.market_asset_code,
        asset_scale=cfg.market_asset_scale,
        pct_bps=body.pct_bps,
        status=RequestStatus.OPEN,
        credit_assessment_id=profile.assessment.id,
    )
    db.add(req)
    try:
        db.flush()
    except IntegrityError:  # el índice único parcial: otra solicitud `open` ganó la carrera
        db.rollback()
        raise AppError(409, "open_request_exists", OPEN_REQUEST_EXISTS) from None
    record_event(
        db,
        "loan_request",
        req.id,
        "created",
        {"min_amount": low, "max_amount": high, "pct_bps": req.pct_bps},
        actor_id=merchant.id,
    )
    db.commit()
    return request_out(req)


def list_requests(
    db: Session, user: User, scope: Scope, status: str | None, in_my_range: bool, page: PageParams
) -> LoanRequestPage:
    """R4, R5: `mine` (negocio, cualquier estado) o `market` (prestamista, solo `open`, con `merchant` y `credit`)."""
    _require_scope_role(user, scope, UserRole.MERCHANT, UserRole.LENDER)
    order = (LoanRequest.created_at.desc(), LoanRequest.id.desc())
    if scope == "mine":
        query = select(LoanRequest).where(LoanRequest.merchant_id == user.id)
        if status is not None:
            query = query.where(LoanRequest.status == status)
        rows = db.scalars(_page(query.order_by(*order), page)).all()
        items = [request_out(req) for req in rows]
        return LoanRequestPage(items=items, next_offset=next_offset(page.offset, page.limit, len(items)))

    query = (
        select(LoanRequest, User)
        .join(User, User.id == LoanRequest.merchant_id)
        .where(LoanRequest.status == RequestStatus.OPEN, LoanRequest.merchant_id != user.id)
    )
    if status is not None and status != RequestStatus.OPEN:
        query = query.where(false())  # market solo tiene solicitudes `open`
    if in_my_range and user.lender_range_min is not None and user.lender_range_max is not None:
        query = query.where(
            LoanRequest.min_amount <= user.lender_range_max, LoanRequest.max_amount >= user.lender_range_min
        )
    rows = db.execute(_page(query.order_by(*order), page)).all()
    profiles = _Profiles(db)
    items = [request_out(req, merchant, profiles(merchant.id)) for req, merchant in rows]
    return LoanRequestPage(items=items, next_offset=next_offset(page.offset, page.limit, len(items)))


def get_request(db: Session, user: User, request_id: str) -> LoanRequestOut:
    """Detalle: el negocio dueño, o un prestamista si está `open` (si no, 403). Con `merchant` y `credit`."""
    req = db.get(LoanRequest, parse_id(request_id))
    if req is None:
        raise AppError(404, "not_found")
    is_owner = user.role == UserRole.MERCHANT and req.merchant_id == user.id
    if not is_owner and not (user.role == UserRole.LENDER and req.status == RequestStatus.OPEN):
        raise AppError(403, "forbidden")
    merchant = db.get(User, req.merchant_id)
    return request_out(req, merchant, get_credit_profile(db, req.merchant_id))


def cancel_request(db: Session, merchant: User, request_id: str) -> LoanRequestOut:
    """R6: solo el dueño (403); `open → cancelled` (si no, 409 `invalid_state`), con su evento."""
    rid = parse_id(request_id)
    req = db.scalar(select(LoanRequest).where(LoanRequest.id == rid).with_for_update())
    if req is None:
        db.rollback()
        raise AppError(404, "not_found")
    if req.merchant_id != merchant.id:
        db.rollback()
        raise AppError(403, "forbidden")
    if req.status != RequestStatus.OPEN:
        db.rollback()
        raise AppError(409, "invalid_state", REQUEST_NOT_OPEN, {"status": req.status})
    req.status = RequestStatus.CANCELLED
    record_event(db, "loan_request", req.id, "cancelled", {"from": RequestStatus.OPEN.value}, actor_id=merchant.id)
    db.commit()
    return request_out(req)


# ── Ofertas ───────────────────────────────────────────────────────────────────


def create_offer(db: Session, lender: User, body: CreateLoanOfferIn) -> LoanOfferOut:
    """R1, R3: rangos y %; cargo 0–10000 y plazo 1–24 (default `DEFAULT_TERM_MONTHS`); wallet de cobro."""
    cfg = get_settings()
    low, high = _validate_range(body.min_amount, body.max_amount)
    _validate_pct(body.pct_bps)
    term_months = cfg.default_term_months if body.term_months is None else body.term_months
    errors: list[dict[str, str]] = []
    if not 0 <= body.fee_bps <= FEE_BPS_MAX:
        errors.append({"field": "fee_bps", "message": "El cargo debe estar entre 0 y 10000 puntos básicos."})
    if not TERM_MONTHS_MIN <= term_months <= TERM_MONTHS_MAX:
        errors.append({"field": "term_months", "message": "El plazo debe estar entre 1 y 24 meses."})
    if errors:
        raise _field_error(errors)
    if not lender.payout_wallet_url:
        raise AppError(422, "wallet_required", WALLET_REQUIRED)

    offer = LoanOffer(
        lender_id=lender.id,
        min_amount=low,
        max_amount=high,
        asset_code=cfg.market_asset_code,
        asset_scale=cfg.market_asset_scale,
        pct_bps=body.pct_bps,
        fee_bps=body.fee_bps,
        term_months=term_months,
        status=OfferStatus.OPEN,
    )
    db.add(offer)
    db.flush()
    record_event(
        db,
        "loan_offer",
        offer.id,
        "created",
        {"min_amount": low, "max_amount": high, "pct_bps": offer.pct_bps, "fee_bps": offer.fee_bps,
         "term_months": term_months},
        actor_id=lender.id,
    )
    db.commit()
    return offer_out(offer, lender)


def _fit_request(fit_min: int | None, fit_max: int | None, pct_bps: int | None) -> AmountRange | None:
    """R7: `fit_min`, `fit_max` y `pct_bps` van los tres juntos o ninguno (422 `validation_error`)."""
    given = {"fit_min": fit_min, "fit_max": fit_max, "pct_bps": pct_bps}
    if all(v is None for v in given.values()):
        return None
    if fit_min is None or fit_max is None or pct_bps is None:
        missing = [name for name, value in given.items() if value is None]
        raise _field_error([{"field": name, "message": FIT_PARAMS} for name in missing])
    return AmountRange(fit_min, fit_max, pct_bps)


def market_request(db: Session, merchant_id: uuid.UUID, profile: CreditProfile) -> AmountRange | LoanRequest:
    """R7 sin `fit_*`: la solicitud `open` del negocio o, si no tiene, la virtual de 13 R4."""
    cfg = get_settings()
    req = _open_request(db, merchant_id)
    if req is not None:
        return req
    return AmountRange(cfg.min_loan_amount, profile.cupo_disponible.value, max(cfg.pct_options_bps))


def score_market_offers(
    db: Session, merchant_id: uuid.UUID, profile: CreditProfile, request: AmountRange | LoanRequest
) -> list[tuple[LoanOffer, User, Proposal | None]]:
    """R7/R8: las ofertas `open` del mercado (más recientes primero) con su `proposal` contra `request`.

    `proposal` es `None` si no encaja o si el negocio no puede tomar ninguna (`can_fit`). Una sola consulta; la usan
    `GET /loan-offers?scope=market` y el dashboard del comercio (13 R4), así `count` y `offers_fit_count` coinciden.
    """
    step = get_settings().amount_step
    cupo = profile.cupo_disponible.value
    fit_allowed = can_fit(profile)
    rows = db.execute(
        select(LoanOffer, User)
        .join(User, User.id == LoanOffer.lender_id)
        .where(LoanOffer.status == OfferStatus.OPEN, LoanOffer.lender_id != merchant_id)
        .order_by(LoanOffer.created_at.desc(), LoanOffer.id.desc())
    ).all()
    return [(offer, lender, fits(request, offer, cupo, step) if fit_allowed else None) for offer, lender in rows]


def market_fit_count(db: Session, merchant_id: uuid.UUID, profile: CreditProfile) -> int:
    """13 R4: cuántas ofertas `open` encajan con la solicitud `open` del negocio (o la virtual); 0 si no puede tomar
    ninguna. Es el `count` de `GET /loan-offers?scope=market` sin `fit_*`."""
    if not can_fit(profile):
        return 0
    request = market_request(db, merchant_id, profile)
    return sum(1 for _, _, proposal in score_market_offers(db, merchant_id, profile, request) if proposal is not None)


def list_offers(
    db: Session,
    user: User,
    scope: Scope,
    status: str | None,
    fit_min: int | None,
    fit_max: int | None,
    pct_bps: int | None,
    fits_only: bool,
    page: PageParams,
) -> LoanOfferPage:
    """R4, R7, R8: `mine` (prestamista) o `market` (negocio, solo `open`, con `proposal` y `count`)."""
    _require_scope_role(user, scope, UserRole.LENDER, UserRole.MERCHANT)
    hypothetical = _fit_request(fit_min, fit_max, pct_bps)
    order = (LoanOffer.created_at.desc(), LoanOffer.id.desc())

    if scope == "mine":
        filters = [LoanOffer.lender_id == user.id]
        if status is not None:
            filters.append(LoanOffer.status == status)
        count = db.scalar(select(func.count()).select_from(LoanOffer).where(*filters)) or 0
        rows = db.scalars(_page(select(LoanOffer).where(*filters).order_by(*order), page)).all()
        items = [offer_out(offer, user) for offer in rows]
        return LoanOfferPage(items=items, next_offset=next_offset(page.offset, page.limit, len(items)), count=count)

    profile = get_credit_profile(db, user.id)
    request = hypothetical or market_request(db, user.id, profile)
    market_status = status is None or status == OfferStatus.OPEN  # market solo tiene ofertas `open`
    scored = score_market_offers(db, user.id, profile, request) if market_status else []
    count = sum(1 for _, _, proposal in scored if proposal is not None)  # R8: todas las páginas
    if fits_only:
        scored = [row for row in scored if row[2] is not None]
    window = scored[page.offset : page.offset + page.limit]
    has_more = page.offset + page.limit < len(scored)
    return LoanOfferPage(
        items=[offer_out(offer, lender, proposal) for offer, lender, proposal in window],
        next_offset=page.offset + page.limit if has_more else None,
        count=count,
    )


def get_offer(db: Session, user: User, offer_id: str) -> LoanOfferOut:
    """Detalle: el prestamista dueño, o un negocio si está `open` (si no, 403). `proposal` va en `null`."""
    offer = db.get(LoanOffer, parse_id(offer_id))
    if offer is None:
        raise AppError(404, "not_found")
    is_owner = user.role == UserRole.LENDER and offer.lender_id == user.id
    if not is_owner and not (user.role == UserRole.MERCHANT and offer.status == OfferStatus.OPEN):
        raise AppError(403, "forbidden")
    return offer_out(offer, db.get(User, offer.lender_id))


def cancel_offer(db: Session, lender: User, offer_id: str) -> LoanOfferOut:
    """R6: solo el dueño (403); `open → cancelled` (si no, 409 `invalid_state`), con su evento."""
    oid = parse_id(offer_id)
    offer = db.scalar(select(LoanOffer).where(LoanOffer.id == oid).with_for_update())
    if offer is None:
        db.rollback()
        raise AppError(404, "not_found")
    if offer.lender_id != lender.id:
        db.rollback()
        raise AppError(403, "forbidden")
    if offer.status != OfferStatus.OPEN:
        db.rollback()
        raise AppError(409, "invalid_state", OFFER_NOT_OPEN, {"status": offer.status})
    offer.status = OfferStatus.CANCELLED
    record_event(db, "loan_offer", offer.id, "cancelled", {"from": OfferStatus.OPEN.value}, actor_id=lender.id)
    db.commit()
    return offer_out(offer, lender)


# ── Matches (R9) ──────────────────────────────────────────────────────────────


def _match(req: LoanRequest, merchant: User, profile: CreditProfile, offer: LoanOffer, lender: User,
           proposal: Proposal) -> MatchOut:
    return MatchOut(
        request=MatchRequest(
            id=req.id,
            merchant=merchant_ref(merchant),
            min_amount=_money(req.min_amount, req.asset_code, req.asset_scale),
            max_amount=_money(req.max_amount, req.asset_code, req.asset_scale),
            pct_bps=req.pct_bps,
            cupo_total=MoneySchema.of(profile.cupo_total),
        ),
        offer=MatchOffer(
            id=offer.id,
            lender=lender_ref(lender),
            min_amount=_money(offer.min_amount, offer.asset_code, offer.asset_scale),
            max_amount=_money(offer.max_amount, offer.asset_code, offer.asset_scale),
            pct_bps=offer.pct_bps,
            fee_bps=offer.fee_bps,
            term_months=offer.term_months,
        ),
        proposal=ProposalOut.of(proposal, offer.asset_code, offer.asset_scale),
        received_at=offer.created_at,
    )


def _pairs(
    requests: Iterable[tuple[LoanRequest, User]], offers: list[tuple[LoanOffer, User]], profiles: _Profiles
) -> list[tuple[LoanRequest, User, CreditProfile, LoanOffer, User, Proposal]]:
    step = get_settings().amount_step
    found = []
    for req, merchant in requests:
        profile = profiles(merchant.id)
        if not can_fit(profile):
            continue
        for offer, lender in offers:
            proposal = fits(req, offer, profile.cupo_disponible.value, step)
            if proposal is not None:
                found.append((req, merchant, profile, offer, lender, proposal))
    return found


def list_matches(db: Session, user: User) -> MatchList:
    """R9: pares solicitud `open` × oferta `open` con `fits()` no nulo, por `received_at` desc (máx. 200)."""
    open_offers = (
        select(LoanOffer, User).join(User, User.id == LoanOffer.lender_id).where(LoanOffer.status == OfferStatus.OPEN)
    )
    open_requests = (
        select(LoanRequest, User)
        .join(User, User.id == LoanRequest.merchant_id)
        .where(LoanRequest.status == RequestStatus.OPEN)
    )
    if user.role == UserRole.MERCHANT:
        open_requests = open_requests.where(LoanRequest.merchant_id == user.id)
    else:
        open_offers = open_offers.where(LoanOffer.lender_id == user.id)

    offers = [(offer, lender) for offer, lender in db.execute(open_offers).all()]
    requests = [(req, merchant) for req, merchant in db.execute(open_requests).all()] if offers else []
    found = _pairs(requests, offers, _Profiles(db))
    found.sort(key=lambda m: (m[3].created_at, m[3].id.hex, m[0].id.hex), reverse=True)
    return MatchList(items=[_match(*m) for m in found[:MATCHES_MAX]])
