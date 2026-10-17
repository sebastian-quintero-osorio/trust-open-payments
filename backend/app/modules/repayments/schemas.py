"""Schemas de abonos (12-repayments; `Repayment` y `RepaymentTrace` del YAML). Las patas reutilizan `PayoutView`
de 07-sales (sin `receiver_wallet_url`, R5)."""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.core.money import MoneySchema
from app.modules.loans.schemas import LoanStatusOut
from app.modules.sales.schemas import PayoutView
from app.modules.users.schemas import LenderRef, MerchantRef

RepaymentDeliveryStatus = Literal["none", "in_progress", "completed", "failed", "held"]


class Delivery(BaseModel):
    """R4: estado de la pata `lender` de la venta y lo que recibió el prestamista (asset de su wallet)."""

    status: RepaymentDeliveryStatus
    received: MoneySchema | None


class RepaymentOut(BaseModel):
    id: uuid.UUID
    sale_id: uuid.UUID
    loan_id: uuid.UUID
    merchant: MerchantRef
    lender: LenderRef
    paid_at: UtcDateTime
    abono: MoneySchema
    trust_cut: MoneySchema
    lender_net: MoneySchema
    pct_applied_bps: int
    delivery: Delivery


class RepaymentPage(BaseModel):
    items: list[RepaymentOut]
    next_offset: int | None


class TraceSale(BaseModel):
    id: uuid.UUID
    amount: MoneySchema
    paid_at: UtcDateTime
    bre_b_reference: str
    incoming_payment_url: str | None


class TraceRepayment(BaseModel):
    abono: MoneySchema
    trust_cut: MoneySchema
    lender_net: MoneySchema
    pct_applied_bps: int
    allocated_before: MoneySchema
    allocated_after: MoneySchema


class TraceLoan(BaseModel):
    id: uuid.UUID
    status: LoanStatusOut
    pct_bps: int
    merchant: MerchantRef
    lender: LenderRef
    principal: MoneySchema
    total_repayable: MoneySchema
    allocated_total: MoneySchema
    delivered_total: MoneySchema


class RepaymentTrace(BaseModel):
    """R5: venta → abono → patas (Open Payments) → crédito."""

    id: uuid.UUID
    sale: TraceSale
    repayment: TraceRepayment
    payouts: list[PayoutView]
    loan: TraceLoan
