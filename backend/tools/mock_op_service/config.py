"""Configuración del mock por variables de entorno (los mismos nombres que op-service en Go, más `MOCK_*`).

Lee `os.environ` directamente: nunca el `.env` de Trust ni `app.core.config` (el mock arranca sin DATABASE_URL).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

DEFAULT_API_KEY = "dev-internal-api-key"
DEFAULT_POOL_SIZE = 20
POOL_WALLET_PREFIX = "https://ilp.interledger-test.dev/trust-recaudo-"
DEFAULT_WEBHOOK_RETRIES = (1.0, 5.0, 30.0, 120.0)  # como Go: 1 s, 5 s, 30 s y 2 min


def default_pool() -> list[str]:
    return [f"{POOL_WALLET_PREFIX}{n:02d}" for n in range(1, DEFAULT_POOL_SIZE + 1)]


@dataclass
class MockConfig:
    api_key: str = DEFAULT_API_KEY
    public_base_url: str = "http://localhost:8090"
    client_wallet_address: str = "https://ilp.interledger-test.dev/trust"
    asset_code: str = "COP"
    asset_scale: int = 2
    pool_wallet_addresses: list[str] = field(default_factory=default_pool)
    webhook_url: str = ""
    webhook_secret: str = ""
    webhook_retry_seconds: tuple[float, ...] = DEFAULT_WEBHOOK_RETRIES
    webhook_timeout_seconds: float = 5.0
    allowed_return_url_prefixes: list[str] = field(default_factory=list)  # vacío = cualquier http(s)
    sim_enabled: bool = True
    sim_max_amount: int = 1_000_000_000  # unidades mínimas: 10.000.000,00 COP
    interaction_timeout_seconds: int = 600
    expiry_grace_seconds: int = 30
    settle_seconds: float = 0.0  # 0 = transferencias y pagos aprobados quedan `completed` en el siguiente GET
    sim_seq_start: int = 0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "MockConfig":
        env = os.environ if env is None else env
        defaults = cls()

        def text(name: str, default: str) -> str:
            value = env.get(name)
            return value.strip() if value is not None and value.strip() else default

        def number(name: str, default: float) -> float:
            value = env.get(name)
            return float(value) if value is not None and value.strip() else default

        def integer(name: str, default: int) -> int:
            value = env.get(name)
            return int(value) if value is not None and value.strip() else default

        def items(name: str) -> list[str]:
            return [item.strip() for item in env.get(name, "").split(",") if item.strip()]

        retries = items("MOCK_WEBHOOK_RETRY_SECONDS")
        return cls(
            api_key=text("INTERNAL_API_KEY", defaults.api_key),
            public_base_url=text("PUBLIC_BASE_URL", defaults.public_base_url).rstrip("/"),
            client_wallet_address=text("OP_CLIENT_WALLET_ADDRESS", defaults.client_wallet_address),
            asset_code=text("MOCK_ASSET_CODE", defaults.asset_code),
            asset_scale=integer("MOCK_ASSET_SCALE", defaults.asset_scale),
            pool_wallet_addresses=items("POOL_WALLET_ADDRESSES") or defaults.pool_wallet_addresses,
            webhook_url=text("TRUST_WEBHOOK_URL", ""),
            webhook_secret=text("TRUST_WEBHOOK_SECRET", ""),
            webhook_retry_seconds=tuple(float(r) for r in retries) if retries else defaults.webhook_retry_seconds,
            webhook_timeout_seconds=number("MOCK_WEBHOOK_TIMEOUT_SECONDS", defaults.webhook_timeout_seconds),
            allowed_return_url_prefixes=items("ALLOWED_RETURN_URL_PREFIXES"),
            sim_enabled=text("SIM_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
            sim_max_amount=integer("SIM_MAX_AMOUNT", defaults.sim_max_amount),
            interaction_timeout_seconds=integer("INTERACTION_TIMEOUT_SECONDS", defaults.interaction_timeout_seconds),
            settle_seconds=number("MOCK_SETTLE_SECONDS", defaults.settle_seconds),
            sim_seq_start=integer("MOCK_SIM_SEQ_START", defaults.sim_seq_start),
        )
