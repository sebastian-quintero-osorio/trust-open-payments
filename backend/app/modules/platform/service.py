"""Salud del backend (03 R1). Por ahora solo la base es real; op-service y las alertas de asset llegan con 02/03."""

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import __version__
from app.core import clock
from app.core.config import get_settings
from app.modules.platform.schemas import Health

logger = logging.getLogger(__name__)


def db_ok(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.exception("La base de datos no responde")
        db.rollback()
        return False


def health(db: Session) -> Health:
    database_ok = db_ok(db)
    # TODO(spec 02/03): `OpClient.health()` y `market_asset_warnings(db, op)` reemplazan este placeholder.
    op_status = "ok"
    warnings: list[str] = []
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
