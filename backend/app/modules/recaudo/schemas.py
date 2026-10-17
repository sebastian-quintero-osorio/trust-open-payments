"""Schemas de la cuenta de recaudo (06-recaudo; `CollectionAccount` del YAML)."""

import uuid
from typing import Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime
from app.core.money import Money, MoneySchema
from app.modules.platform.schemas import Asset
from app.modules.recaudo.models import CollectionAccount


class CollectionAccountOut(BaseModel):
    id: uuid.UUID
    wallet_address: str
    qr_payload: str
    asset: Asset
    status: Literal["active", "revoked"]
    balance: MoneySchema
    created_at: UtcDateTime
    revoked_at: UtcDateTime | None

    @classmethod
    def of(cls, account: CollectionAccount) -> "CollectionAccountOut":
        """`id` de Trust (nunca el de Go) y saldo del libro de Trust: `ledger_in − ledger_out` (R1, R2)."""
        return cls(
            id=account.id,
            wallet_address=account.wallet_address,
            qr_payload=account.qr_payload,
            asset=Asset(asset_code=account.asset_code, asset_scale=account.asset_scale),
            status=account.status,
            balance=MoneySchema.of(
                Money(account.ledger_in - account.ledger_out, account.asset_code, account.asset_scale)
            ),
            created_at=account.created_at,
            revoked_at=account.revoked_at,
        )
