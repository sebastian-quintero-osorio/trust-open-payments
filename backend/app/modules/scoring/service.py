"""Score y cupo de cada negocio (09-scoring): `get_credit_profile` (R2, R3) y las dos vistas (R4–R6)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import BigInteger, cast, exists, func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.deps import parse_id
from app.core.errors import AppError
from app.core.money import Money, MoneySchema, market_money
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, Loan
from app.modules.marketplace.models import LoanRequest, RequestStatus
from app.modules.sales.models import Sale
from app.modules.scoring.features import build_features
from app.modules.scoring.models import CreditAssessment
from app.modules.scoring.schemas import CreditProfileOut, MerchantCreditView, SalesBand
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import merchant_ref
from app.policies.scoring import assess

# R6: cortes fijos de la franja de ventas mensuales (centavos del asset de mercado): $0, $2.500.000, $5.000.000,
# $10.000.000, $25.000.000, $50.000.000, $100.000.000 y $200.000.000.
SALES_BANDS = (0, 250_000_000, 500_000_000, 1_000_000_000, 2_500_000_000, 5_000_000_000, 10_000_000_000, 20_000_000_000)

NO_RELATION = "Solo puedes ver el perfil de negocios con una solicitud abierta o con un crédito contigo."


@dataclass(frozen=True)
class CreditProfile:
    """El assessment vigente y los cupos en el asset de mercado (R3)."""

    assessment: CreditAssessment
    cupo_total: Money
    cupo_usado: Money
    cupo_disponible: Money


def _latest_assessment(db: Session, merchant_id: uuid.UUID) -> CreditAssessment | None:
    return db.scalar(
        select(CreditAssessment)
        .where(CreditAssessment.merchant_id == merchant_id)
        .order_by(CreditAssessment.computed_at.desc(), CreditAssessment.created_at.desc(), CreditAssessment.id.desc())
        .limit(1)
    )


def _is_fresh(db: Session, assessment: CreditAssessment) -> bool:
    """R2: del día local de hoy, con la política vigente y sin ventas del negocio creadas después."""
    start, end = clock.local_day_bounds(clock.today_local())
    if not start <= assessment.computed_at < end or assessment.policy_version != get_settings().scoring_policy:
        return False
    newer_sale = select(Sale.id).where(
        Sale.merchant_id == assessment.merchant_id, Sale.created_at > assessment.computed_at
    )
    return not db.scalar(select(exists(newer_sale)))


def _new_assessment(db: Session, merchant_id: uuid.UUID) -> CreditAssessment:
    cfg = get_settings()
    now = clock.now()
    result = assess(build_features(db, merchant_id, now), cfg)
    assessment = CreditAssessment(
        merchant_id=merchant_id,
        policy_version=result.policy_version,
        score=result.score,
        grade=result.grade,
        eligible=result.eligible,
        unlocks_at=result.unlocks_at,
        cupo_total=result.cupo_total,
        asset_code=cfg.market_asset_code,
        asset_scale=cfg.market_asset_scale,
        features=result.features,
        reasons=list(result.reasons),
        computed_at=now,
    )
    db.add(assessment)
    db.commit()
    return assessment


def cupo_usado(db: Session, merchant_id: uuid.UUID) -> int:
    """R3: `principal` del crédito no terminado del negocio (a lo sumo uno, por el índice único parcial), o 0."""
    total = db.scalar(
        select(cast(func.coalesce(func.sum(Loan.principal), 0), BigInteger)).where(
            Loan.merchant_id == merchant_id, Loan.status.in_(NON_TERMINAL_LOAN_STATUSES)
        )
    )
    return total or 0


def get_credit_profile(db: Session, merchant_id: uuid.UUID) -> CreditProfile:
    """R2/R3: reutiliza el assessment de hoy si sigue vigente; si no, calcula uno nuevo y lo guarda.

    Cuando inserta un assessment **hace commit** (como `ensure_collection_account`): se llama antes de abrir una
    transacción con bloqueos, nunca con filas bloqueadas.
    """
    assessment = _latest_assessment(db, merchant_id)
    if assessment is None or not _is_fresh(db, assessment):
        assessment = _new_assessment(db, merchant_id)
    total = Money(assessment.cupo_total, assessment.asset_code, assessment.asset_scale)
    used = cupo_usado(db, merchant_id)
    return CreditProfile(
        assessment=assessment,
        cupo_total=total,
        cupo_usado=Money(used, total.asset_code, total.asset_scale),
        cupo_disponible=Money(max(0, total.value - used), total.asset_code, total.asset_scale),
    )


def active_months(assessment: CreditAssessment) -> int:
    """R4: sale de `assessment.features`."""
    return int((assessment.features or {}).get("active_months", 0))


def sales_band(avg_monthly_sales: int) -> SalesBand:
    """R6: `min` = mayor corte ≤ promedio; `max` = el corte siguiente, o `null` en el último tramo."""
    index = max(i for i, cut in enumerate(SALES_BANDS) if cut <= max(0, avg_monthly_sales))
    upper = SALES_BANDS[index + 1] if index + 1 < len(SALES_BANDS) else None
    return SalesBand(
        min=MoneySchema.of(market_money(SALES_BANDS[index])),
        max=MoneySchema.of(market_money(upper)) if upper is not None else None,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


def my_credit_profile(db: Session, merchant: User) -> CreditProfileOut:
    """R4: siempre 200 (sin ventas: no elegible, cupo 0 y su razón)."""
    profile = get_credit_profile(db, merchant.id)
    a = profile.assessment
    return CreditProfileOut(
        assessment_id=a.id,
        policy_version=a.policy_version,
        computed_at=a.computed_at,
        score=a.score,
        grade=a.grade,
        eligible=a.eligible,
        unlocks_at=a.unlocks_at,
        active_months=active_months(a),
        cupo_total=MoneySchema.of(profile.cupo_total),
        cupo_usado=MoneySchema.of(profile.cupo_usado),
        cupo_disponible=MoneySchema.of(profile.cupo_disponible),
        reasons=list(a.reasons or []),
    )


def merchant_credit_view(db: Session, lender: User, merchant_id: str) -> MerchantCreditView:
    """R5/R6: 404 si no es un negocio; 403 sin solicitud `open` ni crédito con este prestamista."""
    merchant = db.get(User, parse_id(merchant_id))
    if merchant is None or merchant.role != UserRole.MERCHANT:
        raise AppError(404, "not_found")
    open_request = select(LoanRequest.id).where(
        LoanRequest.merchant_id == merchant.id, LoanRequest.status == RequestStatus.OPEN
    )
    shared_loan = select(Loan.id).where(Loan.merchant_id == merchant.id, Loan.lender_id == lender.id)
    if not db.scalar(select(exists(open_request) | exists(shared_loan))):
        raise AppError(403, "forbidden", NO_RELATION)

    profile = get_credit_profile(db, merchant.id)
    a = profile.assessment
    return MerchantCreditView(
        merchant=merchant_ref(merchant),
        score=a.score,
        grade=a.grade,
        eligible=a.eligible,
        active_months=active_months(a),
        sales_band=sales_band(int((a.features or {}).get("avg_monthly_sales_3m", 0))),
        cupo_total=MoneySchema.of(profile.cupo_total),
        cupo_disponible=MoneySchema.of(profile.cupo_disponible),
        computed_at=a.computed_at,
    )
