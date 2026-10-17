"""`POST /dev/seed` (03 R4–R10): usuarios de demo, cuentas de recaudo, historial de ventas, ofertas y créditos.

Idempotente (R4): busca por email y por claves naturales; nunca duplica filas. Los pasos que dependen de specs
posteriores se activan solos cuando sus módulos existen (`optional_attr`):
- R6 (cuentas de recaudo) necesita `modules.recaudo.service.ensure_collection_account` (spec 06).
- R7 (historial) corre para cada negocio que ya tiene una cuenta de recaudo `active`.
- R9/R10 (créditos) necesitan `policies.terms`, `policies.split` (05), `modules.scoring.service` (09) y
  `modules.loans.service` (11).
"""

import logging
import random
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import exists, func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock, security
from app.core.config import get_settings
from app.core.events import Event, record_event
from app.core.money import bps_of
from app.integrations.op.client import OpClient
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, Loan, LoanOrigin, LoanStatus
from app.modules.marketplace.models import LoanOffer, OfferStatus
from app.modules.platform.lazy import optional_attr
from app.modules.platform.schemas import SeedResult, SeedUser
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus
from app.modules.sales.models import Repayment, Sale, SaleSource, SaleStatus
from app.modules.users.models import User, UserRole

logger = logging.getLogger(__name__)

PLACEHOLDER_WALLET_PREFIX = "https://ilp.interledger-test.dev/demo-"


# ── R5. Usuarios ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SeedUserSpec:
    slug: str
    role: UserRole
    first_name: str
    last_name: str
    business_name: str | None
    city: str
    lender_range: tuple[int, int] | None = None  # centavos del asset de mercado

    @property
    def email(self) -> str:
        return f"{self.slug}@demo.trust"

    @property
    def label(self) -> str:
        return self.business_name or f"{self.first_name} {self.last_name}"


SEED_USERS = (
    SeedUserSpec("marta", UserRole.MERCHANT, "Marta", "Rojas", "Tienda La Esquina", "Bucaramanga"),
    SeedUserSpec("trigal", UserRole.MERCHANT, "Lucía", "Gómez", "Panadería El Trigal", "Bucaramanga"),
    SeedUserSpec("nova", UserRole.MERCHANT, "Andrés", "Niño", "Papelería Nova", "Bucaramanga"),
    SeedUserSpec("camilo", UserRole.LENDER, "Camilo", "Pardo", None, "Bogotá", (100_000_000, 250_000_000)),
    SeedUserSpec("admin", UserRole.ADMIN, "Operador", "Trust", None, "Bogotá"),
)
MERCHANT_SLUGS = ("marta", "trigal", "nova")
LENDER_SLUG = "camilo"


def seed_wallet_url(spec: SeedUserSpec) -> str | None:
    settings = get_settings()
    if spec.role == UserRole.ADMIN:
        return None
    if spec.slug == "marta":
        return settings.demo_merchant_wallet_url
    if spec.slug == LENDER_SLUG:
        return settings.demo_lender_wallet_url
    return f"{PLACEHOLDER_WALLET_PREFIX}{spec.slug}"


def _sync_admin_password(user: User) -> None:
    """R5: admin@demo.trust entra con DEMO_ADMIN_PASSWORD (secreta, nunca en la respuesta). Sin ella, una contraseña
    aleatoria que nadie conoce; si quedó con DEMO_PASSWORD (seed anterior), se cambia: ningún admin con clave pública.
    """
    settings = get_settings()
    if settings.demo_admin_password:
        if not security.verify_password(settings.demo_admin_password, user.password_hash):
            user.password_hash = security.hash_password(settings.demo_admin_password)
    elif security.verify_password(settings.demo_password, user.password_hash):
        user.password_hash = security.hash_password(secrets.token_urlsafe(32))


def _ensure_users(db: Session) -> dict[str, User]:
    """Crea los que faltan (buscando por email) y completa wallet y rango si no los tienen. No hace commit."""
    settings = get_settings()
    users: dict[str, User] = {}
    for spec in SEED_USERS:
        user = db.scalar(select(User).where(User.email == spec.email))
        if user is None:
            password = settings.demo_password if spec.role != UserRole.ADMIN else secrets.token_urlsafe(32)
            user = User(
                email=spec.email,
                password_hash=security.hash_password(password),
                role=spec.role,
                first_name=spec.first_name,
                last_name=spec.last_name,
                business_name=spec.business_name,
                city=spec.city,
            )
            db.add(user)
        if spec.role == UserRole.ADMIN:
            _sync_admin_password(user)
        wallet = seed_wallet_url(spec)
        if wallet is not None and user.payout_wallet_url is None:
            # Directo en `users`, sin llamar a Go, en el asset de mercado (R5).
            user.payout_wallet_url = wallet
            user.payout_wallet_public_name = spec.label
            user.payout_asset_code = settings.market_asset_code
            user.payout_asset_scale = settings.market_asset_scale
        if spec.lender_range is not None and user.role == UserRole.LENDER and user.lender_range_min is None:
            user.lender_range_min, user.lender_range_max = spec.lender_range
        users[spec.slug] = user
    db.flush()
    return users


# ── R7. Historial de ventas ───────────────────────────────────────────────────

WHOLE = 100  # las ventas seed son pesos enteros (centavos múltiplos de 100)
MONTHLY_SALES = {"marta": 1_600_000_000, "trigal": 1_000_000_000, "nova": 1_200_000_000}  # centavos / mes
SKIP_DAY_PROBABILITY = 0.07  # "casi todos los días"
OPEN_HOUR, OPEN_SECONDS = 7, 13 * 3600  # ventas entre 7:00 y 20:00 hora local
SALES_PER_DAY = (8, 18)
MIN_SALE = 2_000  # pesos


@dataclass(frozen=True)
class SeedSale:
    reference: str
    amount: int  # centavos
    paid_at: datetime  # UTC


@dataclass(frozen=True)
class HistoryPlan:
    slug: str
    monthly_target: int  # centavos: promedio exacto de los 3 meses completos anteriores
    window_total: int = 0  # centavos vendidos después del día de activación del crédito sembrado (R9/R10)


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _allocate(total: int, weights: Sequence[int], minimum: int = 0) -> list[int]:
    """Reparte `total` (entero) según `weights` (enteros > 0), cada parte ≥ `minimum`, sumando exactamente `total`.

    Solo aritmética entera: cociente entero y el sobrante a los mayores residuos (largest remainder)."""
    if not weights:
        return []
    base = minimum * len(weights)
    rest = max(0, total - base)
    weight_sum = sum(weights)
    parts = [rest * w // weight_sum for w in weights]
    remainders = [(rest * w) % weight_sum for w in weights]
    leftover = rest - sum(parts)
    for i in sorted(range(len(weights)), key=lambda i: remainders[i], reverse=True)[:leftover]:
        parts[i] += 1
    return [p + minimum for p in parts]


def _month_totals(plan: HistoryPlan, months: list[tuple[int, int]], current: tuple[int, int]) -> dict:
    """Total en pesos de cada mes completo: los 3 anteriores al actual suman exactamente 3 × objetivo (−3 %, 0,
    +3 %); el 4.º anterior, 94 %. El mes en curso no tiene total fijo (va por día, ver `_merchant_history`)."""
    target = plan.monthly_target // WHOLE
    complete = [m for m in months if m != current]
    totals = {m: target * 94 // 100 for m in complete}
    delta = target * 3 // 100
    for m, value in zip(complete[-3:], (target - delta, target, target + delta), strict=False):
        totals[m] = value
    return totals


def _merchant_history(rng: random.Random, plan: HistoryPlan, today: date, activation_day: date) -> list[SeedSale]:
    tz = clock.local_tz()
    first, yesterday = _add_months(today.replace(day=1), -4), today - timedelta(days=1)
    days = [first + timedelta(days=n) for n in range((yesterday - first).days + 1)]
    # El día de la activación no tiene ventas: así todas las del "window" son posteriores a `activated_at`.
    active = [d for d in days if rng.random() >= SKIP_DAY_PROBABILITY and d != activation_day]
    window = [d for d in active if d > activation_day] if plan.window_total else []

    budgets: dict[date, int] = {}  # pesos por día
    for day, amount in zip(window, _allocate(plan.window_total // WHOLE, [rng.randint(80, 120) for _ in window]),
                           strict=True):
        budgets[day] = amount
    months = sorted({(d.year, d.month) for d in days})
    current = (today.year, today.month)
    totals = _month_totals(plan, months, current)
    days_in_current = (_add_months(today.replace(day=1), 1) - today.replace(day=1)).days
    for month in months:
        normal = [d for d in active if (d.year, d.month) == month and d not in budgets]
        if month == current:  # ritmo del mes en curso: 104 % del objetivo, por día con ventas
            remaining = plan.monthly_target // WHOLE * 104 // 100 * len(normal) // days_in_current
        else:  # mes completo: total exacto, descontando lo que ya puso la ventana del crédito
            in_window = sum(amount for d, amount in budgets.items() if (d.year, d.month) == month)
            remaining = max(0, totals[month] - in_window)
        # Pesos enteros: base 75–125 × 115 los sábados, 85 los domingos, 100 el resto.
        weights = [rng.randint(75, 125) * (115 if d.weekday() == 5 else 85 if d.weekday() == 6 else 100)
                   for d in normal]
        budgets.update(zip(normal, _allocate(remaining, weights), strict=True))

    sales: list[SeedSale] = []
    for day in sorted(budgets):
        day_total = budgets[day]
        if day_total <= 0:
            continue
        count = min(rng.randint(*SALES_PER_DAY), max(1, day_total // MIN_SALE))
        amounts = _allocate(day_total, [rng.randint(30, 170) for _ in range(count)], min(MIN_SALE, day_total // count))
        offsets = sorted(rng.sample(range(OPEN_SECONDS), count))
        opening = datetime.combine(day, time(OPEN_HOUR), tzinfo=tz)
        for n, (offset, amount) in enumerate(zip(offsets, amounts, strict=True), start=1):
            paid_at = (opening + timedelta(seconds=offset)).astimezone(UTC)
            sales.append(SeedSale(f"SEED-{plan.slug}-{day:%Y%m%d}-{n}", amount * WHOLE, paid_at))
    return sales


def generate_history(plans: Sequence[HistoryPlan], today: date, activation_day: date) -> dict[str, list[SeedSale]]:
    """Generador determinista (`random.Random(42)`), siempre con todos los planes en el mismo orden: desde el día 1
    del 4.º mes calendario anterior hasta ayer (nada hoy), ventas casi todos los días entre 7:00 y 20:00 locales."""
    rng = random.Random(42)
    return {plan.slug: _merchant_history(rng, plan, today, activation_day) for plan in plans}


# ── R8. Ofertas de Camilo ─────────────────────────────────────────────────────

OFFER_FEE_BPS, OFFER_TERM_MONTHS = 600, 3
SEED_OFFERS = (  # (min, max, pct_bps) en centavos; la primera es la del match y la de R10
    (100_000_000, 250_000_000, 1500),
    (150_000_000, 300_000_000, 1500),
    (50_000_000, 200_000_000, 1000),
    (100_000_000, 200_000_000, 2000),
)


def _ensure_offers(db: Session, lender: User) -> list[LoanOffer]:
    """Una oferta por fila de `SEED_OFFERS`, buscada por sus términos en cualquier estado (una que Marta ya tomó o
    que Camilo canceló no se vuelve a crear). No hace commit."""
    settings = get_settings()
    offers = []
    for min_amount, max_amount, pct_bps in SEED_OFFERS:
        offer = db.scalar(
            select(LoanOffer)
            .where(
                LoanOffer.lender_id == lender.id,
                LoanOffer.min_amount == min_amount,
                LoanOffer.max_amount == max_amount,
                LoanOffer.pct_bps == pct_bps,
                LoanOffer.fee_bps == OFFER_FEE_BPS,
                LoanOffer.term_months == OFFER_TERM_MONTHS,
            )
            .order_by(LoanOffer.created_at)
            .limit(1)
        )
        if offer is None:
            offer = LoanOffer(
                lender_id=lender.id,
                min_amount=min_amount,
                max_amount=max_amount,
                asset_code=settings.market_asset_code,
                asset_scale=settings.market_asset_scale,
                pct_bps=pct_bps,
                fee_bps=OFFER_FEE_BPS,
                term_months=OFFER_TERM_MONTHS,
                status=OfferStatus.OPEN,
            )
            db.add(offer)
        offers.append(offer)
    db.flush()
    return offers


# ── R9/R10. Créditos sembrados ────────────────────────────────────────────────


@dataclass(frozen=True)
class SeedLoanSpec:
    slug: str
    principal: int  # centavos
    pct_bps: int
    allocated_target_bps: int  # fracción de `total_repayable` ya abonada

    @property
    def window_total(self) -> int:
        """Ventas (centavos, pesos enteros) después de la activación cuyo abono suma ≈ el objetivo."""
        total_repayable = self.principal + bps_of(self.principal, OFFER_FEE_BPS)
        target = bps_of(total_repayable, self.allocated_target_bps)
        cents = -(-target * 10_000 // self.pct_bps)
        return -(-cents // WHOLE) * WHOLE


SEED_LOANS = (SeedLoanSpec("trigal", 150_000_000, 1500, 5800), SeedLoanSpec("nova", 200_000_000, 1000, 1200))
MARTA_LOAN = SeedLoanSpec("marta", 200_000_000, 1500, 3200)  # R10, desde la primera oferta
LOAN_AGE = timedelta(days=15)
REVIEW_PERIOD = timedelta(days=30)
SEEDED_LOAN_EVENT = "seeded"


@dataclass(frozen=True)
class LoanTools:
    build_terms: Callable[..., Any]
    split_sale: Callable[..., Any]
    split_state: Callable[..., Any]
    get_credit_profile: Callable[..., Any]
    build_preview: Callable[..., Any]  # 11: snapshot `Preview` (Money) que se guarda en `loans.terms`


def _loan_tools() -> LoanTools | None:
    tools = (
        optional_attr("app.policies.terms", "build_terms"),
        optional_attr("app.policies.split", "split_sale"),
        optional_attr("app.policies.split", "SplitLoanState"),
        optional_attr("app.modules.scoring.service", "get_credit_profile"),
        optional_attr("app.modules.loans.service", "build_preview"),
    )
    if None in tools or optional_attr("app.modules.loans.service", "close_period") is None:
        return None
    return LoanTools(*tools)


def _active_account(db: Session, merchant_id: uuid.UUID) -> CollectionAccount | None:
    return db.scalar(
        select(CollectionAccount).where(
            CollectionAccount.merchant_id == merchant_id, CollectionAccount.status == CollectionAccountStatus.ACTIVE
        )
    )


def _seed_loan(
    db: Session, tools: LoanTools, spec: SeedLoanSpec, merchant: User, lender: User, offer: LoanOffer | None
) -> None:
    """R9/R10: crédito `active` con Camilo, con sus abonos sobre las ventas seed posteriores a `activated_at`.

    `offer=None` crea su propia oferta `taken` (R9); con oferta (R10) la toma si sigue `open`. Hace commit.
    """
    settings = get_settings()
    loans_of_merchant = select(Loan.id).where(Loan.merchant_id == merchant.id)
    if offer is None and db.scalar(select(exists(loans_of_merchant))):
        return
    if db.scalar(select(exists(loans_of_merchant.where(Loan.status.in_(NON_TERMINAL_LOAN_STATUSES))))):
        return
    account = _active_account(db, merchant.id)
    if account is None or merchant.payout_wallet_url is None or lender.payout_wallet_url is None:
        logger.warning("Seed: %s sin cuenta de recaudo o sin wallets; no se siembra su crédito", spec.slug)
        return

    profile = tools.get_credit_profile(db, merchant.id)
    terms = tools.build_terms(spec.principal, spec.pct_bps, OFFER_FEE_BPS, OFFER_TERM_MONTHS, None, settings)
    activated_at = clock.now() - LOAN_AGE

    if offer is None:
        offer = LoanOffer(
            lender_id=lender.id,
            min_amount=spec.principal,
            max_amount=spec.principal,
            asset_code=settings.market_asset_code,
            asset_scale=settings.market_asset_scale,
            pct_bps=spec.pct_bps,
            fee_bps=OFFER_FEE_BPS,
            term_months=OFFER_TERM_MONTHS,
            status=OfferStatus.TAKEN,
        )
        db.add(offer)
    else:
        locked = db.scalar(
            select(LoanOffer)
            .where(LoanOffer.id == offer.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked is None or locked.status != OfferStatus.OPEN:
            db.rollback()
            logger.warning("Seed: la primera oferta de Camilo no está abierta; no se crea el crédito de Marta")
            return
        locked.status = OfferStatus.TAKEN
        offer = locked
    db.flush()

    loan = Loan(
        merchant_id=merchant.id,
        lender_id=lender.id,
        offer_id=offer.id,
        origin=LoanOrigin.OFFER_TAKEN,
        created_by=merchant.id,
        credit_assessment_id=profile.assessment.id,
        collection_account_id=account.id,
        merchant_payout_wallet_url=merchant.payout_wallet_url,
        lender_wallet_url=lender.payout_wallet_url,
        lender_asset_code=lender.payout_asset_code,
        lender_asset_scale=lender.payout_asset_scale,
        asset_code=settings.market_asset_code,
        asset_scale=settings.market_asset_scale,
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
        # Misma forma que un crédito creado por la API (11 R6): el `Preview` completo con montos en Money.
        terms=tools.build_preview(
            LoanOrigin.OFFER_TAKEN, None, offer.id, merchant, lender, terms,
            settings.market_asset_code, settings.market_asset_scale,
        ).model_dump(mode="json"),
        status=LoanStatus.ACTIVE,
        pct_bps=terms.pct_initial_bps,
        period_start=activated_at,
        next_review_at=activated_at + REVIEW_PERIOD,
        disbursement_attempt=1,
        accepted_at=activated_at,
        disbursement_started_at=activated_at,
        activated_at=activated_at,
    )
    db.add(loan)
    db.flush()

    allocated = trust_earned = repayments = 0
    sales = db.scalars(
        select(Sale)
        .where(Sale.merchant_id == merchant.id, Sale.source == SaleSource.SEED, Sale.paid_at >= activated_at)
        .order_by(Sale.paid_at, Sale.id)
    ).all()
    for sale in sales:
        state = tools.split_state(loan.pct_bps, loan.total_repayable, allocated, loan.fee_amount, loan.trust_share_bps)
        split = tools.split_sale(sale.amount, state, policy=settings.trust_cut_policy)
        if split.abono <= 0:
            break
        db.add(
            Repayment(
                sale_id=sale.id,
                loan_id=loan.id,
                merchant_id=merchant.id,
                lender_id=lender.id,
                abono_amount=split.abono,
                trust_cut_amount=split.trust_cut,
                lender_net_amount=split.lender_net,
                asset_code=sale.asset_code,
                asset_scale=sale.asset_scale,
                pct_applied_bps=loan.pct_bps,
                allocated_before=allocated,
                allocated_after=split.allocated_after,
                created_at=sale.paid_at,  # el periodo (11 R15) cuenta abonos por `created_at`
            )
        )
        allocated, trust_earned = split.allocated_after, trust_earned + split.trust_cut
        repayments += 1
    loan.allocated_total = loan.delivered_total = allocated
    loan.trust_earned, loan.trust_pending = trust_earned, 0
    record_event(db, "loan", loan.id, SEEDED_LOAN_EVENT, {"allocated_total": allocated, "repayments": repayments})
    try:
        db.commit()
    except IntegrityError:  # un crédito en vivo ganó la carrera por la oferta o por el negocio
        db.rollback()
        logger.warning("Seed: no se pudo crear el crédito de %s (choca con otro crédito)", spec.slug)


# ── Orquestación ──────────────────────────────────────────────────────────────


def _insert_history(db: Session, users: dict[str, User], accounts: dict[str, CollectionAccount]) -> None:
    """R7: inserta el historial de cada negocio con cuenta `active` que todavía no tiene ventas seed. No hace commit."""
    pending = [
        slug
        for slug in MERCHANT_SLUGS
        if slug in accounts
        and not db.scalar(
            select(exists().where(Sale.merchant_id == users[slug].id, Sale.source == SaleSource.SEED))
        )
    ]
    if not pending:
        return
    windows = {spec.slug: spec.window_total for spec in (*SEED_LOANS, MARTA_LOAN)}
    plans = [HistoryPlan(slug, MONTHLY_SALES[slug], windows.get(slug, 0)) for slug in MERCHANT_SLUGS]
    activation_day = clock.local_date(clock.now() - LOAN_AGE)
    history = generate_history(plans, clock.today_local(), activation_day)
    for slug in pending:
        account = accounts[slug]
        rows = [
            {
                "merchant_id": users[slug].id,
                "collection_account_id": account.id,
                "puntored_event_id": None,
                "bre_b_reference": sale.reference,
                "amount": sale.amount,
                "asset_code": account.asset_code,
                "asset_scale": account.asset_scale,
                "paid_at": sale.paid_at,
                "source": SaleSource.SEED,
                "status": SaleStatus.PROCESSED,
            }
            for sale in history[slug]
        ]
        if rows:
            db.execute(insert(Sale), rows)  # sin `ledger_in` ni `payouts` (R7)


def run_seed(db: Session, op: OpClient, with_active_loan_for_marta: bool = False) -> SeedResult:
    settings = get_settings()
    users = _ensure_users(db)
    db.commit()  # R6: usuarios guardados antes de cualquier llamada a Go

    ensure_collection_account = optional_attr("app.modules.recaudo.service", "ensure_collection_account")
    if ensure_collection_account is not None:
        for slug in MERCHANT_SLUGS:
            ensure_collection_account(db, op, users[slug])  # idempotente; hace commit y llama a Go fuera de tx
    else:
        logger.info("Seed: sin spec 06 todavía; se omiten las cuentas de recaudo")

    accounts = {slug: account for slug in MERCHANT_SLUGS if (account := _active_account(db, users[slug].id))}
    _insert_history(db, users, accounts)
    offers = _ensure_offers(db, users[LENDER_SLUG])
    db.commit()

    tools = _loan_tools()
    if tools is not None:
        for spec in SEED_LOANS:
            _seed_loan(db, tools, spec, users[spec.slug], users[LENDER_SLUG], None)
        if with_active_loan_for_marta:
            _seed_loan(db, tools, MARTA_LOAN, users["marta"], users[LENDER_SLUG], offers[0])
    else:
        logger.info("Seed: sin specs 05, 09 y 11 todavía; no se siembran créditos")

    merchant_ids = [users[slug].id for slug in MERCHANT_SLUGS]
    seed_sales = db.scalar(
        select(func.count()).select_from(Sale).where(Sale.merchant_id.in_(merchant_ids), Sale.source == SaleSource.SEED)
    )
    open_offers = db.scalar(
        select(func.count())
        .select_from(LoanOffer)
        .where(LoanOffer.lender_id == users[LENDER_SLUG].id, LoanOffer.status == OfferStatus.OPEN)
    )
    loans = db.scalar(
        select(func.count(func.distinct(Event.entity_id)))
        .select_from(Event)
        .join(Loan, Loan.id == Event.entity_id)
        .where(Event.entity_type == "loan", Event.type == SEEDED_LOAN_EVENT)
    )
    result = SeedResult(
        users=[
            SeedUser(
                id=users[spec.slug].id,
                email=spec.email,
                password=settings.demo_password,
                role=users[spec.slug].role,
                label=spec.label,
            )
            for spec in SEED_USERS
            if spec.role != UserRole.ADMIN  # su contraseña no se publica (R5)
        ],
        seed_sales=seed_sales or 0,
        open_offers=open_offers or 0,
        loans=loans or 0,
    )
    db.rollback()  # fin de las lecturas
    return result
