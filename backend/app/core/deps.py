"""Dependencias compartidas (00-core, "API"): auth (`current_user`, `require_role`), paginación e ids de ruta."""

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.db import DbSession
from app.core.errors import AppError
from app.core.security import decode_access_token
from app.modules.users.models import User

# auto_error=False: sin token respondemos nosotros con 401 `unauthorized` y el formato único de error.
_bearer = HTTPBearer(auto_error=False)


def current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    if credentials is None:
        raise AppError(401, "unauthorized")
    claims = decode_access_token(credentials.credentials)
    user = db.get(User, claims.sub)
    if user is None:
        raise AppError(401, "unauthorized")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: str) -> Callable[[User], User]:
    """Dependencia que exige uno de los roles: `Depends(require_role("merchant"))`."""

    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise AppError(403, "forbidden")
        return user

    return dependency


class PageParams:
    """`?limit=` (50 por defecto, de 1 a 200) y `?offset=` (desde 0) de las listas `{items, next_offset}`."""

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        self.limit = limit
        self.offset = offset


Pagination = Annotated[PageParams, Depends()]


def next_offset(offset: int, limit: int, returned: int) -> int | None:
    """`next_offset` de una lista: el offset de la página siguiente si esta vino llena; si no, `None`."""
    return offset + limit if returned == limit else None


def parse_id(value: str) -> uuid.UUID:
    """Id de ruta (declarado `str`) → UUID. Un id que no es UUID válido da 404 `not_found`, nunca 422."""
    try:
        return uuid.UUID(value)
    except ValueError:
        raise AppError(404, "not_found") from None
