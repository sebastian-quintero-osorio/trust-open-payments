"""Tope del cuerpo de las peticiones: un POST enorme no llena la memoria del único proceso (00-core, "Concurrencia").

FastAPI y los routers leen el cuerpo completo (`await request.body()`) antes de revisar la firma o el token. Este
middleware ASGI responde 413 `payload_too_large` sin leer si `Content-Length` supera el tope y, sin `Content-Length`
(chunked), deja de leer en cuanto lo leído lo supera.
"""

from collections.abc import Mapping

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response

DEFAULT_MAX_BODY_BYTES = 1024 * 1024  # 1 MiB: ningún endpoint de la API recibe tanto
SMALL_MAX_BODY_BYTES = 64 * 1024  # 64 KiB: endpoints públicos sin token (webhook de Puntored, lista de espera)


class BodySizeLimitMiddleware:
    def __init__(
        self, app: ASGIApp, default: int = DEFAULT_MAX_BODY_BYTES, per_path: Mapping[str, int] | None = None
    ) -> None:
        self.app = app
        self.default = default
        self.per_path = dict(per_path or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self.per_path.get(scope.get("path", "").rstrip("/"), self.default)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await error_response(413, "payload_too_large")(scope, receive, send)
            return

        received = 0
        too_large = False

        async def limited_receive() -> Message:
            # Al pasar el tope la app ve una desconexión (deja de leer) y su respuesta se descarta: va el 413.
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            if not too_large:
                await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception:
            if not too_large:
                raise
        if too_large:
            await error_response(413, "payload_too_large")(scope, receive, send)
