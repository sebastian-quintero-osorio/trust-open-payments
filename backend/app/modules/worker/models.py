"""Tabla `op_cursors` (01-data-model; dueño 08-worker). Sin `id` ni `created_at`."""

from datetime import datetime

from sqlalchemy.orm import Mapped, mapped_column

from app.core import clock
from app.core.db import Base, bigint

SIM_QR_CURSOR = "sim_qr_payments"  # último `seq` reconciliado del simulador (08 R8; 02 R15 lo lee al arrancar)
DAILY_CLOSE_CURSOR = "daily_close"  # último día local (`YYYYMMDD`) con cierre diario hecho (08 R9)


class OpCursor(Base):
    __tablename__ = "op_cursors"

    name: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[bigint]
    updated_at: Mapped[datetime] = mapped_column(default=clock.now, onupdate=clock.now)
