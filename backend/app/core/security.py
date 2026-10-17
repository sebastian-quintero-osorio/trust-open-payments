"""Contraseñas con bcrypt y tokens JWT HS256 con claims `sub`, `role` y `exp` (00-core, "Auth")."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError

JWT_ALGORITHM = "HS256"
BCRYPT_ROUNDS = 12  # conftest lo baja para que los tests sean rápidos


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


@dataclass(frozen=True)
class TokenClaims:
    sub: uuid.UUID
    role: str
    exp: datetime


def create_access_token(user_id: uuid.UUID | str, role: str) -> str:
    expires = clock.now() + timedelta(hours=get_settings().jwt_expires_hours)
    payload = {"sub": str(user_id), "role": str(role), "exp": int(expires.timestamp())}
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> TokenClaims:
    """Valida firma y vencimiento contra `clock.now()` (así un reloj congelado no invalida los tokens)."""
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "role", "exp"], "verify_exp": False},
        )
        claims = TokenClaims(
            sub=uuid.UUID(payload["sub"]),
            role=str(payload["role"]),
            exp=datetime.fromtimestamp(int(payload["exp"]), UTC),
        )
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise AppError(401, "unauthorized") from exc
    if claims.exp <= clock.now():
        raise AppError(401, "unauthorized", "Tu sesión venció. Inicia sesión de nuevo.")
    return claims
