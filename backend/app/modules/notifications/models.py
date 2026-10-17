"""Tabla `notifications` (01-data-model; dueño 14-notifications). Sin `updated_at`."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, CreatedAtMixin, IdMixin, JsonDict


class Notification(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str]  # catálogo en 00-core ("Auditoría y notificaciones")
    title: Mapped[str]
    body: Mapped[str]
    data: Mapped[JsonDict] = mapped_column(default=dict)
    read_at: Mapped[datetime | None]


Index("ix_notifications_user_id_created_at", Notification.user_id, Notification.created_at.desc())
