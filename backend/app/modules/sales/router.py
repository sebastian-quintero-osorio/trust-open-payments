from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.concurrency import run_in_threadpool

from app.core.clock import LocalDate, UtcDateTime
from app.core.db import DbSession
from app.core.deps import Pagination, require_role
from app.core.errors import error_body
from app.modules.sales import service
from app.modules.sales.schemas import PUNTORED_EVENT_SCHEMA, SaleDetail, SalesPage, SalesToday, WebhookAck
from app.modules.users.models import User

router = APIRouter(tags=["sales"])

Merchant = Annotated[User, Depends(require_role("merchant"))]
MerchantOrAdmin = Annotated[User, Depends(require_role("merchant", "admin"))]


# `/sales/today` va antes de `/sales/{sale_id}` para que "today" no se lea como un id.
@router.get(
    "/sales/today",
    response_model=SalesToday,
    summary='Ventas de hoy en vivo, con su división (pantalla "división en vivo")',
)
def get_sales_today(
    user: Merchant,
    db: DbSession,
    since: Annotated[
        UtcDateTime | None,  # sin zona o fuera de rango en UTC → 422 (R9)
        Query(description="El `next_since` de la respuesta anterior, tal cual (con microsegundos)."),
    ] = None,
) -> SalesToday:
    return service.sales_today(db, user, since)


@router.get("/sales", response_model=SalesPage, summary="Historial de ventas del comercio")
def list_sales(
    user: Merchant,
    db: DbSession,
    page: Pagination,
    from_: Annotated[
        LocalDate | None, Query(alias="from", description="Fecha local (America/Bogota), inclusiva")
    ] = None,
    to: Annotated[LocalDate | None, Query(description="Fecha local (America/Bogota), inclusiva")] = None,
) -> SalesPage:
    return service.list_sales(db, user, from_, to, page)


@router.get(
    "/sales/{sale_id}",
    response_model=SaleDetail,
    summary="Detalle de una venta con su abono y sus tres pagos (comercio dueño o admin)",
)
def get_sale(sale_id: str, user: MerchantOrAdmin, db: DbSession) -> SaleDetail:
    return service.sale_detail(db, user, sale_id)


# ── Webhook de Puntored (R1, R2): HMAC sin JWT, body crudo, nunca 422 ─────────

webhooks_router = APIRouter(tags=["webhooks"])

_ERROR_SCHEMA = {"application/json": {"example": error_body("invalid_signature", service.INVALID_SIGNATURE)}}


@webhooks_router.post(
    "/webhooks/puntored/payments",
    response_model=WebhookAck,
    summary="Pago Bre-B recibido en el QR de un negocio (lo llama Puntored / op-service, no el frontend)",
    responses={
        400: {"description": "`bad_request`: JSON ilegible o sin `event_id`/`type`"},
        401: {"description": "`invalid_signature` o `signature_expired`", "content": _ERROR_SCHEMA},
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": PUNTORED_EVENT_SCHEMA}},
        }
    },
)
async def puntored_payment_webhook(
    request: Request,
    db: DbSession,
    x_puntored_signature: Annotated[str | None, Header()] = None,
) -> WebhookAck:
    raw = await request.body()  # R1: la firma se verifica sobre los bytes exactos, antes de parsear
    result = await run_in_threadpool(service.receive_puntored_webhook, db, raw, x_puntored_signature)
    return WebhookAck(status=result.status)
