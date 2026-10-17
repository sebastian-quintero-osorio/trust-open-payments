"""`FakeOpClient`: op-service en memoria para construir y probar Trust sin Go (02 R9–R20).

Valida como Go (404 / 409 / 422 con los mismos códigos), guarda la idempotencia por (método, clave) y expone
controles para los tests y para `/dev/*`: `fail_next`, `set_wallet`, `complete_transfer`, `approve_payment`, etc.
Todo está protegido con un `RLock`; los ayudantes privados no registran llamadas (solo los métodos del Protocol).
"""

import secrets
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, get_args
from urllib.parse import urlsplit

from pydantic import BaseModel

from app.core import clock
from app.core.config import get_settings
from app.core.money import Money
from app.integrations.op.client import IdLike, OpError, OpUnavailable, canonical_json
from app.integrations.op.schemas import (
    Amount,
    CollectionAccount,
    CreateCollectionAccountRequest,
    CreatePaymentRequest,
    CreateSimQrPaymentRequest,
    CreateTransferRequest,
    Health,
    Payment,
    PaymentFailureCode,
    PositiveAmount,
    PuntoredPaymentData,
    PuntoredPaymentEvent,
    SimQrPayment,
    SimQrPaymentList,
    Transfer,
    TransferFailureCode,
    WalletAddress,
)

FAKE_NAMESPACE = uuid.UUID("0b6f4c2e-8d3a-5e71-9f20-7c41a5d9e3b8")
POOL_WALLET_PREFIX = "https://ilp.interledger-test.dev/trust-recaudo-"
QR_PAYLOAD_PREFIX = "https://pay.trustservices.com.co/qr/"
CLIENT_WALLET_ADDRESS = "https://ilp.interledger-test.dev/trust"
FAKE_OP_BASE = "https://ilp.interledger-test.dev/fake"
AUTHORIZATION_TTL = timedelta(minutes=10)

OP_METHODS = frozenset(
    {
        "resolve_wallet_address",
        "create_collection_account",
        "get_collection_account",
        "find_collection_accounts",
        "revoke_collection_account",
        "create_transfer",
        "get_transfer",
        "create_payment",
        "get_payment",
        "create_sim_qr_payment",
        "list_sim_qr_payments",
        "health",
    }
)

@dataclass(frozen=True)
class FakeCall:
    """Una llamada a un método del `OpClient`: `args` son sus argumentos por nombre (sin la clave)."""

    method: str
    args: dict[str, Any]
    idempotency_key: str | None = None

    @property
    def body(self) -> Any:
        return self.args.get("body")


@dataclass(frozen=True)
class _WalletOverride:
    asset_code: str | None
    asset_scale: int | None
    public_name: str | None
    exists: bool


def normalize_wallet_url(url: str) -> str:
    """`$host/path` → `https://host/path`, sin `/` final. Otro esquema o sin host → `OpError 422 invalid_url`."""
    text = url if isinstance(url, str) else ""
    if text.startswith("$"):
        text = "https://" + text[1:]
    text = text.rstrip("/")
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.netloc or any(ch.isspace() for ch in text):
        raise OpError(422, "invalid_url", "La dirección de la wallet no es una URL https válida.")
    return text


def _key(value: IdLike) -> str | None:
    """Id normalizado (UUID en texto); `None` si no es un UUID (Go responde 404)."""
    try:
        return str(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
    except ValueError:
        return None


def _copy[M: BaseModel](model: M) -> M:
    return model.model_copy(deep=True)


def _amount(value: Amount | Money) -> Amount:
    return Amount.from_money(value) if isinstance(value, Money) else Amount(**value.model_dump())


class FakeOpClient:
    def __init__(self, salt: str | None = None, auto_complete: bool | None = None, pool_size: int = 5):
        # Sin salt: uno aleatorio, para no chocar con ids de Go ya guardados en Postgres (R11).
        self.salt = salt if salt is not None else secrets.token_hex(8)
        self._default_auto_complete = (
            auto_complete if auto_complete is not None else get_settings().op_fake_auto_complete
        )
        self._default_pool_size = pool_size
        self._lock = threading.RLock()
        self.reset()

    # ── Controles (R12) ───────────────────────────────────────────────────────

    def reset(self, pool_size: int | None = None) -> None:
        """Vuelve al estado inicial (pool lleno, sin llamadas, sin fallas inyectadas, contadores en 0)."""
        with self._lock:
            self.auto_complete = self._default_auto_complete
            self.pool_size = pool_size if pool_size is not None else self._default_pool_size
            self.calls: list[FakeCall] = []
            self._failures: dict[str, deque[Exception]] = defaultdict(deque)
            self._idempotency: dict[tuple[str, str], tuple[str, BaseModel | OpError]] = {}
            self._counters: dict[str, int] = defaultdict(int)
            self._wallets: dict[str, _WalletOverride] = {}
            self._pool = [f"{POOL_WALLET_PREFIX}{n:02d}" for n in range(1, self.pool_size + 1)]
            self._used_wallets: set[str] = set()
            self._accounts: dict[str, CollectionAccount] = {}
            self._account_order: dict[str, int] = {}
            self._transfers: dict[str, Transfer] = {}
            self._payments: dict[str, Payment] = {}
            self._payment_requests: dict[str, CreatePaymentRequest] = {}
            self._sim_payments: list[SimQrPayment] = []
            self._seq = 0

    def calls_to(self, method: str) -> list[FakeCall]:
        with self._lock:
            return [call for call in self.calls if call.method == method]

    def fail_next(self, method: str, exc: Exception) -> None:
        """La próxima llamada a `method` lanza `exc` (`OpError` u `OpUnavailable`). No se guarda en la idempotencia.

        Varias llamadas seguidas encolan varias fallas, en orden.
        """
        if method not in OP_METHODS:
            raise ValueError(f"FakeOpClient no tiene el método {method!r}")
        with self._lock:
            self._failures[method].append(exc)

    def set_wallet(
        self,
        url: str,
        asset_code: str | None = None,
        asset_scale: int | None = None,
        public_name: str | None = None,
        exists: bool = True,
    ) -> None:
        """Fija cómo resuelve una wallet (sin asset: el de mercado). `exists=False` → `422 wallet_not_found`."""
        with self._lock:
            self._wallets[normalize_wallet_url(url)] = _WalletOverride(asset_code, asset_scale, public_name, exists)

    def resume_seq_after(self, seq: int) -> None:
        """El próximo pago simulado usa un `seq` mayor que `seq` (reinicio en dev: 02 R15)."""
        with self._lock:
            self._seq = max(self._seq, seq)

    # ── Inspección ────────────────────────────────────────────────────────────

    @property
    def collection_accounts(self) -> list[CollectionAccount]:
        with self._lock:
            return [_copy(a) for a in self._accounts.values()]

    @property
    def transfers(self) -> list[Transfer]:
        with self._lock:
            return [_copy(t) for t in self._transfers.values()]

    @property
    def payments(self) -> list[Payment]:
        with self._lock:
            return [_copy(p) for p in self._payments.values()]

    @property
    def sim_qr_payments(self) -> list[SimQrPayment]:
        with self._lock:
            return [_copy(s) for s in self._sim_payments]

    def get_payment_request(self, payment_id: IdLike) -> CreatePaymentRequest:
        """El request con que se creó el pago (para el `return_url` de `/dev/fake-wallet`, 03 R13)."""
        with self._lock:
            self._payment(payment_id)
            return _copy(self._payment_requests[_key(payment_id)])

    # ── Infraestructura común ─────────────────────────────────────────────────

    def _enter(self, method: str, args: dict[str, Any], idempotency_key: str | None = None) -> None:
        """Registra la llamada y lanza la falla inyectada con `fail_next`, si hay una."""
        self.calls.append(FakeCall(method, args, idempotency_key))
        queue = self._failures.get(method)
        if queue:
            raise queue.popleft()

    def _idempotent[M: BaseModel](
        self, method: str, body: BaseModel, idempotency_key: str, action: Callable[[], M]
    ) -> M:
        """R10: misma clave + mismo cuerpo → mismo resultado (respuesta o `OpError` 4xx); otro cuerpo → 409."""
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 255:
            raise OpError(400, "validation_error", "Falta la Idempotency-Key o es demasiado larga.")
        fingerprint = canonical_json(body)
        stored = self._idempotency.get((method, idempotency_key))
        if stored is not None:
            stored_fingerprint, result = stored
            if stored_fingerprint != fingerprint:
                raise OpError(409, "idempotency_conflict", "La Idempotency-Key ya se usó con otro cuerpo.", False)
            if isinstance(result, OpError):
                raise OpError(result.status, result.code, result.message, result.retryable)
            return _copy(result)
        try:
            result = action()
        except OpError as exc:
            if not exc.retryable:  # Go guarda las 4xx definitivas
                self._idempotency[(method, idempotency_key)] = (fingerprint, exc)
            raise
        self._idempotency[(method, idempotency_key)] = (fingerprint, _copy(result))
        return _copy(result)

    def _new_id(self, kind: str) -> uuid.UUID:
        """R11: `uuid5(NS, f"{salt}:{tipo}:{n}")` con contador por tipo."""
        self._counters[kind] += 1
        return uuid.uuid5(FAKE_NAMESPACE, f"{self.salt}:{kind}:{self._counters[kind]}")

    def _resolve(self, url: str) -> WalletAddress:
        normalized = normalize_wallet_url(url)
        override = self._wallets.get(normalized)
        if override is not None and not override.exists:
            raise OpError(422, "wallet_not_found", "La wallet no existe.")
        settings = get_settings()
        asset_code = override.asset_code if override and override.asset_code else settings.market_asset_code
        asset_scale = (
            override.asset_scale if override and override.asset_scale is not None else settings.market_asset_scale
        )
        public_name = override.public_name if override and override.public_name else normalized.rsplit("/", 1)[-1]
        return WalletAddress(url=normalized, public_name=public_name, asset_code=asset_code, asset_scale=asset_scale)

    # ── Wallets (R13) ─────────────────────────────────────────────────────────

    def resolve_wallet_address(self, url: str) -> WalletAddress:
        with self._lock:
            self._enter("resolve_wallet_address", {"url": url})
            return self._resolve(url)

    # ── Cuentas de recaudo (R14) ──────────────────────────────────────────────

    def _account(self, collection_account_id: IdLike) -> CollectionAccount:
        key = _key(collection_account_id)
        account = self._accounts.get(key) if key else None
        if account is None:
            raise OpError(404, "not_found", "La cuenta de recaudo no existe.")
        return account

    def _store_account(self, account: CollectionAccount) -> None:
        key = str(account.id)
        self._accounts[key] = account
        self._account_order.setdefault(key, len(self._account_order))
        self._used_wallets.add(account.wallet_address)  # una wallet del pool nunca se reasigna

    def _set_account_status(self, account: CollectionAccount, status: str) -> None:
        self._accounts[str(account.id)] = account.model_copy(update={"status": status})

    def _assign_account(self, body: CreateCollectionAccountRequest) -> CollectionAccount:
        if any(a.external_ref == body.external_ref and a.status == "active" for a in self._accounts.values()):
            raise OpError(409, "already_assigned", "Ese external_ref ya tiene una cuenta de recaudo activa.")
        settings = get_settings()
        wallet = next((w for w in self._pool if w not in self._used_wallets), None)
        if wallet is None or body.asset_code != settings.market_asset_code:
            raise OpError(409, "pool_exhausted", "No hay cuentas de recaudo libres en ese asset.")
        account_id = self._new_id("collection_account")
        account = CollectionAccount(
            id=account_id,
            external_ref=body.external_ref,
            display_name=body.display_name,
            wallet_address=wallet,
            qr_payload=f"{QR_PAYLOAD_PREFIX}{account_id}",
            asset_code=settings.market_asset_code,
            asset_scale=settings.market_asset_scale,
            status="active",
            created_at=clock.now(),
        )
        self._store_account(account)
        return account

    def create_collection_account(
        self, body: CreateCollectionAccountRequest, idempotency_key: str
    ) -> CollectionAccount:
        with self._lock:
            self._enter("create_collection_account", {"body": _copy(body)}, idempotency_key)
            return self._idempotent(
                "create_collection_account", body, idempotency_key, lambda: self._assign_account(body)
            )

    def get_collection_account(self, collection_account_id: IdLike) -> CollectionAccount:
        with self._lock:
            self._enter("get_collection_account", {"collection_account_id": collection_account_id})
            return _copy(self._account(collection_account_id))

    def find_collection_accounts(self, external_ref: str) -> list[CollectionAccount]:
        """Incluye revocadas, la más reciente primero (empates del reloj congelado: la última creada)."""
        with self._lock:
            self._enter("find_collection_accounts", {"external_ref": external_ref})
            found = [a for a in self._accounts.values() if a.external_ref == external_ref]
            found.sort(key=lambda a: (a.created_at, self._account_order[str(a.id)]), reverse=True)
            return [_copy(a) for a in found]

    def revoke_collection_account(self, collection_account_id: IdLike) -> None:
        """Idempotente: deja la cuenta `revoked`; su wallet no vuelve al pool."""
        with self._lock:
            self._enter("revoke_collection_account", {"collection_account_id": collection_account_id})
            self._set_account_status(self._account(collection_account_id), "revoked")

    def adopt_collection_account(
        self,
        op_id: IdLike,
        external_ref: str,
        wallet_address: str,
        qr_payload: str,
        asset_code: str,
        asset_scale: int,
        status: str,
        *,
        display_name: str = "",
        created_at: datetime | None = None,
    ) -> CollectionAccount:
        """Registra una cuenta que ya existe (guardada en Postgres) tras un reinicio (R15). Idempotente."""
        with self._lock:
            key = _key(op_id)
            if key is None:
                raise ValueError(f"op_id no es un UUID: {op_id!r}")
            if key in self._accounts:
                return _copy(self._accounts[key])
            account = CollectionAccount(
                id=key,
                external_ref=external_ref,
                display_name=display_name,
                wallet_address=wallet_address,
                qr_payload=qr_payload,
                asset_code=asset_code,
                asset_scale=asset_scale,
                status=status,
                created_at=created_at or clock.now(),
            )
            self._store_account(account)
            return _copy(account)

    # ── Transferencias (R16) ──────────────────────────────────────────────────

    def _transfer(self, transfer_id: IdLike) -> Transfer:
        key = _key(transfer_id)
        transfer = self._transfers.get(key) if key else None
        if transfer is None:
            raise OpError(404, "not_found", "La transferencia no existe.")
        return transfer

    def _new_transfer(self, body: CreateTransferRequest) -> Transfer:
        account = self._account(body.collection_account_id)
        if account.status != "active":
            raise OpError(409, "collection_account_not_active", "La cuenta de recaudo no está activa.")
        debit = body.debit_amount
        if (debit.asset_code, debit.asset_scale) != (account.asset_code, account.asset_scale):
            raise OpError(422, "asset_mismatch", "El monto no está en el asset de la cuenta de recaudo.")
        self._resolve(body.receiver_wallet_address)  # Go valida el receptor con un GET público
        transfer = Transfer(
            id=self._new_id("transfer"),
            external_ref=body.external_ref,
            collection_account_id=account.id,
            receiver_wallet_address=body.receiver_wallet_address,
            description=body.description,
            debit_amount=_amount(debit),
            status="processing",
            retryable=False,
            created_at=clock.now(),
        )
        self._transfers[str(transfer.id)] = transfer
        return transfer

    def create_transfer(self, body: CreateTransferRequest, idempotency_key: str) -> Transfer:
        with self._lock:
            self._enter("create_transfer", {"body": _copy(body)}, idempotency_key)
            return self._idempotent("create_transfer", body, idempotency_key, lambda: self._new_transfer(body))

    def get_transfer(self, transfer_id: IdLike) -> Transfer:
        with self._lock:
            self._enter("get_transfer", {"transfer_id": transfer_id})
            transfer = self._transfer(transfer_id)
            if self.auto_complete and transfer.status == "processing":  # R18
                transfer = self._complete_transfer(transfer, None)
            return _copy(transfer)

    def _processing_transfer(self, transfer_id: IdLike) -> Transfer:
        transfer = self._transfer(transfer_id)
        if transfer.status != "processing":
            raise ValueError(f"La transferencia {transfer.id} ya está {transfer.status}: los terminales no cambian")
        return transfer

    def _complete_transfer(self, transfer: Transfer, receive: Amount | Money | None) -> Transfer:
        completed = transfer.model_copy(
            update={
                "status": "completed",
                "sent_amount": _amount(transfer.debit_amount),
                "receive_amount": _amount(receive) if receive is not None else _amount(transfer.debit_amount),
                "incoming_payment_url": f"{FAKE_OP_BASE}/incoming-payments/{self._new_id('incoming_payment')}",
                "outgoing_payment_url": f"{FAKE_OP_BASE}/outgoing-payments/{self._new_id('outgoing_payment')}",
                "completed_at": clock.now(),
            }
        )
        self._transfers[str(transfer.id)] = completed
        return completed

    def complete_transfer(self, transfer_id: IdLike, receive: Amount | Money | None = None) -> Transfer:
        """`processing` → `completed`: `sent_amount = debit_amount`, `receive_amount = receive or debit_amount`."""
        with self._lock:
            return _copy(self._complete_transfer(self._processing_transfer(transfer_id), receive))

    def fail_transfer(self, transfer_id: IdLike, code: str, retryable: bool, sent: int = 0) -> Transfer:
        """`processing` → `failed` con `failure_code`, `retryable` y `sent_amount = sent` (unidades mínimas).

        Con `grant_revoked` también revoca la cuenta de recaudo (como Go).
        """
        with self._lock:
            transfer = self._processing_transfer(transfer_id)
            if code not in get_args(TransferFailureCode):
                raise ValueError(f"failure_code inválido para una transferencia: {code!r}")
            debit = transfer.debit_amount
            if type(sent) is not int or not 0 <= sent <= int(debit.value):
                raise ValueError(f"sent debe ser un entero entre 0 y {debit.value}")
            sent_amount = Amount(value=str(sent), asset_code=debit.asset_code, asset_scale=debit.asset_scale)
            failed = transfer.model_copy(
                update={
                    "status": "failed",
                    "failure_code": code,
                    "retryable": retryable,
                    "sent_amount": sent_amount,
                    "receive_amount": _amount(sent_amount) if sent else None,
                }
            )
            self._transfers[str(transfer.id)] = failed
            if code == "grant_revoked":
                self._set_account_status(self._account(transfer.collection_account_id), "revoked")
            return _copy(failed)

    # ── Pagos (R17) ───────────────────────────────────────────────────────────

    def _payment(self, payment_id: IdLike) -> Payment:
        key = _key(payment_id)
        payment = self._payments.get(key) if key else None
        if payment is None:
            raise OpError(404, "not_found", "El pago no existe.")
        return payment

    def _new_payment(self, body: CreatePaymentRequest) -> Payment:
        self._resolve(body.sender_wallet_address)
        receiver = self._resolve(body.receiver_wallet_address)
        receive = body.receive_amount
        if (receive.asset_code, receive.asset_scale) != (receiver.asset_code, receiver.asset_scale):
            raise OpError(422, "asset_mismatch", "El monto no está en el asset de la wallet receptora.")
        payment_id = self._new_id("payment")
        now = clock.now()
        public_api_url = get_settings().public_api_url.rstrip("/")
        payment = Payment(
            id=payment_id,
            external_ref=body.external_ref,
            sender_wallet_address=body.sender_wallet_address,
            receiver_wallet_address=body.receiver_wallet_address,
            receive_amount=_amount(receive),
            debit_amount=_amount(receive),  # el fake no cobra conversión ni comisiones
            status="awaiting_authorization",
            authorization_url=f"{public_api_url}/api/v1/dev/fake-wallet/authorize/{payment_id}",
            authorization_expires_at=now + AUTHORIZATION_TTL,
            created_at=now,
            updated_at=now,
        )
        self._payments[str(payment_id)] = payment
        self._payment_requests[str(payment_id)] = _copy(body)
        return payment

    def create_payment(self, body: CreatePaymentRequest, idempotency_key: str) -> Payment:
        with self._lock:
            self._enter("create_payment", {"body": _copy(body)}, idempotency_key)
            return self._idempotent("create_payment", body, idempotency_key, lambda: self._new_payment(body))

    def get_payment(self, payment_id: IdLike) -> Payment:
        with self._lock:
            self._enter("get_payment", {"payment_id": payment_id})
            payment = self._payment(payment_id)
            if self.auto_complete and payment.status == "processing":  # R18 (nunca desde awaiting_authorization)
                payment = self._move_payment(payment, ("processing",), "completed")
            return _copy(payment)

    def _move_payment(self, payment: Payment, sources: tuple[str, ...], target: str, **changes: Any) -> Payment:
        if payment.status not in sources:
            raise ValueError(f"El pago {payment.id} está {payment.status}: no puede pasar a {target}")
        update: dict[str, Any] = {"status": target, "updated_at": clock.now(), "authorization_url": None, **changes}
        if target == "completed":
            update |= {
                "sent_amount": _amount(payment.debit_amount or payment.receive_amount),
                "incoming_payment_url": f"{FAKE_OP_BASE}/incoming-payments/{self._new_id('incoming_payment')}",
                "outgoing_payment_url": f"{FAKE_OP_BASE}/outgoing-payments/{self._new_id('outgoing_payment')}",
            }
        moved = payment.model_copy(update=update)
        self._payments[str(payment.id)] = moved
        return moved

    def approve_payment(self, payment_id: IdLike) -> Payment:
        """`awaiting_authorization` → `processing` (el prestamista aprobó en su wallet)."""
        with self._lock:
            return _copy(self._move_payment(self._payment(payment_id), ("awaiting_authorization",), "processing"))

    def complete_payment(self, payment_id: IdLike) -> Payment:
        """`processing` → `completed` (`sent_amount = debit_amount`)."""
        with self._lock:
            return _copy(self._move_payment(self._payment(payment_id), ("processing",), "completed"))

    def reject_payment(self, payment_id: IdLike) -> Payment:
        with self._lock:
            return _copy(self._move_payment(self._payment(payment_id), ("awaiting_authorization",), "rejected"))

    def expire_payment(self, payment_id: IdLike) -> Payment:
        with self._lock:
            return _copy(self._move_payment(self._payment(payment_id), ("awaiting_authorization",), "expired"))

    def fail_payment(self, payment_id: IdLike, code: str = "upstream_error") -> Payment:
        """`awaiting_authorization` o `processing` → `failed` con `failure_code`."""
        if code not in get_args(PaymentFailureCode):
            raise ValueError(f"failure_code inválido para un pago: {code!r}")
        with self._lock:
            payment = self._payment(payment_id)
            return _copy(
                self._move_payment(payment, ("awaiting_authorization", "processing"), "failed", failure_code=code)
            )

    # ── Simulador de Puntored (R19) ───────────────────────────────────────────

    def _new_sim_payment(self, body: CreateSimQrPaymentRequest) -> SimQrPayment:
        account = self._account(body.collection_account_id)
        if account.status != "active":
            raise OpError(409, "collection_account_not_active", "La cuenta de recaudo no está activa.")
        amount = body.amount
        if (amount.asset_code, amount.asset_scale) != (account.asset_code, account.asset_scale):
            raise OpError(422, "asset_mismatch", "El monto no está en el asset de la cuenta de recaudo.")
        self._seq += 1
        seq, now = self._seq, clock.now()
        reference = f"BREB-SIM-{seq:06d}"
        incoming_url = f"{FAKE_OP_BASE}/incoming-payments/{self._new_id('incoming_payment')}"
        event = PuntoredPaymentEvent(
            event_id=self._new_id("webhook_event"),
            type="qr_payment.received",
            occurred_at=now,
            data=PuntoredPaymentData(
                collection_account_id=account.id,
                collection_wallet_address=account.wallet_address,
                amount=PositiveAmount(**amount.model_dump()),
                bre_b_reference=reference,
                payer_bank=body.payer_bank,
                incoming_payment_url=incoming_url,
            ),
        )
        sim = SimQrPayment(
            id=self._new_id("sim_qr_payment"),
            seq=seq,
            collection_account_id=account.id,
            amount=_amount(amount),
            payer_bank=body.payer_bank,
            bre_b_reference=reference,
            status="completed",
            incoming_payment_url=incoming_url,
            webhook_event=event,
            created_at=now,
        )
        self._sim_payments.append(sim)
        return sim

    def create_sim_qr_payment(self, body: CreateSimQrPaymentRequest, idempotency_key: str) -> SimQrPayment:
        """Completa al instante y no envía el webhook por HTTP: Trust lo procesa con `webhook_event`."""
        with self._lock:
            self._enter("create_sim_qr_payment", {"body": _copy(body)}, idempotency_key)
            return self._idempotent(
                "create_sim_qr_payment", body, idempotency_key, lambda: self._new_sim_payment(body)
            )

    def list_sim_qr_payments(self, after: int = 0, limit: int = 100) -> SimQrPaymentList:
        with self._lock:
            self._enter("list_sim_qr_payments", {"after": after, "limit": limit})
            if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 200:
                raise OpError(400, "validation_error", "after debe ser ≥ 0 y limit estar entre 1 y 200.")
            items = [_copy(s) for s in self._sim_payments if s.seq is not None and s.seq > after][:limit]
            return SimQrPaymentList(items=items, next_after=items[-1].seq if items else after)

    # ── Plataforma (R20) ──────────────────────────────────────────────────────

    def health(self) -> Health:
        with self._lock:
            self._enter("health", {})
            return Health(status="ok", client_wallet_address=CLIENT_WALLET_ADDRESS)


__all__ = ["FakeCall", "FakeOpClient", "OpError", "OpUnavailable", "normalize_wallet_url"]
