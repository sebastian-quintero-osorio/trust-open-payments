from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.deps import require_role
from app.modules.scoring import service
from app.modules.scoring.schemas import CreditProfileOut, MerchantCreditView
from app.modules.users.models import User

router = APIRouter(tags=["scoring"])

Merchant = Annotated[User, Depends(require_role("merchant"))]
Lender = Annotated[User, Depends(require_role("lender"))]


@router.get(
    "/me/credit-profile",
    response_model=CreditProfileOut,
    summary="Score, grado, elegibilidad y cupo del comercio",
)
def get_my_credit_profile(user: Merchant, db: DbSession) -> CreditProfileOut:
    return service.my_credit_profile(db, user)


@router.get(
    "/merchants/{merchant_id}/credit-profile",
    response_model=MerchantCreditView,
    summary="Perfil resumido de un comercio, para el prestamista",
)
def get_merchant_credit_profile(merchant_id: str, user: Lender, db: DbSession) -> MerchantCreditView:
    return service.merchant_credit_view(db, user, merchant_id)
