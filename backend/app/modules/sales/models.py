"""Tablas `sales`, `repayments`, `payouts` y `webhook_events` (01-data-model; dueño 07-sales)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core import clock
from app.core.db import Base, CreatedAtMixin, IdMixin, JsonDict, TimestampsMixin, bigint, smallint, sql_in


class SaleSource(StrEnum):
    LIVE = "live"
    SEED = "seed"


class SaleStatus(StrEnum):
    PROCESSED = "processed"
    HELD = "held"


class PayoutLeg(StrEnum):
    LENDER = "lender"
    MERCHANT = "merchant"
    TRUST = "trust"


class PayoutStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED_FINAL = "failed_final"
    HELD = "held"
    SKIPPED = "skipped"


class WebhookSource(StrEnum):
    PUNTORED = "puntored"
    RECONCILIATION = "reconciliation"


class WebhookStatus(StrEnum):
    PROCESSED = "processed"
    UNMATCHED = "unmatched"
    IGNORED = "ignored"


class Sale(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "sales"
    __table_args__ = (
        CheckConstraint(sql_in("source", SaleSource), name="source_enum"),
        CheckConstraint(sql_in("status", SaleStatus), name="status_enum"),
        UniqueConstraint("collection_account_id", "bre_b_reference"),
        Index(None, "merchant_id", "paid_at"),
    )

    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    collection_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collection_accounts.id"))
    puntored_event_id: Mapped[str | None] = mapped_column(unique=True)
    bre_b_reference: Mapped[str]
    amount: Mapped[bigint]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]
    payer_bank: Mapped[str | None]
    incoming_payment_url: Mapped[str | None]
    paid_at: Mapped[datetime]
    source: Mapped[str]
    status: Mapped[str]


class Repayment(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "repayments"
    __table_args__ = (
        Index(None, "loan_id", "created_at"),
        Index(None, "lender_id", "created_at"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), unique=True)
    loan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loans.id"))
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    lender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    abono_amount: Mapped[bigint]
    trust_cut_amount: Mapped[bigint]
    lender_net_amount: Mapped[bigint]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]
    pct_applied_bps: Mapped[int]
    allocated_before: Mapped[bigint]
    allocated_after: Mapped[bigint]


class Payout(IdMixin, TimestampsMixin, Base):
    """Una pata de la división de una venta (`lender`, `merchant` o `trust`)."""

    __tablename__ = "payouts"
    __table_args__ = (
        CheckConstraint(sql_in("leg", PayoutLeg), name="leg_enum"),
        CheckConstraint(sql_in("status", PayoutStatus), name="status_enum"),
        UniqueConstraint("sale_id", "leg"),
        Index(None, "status", "next_attempt_at"),
    )

    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"))
    loan_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("loans.id"))
    leg: Mapped[str]
    receiver_wallet_url: Mapped[str | None]
    amount: Mapped[bigint]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]
    description: Mapped[str]
    status: Mapped[str]
    attempt: Mapped[int] = mapped_column(default=1, server_default="1")
    current_debit_amount: Mapped[bigint]
    sent_total: Mapped[bigint] = mapped_column(default=0, server_default="0")
    op_transfer_id: Mapped[uuid.UUID | None]
    receive_value: Mapped[bigint | None]
    receive_asset_code: Mapped[str | None]
    receive_asset_scale: Mapped[smallint | None]
    failure_code: Mapped[str | None]
    last_error: Mapped[str | None]
    next_attempt_at: Mapped[datetime | None]
    incoming_payment_url: Mapped[str | None]
    outgoing_payment_url: Mapped[str | None]
    completed_at: Mapped[datetime | None]


class WebhookEvent(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "webhook_events"
    __table_args__ = (
        CheckConstraint(sql_in("source", WebhookSource), name="source_enum"),
        CheckConstraint(sql_in("status", WebhookStatus), name="status_enum"),
    )

    event_id: Mapped[str] = mapped_column(unique=True)
    source: Mapped[str]
    type: Mapped[str]
    payload: Mapped[JsonDict]
    status: Mapped[str | None]  # se llena al terminar de procesar, en la misma transacción
    sale_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sales.id"))
    error: Mapped[str | None]
    received_at: Mapped[datetime] = mapped_column(default=clock.now)
    processed_at: Mapped[datetime | None]
