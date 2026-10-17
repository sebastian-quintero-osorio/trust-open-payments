"""Schemas de usuarios (04-users) y objetos de referencia `MerchantRef` / `LenderRef` (00-core)."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator

from app.core.money import MoneySchema
from app.modules.users.models import User

PASSWORD_MIN_BYTES = 8
PASSWORD_MAX_BYTES = 72  # límite de bcrypt

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# ── Referencias (00-core) ─────────────────────────────────────────────────────


class MerchantRef(BaseModel):
    id: uuid.UUID
    business_name: str
    city: str
    initials: str


class LenderRef(BaseModel):
    id: uuid.UUID
    public_name: str


def initials(business_name: str) -> str:
    """Primeras letras de las dos primeras palabras, en mayúscula: "Tienda La Esquina" → "TL" ("Ferretería" → "F")."""
    return "".join(word[0] for word in business_name.split()[:2]).upper()


def person_initials(first_name: str, last_name: str) -> str:
    """Inicial del nombre + inicial del apellido, en mayúscula: "Camilo" + "Pardo" → "CP" (04 R4)."""
    return "".join(name.strip()[0] for name in (first_name, last_name) if name.strip()).upper()


def public_name(first_name: str, last_name: str) -> str:
    """Nombre más la inicial del apellido: "Camilo" + "Pardo" → "Camilo P." (sin apellido, solo el nombre)."""
    first, last = first_name.strip(), last_name.strip()
    return f"{first} {last[0].upper()}." if last else first


def merchant_ref(user: User) -> MerchantRef:
    business_name = user.business_name or ""  # un comercio siempre tiene business_name (CHECK de `users`)
    return MerchantRef(id=user.id, business_name=business_name, city=user.city, initials=initials(business_name))


def lender_ref(user: User) -> LenderRef:
    return LenderRef(id=user.id, public_name=public_name(user.first_name, user.last_name))


# ── Auth (04 R1–R3) ───────────────────────────────────────────────────────────


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    role: Literal["merchant", "lender"]
    first_name: NonEmptyStr
    last_name: NonEmptyStr
    city: NonEmptyStr
    business_name: NonEmptyStr | None = None

    @field_validator("password")
    @classmethod
    def _password_bytes(cls, value: str) -> str:
        if not PASSWORD_MIN_BYTES <= len(value.encode()) <= PASSWORD_MAX_BYTES:
            raise ValueError(f"La contraseña debe tener entre {PASSWORD_MIN_BYTES} y {PASSWORD_MAX_BYTES} bytes.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── Perfil (04 R4, R5) ────────────────────────────────────────────────────────


class PayoutWallet(BaseModel):
    url: str
    public_name: str | None
    asset_code: str
    asset_scale: int


class Range(BaseModel):
    """"Mi rango" del prestamista: dos Money del asset de mercado."""

    min: MoneySchema
    max: MoneySchema


class CollectionAccountSummary(BaseModel):
    id: uuid.UUID
    status: Literal["active", "revoked"]
    qr_payload: str


class Me(BaseModel):
    id: uuid.UUID
    email: str
    role: Literal["merchant", "lender", "admin"]
    first_name: str
    last_name: str
    business_name: str | None
    city: str
    initials: str
    public_name: str
    payout_wallet: PayoutWallet | None
    lender_range: Range | None
    collection_account: CollectionAccountSummary | None
    unread_notifications: int


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: Me


class UpdateMeRequest(BaseModel):
    """PATCH /me (04 R6). `business_name` y `email` no se editan: si llegan, se ignoran."""

    first_name: NonEmptyStr | None = None
    last_name: NonEmptyStr | None = None
    city: NonEmptyStr | None = None
    lender_range: Range | None = None  # `null` explícito lo borra; ausente no lo toca (`model_fields_set`)

    @field_validator("first_name", "last_name", "city", mode="before")
    @classmethod
    def _not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("No puede estar vacío.")
        return value


class SetPayoutWalletRequest(BaseModel):
    wallet_address: str = Field(examples=["$ilp.interledger-test.dev/tienda-la-esquina"])
