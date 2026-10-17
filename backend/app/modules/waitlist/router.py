from ipaddress import ip_address, ip_network
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.core.db import DbSession
from app.core.errors import AppError, error_response
from app.modules.waitlist import service
from app.modules.waitlist.schemas import WaitlistRequest, WaitlistResult

router = APIRouter(tags=["waitlist"])


async def raw_body(request: Request) -> bytes:
    """El cuerpo crudo: JSON o urlencoded se leen a mano (R2), sin `python-multipart`."""
    return await request.body()


RawBody = Annotated[bytes, Depends(raw_body)]

_REQUEST_SCHEMA = WaitlistRequest.model_json_schema()
_OPENAPI_EXTRA = {
    "security": [],
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": _REQUEST_SCHEMA},
            service.FORM_CONTENT_TYPE: {"schema": _REQUEST_SCHEMA},
        },
    },
}


def _trusted_proxy(host: str) -> bool:
    """`host` está en `TRUSTED_PROXIES` (IP o nombre exacto, o dentro de una red CIDR)."""
    for entry in get_settings().trusted_proxies:
        if entry == host:
            return True
        try:
            if ip_address(host) in ip_network(entry, strict=False):
                return True
        except ValueError:  # `host` o `entry` no es una IP/red (p. ej. "testclient")
            continue
    return False


def client_ip(request: Request) -> str:
    """R7: la IP de la conexión; el primer valor de `X-Forwarded-For` solo si la conexión viene de un proxy de
    `TRUSTED_PROXIES` (Caddy reescribe el header). Sin esa condición cualquiera inventaba una IP por petición y se
    saltaba el límite."""
    peer = request.client.host if request.client else None
    if peer is not None and _trusted_proxy(peer):
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return peer or "desconocida"


def _back_to_landing(ok: bool) -> RedirectResponse:
    """R5: el formulario sin JS vuelve a la sección de la lista en la landing."""
    lista = "ok" if ok else "error"
    return RedirectResponse(f"{get_settings().landing_url.rstrip('/')}/?lista={lista}#lista-de-espera", 303)


@router.post(
    "/waitlist",
    status_code=201,
    response_model=WaitlistResult,
    summary="Inscribirse en la lista de espera (formulario de la landing)",
    responses={
        200: {"model": WaitlistResult, "description": "Ya estaba inscrito (JSON); se actualizan tipo, nombre y ciudad"},
        303: {"description": "Envío por formulario (sin JS): `?lista=ok` o `?lista=error`"},
    },
    openapi_extra=_OPENAPI_EXTRA,
)
def join_waitlist(request: Request, body: RawBody, db: DbSession) -> Response:
    form = service.is_form(request.headers.get("content-type"))

    retry_after = service.limiter.hit(client_ip(request))  # R7: cuenta toda petición, antes que nada
    if retry_after is not None:
        if form:
            return _back_to_landing(ok=False)
        response = error_response(429, "rate_limited")
        response.headers["Retry-After"] = str(retry_after)
        return response

    try:
        raw = service.parse_body(body, form)
        if service.is_spam(raw):  # R4: éxito aparente, sin guardar (antes de validar: no le da pistas al bot)
            return _back_to_landing(ok=True) if form else JSONResponse({"status": "created"}, status_code=201)
        data = service.validate(raw)
    except service.InvalidSubmission as exc:
        if form:
            return _back_to_landing(ok=False)
        raise AppError(422, "validation_error", details={"fields": exc.fields}) from None

    created = service.upsert(db, data)  # R3: nunca 409
    if form:
        return _back_to_landing(ok=True)
    result = WaitlistResult(status="created" if created else "exists")
    return JSONResponse(result.model_dump(), status_code=201 if created else 200)
