from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.deps import require_role
from app.integrations.op.client import OpClientDep
from app.modules.admin import service
from app.modules.admin.schemas import AdminDashboard, AdminPayoutOut, RevokeResult
from app.modules.users.models import User

router = APIRouter(prefix="/admin", tags=["admin"])

Admin = Annotated[User, Depends(require_role("admin"))]  # R1: todo el router


@router.get(
    "/dashboard",
    response_model=AdminDashboard,
    summary="Ingresos de Trust, fondos retenidos y estado de pagos y créditos",
)
def get_admin_dashboard(user: Admin, db: DbSession) -> AdminDashboard:
    return service.dashboard(db)


@router.post(
    "/payouts/{payout_id}/retry",
    response_model=AdminPayoutOut,
    summary="Reintentar un pago atascado (`failed_final` o `held`)",
)
def retry_payout(payout_id: str, user: Admin, db: DbSession) -> AdminPayoutOut:
    return service.retry_payout(db, user, payout_id)


@router.post(
    "/collection-accounts/{collection_account_id}/revoke",
    response_model=RevokeResult,
    summary="Revocar una cuenta de recaudo (sus pagos pendientes quedan retenidos)",
)
def revoke_collection_account(collection_account_id: str, user: Admin, db: DbSession, op: OpClientDep) -> RevokeResult:
    return service.revoke_collection_account(db, op, user, collection_account_id)
