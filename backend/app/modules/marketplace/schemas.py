"""Schemas del marketplace (10-marketplace; `LoanRequest`, `LoanOffer`, `Proposal` y `Match` del YAML)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field, StrictInt

from app.core.clock import UtcDateTime
from app.core.money import Money, MoneySchema
from app.modules.users.schemas import LenderRef, MerchantRef
from app.policies.matching import Proposal

GradeOut = Literal["A", "B", "C", "D"]
RequestStatusOut = Literal["open", "matched", "cancelled"]
OfferStatusOut = Literal["open", "taken", "cancelled"]


# ── Entrada ───────────────────────────────────────────────────────────────────


class CreateLoanRequestIn(BaseModel):
    """Rangos y `pct_bps` se validan en el service (R1), para dar `asset_mismatch` / `pct_not_allowed`."""

    min_amount: MoneySchema
    max_amount: MoneySchema
    pct_bps: StrictInt = Field(examples=[1500])


class CreateLoanOfferIn(BaseModel):
    """`fee_bps` y `term_months` se validan en el service (R3), después de R1."""

    min_amount: MoneySchema
    max_amount: MoneySchema
    pct_bps: StrictInt = Field(examples=[1500])
    fee_bps: StrictInt = Field(examples=[500], description="Cargo fijo sobre el principal (500 = 5 %), 0–10000")
    term_months: StrictInt | None = Field(default=None, examples=[3], description="1–24; si falta, el default")


# ── Salida ────────────────────────────────────────────────────────────────────


class CreditSummary(BaseModel):
    score: int
    grade: GradeOut
    cupo_total: MoneySchema
    cupo_disponible: MoneySchema


class LoanRequestOut(BaseModel):
    id: uuid.UUID
    status: RequestStatusOut
    pct_bps: int
    credit_assessment_id: uuid.UUID
    created_at: UtcDateTime
    min_amount: MoneySchema
    max_amount: MoneySchema
    merchant: MerchantRef | None
    credit: CreditSummary | None


class ProposalOut(BaseModel):
    amount: MoneySchema
    overlap_min: MoneySchema
    overlap_max: MoneySchema
    pct_bps: int

    @classmethod
    def of(cls, proposal: Proposal, asset_code: str, asset_scale: int) -> "ProposalOut":
        def money(value: int) -> MoneySchema:
            return MoneySchema.of(Money(value, asset_code, asset_scale))

        return cls(
            amount=money(proposal.amount),
            overlap_min=money(proposal.overlap_min),
            overlap_max=money(proposal.overlap_max),
            pct_bps=proposal.pct_bps,
        )


class LoanOfferOut(BaseModel):
    id: uuid.UUID
    status: OfferStatusOut
    lender: LenderRef
    min_amount: MoneySchema
    max_amount: MoneySchema
    pct_bps: int
    fee_bps: int
    term_months: int
    created_at: UtcDateTime
    proposal: ProposalOut | None


class LoanRequestPage(BaseModel):
    items: list[LoanRequestOut]
    next_offset: int | None


class LoanOfferPage(BaseModel):
    items: list[LoanOfferOut]
    next_offset: int | None
    count: int


class MatchRequest(BaseModel):
    id: uuid.UUID
    merchant: MerchantRef
    min_amount: MoneySchema
    max_amount: MoneySchema
    pct_bps: int
    cupo_total: MoneySchema


class MatchOffer(BaseModel):
    id: uuid.UUID
    lender: LenderRef
    min_amount: MoneySchema
    max_amount: MoneySchema
    pct_bps: int
    fee_bps: int
    term_months: int


class MatchOut(BaseModel):
    request: MatchRequest
    offer: MatchOffer
    proposal: ProposalOut
    received_at: UtcDateTime


class MatchList(BaseModel):
    items: list[MatchOut]
