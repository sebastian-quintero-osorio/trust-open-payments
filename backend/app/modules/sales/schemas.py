"""Schemas de ventas (07-sales; `SalesToday`, `SaleRow`, `SaleDetail`, `PayoutView` del YAML)."""

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.core.money import Money, MoneySchema
from app.modules.sales.models import Payout, Repayment, Sale

DeliveryStatus = Literal["in_progress", "completed", "failed", "held"]


class WebhookAck(BaseModel):
    status: Literal["processed", "duplicate", "unmatched", "ignored"]


class SaleRow(BaseModel):
    sale_id: uuid.UUID
    paid_at: UtcDateTime
    amount: MoneySchema
    merchant_amount: MoneySchema
    abono_amount: MoneySchema
    payout_status: DeliveryStatus


class TodaySummaryOut(BaseModel):
    sales_total: MoneySchema
    sales_count: int
    merchant_total: MoneySchema
    abonado_total: MoneySchema


class SplitRule(BaseModel):
    merchant_bps: int
    lender_bps: int


class SalesToday(BaseModel):
    date: date
    live: Literal[True] = True
    summary: TodaySummaryOut
    rule: SplitRule
    wallet_today: MoneySchema
    rows: list[SaleRow]
    next_since: str | None  # cursor con microsegundos (R9); se serializa a mano, nunca con el formato corto


class SalesPage(BaseModel):
    items: list[SaleRow]
    next_offset: int | None


class PayoutView(BaseModel):
    """Una pata con su estado de entrega (también la usan 12-repayments y 16-admin)."""

    id: uuid.UUID
    leg: Literal["lender", "merchant", "trust"]
    amount: MoneySchema
    sent_total: MoneySchema
    status: Literal["pending", "processing", "completed", "failed_final", "held", "skipped"]
    attempt: int
    receive: MoneySchema | None
    incoming_payment_url: str | None
    outgoing_payment_url: str | None
    completed_at: UtcDateTime | None
    failure_code: str | None

    @classmethod
    def of(cls, payout: Payout) -> "PayoutView":
        receive = None
        if payout.receive_value is not None and payout.receive_asset_code is not None:
            receive = MoneySchema.of(
                Money(payout.receive_value, payout.receive_asset_code, payout.receive_asset_scale or 0)
            )
        return cls(
            id=payout.id,
            leg=payout.leg,
            amount=MoneySchema.of(Money(payout.amount, payout.asset_code, payout.asset_scale)),
            sent_total=MoneySchema.of(Money(payout.sent_total, payout.asset_code, payout.asset_scale)),
            status=payout.status,
            attempt=payout.attempt,
            receive=receive,
            incoming_payment_url=payout.incoming_payment_url,
            outgoing_payment_url=payout.outgoing_payment_url,
            completed_at=payout.completed_at,
            failure_code=payout.failure_code,
        )


class SaleInfo(BaseModel):
    id: uuid.UUID
    amount: MoneySchema
    paid_at: UtcDateTime
    bre_b_reference: str
    payer_bank: str | None
    incoming_payment_url: str | None
    source: Literal["live", "seed"]
    status: Literal["processed", "held"]

    @classmethod
    def of(cls, sale: Sale) -> "SaleInfo":
        return cls(
            id=sale.id,
            amount=MoneySchema.of(Money(sale.amount, sale.asset_code, sale.asset_scale)),
            paid_at=sale.paid_at,
            bre_b_reference=sale.bre_b_reference,
            payer_bank=sale.payer_bank,
            incoming_payment_url=sale.incoming_payment_url,
            source=sale.source,
            status=sale.status,
        )


class RepaymentInfo(BaseModel):
    id: uuid.UUID
    loan_id: uuid.UUID
    abono: MoneySchema
    trust_cut: MoneySchema
    lender_net: MoneySchema
    pct_applied_bps: int

    @classmethod
    def of(cls, repayment: Repayment) -> "RepaymentInfo":
        def money(value: int) -> MoneySchema:
            return MoneySchema.of(Money(value, repayment.asset_code, repayment.asset_scale))

        return cls(
            id=repayment.id,
            loan_id=repayment.loan_id,
            abono=money(repayment.abono_amount),
            trust_cut=money(repayment.trust_cut_amount),
            lender_net=money(repayment.lender_net_amount),
            pct_applied_bps=repayment.pct_applied_bps,
        )


class SaleDetail(BaseModel):
    sale: SaleInfo
    repayment: RepaymentInfo | None
    payouts: list[PayoutView]


# Schema del body del webhook para `/docs` (R2: la ruta no usa un modelo Pydantic, así nunca responde 422). Copia
# en línea de `PuntoredPaymentEvent` del YAML, sin `$ref` a componentes que la app no define.
_MONEY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["value", "asset_code", "asset_scale"],
    "properties": {
        "value": {"type": "string", "pattern": "^[1-9][0-9]*$"},
        "asset_code": {"type": "string"},
        "asset_scale": {"type": "integer", "minimum": 0, "maximum": 255},
    },
}
PUNTORED_EVENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["event_id", "type", "occurred_at", "data"],
    "properties": {
        "event_id": {"type": "string", "format": "uuid"},
        "type": {"type": "string", "examples": ["qr_payment.received"]},
        "occurred_at": {"type": "string", "format": "date-time"},
        "data": {
            "type": "object",
            "required": ["collection_account_id", "collection_wallet_address", "amount", "bre_b_reference"],
            "properties": {
                "collection_account_id": {"type": "string", "format": "uuid"},
                "collection_wallet_address": {"type": "string", "format": "uri"},
                "amount": _MONEY_SCHEMA,
                "bre_b_reference": {"type": "string"},
                "payer_bank": {"type": ["string", "null"]},
                "incoming_payment_url": {"type": ["string", "null"], "format": "uri"},
            },
        },
    },
}
