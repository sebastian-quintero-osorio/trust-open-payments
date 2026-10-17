"""Tabla `waitlist` (01-data-model; dueño 15-waitlist)."""

from enum import StrEnum

from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, IdMixin, TimestampsMixin, sql_in


class WaitlistTipo(StrEnum):
    COMERCIO = "comercio"
    PRESTAMISTA = "prestamista"


class WaitlistEntry(IdMixin, TimestampsMixin, Base):
    __tablename__ = "waitlist"
    __table_args__ = (CheckConstraint(sql_in("tipo", WaitlistTipo), name="tipo_enum"),)

    tipo: Mapped[str]
    nombre: Mapped[str]
    correo: Mapped[str] = mapped_column(unique=True)  # guardado en minúsculas
    ciudad: Mapped[str]
