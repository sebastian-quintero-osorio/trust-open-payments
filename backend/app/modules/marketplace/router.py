from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.core.db import DbSession
from app.core.deps import CurrentUser, Pagination, require_role
from app.modules.marketplace import service
from app.modules.marketplace.schemas import (
    CreateLoanOfferIn,
    CreateLoanRequestIn,
    LoanOfferOut,
    LoanOfferPage,
    LoanRequestOut,
    LoanRequestPage,
    MatchList,
)
from app.modules.users.models import User

router = APIRouter(tags=["marketplace"])

Merchant = Annotated[User, Depends(require_role("merchant"))]
Lender = Annotated[User, Depends(require_role("lender"))]
MerchantOrLender = Annotated[User, Depends(require_role("merchant", "lender"))]
ScopeQuery = Annotated[
    Literal["mine", "market"],
    Query(description="`mine` (las propias) o `market` (las `open` de la contraparte)"),
]


# ── Solicitudes ───────────────────────────────────────────────────────────────


@router.post(
    "/loan-requests",
    status_code=201,
    response_model=LoanRequestOut,
    summary="El comercio publica una solicitud (rango + % de sus ventas)",
)
def create_loan_request(body: CreateLoanRequestIn, user: Merchant, db: DbSession) -> LoanRequestOut:
    return service.create_request(db, user, body)


@router.get(
    "/loan-requests",
    response_model=LoanRequestPage,
    summary="Mis solicitudes (comercio) o solicitudes abiertas del mercado (prestamista)",
)
def list_loan_requests(
    user: MerchantOrLender,
    db: DbSession,
    page: Pagination,
    scope: ScopeQuery,
    status: Literal["open", "matched", "cancelled"] | None = None,
    in_my_range: Annotated[bool, Query(description='Solo market: se cruza con "Mi rango" del prestamista')] = False,
) -> LoanRequestPage:
    return service.list_requests(db, user, scope, status, in_my_range, page)


@router.get(
    "/loan-requests/{request_id}",
    response_model=LoanRequestOut,
    summary="Detalle de una solicitud (dueño, o prestamista si está `open`)",
)
def get_loan_request(request_id: str, user: CurrentUser, db: DbSession) -> LoanRequestOut:
    return service.get_request(db, user, request_id)


@router.post(
    "/loan-requests/{request_id}/cancel",
    response_model=LoanRequestOut,
    summary="Cancelar mi solicitud (`open` → `cancelled`)",
)
def cancel_loan_request(request_id: str, user: Merchant, db: DbSession) -> LoanRequestOut:
    return service.cancel_request(db, user, request_id)


# ── Ofertas ───────────────────────────────────────────────────────────────────


@router.post(
    "/loan-offers",
    status_code=201,
    response_model=LoanOfferOut,
    summary="El prestamista publica una oferta (rango + % + cargo + plazo)",
)
def create_loan_offer(body: CreateLoanOfferIn, user: Lender, db: DbSession) -> LoanOfferOut:
    return service.create_offer(db, user, body)


@router.get(
    "/loan-offers",
    response_model=LoanOfferPage,
    summary='Mis ofertas (prestamista) u ofertas abiertas del mercado (comercio), con "N ofertas encajan"',
)
def list_loan_offers(
    user: MerchantOrLender,
    db: DbSession,
    page: Pagination,
    scope: ScopeQuery,
    status: Literal["open", "taken", "cancelled"] | None = None,
    fit_min: Annotated[int | None, Query(ge=1, description="Centavos del asset de mercado (slider del mínimo)")] = None,
    fit_max: Annotated[int | None, Query(ge=1, description="Centavos del asset de mercado (slider del máximo)")] = None,
    pct_bps: Annotated[int | None, Query(description="Va con fit_min y fit_max")] = None,
    fits_only: bool = False,
) -> LoanOfferPage:
    return service.list_offers(db, user, scope, status, fit_min, fit_max, pct_bps, fits_only, page)


@router.get(
    "/loan-offers/{offer_id}",
    response_model=LoanOfferOut,
    summary="Detalle de una oferta (dueño, o comercio si está `open`)",
)
def get_loan_offer(offer_id: str, user: CurrentUser, db: DbSession) -> LoanOfferOut:
    return service.get_offer(db, user, offer_id)


@router.post(
    "/loan-offers/{offer_id}/cancel",
    response_model=LoanOfferOut,
    summary="Cancelar mi oferta (`open` → `cancelled`)",
)
def cancel_loan_offer(offer_id: str, user: Lender, db: DbSession) -> LoanOfferOut:
    return service.cancel_offer(db, user, offer_id)


# ── Matches ───────────────────────────────────────────────────────────────────


@router.get(
    "/matches",
    response_model=MatchList,
    summary="Matches calculados al vuelo (solicitud × oferta que encajan)",
)
def list_matches(user: MerchantOrLender, db: DbSession) -> MatchList:
    return service.list_matches(db, user)
