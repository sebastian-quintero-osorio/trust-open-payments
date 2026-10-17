"""Cuenta de recaudo de cada negocio (06-recaudo): lectura y `ensure_collection_account` (R1–R6)."""

import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.events import Event, record_event
from app.integrations.op import schemas as op_schemas
from app.integrations.op.client import OpClient, OpError, OpUnavailable
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus
from app.modules.users.models import User

logger = logging.getLogger(__name__)

WALLET_REQUIRED = "Vincula la wallet de tu negocio antes de activar tu QR de cobro."
POOL_EXHAUSTED = "No hay cuentas de recaudo disponibles en este momento. Intenta de nuevo más tarde."
NO_ACCOUNT = "Todavía no tienes una cuenta de recaudo. Activa tu QR para empezar a cobrar."
POOL_EXHAUSTED_EVENT = "collection_account_pool_exhausted"


def external_ref(merchant_id: uuid.UUID) -> str:
    """`external_ref` del negocio en op-service (también lo usa el arranque del fake, 02 R15)."""
    return f"merchant:{merchant_id}"


def active_account(db: Session, merchant_id: uuid.UUID) -> CollectionAccount | None:
    return db.scalar(
        select(CollectionAccount).where(
            CollectionAccount.merchant_id == merchant_id, CollectionAccount.status == CollectionAccountStatus.ACTIVE
        )
    )


def current_account(db: Session, merchant_id: uuid.UUID) -> CollectionAccount | None:
    """R1: la cuenta `active` del negocio; si no hay, la más reciente (`revoked`); si nunca tuvo, `None`."""
    return db.scalar(
        select(CollectionAccount)
        .where(CollectionAccount.merchant_id == merchant_id)
        .order_by(
            (CollectionAccount.status == CollectionAccountStatus.ACTIVE).desc(),
            CollectionAccount.created_at.desc(),
            CollectionAccount.id.desc(),
        )
        .limit(1)
    )


def get_collection_account(db: Session, merchant: User) -> CollectionAccount:
    """`GET /me/collection-account`: solo lee la base, nunca llama a Go."""
    account = current_account(db, merchant.id)
    if account is None:
        raise AppError(404, "not_found", NO_ACCOUNT)
    return account


def idempotency_key(db: Session, merchant_id: uuid.UUID) -> str:
    """R4.3: `merchant:{id}:recaudo`; con `n` = cuentas revocadas + intentos terminados en `pool_exhausted`,
    `merchant:{id}:recaudo:{n+1}` (Go guarda 72 h la respuesta de la clave anterior, también la 409 definitiva)."""
    revoked = db.scalar(
        select(func.count())
        .select_from(CollectionAccount)
        .where(
            CollectionAccount.merchant_id == merchant_id,
            CollectionAccount.status == CollectionAccountStatus.REVOKED,
        )
    )
    exhausted = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.entity_type == "user", Event.entity_id == merchant_id, Event.type == POOL_EXHAUSTED_EVENT)
    )
    n = revoked + exhausted
    base = f"{external_ref(merchant_id)}:recaudo"
    return f"{base}:{n + 1}" if n else base


def _op_failure(exc: OpError | OpUnavailable) -> AppError:
    if isinstance(exc, OpUnavailable):
        return AppError(503, "op_unavailable")
    logger.warning("op-service rechazó la cuenta de recaudo: %r", exc)
    return AppError(502, "op_unavailable")


def _recover_assigned(op: OpClient, ref: str) -> op_schemas.CollectionAccount:
    """R4.5: Go ya tiene una cuenta `active` para el negocio (otra clave o la base perdió la fila): se recupera."""
    try:
        found = op.find_collection_accounts(ref)
    except (OpError, OpUnavailable) as exc:
        raise _op_failure(exc) from exc
    account = next((a for a in found if a.status == "active"), None)
    if account is None:
        logger.error("op-service dijo already_assigned para %s pero no devuelve ninguna cuenta active", ref)
        raise AppError(502, "op_unavailable")
    return account


def _record_pool_exhausted(db: Session, merchant_id: uuid.UUID, key: str) -> None:
    """R4.5: deja constancia del intento para que el próximo use otra clave (Go repetiría la 409 por 72 h)."""
    record_event(db, "user", merchant_id, POOL_EXHAUSTED_EVENT, {"idempotency_key": key}, actor_id=merchant_id)
    db.commit()


def _assign_in_go(
    db: Session, op: OpClient, merchant_id: uuid.UUID, body: op_schemas.CreateCollectionAccountRequest, key: str
) -> op_schemas.CollectionAccount:
    try:
        return op.create_collection_account(body, idempotency_key=key)
    except OpError as exc:
        if exc.status == 409 and exc.code == "already_assigned":
            return _recover_assigned(op, body.external_ref)
        if exc.status == 409 and exc.code == "pool_exhausted":
            _record_pool_exhausted(db, merchant_id, key)  # transacción nueva: no se crea ninguna cuenta
            raise AppError(409, "pool_exhausted", POOL_EXHAUSTED) from exc
        raise _op_failure(exc) from exc
    except OpUnavailable as exc:
        raise _op_failure(exc) from exc


def ensure_collection_account(db: Session, op: OpClient, merchant: User) -> CollectionAccount:
    """R4: la cuenta `active` del negocio, creándola en Go si no tiene. Idempotente; hace commit por dentro (R5).

    Nunca llama a Go con una transacción abierta y, si Go falla, no guarda ninguna cuenta (con `pool_exhausted`
    solo el evento que cambia la clave del próximo intento).
    """
    existing = active_account(db, merchant.id)
    if existing is not None:  # 1. ya tiene: sin llamar a Go
        return existing
    if not merchant.payout_wallet_url:  # 2.
        raise AppError(422, "wallet_required", WALLET_REQUIRED)
    merchant_id = merchant.id
    settings = get_settings()
    key = idempotency_key(db, merchant_id)  # 3.
    body = op_schemas.CreateCollectionAccountRequest(
        external_ref=external_ref(merchant_id),
        display_name=(merchant.business_name or "")[:100],
        asset_code=settings.market_asset_code,
    )
    db.commit()  # 4. no queda transacción abierta durante la llamada a Go

    remote = _assign_in_go(db, op, merchant_id, body, key)  # 4. y 5.

    market = (settings.market_asset_code, settings.market_asset_scale)
    if (remote.asset_code, remote.asset_scale) != market:  # R6: se guarda igual; /health queda degraded
        logger.error(
            "market_asset_mismatch: op-service asignó a %s la cuenta %s en %s/%s (se esperaba %s/%s)",
            body.external_ref, remote.id, remote.asset_code, remote.asset_scale, *market,
        )

    account = CollectionAccount(  # 6. transacción nueva
        merchant_id=merchant_id,
        op_collection_account_id=remote.id,
        wallet_address=remote.wallet_address,
        qr_payload=remote.qr_payload,
        asset_code=remote.asset_code,
        asset_scale=remote.asset_scale,
        status=CollectionAccountStatus.ACTIVE,
    )
    try:
        db.add(account)
        db.flush()
        record_event(
            db,
            "collection_account",
            account.id,
            "assigned",
            {"op_id": str(remote.id), "wallet_address": remote.wallet_address},
            actor_id=merchant_id,
        )
        db.commit()
    except IntegrityError:  # 7. otro POST simultáneo guardó la misma cuenta (mismo replay de Go)
        db.rollback()
        winner = active_account(db, merchant_id)
        if winner is None:
            logger.error("No se guardó la cuenta de recaudo %s de %s y no hay otra active", remote.id, merchant_id)
            raise AppError(502, "op_unavailable") from None
        return winner
    return account
