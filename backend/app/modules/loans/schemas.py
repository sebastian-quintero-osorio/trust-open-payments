"""Schemas de créditos (spec 11; `LoanDraft`, `LoanPreview`, `Loan`, `LoanSummary`, `DisburseResponse` y
`LoanPctChange` del YAML). `LoanPctChangeOut` también lo devuelve `/dev/loans/{loan_id}/close-period` (03 R12)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, StrictInt

from app.core.clock import UtcDateTime
from app.core.money import Money, MoneySchema
from app.modules.loans.models import LoanPctChange
from app.modules.users.schemas import LenderRef, MerchantRef

LoanStatusOut = Literal[
    "pending_acceptance", "pending_disbursement", "disbursing", "active", "paid", "cancelled", "rejected", "expired"
]
LoanOriginOut = Literal["offer_taken", "request_funded", "match"]


# ── Entrada ───────────────────────────────────────────────────────────────────


class LoanDraftIn(BaseModel):
    """`fee_bps`, `term_months` y `amount` se validan en el service (R1, R2), en el orden de R4."""

    request_id: uuid.UUID | None = None
    offer_id: uuid.UUID | None = None
    amount: MoneySchema | None = None
    fee_bps: StrictInt | None = Field(default=None, examples=[500], description="Solo request_funded: 0–10000")
    term_months: StrictInt | None = Field(default=None, examples=[3], description="Solo request_funded: 1–24")


class DisburseIn(BaseModel):
    client: Literal["web", "mobile"]


# ── Salida ────────────────────────────────────────────────────────────────────


class TermsOut(BaseModel):
    principal: MoneySchema
    fee_bps: int
    fee_amount: MoneySchema
    total_repayable: MoneySchema
    trust_share_bps: int
    term_months: int
    monthly_minimum: MoneySchema
    pct_initial_bps: int
    pct_min_bps: int
    pct_max_bps: int


class SplitExampleOut(BaseModel):
    sale: MoneySchema
    merchant_amount: MoneySchema
    abono: MoneySchema
    trust_cut: MoneySchema
    lender_net: MoneySchema


class LoanPreviewOut(BaseModel):
    origin: LoanOriginOut
    request_id: uuid.UUID | None
    offer_id: uuid.UUID | None
    merchant: MerchantRef
    lender: LenderRef
    terms: TermsOut
    example: SplitExampleOut
    expires_in_hours: int


class NextActionOut(BaseModel):
    type: Literal["accept", "disburse", "authorize_in_wallet", "none"]
    actor: Literal["merchant", "lender"] | None


class PeriodOut(BaseModel):
    start: UtcDateTime
    next_review_at: UtcDateTime
    allocated_in_period: MoneySchema
    target: MoneySchema


class DisbursementOut(BaseModel):
    status: Literal["not_started", "in_progress", "completed"]
    authorization_url: str | None
    authorization_expires_at: UtcDateTime | None
    attempt: int


class LoanOut(BaseModel):
    id: uuid.UUID
    status: LoanStatusOut
    origin: LoanOriginOut
    merchant: MerchantRef
    lender: LenderRef
    request_id: uuid.UUID | None
    offer_id: uuid.UUID | None
    terms: TermsOut
    pct_bps: int
    allocated: MoneySchema
    delivered: MoneySchema
    outstanding: MoneySchema
    pct_paid_bps: int
    period: PeriodOut | None
    is_late: bool
    missed_periods: int
    next_action: NextActionOut
    disbursement: DisbursementOut
    expires_at: UtcDateTime | None
    created_at: UtcDateTime
    accepted_at: UtcDateTime | None
    activated_at: UtcDateTime | None
    paid_at: UtcDateTime | None


class LoanSummaryOut(BaseModel):
    id: uuid.UUID
    status: LoanStatusOut
    origin: LoanOriginOut
    merchant: MerchantRef
    lender: LenderRef
    principal: MoneySchema
    pct_bps: int
    allocated: MoneySchema
    pct_paid_bps: int
    is_late: bool
    next_action: NextActionOut
    created_at: UtcDateTime
    activated_at: UtcDateTime | None


class LoanSummaryPage(BaseModel):
    items: list[LoanSummaryOut]
    next_offset: int | None


class DisburseOut(BaseModel):
    status: Literal["disbursing"] = "disbursing"
    authorization_url: str
    authorization_expires_at: UtcDateTime


class LoanPctChangeOut(BaseModel):
    id: uuid.UUID
    old_pct_bps: int
    new_pct_bps: int
    reason: Literal["missed_minimum", "ahead_of_schedule", "unchanged", "manual"]
    period_start: UtcDateTime
    period_end: UtcDateTime
    allocated_in_period: MoneySchema
    target_amount: MoneySchema
    created_at: UtcDateTime


class LoanPctHistory(BaseModel):
    items: list[LoanPctChangeOut]


def pct_change_out(change: LoanPctChange, asset_code: str, asset_scale: int) -> LoanPctChangeOut:
    """`loan_pct_changes` no guarda asset: los montos van en el asset del crédito."""
    return LoanPctChangeOut(
        id=change.id,
        old_pct_bps=change.old_pct_bps,
        new_pct_bps=change.new_pct_bps,
        reason=change.reason,
        period_start=change.period_start,
        period_end=change.period_end,
        allocated_in_period=MoneySchema.of(Money(change.allocated_in_period, asset_code, asset_scale)),
        target_amount=MoneySchema.of(Money(change.target_amount, asset_code, asset_scale)),
        created_at=change.created_at,
    )
