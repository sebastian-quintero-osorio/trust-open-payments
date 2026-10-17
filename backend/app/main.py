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
from app.modules.platform.router import router as platform_router
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
    # TODO(spec 03 R1): verificar el asset de mercado.
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
    app.include_router(platform_router, prefix=API_PREFIX)
    return app


app = create_app()
