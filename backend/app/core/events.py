"""Auditoría: tabla `events` y `record_event` (00-core, "Auditoría y notificaciones")."""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base, CreatedAtMixin, IdMixin, JsonDict


class Event(IdMixin, CreatedAtMixin, Base):
    __tablename__ = "events"
    __table_args__ = (Index(None, "entity_type", "entity_id", "created_at"),)

    entity_type: Mapped[str]
    entity_id: Mapped[uuid.UUID]
    type: Mapped[str]
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    data: Mapped[JsonDict] = mapped_column(default=dict)


def record_event(
    db: Session,
    entity_type: str,
    entity_id: uuid.UUID,
    type: str,
    data: dict[str, Any] | None = None,
    actor_id: uuid.UUID | None = None,
) -> Event:
    """Agrega el evento a la transacción abierta; el commit lo hace quien cambia el estado."""
    event = Event(entity_type=entity_type, entity_id=entity_id, type=type, data=data or {}, actor_id=actor_id)
    db.add(event)
    return event
