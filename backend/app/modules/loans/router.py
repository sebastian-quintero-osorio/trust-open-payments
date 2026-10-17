from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.db import DbSession
from app.core.deps import CurrentUser, Pagination, require_role
from app.integrations.op.client import OpClientDep
from app.modules.loans import service
from app.modules.loans.schemas import (
    DisburseIn,
    DisburseOut,
    LoanDraftIn,
    LoanOut,
    LoanPctHistory,
    LoanPreviewOut,
    LoanSummaryPage,
)
from app.modules.users.models import User

router = APIRouter(tags=["loans"])

Merchant = Annotated[User, Depends(require_role("merchant"))]
Lender = Annotated[User, Depends(require_role("lender"))]
MerchantOrLender = Annotated[User, Depends(require_role("merchant", "lender"))]


@router.post(
    "/loans/preview",
    response_model=LoanPreviewOut,
    summary='Condiciones completas sin crear nada ("Ver condiciones completas")',
)
def preview_loan(body: LoanDraftIn, user: MerchantOrLender, db: DbSession) -> LoanPreviewOut:
    return service.preview(db, user, body)


@router.post(
    "/loans",
    status_code=201,
    response_model=LoanOut,
    summary='Crear un crédito ("Aceptar oferta" del comercio o "Fondear solicitud" del prestamista)',
)
def create_loan(body: LoanDraftIn, user: MerchantOrLender, db: DbSession) -> LoanOut:
    return service.create(db, user, body)


@router.get("/loans", response_model=LoanSummaryPage, summary="Mis créditos (admin ve todos)")
def list_loans(
    user: CurrentUser,
    db: DbSession,
    page: Pagination,
    status: Annotated[
        str | None, Query(description="Uno o varios estados separados por coma, por ejemplo `active,paid`")
    ] = None,
) -> LoanSummaryPage:
    return service.list_loans(db, user, status, page)


@router.get("/loans/{loan_id}", response_model=LoanOut, summary="Detalle del crédito (partes o admin)")
def get_loan(loan_id: str, user: CurrentUser, db: DbSession) -> LoanOut:
    return service.get_loan(db, user, loan_id)


@router.post(
    "/loans/{loan_id}/accept",
    response_model=LoanOut,
    summary="El comercio acepta un crédito que le ofrecieron (`pending_acceptance` → `pending_disbursement`)",
)
def accept_loan(loan_id: str, user: Merchant, db: DbSession) -> LoanOut:
    return service.accept(db, user, loan_id)


@router.post(
    "/loans/{loan_id}/reject",
    response_model=LoanOut,
    summary="Rechazar (comercio en `pending_acceptance` o prestamista en `pending_disbursement`)",
)
def reject_loan(loan_id: str, user: MerchantOrLender, db: DbSession) -> LoanOut:
    return service.reject(db, user, loan_id)


@router.post(
    "/loans/{loan_id}/cancel",
    response_model=LoanOut,
    summary="Cancelar antes del desembolso (cualquiera de las partes, en `pending_*`)",
)
def cancel_loan(loan_id: str, user: MerchantOrLender, db: DbSession) -> LoanOut:
    return service.cancel(db, user, loan_id)


@router.post(
    "/loans/{loan_id}/disburse",
    response_model=DisburseOut,
    summary="El prestamista inicia el desembolso (devuelve la URL de autorización en su wallet)",
)
def disburse_loan(loan_id: str, body: DisburseIn, user: Lender, db: DbSession, op: OpClientDep) -> DisburseOut:
    return service.disburse(db, op, user, loan_id, body.client)


@router.get(
    "/loans/{loan_id}/pct-history",
    response_model=LoanPctHistory,
    summary="Historial de revisiones del % por periodo",
)
def get_loan_pct_history(loan_id: str, user: CurrentUser, db: DbSession) -> LoanPctHistory:
    return service.pct_history(db, user, loan_id)
