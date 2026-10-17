"""Estado en memoria del mock y las reglas de op-service (`contracts/op-service.openapi.yaml`).

Todo pasa bajo un `RLock`: cada operación es atómica, así que `409 request_in_progress` nunca ocurre aquí.
Los estados avanzan de forma perezosa y determinista con el reloj (`clock`):

- transferencia: `processing` → `completed` cuando se consulta después de `created_at + settle_seconds`;
- pago: `awaiting_authorization` → (consentimiento) `processing` → `completed` en un GET posterior a
  `aprobación + settle_seconds`; sin aprobación pasa a `expired` en `authorization_expires_at + 30 s`;
- pago simulado de Puntored: `completed` en el mismo POST; el webhook lo envía `WebhookDispatcher` en segundo plano.
"""

import hashlib
import json
import secrets
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from tools.mock_op_service.config import MockConfig
from tools.mock_op_service.models import (
    CreateCollectionAccountRequest,
    CreatePaymentRequest,
    CreateSimQrPaymentRequest,
    CreateTransferRequest,
    FailPaymentRequest,
    FailTransferRequest,
    FaultRequest,
    PositiveAmount,
    WalletOverride,
)

IDEMPOTENCY_TTL = timedelta(hours=72)
# failure_code de transferencia → retryable por defecto (descripción de `Transfer.failure_code`).
TRANSFER_FAILURES = {
    "insufficient_balance": True,
    "receiver_unreachable": True,
    "upstream_error": True,
    "internal": True,
    "invalid_amount": False,
    "amount_too_small": False,
    "grant_revoked": False,
}
PAYMENT_FAILURES = frozenset(
    {"insufficient_balance", "quote_expired", "receiver_unreachable", "upstream_error", "internal"}
)
REJECTED_RESULTS = frozenset({"grant_rejected", "request_denied"})
AWAITING = "awaiting_authorization"
OP_PARAMS = ("op_resource", "op_id", "op_status")

Json = dict[str, Any]


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None) -> str | None:
    """RFC 3339 en UTC con `Z`."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value is not None else None


def dumps(payload: Any) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def fingerprint(raw: bytes) -> str:
    """Cuerpo como JSON canónico (claves ordenadas, sin espacios); si no es JSON, el hash de los bytes."""
    try:
        canonical = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except ValueError:
        return "raw:" + hashlib.sha256(raw).hexdigest()
    return "json:" + canonical


class MockError(Exception):
    """Error con el formato del contrato: `{"error": {code, message, retryable}}`."""

    def __init__(self, status: int, code: str, message: str, retryable: bool = False):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message, self.retryable = status, code, message, retryable

    def body(self) -> Json:
        return {"error": {"code": self.code, "message": self.message, "retryable": self.retryable}}


def _not_found(what: str) -> MockError:
    return MockError(404, "not_found", f"{what} no existe.")


def _key(value: Any) -> str | None:
    """Id normalizado; `None` si no es un UUID (→ 404, nunca 422)."""
    try:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
    except ValueError:
        return None


def _host_url(wallet_url: str) -> str:
    parts = urlsplit(wallet_url)
    return f"{parts.scheme or 'https'}://{parts.netloc or 'ilp.interledger-test.dev'}"


@dataclass(frozen=True)
class Amount:
    value: int
    asset_code: str
    asset_scale: int

    @classmethod
    def of(cls, model: PositiveAmount) -> "Amount":
        return cls(int(model.value), model.asset_code, model.asset_scale)

    def same_asset(self, asset_code: str, asset_scale: int) -> bool:
        return (self.asset_code, self.asset_scale) == (asset_code, asset_scale)

    def converted(self, asset_code: str, asset_scale: int) -> "Amount":
        """Mismo valor en otro asset, solo reescalado (el mock no tiene tasas de cambio ni comisiones)."""
        if self.same_asset(asset_code, asset_scale):
            return self
        shift = asset_scale - self.asset_scale
        value = self.value * 10**shift if shift >= 0 else self.value // 10**-shift
        return Amount(value, asset_code, asset_scale)

    def json(self) -> Json:
        return {"value": str(self.value), "asset_code": self.asset_code, "asset_scale": self.asset_scale}


@dataclass(frozen=True)
class Wallet:
    url: str
    public_name: str | None
    asset_code: str
    asset_scale: int

    def json(self) -> Json:
        return {
            "url": self.url,
            "public_name": self.public_name,
            "asset_code": self.asset_code,
            "asset_scale": self.asset_scale,
        }


def normalize_wallet_url(url: str) -> str:
    """`$host/path` → `https://host/path`, sin `/` final. Otro esquema, sin host o con espacios → 422 `invalid_url`."""
    text = ("https://" + url[1:] if url.startswith("$") else url).rstrip("/")
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or any(ch.isspace() for ch in text):
        raise MockError(422, "invalid_url", "La dirección de la wallet no es una URL https válida.")
    return text


def with_op_params(return_url: str, resource: str, resource_id: str, status: str) -> str:
    """Agrega `op_resource`, `op_id` y `op_status` a la query existente (sin borrar `loan_id`, `client`, etc.)."""
    parts = urlsplit(return_url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in OP_PARAMS]
    query += [("op_resource", resource), ("op_id", resource_id), ("op_status", status)]
    return urlunsplit(parts._replace(query=urlencode(query)))


# ── Recursos ──────────────────────────────────────────────────────────────────


@dataclass
class Account:
    id: str
    external_ref: str
    display_name: str
    wallet_address: str
    asset_code: str
    asset_scale: int
    created_at: datetime
    order: int
    status: str = "active"
    revoked_at: datetime | None = None
    credited: int = 0  # entradas del simulador
    debited: int = 0  # transferencias completadas (y lo enviado de las fallidas)

    def json(self, public_base_url: str) -> Json:
        return {
            "id": self.id,
            "external_ref": self.external_ref,
            "display_name": self.display_name,
            "wallet_address": self.wallet_address,
            "qr_payload": f"{public_base_url}/qr/{self.id}",
            "asset_code": self.asset_code,
            "asset_scale": self.asset_scale,
            "status": self.status,
            "ledger_balance": Amount(max(self.credited - self.debited, 0), self.asset_code, self.asset_scale).json(),
            "created_at": iso(self.created_at),
        }


@dataclass
class Transfer:
    id: str
    external_ref: str
    collection_account_id: str
    receiver_wallet_address: str
    receiver_asset: tuple[str, int]
    description: str | None
    debit_amount: Amount
    created_at: datetime
    ready_at: datetime
    status: str = "processing"
    sent_amount: Amount | None = None
    receive_amount: Amount | None = None
    failure_code: str | None = None
    retryable: bool = False
    incoming_payment_url: str | None = None
    outgoing_payment_url: str | None = None
    completed_at: datetime | None = None

    def json(self) -> Json:
        return {
            "id": self.id,
            "external_ref": self.external_ref,
            "collection_account_id": self.collection_account_id,
            "receiver_wallet_address": self.receiver_wallet_address,
            "description": self.description,
            "debit_amount": self.debit_amount.json(),
            "sent_amount": self.sent_amount.json() if self.sent_amount else None,
            "receive_amount": self.receive_amount.json() if self.receive_amount else None,
            "status": self.status,
            "failure_code": self.failure_code,
            "retryable": self.retryable,
            "incoming_payment_url": self.incoming_payment_url,
            "outgoing_payment_url": self.outgoing_payment_url,
            "created_at": iso(self.created_at),
            "completed_at": iso(self.completed_at),
        }


@dataclass
class Payment:
    id: str
    attempt_id: str
    external_ref: str
    sender_wallet_address: str
    receiver_wallet_address: str
    receive_amount: Amount
    debit_amount: Amount
    return_url: str
    authorization_expires_at: datetime
    incoming_payment_url: str
    created_at: datetime
    updated_at: datetime
    status: str = AWAITING
    attempt_used: bool = False
    ready_at: datetime | None = None
    sent_amount: Amount | None = None
    failure_code: str | None = None
    outgoing_payment_url: str | None = None

    def json(self, public_base_url: str) -> Json:
        awaiting = self.status == AWAITING
        return {
            "id": self.id,
            "external_ref": self.external_ref,
            "sender_wallet_address": self.sender_wallet_address,
            "receiver_wallet_address": self.receiver_wallet_address,
            "receive_amount": self.receive_amount.json(),
            "debit_amount": self.debit_amount.json(),
            "sent_amount": self.sent_amount.json() if self.sent_amount else None,
            "status": self.status,
            "authorization_url": f"{public_base_url}/mock/consent/{self.id}" if awaiting else None,
            "authorization_expires_at": iso(self.authorization_expires_at),
            "failure_code": self.failure_code,
            "incoming_payment_url": self.incoming_payment_url,
            "outgoing_payment_url": self.outgoing_payment_url,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


@dataclass
class SimPayment:
    id: str
    seq: int
    collection_account_id: str
    amount: Amount
    payer_bank: str | None
    bre_b_reference: str
    incoming_payment_url: str
    event_bytes: bytes  # el body exacto del webhook: se genera una vez y se reusa en cada reintento
    created_at: datetime
    status: str = "completed"
    webhook_delivered_at: datetime | None = None

    def json(self) -> Json:
        return {
            "id": self.id,
            "seq": self.seq,
            "collection_account_id": self.collection_account_id,
            "amount": self.amount.json(),
            "payer_bank": self.payer_bank,
            "bre_b_reference": self.bre_b_reference,
            "status": self.status,
            "incoming_payment_url": self.incoming_payment_url,
            "webhook_event": json.loads(self.event_bytes) if self.status == "completed" else None,
            "webhook_delivered_at": iso(self.webhook_delivered_at),
            "created_at": iso(self.created_at),
        }


@dataclass
class _Stored:
    fingerprint: str
    status: int
    body: bytes
    stored_at: datetime


@dataclass
class _Fault:
    method: str
    path: str
    status: int
    code: str
    remaining: int

    def matches(self, method: str, path: str) -> bool:
        if self.method != method.upper():
            return False
        return path.startswith(self.path[:-1]) if self.path.endswith("*") else path == self.path


@dataclass
class Reply:
    status: int
    body: bytes
    replayed: bool = False


def _storable(status: int, payload: Json) -> bool:
    """2xx y 4xx definitivas (400, 404, 409 de estado, 422). Nunca 5xx, 401 ni 409 reintentable."""
    if 200 <= status < 300 or status in (400, 404, 422):
        return True
    return status == 409 and not payload.get("error", {}).get("retryable", False)


# ── Store ─────────────────────────────────────────────────────────────────────


@dataclass
class MockStore:
    config: MockConfig
    clock: Callable[[], datetime] = utcnow
    on_sim_completed: Callable[[str], None] | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self._idempotency: dict[tuple[str, str, str], _Stored] = {}
            self._faults: list[_Fault] = []
            self._wallets: dict[str, WalletOverride] = {}
            self._used_pool: set[str] = set()
            self._accounts: dict[str, Account] = {}
            self._transfers: dict[str, Transfer] = {}
            self._payments: dict[str, Payment] = {}
            self._attempts: dict[str, str] = {}
            self._sims: dict[str, SimPayment] = {}
            self._seq = self.config.sim_seq_start

    def now(self) -> datetime:
        return self.clock()

    # ── Idempotencia y fallas inyectadas ──────────────────────────────────────

    def idempotent(
        self, method: str, path: str, key: str | None, raw: bytes, run: Callable[[], tuple[int, Json]]
    ) -> Reply:
        """Alcance (método, ruta, clave). Misma clave + mismo cuerpo → respuesta guardada con su status original;
        otro cuerpo → 409 `idempotency_conflict`. Solo se guardan 2xx y 4xx definitivas, por 72 h."""
        if not key or len(key) > 255:
            error = MockError(400, "validation_error", "Falta el header Idempotency-Key o pasa de 255 caracteres.")
            return Reply(400, dumps(error.body()))
        scope, print_ = (method.upper(), path, key), fingerprint(raw)
        with self.lock:
            now = self.now()
            stored = self._idempotency.get(scope)
            if stored is not None and now - stored.stored_at < IDEMPOTENCY_TTL:
                if stored.fingerprint != print_:
                    error = MockError(409, "idempotency_conflict", "La Idempotency-Key ya se usó con otro cuerpo.")
                    return Reply(409, dumps(error.body()))
                return Reply(stored.status, stored.body, replayed=True)
            try:
                fault = self.take_fault(method, path)
                if fault is not None:
                    raise fault
                status, payload = run()
            except MockError as exc:
                status, payload = exc.status, exc.body()
            body = dumps(payload)
            if _storable(status, payload):
                self._idempotency[scope] = _Stored(print_, status, body, now)
            return Reply(status, body)

    def add_fault(self, body: FaultRequest) -> Json:
        status = body.status
        code = body.code or ("upstream_error" if status >= 500 else "validation_error")
        with self.lock:
            self._faults.append(_Fault(body.method.upper(), body.path, status, code, body.times))
            return {"faults": len(self._faults)}

    def take_fault(self, method: str, path: str) -> MockError | None:
        with self.lock:
            for fault in self._faults:
                if fault.matches(method, path):
                    fault.remaining -= 1
                    if fault.remaining <= 0:
                        self._faults.remove(fault)
                    retryable = fault.status >= 500 or fault.status == 409
                    return MockError(fault.status, fault.code, "Falla inyectada por el mock.", retryable)
            return None

    # ── Wallets ───────────────────────────────────────────────────────────────

    def set_wallet(self, body: WalletOverride) -> Json:
        with self.lock:
            url = normalize_wallet_url(body.url)
            self._wallets[url] = body
            return self.resolve_wallet(url).json() if body.exists else {"url": url, "exists": False}

    def resolve_wallet(self, url: str) -> Wallet:
        with self.lock:
            normalized = normalize_wallet_url(url)
            override = self._wallets.get(normalized)
            if override is not None and not override.exists:
                raise MockError(422, "wallet_not_found", "La wallet no existe.")
            asset_code = override.asset_code if override and override.asset_code else self.config.asset_code
            scale = override.asset_scale if override and override.asset_scale is not None else self.config.asset_scale
            name = override.public_name if override and override.public_name else normalized.rsplit("/", 1)[-1]
            return Wallet(normalized, name, asset_code, scale)

    # ── Cuentas de recaudo ────────────────────────────────────────────────────

    def _account(self, account_id: Any) -> Account:
        key = _key(account_id)
        account = self._accounts.get(key) if key else None
        if account is None:
            raise _not_found("La cuenta de recaudo")
        return account

    def create_collection_account(self, body: CreateCollectionAccountRequest) -> tuple[int, Json]:
        with self.lock:
            if any(a.external_ref == body.external_ref and a.status == "active" for a in self._accounts.values()):
                raise MockError(409, "already_assigned", "Ese external_ref ya tiene una cuenta de recaudo activa.")
            pool = [w for w in self.config.pool_wallet_addresses if w not in self._used_pool]
            if not pool or body.asset_code != self.config.asset_code:
                raise MockError(409, "pool_exhausted", "No hay wallets autorizadas libres en ese asset.")
            account = Account(
                id=str(uuid.uuid4()),
                external_ref=body.external_ref,
                display_name=body.display_name,
                wallet_address=pool[0],
                asset_code=self.config.asset_code,
                asset_scale=self.config.asset_scale,
                created_at=self.now(),
                order=len(self._accounts),
            )
            self._used_pool.add(account.wallet_address)  # una wallet del pool nunca vuelve a asignarse
            self._accounts[account.id] = account
            return 201, account.json(self.config.public_base_url)

    def get_collection_account(self, account_id: str) -> Json:
        with self.lock:
            return self._account(account_id).json(self.config.public_base_url)

    def find_collection_accounts(self, external_ref: str) -> Json:
        """Incluye revocadas, la más reciente primero."""
        with self.lock:
            found = [a for a in self._accounts.values() if a.external_ref == external_ref]
            found.sort(key=lambda a: (a.created_at, a.order), reverse=True)
            return {"items": [a.json(self.config.public_base_url) for a in found]}

    def revoke_collection_account(self, account_id: str) -> None:
        """Idempotente: la cuenta queda `revoked` y su wallet no vuelve al pool."""
        with self.lock:
            account = self._account(account_id)
            if account.status != "revoked":
                account.status, account.revoked_at = "revoked", self.now()

    # ── Transferencias ────────────────────────────────────────────────────────

    def _transfer(self, transfer_id: Any) -> Transfer:
        key = _key(transfer_id)
        transfer = self._transfers.get(key) if key else None
        if transfer is None:
            raise _not_found("La transferencia")
        return transfer

    def create_transfer(self, body: CreateTransferRequest) -> tuple[int, Json]:
        """Valida (cuenta activa, asset, receptor resoluble), guarda y responde `processing` sin URLs de OP."""
        with self.lock:
            account = self._account(body.collection_account_id)
            if account.status != "active":
                raise MockError(409, "collection_account_not_active", "La cuenta de recaudo no está activa.")
            debit = Amount.of(body.debit_amount)
            if not debit.same_asset(account.asset_code, account.asset_scale):
                raise MockError(422, "asset_mismatch", "El monto no está en el asset de la cuenta de recaudo.")
            receiver = self.resolve_wallet(body.receiver_wallet_address)
            now = self.now()
            transfer = Transfer(
                id=str(uuid.uuid4()),
                external_ref=body.external_ref,
                collection_account_id=account.id,
                receiver_wallet_address=body.receiver_wallet_address,
                receiver_asset=(receiver.asset_code, receiver.asset_scale),
                description=body.description,
                debit_amount=debit,
                created_at=now,
                ready_at=now + timedelta(seconds=self.config.settle_seconds),
            )
            self._transfers[transfer.id] = transfer
            return 201, transfer.json()

    def get_transfer(self, transfer_id: str) -> Json:
        with self.lock:
            transfer = self._transfer(transfer_id)
            self._settle_transfer(transfer)
            return transfer.json()

    def _settle_transfer(self, transfer: Transfer) -> None:
        """Ya pasó `ready_at`: se completa, o falla con `grant_revoked` si la cuenta se revocó antes de ejecutarla."""
        if transfer.status != "processing" or self.now() < transfer.ready_at:
            return
        account = self._accounts[transfer.collection_account_id]
        if account.revoked_at is not None and account.revoked_at <= transfer.ready_at:
            self._fail_transfer(transfer, "grant_revoked", False, 0)
            return
        receiver_code, receiver_scale = transfer.receiver_asset
        transfer.status = "completed"
        transfer.sent_amount = transfer.debit_amount
        transfer.receive_amount = transfer.debit_amount.converted(receiver_code, receiver_scale)
        transfer.incoming_payment_url = (
            f"{_host_url(transfer.receiver_wallet_address)}/incoming-payments/{uuid.uuid4()}"
        )
        transfer.outgoing_payment_url = f"{_host_url(account.wallet_address)}/outgoing-payments/{uuid.uuid4()}"
        transfer.completed_at = transfer.ready_at
        account.debited += transfer.debit_amount.value

    def _fail_transfer(self, transfer: Transfer, code: str, retryable: bool, sent: int) -> None:
        account = self._accounts[transfer.collection_account_id]
        debit = transfer.debit_amount
        transfer.status, transfer.failure_code, transfer.retryable = "failed", code, retryable
        transfer.sent_amount = Amount(sent, debit.asset_code, debit.asset_scale)
        transfer.receive_amount = (
            transfer.sent_amount.converted(*transfer.receiver_asset) if sent else None
        )
        account.debited += sent
        if code == "grant_revoked" and account.status != "revoked":
            account.status, account.revoked_at = "revoked", self.now()

    def fail_transfer(self, transfer_id: str, body: FailTransferRequest) -> Json:
        """Control del mock: `processing` → `failed` (con `grant_revoked` la cuenta queda `revoked`, como en Go)."""
        with self.lock:
            transfer = self._transfer(transfer_id)
            if transfer.status != "processing":
                raise MockError(409, "invalid_state", f"La transferencia ya está {transfer.status}.")
            if body.failure_code not in TRANSFER_FAILURES:
                raise MockError(400, "validation_error", f"failure_code debe ser uno de {sorted(TRANSFER_FAILURES)}.")
            sent = int(body.sent_value)
            if sent > transfer.debit_amount.value:
                raise MockError(400, "validation_error", "sent_value no puede superar debit_amount.")
            retryable = TRANSFER_FAILURES[body.failure_code] if body.retryable is None else body.retryable
            self._fail_transfer(transfer, body.failure_code, retryable, sent)
            return transfer.json()

    # ── Pagos (desembolso) ────────────────────────────────────────────────────

    def _payment(self, payment_id: Any) -> Payment:
        key = _key(payment_id)
        payment = self._payments.get(key) if key else None
        if payment is None:
            raise _not_found("El pago")
        return payment

    def create_payment(self, body: CreatePaymentRequest) -> tuple[int, Json]:
        with self.lock:
            parts = urlsplit(body.return_url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise MockError(422, "invalid_url", "return_url no es una URL http(s) absoluta.")
            prefixes = self.config.allowed_return_url_prefixes
            if prefixes and not any(body.return_url.startswith(prefix) for prefix in prefixes):
                raise MockError(422, "return_url_not_allowed", "return_url no está en ALLOWED_RETURN_URL_PREFIXES.")
            sender = self.resolve_wallet(body.sender_wallet_address)
            receiver = self.resolve_wallet(body.receiver_wallet_address)
            receive = Amount.of(body.receive_amount)
            if not receive.same_asset(receiver.asset_code, receiver.asset_scale):
                raise MockError(422, "asset_mismatch", "El monto no está en el asset de la wallet receptora.")
            debit = receive.converted(sender.asset_code, sender.asset_scale)  # la "quote": sin comisiones
            if debit.value <= 0:
                raise MockError(422, "invalid_amount", "El monto convertido al asset del emisor queda en 0.")
            now = self.now()
            payment = Payment(
                id=str(uuid.uuid4()),
                attempt_id=secrets.token_urlsafe(16),
                external_ref=body.external_ref,
                sender_wallet_address=body.sender_wallet_address,
                receiver_wallet_address=body.receiver_wallet_address,
                receive_amount=receive,
                debit_amount=debit,
                return_url=body.return_url,
                authorization_expires_at=now + timedelta(seconds=self.config.interaction_timeout_seconds),
                incoming_payment_url=f"{_host_url(receiver.url)}/incoming-payments/{uuid.uuid4()}",
                created_at=now,
                updated_at=now,
            )
            self._payments[payment.id] = payment
            self._attempts[payment.attempt_id] = payment.id
            return 201, payment.json(self.config.public_base_url)

    def get_payment(self, payment_id: str) -> Json:
        with self.lock:
            payment = self._payment(payment_id)
            self._settle_payment(payment)
            return payment.json(self.config.public_base_url)

    def peek_payment(self, payment_id: str) -> Payment:
        """El pago (ya asentado) para la página de consentimiento; 404 si no existe."""
        with self.lock:
            payment = self._payment(payment_id)
            self._settle_payment(payment)
            return payment

    def _settle_payment(self, payment: Payment) -> None:
        now = self.now()
        if payment.status == AWAITING:
            deadline = payment.authorization_expires_at + timedelta(seconds=self.config.expiry_grace_seconds)
            if now >= deadline and not payment.attempt_used:
                payment.status, payment.updated_at = "expired", deadline
        elif payment.status == "processing" and payment.ready_at is not None and now >= payment.ready_at:
            payment.status, payment.updated_at = "completed", payment.ready_at
            payment.sent_amount = payment.debit_amount

    def consent(self, payment_id: str, decision: str) -> str:
        """Página de consentimiento simulada: `approve` | `reject` → URL del 302 al `return_url`."""
        with self.lock:
            payment = self._payment(payment_id)
            return self._finish(payment, "approve" if decision == "approve" else "grant_rejected")

    def interact_finish(self, attempt_id: str, interact_ref: str | None, hash_: str | None, result: str | None) -> str:
        """`/v1/interact/finish/{attempt_id}` simplificado (sin verificar el hash). 400 si falta todo."""
        with self.lock:
            payment_id = self._attempts.get(attempt_id)
            if payment_id is None:
                raise MockError(400, "validation_error", "El attempt no existe.")
            if not result and not (interact_ref and hash_):
                raise MockError(400, "validation_error", "Faltan result o interact_ref y hash.")
            return self._finish(self._payments[payment_id], result or "approve")

    def _finish(self, payment: Payment, outcome: str) -> str:
        """El attempt es de un solo uso: si ya se usó o el pago no espera, solo redirige con el estado actual."""
        self._settle_payment(payment)
        if payment.status == AWAITING and not payment.attempt_used:
            payment.attempt_used = True
            now = self.now()
            if now > payment.authorization_expires_at or outcome == "invalid_interaction":
                payment.status = "expired"
            elif outcome == "approve":
                payment.status = "processing"
                payment.ready_at = now + timedelta(seconds=self.config.settle_seconds)
                payment.outgoing_payment_url = (
                    f"{_host_url(payment.sender_wallet_address)}/outgoing-payments/{uuid.uuid4()}"
                )
            elif outcome in REJECTED_RESULTS:
                payment.status = "rejected"
            else:
                payment.status, payment.failure_code = "failed", "upstream_error"
            payment.updated_at = now
        return with_op_params(payment.return_url, "payment", payment.id, payment.status)

    def fail_payment(self, payment_id: str, body: FailPaymentRequest) -> Json:
        """Control del mock: `awaiting_authorization` o `processing` → `failed` con `failure_code`."""
        with self.lock:
            payment = self._payment(payment_id)
            self._settle_payment(payment)
            if payment.status not in (AWAITING, "processing"):
                raise MockError(409, "invalid_state", f"El pago ya está {payment.status}.")
            if body.failure_code not in PAYMENT_FAILURES:
                raise MockError(400, "validation_error", f"failure_code debe ser uno de {sorted(PAYMENT_FAILURES)}.")
            payment.status, payment.failure_code, payment.updated_at = "failed", body.failure_code, self.now()
            payment.attempt_used = True
            return payment.json(self.config.public_base_url)

    # ── Simulador de Puntored ─────────────────────────────────────────────────

    def create_sim_qr_payment(self, body: CreateSimQrPaymentRequest) -> tuple[int, Json]:
        """El pago llega a la cuenta de recaudo en el acto (`completed`) y su webhook queda en cola."""
        with self.lock:
            if not self.config.sim_enabled:
                raise MockError(409, "sim_disabled", "El simulador de Puntored está apagado (SIM_ENABLED=false).")
            account = self._account(body.collection_account_id)
            if account.status != "active":
                raise MockError(409, "collection_account_not_active", "La cuenta de recaudo no está activa.")
            amount = Amount.of(body.amount)
            if not amount.same_asset(account.asset_code, account.asset_scale):
                raise MockError(422, "asset_mismatch", "El monto no está en el asset de la cuenta de recaudo.")
            if amount.value > self.config.sim_max_amount:
                limit = self.config.sim_max_amount
                raise MockError(422, "invalid_amount", f"El monto supera SIM_MAX_AMOUNT ({limit}).")
            self._seq += 1
            now = self.now()
            reference = f"BREB-SIM-{self._seq:06d}"
            incoming = f"{_host_url(account.wallet_address)}/incoming-payments/{uuid.uuid4()}"
            event = {
                "event_id": str(uuid.uuid4()),
                "type": "qr_payment.received",
                "occurred_at": iso(now),
                "data": {
                    "collection_account_id": account.id,
                    "collection_wallet_address": account.wallet_address,
                    "amount": amount.json(),
                    "bre_b_reference": reference,
                    "payer_bank": body.payer_bank,
                    "incoming_payment_url": incoming,
                },
            }
            sim = SimPayment(
                id=str(uuid.uuid4()),
                seq=self._seq,
                collection_account_id=account.id,
                amount=amount,
                payer_bank=body.payer_bank,
                bre_b_reference=reference,
                incoming_payment_url=incoming,
                event_bytes=dumps(event),
                created_at=now,
            )
            self._sims[sim.id] = sim
            account.credited += amount.value
            if self.on_sim_completed is not None:
                self.on_sim_completed(sim.id)
            return 201, sim.json()

    def list_sim_qr_payments(self, after: int, limit: int) -> Json:
        """Solo terminales, en orden ascendente por `seq`; `next_after` = último `seq` o el mismo `after`."""
        with self.lock:
            terminal = sorted(
                (s for s in self._sims.values() if s.status in ("completed", "failed") and s.seq > after),
                key=lambda s: s.seq,
            )[:limit]
            return {"items": [s.json() for s in terminal], "next_after": terminal[-1].seq if terminal else after}

    def webhook_payload(self, sim_id: str) -> bytes | None:
        with self.lock:
            sim = self._sims.get(sim_id)
            return sim.event_bytes if sim is not None and sim.status == "completed" else None

    def mark_webhook_delivered(self, sim_id: str) -> None:
        with self.lock:
            sim = self._sims.get(sim_id)
            if sim is not None and sim.webhook_delivered_at is None:
                sim.webhook_delivered_at = self.now()
