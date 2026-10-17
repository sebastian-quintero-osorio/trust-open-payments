import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.core.clock import UtcDateTime
from app.core.money import MoneySchema


class Health(BaseModel):
    status: Literal["ok", "degraded", "down"]
    db: Literal["ok", "down"]
    op: Literal["ok", "degraded", "down"]
    op_client: Literal["fake", "http"]
    version: str
    time: UtcDateTime
    warnings: list[str]


class Asset(BaseModel):
    asset_code: str
    asset_scale: int


class ExampleSplit(BaseModel):
    pct_bps: int
    abono: MoneySchema
    merchant: MoneySchema


class Config(BaseModel):
    market_asset: Asset
    pct_options_bps: list[int]
    min_loan_amount: MoneySchema
    amount_step: MoneySchema
    cupo_cap: MoneySchema
    default_term_months: int
    timezone: str
    dev_endpoints_enabled: bool
    example_sale: MoneySchema
    example_splits: list[ExampleSplit]


# ── /dev (03 R4–R13) ──────────────────────────────────────────────────────────


class SeedRequest(BaseModel):
    with_active_loan_for_marta: bool = False


class SeedUser(BaseModel):
    id: uuid.UUID
    email: str
    password: str
    role: Literal["merchant", "lender", "admin"]
    label: str


class SeedResult(BaseModel):
    users: list[SeedUser]
    seed_sales: int
    open_offers: int
    loans: int


class SimulateSaleRequest(BaseModel):
    """Exactamente uno de `merchant_id` o `collection_account_id` (03 R11; se valida en el service)."""

    merchant_id: uuid.UUID | None = None
    collection_account_id: uuid.UUID | None = Field(default=None, description="Id de Trust de la cuenta de recaudo")
    amount: MoneySchema
    payer_bank: str | None = Field(default=None, max_length=60, examples=["Bancolombia"])


class SimulateSaleResponse(BaseModel):
    sim_payment_id: uuid.UUID
    bre_b_reference: str
    status: Literal["processing", "completed", "failed"]
    processed: bool
    sale_id: uuid.UUID | None
