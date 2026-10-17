"""Schemas del operador de Trust (16-admin; `AdminDashboard` y `AdminPayout` del YAML)."""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.core.money import Money, MoneySchema
from app.modules.recaudo.schemas import CollectionAccountOut
from app.modules.sales.models import Payout
from app.modules.users.schemas import MerchantRef

Leg = Literal["lender", "merchant", "trust"]


class PayoutsByStatus(BaseModel):
    pending: int
    processing: int
    completed: int
    failed_final: int
    held: int
    skipped: int


class LoansByStatus(BaseModel):
    pending_acceptance: int
    pending_disbursement: int
    disbursing: int
    active: int
    paid: int
    cancelled: int
    rejected: int
    expired: int


class CollectionAccountsByStatus(BaseModel):
    active: int
    revoked: int


class RetainedPayout(BaseModel):
    id: uuid.UUID
    leg: Leg
    status: Literal["failed_final", "held"]
    failure_code: str | None
    amount: MoneySchema
    sent_total: MoneySchema
    merchant: MerchantRef


class AdminDashboard(BaseModel):
    trust_revenue: list[MoneySchema]
    trust_pending: list[MoneySchema]
    retained_funds: list[MoneySchema]
    in_flight: list[MoneySchema]
    payouts_by_status: PayoutsByStatus
    loans_by_status: LoansByStatus
    collection_accounts: CollectionAccountsByStatus
    retained_payouts: list[RetainedPayout]


class AdminPayoutOut(BaseModel):
    id: uuid.UUID
    sale_id: uuid.UUID
    loan_id: uuid.UUID | None
    leg: Leg
    status: Literal["pending", "processing", "completed", "failed_final", "held", "skipped"]
    attempt: int
    amount: MoneySchema
    sent_total: MoneySchema
    current_debit_amount: MoneySchema
    failure_code: str | None
    next_attempt_at: UtcDateTime | None

    @classmethod
    def of(cls, payout: Payout) -> "AdminPayoutOut":
        def money(value: int) -> MoneySchema:
            return MoneySchema.of(Money(value, payout.asset_code, payout.asset_scale))

        return cls(
            id=payout.id,
            sale_id=payout.sale_id,
            loan_id=payout.loan_id,
            leg=payout.leg,
            status=payout.status,
            attempt=payout.attempt,
            amount=money(payout.amount),
            sent_total=money(payout.sent_total),
            current_debit_amount=money(payout.current_debit_amount),
            failure_code=payout.failure_code,
            next_attempt_at=payout.next_attempt_at,
        )


class RevokeResult(BaseModel):
    collection_account: CollectionAccountOut
    held_payouts: int
