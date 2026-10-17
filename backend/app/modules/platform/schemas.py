from typing import Literal

from pydantic import BaseModel

from app.core.clock import UtcDateTime


class Health(BaseModel):
    status: Literal["ok", "degraded", "down"]
    db: Literal["ok", "down"]
    op: Literal["ok", "degraded", "down"]
    op_client: Literal["fake", "http"]
    version: str
    time: UtcDateTime
    warnings: list[str]
