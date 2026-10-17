"""Envío del webhook `puntoredPayment` a Trust, firmado y con reintentos, en un hilo de fondo.

Firma: `X-Puntored-Signature: t=<unix>,v1=<hex(HMAC-SHA256(TRUST_WEBHOOK_SECRET, t + "." + raw_body))>`, con `t` de
cada envío y siempre los mismos bytes del body (los que guardó el store al completar el pago).
Reintentos si no hay 2xx: a los 1 s, 5 s, 30 s y 2 min (`MOCK_WEBHOOK_RETRY_SECONDS`). Nunca bloquea un request.
"""

import hashlib
import heapq
import hmac
import itertools
import logging
import threading
import time

import httpx

from tools.mock_op_service.config import MockConfig
from tools.mock_op_service.store import MockStore

logger = logging.getLogger("uvicorn.error.mock_op_service")
SIGNATURE_HEADER = "X-Puntored-Signature"


def sign(secret: str, raw: bytes, t: int) -> str:
    digest = hmac.new(secret.encode(), str(t).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


class WebhookDispatcher:
    def __init__(self, store: MockStore, config: MockConfig, client: httpx.Client | None = None):
        self._store = store
        self._config = config
        self._client = client
        self._owns_client = client is None
        self._cond = threading.Condition()
        self._queue: list[tuple[float, int, str, int]] = []  # (vence, desempate, sim_id, intento)
        self._counter = itertools.count()
        self._inflight = 0
        self._stopping = False
        self._thread: threading.Thread | None = None
        self.attempts: list[tuple[str, int | None]] = []  # (sim_id, status HTTP o None si no conectó)

    def enqueue(self, sim_id: str) -> None:
        with self._cond:
            heapq.heappush(self._queue, (time.monotonic(), next(self._counter), sim_id, 0))
            if self._thread is None or not self._thread.is_alive():
                self._stopping = False
                self._thread = threading.Thread(target=self._run, name="mock-op-webhooks", daemon=True)
                self._thread.start()
            self._cond.notify_all()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Espera a que no quede nada en cola ni en vuelo (tests)."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._queue and not self._inflight, timeout)

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stopping:
                    if self._queue:
                        delay = self._queue[0][0] - time.monotonic()
                        if delay <= 0:
                            break
                        self._cond.wait(delay)
                    else:
                        self._cond.wait()
                if self._stopping:
                    return
                _, _, sim_id, attempt = heapq.heappop(self._queue)
                self._inflight += 1
            try:
                done = self._deliver(sim_id)
            except Exception:  # nunca tumbar el hilo
                logger.exception("mock op-service: falló el envío del webhook del pago %s", sim_id)
                done = False
            with self._cond:
                self._inflight -= 1
                retries = self._config.webhook_retry_seconds
                if not done and attempt < len(retries):
                    due = time.monotonic() + retries[attempt]
                    heapq.heappush(self._queue, (due, next(self._counter), sim_id, attempt + 1))
                elif not done:
                    logger.warning("mock op-service: webhook del pago %s sin 2xx tras %d intentos", sim_id, attempt + 1)
                self._cond.notify_all()

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._config.webhook_timeout_seconds)
        return self._client

    def _deliver(self, sim_id: str) -> bool:
        """Un intento. `True` = terminado (2xx, o nada que enviar); `False` = reintentar."""
        raw = self._store.webhook_payload(sim_id)
        url = self._config.webhook_url
        if raw is None:
            return True  # el store se reinició
        if not url:
            logger.info("mock op-service: sin TRUST_WEBHOOK_URL; el pago %s no se notifica", sim_id)
            return True
        headers = {
            "Content-Type": "application/json",
            SIGNATURE_HEADER: sign(self._config.webhook_secret, raw, int(time.time())),
        }
        try:
            response = self._http().post(url, content=raw, headers=headers)
        except httpx.HTTPError as exc:
            self.attempts.append((sim_id, None))
            logger.warning("mock op-service: webhook %s → %s (%s)", sim_id, url, type(exc).__name__)
            return False
        self.attempts.append((sim_id, response.status_code))
        logger.info("mock op-service: webhook %s → %s %d", sim_id, url, response.status_code)
        if 200 <= response.status_code < 300:
            self._store.mark_webhook_delivered(sim_id)
            return True
        return False
