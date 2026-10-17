"""Tabla `collection_accounts` (01-data-model; dueño 06-recaudo)."""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampsMixin, bigint, smallint, sql_in


class CollectionAccountStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class CollectionAccount(IdMixin, TimestampsMixin, Base):
    __tablename__ = "collection_accounts"
    __table_args__ = (
        CheckConstraint(sql_in("status", CollectionAccountStatus), name="status_enum"),
        Index(
            "uq_collection_accounts_active_merchant",
            "merchant_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    merchant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    op_collection_account_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    wallet_address: Mapped[str]
    qr_payload: Mapped[str]
    asset_code: Mapped[str]
    asset_scale: Mapped[smallint]
    status: Mapped[str]
    ledger_in: Mapped[bigint] = mapped_column(default=0, server_default="0")
    ledger_out: Mapped[bigint] = mapped_column(default=0, server_default="0")
    revoked_at: Mapped[datetime | None]
