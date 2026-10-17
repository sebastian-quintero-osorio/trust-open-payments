"""Schemas de créditos (spec 11). Por ahora solo `LoanPctChange`, que también devuelve
`/dev/loans/{loan_id}/close-period` (03 R12)."""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.core.money import Money, MoneySchema
from app.modules.loans.models import LoanPctChange


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
