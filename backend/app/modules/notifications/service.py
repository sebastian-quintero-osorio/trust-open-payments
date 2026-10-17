"""Avisos in-app (14-notifications R1): `notify` escribe en la transacción de quien llama (sin commit)."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.modules.notifications.catalog import NOTIFICATION_TYPES
from app.modules.notifications.models import Notification


def notify(
    db: Session, user_id: uuid.UUID, type: str, title: str, body: str, data: dict[str, Any] | None = None
) -> Notification:
    """Agrega el aviso a la transacción abierta. `type` fuera del catálogo de 00-core → `ValueError`."""
    if type not in NOTIFICATION_TYPES:
        raise ValueError(f"Tipo de aviso fuera del catálogo: {type!r}")
    notification = Notification(user_id=user_id, type=type, title=title, body=body, data=data or {})
    db.add(notification)
    return notification
