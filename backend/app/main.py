"""Punto de entrada: `uvicorn app.main:app --workers 1`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import get_settings
from app.core.db import engine
from app.core.errors import register_error_handlers
from app.models import Base
from app.modules.platform.router import router as platform_router
from app.worker import start_worker

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    # TODO(spec 03 R1): verificar el asset de mercado · TODO(spec 02 R15): cargar collection_accounts en el fake.
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
