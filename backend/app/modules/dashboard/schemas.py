"""Schemas de los dashboards (13-dashboard; `MerchantDashboard` y `LenderDashboard` del YAML)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.core.clock import UtcDateTime
from app.core.money import MoneySchema
from app.modules.loans.schemas import LoanStatusOut
from app.modules.sales.schemas import TodaySummaryOut
from app.modules.users.schemas import LenderRef, MerchantRef, Range

NextAction = Literal[
    "link_wallet",
    "activate_qr",
    "accept_loan",
    "wait_disbursement",
    "view_loan",
    "wait_unlock",
    "view_offers",
    "request_credit",
]


# ── Comercio (COPY 13a) ───────────────────────────────────────────────────────


class MerchantGreeting(BaseModel):
    first_name: str
    business_name: str
    city: str
    initials: str


class CreditOut(BaseModel):
    cupo_total: MoneySchema
    cupo_disponible: MoneySchema
    score: int
    grade: Literal["A", "B", "C", "D"]
    eligible: bool
    unlocks_at: UtcDateTime | None


class ActiveLoanOut(BaseModel):
    id: uuid.UUID
    status: LoanStatusOut
    principal: MoneySchema
    pct_bps: int
    lender: LenderRef
    allocated: MoneySchema
    pct_paid_bps: int
    since: UtcDateTime | None
    is_late: bool


class CollectionAccountOut(BaseModel):
    qr_payload: str
    status: Literal["active", "revoked"]


class MerchantDashboard(BaseModel):
    greeting: MerchantGreeting
    unread_notifications: int
    credit: CreditOut
    active_loan: ActiveLoanOut | None
    today: TodaySummaryOut
    offers_fit_count: int
    collection_account: CollectionAccountOut | None
    next_action: NextAction


# ── Prestamista (COPY 13d) ────────────────────────────────────────────────────


class LenderGreeting(BaseModel):
    first_name: str
    public_name: str


class AbonosHoy(BaseModel):
    gross: MoneySchema
    trust_cut: MoneySchema
    net: MoneySchema


class AssetTotals(BaseModel):
    asset_code: str
    asset_scale: int
    total_prestado: MoneySchema
    abonos_hoy: AbonosHoy
    recibido_hoy: MoneySchema


class HourlyAbono(BaseModel):
    hour: str = Field(examples=["08:00"])
    amount: MoneySchema


class LenderLoanRow(BaseModel):
    id: uuid.UUID
    merchant: MerchantRef
    principal: MoneySchema
    pct_bps: int
    pct_paid_bps: int
    today_gross: MoneySchema
    status: LoanStatusOut
    is_late: bool


class LastRepayment(BaseModel):
    id: uuid.UUID
    paid_at: UtcDateTime
    merchant: MerchantRef
    gross: MoneySchema
    trust_cut: MoneySchema
    net: MoneySchema


class LenderDashboard(BaseModel):
    greeting: LenderGreeting
    unread_notifications: int
    active_loans_count: int
    totals: list[AssetTotals]
    mi_rango: Range | None
    abonos_por_hora: list[HourlyAbono]
    loans: list[LenderLoanRow]
    last_repayments: list[LastRepayment]
