from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.core.db import DbSession
from app.core.deps import require_role
from app.integrations.op.client import OpClientDep
from app.modules.recaudo import service
from app.modules.recaudo.schemas import CollectionAccountOut
from app.modules.users.models import User

router = APIRouter(tags=["recaudo"])

Merchant = Annotated[User, Depends(require_role("merchant"))]


@router.get(
    "/me/collection-account",
    response_model=CollectionAccountOut,
    summary="Cuenta de recaudo del comercio (QR y saldo)",
)
def get_collection_account(user: Merchant, db: DbSession) -> CollectionAccountOut:
    return CollectionAccountOut.of(service.get_collection_account(db, user))


@router.post(
    "/me/collection-account",
    status_code=201,
    response_model=CollectionAccountOut,
    summary="Activar el QR (asigna una cuenta de recaudo)",
    responses={200: {"model": CollectionAccountOut, "description": "Ya existía"}},
)
def create_collection_account(
    user: Merchant, db: DbSession, op: OpClientDep, response: Response
) -> CollectionAccountOut:
    # R3: 200 si ya había una cuenta `active`; 201 si se asignó ahora (la firma de ensure no cambia).
    if service.active_account(db, user.id) is not None:
        response.status_code = 200
    return CollectionAccountOut.of(service.ensure_collection_account(db, op, user))
