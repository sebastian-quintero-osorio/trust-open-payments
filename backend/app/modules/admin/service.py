"""Operador de Trust (16-admin): dashboard de dinero (R2–R4), reintento de patas (R5) y revocación de cuentas (R6)."""

import logging
import uuid

from sqlalchemy import BigInteger, case, cast, func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.deps import parse_id
from app.core.errors import AppError
from app.core.events import record_event
from app.core.money import Money, MoneySchema
from app.integrations.op.client import OpClient, OpError, OpUnavailable
from app.modules.admin.schemas import (
    AdminDashboard,
    AdminPayoutOut,
    CollectionAccountsByStatus,
    LoansByStatus,
    PayoutsByStatus,
    RetainedPayout,
    RevokeResult,
)
from app.modules.loans.models import Loan, LoanStatus
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus
from app.modules.recaudo.schemas import CollectionAccountOut
from app.modules.sales.models import Payout, PayoutLeg, PayoutStatus, Sale
from app.modules.users.models import User
from app.modules.users.schemas import merchant_ref
from app.worker import pending_legs_to_hold

logger = logging.getLogger(__name__)

Asset = tuple[str, int]

RETAINED = (PayoutStatus.FAILED_FINAL, PayoutStatus.HELD)
IN_FLIGHT = (PayoutStatus.PENDING, PayoutStatus.PROCESSING)
RETAINED_LIMIT = 50
HELD_REASON = "admin_revoked"

PAYOUT_NOT_FOUND = "No encontramos esa transferencia."
ACCOUNT_NOT_FOUND = "No encontramos esa cuenta de recaudo."
NOT_RETRYABLE = "Solo se puede reintentar una transferencia fallida o retenida."
ACCOUNT_NOT_ACTIVE = "La cuenta de recaudo de esta venta no está activa."
NOTHING_LEFT = "Esta transferencia ya no tiene saldo por enviar."
MERCHANT_WALLET_REQUIRED = "El negocio todavía no tiene una wallet vinculada."
TRUST_WALLET_MISSING = "Falta configurar la wallet de ingresos de Trust (TRUST_REVENUE_WALLET_ADDRESS)."
NO_RECEIVER = "Esta transferencia no tiene receptor."


def _sum(expression) -> object:
    """`SUM` como `bigint` (Postgres devuelve `numeric` al sumar `bigint`) y 0 si no hay filas."""
    return cast(func.coalesce(func.sum(expression), 0), BigInteger)


def _locked[M](db: Session, model: type[M], row_id: uuid.UUID) -> M | None:
    """`SELECT … FOR UPDATE` que refresca el objeto del mapa de identidad."""
    return db.scalar(
        select(model).where(model.id == row_id).with_for_update().execution_options(populate_existing=True)
    )


# ── Dashboard (R2–R4) ─────────────────────────────────────────────────────────


def _per_asset(sums: dict[Asset, int], assets: list[Asset]) -> list[MoneySchema]:
    return [MoneySchema.of(Money(sums.get(asset, 0), *asset)) for asset in assets]


def _counts(db: Session, column, values) -> dict[str, int]:
    """Conteo por estado con todos los valores del enum, también en 0 (R3)."""
    found = {status: count for status, count in db.execute(select(column, func.count()).group_by(column))}
    return {value.value: found.get(value.value, 0) for value in values}


def dashboard(db: Session) -> AdminDashboard:
    """R2: cada unidad de una pata cae en un solo grupo (entregado a Trust, retenido o en camino), por asset."""
    remaining = Payout.amount - Payout.sent_total
    payout_sums = db.execute(
        select(
            Payout.asset_code,
            Payout.asset_scale,
            _sum(case((Payout.leg == PayoutLeg.TRUST, Payout.sent_total), else_=0)),
            _sum(case((Payout.status.in_(RETAINED), remaining), else_=0)),
            _sum(case((Payout.status.in_(IN_FLIGHT), remaining), else_=0)),
        ).group_by(Payout.asset_code, Payout.asset_scale)
    ).all()
    loan_sums = db.execute(
        select(Loan.asset_code, Loan.asset_scale, _sum(Loan.trust_pending)).group_by(Loan.asset_code, Loan.asset_scale)
    ).all()

    revenue = {(code, scale): value for code, scale, value, _, _ in payout_sums}
    retained = {(code, scale): value for code, scale, _, value, _ in payout_sums}
    in_flight = {(code, scale): value for code, scale, _, _, value in payout_sums}
    trust_pending = {(code, scale): value for code, scale, value in loan_sums}

    # Una entrada por asset presente en patas o créditos (el de mercado primero); sin filas, "0" en el de mercado.
    cfg = get_settings()
    market: Asset = (cfg.market_asset_code, cfg.market_asset_scale)
    present = set(revenue) | set(trust_pending)
    assets = ([market] if market in present or not present else []) + sorted(present - {market})

    retained_rows = db.execute(
        select(Payout, User)
        .join(Sale, Sale.id == Payout.sale_id)
        .join(User, User.id == Sale.merchant_id)
        .where(Payout.status.in_(RETAINED))
        .order_by(Payout.created_at.desc(), Payout.id.desc())
        .limit(RETAINED_LIMIT)
    ).all()

    return AdminDashboard(
        trust_revenue=_per_asset(revenue, assets),
        trust_pending=_per_asset(trust_pending, assets),
        retained_funds=_per_asset(retained, assets),
        in_flight=_per_asset(in_flight, assets),
        payouts_by_status=PayoutsByStatus(**_counts(db, Payout.status, PayoutStatus)),
        loans_by_status=LoansByStatus(**_counts(db, Loan.status, LoanStatus)),
        collection_accounts=CollectionAccountsByStatus(
            **_counts(db, CollectionAccount.status, CollectionAccountStatus)
        ),
        retained_payouts=[
            RetainedPayout(
                id=payout.id,
                leg=payout.leg,
                status=payout.status,
                failure_code=payout.failure_code,
                amount=MoneySchema.of(Money(payout.amount, payout.asset_code, payout.asset_scale)),
                sent_total=MoneySchema.of(Money(payout.sent_total, payout.asset_code, payout.asset_scale)),
                merchant=merchant_ref(merchant),
            )
            for payout, merchant in retained_rows
        ],
    )


# ── Reintentar una pata (R5) ──────────────────────────────────────────────────


def _receiver(db: Session, payout: Payout, merchant_id: uuid.UUID) -> str:
    """Receptor del reintento: el congelado; si es null (07 R4), la wallet actual del negocio o la de Trust."""
    if payout.receiver_wallet_url:
        return payout.receiver_wallet_url
    if payout.leg == PayoutLeg.MERCHANT:
        merchant = db.get(User, merchant_id)
        if merchant is None or not merchant.payout_wallet_url:
            raise AppError(422, "wallet_required", MERCHANT_WALLET_REQUIRED)
        return merchant.payout_wallet_url
    if payout.leg == PayoutLeg.TRUST:
        trust_wallet = get_settings().trust_revenue_wallet_address
        if not trust_wallet:
            raise AppError(409, "invalid_state", TRUST_WALLET_MISSING)
        return trust_wallet
    raise AppError(409, "invalid_state", NO_RECEIVER)  # una pata lender siempre nace con receptor (01)


def retry_payout(db: Session, admin: User, payout_id: str) -> AdminPayoutOut:
    """R5: bloquea cuenta → pata (orden de 00-core), valida y deja la pata `pending` con un intento nuevo; el worker
    (08) la envía con la clave `payout:{id}:{attempt}`."""
    pid = parse_id(payout_id)
    sale = db.execute(
        select(Sale.collection_account_id, Sale.merchant_id).join(Payout, Payout.sale_id == Sale.id).where(
            Payout.id == pid
        )
    ).first()
    if sale is None:
        raise AppError(404, "not_found", PAYOUT_NOT_FOUND)
    account_id, merchant_id = sale
    try:
        account = _locked(db, CollectionAccount, account_id)
        payout = _locked(db, Payout, pid)
        if account is None or payout is None:
            raise AppError(404, "not_found", PAYOUT_NOT_FOUND)
        if payout.status not in RETAINED:
            raise AppError(409, "invalid_state", NOT_RETRYABLE, {"status": payout.status})
        if account.status != CollectionAccountStatus.ACTIVE:
            raise AppError(409, "collection_account_not_active", ACCOUNT_NOT_ACTIVE)
        remaining = payout.amount - payout.sent_total
        if remaining <= 0:
            raise AppError(409, "invalid_state", NOTHING_LEFT)
        receiver = _receiver(db, payout, merchant_id)
    except AppError:
        db.rollback()
        raise

    payout.receiver_wallet_url = receiver
    payout.status = PayoutStatus.PENDING
    payout.attempt += 1
    payout.current_debit_amount = remaining
    payout.op_transfer_id = payout.failure_code = payout.last_error = None
    payout.next_attempt_at = clock.now()
    record_event(
        db,
        "payout",
        payout.id,
        "manual_retry",
        {"attempt": payout.attempt, "current_debit_amount": remaining},
        actor_id=admin.id,
    )
    db.commit()
    return AdminPayoutOut.of(payout)


# ── Revocar una cuenta de recaudo (R6, R7) ────────────────────────────────────


def _revoke_locally(db: Session, admin: User, account: CollectionAccount) -> int:
    """R6.1 (cuenta ya bloqueada): `revoked` y sus patas `pending` → `held`, salvo las de envío ambiguo (08 R4: siguen
    `pending` y el worker repite su clave). Las `processing` no se tocan. Mismo orden de bloqueo que el worker."""
    account.status, account.revoked_at = CollectionAccountStatus.REVOKED, clock.now()
    pending = pending_legs_to_hold(db, account.id)
    for payout in pending:
        payout.status, payout.next_attempt_at = PayoutStatus.HELD, None
        record_event(
            db, "payout", payout.id, "held", {"reason": HELD_REASON, "attempt": payout.attempt}, actor_id=admin.id
        )
    record_event(
        db,
        "collection_account",
        account.id,
        "revoked",
        {"source": "admin", "held_payouts": len(pending)},
        actor_id=admin.id,
    )
    return len(pending)


def revoke_collection_account(db: Session, op: OpClient, admin: User, collection_account_id: str) -> RevokeResult:
    """R6: primero la revocación local (commit) y después Go, también si ya estaba revocada (el DELETE es
    idempotente y así se completa una revocación a medias). Si Go falla, la revocación local se queda."""
    account = _locked(db, CollectionAccount, parse_id(collection_account_id))
    if account is None:
        db.rollback()
        raise AppError(404, "not_found", ACCOUNT_NOT_FOUND)
    held = _revoke_locally(db, admin, account) if account.status == CollectionAccountStatus.ACTIVE else 0
    op_id = account.op_collection_account_id
    db.commit()  # nunca se llama a Go con la transacción abierta

    try:
        op.revoke_collection_account(op_id)
    except OpError as exc:
        if exc.status != 404:  # 404: Go ya no la tiene, cuenta como revocada
            logger.warning("admin: op-service no revocó la cuenta %s (%s): %r", account.id, op_id, exc)
            raise AppError(502, "op_unavailable") from exc
    except OpUnavailable as exc:
        logger.warning("admin: op-service no disponible al revocar la cuenta %s (%s): %s", account.id, op_id, exc)
        raise AppError(503, "op_unavailable") from exc
    return RevokeResult(collection_account=CollectionAccountOut.of(account), held_payouts=held)
