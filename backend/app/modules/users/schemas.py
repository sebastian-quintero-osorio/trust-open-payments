"""Objetos de referencia `MerchantRef` y `LenderRef`, reutilizados en todas las respuestas (00-core)."""

import uuid

from pydantic import BaseModel

from app.modules.users.models import User


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


def public_name(first_name: str, last_name: str) -> str:
    """Nombre más la inicial del apellido: "Camilo" + "Pardo" → "Camilo P." (sin apellido, solo el nombre)."""
    first, last = first_name.strip(), last_name.strip()
    return f"{first} {last[0].upper()}." if last else first


def merchant_ref(user: User) -> MerchantRef:
    business_name = user.business_name or ""  # un comercio siempre tiene business_name (CHECK de `users`)
    return MerchantRef(id=user.id, business_name=business_name, city=user.city, initials=initials(business_name))


def lender_ref(user: User) -> LenderRef:
    return LenderRef(id=user.id, public_name=public_name(user.first_name, user.last_name))
