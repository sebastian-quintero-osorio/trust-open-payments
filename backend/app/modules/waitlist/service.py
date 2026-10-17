"""Lista de espera (15-waitlist): límite por IP en memoria (R7), honeypot (R4) y upsert por correo (R3)."""

import json
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from pydantic import ValidationError
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import validation_fields
from app.modules.waitlist.models import WaitlistEntry
from app.modules.waitlist.schemas import WaitlistRequest

RATE_LIMIT = 10  # peticiones
RATE_WINDOW_SECONDS = 60.0
MAX_KEYS = 10_000  # IPs recordadas a la vez: tope de memoria aunque lleguen muchas IPs distintas en la ventana


class RateLimiter:
    """Ventana deslizante por clave (IP), en memoria: hay un solo proceso (00-core, "Concurrencia").

    Las claves van en orden de último uso: las que salieron de la ventana se borran desde el frente y, si aun así
    hay más de `max_keys`, se olvidan las menos recientes (la memoria queda acotada).
    """

    def __init__(
        self,
        limit: int = RATE_LIMIT,
        window: float = RATE_WINDOW_SECONDS,
        now: Callable[[], float] = time.monotonic,
        max_keys: int = MAX_KEYS,
    ):
        self.limit = limit
        self.window = window
        self.now = now
        self.max_keys = max_keys
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def hit(self, key: str) -> int | None:
        """Cuenta la petición. `None` si pasa; si supera el límite, los segundos hasta que vuelva a pasar (no se
        cuenta: el bloqueo dura hasta que la más vieja de las 10 sale de la ventana)."""
        now = self.now()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            self._hits.move_to_end(key)
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, math.ceil(hits[0] + self.window - now))
            hits.append(now)
            self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        while self._hits:
            oldest_key, oldest = next(iter(self._hits.items()))
            expired = not oldest or oldest[-1] <= now - self.window
            if not expired and len(self._hits) <= self.max_keys:
                break
            del self._hits[oldest_key]

    def __len__(self) -> int:
        return len(self._hits)

    def reset(self) -> None:
        """Olvida todas las peticiones (tests)."""
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def is_spam(raw: dict[str, Any]) -> bool:
    """R4: `website` (honeypot) con cualquier valor no vacío."""
    value = raw.get("website")
    if value is None:
        return False
    return bool(value.strip()) if isinstance(value, str) else True


def upsert(db: Session, data: WaitlistRequest) -> bool:
    """R3: inserta (→ `True`) o, si el correo ya estaba, actualiza `tipo`, `nombre` y `ciudad` (→ `False`).

    `INSERT … ON CONFLICT (correo) DO NOTHING` y después `UPDATE`: dos envíos simultáneos con el mismo correo no
    chocan nunca (el segundo queda como actualización). `correo` llega y se guarda en minúsculas, así que comparar
    `correo` es comparar `lower(correo)`.
    """
    now = clock.now()
    fields = {"tipo": data.tipo, "nombre": data.nombre, "ciudad": data.ciudad}
    inserted = db.scalar(
        pg_insert(WaitlistEntry)
        .values(correo=data.correo, created_at=now, updated_at=now, **fields)
        .on_conflict_do_nothing(index_elements=[WaitlistEntry.correo])
        .returning(WaitlistEntry.id)
    )
    if inserted is None:
        db.execute(
            update(WaitlistEntry)
            .where(WaitlistEntry.correo == data.correo)
            .values(updated_at=now, **fields)
            .execution_options(synchronize_session=False)
        )
    db.commit()
    return inserted is not None


# ── Cuerpo de la petición (R2) ────────────────────────────────────────────────

FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
_FORM_MAX_FIELDS = 20


class InvalidSubmission(Exception):
    """Cuerpo ilegible o datos inválidos: 422 con JSON, 303 a `?lista=error` con el formulario."""

    def __init__(self, fields: list[dict[str, Any]]):
        self.fields = fields
        super().__init__("Envío inválido a la lista de espera")


def is_form(content_type: str | None) -> bool:
    """R2: el formulario sin JS llega como urlencoded; cualquier otro `Content-Type` se lee como JSON."""
    return (content_type or "").split(";", 1)[0].strip().lower() == FORM_CONTENT_TYPE


def parse_body(body: bytes, form: bool) -> dict[str, Any]:
    """Urlencoded con `urllib.parse.parse_qs` (sin `python-multipart`; primer valor de cada campo) o JSON (objeto)."""
    try:
        if form:
            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=_FORM_MAX_FIELDS)
            return {key: values[0] for key, values in parsed.items()}
        data = json.loads(body)
    except (ValueError, RecursionError):  # JSON malformado o demasiado anidado, UTF-8 inválido o demasiados campos
        raise InvalidSubmission([_body_error("El cuerpo no se puede leer.")]) from None
    if not isinstance(data, dict):
        raise InvalidSubmission([_body_error("El cuerpo debe ser un objeto JSON.")])
    return data


def validate(raw: dict[str, Any]) -> WaitlistRequest:
    try:
        return WaitlistRequest.model_validate(raw)
    except ValidationError as exc:
        errors = [{**err, "loc": ("body", *err.get("loc", ()))} for err in exc.errors()]
        raise InvalidSubmission(validation_fields(errors)) from None


def _body_error(message: str) -> dict[str, Any]:
    return {"field": "", "loc": ["body"], "type": "invalid_body", "message": message}
