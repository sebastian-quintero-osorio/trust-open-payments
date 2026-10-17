"""Formato único de error: `{"error": {"code", "message", "details"}}` (00-core, "Errores")."""

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

DEFAULT_MESSAGES = {
    "bad_request": "La solicitud no es válida.",
    "unauthorized": "Inicia sesión para continuar.",
    "forbidden": "No tienes permiso para hacer esto.",
    "not_found": "No encontramos lo que buscas.",
    "validation_error": "Revisa los datos enviados.",
    "rate_limited": "Demasiadas solicitudes. Intenta de nuevo en un momento.",
    "op_unavailable": "El servicio de pagos no responde. Intenta de nuevo más tarde.",
    "internal_error": "Ocurrió un error inesperado. Intenta de nuevo más tarde.",
}

_HTTP_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    422: "validation_error",
    429: "rate_limited",
}


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str | None = None, details: dict[str, Any] | None = None):
        self.status = status
        self.code = code
        self.message = message or DEFAULT_MESSAGES.get(code, DEFAULT_MESSAGES["bad_request"])
        self.details = details or {}
        super().__init__(f"{status} {code}: {self.message}")


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def error_response(status: int, code: str, message: str | None = None, details: dict[str, Any] | None = None):
    message = message or DEFAULT_MESSAGES.get(code, DEFAULT_MESSAGES["bad_request"])
    return JSONResponse(status_code=status, content=jsonable_encoder(error_body(code, message, details)))


async def _app_error(_: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status, exc.code, exc.message, exc.details)


def validation_fields(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """`details.fields` de un `validation_error`, desde los errores de Pydantic (`exc.errors()`)."""
    return [
        {
            "field": ".".join(str(part) for part in err.get("loc", ()) if part not in ("body", "query", "path")),
            "loc": [str(part) for part in err.get("loc", ())],
            "type": err.get("type", ""),
            "message": err.get("msg", ""),
        }
        for err in errors
    ]


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(422, "validation_error", details={"fields": validation_fields(exc.errors())})


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_STATUS_CODES.get(exc.status_code)
    if code is None:
        code = "bad_request" if exc.status_code < 500 else "internal_error"
    response = error_response(exc.status_code, code)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Error no controlado", exc_info=exc)
    return error_response(500, "internal_error")


async def _catch_unhandled_errors(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    try:
        return await call_next(request)
    except Exception as exc:
        return await _unhandled_error(request, exc)


def register_error_handlers(app: FastAPI) -> None:
    """Registra los handlers y el middleware que convierte errores no controlados en 500 `internal_error`.

    Se llama **antes** de agregar `CORSMiddleware`: el último middleware agregado queda por fuera, así el 500
    también lleva los headers CORS. El handler de `Exception` queda como respaldo: Starlette lo ejecuta en
    `ServerErrorMiddleware`, por fuera de CORS, y vuelve a lanzar la excepción.
    """
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(Exception, _unhandled_error)
    app.middleware("http")(_catch_unhandled_errors)
