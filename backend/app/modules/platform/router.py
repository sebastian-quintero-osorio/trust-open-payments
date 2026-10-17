from fastapi import APIRouter, Response

from app.core.db import DbSession
from app.modules.platform import service
from app.modules.platform.schemas import Health

router = APIRouter(tags=["platform"])


@router.get(
    "/health",
    response_model=Health,
    summary="Estado del backend, la base de datos y op-service",
    responses={503: {"model": Health, "description": "Base de datos caída"}},
)
def get_health(db: DbSession, response: Response) -> Health:
    result = service.health(db)
    if result.status == "down":
        response.status_code = 503
    return result
