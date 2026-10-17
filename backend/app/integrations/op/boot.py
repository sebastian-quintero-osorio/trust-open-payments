"""Arranque en modo fake (02 R15): las cuentas de recaudo guardadas en Postgres vuelven a existir en el fake."""

import logging

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.orm import Session

from app.integrations.op.fake import FakeOpClient
from app.modules.recaudo.models import CollectionAccount
from app.modules.sales.models import Sale
from app.modules.users.models import User
from app.modules.worker.models import SIM_QR_CURSOR, OpCursor

logger = logging.getLogger(__name__)

SIM_CURSOR = SIM_QR_CURSOR


def adopt_collection_accounts(db: Session, fake: FakeOpClient) -> int:
    """Carga todas las filas de `collection_accounts` en el fake con `adopt_collection_account`.

    Sin esto, un reinicio con `OP_CLIENT=fake` deja a `/dev/sales/simulate` con cuentas que el fake no conoce (404).
    También continúa el `seq` del simulador después del último guardado (`BREB-SIM-n` de `sales` y el cursor de
    reconciliación), para que un pago nuevo no repita un `bre_b_reference` ya registrado en esa cuenta.
    Solo lee la base; no deja la transacción abierta.
    """
    rows = db.execute(
        select(
            CollectionAccount.op_collection_account_id,
            CollectionAccount.merchant_id,
            CollectionAccount.wallet_address,
            CollectionAccount.qr_payload,
            CollectionAccount.asset_code,
            CollectionAccount.asset_scale,
            CollectionAccount.status,
            CollectionAccount.created_at,
            User.business_name,
        )
        .join(User, User.id == CollectionAccount.merchant_id)
        .order_by(CollectionAccount.created_at, CollectionAccount.id)
    ).all()
    last_reference = db.scalar(
        select(func.max(cast(func.substring(Sale.bre_b_reference, r"^BREB-SIM-([0-9]+)$"), BigInteger)))
    )
    cursor = db.scalar(select(OpCursor.value).where(OpCursor.name == SIM_CURSOR))
    db.rollback()  # fin de la lectura (filas planas, sin objetos ORM): nada queda abierto

    for row in rows:
        fake.adopt_collection_account(
            row.op_collection_account_id,
            f"merchant:{row.merchant_id}",  # el external_ref que usa 06 R4
            row.wallet_address,
            row.qr_payload,
            row.asset_code,
            row.asset_scale,
            row.status,
            display_name=(row.business_name or "")[:100],
            created_at=row.created_at,
        )
    fake.resume_seq_after(max(last_reference or 0, cursor or 0))
    if rows:
        logger.info("FakeOpClient: %d cuentas de recaudo cargadas desde la base", len(rows))
    return len(rows)
