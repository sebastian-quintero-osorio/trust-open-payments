"""Créditos (11-loans): preview y creación (R1–R6), aceptar, rechazar y cancelar (R7, R8), desembolso (R9, R10),
detalle y listas (R13, R14) y las funciones que llama el worker: `on_disbursement_update` (R11), `expire_pending`
(R12) y `close_period` (R15).

Nunca se llama a op-service con una transacción abierta: el desembolso hace commit antes de `create_payment` y
aplica el resultado en una transacción nueva. Bloqueos en el orden de 00-core: cuenta de recaudo → crédito →
solicitud → oferta. Las tres funciones del worker escriben en la transacción de quien llama (no hacen commit).
"""

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import BigInteger, Select, cast, exists, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.deps import PageParams, next_offset, parse_id
from app.core.errors import AppError
from app.core.events import record_event
from app.core.money import BIGINT_MAX, Money, MoneySchema, format_es, market_money, require_market_asset
from app.integrations.op.client import OpClient, OpError, OpUnavailable
from app.integrations.op.schemas import Amount, CreatePaymentRequest, Payment
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, Loan, LoanOrigin, LoanPctChange, LoanStatus
from app.modules.loans.schemas import (
    DisbursementOut,
    DisburseOut,
    LoanDraftIn,
    LoanOut,
    LoanPctHistory,
    LoanPreviewOut,
    LoanSummaryOut,
    LoanSummaryPage,
    NextActionOut,
    PeriodOut,
    SplitExampleOut,
    TermsOut,
    pct_change_out,
)
from app.modules.marketplace.models import LoanOffer, LoanRequest, OfferStatus, RequestStatus
from app.modules.notifications.catalog import render
from app.modules.notifications.service import notify
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus
from app.modules.sales.models import Repayment
from app.modules.scoring.service import CreditProfile, cupo_usado, get_credit_profile
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import lender_ref, merchant_ref, public_name
from app.policies.matching import fits
from app.policies.repricing import review_period
from app.policies.split import SplitLoanState, split_sale
from app.policies.terms import Terms, build_terms

logger = logging.getLogger(__name__)

FEE_BPS_MAX = 10_000
TERM_MONTHS_MIN, TERM_MONTHS_MAX = 1, 24
EXAMPLE_SALE_UNITS = 48_000  # R5: venta de ejemplo de $48.000 (en unidades del asset; × 10^escala)
PERIOD_LENGTH = timedelta(days=30)  # R11, R15
DISBURSE_MIN_REMAINING = timedelta(minutes=1)  # R9: menos de 1 min para vencer → lo resuelve el worker
DESCRIPTION_MAX = 200  # CreatePaymentRequest.description (op-service)
PENDING_STATUSES = (LoanStatus.PENDING_ACCEPTANCE, LoanStatus.PENDING_DISBURSEMENT)
FAILED_PAYMENT_STATUSES = ("failed", "rejected", "expired")

MISSING_OFFER = "Elige la oferta que quieres tomar (falta offer_id)."
MISSING_REQUEST = "Elige la solicitud que quieres fondear (falta request_id)."
REQUEST_NOT_FOUND = "No encontramos esa solicitud."
OFFER_NOT_FOUND = "No encontramos esa oferta."
LOAN_NOT_FOUND = "No encontramos ese crédito."
NOT_YOUR_REQUEST = "Esa solicitud no es tuya."
NOT_YOUR_OFFER = "Esa oferta no es tuya."
NOT_YOUR_LOAN = "Este crédito no es tuyo."
REQUEST_NOT_OPEN = "Esta solicitud ya no está abierta."
OFFER_NOT_OPEN = "Esta oferta ya no está abierta."
AMOUNT_REQUIRED = "Indica el monto del crédito."
NO_OVERLAP = "El monto no está dentro del rango acordado."
LOAN_STATE = "El crédito no está en un estado que permita esta acción."
LOAN_EXPIRED = "El crédito ya venció."
DISBURSE_IN_PROGRESS = "El desembolso ya está en proceso. Espera unos segundos y vuelve a consultar el crédito."
DISBURSE_EXPIRING = "El crédito está por vencer y ya no se puede desembolsar."
DISBURSE_OTHER_CLIENT = "Ya iniciaste este desembolso desde otro dispositivo. Termínalo allí."
DISBURSE_REJECTED = "El servicio de pagos rechazó el desembolso. Revisa tu wallet e inténtalo de nuevo."


# ── Utilidades ────────────────────────────────────────────────────────────────


def _money(value: int, asset_code: str, asset_scale: int) -> MoneySchema:
    return MoneySchema.of(Money(value, asset_code, asset_scale))


def _loan_money(loan: Loan, value: int) -> Money:
    return Money(value, loan.asset_code, loan.asset_scale)


def _fetch[M](db: Session, query: Select[tuple[M]], lock: bool) -> M | None:
    """Una fila; con `lock`, `SELECT … FOR UPDATE` que refresca el objeto del mapa de identidad."""
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def _locked_loan(db: Session, loan_id: uuid.UUID) -> Loan | None:
    return _fetch(db, select(Loan).where(Loan.id == loan_id), lock=True)


def _field_error(fields: list[dict[str, str]]) -> AppError:
    return AppError(422, "validation_error", details={"fields": fields})


def _invalid_state(message: str, loan: Loan | None = None) -> AppError:
    return AppError(409, "invalid_state", message, {"status": loan.status} if loan is not None else {})


def _abonos(db: Session, loan_id: uuid.UUID, start: datetime | None, end: datetime | None = None) -> int:
    """Σ `repayments.abono_amount` del crédito con `created_at` en [start, end) (sin límite si es `None`)."""
    query = select(cast(func.coalesce(func.sum(Repayment.abono_amount), 0), BigInteger)).where(
        Repayment.loan_id == loan_id
    )
    if start is not None:
        query = query.where(Repayment.created_at >= start)
    if end is not None:
        query = query.where(Repayment.created_at < end)
    return db.scalar(query) or 0


def _active_loan_exists(db: Session, merchant_id: uuid.UUID) -> bool:
    active = select(Loan.id).where(Loan.merchant_id == merchant_id, Loan.status.in_(NON_TERMINAL_LOAN_STATUSES))
    return bool(db.scalar(select(exists(active))))


def _users(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, User]:
    wanted = set(ids)
    if not wanted:
        return {}
    return {user.id: user for user in db.scalars(select(User).where(User.id.in_(wanted)))}


def _render_notify(db: Session, user_id: uuid.UUID, type: str, audience: str, data: dict[str, Any], **vars: Any):
    notify(db, user_id, type, *render(type, audience, **vars), data)


# ── Armado de respuestas (R5, R13) ────────────────────────────────────────────


def terms_out(source: Terms | Loan, asset_code: str, asset_scale: int) -> TermsOut:
    """`Terms` en Money: sirve para un `Terms` de 05 o para las columnas de términos de un crédito (mismos nombres)."""

    def money(value: int) -> MoneySchema:
        return _money(value, asset_code, asset_scale)

    return TermsOut(
        principal=money(source.principal),
        fee_bps=source.fee_bps,
        fee_amount=money(source.fee_amount),
        total_repayable=money(source.total_repayable),
        trust_share_bps=source.trust_share_bps,
        term_months=source.term_months,
        monthly_minimum=money(source.monthly_minimum),
        pct_initial_bps=source.pct_initial_bps,
        pct_min_bps=source.pct_min_bps,
        pct_max_bps=source.pct_max_bps,
    )


def build_preview(
    origin: str,
    request_id: uuid.UUID | None,
    offer_id: uuid.UUID | None,
    merchant: User,
    lender: User,
    terms: Terms,
    asset_code: str,
    asset_scale: int,
) -> LoanPreviewOut:
    """R5: `Preview` = `Terms` en Money + división de una venta de ejemplo de $48.000. Su `model_dump(mode="json")`
    es el snapshot de `loans.terms` (R6; también lo usa el seed, 03 R9/R10)."""
    cfg = get_settings()
    sale = EXAMPLE_SALE_UNITS * 10**asset_scale
    state = SplitLoanState(terms.pct_initial_bps, terms.total_repayable, 0, terms.fee_amount, terms.trust_share_bps)
    split = split_sale(sale, state, cfg.trust_cut_policy)

    def money(value: int) -> MoneySchema:
        return _money(value, asset_code, asset_scale)

    return LoanPreviewOut(
        origin=origin,
        request_id=request_id,
        offer_id=offer_id,
        merchant=merchant_ref(merchant),
        lender=lender_ref(lender),
        terms=terms_out(terms, asset_code, asset_scale),
        example=SplitExampleOut(
            sale=money(sale),
            merchant_amount=money(split.merchant_amount),
            abono=money(split.abono),
            trust_cut=money(split.trust_cut),
            lender_net=money(split.lender_net),
        ),
        expires_in_hours=cfg.pending_loan_ttl_hours,
    )


def next_action(loan: Loan) -> NextActionOut:
    """R13."""
    if loan.status == LoanStatus.PENDING_ACCEPTANCE:
        return NextActionOut(type="accept", actor="merchant")
    if loan.status == LoanStatus.PENDING_DISBURSEMENT:
        return NextActionOut(type="disburse", actor="lender")
    if loan.status == LoanStatus.DISBURSING and loan.disbursement_authorization_url:
        return NextActionOut(type="authorize_in_wallet", actor="lender")
    return NextActionOut(type="none", actor=None)


def pct_paid_bps(loan: Loan) -> int:
    """R13: `allocated_total × 10000 // total_repayable` (también lo usa 13-dashboard)."""
    return loan.allocated_total * 10_000 // loan.total_repayable if loan.total_repayable > 0 else 0


def _period(db: Session, loan: Loan) -> PeriodOut | None:
    """R13: solo en `active`. `target = min(monthly_minimum, total_repayable − (allocated_total − en el periodo))`."""
    if loan.status != LoanStatus.ACTIVE or loan.period_start is None or loan.next_review_at is None:
        return None
    in_period = _abonos(db, loan.id, loan.period_start)
    target = max(0, min(loan.monthly_minimum, loan.total_repayable - (loan.allocated_total - in_period)))
    return PeriodOut(
        start=loan.period_start,
        next_review_at=loan.next_review_at,
        allocated_in_period=MoneySchema.of(_loan_money(loan, in_period)),
        target=MoneySchema.of(_loan_money(loan, target)),
    )


def _disbursement(loan: Loan, viewer: User) -> DisbursementOut:
    if loan.status == LoanStatus.DISBURSING:
        status = "in_progress"
    elif loan.status in (LoanStatus.ACTIVE, LoanStatus.PAID):
        status = "completed"
    else:
        status = "not_started"
    is_lender = viewer.id == loan.lender_id  # R13: la URL de autorización solo la ve el prestamista
    return DisbursementOut(
        status=status,
        authorization_url=loan.disbursement_authorization_url if is_lender else None,
        authorization_expires_at=loan.disbursement_authorization_expires_at if is_lender else None,
        attempt=loan.disbursement_attempt,
    )


def loan_out(
    db: Session, loan: Loan, viewer: User, merchant: User | None = None, lender: User | None = None
) -> LoanOut:
    merchant = merchant or db.get(User, loan.merchant_id)
    lender = lender or db.get(User, loan.lender_id)
    return LoanOut(
        id=loan.id,
        status=loan.status,
        origin=loan.origin,
        merchant=merchant_ref(merchant),
        lender=lender_ref(lender),
        request_id=loan.request_id,
        offer_id=loan.offer_id,
        terms=terms_out(loan, loan.asset_code, loan.asset_scale),
        pct_bps=loan.pct_bps,
        allocated=MoneySchema.of(_loan_money(loan, loan.allocated_total)),
        delivered=MoneySchema.of(_loan_money(loan, loan.delivered_total)),
        outstanding=MoneySchema.of(_loan_money(loan, loan.total_repayable - loan.allocated_total)),
        pct_paid_bps=pct_paid_bps(loan),
        period=_period(db, loan),
        is_late=loan.is_late,
        missed_periods=loan.missed_periods,
        next_action=next_action(loan),
        disbursement=_disbursement(loan, viewer),
        expires_at=loan.expires_at if loan.status in PENDING_STATUSES else None,
        created_at=loan.created_at,
        accepted_at=loan.accepted_at,
        activated_at=loan.activated_at,
        paid_at=loan.paid_at,
    )


def loan_summary(loan: Loan, merchant: User, lender: User) -> LoanSummaryOut:
    return LoanSummaryOut(
        id=loan.id,
        status=loan.status,
        origin=loan.origin,
        merchant=merchant_ref(merchant),
        lender=lender_ref(lender),
        principal=MoneySchema.of(_loan_money(loan, loan.principal)),
        pct_bps=loan.pct_bps,
        allocated=MoneySchema.of(_loan_money(loan, loan.allocated_total)),
        pct_paid_bps=pct_paid_bps(loan),
        is_late=loan.is_late,
        next_action=next_action(loan),
        created_at=loan.created_at,
        activated_at=loan.activated_at,
    )


# ── Preview y creación (R1–R6) ────────────────────────────────────────────────


@dataclass
class _Rows:
    account: CollectionAccount | None
    request: LoanRequest | None
    offer: LoanOffer | None


@dataclass(frozen=True)
class _Plan:
    origin: LoanOrigin
    merchant: User
    lender: User
    request: LoanRequest | None
    offer: LoanOffer | None
    account: CollectionAccount
    terms: Terms
    preview: LoanPreviewOut


def _check_form(user: User, draft: LoanDraftIn) -> None:
    """R1/R4, paso 1: 400 si falta el id obligatorio del rol."""
    if user.role == UserRole.MERCHANT and draft.offer_id is None:
        raise AppError(400, "bad_request", MISSING_OFFER, {"field": "offer_id"})
    if user.role == UserRole.LENDER and draft.request_id is None:
        raise AppError(400, "bad_request", MISSING_REQUEST, {"field": "request_id"})


def _merchant_id(db: Session, user: User, draft: LoanDraftIn) -> uuid.UUID:
    """El negocio del crédito: quien crea (merchant) o el dueño de la solicitud (lender; 404 si no existe)."""
    if user.role == UserRole.MERCHANT:
        return user.id
    request = db.get(LoanRequest, draft.request_id)
    if request is None:
        raise AppError(404, "not_found", REQUEST_NOT_FOUND)
    return request.merchant_id


def _load(db: Session, user: User, draft: LoanDraftIn, merchant_id: uuid.UUID, lock: bool) -> _Rows:
    """Lee las filas del borrador; con `lock`, las bloquea en el orden de 00-core: cuenta → solicitud → oferta."""
    account = _fetch(
        db,
        select(CollectionAccount).where(
            CollectionAccount.merchant_id == merchant_id, CollectionAccount.status == CollectionAccountStatus.ACTIVE
        ),
        lock,
    )
    request = None
    if draft.request_id is not None:
        request = _fetch(db, select(LoanRequest).where(LoanRequest.id == draft.request_id), lock)
    elif user.role == UserRole.MERCHANT:  # R1: sin request_id, su solicitud `open` (si tiene)
        request = _fetch(
            db,
            select(LoanRequest).where(LoanRequest.merchant_id == user.id, LoanRequest.status == RequestStatus.OPEN),
            lock,
        )
    offer = None
    if draft.offer_id is not None:
        offer = _fetch(db, select(LoanOffer).where(LoanOffer.id == draft.offer_id), lock)
    return _Rows(account, request, offer)


def _funding_terms(draft: LoanDraftIn) -> tuple[int, int]:
    """R1 (`request_funded`): `fee_bps` obligatorio 0–10000; `term_months` 1–24 (default `DEFAULT_TERM_MONTHS`)."""
    term_months = get_settings().default_term_months if draft.term_months is None else draft.term_months
    errors: list[dict[str, str]] = []
    if draft.fee_bps is None:
        errors.append({"field": "fee_bps", "message": "Indica el cargo del crédito (0 a 10000 puntos básicos)."})
    elif not 0 <= draft.fee_bps <= FEE_BPS_MAX:
        errors.append({"field": "fee_bps", "message": "El cargo debe estar entre 0 y 10000 puntos básicos."})
    if not TERM_MONTHS_MIN <= term_months <= TERM_MONTHS_MAX:
        errors.append({"field": "term_months", "message": "El plazo debe estar entre 1 y 24 meses."})
    if errors:
        raise _field_error(errors)
    return draft.fee_bps, term_months


def _exceeds_cupo(cupo: int) -> AppError:
    return AppError(
        422, "exceeds_cupo", "El monto supera el cupo disponible.", {"cupo_disponible": market_money(cupo).to_api()}
    )


def _no_fit(request: LoanRequest, offer: LoanOffer, cupo: int) -> AppError:
    """R2: `fits()` dio `None` → `exceeds_cupo` si el mínimo común pasa el cupo; si no, `no_overlap`."""
    if max(request.min_amount, offer.min_amount) > cupo:
        return _exceeds_cupo(cupo)
    return AppError(422, "no_overlap", "La solicitud y la oferta no encajan.")


def _amount(draft: LoanDraftIn, request: LoanRequest | None, offer: LoanOffer | None, cupo: int) -> int:
    """R2: monto del crédito, de `fits()` o del borrador."""
    cfg = get_settings()
    proposal = fits(request, offer, cupo, cfg.amount_step) if request is not None and offer is not None else None
    if draft.amount is None:
        if request is None or offer is None:
            raise _field_error([{"field": "amount", "message": AMOUNT_REQUIRED}])
        if proposal is None:
            raise _no_fit(request, offer, cupo)
        return proposal.amount

    amount = require_market_asset(draft.amount.to_money()).value
    step = format_es(market_money(cfg.amount_step))
    if amount > BIGINT_MAX:
        raise _field_error([{"field": "amount", "message": "El monto es demasiado grande."}])
    if amount % cfg.amount_step:
        raise _field_error([{"field": "amount", "message": f"El monto debe ser múltiplo de {step}."}])
    if amount < cfg.min_loan_amount:
        minimum = format_es(market_money(cfg.min_loan_amount))
        raise _field_error([{"field": "amount", "message": f"El monto debe ser de al menos {minimum}."}])
    if request is not None and offer is not None and proposal is None:
        raise _no_fit(request, offer, cupo)
    for side in (request, offer):
        if side is not None and not side.min_amount <= amount <= side.max_amount:
            raise AppError(
                422,
                "no_overlap",
                NO_OVERLAP,
                {"min": market_money(side.min_amount).to_api(), "max": market_money(side.max_amount).to_api()},
            )
    if amount > cupo:
        raise _exceeds_cupo(cupo)
    return amount


def _plan(db: Session, user: User, draft: LoanDraftIn, rows: _Rows, profile: CreditProfile) -> _Plan:
    """R1–R4 en el orden de R4 y R2/R3: arma los términos y el `Preview`, sin escribir nada."""
    cfg = get_settings()
    is_merchant = user.role == UserRole.MERCHANT
    request, offer, account = rows.request, rows.offer, rows.account

    if draft.request_id is not None and request is None:
        raise AppError(404, "not_found", REQUEST_NOT_FOUND)
    if draft.offer_id is not None and offer is None:
        raise AppError(404, "not_found", OFFER_NOT_FOUND)
    if is_merchant and draft.request_id is not None and request.merchant_id != user.id:
        raise AppError(403, "forbidden", NOT_YOUR_REQUEST)
    if not is_merchant and offer is not None and offer.lender_id != user.id:
        raise AppError(403, "forbidden", NOT_YOUR_OFFER)
    if request is not None and request.status != RequestStatus.OPEN:
        raise AppError(409, "invalid_state", REQUEST_NOT_OPEN, {"status": request.status})
    if offer is not None and offer.status != OfferStatus.OPEN:
        raise AppError(409, "invalid_state", OFFER_NOT_OPEN, {"status": offer.status})

    merchant = user if is_merchant else db.get(User, request.merchant_id)
    lender = db.get(User, offer.lender_id) if is_merchant else user
    if _active_loan_exists(db, merchant.id):
        message = "Ya tienes un crédito en curso." if is_merchant else "Este negocio ya tiene un crédito en curso."
        raise AppError(409, "active_loan_exists", message)
    if not profile.assessment.eligible:
        message = (
            "Todavía no cumples los requisitos para pedir un crédito."
            if is_merchant
            else "Este negocio todavía no cumple los requisitos para un crédito."
        )
        unlocks_at = profile.assessment.unlocks_at
        details = {"unlocks_at": unlocks_at.isoformat().replace("+00:00", "Z") if unlocks_at else None}
        raise AppError(422, "not_eligible", message, details)
    if not merchant.payout_wallet_url:
        message = (
            "Vincula tu wallet de cobro antes de tomar un crédito."
            if is_merchant
            else "El negocio todavía no vinculó su wallet de cobro."
        )
        raise AppError(422, "wallet_required", message, {"party": "merchant"})
    if not lender.payout_wallet_url:
        message = (
            "El prestamista todavía no vinculó su wallet."
            if is_merchant
            else "Vincula tu wallet antes de fondear un crédito."
        )
        raise AppError(422, "wallet_required", message, {"party": "lender"})
    if account is None:
        message = (
            "Activa tu QR de Trust antes de tomar un crédito."
            if is_merchant
            else "El negocio todavía no tiene su QR de Trust activo."
        )
        raise AppError(422, "collection_account_required", message)
    market = (cfg.market_asset_code, cfg.market_asset_scale)
    expected = {"asset_code": cfg.market_asset_code, "asset_scale": cfg.market_asset_scale}
    if (merchant.payout_asset_code, merchant.payout_asset_scale) != market:
        raise AppError(422, "asset_mismatch", "La wallet del negocio debe estar en el asset de mercado.", {
            "expected": expected, "party": "merchant"})
    if (account.asset_code, account.asset_scale) != market:
        raise AppError(422, "asset_mismatch", "La cuenta de recaudo debe estar en el asset de mercado.", {
            "expected": expected, "party": "collection_account"})

    if request is not None and offer is not None:
        origin = LoanOrigin.MATCH
    elif offer is not None:
        origin = LoanOrigin.OFFER_TAKEN
    else:
        origin = LoanOrigin.REQUEST_FUNDED
    if origin == LoanOrigin.REQUEST_FUNDED:
        fee_bps, term_months = _funding_terms(draft)
    else:  # R1: cargo y plazo de la oferta (se ignoran los del borrador)
        fee_bps, term_months = offer.fee_bps, offer.term_months
    cupo = max(0, profile.cupo_total.value - cupo_usado(db, merchant.id))
    amount = _amount(draft, request, offer, cupo)
    pct_bps = offer.pct_bps if offer is not None else request.pct_bps  # R3
    terms = build_terms(amount, pct_bps, fee_bps, term_months, request.pct_bps if request else None, cfg)
    preview = build_preview(
        origin,
        request.id if request else None,
        offer.id if offer else None,
        merchant,
        lender,
        terms,
        cfg.market_asset_code,
        cfg.market_asset_scale,
    )
    return _Plan(origin, merchant, lender, request, offer, account, terms, preview)


def preview(db: Session, user: User, draft: LoanDraftIn) -> LoanPreviewOut:
    """R5: R1–R4 sin escribir (salvo el assessment que pueda crear `get_credit_profile`)."""
    _check_form(user, draft)
    merchant_id = _merchant_id(db, user, draft)
    profile = get_credit_profile(db, merchant_id)
    try:
        return _plan(db, user, draft, _load(db, user, draft, merchant_id, lock=False), profile).preview
    finally:
        db.rollback()  # fin de las lecturas


def _integrity_error(exc: IntegrityError) -> AppError:
    """R6: carrera perdida contra un índice único parcial de `loans`."""
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint == "uq_loans_open_merchant":
        return AppError(409, "active_loan_exists", "El negocio ya tiene un crédito en curso.")
    if constraint in ("uq_loans_offer", "uq_loans_request"):
        return AppError(409, "invalid_state", "La solicitud o la oferta ya están en otro crédito.")
    raise exc


def create(db: Session, user: User, draft: LoanDraftIn) -> LoanOut:
    """R6: `get_credit_profile` primero (puede hacer commit); luego una transacción con los bloqueos de 00-core en la
    que se revalida R4 y se inserta el crédito."""
    cfg = get_settings()
    _check_form(user, draft)
    merchant_id = _merchant_id(db, user, draft)
    profile = get_credit_profile(db, merchant_id)
    try:
        plan = _plan(db, user, draft, _load(db, user, draft, merchant_id, lock=True), profile)
    except AppError:
        db.rollback()
        raise

    now = clock.now()
    terms, merchant, lender = plan.terms, plan.merchant, plan.lender
    by_merchant = user.role == UserRole.MERCHANT
    lender_scale = lender.payout_asset_scale
    loan = Loan(
        merchant_id=merchant.id,
        lender_id=lender.id,
        request_id=plan.request.id if plan.request else None,
        offer_id=plan.offer.id if plan.offer else None,
        origin=plan.origin,
        created_by=user.id,
        credit_assessment_id=profile.assessment.id,
        collection_account_id=plan.account.id,
        merchant_payout_wallet_url=merchant.payout_wallet_url,
        lender_wallet_url=lender.payout_wallet_url,
        lender_asset_code=lender.payout_asset_code or cfg.market_asset_code,
        lender_asset_scale=lender_scale if lender_scale is not None else cfg.market_asset_scale,
        asset_code=cfg.market_asset_code,
        asset_scale=cfg.market_asset_scale,
        principal=terms.principal,
        fee_bps=terms.fee_bps,
        fee_amount=terms.fee_amount,
        total_repayable=terms.total_repayable,
        trust_share_bps=terms.trust_share_bps,
        term_months=terms.term_months,
        monthly_minimum=terms.monthly_minimum,
        pct_initial_bps=terms.pct_initial_bps,
        pct_min_bps=terms.pct_min_bps,
        pct_max_bps=terms.pct_max_bps,
        terms=plan.preview.model_dump(mode="json"),  # auditoría: lo que se mostró en el preview
        status=LoanStatus.PENDING_DISBURSEMENT if by_merchant else LoanStatus.PENDING_ACCEPTANCE,
        pct_bps=terms.pct_initial_bps,
        accepted_at=now if by_merchant else None,
        expires_at=now + timedelta(hours=cfg.pending_loan_ttl_hours),
    )
    db.add(loan)
    if plan.request is not None:
        plan.request.status = RequestStatus.MATCHED
    if plan.offer is not None:
        plan.offer.status = OfferStatus.TAKEN
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _integrity_error(exc) from None

    record_event(
        db,
        "loan",
        loan.id,
        "created",
        {
            "origin": loan.origin,
            "status": loan.status,
            "principal": loan.principal,
            "pct_bps": loan.pct_bps,
            "request_id": str(loan.request_id) if loan.request_id else None,
            "offer_id": str(loan.offer_id) if loan.offer_id else None,
        },
        actor_id=user.id,
    )
    if plan.request is not None:
        record_event(db, "loan_request", plan.request.id, "matched", {"loan_id": str(loan.id)}, actor_id=user.id)
    if plan.offer is not None:
        record_event(db, "loan_offer", plan.offer.id, "taken", {"loan_id": str(loan.id)}, actor_id=user.id)
    principal = _loan_money(loan, loan.principal)
    if by_merchant:
        data = {"loan_id": str(loan.id), "principal": principal.to_api()}
        _render_notify(
            db, lender.id, "loan_accepted", "lender", data, negocio=merchant.business_name or "", monto=principal
        )
    else:
        data = {"loan_id": str(loan.id), "principal": principal.to_api(), "pct_bps": loan.pct_bps}
        _render_notify(
            db,
            merchant.id,
            "offer_received",
            "merchant",
            data,
            prestamista=public_name(lender.first_name, lender.last_name),
            monto=principal,
            pct=loan.pct_bps,
        )
    db.commit()
    return loan_out(db, loan, user, merchant, lender)


# ── Aceptar, rechazar, cancelar (R7, R8) ──────────────────────────────────────


def _lock_party_loan(db: Session, user: User, loan_id: str) -> Loan:
    """Bloquea el crédito: 404 si no existe; 403 si el usuario no es una de sus partes."""
    loan = _locked_loan(db, parse_id(loan_id))
    if loan is None:
        raise AppError(404, "not_found", LOAN_NOT_FOUND)
    if user.id not in (loan.merchant_id, loan.lender_id):
        raise AppError(403, "forbidden", NOT_YOUR_LOAN)
    return loan


def _release(db: Session, loan: Loan, actor_id: uuid.UUID | None) -> None:
    """Reapertura (01): solicitud `matched → open` y oferta `taken → open`, bloqueadas después del crédito."""
    if loan.request_id is not None:
        request = _fetch(db, select(LoanRequest).where(LoanRequest.id == loan.request_id), lock=True)
        if request is not None and request.status == RequestStatus.MATCHED:
            other_open = select(LoanRequest.id).where(
                LoanRequest.merchant_id == request.merchant_id, LoanRequest.status == RequestStatus.OPEN
            )
            if db.scalar(select(exists(other_open))):  # nunca dos `open` (índice único parcial)
                logger.warning("loans: la solicitud %s no se reabre (el negocio ya tiene otra abierta)", request.id)
            else:
                request.status = RequestStatus.OPEN
                record_event(db, "loan_request", request.id, "reopened", {"loan_id": str(loan.id)}, actor_id)
    if loan.offer_id is not None:
        offer = _fetch(db, select(LoanOffer).where(LoanOffer.id == loan.offer_id), lock=True)
        if offer is not None and offer.status == OfferStatus.TAKEN:
            offer.status = OfferStatus.OPEN
            record_event(db, "loan_offer", offer.id, "reopened", {"loan_id": str(loan.id)}, actor_id)


def accept(db: Session, user: User, loan_id: str) -> LoanOut:
    """R7: el negocio dueño acepta (`pending_acceptance` → `pending_disbursement`); vencido → lo resuelve R12."""
    now = clock.now()
    try:
        loan = _lock_party_loan(db, user, loan_id)
        if loan.merchant_id != user.id:
            raise AppError(403, "forbidden", NOT_YOUR_LOAN)
        if loan.status != LoanStatus.PENDING_ACCEPTANCE:
            raise _invalid_state(LOAN_STATE, loan)
        if loan.expires_at is not None and loan.expires_at <= now:
            raise _invalid_state(LOAN_EXPIRED, loan)
    except AppError:
        db.rollback()
        raise
    loan.status = LoanStatus.PENDING_DISBURSEMENT
    loan.accepted_at = now
    loan.expires_at = now + timedelta(hours=get_settings().pending_loan_ttl_hours)
    record_event(db, "loan", loan.id, "accepted", {"from": LoanStatus.PENDING_ACCEPTANCE.value}, actor_id=user.id)
    principal = _loan_money(loan, loan.principal)
    data = {"loan_id": str(loan.id), "principal": principal.to_api()}
    _render_notify(db, loan.lender_id, "loan_accepted", "lender", data, negocio=user.business_name or "",
                   monto=principal)
    db.commit()
    return loan_out(db, loan, user, merchant=user)


def _close_pending(db: Session, user: User, loan_id: str, target: LoanStatus) -> LoanOut:
    """R8: `rejected` (negocio en `pending_acceptance`, prestamista en `pending_disbursement`) o `cancelled`
    (cualquier parte en `pending_*`); reabre solicitud y oferta y avisa a la contraparte."""
    try:
        loan = _lock_party_loan(db, user, loan_id)
        if target == LoanStatus.REJECTED:
            allowed = (
                (LoanStatus.PENDING_ACCEPTANCE,) if user.id == loan.merchant_id else (LoanStatus.PENDING_DISBURSEMENT,)
            )
        else:
            allowed = PENDING_STATUSES
        if loan.status not in allowed:
            raise _invalid_state(LOAN_STATE, loan)
    except AppError:
        db.rollback()
        raise
    now = clock.now()
    previous = loan.status
    loan.status = target
    if target == LoanStatus.REJECTED:
        loan.rejected_at = now
    else:
        loan.cancelled_at = now
    _release(db, loan, user.id)
    record_event(db, "loan", loan.id, target.value, {"from": previous}, actor_id=user.id)

    merchant, lender = db.get(User, loan.merchant_id), db.get(User, loan.lender_id)
    by_merchant = user.id == loan.merchant_id
    quien = (merchant.business_name or "") if by_merchant else public_name(lender.first_name, lender.last_name)
    counterpart, audience = (lender.id, "lender") if by_merchant else (merchant.id, "merchant")
    principal = _loan_money(loan, loan.principal)
    data = {"loan_id": str(loan.id), "principal": principal.to_api()}
    type = "loan_rejected" if target == LoanStatus.REJECTED else "loan_cancelled"
    _render_notify(db, counterpart, type, audience, data, quien=quien, monto=principal)
    db.commit()
    return loan_out(db, loan, user, merchant, lender)


def reject(db: Session, user: User, loan_id: str) -> LoanOut:
    return _close_pending(db, user, loan_id, LoanStatus.REJECTED)


def cancel(db: Session, user: User, loan_id: str) -> LoanOut:
    return _close_pending(db, user, loan_id, LoanStatus.CANCELLED)


# ── Desembolso (R9, R10) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Disbursement:
    """Copia plana de lo que necesita la llamada a Go (se usa sin transacción abierta)."""

    loan_id: uuid.UUID
    attempt: int
    sender: str
    receiver: str
    principal: Money
    business_name: str

    @property
    def idempotency_key(self) -> str:
        return f"loan:{self.loan_id}:disb:{self.attempt}"


def _authorization(loan: Loan) -> DisburseOut | None:
    """La autorización guardada si sigue vigente (`authorization_expires_at > now`)."""
    url, expires_at = loan.disbursement_authorization_url, loan.disbursement_authorization_expires_at
    if url and expires_at is not None and expires_at > clock.now():
        return DisburseOut(authorization_url=url, authorization_expires_at=expires_at)
    return None


def _claim_disbursement(db: Session, user: User, loan_id: str) -> _Disbursement | DisburseOut:
    """R9: transacción corta con el crédito bloqueado. Devuelve la autorización vigente o el intento a mandar a Go."""
    try:
        loan = _locked_loan(db, parse_id(loan_id))
        if loan is None:
            raise AppError(404, "not_found", LOAN_NOT_FOUND)
        if loan.lender_id != user.id:
            raise AppError(403, "forbidden", NOT_YOUR_LOAN)
        if loan.status == LoanStatus.DISBURSING:
            stored = _authorization(loan)
            if stored is None:
                raise _invalid_state(DISBURSE_IN_PROGRESS, loan)
            db.rollback()
            return stored
        if loan.status != LoanStatus.PENDING_DISBURSEMENT:
            raise _invalid_state(LOAN_STATE, loan)
        if loan.expires_at is not None and loan.expires_at - clock.now() < DISBURSE_MIN_REMAINING:
            raise _invalid_state(DISBURSE_EXPIRING, loan)
    except AppError:
        db.rollback()
        raise
    if loan.disbursement_attempt < 1:
        loan.disbursement_attempt = 1
        record_event(db, "loan", loan.id, "disbursement_requested", {"attempt": 1}, actor_id=user.id)
    merchant = db.get(User, loan.merchant_id)
    claim = _Disbursement(
        loan_id=loan.id,
        attempt=loan.disbursement_attempt,
        sender=loan.lender_wallet_url,
        receiver=loan.merchant_payout_wallet_url,
        principal=_loan_money(loan, loan.principal),
        business_name=(merchant.business_name if merchant else None) or "",
    )
    db.commit()  # R9: nunca se llama a Go con una transacción abierta
    return claim


def _reject_attempt(db: Session, claim: _Disbursement, exc: OpError) -> None:
    """R10: 4xx definitivo de Go → `disbursement_attempt += 1` (si nadie lo cambió mientras tanto)."""
    loan = _locked_loan(db, claim.loan_id)
    if (
        loan is not None
        and loan.status == LoanStatus.PENDING_DISBURSEMENT
        and loan.disbursement_attempt == claim.attempt
    ):
        loan.disbursement_attempt += 1
        record_event(
            db,
            "loan",
            loan.id,
            "disbursement_rejected",
            {"attempt": claim.attempt, "op_status": exc.status, "op_code": exc.code},
        )
    db.commit()


def _start_disbursing(db: Session, claim: _Disbursement, payment: Payment) -> DisburseOut:
    """R10: 201 de Go → `disbursing` con el pago y su autorización; un doble clic devuelve lo guardado."""
    loan = _locked_loan(db, claim.loan_id)
    if (
        loan is not None
        and loan.status == LoanStatus.PENDING_DISBURSEMENT
        and loan.disbursement_attempt == claim.attempt
    ):
        loan.status = LoanStatus.DISBURSING
        loan.op_disbursement_payment_id = payment.id
        loan.disbursement_authorization_url = payment.authorization_url
        loan.disbursement_authorization_expires_at = payment.authorization_expires_at
        loan.disbursement_started_at = clock.now()
        record_event(
            db,
            "loan",
            loan.id,
            "disbursing",
            {"attempt": claim.attempt, "payment_id": str(payment.id), "payment_status": payment.status},
        )
        db.commit()
        stored = _authorization(loan)
        if stored is None:  # Go ya no ofrece autorización (el pago avanzó): lo resuelve el worker (J3)
            raise _invalid_state(DISBURSE_IN_PROGRESS, loan)
        return stored
    if loan is not None and loan.status == LoanStatus.DISBURSING and loan.op_disbursement_payment_id == payment.id:
        stored = _authorization(loan)
        db.rollback()
        if stored is not None:
            return stored
        raise _invalid_state(DISBURSE_IN_PROGRESS, loan)
    db.rollback()
    raise _invalid_state(LOAN_STATE, loan)


def disburse(db: Session, op: OpClient, user: User, loan_id: str, client: str) -> DisburseOut:
    """R9/R10: el prestamista dueño inicia el desembolso y recibe la URL de autorización de su wallet."""
    claim = _claim_disbursement(db, user, loan_id)
    if isinstance(claim, DisburseOut):
        return claim
    base = get_settings().app_web_url.rstrip("/")
    request = CreatePaymentRequest(
        sender_wallet_address=claim.sender,
        receiver_wallet_address=claim.receiver,
        receive_amount=Amount.from_money(claim.principal),
        description=f"Desembolso crédito Trust · {claim.business_name}"[:DESCRIPTION_MAX],
        external_ref=f"loan:{claim.loan_id}:disb",
        return_url=f"{base}/op/return?loan_id={claim.loan_id}&client={client}",
    )
    try:
        payment = op.create_payment(request, idempotency_key=claim.idempotency_key)
    except OpUnavailable as exc:
        raise AppError(503, "op_unavailable") from exc
    except OpError as exc:
        if exc.retryable or exc.code == "request_in_progress" or exc.status == 401:  # como Go caído (misma clave)
            logger.warning("loans: op-service no pudo crear el desembolso %s: %r", claim.idempotency_key, exc)
            raise AppError(503, "op_unavailable") from exc
        if exc.status == 409 and exc.code == "idempotency_conflict":
            raise AppError(409, "invalid_state", DISBURSE_OTHER_CLIENT, {"op_code": exc.code}) from exc
        logger.warning("loans: op-service rechazó el desembolso %s: %r", claim.idempotency_key, exc)
        _reject_attempt(db, claim, exc)
        raise AppError(422, "disbursement_rejected", DISBURSE_REJECTED, {"op_code": exc.code}) from exc
    return _start_disbursing(db, claim, payment)


# ── Lecturas (R13, R14) ───────────────────────────────────────────────────────


def _visible_loan(db: Session, user: User, loan_id: str) -> Loan:
    """Partes o admin; 404 si no existe, 403 si es un tercero."""
    loan = db.get(Loan, parse_id(loan_id))
    if loan is None:
        raise AppError(404, "not_found", LOAN_NOT_FOUND)
    if user.role != UserRole.ADMIN and user.id not in (loan.merchant_id, loan.lender_id):
        raise AppError(403, "forbidden", NOT_YOUR_LOAN)
    return loan


def get_loan(db: Session, user: User, loan_id: str) -> LoanOut:
    return loan_out(db, _visible_loan(db, user, loan_id), user)


def _statuses(status: str | None) -> list[str]:
    """`?status=a,b` → lista de estados válidos (uno desconocido → 422 `validation_error`)."""
    values = [part.strip() for part in (status or "").split(",") if part.strip()]
    known = {s.value for s in LoanStatus}
    unknown = [value for value in values if value not in known]
    if unknown:
        allowed = ", ".join(s.value for s in LoanStatus)
        raise _field_error([{"field": "status", "message": f"Estado desconocido: {unknown[0]}. Usa: {allowed}."}])
    return values


def list_loans(db: Session, user: User, status: str | None, page: PageParams) -> LoanSummaryPage:
    """R14: merchant y lender ven los suyos, admin todos; más recientes primero."""
    statuses = _statuses(status)
    query = select(Loan)
    if user.role == UserRole.MERCHANT:
        query = query.where(Loan.merchant_id == user.id)
    elif user.role == UserRole.LENDER:
        query = query.where(Loan.lender_id == user.id)
    if statuses:
        query = query.where(Loan.status.in_(statuses))
    loans = db.scalars(
        query.order_by(Loan.created_at.desc(), Loan.id.desc()).limit(page.limit).offset(page.offset)
    ).all()
    users = _users(db, [loan.merchant_id for loan in loans] + [loan.lender_id for loan in loans])
    items = [loan_summary(loan, users[loan.merchant_id], users[loan.lender_id]) for loan in loans]
    return LoanSummaryPage(items=items, next_offset=next_offset(page.offset, page.limit, len(items)))


def pct_history(db: Session, user: User, loan_id: str) -> LoanPctHistory:
    """R14: revisiones del % por `period_start` asc."""
    loan = _visible_loan(db, user, loan_id)
    changes = db.scalars(
        select(LoanPctChange)
        .where(LoanPctChange.loan_id == loan.id)
        .order_by(LoanPctChange.period_start, LoanPctChange.created_at, LoanPctChange.id)
    ).all()
    return LoanPctHistory(items=[pct_change_out(c, loan.asset_code, loan.asset_scale) for c in changes])


# ── Funciones del worker (R11, R12, R15) ──────────────────────────────────────


def _clear_authorization(loan: Loan) -> None:
    loan.disbursement_authorization_url = None
    loan.disbursement_authorization_expires_at = None


def on_disbursement_update(db: Session, loan: Loan, payment: Payment) -> None:
    """R11: aplica el estado del pago de desembolso al crédito (ya bloqueado por el worker). No hace commit."""
    if loan.status != LoanStatus.DISBURSING or payment.id != loan.op_disbursement_payment_id:
        return
    now = clock.now()
    principal = _loan_money(loan, loan.principal)
    if payment.status == "completed":
        loan.status = LoanStatus.ACTIVE
        loan.activated_at = loan.period_start = now
        loan.next_review_at = now + PERIOD_LENGTH
        _clear_authorization(loan)
        record_event(db, "loan", loan.id, "activated", {"payment_id": str(payment.id)})
        merchant = db.get(User, loan.merchant_id)
        data = {"loan_id": str(loan.id), "principal": principal.to_api(), "pct_bps": loan.pct_bps}
        _render_notify(db, loan.merchant_id, "loan_active", "merchant", data, monto=principal, pct=loan.pct_bps)
        _render_notify(db, loan.lender_id, "loan_active", "lender", data, negocio=merchant.business_name or "",
                       monto=principal)
    elif payment.status in FAILED_PAYMENT_STATUSES:
        attempt = loan.disbursement_attempt
        loan.status = LoanStatus.PENDING_DISBURSEMENT
        loan.disbursement_attempt = attempt + 1
        _clear_authorization(loan)
        record_event(
            db,
            "loan",
            loan.id,
            "disbursement_failed",
            {"payment_id": str(payment.id), "payment_status": payment.status, "failure_code": payment.failure_code,
             "attempt": attempt},
        )
        merchant = db.get(User, loan.merchant_id)
        data = {"loan_id": str(loan.id), "failure_code": payment.failure_code or payment.status}
        _render_notify(db, loan.lender_id, "action_required", "lender", data, monto=principal,
                       negocio=merchant.business_name or "")
    elif payment.status == "processing" and loan.disbursement_authorization_url is not None:
        _clear_authorization(loan)  # ya autorizó en su wallet
        record_event(db, "loan", loan.id, "disbursement_authorized", {"payment_id": str(payment.id)})


def expire_pending(db: Session, now: datetime) -> int:
    """R12: `pending_*` con `expires_at ≤ now` → `expired` (con reapertura y avisos). No hace commit."""
    loans = db.scalars(
        select(Loan)
        .where(Loan.status.in_(PENDING_STATUSES), Loan.expires_at <= now)
        .order_by(Loan.expires_at, Loan.id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).all()
    hours = get_settings().pending_loan_ttl_hours
    for loan in loans:
        previous = loan.status
        loan.status = LoanStatus.EXPIRED
        loan.expired_at = now
        _release(db, loan, None)
        record_event(db, "loan", loan.id, "expired", {"from": previous})
        data = {"loan_id": str(loan.id)}
        _render_notify(db, loan.merchant_id, "loan_expired", "merchant", data, horas=hours)
        _render_notify(db, loan.lender_id, "loan_expired", "lender", data, horas=hours)
    return len(loans)


def close_period(db: Session, loan_id: uuid.UUID, now: datetime) -> LoanPctChange:
    """R15: revisa el % del periodo y abre el siguiente. `ValueError` si el crédito no existe o no está `active`.
    No hace commit."""
    loan = _locked_loan(db, loan_id)
    if loan is None or loan.status != LoanStatus.ACTIVE:
        raise ValueError("Solo se puede cerrar el periodo de un crédito activo")
    cfg = get_settings()
    start = loan.period_start or loan.activated_at or now
    end = loan.next_review_at if loan.next_review_at is not None and now >= loan.next_review_at else now
    in_period = _abonos(db, loan.id, start, end)
    balance = max(0, loan.total_repayable - _abonos(db, loan.id, None, start))
    decision = review_period(
        loan.pct_bps,
        loan.pct_min_bps,
        loan.pct_max_bps,
        loan.monthly_minimum,
        balance,
        in_period,
        cfg.reprice_step_bps,
    )
    old_pct, was_late = loan.pct_bps, loan.is_late
    change = LoanPctChange(
        loan_id=loan.id,
        old_pct_bps=old_pct,
        new_pct_bps=decision.new_pct_bps,
        reason=decision.reason,
        period_start=start,
        period_end=end,
        allocated_in_period=in_period,
        target_amount=decision.target_amount,
    )
    db.add(change)
    loan.pct_bps = decision.new_pct_bps
    if decision.missed:
        loan.missed_periods += 1
    loan.is_late = loan.missed_periods >= 2
    loan.period_start = end
    loan.next_review_at = end + PERIOD_LENGTH
    db.flush()
    record_event(
        db,
        "loan",
        loan.id,
        "period_closed",
        {
            "pct_change_id": str(change.id),
            "old_pct_bps": old_pct,
            "new_pct_bps": decision.new_pct_bps,
            "reason": decision.reason,
            "allocated_in_period": in_period,
            "target_amount": decision.target_amount,
            "missed_periods": loan.missed_periods,
        },
    )
    merchant = db.get(User, loan.merchant_id)
    negocio = merchant.business_name or ""
    if decision.new_pct_bps != old_pct:
        data = {
            "loan_id": str(loan.id),
            "old_pct_bps": old_pct,
            "new_pct_bps": decision.new_pct_bps,
            "reason": decision.reason,
        }
        values = {"pct_nuevo": decision.new_pct_bps, "pct_anterior": old_pct}
        _render_notify(db, loan.merchant_id, "pct_changed", "merchant", data, **values)
        _render_notify(db, loan.lender_id, "pct_changed", "lender", data, negocio=negocio, **values)
    if loan.is_late and not was_late:
        data = {"loan_id": str(loan.id), "missed_periods": loan.missed_periods}
        _render_notify(db, loan.merchant_id, "loan_late", "merchant", data, n=loan.missed_periods)
        _render_notify(db, loan.lender_id, "loan_late", "lender", data, negocio=negocio, n=loan.missed_periods)
    return change
