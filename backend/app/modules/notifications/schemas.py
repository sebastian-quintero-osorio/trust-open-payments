"""Schemas de avisos (14-notifications; `Notification` del YAML)."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.modules.notifications.catalog import NOTIFICATION_TYPES
from app.modules.notifications.models import Notification

NotificationType = Literal[NOTIFICATION_TYPES]  # el catálogo de 00-core (enum `NotificationType` del YAML)


class NotificationOut(BaseModel):
    id: uuid.UUID
    type: NotificationType
    title: str
    body: str
    data: dict[str, Any]  # ids y Money crudos, tal como los guardó el emisor (14 R2)
    read_at: UtcDateTime | None
    created_at: UtcDateTime

    @classmethod
    def of(cls, notification: Notification) -> "NotificationOut":
        return cls(
            id=notification.id,
            type=notification.type,
            title=notification.title,
            body=notification.body,
            data=notification.data or {},
            read_at=notification.read_at,
            created_at=notification.created_at,
        )


class NotificationPage(BaseModel):
    unread_count: int
    items: list[NotificationOut]
    next_offset: int | None


class ReadAllResult(BaseModel):
    unread_count: Literal[0] = 0
    updated: int
