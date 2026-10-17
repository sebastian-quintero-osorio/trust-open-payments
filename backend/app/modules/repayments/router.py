import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.clock import LocalDate
from app.core.db import DbSession
from app.core.deps import Pagination, require_role
from app.modules.repayments import service
from app.modules.repayments.schemas import RepaymentPage, RepaymentTrace
from app.modules.users.models import User

router = APIRouter(tags=["repayments"])

AnyRole = Annotated[User, Depends(require_role("lender", "merchant", "admin"))]


@router.get(
    "/repayments",
    response_model=RepaymentPage,
    summary="Abonos (prestamista, comercio o admin), con bruto, parte Trust, neto y entrega",
)
def list_repayments(
    user: AnyRole,
    db: DbSession,
    page: Pagination,
    loan_id: Annotated[uuid.UUID | None, Query(description="Solo los abonos de este crédito")] = None,
    from_: Annotated[
        LocalDate | None, Query(alias="from", description="Fecha local (America/Bogota), inclusiva")
    ] = None,
    to: Annotated[LocalDate | None, Query(description="Fecha local (America/Bogota), inclusiva")] = None,
) -> RepaymentPage:
    return service.list_repayments(db, user, loan_id, from_, to, page)


@router.get(
    "/repayments/{repayment_id}",
    response_model=RepaymentTrace,
    summary="Traza de un abono: venta → abono → pagos Open Payments → crédito",
)
def get_repayment_trace(repayment_id: str, user: AnyRole, db: DbSession) -> RepaymentTrace:
    return service.repayment_trace(db, user, repayment_id)
