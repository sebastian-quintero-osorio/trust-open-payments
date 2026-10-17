"""Salud (03 R1), verificación del asset de mercado y configuración pública (03 R2)."""

import logging
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import __version__
from app.core import clock
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.money import MoneySchema, bps_of, market_money
from app.integrations.op.client import OpClient, OpError, OpUnavailable
from app.modules.platform.schemas import Asset, Config, ExampleSplit, Health
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus

logger = logging.getLogger(__name__)

EXAMPLE_SALE = 4_800_000  # $48.000 en centavos del asset de mercado (03 R2)

OpState = Literal["ok", "degraded", "down"]


def db_ok(db: Session) -> bool:
    """`SELECT 1` y cierra la transacción (las llamadas a Go que siguen no quedan dentro de una)."""
    try:
        db.execute(text("SELECT 1"))
        db.rollback()
        return True
    except SQLAlchemyError:
        logger.exception("La base de datos no responde")
        db.rollback()
        return False


def op_state(op: OpClient) -> OpState:
    """`OpClient.health()` (timeout de 2 s en el cliente HTTP, 02 R7): error → `down`; `status` distinto de ok →
    `degraded`."""
    try:
        result = op.health()
    except (OpError, OpUnavailable) as exc:
        logger.warning("op-service no responde al health: %r", exc)
        return "down"
    except Exception:
        logger.exception("Error inesperado al consultar el health de op-service")
        return "down"
    return "ok" if result.status == "ok" else "degraded"


def market_asset_warnings(
    db: Session, op: OpClient, *, resolve_wallet: bool = True, check_accounts: bool = True
) -> list[str]:
    """Compara con `MARKET_ASSET_CODE`/`MARKET_ASSET_SCALE` el asset de `TRUST_REVENUE_WALLET_ADDRESS` y el de las
    cuentas de recaudo `active` (una alerta por asset distinto). Nunca lanza.

    `resolve_wallet=False` cuando `OpClient.health()` falló; `check_accounts=False` con la base caída.
    """
    settings = get_settings()
    market = (settings.market_asset_code, settings.market_asset_scale)
    expected = f"{market[0]}/{market[1]}"
    warnings: list[str] = []

    if resolve_wallet:
        address = settings.trust_revenue_wallet_address.strip()
        if not address:
            logger.warning("TRUST_REVENUE_WALLET_ADDRESS está vacía: no se verifica el asset de la wallet de Trust")
        else:
            try:
                wallet = op.resolve_wallet_address(address)
            except (OpError, OpUnavailable) as exc:
                logger.warning("No se pudo resolver TRUST_REVENUE_WALLET_ADDRESS (%s): %r", address, exc)
            else:
                if (wallet.asset_code, wallet.asset_scale) != market:
                    warnings.append(
                        f"market_asset_mismatch: expected {expected}, wallet {wallet.asset_code}/{wallet.asset_scale}"
                    )

    if check_accounts:
        try:
            rows = db.execute(
                select(CollectionAccount.asset_code, CollectionAccount.asset_scale)
                .where(CollectionAccount.status == CollectionAccountStatus.ACTIVE)
                .distinct()
                .order_by(CollectionAccount.asset_code, CollectionAccount.asset_scale)
            ).all()
        except SQLAlchemyError:
            logger.exception("No se pudieron leer las cuentas de recaudo para verificar el asset de mercado")
            rows = []
        finally:
            db.rollback()
        for asset_code, asset_scale in rows:
            if (asset_code, asset_scale) != market:
                warnings.append(
                    f"market_asset_mismatch: expected {expected}, collection_account {asset_code}/{asset_scale}"
                )
    return warnings


def log_market_asset_warnings(op: OpClient) -> list[str]:
    """Arranque (lifespan): corre la verificación una vez y loguea ERROR por cada alerta. Nunca rompe el arranque."""
    try:
        with SessionLocal() as db:
            warnings = market_asset_warnings(db, op)
    except Exception:
        logger.warning("No se pudo verificar el asset de mercado al arrancar", exc_info=True)
        return []
    for warning in warnings:
        logger.error(warning)
    return warnings


def health(db: Session, op: OpClient) -> Health:
    database_ok = db_ok(db)
    op_status = op_state(op)
    warnings = market_asset_warnings(db, op, resolve_wallet=op_status != "down", check_accounts=database_ok)
    if not database_ok:
        status = "down"
    elif op_status != "ok" or warnings:
        status = "degraded"
    else:
        status = "ok"
    return Health(
        status=status,
        db="ok" if database_ok else "down",
        op=op_status,
        op_client=get_settings().op_client,
        version=__version__,
        time=clock.now(),
        warnings=warnings,
    )


def config() -> Config:
    settings = get_settings()
    splits = []
    for pct in settings.pct_options_bps:
        abono = bps_of(EXAMPLE_SALE, pct)
        splits.append(
            ExampleSplit(
                pct_bps=pct,
                abono=MoneySchema.of(market_money(abono)),
                merchant=MoneySchema.of(market_money(EXAMPLE_SALE - abono)),
            )
        )
    return Config(
        market_asset=Asset(asset_code=settings.market_asset_code, asset_scale=settings.market_asset_scale),
        pct_options_bps=list(settings.pct_options_bps),
        min_loan_amount=MoneySchema.of(market_money(settings.min_loan_amount)),
        amount_step=MoneySchema.of(market_money(settings.amount_step)),
        cupo_cap=MoneySchema.of(market_money(settings.cupo_cap)),
        default_term_months=settings.default_term_months,
        timezone=settings.timezone,
        dev_endpoints_enabled=settings.dev_endpoints_enabled,
        example_sale=MoneySchema.of(market_money(EXAMPLE_SALE)),
        example_splits=splits,
    )
