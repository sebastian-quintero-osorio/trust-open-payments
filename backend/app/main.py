"""Punto de entrada: `uvicorn app.main:app --workers 1`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.core.errors import register_error_handlers
from app.integrations.op.boot import adopt_collection_accounts
from app.integrations.op.client import OpClient, get_op_client
from app.integrations.op.fake import FakeOpClient
from app.models import Base
from app.modules.admin.router import router as admin_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.loans.router import router as loans_router
from app.modules.marketplace.router import router as marketplace_router
from app.modules.notifications.router import router as notifications_router
from app.modules.platform.router import dev_router
from app.modules.platform.router import router as platform_router
from app.modules.platform.service import log_market_asset_warnings
from app.modules.recaudo.router import router as recaudo_router
from app.modules.repayments.router import router as repayments_router
from app.modules.sales.router import router as sales_router
from app.modules.sales.router import webhooks_router
from app.modules.scoring.router import router as scoring_router
from app.modules.users.router import router as users_router
from app.modules.waitlist.router import router as waitlist_router
from app.worker import start_worker

API_PREFIX = "/api/v1"


def app_op_client(app: FastAPI) -> OpClient:
    """El `OpClient` que reciben las rutas de esta app: respeta `dependency_overrides` (tests) y si no, el singleton."""
    return app.dependency_overrides.get(get_op_client, get_op_client)()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    op = app_op_client(app)
    if isinstance(op, FakeOpClient):  # OP_CLIENT=fake: un reinicio no pierde las cuentas de recaudo (02 R15)
        with SessionLocal() as db:
            adopt_collection_accounts(db, op)
    log_market_asset_warnings(op)  # 03 R1: un ERROR por cada desajuste del asset de mercado; nunca rompe el arranque
    stop_worker = start_worker() if get_settings().worker_enabled else None
    try:
        yield
    finally:
        if stop_worker is not None:
            stop_worker()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Trust API", version=__version__, lifespan=lifespan)
    register_error_handlers(app)  # antes de CORS, para que CORS envuelva también los 500
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(users_router, prefix=API_PREFIX)
    app.include_router(recaudo_router, prefix=API_PREFIX)
    app.include_router(scoring_router, prefix=API_PREFIX)
    app.include_router(marketplace_router, prefix=API_PREFIX)
    app.include_router(loans_router, prefix=API_PREFIX)
    app.include_router(sales_router, prefix=API_PREFIX)
    app.include_router(webhooks_router, prefix=API_PREFIX)
    app.include_router(repayments_router, prefix=API_PREFIX)
    app.include_router(dashboard_router, prefix=API_PREFIX)
    app.include_router(notifications_router, prefix=API_PREFIX)
    app.include_router(waitlist_router, prefix=API_PREFIX)
    app.include_router(admin_router, prefix=API_PREFIX)
    app.include_router(platform_router, prefix=API_PREFIX)
    app.include_router(dev_router, prefix=API_PREFIX)
    return app


app = create_app()
