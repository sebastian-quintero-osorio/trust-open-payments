"""Atajos de demo `/dev/*` (03 R3, R11–R13): guarda, simular venta, cerrar periodo y wallet falsa."""

import html
import logging
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.deps import parse_id
from app.core.errors import AppError
from app.core.money import format_es, require_market_asset
from app.integrations.op.client import OpClient, OpClientDep, OpError, OpUnavailable
from app.integrations.op.fake import FakeOpClient
from app.integrations.op.schemas import CreateSimQrPaymentRequest, Payment, PositiveAmount
from app.modules.loans.models import Loan
from app.modules.loans.schemas import LoanPctChangeOut, pct_change_out
from app.modules.platform.lazy import optional_attr
from app.modules.platform.schemas import SimulateSaleRequest, SimulateSaleResponse
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus

logger = logging.getLogger(__name__)


# ── R3. Guardas ───────────────────────────────────────────────────────────────


def require_dev() -> None:
    """404 `not_found` si `DEV_ENDPOINTS_ENABLED` es false (se lee en cada petición)."""
    if not get_settings().dev_endpoints_enabled:
        raise AppError(404, "not_found")


def require_fake_op(op: OpClientDep) -> FakeOpClient:
    """`/dev/fake-wallet` solo existe con `OP_CLIENT=fake`."""
    if not isinstance(op, FakeOpClient):
        raise AppError(404, "not_found")
    return op


# ── R11. Simular venta ────────────────────────────────────────────────────────

SIM_CONFLICTS = {
    "collection_account_not_active": "La cuenta de recaudo del negocio no está activa.",
    "sim_disabled": "El simulador de pagos está apagado.",
    "sim_payer_not_authorized": "El pagador del simulador no está autorizado.",
}
SIM_UNPROCESSABLE = {
    "asset_mismatch": "El monto no está en el asset de la cuenta de recaudo.",
    "invalid_amount": "El monto no es válido para un pago Bre-B.",
}


def _validation_error(field: str, message: str) -> AppError:
    return AppError(422, "validation_error", details={"fields": [{"field": field, "message": message}]})


def _sim_account(db: Session, body: SimulateSaleRequest) -> CollectionAccount:
    if body.collection_account_id is not None:
        account = db.get(CollectionAccount, body.collection_account_id)
        if account is None:
            raise AppError(404, "not_found", "No encontramos esa cuenta de recaudo.")
        if account.status != CollectionAccountStatus.ACTIVE:
            raise AppError(409, "collection_account_not_active", SIM_CONFLICTS["collection_account_not_active"])
        return account
    account = db.scalar(
        select(CollectionAccount).where(
            CollectionAccount.merchant_id == body.merchant_id,
            CollectionAccount.status == CollectionAccountStatus.ACTIVE,
        )
    )
    if account is None:
        raise AppError(404, "not_found", "El negocio no tiene una cuenta de recaudo activa.")
    return account


def simulate_sale(db: Session, op: OpClient, body: SimulateSaleRequest) -> SimulateSaleResponse:
    if (body.merchant_id is None) == (body.collection_account_id is None):
        raise _validation_error("merchant_id", "Envía merchant_id o collection_account_id (exactamente uno).")
    account = _sim_account(db, body)
    amount = require_market_asset(body.amount.to_money())  # 422 `asset_mismatch` sin llamar a Go
    if amount.value <= 0:
        raise _validation_error("amount.value", "El monto debe ser mayor que 0.")
    op_account_id = account.op_collection_account_id
    db.rollback()  # fin de la lectura: nunca se llama a Go con una transacción abierta

    request = CreateSimQrPaymentRequest(
        collection_account_id=op_account_id, amount=PositiveAmount.from_money(amount), payer_bank=body.payer_bank
    )
    try:
        sim = op.create_sim_qr_payment(request, idempotency_key=f"sim:{uuid.uuid4()}")
    except OpError as exc:
        if exc.retryable:
            raise AppError(502, "op_unavailable") from exc
        if exc.status == 404:
            raise AppError(404, "not_found", "op-service no conoce esa cuenta de recaudo.") from exc
        if exc.status == 409 and exc.code in SIM_CONFLICTS:
            raise AppError(409, exc.code, SIM_CONFLICTS[exc.code]) from exc
        if exc.status == 422 and exc.code in SIM_UNPROCESSABLE:
            raise AppError(422, exc.code, SIM_UNPROCESSABLE[exc.code]) from exc
        logger.warning("op-service rechazó el pago simulado: %r", exc)
        raise AppError(502, "op_unavailable") from exc
    except OpUnavailable as exc:
        raise AppError(503, "op_unavailable") from exc

    processed, sale_id = False, None
    if isinstance(op, FakeOpClient) and sim.webhook_event is not None:
        process_puntored_event = optional_attr("app.modules.sales.service", "process_puntored_event")
        if process_puntored_event is None:
            logger.warning("Sin spec 07 todavía: el pago simulado %s no se procesa como venta", sim.id)
        else:
            try:
                result = process_puntored_event(db, sim.webhook_event.model_dump(mode="json"), source="reconciliation")
                db.commit()
            except Exception:
                db.rollback()
                raise
            sale_id = result.sale_id
            processed = sale_id is not None
    return SimulateSaleResponse(
        sim_payment_id=sim.id,
        bre_b_reference=sim.bre_b_reference,
        status=sim.status,
        processed=processed,
        sale_id=sale_id,
    )


# ── R12. Cerrar periodo ───────────────────────────────────────────────────────


def close_period(db: Session, loan_id: str) -> LoanPctChangeOut:
    loan = db.get(Loan, parse_id(loan_id))
    if loan is None:
        raise AppError(404, "not_found", "No encontramos ese crédito.")
    loan_key, asset_code, asset_scale = loan.id, loan.asset_code, loan.asset_scale
    close = optional_attr("app.modules.loans.service", "close_period")
    if close is None:
        raise AppError(409, "invalid_state", "El recalculo del % todavía no está disponible.")
    try:
        change = close(db, loan_key, clock.now())
    except ValueError as exc:  # 11 R15: el crédito no está `active`
        db.rollback()
        raise AppError(409, "invalid_state", "Solo se puede cerrar el periodo de un crédito activo.") from exc
    db.commit()
    return pct_change_out(change, asset_code, asset_scale)


# ── R13. Wallet falsa ─────────────────────────────────────────────────────────

AWAITING = "awaiting_authorization"


def _payment(fake: FakeOpClient, payment_id: uuid.UUID) -> Payment:
    """Estado actual sin `get_payment` (que registra la llamada y puede auto-completar el pago)."""
    for payment in fake.payments:
        if payment.id == payment_id:
            return payment
    raise AppError(404, "not_found", "No encontramos ese pago.")


def _with_op_params(return_url: str, payment: Payment) -> str:
    """Agrega `op_resource`, `op_id` y `op_status` a la query existente (sin borrar `loan_id` ni `client`)."""
    parts = urlsplit(return_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("op_resource", "op_id", "op_status")]
    query += [("op_resource", "payment"), ("op_id", str(payment.id)), ("op_status", payment.status)]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _authorize_page(payment: Payment) -> str:
    amount = payment.receive_amount.to_money()
    receiver = html.escape(payment.receiver_wallet_address)
    sender = html.escape(payment.sender_wallet_address)
    pending = payment.status == AWAITING
    actions = (
        '<a class="ok" href="?decision=approve">Aprobar</a> <a class="no" href="?decision=reject">Rechazar</a>'
        if pending
        else f"<p>Este pago ya está <b>{html.escape(payment.status)}</b>.</p>"
        '<a href="?decision=approve">Volver a la app</a>'
    )
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wallet de prueba</title>
<style>body{{font-family:system-ui,sans-serif;max-width:28rem;margin:3rem auto;padding:0 1rem;color:#1f2937}}
a{{display:inline-block;margin:.5rem .5rem 0 0;padding:.7rem 1.4rem;border-radius:.5rem;text-decoration:none;
color:#fff;background:#4b5563}}a.ok{{background:#15803d}}a.no{{background:#b91c1c}}</style></head>
<body><h1>Autorizar pago</h1>
<p>Wallet de prueba de Trust (sin op-service). Monto: <b>{html.escape(format_es(amount))}
{html.escape(amount.asset_code)}</b></p>
<p>Desde: {sender}<br>Hacia: {receiver}</p>
{actions}
</body></html>"""


def fake_wallet_authorize(fake: FakeOpClient, payment_id: str, decision: str | None) -> Response:
    key = parse_id(payment_id)
    try:
        request = fake.get_payment_request(key)
    except OpError as exc:
        raise AppError(404, "not_found", "No encontramos ese pago.") from exc
    payment = _payment(fake, key)
    if decision is None:
        return HTMLResponse(_authorize_page(payment))
    if payment.status == AWAITING:
        try:
            payment = fake.approve_payment(key) if decision == "approve" else fake.reject_payment(key)
        except ValueError:  # cambió de estado entre la lectura y la decisión
            payment = _payment(fake, key)
    return RedirectResponse(_with_op_params(request.return_url, payment), status_code=302)
