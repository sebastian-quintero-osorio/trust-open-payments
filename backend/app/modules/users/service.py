"""Registro, login, perfil `/me` y wallet propia (04-users)."""

import logging
import uuid

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.events import record_event
from app.core.money import MoneySchema, market_money
from app.integrations.op.client import OpClient, OpError, OpUnavailable
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, Loan
from app.modules.notifications.models import Notification
from app.modules.recaudo.service import current_account
from app.modules.users.models import User, UserRole
from app.modules.users.schemas import (
    PASSWORD_MAX_BYTES,
    CollectionAccountSummary,
    LoginRequest,
    Me,
    PayoutWallet,
    Range,
    RegisterRequest,
    UpdateMeRequest,
    initials,
    person_initials,
    public_name,
)

logger = logging.getLogger(__name__)

INVALID_CREDENTIALS = "El correo o la contraseña no son correctos."
OP_WALLET_ERRORS = {
    "wallet_not_found": "No encontramos esa wallet. Revisa la dirección.",
    "invalid_url": "La dirección de la wallet no es válida. Usa https://… o $host/ruta.",
}

_dummy_hash: str | None = None


def _validation_error(field: str, message: str) -> AppError:
    return AppError(422, "validation_error", details={"fields": [{"field": field, "message": message}]})


# ── Registro y login (R1–R3) ──────────────────────────────────────────────────


def register(db: Session, body: RegisterRequest) -> tuple[User, str]:
    """R2: crea un comercio o prestamista y devuelve `(usuario, token)`. Email repetido → 409 `already_exists`."""
    email = body.email.lower()
    is_merchant = body.role == UserRole.MERCHANT
    if is_merchant and not body.business_name:
        raise _validation_error("business_name", "El nombre del negocio es obligatorio para comercios.")
    if db.scalar(select(exists().where(User.email == email))):  # se guarda en minúsculas (R1)
        raise AppError(409, "already_exists", "Ya existe una cuenta con ese correo.")
    user = User(
        email=email,
        password_hash=security.hash_password(body.password),
        role=body.role,
        first_name=body.first_name,
        last_name=body.last_name,
        business_name=body.business_name if is_merchant else None,
        city=body.city,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # dos registros simultáneos con el mismo correo
        db.rollback()
        raise AppError(409, "already_exists", "Ya existe una cuenta con ese correo.") from None
    return user, security.create_access_token(user.id, user.role)


def _dummy_password_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = security.hash_password("trust-dummy-password")
    return _dummy_hash


def login(db: Session, body: LoginRequest) -> tuple[User, str]:
    """R1, R3: email insensible a mayúsculas; mismo 401 `invalid_credentials` para email o contraseña errados."""
    if len(body.password.encode()) > PASSWORD_MAX_BYTES:  # sin llamar a bcrypt
        raise AppError(401, "invalid_credentials", INVALID_CREDENTIALS)
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None:
        security.verify_password(body.password, _dummy_password_hash())  # mismo tiempo que con un email real
        raise AppError(401, "invalid_credentials", INVALID_CREDENTIALS)
    if not security.verify_password(body.password, user.password_hash):
        raise AppError(401, "invalid_credentials", INVALID_CREDENTIALS)
    return user, security.create_access_token(user.id, user.role)


# ── Perfil (R4, R5) ───────────────────────────────────────────────────────────


def build_me(db: Session, user: User) -> Me:
    is_merchant = user.role == UserRole.MERCHANT
    payout_wallet = None
    if user.payout_wallet_url is not None:
        payout_wallet = PayoutWallet(
            url=user.payout_wallet_url,
            public_name=user.payout_wallet_public_name,
            asset_code=user.payout_asset_code or "",
            asset_scale=user.payout_asset_scale if user.payout_asset_scale is not None else 0,
        )
    lender_range = None
    if user.role == UserRole.LENDER and user.lender_range_min is not None and user.lender_range_max is not None:
        lender_range = Range(
            min=MoneySchema.of(market_money(user.lender_range_min)),
            max=MoneySchema.of(market_money(user.lender_range_max)),
        )
    collection_account = None
    if is_merchant:
        account = current_account(db, user.id)  # la `active`; si no hay, la más reciente (06 R1)
        if account is not None:
            collection_account = CollectionAccountSummary(
                id=account.id, status=account.status, qr_payload=account.qr_payload
            )
    unread = db.scalar(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == user.id, Notification.read_at.is_(None)
        )
    )
    return Me(
        id=user.id,
        email=user.email,
        role=user.role,
        first_name=user.first_name,
        last_name=user.last_name,
        business_name=user.business_name,
        city=user.city,
        initials=(
            initials(user.business_name or "") if is_merchant else person_initials(user.first_name, user.last_name)
        ),
        public_name=public_name(user.first_name, user.last_name),
        payout_wallet=payout_wallet,
        lender_range=lender_range,
        collection_account=collection_account,
        unread_notifications=unread or 0,
    )


def update_me(db: Session, user: User, body: UpdateMeRequest) -> User:
    """R6: solo nombres, ciudad y "Mi rango" (prestamista, asset de mercado, `0 < min ≤ max`; `null` lo borra)."""
    fields = body.model_fields_set
    if "lender_range" in fields:
        if user.role != UserRole.LENDER:
            raise _validation_error("lender_range", "Solo los prestamistas tienen rango.")
        if body.lender_range is None:
            user.lender_range_min = user.lender_range_max = None
        else:
            low, high = body.lender_range.min.to_money(), body.lender_range.max.to_money()
            settings = get_settings()
            market = (settings.market_asset_code, settings.market_asset_scale)
            if (low.asset_code, low.asset_scale) != market or (high.asset_code, high.asset_scale) != market:
                raise AppError(
                    422,
                    "asset_mismatch",
                    f"El rango debe estar en {settings.market_asset_code} con escala {settings.market_asset_scale}.",
                    {"expected": {"asset_code": market[0], "asset_scale": market[1]}},
                )
            if not 0 < low.value <= high.value:
                raise _validation_error("lender_range", "El mínimo debe ser mayor que 0 y no superar el máximo.")
            user.lender_range_min, user.lender_range_max = low.value, high.value
    for name in ("first_name", "last_name", "city"):
        if name in fields:
            setattr(user, name, getattr(body, name))
    db.commit()
    return user


# ── Wallet propia (R7–R9) ─────────────────────────────────────────────────────


def _has_open_loan(db: Session, user_id: uuid.UUID) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    Loan.status.in_(NON_TERMINAL_LOAN_STATUSES),
                    or_(Loan.merchant_id == user_id, Loan.lender_id == user_id),
                )
            )
        )
    )


def _active_loan_error() -> AppError:
    return AppError(409, "active_loan_exists", "No puedes cambiar la wallet mientras tengas un crédito en curso.")


def set_payout_wallet(db: Session, op: OpClient, user: User, wallet_address: str) -> User:
    """R7: valida sin Go, cierra la transacción, resuelve la wallet en Go y la guarda en una transacción nueva."""
    if user.role == UserRole.ADMIN:
        raise AppError(403, "forbidden")
    if _has_open_loan(db, user.id):
        raise _active_loan_error()
    user_id, role = user.id, user.role
    db.commit()  # nunca se llama a op-service con una transacción abierta

    try:
        wallet = op.resolve_wallet_address(wallet_address)
    except OpError as exc:
        if exc.status == 422 and exc.code in OP_WALLET_ERRORS:
            raise AppError(422, exc.code, OP_WALLET_ERRORS[exc.code]) from exc
        logger.warning("op-service rechazó resolver la wallet: %r", exc)
        raise AppError(502, "op_unavailable") from exc
    except OpUnavailable as exc:
        raise AppError(503, "op_unavailable") from exc

    settings = get_settings()
    market = (settings.market_asset_code, settings.market_asset_scale)
    if role == UserRole.MERCHANT and (wallet.asset_code, wallet.asset_scale) != market:
        raise AppError(
            422,
            "asset_mismatch",
            f"La wallet de tu negocio debe estar en {market[0]} con escala {market[1]}; "
            f"esta es {wallet.asset_code} con escala {wallet.asset_scale}.",
            {
                "expected": {"asset_code": market[0], "asset_scale": market[1]},
                "wallet": {"asset_code": wallet.asset_code, "asset_scale": wallet.asset_scale},
            },
        )

    locked = db.scalar(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )
    if locked is None:
        db.rollback()
        raise AppError(401, "unauthorized")
    if _has_open_loan(db, user_id):  # paso 5: un crédito pudo nacer mientras hablábamos con Go
        db.rollback()
        raise _active_loan_error()
    changed = (
        locked.payout_wallet_url,
        locked.payout_wallet_public_name,
        locked.payout_asset_code,
        locked.payout_asset_scale,
    ) != (wallet.url, wallet.public_name, wallet.asset_code, wallet.asset_scale)
    locked.payout_wallet_url = wallet.url
    locked.payout_wallet_public_name = wallet.public_name
    locked.payout_asset_code = wallet.asset_code
    locked.payout_asset_scale = wallet.asset_scale
    if changed:
        record_event(
            db,
            "user",
            user_id,
            "payout_wallet_set",
            {"url": wallet.url, "asset_code": wallet.asset_code, "asset_scale": wallet.asset_scale},
            actor_id=user_id,
        )
    db.commit()
    return locked
