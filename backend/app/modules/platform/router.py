from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Response

from app.core.db import DbSession
from app.integrations.op.client import OpClientDep
from app.integrations.op.fake import FakeOpClient
from app.modules.loans.schemas import LoanPctChangeOut
from app.modules.platform import dev, seed, service
from app.modules.platform.schemas import (
    Config,
    Health,
    SeedRequest,
    SeedResult,
    SimulateSaleRequest,
    SimulateSaleResponse,
)

router = APIRouter(tags=["platform"])


@router.get(
    "/health",
    response_model=Health,
    summary="Estado del backend, la base de datos y op-service",
    responses={503: {"model": Health, "description": "Base de datos caída"}},
)
def get_health(db: DbSession, op: OpClientDep, response: Response) -> Health:
    result = service.health(db, op)
    if result.status == "down":
        response.status_code = 503
    return result


@router.get("/config", response_model=Config, summary="Parámetros públicos para formularios")
def get_config() -> Config:
    return service.config()


# ── /dev: solo con DEV_ENDPOINTS_ENABLED=true; si no, 404 (03 R3) ─────────────

dev_router = APIRouter(prefix="/dev", tags=["dev"], dependencies=[Depends(dev.require_dev)])


@dev_router.post("/seed", response_model=SeedResult, summary="Sembrar datos de demo (idempotente)")
def dev_seed(db: DbSession, op: OpClientDep, body: Annotated[SeedRequest | None, Body()] = None) -> SeedResult:
    return seed.run_seed(db, op, with_active_loan_for_marta=bool(body and body.with_active_loan_for_marta))


@dev_router.post(
    "/sales/simulate",
    status_code=201,
    response_model=SimulateSaleResponse,
    summary="Simular un pago Bre-B al QR de un negocio",
)
def dev_simulate_sale(body: SimulateSaleRequest, db: DbSession, op: OpClientDep) -> SimulateSaleResponse:
    return dev.simulate_sale(db, op, body)


@dev_router.post(
    "/loans/{loan_id}/close-period",
    response_model=LoanPctChangeOut,
    summary="Forzar la revisión del % del periodo actual",
)
def dev_close_period(loan_id: str, db: DbSession) -> LoanPctChangeOut:
    return dev.close_period(db, loan_id)


@dev_router.get(
    "/fake-wallet/authorize/{payment_id}",
    response_model=None,
    summary="Wallet falsa (solo con el cliente falso de Go) para aprobar o rechazar un desembolso",
    responses={
        200: {"description": 'Sin `decision`, página HTML con "Aprobar" y "Rechazar"', "content": {"text/html": {}}},
        302: {"description": "Redirige al `return_url` con `op_resource`, `op_id` y `op_status`"},
    },
)
def dev_fake_wallet_authorize(
    payment_id: str,
    fake: Annotated[FakeOpClient, Depends(dev.require_fake_op)],
    decision: Annotated[Literal["approve", "reject"] | None, Query()] = None,
) -> Response:
    return dev.fake_wallet_authorize(fake, payment_id, decision)
