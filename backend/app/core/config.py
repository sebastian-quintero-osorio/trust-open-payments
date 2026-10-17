"""Configuración por variables de entorno (00-core, tabla "Configuración")."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CommaList = Annotated[list[str], NoDecode]
CommaIntList = Annotated[list[int], NoDecode]
PUBLISHED_DEMO_PASSWORD = "trust2026"  # default de DEMO_PASSWORD, publicado en el README y en .env.example
DEMO_ADMIN_PASSWORD_MIN = 12


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=1)
    # HS256 pide una clave de al menos 32 bytes; sin ella la app no arranca.
    jwt_secret: str = Field(min_length=32)
    jwt_expires_hours: int = 168
    cors_origins: CommaList = []
    # IPs o redes (CIDR) de los proxies cuyo `X-Forwarded-For` se cree (Caddy); vacío: se usa la IP de la conexión.
    trusted_proxies: CommaList = []
    timezone: str = "America/Bogota"

    market_asset_code: str = "COP"
    market_asset_scale: int = 2

    op_client: Literal["fake", "http"] = "fake"
    op_service_base_url: str = ""
    op_service_api_key: str = ""
    op_http_timeout_seconds: float = 10
    op_fake_auto_complete: bool = True
    public_api_url: str = "http://localhost:8000"

    puntored_webhook_secrets: CommaList = []
    notify_sale_split: bool = True

    trust_revenue_wallet_address: str = ""
    trust_share_bps: int = 2000
    trust_leg_min: int = 10_000
    trust_cut_policy: Literal["prorated", "literal"] = "prorated"

    min_loan_amount: int = 50_000_000
    amount_step: int = 5_000_000
    cupo_cap: int = 1_000_000_000
    pct_options_bps: CommaIntList = [1000, 1500, 2000]
    default_term_months: int = 3
    reprice_step_bps: int = 250
    reprice_max_delta_bps: int = 500

    min_months: int = 3
    scoring_policy: str = "v1"
    min_sales_per_month: int = 10
    score_volume_ref: int = 1_000_000_000

    pending_loan_ttl_hours: int = 48
    app_web_url: str = "https://app.trustservices.com.co"

    worker_enabled: bool = True
    worker_interval_seconds: float = 2

    dev_endpoints_enabled: bool = False
    demo_password: str = PUBLISHED_DEMO_PASSWORD
    # admin@demo.trust no usa DEMO_PASSWORD (publicada en el README y en la respuesta de /dev/seed): vacía, el seed lo
    # deja sin contraseña utilizable (03 R5).
    demo_admin_password: str = ""
    demo_merchant_wallet_url: str = "https://ilp.interledger-test.dev/demo-marta"
    demo_lender_wallet_url: str = "https://ilp.interledger-test.dev/demo-camilo"
    landing_url: str = "https://trustservices.com.co"

    @field_validator("cors_origins", "trusted_proxies", "puntored_webhook_secrets", "pct_options_bps", mode="before")
    @classmethod
    def _split_commas(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


    @model_validator(mode="after")
    def _admin_password_is_secret(self) -> Self:
        if self.demo_admin_password:
            if len(self.demo_admin_password) < DEMO_ADMIN_PASSWORD_MIN:
                raise ValueError(f"DEMO_ADMIN_PASSWORD debe tener al menos {DEMO_ADMIN_PASSWORD_MIN} caracteres")
            if self.demo_admin_password in (self.demo_password, PUBLISHED_DEMO_PASSWORD):
                raise ValueError("DEMO_ADMIN_PASSWORD no puede ser DEMO_PASSWORD ni la contraseña de demo publicada")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
