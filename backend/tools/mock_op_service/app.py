"""Mock HTTP de op-service (Go) en memoria. Arranque (desde `backend/`):

    uv run uvicorn tools.mock_op_service.app:app --port 8090

Implementa lo que llama el `HttpOpClient` de Trust según `contracts/op-service.openapi.yaml`, más una página de
consentimiento simulada (`/mock/consent/{payment_id}`) y controles `/mock/*` para demos y tests. Ver README.md.
"""

import hmac
import html
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from tools.mock_op_service.config import MockConfig
from tools.mock_op_service.models import (
    CreateCollectionAccountRequest,
    CreatePaymentRequest,
    CreateSimQrPaymentRequest,
    CreateTransferRequest,
    FailPaymentRequest,
    FailTransferRequest,
    FaultRequest,
    WalletOverride,
)
from tools.mock_op_service.store import AWAITING, MockError, MockStore, dumps, utcnow
from tools.mock_op_service.webhooks import WebhookDispatcher, logger

VERSION = "mock-0.1.0"
REPLAYED_HEADER = "Idempotent-Replayed"


def _json(payload: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(dumps(payload), status_code=status, headers=headers, media_type="application/json")


def _error(exc: MockError) -> Response:
    return _json(exc.body(), exc.status)


def _validation_message(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:5]:
        location = ".".join(str(p) for p in error.get("loc", ())) or "body"
        parts.append(f"{location}: {error.get('msg', 'inválido')}")
    return "Cuerpo inválido: " + "; ".join(parts)


def _parse[M: BaseModel](model: type[M], raw: bytes) -> M:
    try:
        return model.model_validate_json(raw)
    except ValidationError as exc:
        raise MockError(400, "validation_error", _validation_message(exc)) from exc


def _int_param(request: Request, name: str, default: int, low: int, high: int | None = None) -> int:
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = low - 1
    if value < low or (high is not None and value > high):
        bounds = f"entre {low} y {high}" if high is not None else f"≥ {low}"
        raise MockError(400, "validation_error", f"{name} debe ser un entero {bounds}.")
    return value


def _html_page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:30rem;margin:3rem auto;padding:0 1rem;color:#1f2937}}
a.btn{{display:inline-block;margin:.5rem .5rem 0 0;padding:.7rem 1.4rem;border-radius:.5rem;text-decoration:none;
color:#fff;background:#4b5563}}a.ok{{background:#15803d}}a.no{{background:#b91c1c}}code{{word-break:break-all}}</style>
</head><body>{body}</body></html>""",
        status_code=status,
    )


def _format_amount(value: int, scale: int) -> str:
    units, cents = divmod(value, 10**scale) if scale else (value, 0)
    text = f"{units:,}".replace(",", ".")
    return f"{text},{cents:0{scale}d}" if scale else text


def create_app(
    config: MockConfig | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    webhook_client: httpx.Client | None = None,
) -> FastAPI:
    """App del mock. Sin argumentos lee el entorno (`MockConfig.from_env()`); los tests pasan su config y reloj."""
    config = config or MockConfig.from_env()
    store = MockStore(config, clock=clock or utcnow)
    dispatcher = WebhookDispatcher(store, config, webhook_client)
    store.on_sim_completed = dispatcher.enqueue

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info(
            "mock op-service listo: %d wallets en el pool (%s/%d), webhook → %s",
            len(config.pool_wallet_addresses),
            config.asset_code,
            config.asset_scale,
            config.webhook_url or "(sin TRUST_WEBHOOK_URL)",
        )
        yield
        dispatcher.stop()

    app = FastAPI(title="Mock op-service (Trust)", version=VERSION, lifespan=lifespan)
    app.state.config, app.state.store, app.state.dispatcher = config, store, dispatcher

    @app.exception_handler(MockError)
    async def _mock_error(_: Request, exc: MockError) -> Response:
        return _error(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> Response:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "validation_error")
        return _error(MockError(exc.status_code, code, str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> Response:
        return _error(MockError(400, "validation_error", f"Petición inválida: {exc.errors()[:3]}"))

    def require_api_key(x_internal_api_key: str | None = Header(default=None)) -> None:
        if x_internal_api_key is None or not hmac.compare_digest(
            x_internal_api_key.encode(), config.api_key.encode()
        ):
            raise MockError(401, "unauthorized", "Falta X-Internal-Api-Key o no es válida.")

    def injected_fault(request: Request) -> None:
        """Fallas de `/mock/faults` para los GET/DELETE (los POST las aplican tras mirar la idempotencia)."""
        if request.method != "POST":
            fault = store.take_fault(request.method, request.url.path)
            if fault is not None:
                raise fault

    public = APIRouter()
    internal = APIRouter(dependencies=[Depends(require_api_key), Depends(injected_fault)])
    controls = APIRouter(prefix="/mock", dependencies=[Depends(require_api_key)])

    async def idempotent_post(request: Request, model: type[BaseModel], action: Callable[[Any], tuple[int, Any]]):
        raw = await request.body()
        reply = store.idempotent(
            "POST",
            request.url.path,
            request.headers.get("Idempotency-Key"),
            raw,
            lambda: action(_parse(model, raw)),
        )
        headers = {REPLAYED_HEADER: "true"} if reply.replayed else None
        return Response(reply.body, status_code=reply.status, headers=headers, media_type="application/json")

    # ── Plataforma y wallets ──────────────────────────────────────────────────

    @public.get("/v1/health")
    def health() -> Response:
        return _json({"status": "ok", "client_wallet_address": config.client_wallet_address, "version": VERSION})

    @internal.get("/v1/wallet-addresses")
    def resolve_wallet_address(request: Request) -> Response:
        url = request.query_params.get("url")
        if not url:
            raise MockError(400, "validation_error", "Falta el parámetro url.")
        return _json(store.resolve_wallet(url).json())

    # ── Cuentas de recaudo ────────────────────────────────────────────────────

    @internal.post("/v1/collection-accounts")
    async def create_collection_account(request: Request) -> Response:
        return await idempotent_post(request, CreateCollectionAccountRequest, store.create_collection_account)

    @internal.get("/v1/collection-accounts")
    def list_collection_accounts(request: Request) -> Response:
        external_ref = request.query_params.get("external_ref")
        if external_ref is None:
            raise MockError(400, "validation_error", "Falta el parámetro external_ref.")
        return _json(store.find_collection_accounts(external_ref))

    @internal.get("/v1/collection-accounts/{collection_account_id}")
    def get_collection_account(collection_account_id: str) -> Response:
        return _json(store.get_collection_account(collection_account_id))

    @internal.delete("/v1/collection-accounts/{collection_account_id}")
    def revoke_collection_account(collection_account_id: str) -> Response:
        store.revoke_collection_account(collection_account_id)
        return Response(status_code=204)

    # ── Transferencias ────────────────────────────────────────────────────────

    @internal.post("/v1/transfers")
    async def create_transfer(request: Request) -> Response:
        return await idempotent_post(request, CreateTransferRequest, store.create_transfer)

    @internal.get("/v1/transfers/{transfer_id}")
    def get_transfer(transfer_id: str) -> Response:
        return _json(store.get_transfer(transfer_id))

    # ── Pagos ─────────────────────────────────────────────────────────────────

    @internal.post("/v1/payments")
    async def create_payment(request: Request) -> Response:
        return await idempotent_post(request, CreatePaymentRequest, store.create_payment)

    @internal.get("/v1/payments/{payment_id}")
    def get_payment(payment_id: str) -> Response:
        return _json(store.get_payment(payment_id))

    # ── Simulador de Puntored ─────────────────────────────────────────────────

    @internal.post("/v1/sim/puntored/qr-payments")
    async def create_sim_qr_payment(request: Request) -> Response:
        return await idempotent_post(request, CreateSimQrPaymentRequest, store.create_sim_qr_payment)

    @internal.get("/v1/sim/puntored/qr-payments")
    def list_sim_qr_payments(request: Request) -> Response:
        after = _int_param(request, "after", 0, 0)
        limit = _int_param(request, "limit", 100, 1, 200)
        return _json(store.list_sim_qr_payments(after, limit))

    # ── Navegador: consentimiento simulado ────────────────────────────────────

    @public.get("/v1/interact/finish/{attempt_id}")
    def interact_finish(
        attempt_id: str,
        interact_ref: str | None = None,
        hash_: str | None = Query(default=None, alias="hash"),
        result: str | None = None,
    ) -> Response:
        try:
            location = store.interact_finish(attempt_id, interact_ref, hash_, result)
        except MockError as exc:
            return _html_page("Error", f"<h1>No pudimos terminar la autorización</h1><p>{html.escape(exc.message)}</p>",
                              400)
        return RedirectResponse(location, status_code=302)

    @public.get("/mock/consent/{payment_id}")
    def consent(payment_id: str, decision: str | None = None) -> Response:
        """La "wallet" del prestamista: sin `decision` muestra Aprobar/Rechazar; con ella, 302 al `return_url`."""
        try:
            payment = store.peek_payment(payment_id)
        except MockError:
            return _html_page("Pago no encontrado", "<h1>Pago no encontrado</h1>", 404)
        if decision is None:
            amount = payment.debit_amount
            pending = payment.status == AWAITING
            actions = (
                '<a class="btn ok" href="?decision=approve">Aprobar</a>'
                '<a class="btn no" href="?decision=reject">Rechazar</a>'
                if pending
                else f'<p>Este pago ya está <b>{html.escape(payment.status)}</b>.</p>'
                '<a class="btn" href="?decision=approve">Volver a la app</a>'
            )
            return _html_page(
                "Autorizar pago",
                f"<h1>Autorizar pago</h1><p>Wallet de prueba (mock de op-service).</p>"
                f"<p>Monto a debitar: <b>{_format_amount(amount.value, amount.asset_scale)} "
                f"{html.escape(amount.asset_code)}</b></p>"
                f"<p>Desde: <code>{html.escape(payment.sender_wallet_address)}</code><br>"
                f"Hacia: <code>{html.escape(payment.receiver_wallet_address)}</code></p>{actions}",
            )
        if decision not in ("approve", "reject"):
            return _html_page("Decisión inválida", "<h1>decision debe ser approve o reject</h1>", 400)
        return RedirectResponse(store.consent(payment_id, decision), status_code=302)

    # ── Controles del mock (no son parte del contrato) ────────────────────────

    @controls.post("/reset")
    def reset() -> Response:
        store.reset()
        return Response(status_code=204)

    @controls.post("/wallets")
    async def set_wallet(request: Request) -> Response:
        return _json(store.set_wallet(_parse(WalletOverride, await request.body())))

    @controls.post("/faults")
    async def add_fault(request: Request) -> Response:
        return _json(store.add_fault(_parse(FaultRequest, await request.body())), 201)

    @controls.post("/transfers/{transfer_id}/fail")
    async def fail_transfer(transfer_id: str, request: Request) -> Response:
        return _json(store.fail_transfer(transfer_id, _parse(FailTransferRequest, await request.body())))

    @controls.post("/payments/{payment_id}/fail")
    async def fail_payment(payment_id: str, request: Request) -> Response:
        return _json(store.fail_payment(payment_id, _parse(FailPaymentRequest, await request.body())))

    app.include_router(public)
    app.include_router(internal)
    app.include_router(controls)
    return app


app = create_app()
