"""Tablas `loan_requests` y `loan_offers` (01-data-model; dueño 10-marketplace)."""

import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampsMixin, bigint, smallint, sql_in


class RequestStatus(StrEnum):
    OPEN = "open"
    MATCHED = "matched"
    CANCELLED = "cancelled"


class OfferStatus(StrEnum):
    OPEN = "open"
    TAKEN = "taken"
    CANCELLED = "cancelled"


class LoanRequest(IdMixin, TimestampsMixin, Base):
    __tablename__ = "loan_requests"
    __table_args__ = (
        CheckConstraint("0 < min_amount AND min_amount <= max_amount", name="amount_range"),
        CheckConstraint(sql_in("status", RequestStatus), name="status_enum"),
        Index(
            "uq_loan_requests_open_merchant",
            "merchant_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    min_amount: Mapped[bigint]
    max_amount: Mapped[bigint]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]
    pct_bps: Mapped[int]
    status: Mapped[str]
    credit_assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("credit_assessments.id"))


class LoanOffer(IdMixin, TimestampsMixin, Base):
    __tablename__ = "loan_offers"
    __table_args__ = (
        CheckConstraint("0 < min_amount AND min_amount <= max_amount", name="amount_range"),
        CheckConstraint("fee_bps >= 0", name="fee_bps_non_negative"),
        CheckConstraint("term_months >= 1", name="term_months_positive"),
        CheckConstraint(sql_in("status", OfferStatus), name="status_enum"),
    )

    lender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    min_amount: Mapped[bigint]
    max_amount: Mapped[bigint]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]
    pct_bps: Mapped[int]
    fee_bps: Mapped[int]
    term_months: Mapped[int]
    status: Mapped[str]
