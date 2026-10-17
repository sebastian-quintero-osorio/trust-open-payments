from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.deps import require_role
from app.modules.dashboard import service
from app.modules.dashboard.schemas import LenderDashboard, MerchantDashboard
from app.modules.users.models import User

router = APIRouter(tags=["dashboard"])

Merchant = Annotated[User, Depends(require_role("merchant"))]
Lender = Annotated[User, Depends(require_role("lender"))]


@router.get("/dashboard/merchant", response_model=MerchantDashboard, summary='Inicio del comercio (pantalla "Tu cupo")')
def get_merchant_dashboard(user: Merchant, db: DbSession) -> MerchantDashboard:
    return service.merchant_dashboard(db, user)


@router.get(
    "/dashboard/lender", response_model=LenderDashboard, summary='Inicio del prestamista (pantalla "Mi portafolio")'
)
def get_lender_dashboard(user: Lender, db: DbSession) -> LenderDashboard:
    return service.lender_dashboard(db, user)
