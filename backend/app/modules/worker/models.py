"""Tabla `op_cursors` (01-data-model; dueño 08-worker). Sin `id` ni `created_at`."""

from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from app.core import clock
from app.core.db import Base, bigint


class OpCursor(Base):
    __tablename__ = "op_cursors"

    name: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[bigint]
    updated_at: Mapped[datetime] = mapped_column(default=clock.now, onupdate=clock.now)
