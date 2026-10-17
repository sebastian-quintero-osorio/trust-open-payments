"""Tabla `users` (01-data-model; dueño 04-users)."""

from enum import StrEnum

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampsMixin, bigint, smallint, sql_in


class UserRole(StrEnum):
    MERCHANT = "merchant"
    LENDER = "lender"
    ADMIN = "admin"


class User(IdMixin, TimestampsMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(sql_in("role", UserRole), name="role_enum"),
        CheckConstraint("role <> 'merchant' OR business_name IS NOT NULL", name="merchant_business_name"),
    )

    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    role: Mapped[str]
    first_name: Mapped[str]
    last_name: Mapped[str]
    business_name: Mapped[str | None]
    city: Mapped[str]
    payout_wallet_url: Mapped[str | None]
    payout_wallet_public_name: Mapped[str | None]
    payout_asset_code: Mapped[str | None]
    payout_asset_scale: Mapped[smallint | None]
    lender_range_min: Mapped[bigint | None]
    lender_range_max: Mapped[bigint | None]
