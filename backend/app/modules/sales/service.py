"""Ventas (07-sales): webhook de Puntored, división de cada venta en una transacción y ventas del comercio.

`process_puntored_event` es el único camino que crea ventas `live`, abonos y patas. No hace commit (lo hace quien
llama: el router del webhook, la reconciliación del worker o el simulador de /dev) y nunca llama a op-service.
"""

import hashlib
import hmac
import json
import logging
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import clock
from app.core.clock import UtcDateTime
from app.core.config import get_settings
from app.core.deps import PageParams, next_offset, parse_id
from app.core.errors import AppError
from app.core.events import record_event
from app.core.money import BIGINT_MAX, VALUE_MAX_DIGITS, Money, MoneySchema, market_money
from app.integrations.op.schemas import PuntoredPaymentData
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, Loan, LoanStatus
from app.modules.notifications.catalog import render
from app.modules.notifications.service import notify
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus
from app.modules.sales.models import (
    Payout,
    PayoutLeg,
    PayoutStatus,
    Repayment,
    Sale,
    SaleSource,
    SaleStatus,
    WebhookEvent,
    WebhookSource,
    WebhookStatus,
)
from app.modules.sales.schemas import (
    DeliveryStatus,
    PayoutView,
    RepaymentInfo,
    SaleDetail,
    SaleInfo,
    SaleRow,
    SalesPage,
    SalesToday,
    SplitRule,
    TodaySummaryOut,
)
from app.modules.users.models import User, UserRole
from app.policies.split import SaleSplit, SplitLoanState, split_sale

logger = logging.getLogger(__name__)

PAYMENT_RECEIVED = "qr_payment.received"
SIGNATURE_TOLERANCE_SECONDS = 300
DESCRIPTION_MAX = 200
MAX_JSON_DEPTH = 64  # niveles de objetos/listas de un evento (el del YAML tiene 3)
LEG_ORDER = (PayoutLeg.LENDER, PayoutLeg.MERCHANT, PayoutLeg.TRUST)
DESCRIPTIONS = {  # R6
    PayoutLeg.LENDER: "Abono venta {hora} · {negocio}",
    PayoutLeg.MERCHANT: "Venta {hora} · {negocio}",
    PayoutLeg.TRUST: "Parte Trust venta {hora} · {negocio}",
}

INVALID_SIGNATURE = "La firma del webhook no es válida."
SIGNATURE_EXPIRED = "La firma del webhook venció. Reenvía el evento con una firma nueva."
BAD_EVENT = "El cuerpo del webhook debe ser un JSON con event_id y type."
SALE_NOT_FOUND = "No encontramos esa venta."
FOREIGN_SALE = "Esta venta no es de tu negocio."
BAD_RANGE = "La fecha inicial no puede ser posterior a la final."

_T_RE = re.compile(r"[0-9]+")


@dataclass(frozen=True)
class WebhookResult:
    status: str  # processed | duplicate | unmatched | ignored
    sale_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TodaySummary:
    sales_total: Money
    sales_count: int
    merchant_total: Money
    abonado_total: Money


# ── R1. Firma ─────────────────────────────────────────────────────────────────


def _parse_signature(header: str | None) -> tuple[str, list[str]] | None:
    """`t=<unix>,v1=<hex>[,v1=…]` → (t tal cual llegó, firmas v1). `None` si falta o está mal formado."""
    if not header:
        return None
    t: str | None = None
    signatures: list[str] = []
    for part in header.split(","):
        part = part.strip()
        if not part:
            continue
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep:
            return None
        if key == "t":
            if t is not None or not _T_RE.fullmatch(value):
                return None
            t = value
        elif key == "v1" and value:
            signatures.append(value)
        # Otros esquemas (v0, …) se ignoran.
    if t is None or not signatures:
        return None
    return t, signatures


def verify_signature(raw: bytes, header: str | None, secrets: Sequence[str], now: datetime) -> None:
    """R1: HMAC-SHA256 sobre `t + "." + body crudo` con cualquier secreto vigente (rotación), en tiempo constante.

    Sin header, mal formado o sin coincidencia → 401 `invalid_signature`; firma válida pero con
    `|now − t| > 300 s` → 401 `signature_expired`.
    """
    parsed = _parse_signature(header)
    if parsed is None:
        raise AppError(401, "invalid_signature", INVALID_SIGNATURE)
    t, signatures = parsed
    signed = t.encode() + b"." + raw
    valid = False
    for secret in secrets:
        expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest().encode()
        for candidate in signatures:
            # Sin cortar el ciclo: el tiempo no depende de cuál firma coincide.
            valid |= hmac.compare_digest(expected, candidate.encode())
    if not valid:
        raise AppError(401, "invalid_signature", INVALID_SIGNATURE)
    # Enteros (sin float) y con tope de dígitos: `int()` falla sobre 4300 y un `t` así está vencido de todos modos.
    digits = t.lstrip("0") or "0"
    if len(digits) > 18 or abs(int(now.timestamp()) - int(digits)) > SIGNATURE_TOLERANCE_SECONDS:
        raise AppError(401, "signature_expired", SIGNATURE_EXPIRED)


def _reject_constant(value: str) -> Any:
    raise ValueError(f"constante JSON no válida: {value}")


def _finite_float(text: str) -> float:
    """`1e400` no es un literal NaN/Infinity pero da `inf`, que JSONB rechaza: también es 400."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"número JSON fuera de rango: {text}")
    return value


def _has_nul(value: Any) -> bool:
    """Postgres no admite `\\x00` en text ni en JSONB: cualquier clave o texto con NUL hace fallar el insert."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def _strip_nul(value: Any) -> Any:
    """Quita los NUL de claves y textos: la reconciliación (08) no pasa por `parse_event` y no debe fallar en bucle."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {_strip_nul(k): _strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_nul(v) for v in value]
    return value


def _too_deep(value: Any, limit: int = MAX_JSON_DEPTH) -> bool:
    """Profundidad de objetos/listas sin recursión: lo que sigue (`_has_nul`, `_strip_nul`, JSONB) sí es recursivo."""
    stack = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict | list):
            if depth > limit:
                return True
            children = node.values() if isinstance(node, dict) else node
            stack.extend((child, depth + 1) for child in children)
    return False


def parse_event(raw: bytes) -> dict[str, Any]:
    """R2: `json.loads(raw)`; JSON ilegible, que no es objeto o sin `event_id`/`type` (texto) → 400 `bad_request`.

    También 400 lo que Postgres no puede guardar (NaN/Infinity, números que desbordan a `inf`, `\\u0000`) y el JSON
    anidado a más de 64 niveles (`RecursionError` al leerlo o al guardarlo): si no, el insert del paso 1 da 500 y Go
    reintenta para siempre.
    """
    try:
        event = json.loads(raw, parse_constant=_reject_constant, parse_float=_finite_float)
    except (ValueError, RecursionError):  # ValueError incluye JSONDecodeError y UnicodeDecodeError
        raise AppError(400, "bad_request", BAD_EVENT) from None
    if not isinstance(event, dict) or _too_deep(event) or _has_nul(event):
        raise AppError(400, "bad_request", BAD_EVENT)
    event_id, event_type = event.get("event_id"), event.get("type")
    if not isinstance(event_id, str) or not event_id.strip() or not isinstance(event_type, str):
        raise AppError(400, "bad_request", BAD_EVENT)
    return event


def receive_puntored_webhook(db: Session, raw: bytes, signature: str | None) -> WebhookResult:
    """R2: firma → JSON → `process_puntored_event(…, "puntored")` → commit. Con 401/400 no se escribe nada."""
    verify_signature(raw, signature, get_settings().puntored_webhook_secrets, clock.now())
    event = parse_event(raw)
    try:
        result = process_puntored_event(db, event, WebhookSource.PUNTORED)
        db.commit()
    except Exception:
        db.rollback()  # 500: Go reintenta y el dedup por event_id lo hace seguro
        raise
    return result


# ── R3. Procesar un evento ────────────────────────────────────────────────────


class _PaymentEvent(BaseModel):
    """Lo que la división necesita del evento `qr_payment.received` (forma del YAML)."""

    occurred_at: UtcDateTime
    data: PuntoredPaymentData


def _finish(
    db: Session,
    webhook: WebhookEvent,
    status: WebhookStatus,
    now: datetime,
    *,
    error: str | None = None,
    sale_id: uuid.UUID | None = None,
) -> None:
    webhook.status, webhook.error, webhook.sale_id, webhook.processed_at = status, error, sale_id, now
    data: dict[str, Any] = {"event_id": webhook.event_id, "source": webhook.source}
    if error is not None:
        data["error"] = error
    if sale_id is not None:
        data["sale_id"] = str(sale_id)
    record_event(db, "webhook_event", webhook.id, status, data)


def _unmatched(db: Session, webhook: WebhookEvent, error: str, now: datetime) -> WebhookResult:
    _finish(db, webhook, WebhookStatus.UNMATCHED, now, error=error)
    return WebhookResult("unmatched")


def _description(template: str, paid_at: datetime, business_name: str) -> str:
    """R6: descripción congelada con la hora local de la venta (nunca `now()`), máximo 200 caracteres."""
    return template.format(hora=clock.format_hora(paid_at), negocio=business_name)[:DESCRIPTION_MAX]


def process_puntored_event(db: Session, event: dict[str, Any], source: str) -> WebhookResult:
    """R3: guarda el evento (dedup por `event_id`), valida, divide la venta y crea abono y patas.

    No hace commit ni llama a Go. Bloquea la cuenta de recaudo y después el crédito (orden de 00-core).
    """
    if isinstance(event, BaseModel):  # el worker puede pasar el `webhook_event` de op-service tal cual
        event = event.model_dump(mode="json")
    event = _strip_nul(event)
    settings = get_settings()
    now = clock.now()
    event_id, event_type = event.get("event_id"), event.get("type")
    if not isinstance(event_id, str) or not event_id or not isinstance(event_type, str):
        raise ValueError("El evento necesita event_id y type de texto")

    # 1. Dedup por event_id: el estado final se escribe al terminar, en esta misma transacción.
    webhook_id = db.scalar(
        pg_insert(WebhookEvent)
        .values(
            id=uuid.uuid4(),
            event_id=event_id,
            source=source,
            type=event_type,
            payload=event,
            received_at=now,
            created_at=now,
        )
        .on_conflict_do_nothing(index_elements=[WebhookEvent.event_id])
        .returning(WebhookEvent.id)
    )
    if webhook_id is None:
        return WebhookResult("duplicate")
    webhook = db.get(WebhookEvent, webhook_id)
    assert webhook is not None

    # 2. Solo se procesan pagos recibidos (los reversos se guardan y se ignoran).
    if event_type != PAYMENT_RECEIVED:
        _finish(db, webhook, WebhookStatus.IGNORED, now)
        return WebhookResult("ignored")

    # 3. Datos válidos y cuenta de recaudo conocida, con su wallet y su asset (FOR UPDATE).
    try:
        payment = _PaymentEvent.model_validate(event)
    except ValidationError:
        return _unmatched(db, webhook, "invalid_payload", now)
    data = payment.data
    if not data.bre_b_reference.strip():  # sin referencia no hay dedup posible (R3.5): cuenta como campo faltante
        return _unmatched(db, webhook, "invalid_payload", now)
    if len(data.amount.value) > VALUE_MAX_DIGITS:  # `int()` fallaría sobre 4300 dígitos (500 y Go reintentaría)
        return _unmatched(db, webhook, "invalid_payload", now)
    amount = data.amount.to_money()
    if amount.value > BIGINT_MAX:  # cabe en el patrón del YAML pero no en `sales.amount`: dato inválido, no 500
        return _unmatched(db, webhook, "invalid_payload", now)
    account = db.scalar(
        select(CollectionAccount)
        .where(CollectionAccount.op_collection_account_id == data.collection_account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        return _unmatched(db, webhook, "unknown_account", now)
    if account.wallet_address != data.collection_wallet_address:
        return _unmatched(db, webhook, "wallet_mismatch", now)
    if (amount.asset_code, amount.asset_scale) != (account.asset_code, account.asset_scale):
        return _unmatched(db, webhook, "asset_mismatch", now)
    now = clock.now()  # después del FOR UPDATE: created_at sigue el orden de commit (cursor R9)

    # 4. Crédito no terminado del negocio (FOR UPDATE, después de la cuenta); solo abona si está `active`.
    loan = db.scalar(
        select(Loan)
        .where(Loan.merchant_id == account.merchant_id, Loan.status.in_(NON_TERMINAL_LOAN_STATUSES))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    active = loan if loan is not None and loan.status == LoanStatus.ACTIVE else None

    # 5. Dedup por referencia Bre-B (seguro ante carreras porque la cuenta está bloqueada).
    repeated = db.scalar(
        select(Sale.id).where(
            Sale.collection_account_id == account.id, Sale.bre_b_reference == data.bre_b_reference
        )
    )
    if repeated is not None:
        _finish(db, webhook, WebhookStatus.IGNORED, now, error="duplicate_reference")
        return WebhookResult("duplicate")

    # 6. La venta (`held` si la cuenta ya no está activa: la plata está en recaudo, las patas quedan retenidas).
    merchant = db.get(User, account.merchant_id)
    assert merchant is not None
    held = account.status != CollectionAccountStatus.ACTIVE
    sale = Sale(
        id=uuid.uuid4(),
        merchant_id=account.merchant_id,
        collection_account_id=account.id,
        puntored_event_id=event_id,
        bre_b_reference=data.bre_b_reference,
        amount=amount.value,
        asset_code=amount.asset_code,
        asset_scale=amount.asset_scale,
        payer_bank=data.payer_bank,
        incoming_payment_url=data.incoming_payment_url,
        paid_at=payment.occurred_at,
        source=SaleSource.LIVE,
        status=SaleStatus.HELD if held else SaleStatus.PROCESSED,
        created_at=now,
    )
    db.add(sale)
    db.flush()  # la venta existe antes que su abono y sus patas (llaves foráneas)

    # 6b. `trust_pending` es plata de la cuenta donde se acumuló: si esta venta llega por otra cuenta, sale antes como
    #     pata Trust de esa cuenta (R3.9), nunca debitada de una cuenta que no la recibió.
    if active is not None and active.trust_pending > 0:
        _flush_trust_pending_of_other_account(db, active, account, merchant)

    # 7. División.
    state = None
    if active is not None:
        state = SplitLoanState(
            active.pct_bps, active.total_repayable, active.allocated_total, active.fee_amount, active.trust_share_bps
        )
    split = split_sale(amount.value, state, policy=settings.trust_cut_policy)

    # 8. Abono (bruto) y contabilidad del crédito.
    if active is not None and split.abono > 0:
        db.add(
            Repayment(
                sale_id=sale.id,
                loan_id=active.id,
                merchant_id=active.merchant_id,
                lender_id=active.lender_id,
                abono_amount=split.abono,
                trust_cut_amount=split.trust_cut,
                lender_net_amount=split.lender_net,
                asset_code=amount.asset_code,
                asset_scale=amount.asset_scale,
                pct_applied_bps=active.pct_bps,
                allocated_before=active.allocated_total,
                allocated_after=split.allocated_after,
                created_at=now,
            )
        )
        active.allocated_total = split.allocated_after
        active.trust_earned += split.trust_cut
        active.trust_pending += split.trust_cut

    # 9. Pata Trust: acumula hasta TRUST_LEG_MIN; con el último abono sale lo que quede.
    trust_amount = 0
    if active is not None and active.trust_pending > 0 and (
        active.trust_pending >= settings.trust_leg_min or split.paid_off
    ):
        trust_amount = active.trust_pending
        active.trust_pending -= trust_amount

    # 10. Patas (R4) con monto > 0.
    _create_legs(db, sale, merchant, active, split, trust_amount, held)

    # 11. Libro de la cuenta de recaudo.
    account.ledger_in += amount.value

    # 12. Último abono: crédito pagado.
    if active is not None and split.paid_off:
        _mark_paid(db, active, merchant, sale, now)

    # 13. Evento procesado y aviso de la división (solo ventas `processed`: con patas held no hay plata en camino).
    _finish(db, webhook, WebhookStatus.PROCESSED, now, sale_id=sale.id)
    if settings.notify_sale_split and not held:
        _notify_sale_split(db, sale, merchant, active, split)
    return WebhookResult("processed", sale.id)


def _trust_wallet(sale: Sale, trust_amount: int) -> str | None:
    trust_wallet = get_settings().trust_revenue_wallet_address or None
    if trust_amount > 0 and trust_wallet is None:
        logger.error("TRUST_REVENUE_WALLET_ADDRESS vacío: la pata Trust de la venta %s queda held", sale.id)
    return trust_wallet


def _add_leg(
    db: Session,
    sale: Sale,
    loan_id: uuid.UUID | None,
    leg: PayoutLeg,
    amount: int,
    receiver: str | None,
    held: bool,
    business_name: str,
    **event_data: Any,
) -> Payout:
    """R4: receptor congelado, asset de la venta, `attempt = 1`, `current_debit_amount = amount`."""
    status = PayoutStatus.HELD if held or receiver is None else PayoutStatus.PENDING
    payout = Payout(
        id=uuid.uuid4(),
        sale_id=sale.id,
        loan_id=loan_id,
        leg=leg,
        receiver_wallet_url=receiver,
        amount=amount,
        asset_code=sale.asset_code,
        asset_scale=sale.asset_scale,
        description=_description(DESCRIPTIONS[leg], sale.paid_at, business_name),
        status=status,
        attempt=1,
        current_debit_amount=amount,
        sent_total=0,
    )
    db.add(payout)
    record_event(
        db,
        "payout",
        payout.id,
        "created",
        {"sale_id": str(sale.id), "leg": leg, "amount": amount, "status": status, **event_data},
    )
    return payout


def _create_legs(
    db: Session,
    sale: Sale,
    merchant: User,
    loan: Loan | None,
    split: SaleSplit,
    trust_amount: int,
    held: bool,
) -> list[Payout]:
    """R4: las patas de la venta con monto > 0."""
    business_name = merchant.business_name or ""
    loan_id = loan.id if loan is not None else None
    legs: list[tuple[PayoutLeg, int, str | None]] = []
    if loan is not None:
        legs.append((PayoutLeg.LENDER, split.lender_net, loan.lender_wallet_url))
    merchant_wallet = loan.merchant_payout_wallet_url if loan is not None else merchant.payout_wallet_url
    legs.append((PayoutLeg.MERCHANT, split.merchant_amount, merchant_wallet))
    if loan is not None:
        legs.append((PayoutLeg.TRUST, trust_amount, _trust_wallet(sale, trust_amount)))
    return [
        _add_leg(db, sale, loan_id, leg, amount, receiver, held, business_name)
        for leg, amount, receiver in legs
        if amount > 0
    ]


def _flush_trust_pending_of_other_account(db: Session, loan: Loan, account: CollectionAccount, merchant: User) -> None:
    """R3.9: lo pendiente (`trust_pending > 0`) se acumuló en la cuenta de la venta con el último abono con parte
    Trust (el mayor `allocated_after`: crece con cada abono). Si no es `account`, sale ya como pata Trust de esa venta
    (que no tiene pata Trust: si la hubiera tenido, habría barrido lo pendiente) y lo pendiente vuelve a 0. La pata
    queda `held` si esa venta o su cuenta ya no está activa (fondos retenidos en esa cuenta, 16).

    No bloquea la otra cuenta (el orden de 00-core es cuenta → crédito): si la revocan a la vez y la pata nace
    `pending`, el worker recibe 409 `collection_account_not_active` y la retiene (08 R4).
    """
    last_sale = db.scalar(
        select(Sale)
        .join(Repayment, Repayment.sale_id == Sale.id)
        .where(Repayment.loan_id == loan.id, Repayment.trust_cut_amount > 0)
        .order_by(Repayment.allocated_after.desc())
        .limit(1)
    )
    if last_sale is None or last_sale.collection_account_id == account.id:
        return
    other = db.get(CollectionAccount, last_sale.collection_account_id)
    held = last_sale.status == SaleStatus.HELD or other is None or other.status != CollectionAccountStatus.ACTIVE
    amount, loan.trust_pending = loan.trust_pending, 0
    _add_leg(
        db,
        last_sale,
        loan.id,
        PayoutLeg.TRUST,
        amount,
        _trust_wallet(last_sale, amount),
        held,
        merchant.business_name or "",
        reason="trust_pending_account_changed",
    )


def _mark_paid(db: Session, loan: Loan, merchant: User, sale: Sale, now: datetime) -> None:
    """R3.12: `active` → `paid` (máquina de estados de 01) y `loan_paid` al negocio y al prestamista."""
    loan.status, loan.paid_at = LoanStatus.PAID, now
    record_event(
        db, "loan", loan.id, "paid", {"sale_id": str(sale.id), "allocated_total": loan.allocated_total}
    )
    total = Money(loan.total_repayable, loan.asset_code, loan.asset_scale)
    data = {"loan_id": str(loan.id), "total_repayable": total.to_api()}
    negocio = merchant.business_name or ""
    notify(db, loan.merchant_id, "loan_paid", *render("loan_paid", "merchant", total=total, negocio=negocio), data)
    notify(db, loan.lender_id, "loan_paid", *render("loan_paid", "lender", total=total, negocio=negocio), data)


def _notify_sale_split(db: Session, sale: Sale, merchant: User, loan: Loan | None, split: SaleSplit) -> None:
    def money(value: int) -> Money:
        return Money(value, sale.asset_code, sale.asset_scale)

    venta, para_ti, abono = money(sale.amount), money(split.merchant_amount), money(split.abono)
    data: dict[str, Any] = {
        "sale_id": str(sale.id),
        "amount": venta.to_api(),
        "merchant_amount": para_ti.to_api(),
        "abono": abono.to_api(),
    }
    if loan is not None:
        data["loan_id"] = str(loan.id)
    title, body = render("sale_split", "merchant", venta=venta, para_ti=para_ti, abono=abono)
    notify(db, merchant.id, "sale_split", title, body, data)


# ── R7. Estado de entrega agregado ────────────────────────────────────────────


def payout_status(statuses: Iterable[str]) -> DeliveryStatus:
    """`failed` > `held` > `in_progress` > `completed` (también sin patas); las `skipped` se ignoran."""
    present = {s for s in statuses if s != PayoutStatus.SKIPPED}
    if PayoutStatus.FAILED_FINAL in present:
        return "failed"
    if PayoutStatus.HELD in present:
        return "held"
    if present & {PayoutStatus.PENDING, PayoutStatus.PROCESSING}:
        return "in_progress"
    return "completed"


def _payout_statuses(db: Session, sale_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    by_sale: dict[uuid.UUID, list[str]] = defaultdict(list)
    if sale_ids:
        for sale_id, status in db.execute(
            select(Payout.sale_id, Payout.status).where(Payout.sale_id.in_(sale_ids))
        ):
            by_sale[sale_id].append(status)
    return by_sale


def _money(value: int, sale: Sale) -> MoneySchema:
    return MoneySchema.of(Money(value, sale.asset_code, sale.asset_scale))


def _rows(db: Session, results: Sequence[Any]) -> list[SaleRow]:
    """(venta, abono | None) → filas con R8 (`merchant_amount = amount − abono`) y R7."""
    statuses = _payout_statuses(db, [sale.id for sale, _ in results])
    rows = []
    for sale, abono in results:
        abono = abono or 0
        rows.append(
            SaleRow(
                sale_id=sale.id,
                paid_at=sale.paid_at,
                amount=_money(sale.amount, sale),
                merchant_amount=_money(sale.amount - abono, sale),
                abono_amount=_money(abono, sale),
                payout_status=payout_status(statuses.get(sale.id, [])),
            )
        )
    return rows


def _sales_with_abono():
    return select(Sale, Repayment.abono_amount).outerjoin(Repayment, Repayment.sale_id == Sale.id)


# ── R9 / R10. Ventas de hoy ───────────────────────────────────────────────────


def today_summary(db: Session, merchant_id: uuid.UUID, day: date) -> TodaySummary:
    """R10: totales del día local `day` (todas las fuentes): ventas, cantidad, "para ti" y abonado (bruto)."""
    start, end = clock.local_day_bounds(day)
    count, sales_total, abonado = db.execute(
        select(
            func.count(Sale.id),
            cast(func.coalesce(func.sum(Sale.amount), 0), BigInteger),
            cast(func.coalesce(func.sum(Repayment.abono_amount), 0), BigInteger),
        )
        .select_from(Sale)
        .outerjoin(Repayment, Repayment.sale_id == Sale.id)
        .where(Sale.merchant_id == merchant_id, Sale.paid_at >= start, Sale.paid_at < end)
    ).one()
    return TodaySummary(
        sales_total=market_money(int(sales_total)),
        sales_count=int(count),
        merchant_total=market_money(int(sales_total) - int(abonado)),
        abonado_total=market_money(int(abonado)),
    )


def format_cursor(dt: datetime) -> str:
    """`next_since` con microsegundos siempre (`2026-10-16T15:42:01.204312Z`), para no perder precisión."""
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def split_rule(db: Session, merchant_id: uuid.UUID) -> SplitRule:
    """R8: `{10000 − pct, pct}` del crédito `active`; sin crédito activo, `{10000, 0}`."""
    pct = db.scalar(select(Loan.pct_bps).where(Loan.merchant_id == merchant_id, Loan.status == LoanStatus.ACTIVE))
    pct = pct or 0
    return SplitRule(merchant_bps=10_000 - pct, lender_bps=pct)


def sales_today(db: Session, merchant: User, since: datetime | None) -> SalesToday:
    day = clock.today_local()
    start, end = clock.local_day_bounds(day)
    of_today = (Sale.merchant_id == merchant.id, Sale.paid_at >= start, Sale.paid_at < end)
    summary = today_summary(db, merchant.id, day)

    query = _sales_with_abono().where(*of_today)
    if since is not None:
        query = query.where(Sale.created_at > since)
    results = db.execute(query.order_by(Sale.paid_at.desc(), Sale.id.desc())).all()

    # El cursor nunca retrocede: el `created_at` más reciente de las filas leídas o, si no hay nuevas, el `since`.
    # Sale de `results` (la misma foto de la consulta): una segunda consulta podría ver una venta que `rows` no trae
    # y el cursor la saltaría para siempre. Con `since`, toda fila excluida tiene `created_at <= since`.
    latest = max((sale.created_at for sale, _ in results), default=None)
    candidates = [c for c in (latest, since) if c is not None]
    cursor = max(candidates) if candidates else None

    return SalesToday(
        date=day,
        live=True,
        summary=TodaySummaryOut(
            sales_total=MoneySchema.of(summary.sales_total),
            sales_count=summary.sales_count,
            merchant_total=MoneySchema.of(summary.merchant_total),
            abonado_total=MoneySchema.of(summary.abonado_total),
        ),
        rule=split_rule(db, merchant.id),
        wallet_today=MoneySchema.of(summary.merchant_total),
        rows=_rows(db, results),
        next_since=format_cursor(cursor) if cursor is not None else None,
    )


# ── R11. Historial y detalle ──────────────────────────────────────────────────


def list_sales(db: Session, merchant: User, from_: date | None, to: date | None, page: PageParams) -> SalesPage:
    if from_ is not None and to is not None and from_ > to:
        raise AppError(
            422, "validation_error", BAD_RANGE, {"fields": [{"field": "from", "message": BAD_RANGE}]}
        )
    query = _sales_with_abono().where(Sale.merchant_id == merchant.id)
    if from_ is not None:
        query = query.where(Sale.paid_at >= clock.local_day_bounds(from_)[0])
    if to is not None:
        query = query.where(Sale.paid_at < clock.local_day_bounds(to)[1])
    results = db.execute(
        query.order_by(Sale.paid_at.desc(), Sale.id).limit(page.limit).offset(page.offset)
    ).all()
    return SalesPage(items=_rows(db, results), next_offset=next_offset(page.offset, page.limit, len(results)))


def sale_detail(db: Session, user: User, sale_id: str) -> SaleDetail:
    sale = db.get(Sale, parse_id(sale_id))
    if sale is None:
        raise AppError(404, "not_found", SALE_NOT_FOUND)
    if user.role != UserRole.ADMIN and sale.merchant_id != user.id:
        raise AppError(403, "forbidden", FOREIGN_SALE)
    repayment = db.scalar(select(Repayment).where(Repayment.sale_id == sale.id))
    payouts = db.scalars(select(Payout).where(Payout.sale_id == sale.id)).all()
    payouts = sorted(payouts, key=lambda p: LEG_ORDER.index(PayoutLeg(p.leg)))
    return SaleDetail(
        sale=SaleInfo.of(sale),
        repayment=RepaymentInfo.of(repayment) if repayment is not None else None,
        payouts=[PayoutView.of(p) for p in payouts],
    )
