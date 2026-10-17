"""Tablas `loans` y `loan_pct_changes` (01-data-model; dueño 11-loans)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAtMixin, IdMixin, JsonDict, TimestampsMixin, bigint, smallint, sql_in


class LoanOrigin(StrEnum):
    OFFER_TAKEN = "offer_taken"
    REQUEST_FUNDED = "request_funded"
    MATCH = "match"


class LoanStatus(StrEnum):
    PENDING_ACCEPTANCE = "pending_acceptance"
    PENDING_DISBURSEMENT = "pending_disbursement"
    DISBURSING = "disbursing"
    ACTIVE = "active"
    PAID = "paid"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


NON_TERMINAL_LOAN_STATUSES = (
    LoanStatus.PENDING_ACCEPTANCE,
    LoanStatus.PENDING_DISBURSEMENT,
    LoanStatus.DISBURSING,
    LoanStatus.ACTIVE,
)
TERMINAL_LOAN_STATUSES = (LoanStatus.PAID, LoanStatus.CANCELLED, LoanStatus.REJECTED, LoanStatus.EXPIRED)
# Estados que liberan la solicitud y la oferta (vuelven a `open`).
RELEASING_LOAN_STATUSES = (LoanStatus.CANCELLED, LoanStatus.REJECTED, LoanStatus.EXPIRED)


class PctChangeReason(StrEnum):
    MISSED_MINIMUM = "missed_minimum"
    AHEAD_OF_SCHEDULE = "ahead_of_schedule"
    UNCHANGED = "unchanged"
    MANUAL = "manual"


_NOT_RELEASED = f"NOT ({sql_in('status', RELEASING_LOAN_STATUSES)})"


class Loan(IdMixin, TimestampsMixin, Base):
    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint(sql_in("origin", LoanOrigin), name="origin_enum"),
        CheckConstraint(sql_in("status", LoanStatus), name="status_enum"),
        CheckConstraint("allocated_total <= total_repayable", name="allocated_within_total"),
        Index(
            "uq_loans_open_merchant",
            "merchant_id",
            unique=True,
            postgresql_where=text(sql_in("status", NON_TERMINAL_LOAN_STATUSES)),
        ),
        Index(
            "uq_loans_offer",
            "offer_id",
            unique=True,
            postgresql_where=text(f"offer_id IS NOT NULL AND {_NOT_RELEASED}"),
        ),
        Index(
            "uq_loans_request",
            "request_id",
            unique=True,
            postgresql_where=text(f"request_id IS NOT NULL AND {_NOT_RELEASED}"),
        ),
    )

    # Partes
    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    lender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    request_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("loan_requests.id"))
    offer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("loan_offers.id"))
    origin: Mapped[str]
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    credit_assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_assessments.id"))

    # Congelado al crear
    collection_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collection_accounts.id"))
    merchant_payout_wallet_url: Mapped[str]
    lender_wallet_url: Mapped[str]
    lender_asset_code: Mapped[str]
    lender_asset_scale: Mapped[smallint]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]

    # Términos (inmutables)
    principal: Mapped[bigint]
    fee_bps: Mapped[int]
    fee_amount: Mapped[bigint]
    total_repayable: Mapped[bigint]
    trust_share_bps: Mapped[int]
    term_months: Mapped[int]
    monthly_minimum: Mapped[bigint]
    pct_initial_bps: Mapped[int]
    pct_min_bps: Mapped[int]
    pct_max_bps: Mapped[int]
    terms: Mapped[JsonDict]

    # Estado
    status: Mapped[str]
    pct_bps: Mapped[int]
    is_late: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    missed_periods: Mapped[int] = mapped_column(default=0, server_default="0")
    period_start: Mapped[datetime | None]
    next_review_at: Mapped[datetime | None]

    # Contabilidad
    allocated_total: Mapped[bigint] = mapped_column(default=0, server_default="0")
    delivered_total: Mapped[bigint] = mapped_column(default=0, server_default="0")
    trust_earned: Mapped[bigint] = mapped_column(default=0, server_default="0")
    trust_pending: Mapped[bigint] = mapped_column(default=0, server_default="0")

    # Desembolso
    op_disbursement_payment_id: Mapped[uuid.UUID | None]
    disbursement_attempt: Mapped[int] = mapped_column(default=0, server_default="0")
    disbursement_authorization_url: Mapped[str | None]
    disbursement_authorization_expires_at: Mapped[datetime | None]

    # Tiempos
    expires_at: Mapped[datetime | None]
    accepted_at: Mapped[datetime | None]
    disbursement_started_at: Mapped[datetime | None]
    activated_at: Mapped[datetime | None]
    paid_at: Mapped[datetime | None]
    cancelled_at: Mapped[datetime | None]
    rejected_at: Mapped[datetime | None]
    expired_at: Mapped[datetime | None]


class LoanPctChange(IdMixin, CreatedAtMixin, Base):
    """Una fila por cada revisión de periodo (también `unchanged`). Sin `updated_at`."""

    __tablename__ = "loan_pct_changes"
    __table_args__ = (CheckConstraint(sql_in("reason", PctChangeReason), name="reason_enum"),)

    loan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("loans.id"))
    old_pct_bps: Mapped[int]
    new_pct_bps: Mapped[int]
    reason: Mapped[str]
    period_start: Mapped[datetime]
    period_end: Mapped[datetime]
    allocated_in_period: Mapped[bigint]
    target_amount: Mapped[bigint]
