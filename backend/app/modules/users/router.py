from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.db import DbSession
from app.core.deps import CurrentUser, require_role
from app.integrations.op.client import OpClientDep
from app.modules.users import service
from app.modules.users.models import User
from app.modules.users.schemas import (
    AuthResponse,
    LoginRequest,
    Me,
    RegisterRequest,
    SetPayoutWalletRequest,
    UpdateMeRequest,
)

router = APIRouter(tags=["auth"])

WalletOwner = Annotated[User, Depends(require_role("merchant", "lender"))]


@router.post(
    "/auth/register", status_code=201, response_model=AuthResponse, summary="Registro de comercio o prestamista"
)
def register(body: RegisterRequest, db: DbSession) -> AuthResponse:
    user, token = service.register(db, body)
    return AuthResponse(access_token=token, user=service.build_me(db, user))


@router.post("/auth/login", response_model=AuthResponse, summary="Login (web y móvil)")
def login(body: LoginRequest, db: DbSession) -> AuthResponse:
    user, token = service.login(db, body)
    return AuthResponse(access_token=token, user=service.build_me(db, user))


@router.get("/me", response_model=Me, summary="Perfil del usuario")
def get_me(user: CurrentUser, db: DbSession) -> Me:
    return service.build_me(db, user)


@router.patch("/me", response_model=Me, summary='Editar nombres, ciudad y "Mi rango" (solo prestamista)')
def update_me(body: UpdateMeRequest, user: CurrentUser, db: DbSession) -> Me:
    return service.build_me(db, service.update_me(db, user, body))


@router.put("/me/payout-wallet", response_model=Me, summary="Vincular la wallet propia o de cobro")
def set_payout_wallet(body: SetPayoutWalletRequest, user: WalletOwner, db: DbSession, op: OpClientDep) -> Me:
    return service.build_me(db, service.set_payout_wallet(db, op, user, body.wallet_address))
