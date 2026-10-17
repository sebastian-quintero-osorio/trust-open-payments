"""Abonos (12-repayments): lista con su entrega (R1–R4, R6) y traza venta → abono → patas → crédito (R5).

Solo lectura y sin llamar a op-service. La lista sale de una sola consulta con joins (sin N+1).
"""

import uuid
from datetime import date

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session, aliased

from app.core import clock
from app.core.deps import PageParams, next_offset, parse_id
from app.core.errors import AppError
from app.core.money import Money, MoneySchema
from app.modules.loans.models import Loan
from app.modules.loans.service import LOAN_NOT_FOUND, NOT_YOUR_LOAN
from app.modules.repayments.schemas import (
    Delivery,
    RepaymentDeliveryStatus,
    RepaymentOut,
    RepaymentPage,
    RepaymentTrace,
    TraceLoan,
    TraceRepayment,
    TraceSale,
)
from app.modules.sales.models import Payout, PayoutLeg, PayoutStatus, Repayment, Sale
from app.modules.sales.schemas import PayoutView
from app.modules.sales.service import BAD_RANGE, LEG_ORDER
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import lender_ref, merchant_ref

REPAYMENT_NOT_FOUND = "No encontramos ese abono."
NOT_YOUR_REPAYMENT = "Este abono no es de un crédito tuyo."

# R4: estado de la pata `lender` → `delivery.status` (mismo vocabulario que 07 R7, más `none`).
_DELIVERY: dict[str, RepaymentDeliveryStatus] = {
    PayoutStatus.PENDING: "in_progress",
    PayoutStatus.PROCESSING: "in_progress",
    PayoutStatus.COMPLETED: "completed",
    PayoutStatus.HELD: "held",
    PayoutStatus.FAILED_FINAL: "failed",
    PayoutStatus.SKIPPED: "none",
}
# R5: el prestamista ve su pata y la de Trust (sale de su parte); el negocio y el admin, las tres.
_LENDER_LEGS = frozenset({PayoutLeg.LENDER, PayoutLeg.TRUST})


def _money(value: int, asset_code: str, asset_scale: int) -> MoneySchema:
    return MoneySchema.of(Money(value, asset_code, asset_scale))


def delivery(leg: Payout | None) -> Delivery:
    """R4: sin pata `lender` (lender_net = 0, o abono sembrado sin patas) o `skipped` → `none`."""
    if leg is None:
        return Delivery(status="none", received=None)
    received = None
    if leg.receive_value is not None and leg.receive_asset_code is not None and leg.receive_asset_scale is not None:
        received = _money(leg.receive_value, leg.receive_asset_code, leg.receive_asset_scale)
    return Delivery(status=_DELIVERY[leg.status], received=received)


def _visible_to(user: User) -> list:
    """R1: lender → los suyos, merchant → los suyos, admin → todos."""
    if user.role == UserRole.LENDER:
        return [Repayment.lender_id == user.id]
    if user.role == UserRole.MERCHANT:
        return [Repayment.merchant_id == user.id]
    return []


def _check_loan_filter(db: Session, user: User, loan_id: uuid.UUID) -> None:
    """R1: `loan_id` inexistente → 404; de un crédito del que no es parte (y no es admin) → 403."""
    loan = db.get(Loan, loan_id)
    if loan is None:
        raise AppError(404, "not_found", LOAN_NOT_FOUND)
    if user.role != UserRole.ADMIN and user.id not in (loan.merchant_id, loan.lender_id):
        raise AppError(403, "forbidden", NOT_YOUR_LOAN)


def _list_query() -> Select:
    """R6: `repayments` ⋈ `sales` ⋈ `users` (negocio y prestamista) ⟕ pata `lender` (única por venta)."""
    merchant, lender = aliased(User), aliased(User)
    lender_leg = and_(Payout.sale_id == Repayment.sale_id, Payout.leg == PayoutLeg.LENDER)
    return (
        select(Repayment, Sale.paid_at, merchant, lender, Payout)
        .join(Sale, Sale.id == Repayment.sale_id)
        .join(merchant, merchant.id == Repayment.merchant_id)
        .join(lender, lender.id == Repayment.lender_id)
        .outerjoin(Payout, lender_leg)
    )


def list_repayments(
    db: Session, user: User, loan_id: uuid.UUID | None, from_: date | None, to: date | None, page: PageParams
) -> RepaymentPage:
    """R1–R4: filtros por crédito y por día local de la venta (inclusivos), orden `paid_at` desc y luego `id`."""
    if from_ is not None and to is not None and from_ > to:
        raise AppError(422, "validation_error", BAD_RANGE, {"fields": [{"field": "from", "message": BAD_RANGE}]})
    if loan_id is not None:
        _check_loan_filter(db, user, loan_id)

    query = _list_query().where(*_visible_to(user))
    if loan_id is not None:
        query = query.where(Repayment.loan_id == loan_id)
    if from_ is not None:
        query = query.where(Sale.paid_at >= clock.local_day_bounds(from_)[0])
    if to is not None:
        query = query.where(Sale.paid_at < clock.local_day_bounds(to)[1])
    rows = db.execute(
        query.order_by(Sale.paid_at.desc(), Repayment.id).limit(page.limit).offset(page.offset)
    ).all()

    items = []
    for repayment, paid_at, merchant, lender, leg in rows:
        code, scale = repayment.asset_code, repayment.asset_scale  # R3: asset del crédito
        items.append(
            RepaymentOut(
                id=repayment.id,
                sale_id=repayment.sale_id,
                loan_id=repayment.loan_id,
                merchant=merchant_ref(merchant),
                lender=lender_ref(lender),
                paid_at=paid_at,
                abono=_money(repayment.abono_amount, code, scale),
                trust_cut=_money(repayment.trust_cut_amount, code, scale),
                lender_net=_money(repayment.lender_net_amount, code, scale),
                pct_applied_bps=repayment.pct_applied_bps,
                delivery=delivery(leg),
            )
        )
    return RepaymentPage(items=items, next_offset=next_offset(page.offset, page.limit, len(items)))


def repayment_trace(db: Session, user: User, repayment_id: str) -> RepaymentTrace:
    """R5: lender o merchant del crédito, o admin (ajeno → 403). Nunca expone `receiver_wallet_url`."""
    repayment = db.get(Repayment, parse_id(repayment_id))
    if repayment is None:
        raise AppError(404, "not_found", REPAYMENT_NOT_FOUND)
    if user.role != UserRole.ADMIN and user.id not in (repayment.merchant_id, repayment.lender_id):
        raise AppError(403, "forbidden", NOT_YOUR_REPAYMENT)

    sale = db.get(Sale, repayment.sale_id)
    loan = db.get(Loan, repayment.loan_id)
    assert sale is not None and loan is not None  # llaves foráneas
    merchant, lender = db.get(User, loan.merchant_id), db.get(User, loan.lender_id)
    assert merchant is not None and lender is not None

    payouts = db.scalars(select(Payout).where(Payout.sale_id == sale.id)).all()
    if user.role == UserRole.LENDER:
        payouts = [p for p in payouts if p.leg in _LENDER_LEGS]
    payouts = sorted(payouts, key=lambda p: LEG_ORDER.index(PayoutLeg(p.leg)))

    code, scale = repayment.asset_code, repayment.asset_scale
    loan_code, loan_scale = loan.asset_code, loan.asset_scale
    return RepaymentTrace(
        id=repayment.id,
        sale=TraceSale(
            id=sale.id,
            amount=_money(sale.amount, sale.asset_code, sale.asset_scale),
            paid_at=sale.paid_at,
            bre_b_reference=sale.bre_b_reference,
            incoming_payment_url=sale.incoming_payment_url,
        ),
        repayment=TraceRepayment(
            abono=_money(repayment.abono_amount, code, scale),
            trust_cut=_money(repayment.trust_cut_amount, code, scale),
            lender_net=_money(repayment.lender_net_amount, code, scale),
            pct_applied_bps=repayment.pct_applied_bps,
            allocated_before=_money(repayment.allocated_before, code, scale),
            allocated_after=_money(repayment.allocated_after, code, scale),
        ),
        payouts=[PayoutView.of(p) for p in payouts],
        loan=TraceLoan(
            id=loan.id,
            status=loan.status,
            pct_bps=loan.pct_bps,
            merchant=merchant_ref(merchant),
            lender=lender_ref(lender),
            principal=_money(loan.principal, loan_code, loan_scale),
            total_repayable=_money(loan.total_repayable, loan_code, loan_scale),
            allocated_total=_money(loan.allocated_total, loan_code, loan_scale),
            delivered_total=_money(loan.delivered_total, loan_code, loan_scale),
        ),
    )
