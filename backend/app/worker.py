"""Worker (spec 08): todo lo que depende de Go por polling, en un hilo daemon del lifespan.

`run_once(db, op, now, reconcile)` corre los jobs J1…J6 en orden, cada uno aislado (rollback + log): enviar patas,
seguirlas, seguir desembolsos, vencer créditos pendientes, reconciliar webhooks perdidos y cerrar periodos.

Nunca se llama a op-service con una transacción abierta (R3): (a) tx corta que lee las filas con
`FOR UPDATE SKIP LOCKED` y copia lo necesario a objetos planos, commit; (b) llamada a Go; (c) tx corta por fila que
bloquea en el orden de 00-core (cuenta → crédito → pata) y aplica el resultado solo si la fila no cambió.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any

from sqlalchemy import case, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.events import Event, record_event
from app.core.money import Money
from app.integrations.op.client import OpClient, OpError, OpUnavailable, get_op_client
from app.integrations.op.schemas import CreateTransferRequest, PositiveAmount, Transfer
from app.modules.loans.models import Loan, LoanStatus
from app.modules.notifications.catalog import render
from app.modules.notifications.service import notify
from app.modules.platform.lazy import optional_attr
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus
from app.modules.sales.models import Payout, PayoutLeg, PayoutStatus, Repayment, Sale, WebhookSource
from app.modules.sales.service import process_puntored_event
from app.modules.users.models import User, UserRole
from app.modules.worker.models import DAILY_CLOSE_CURSOR, SIM_QR_CURSOR, OpCursor

logger = logging.getLogger(__name__)

BATCH_SIZE = 50  # R3: filas por job y tick
SUBMIT_RETRY_DELAY = timedelta(seconds=10)  # R4: Go caído / 401 / request_in_progress → misma clave en 10 s
RETRY_BACKOFF = (  # R5: espera antes del intento n + 1, según el intento n que falló
    timedelta(seconds=10),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=5),
)
MAX_ATTEMPTS = 5
RECONCILE_EVERY_SECONDS = 30.0  # R1
DAILY_CLOSE_AT = dtime(0, 5)  # R9: hora local desde la que corre el cierre diario
LOANS_SERVICE = "app.modules.loans.service"  # 11-loans: on_disbursement_update, expire_pending, close_period

ACCOUNT_NOT_ACTIVE = "collection_account_not_active"
GRANT_REVOKED = "grant_revoked"
SUBMIT_UNKNOWN = "submit_unknown"  # evento de pata: el intento N se mandó a Go sin respuesta definitiva (R4)
LEG_LABELS = {PayoutLeg.LENDER: "prestamista", PayoutLeg.MERCHANT: "negocio", PayoutLeg.TRUST: "Trust"}
DELIVERED_LEGS = (PayoutLeg.LENDER, PayoutLeg.TRUST)  # suman a `loans.delivered_total` (R5)
LEG_RANK = case(
    {PayoutLeg.LENDER.value: 0, PayoutLeg.MERCHANT.value: 1, PayoutLeg.TRUST.value: 2}, value=Payout.leg, else_=3
)

_monotonic = time.monotonic  # reloj del loop (R1); los tests lo reemplazan


# ── Utilidades ────────────────────────────────────────────────────────────────


def _loans_fn(name: str) -> Callable[..., Any] | None:
    """Función de 11-loans si ya existe; si no, `None` y el job que la usa no hace nada."""
    return optional_attr(LOANS_SERVICE, name)


def _locked[M](db: Session, model: type[M], row_id: uuid.UUID) -> M | None:
    """`SELECT … FOR UPDATE` que refresca el objeto (con `expire_on_commit=False` el mapa de identidad está viejo)."""
    return db.scalar(
        select(model).where(model.id == row_id).with_for_update().execution_options(populate_existing=True)
    )


def _set_cursor(db: Session, name: str, value: int, now: datetime) -> None:
    db.execute(
        pg_insert(OpCursor)
        .values(name=name, value=value, updated_at=now)
        .on_conflict_do_update(index_elements=[OpCursor.name], set_={"value": value, "updated_at": now})
    )


def _get_cursor(db: Session, name: str) -> int | None:
    return db.scalar(select(OpCursor.value).where(OpCursor.name == name))


@dataclass(frozen=True)
class _Leg:
    """Copia plana de una pata leída en (a): se usa mientras se llama a Go, sin tocar la sesión."""

    id: uuid.UUID
    sale_id: uuid.UUID
    loan_id: uuid.UUID | None
    leg: str
    account_id: uuid.UUID
    op_account_id: uuid.UUID
    receiver: str | None
    debit: int  # current_debit_amount
    asset_code: str
    asset_scale: int
    description: str
    status: str
    attempt: int
    op_transfer_id: uuid.UUID | None

    @property
    def key(self) -> str:
        return f"payout:{self.id}:{self.attempt}"


def _claim_legs(db: Session, status: PayoutStatus, now: datetime) -> list[_Leg]:
    """(a) Hasta 50 patas en `status` (`FOR UPDATE OF payouts SKIP LOCKED`), copiadas y con commit."""
    query = (
        select(
            Payout.id,
            Payout.sale_id,
            Payout.loan_id,
            Payout.leg,
            Sale.collection_account_id,
            CollectionAccount.op_collection_account_id,
            Payout.receiver_wallet_url,
            Payout.current_debit_amount,
            Payout.asset_code,
            Payout.asset_scale,
            Payout.description,
            Payout.status,
            Payout.attempt,
            Payout.op_transfer_id,
        )
        .join(Sale, Sale.id == Payout.sale_id)
        .join(CollectionAccount, CollectionAccount.id == Sale.collection_account_id)
        .where(Payout.status == status)
    )
    if status == PayoutStatus.PENDING:
        query = query.where(or_(Payout.next_attempt_at.is_(None), Payout.next_attempt_at <= now))
    else:
        query = query.where(Payout.op_transfer_id.is_not(None))
    rows = db.execute(
        query.order_by(Payout.created_at, LEG_RANK, Payout.id)
        .limit(BATCH_SIZE)
        .with_for_update(of=Payout, skip_locked=True)
    ).all()
    db.commit()
    return [_Leg(*row) for row in rows]


def _unchanged(payout: Payout | None, leg: _Leg) -> bool:
    """R3: el resultado se aplica solo si la pata sigue en el `status` y el `attempt` leídos en (a)."""
    if payout is None or (payout.status, payout.attempt) != (leg.status, leg.attempt):
        logger.info("worker: la pata %s cambió mientras se llamaba a Go; se descarta el resultado", leg.id)
        return False
    return True


# ── Efectos compartidos ───────────────────────────────────────────────────────


def _hold(db: Session, payout: Payout, reason: str) -> None:
    payout.status, payout.next_attempt_at = PayoutStatus.HELD, None
    record_event(db, "payout", payout.id, "held", {"reason": reason, "attempt": payout.attempt})


def _mark_submit_unknown(db: Session, payout: Payout) -> None:
    """R4: el intento actual se mandó a Go y no hubo respuesta definitiva (Go pudo crear la transferencia). Un evento
    por (pata, intento); deja de contar cuando la pata sale de `pending` o cambia de intento."""
    marked = db.scalar(
        select(Event.id)
        .where(
            Event.entity_type == "payout",
            Event.entity_id == payout.id,
            Event.type == SUBMIT_UNKNOWN,
            Event.data["attempt"].as_integer() == payout.attempt,
        )
        .limit(1)
    )
    if marked is None:
        record_event(db, "payout", payout.id, SUBMIT_UNKNOWN, {"attempt": payout.attempt})


def _submit_unknown_ids(db: Session, payouts: Iterable[Payout]) -> set[uuid.UUID]:
    """De estas patas, las que tienen su intento actual marcado `submit_unknown`."""
    attempts = {payout.id: payout.attempt for payout in payouts}
    if not attempts:
        return set()
    rows = db.execute(
        select(Event.entity_id, Event.data["attempt"].as_integer()).where(
            Event.entity_type == "payout", Event.type == SUBMIT_UNKNOWN, Event.entity_id.in_(attempts)
        )
    ).all()
    return {payout_id for payout_id, attempt in rows if attempts.get(payout_id) == attempt}


def pending_legs_to_hold(db: Session, account_id: uuid.UUID) -> list[Payout]:
    """Patas `pending` de la cuenta que pasan a `held` al revocarla (aquí y en 16 R6), bloqueadas en el orden de J1.

    Quedan fuera las de envío ambiguo (`submit_unknown`): Go pudo haber creado su transferencia (una respuesta
    perdida) y revocar no cancela un pago ya creado. Siguen `pending` y J1 repite la misma clave: un 201 repetido →
    `processing` (J2 la contabiliza); una 409 `collection_account_not_active` → no se creó nada → `held`.
    """
    pending = db.scalars(
        select(Payout)
        .join(Sale, Sale.id == Payout.sale_id)
        .where(Sale.collection_account_id == account_id, Payout.status == PayoutStatus.PENDING)
        .order_by(Payout.created_at, LEG_RANK, Payout.id)
        .with_for_update(of=Payout)
        .execution_options(populate_existing=True)
    ).all()
    unknown = _submit_unknown_ids(db, pending)
    return [payout for payout in pending if payout.id not in unknown]


def _revoke_account(db: Session, account: CollectionAccount, reason: str, now: datetime) -> list[Payout]:
    """Cuenta `revoked` (si seguía activa) y sus patas `pending` → `held` (salvo las de envío ambiguo). La cuenta ya
    está bloqueada."""
    if account.status == CollectionAccountStatus.ACTIVE:
        account.status, account.revoked_at = CollectionAccountStatus.REVOKED, now
        record_event(db, "collection_account", account.id, "revoked", {"reason": reason, "source": "worker"})
    held = pending_legs_to_hold(db, account.id)
    for payout in held:
        _hold(db, payout, reason)
    return held


def _fail_final(db: Session, payout: Payout, failure_code: str, last_error: str) -> None:
    """`failed_final` + evento + `payout_failed` a cada admin (14 R4). La plata queda en recaudo."""
    payout.status, payout.next_attempt_at = PayoutStatus.FAILED_FINAL, None
    payout.failure_code, payout.last_error = failure_code, last_error
    record_event(
        db,
        "payout",
        payout.id,
        "failed_final",
        {"attempt": payout.attempt, "failure_code": failure_code, "sent_total": payout.sent_total},
    )
    monto = Money(payout.amount, payout.asset_code, payout.asset_scale)
    title, body = render(
        "payout_failed", "admin", leg=LEG_LABELS.get(payout.leg, payout.leg), monto=monto, failure_code=failure_code
    )
    data: dict[str, Any] = {
        "payout_id": str(payout.id),
        "sale_id": str(payout.sale_id),
        "leg": payout.leg,
        "failure_code": failure_code,
    }
    if payout.loan_id is not None:
        data["loan_id"] = str(payout.loan_id)
    admins = db.scalars(select(User.id).where(User.role == UserRole.ADMIN).order_by(User.created_at, User.id))
    for admin_id in admins.all():
        notify(db, admin_id, "payout_failed", title, body, data)


def _notify_abono(db: Session, payout: Payout) -> None:
    """`abono_received` al prestamista con los datos del abono de esa venta (14, catálogo)."""
    repayment = db.scalar(select(Repayment).where(Repayment.sale_id == payout.sale_id))
    sale = db.get(Sale, payout.sale_id)
    if repayment is None or sale is None:
        logger.error("worker: la pata lender %s no tiene abono; no se avisa abono_received", payout.id)
        return
    merchant = db.get(User, sale.merchant_id)

    def money(value: int) -> Money:
        return Money(value, repayment.asset_code, repayment.asset_scale)

    gross = money(repayment.abono_amount)
    trust_cut = money(repayment.trust_cut_amount)
    net = money(repayment.lender_net_amount)
    received = None
    if payout.receive_value is not None and payout.receive_asset_code and payout.receive_asset_scale is not None:
        received = Money(payout.receive_value, payout.receive_asset_code, payout.receive_asset_scale)
    title, body = render(
        "abono_received",
        "lender",
        bruto=gross,
        negocio=(merchant.business_name if merchant else None) or "",
        venta=Money(sale.amount, sale.asset_code, sale.asset_scale),
        hora=sale.paid_at,
        trust=trust_cut,
        neto=net,
    )
    data = {
        "loan_id": str(repayment.loan_id),
        "repayment_id": str(repayment.id),
        "sale_id": str(sale.id),
        "payout_id": str(payout.id),
        "gross": gross.to_api(),
        "trust_cut": trust_cut.to_api(),
        "net": net.to_api(),
        "received": received.to_api() if received is not None else None,
    }
    notify(db, repayment.lender_id, "abono_received", title, body, data)


def _complete(db: Session, payout: Payout, completed_at: datetime) -> None:
    payout.status, payout.completed_at, payout.next_attempt_at = PayoutStatus.COMPLETED, completed_at, None
    record_event(
        db,
        "payout",
        payout.id,
        "completed",
        {"attempt": payout.attempt, "sent_total": payout.sent_total, "op_transfer_id": str(payout.op_transfer_id)},
    )
    if payout.leg == PayoutLeg.LENDER:
        _notify_abono(db, payout)


# ── J1 · enviar patas (R4) ────────────────────────────────────────────────────


def _transfer_body(leg: _Leg) -> CreateTransferRequest:
    """Cuerpo armado solo con lo guardado en la pata: el reintento con la misma clave manda el mismo JSON."""
    return CreateTransferRequest(
        collection_account_id=leg.op_account_id,
        receiver_wallet_address=leg.receiver or "",
        debit_amount=PositiveAmount.from_money(Money(leg.debit, leg.asset_code, leg.asset_scale)),
        description=leg.description,
        external_ref=f"payout:{leg.id}",
    )


def _apply_submitted(db: Session, leg: _Leg, transfer: Transfer) -> None:
    """201: siempre se aplica (la transferencia ya existe en Go), aunque la pata haya cambiado."""
    payout = _locked(db, Payout, leg.id)
    if payout is None:
        return
    if payout.status == PayoutStatus.PROCESSING and payout.op_transfer_id == transfer.id:
        return
    if (payout.status, payout.attempt) != (leg.status, leg.attempt):
        logger.warning("worker: la pata %s cambió durante el envío; se aplica el 201 de %s", leg.id, transfer.id)
    payout.status, payout.op_transfer_id, payout.next_attempt_at = PayoutStatus.PROCESSING, transfer.id, None
    payout.attempt, payout.current_debit_amount = leg.attempt, leg.debit  # el intento que existe en Go
    record_event(
        db,
        "payout",
        payout.id,
        "processing",
        {"attempt": leg.attempt, "op_transfer_id": str(transfer.id), "debit_amount": leg.debit},
    )


def _apply_deferred(db: Session, leg: _Leg, now: datetime) -> None:
    """Misma clave en 10 s; el intento queda marcado `submit_unknown` (Go pudo haberlo creado)."""
    payout = _locked(db, Payout, leg.id)
    if _unchanged(payout, leg):
        payout.next_attempt_at = now + SUBMIT_RETRY_DELAY
        _mark_submit_unknown(db, payout)


def _apply_rejected(db: Session, leg: _Leg, exc: OpError) -> None:
    payout = _locked(db, Payout, leg.id)
    if _unchanged(payout, leg):
        _fail_final(db, payout, exc.code, str(exc)[:500])


def _submit_one(db: Session, op: OpClient, leg: _Leg, now: datetime, blocked: set[uuid.UUID]) -> None:
    try:
        transfer = op.create_transfer(_transfer_body(leg), leg.key)
    except OpUnavailable as exc:
        logger.warning("worker: op-service no disponible al enviar %s (%s); misma clave en 10 s", leg.key, exc)
        _apply_deferred(db, leg, now)
    except OpError as exc:
        if exc.status == 409 and exc.code == ACCOUNT_NOT_ACTIVE:
            blocked.add(leg.account_id)
            account = _locked(db, CollectionAccount, leg.account_id)
            if account is not None:
                held = {payout.id for payout in _revoke_account(db, account, ACCOUNT_NOT_ACTIVE, now)}
                # Para la clave de esta pata la 409 es definitiva (Go repetiría un 201 guardado): retenerla es seguro
                # aunque su intento estuviera marcado `submit_unknown`.
                payout = _locked(db, Payout, leg.id)
                if payout is not None and payout.id not in held and _unchanged(payout, leg):
                    _hold(db, payout, ACCOUNT_NOT_ACTIVE)
        elif exc.retryable or exc.status == 401 or (exc.status == 409 and exc.code == "request_in_progress"):
            # R4 / 02 R3: retryable, 401 y request_in_progress = Go caído
            logger.warning("worker: %s al enviar %s; misma clave en 10 s", exc, leg.key)
            _apply_deferred(db, leg, now)
        else:
            logger.error("worker: Go rechazó %s: %s", leg.key, exc)
            _apply_rejected(db, leg, exc)
    else:
        _apply_submitted(db, leg, transfer)
    db.commit()


def job_submit_payouts(db: Session, op: OpClient, now: datetime) -> None:
    """J1: patas `pending` vencidas → `create_transfer` con `Idempotency-Key: payout:{id}:{attempt}`."""
    blocked: set[uuid.UUID] = set()  # cuentas con 409 collection_account_not_active en este tick
    for leg in _claim_legs(db, PayoutStatus.PENDING, now):
        if leg.account_id in blocked:
            continue
        try:
            _submit_one(db, op, leg, now, blocked)
        except Exception:
            db.rollback()
            logger.exception("worker: falló el envío de la pata %s", leg.id)


# ── J2 · seguir patas (R5) ────────────────────────────────────────────────────


def _store_transfer_data(payout: Payout, transfer: Transfer) -> None:
    """`receive_*` (acumulado entre intentos si el asset no cambia) y URLs de trazabilidad."""
    receive = transfer.receive_amount
    if receive is not None:
        value = int(receive.value)
        stored_asset = (payout.receive_asset_code, payout.receive_asset_scale)
        same_asset = stored_asset == (receive.asset_code, receive.asset_scale)
        if payout.receive_value is not None and same_asset:
            payout.receive_value += value
        else:
            payout.receive_value = value
            payout.receive_asset_code, payout.receive_asset_scale = receive.asset_code, receive.asset_scale
    if transfer.incoming_payment_url:
        payout.incoming_payment_url = transfer.incoming_payment_url
    if transfer.outgoing_payment_url:
        payout.outgoing_payment_url = transfer.outgoing_payment_url


def _apply_transfer(db: Session, leg: _Leg, transfer: Transfer, now: datetime) -> None:
    """(c) Bloquea cuenta → crédito → pata y aplica `completed` / `failed` (R5)."""
    account = _locked(db, CollectionAccount, leg.account_id)
    loan = _locked(db, Loan, leg.loan_id) if leg.loan_id is not None and leg.leg in DELIVERED_LEGS else None
    payout = _locked(db, Payout, leg.id)
    if account is None or not _unchanged(payout, leg) or payout.op_transfer_id != transfer.id:
        return

    # Lo entregado en este intento, acotado al monto del intento (Go nunca envía más que el debit).
    if transfer.status == "completed":
        delivered = leg.debit
    else:
        delivered = int(transfer.sent_amount.value) if transfer.sent_amount is not None else 0
    delivered = max(0, min(delivered, leg.debit))
    payout.sent_total += delivered
    account.ledger_out += delivered
    if loan is not None:
        loan.delivered_total += delivered
    _store_transfer_data(payout, transfer)

    if transfer.status == "completed":
        _complete(db, payout, transfer.completed_at or now)
        return

    code = transfer.failure_code or "internal"
    payout.failure_code = code
    payout.last_error = f"Transferencia {transfer.id} fallida: {code} (enviado {delivered} de {leg.debit})"
    remaining = payout.amount - payout.sent_total
    if code == GRANT_REVOKED:
        _revoke_account(db, account, GRANT_REVOKED, now)
        if remaining > 0:
            _hold(db, payout, GRANT_REVOKED)
        else:
            _complete(db, payout, now)
    elif remaining <= 0:
        _complete(db, payout, now)
    elif account.status != CollectionAccountStatus.ACTIVE:  # p. ej. 16 la revocó mientras estaba `processing`
        _hold(db, payout, ACCOUNT_NOT_ACTIVE)
    elif transfer.retryable and payout.attempt < MAX_ATTEMPTS:
        previous = payout.attempt
        payout.attempt = previous + 1
        payout.current_debit_amount = remaining
        payout.op_transfer_id = None
        payout.status = PayoutStatus.PENDING
        payout.next_attempt_at = now + RETRY_BACKOFF[previous - 1]
        record_event(
            db,
            "payout",
            payout.id,
            "retry_scheduled",
            {
                "attempt": payout.attempt,
                "failure_code": code,
                "sent_total": payout.sent_total,
                "current_debit_amount": remaining,
                "next_attempt_at": payout.next_attempt_at.isoformat(),
            },
        )
    else:
        _fail_final(db, payout, code, payout.last_error)


def _apply_lost_transfer(db: Session, leg: _Leg, exc: OpError) -> None:
    """404 de `get_transfer`: Go no tiene la transferencia → `failed_final` (16 R5 la reintenta con clave nueva).

    Bloquea solo la pata (subconjunto del orden de 00-core): no hay entrega que contabilizar.
    """
    payout = _locked(db, Payout, leg.id)
    if _unchanged(payout, leg) and payout.op_transfer_id == leg.op_transfer_id:
        _fail_final(db, payout, exc.code, f"Go no encuentra la transferencia {leg.op_transfer_id}")


def _poll_one(db: Session, op: OpClient, leg: _Leg, now: datetime) -> None:
    assert leg.op_transfer_id is not None
    try:
        transfer = op.get_transfer(leg.op_transfer_id)
    except OpUnavailable as exc:
        logger.warning("worker: op-service no disponible al consultar la pata %s (%s)", leg.id, exc)
        return
    except OpError as exc:
        if exc.retryable or exc.status == 401:  # igual que Go caído (02 R3): siguiente tick
            logger.warning("worker: %s al consultar la pata %s; siguiente tick", exc, leg.id)
        elif exc.status == 404:
            logger.error("worker: Go no encuentra la transferencia %s de la pata %s", leg.op_transfer_id, leg.id)
            _apply_lost_transfer(db, leg, exc)
            db.commit()
        else:
            logger.warning("worker: Go rechazó la consulta de la pata %s: %s; siguiente tick", leg.id, exc)
        return
    if transfer.status == "processing":
        return
    _apply_transfer(db, leg, transfer, now)
    db.commit()


def job_poll_payouts(db: Session, op: OpClient, now: datetime) -> None:
    """J2: patas `processing` → `get_transfer`; contabilidad de entrega y reintentos con backoff."""
    for leg in _claim_legs(db, PayoutStatus.PROCESSING, now):
        try:
            _poll_one(db, op, leg, now)
        except Exception:
            db.rollback()
            logger.exception("worker: falló el seguimiento de la pata %s", leg.id)


# ── J3 · desembolsos (R6) ─────────────────────────────────────────────────────


def job_poll_disbursements(db: Session, op: OpClient, now: datetime) -> None:
    """J3: créditos `disbursing` → `get_payment` → `on_disbursement_update` (11) con el crédito bloqueado."""
    on_update = _loans_fn("on_disbursement_update")
    if on_update is None:
        return
    rows = db.execute(
        select(Loan.id, Loan.op_disbursement_payment_id)
        .where(Loan.status == LoanStatus.DISBURSING, Loan.op_disbursement_payment_id.is_not(None))
        .order_by(Loan.disbursement_started_at, Loan.id)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    ).all()
    db.commit()
    for loan_id, payment_id in rows:
        try:
            payment = op.get_payment(payment_id)
            loan = _locked(db, Loan, loan_id)
            if loan is not None:
                on_update(db, loan, payment)
            db.commit()
        except OpUnavailable as exc:
            logger.warning("worker: op-service no disponible al consultar el desembolso de %s (%s)", loan_id, exc)
        except OpError as exc:  # un 4xx de Go no merece un traceback en cada tick
            db.rollback()
            logger.warning("worker: Go rechazó la consulta del desembolso del crédito %s: %s", loan_id, exc)
        except Exception:
            db.rollback()
            logger.exception("worker: falló el seguimiento del desembolso del crédito %s", loan_id)


# ── J4 · vencer (R7) ──────────────────────────────────────────────────────────


def job_expire_pending(db: Session, op: OpClient, now: datetime) -> None:
    expire = _loans_fn("expire_pending")
    if expire is None:
        return
    expired = expire(db, now)
    db.commit()
    if expired:
        logger.info("worker: %d créditos pendientes vencidos", expired)


# ── J5 · reconciliar (R8) ─────────────────────────────────────────────────────


def job_reconcile(db: Session, op: OpClient, now: datetime) -> None:
    """J5: pagos del simulador desde el cursor → `process_puntored_event(…, "reconciliation")`, uno por transacción.

    Commit por item: la cuenta y el crédito que bloquea un item se sueltan antes del siguiente (con una sola
    transacción, un webhook en vivo del mismo evento esperaba la cuenta mientras J5 esperaba su `webhook_events`:
    deadlock). El cursor avanza solo por el tramo inicial de items terminados sin error: un item que lanzó o que sigue
    `processing` se repite en la próxima reconciliación (el dedup por `event_id` y referencia lo hace seguro).
    """
    cursor = _get_cursor(db, SIM_QR_CURSOR) or 0
    db.commit()  # sin transacción abierta durante la llamada a Go
    try:
        page = op.list_sim_qr_payments(after=cursor)
    except OpUnavailable as exc:
        logger.warning("worker: op-service no disponible para reconciliar (%s); se reintenta en 30 s", exc)
        return
    counts = {"processed": 0, "duplicate": 0, "unmatched": 0, "ignored": 0, "skipped": 0, "pending": 0, "error": 0}
    new_cursor, contiguous = cursor, True
    for item in page.items:
        done = True
        if item.status == "processing":  # sin resultado todavía: el cursor no lo pasa
            counts["pending"] += 1
            done = False
        elif item.status != "completed" or item.webhook_event is None:
            counts["skipped"] += 1
        else:
            try:
                result = process_puntored_event(db, item.webhook_event, WebhookSource.RECONCILIATION)
                db.commit()
                counts[result.status] += 1
            except Exception:
                db.rollback()
                counts["error"] += 1
                done = False
                logger.exception("worker: falló la reconciliación del pago simulado %s (seq %s)", item.id, item.seq)
        contiguous = contiguous and done
        if contiguous and item.seq is not None:
            new_cursor = max(new_cursor, item.seq)
    if contiguous:
        new_cursor = max(new_cursor, page.next_after)
    _set_cursor(db, SIM_QR_CURSOR, new_cursor, now)
    db.commit()
    if page.items:
        logger.info("worker: reconciliación desde %d hasta %d: %s", cursor, new_cursor, counts)


# ── J6 · cierre diario (R9) ───────────────────────────────────────────────────


def job_daily_close(db: Session, op: OpClient, now: datetime) -> None:
    """J6: desde las 00:05 locales, una vez por día: `close_period` de cada crédito `active` con revisión vencida."""
    close = _loans_fn("close_period")
    if close is None:
        return
    local = clock.to_local(now)
    if local.time() < DAILY_CLOSE_AT:
        return
    today = int(local.strftime("%Y%m%d"))
    last = _get_cursor(db, DAILY_CLOSE_CURSOR) or 0
    if last >= today:
        db.commit()
        return
    loan_ids = db.scalars(
        select(Loan.id)
        .where(Loan.status == LoanStatus.ACTIVE, Loan.next_review_at <= now)
        .order_by(Loan.next_review_at, Loan.id)
    ).all()
    db.commit()
    for loan_id in loan_ids:
        try:
            close(db, loan_id, now)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("worker: falló el cierre de periodo del crédito %s", loan_id)
    _set_cursor(db, DAILY_CLOSE_CURSOR, today, now)
    db.commit()


# ── run_once y loop (R1, R2) ──────────────────────────────────────────────────

Job = Callable[[Session, OpClient, datetime], None]


def _jobs(reconcile: bool) -> Iterable[tuple[str, Job]]:
    yield "J1 enviar patas", job_submit_payouts
    yield "J2 seguir patas", job_poll_payouts
    yield "J3 desembolsos", job_poll_disbursements
    yield "J4 vencer pendientes", job_expire_pending
    if reconcile:
        yield "J5 reconciliar", job_reconcile
    yield "J6 cierre diario", job_daily_close


def run_once(db: Session, op: OpClient, now: datetime, reconcile: bool = True) -> None:
    """J1…J6 en orden; cada job en sus propias transacciones y aislado: una falla nunca frena a los demás (R2)."""
    for name, job in _jobs(reconcile):
        try:
            job(db, op, now)
        except Exception:
            db.rollback()
            logger.exception("worker: falló el job %s", name)


def start_worker(op_getter: Callable[[], OpClient] = get_op_client) -> Callable[[], None]:
    """Arranca el hilo daemon del worker y devuelve la función que lo detiene (R1)."""
    interval = get_settings().worker_interval_seconds
    stop = threading.Event()

    def loop() -> None:
        last_reconcile: float | None = None
        while not stop.is_set():
            started = _monotonic()
            due = last_reconcile is None or started - last_reconcile >= RECONCILE_EVERY_SECONDS
            try:
                with SessionLocal() as db:
                    run_once(db, op_getter(), clock.now(), reconcile=due)
            except Exception:
                logger.exception("worker: falló el tick")
            if due:
                last_reconcile = started
            stop.wait(interval)

    thread = threading.Thread(target=loop, name="trust-worker", daemon=True)
    thread.start()
    logger.info("worker: arrancó (cada %s s)", interval)

    def stop_worker() -> None:
        stop.set()
        thread.join(timeout=max(5.0, interval * 2))

    return stop_worker
