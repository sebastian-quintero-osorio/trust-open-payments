from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.deps import Pagination, require_role
from app.modules.notifications import service
from app.modules.notifications.schemas import NotificationOut, NotificationPage, ReadAllResult
from app.modules.users.models import User

router = APIRouter(prefix="/notifications", tags=["notifications"])

AnyRole = Annotated[User, Depends(require_role("merchant", "lender", "admin"))]


@router.get("", response_model=NotificationPage, summary="Mis notificaciones (orden `created_at` desc)")
def list_notifications(user: AnyRole, db: DbSession, page: Pagination) -> NotificationPage:
    return service.list_notifications(db, user, page)


@router.post("/read-all", response_model=ReadAllResult, summary="Marcar todas como leídas")
def read_all_notifications(user: AnyRole, db: DbSession) -> ReadAllResult:
    return service.read_all(db, user)


@router.post("/{notification_id}/read", response_model=NotificationOut, summary="Marcar una como leída")
def read_notification(notification_id: str, user: AnyRole, db: DbSession) -> NotificationOut:
    return service.read_one(db, user, notification_id)
