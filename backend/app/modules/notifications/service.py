"""Avisos in-app (14-notifications): `notify` en la transacción de quien llama (R1); lista y lectura (R5, R6)."""

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core import clock
from app.core.deps import PageParams, next_offset, parse_id
from app.core.errors import AppError
from app.modules.notifications.catalog import NOTIFICATION_TYPES
from app.modules.notifications.models import Notification
from app.modules.notifications.schemas import NotificationOut, NotificationPage, ReadAllResult
from app.modules.users.models import User

NOT_FOUND = "No encontramos esa notificación."
NOT_YOURS = "Esa notificación no es tuya."


def notify(
    db: Session, user_id: uuid.UUID, type: str, title: str, body: str, data: dict[str, Any] | None = None
) -> Notification:
    """Agrega el aviso a la transacción abierta. `type` fuera del catálogo de 00-core → `ValueError`."""
    if type not in NOTIFICATION_TYPES:
        raise ValueError(f"Tipo de aviso fuera del catálogo: {type!r}")
    notification = Notification(user_id=user_id, type=type, title=title, body=body, data=data or {})
    db.add(notification)
    return notification


def unread_count(db: Session, user_id: uuid.UUID) -> int:
    """14 R5: avisos del usuario con `read_at` null (lo usan `GET /me`, los dashboards de 13 y la lista)."""
    count = db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id, Notification.read_at.is_(None)
        )
    )
    return count or 0


def list_notifications(db: Session, user: User, page: PageParams) -> NotificationPage:
    """R5: solo las del usuario, `created_at` desc (empates por id desc); `unread_count` no depende de la página."""
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return NotificationPage(
        unread_count=unread_count(db, user.id),
        items=[NotificationOut.of(row) for row in rows],
        next_offset=next_offset(page.offset, page.limit, len(rows)),
    )


def read_all(db: Session, user: User) -> ReadAllResult:
    """R6: `read_at = now` en las del usuario sin leer; devuelve cuántas cambió (idempotente: la segunda da 0)."""
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=clock.now())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return ReadAllResult(updated=result.rowcount or 0)


def read_one(db: Session, user: User, notification_id: str) -> NotificationOut:
    """R6: solo el dueño (ajena → 403); si ya estaba leída, `read_at` no cambia."""
    notification = db.scalar(
        select(Notification)
        .where(Notification.id == parse_id(notification_id))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if notification is None:
        db.rollback()
        raise AppError(404, "not_found", NOT_FOUND)
    if notification.user_id != user.id:
        db.rollback()
        raise AppError(403, "forbidden", NOT_YOURS)
    if notification.read_at is None:
        notification.read_at = clock.now()
    db.commit()
    return NotificationOut.of(notification)
