"""Dashboards (13-dashboard): una llamada por pantalla de inicio, del comercio (R1–R7) y del prestamista (R7–R12).

Solo lectura y sin llamar a op-service. "Hoy" es siempre `today_local()` / `local_day_bounds` (R13). Sin N+1: cada
cifra sale de una consulta (los totales, con `GROUP BY`). `get_credit_profile` puede hacer commit (guarda el
assessment del día), por eso va primero.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import BigInteger, Integer, case, cast, exists, extract, func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.money import Money, MoneySchema, market_money
from app.modules.dashboard.schemas import (
    AbonosHoy,
    ActiveLoanOut,
    AssetTotals,
    CollectionAccountOut,
    CreditOut,
    HourlyAbono,
    LastRepayment,
    LenderDashboard,
    LenderGreeting,
    LenderLoanRow,
    MerchantDashboard,
    MerchantGreeting,
    NextAction,
)
from app.modules.loans.models import NON_TERMINAL_LOAN_STATUSES, Loan, LoanStatus
from app.modules.loans.service import pct_paid_bps
from app.modules.marketplace.models import LoanRequest, RequestStatus
from app.modules.marketplace.service import market_fit_count
from app.modules.notifications.service import unread_count
from app.modules.recaudo.models import CollectionAccount, CollectionAccountStatus
from app.modules.recaudo.service import current_account
from app.modules.sales.models import Payout, PayoutLeg, Repayment, Sale
from app.modules.sales.schemas import TodaySummaryOut
from app.modules.sales.service import today_summary
from app.modules.scoring.service import CreditProfile, get_credit_profile
from app.modules.users.models import User
from app.modules.users.schemas import Range, initials, lender_ref, merchant_ref, public_name

LAST_REPAYMENTS = 5  # R11
WAIT_DISBURSEMENT = (LoanStatus.PENDING_DISBURSEMENT, LoanStatus.DISBURSING)
LENT_STATUSES = (LoanStatus.ACTIVE, LoanStatus.DISBURSING)  # R9: `total_prestado`

Asset = tuple[str, int]


def _sum(column) -> object:
    """`SUM` como `bigint` (Postgres devuelve `numeric` al sumar `bigint`) y 0 si no hay filas."""
    return cast(func.coalesce(func.sum(column), 0), BigInteger)


def _money(value: int, asset: Asset) -> MoneySchema:
    return MoneySchema.of(Money(value, asset[0], asset[1]))


# ── Comercio ──────────────────────────────────────────────────────────────────


def _credit(profile: CreditProfile) -> CreditOut:
    """R1."""
    a = profile.assessment
    return CreditOut(
        cupo_total=MoneySchema.of(profile.cupo_total),
        cupo_disponible=MoneySchema.of(profile.cupo_disponible),
        score=a.score,
        grade=a.grade,
        eligible=a.eligible,
        unlocks_at=a.unlocks_at,
    )


def _active_loan(loan: Loan, lender: User) -> ActiveLoanOut:
    """R2: `allocated = allocated_total`, `pct_paid_bps` de 11 R13 y `since = activated_at` (null si no está activo)."""
    asset = (loan.asset_code, loan.asset_scale)
    return ActiveLoanOut(
        id=loan.id,
        status=loan.status,
        principal=_money(loan.principal, asset),
        pct_bps=loan.pct_bps,
        lender=lender_ref(lender),
        allocated=_money(loan.allocated_total, asset),
        pct_paid_bps=pct_paid_bps(loan),
        since=loan.activated_at if loan.status == LoanStatus.ACTIVE else None,
        is_late=loan.is_late,
    )


def next_action(
    merchant: User,
    account: CollectionAccount | None,
    loan: Loan | None,
    eligible: bool,
    has_open_request: bool,
    offers_fit_count: int,
) -> NextAction:
    """R6: la primera que aplique, en este orden."""
    if merchant.payout_wallet_url is None:
        return "link_wallet"
    if account is None or account.status != CollectionAccountStatus.ACTIVE:
        return "activate_qr"
    status = loan.status if loan is not None else None
    if status == LoanStatus.PENDING_ACCEPTANCE:
        return "accept_loan"
    if status in WAIT_DISBURSEMENT:
        return "wait_disbursement"
    if status == LoanStatus.ACTIVE:
        return "view_loan"
    if not eligible:
        return "wait_unlock"
    if has_open_request and offers_fit_count > 0:
        return "view_offers"
    return "request_credit"


def merchant_dashboard(db: Session, merchant: User) -> MerchantDashboard:
    profile = get_credit_profile(db, merchant.id)  # R1; puede hacer commit: antes de cualquier otra lectura
    row = db.execute(
        select(Loan, User)
        .join(User, User.id == Loan.lender_id)
        .where(Loan.merchant_id == merchant.id, Loan.status.in_(NON_TERMINAL_LOAN_STATUSES))
    ).first()  # a lo sumo uno (índice único parcial de 01)
    loan, lender = (row[0], row[1]) if row is not None else (None, None)
    summary = today_summary(db, merchant.id, clock.today_local())  # R3: la misma cifra que GET /sales/today
    fit_count = market_fit_count(db, merchant.id, profile)  # R4: el `count` de GET /loan-offers?scope=market
    account = current_account(db, merchant.id)  # R5: la `active`; si no hay, la más reciente
    has_open_request = bool(
        db.scalar(
            select(
                exists().where(LoanRequest.merchant_id == merchant.id, LoanRequest.status == RequestStatus.OPEN)
            )
        )
    )
    business_name = merchant.business_name or ""
    return MerchantDashboard(
        greeting=MerchantGreeting(
            first_name=merchant.first_name,
            business_name=business_name,
            city=merchant.city,
            initials=initials(business_name),
        ),
        unread_notifications=unread_count(db, merchant.id),  # R7
        credit=_credit(profile),
        active_loan=_active_loan(loan, lender) if loan is not None and lender is not None else None,
        today=TodaySummaryOut(
            sales_total=MoneySchema.of(summary.sales_total),
            sales_count=summary.sales_count,
            merchant_total=MoneySchema.of(summary.merchant_total),
            abonado_total=MoneySchema.of(summary.abonado_total),
        ),
        offers_fit_count=fit_count,
        collection_account=(
            CollectionAccountOut(qr_payload=account.qr_payload, status=account.status) if account is not None else None
        ),
        next_action=next_action(
            merchant, account, loan, profile.assessment.eligible, has_open_request, fit_count
        ),
    )


# ── Prestamista ───────────────────────────────────────────────────────────────


@dataclass
class _Totals:
    total_prestado: int = 0
    gross: int = 0
    trust_cut: int = 0
    net: int = 0
    recibido: int = 0


def _today_by_loan(db: Session, lender_id: uuid.UUID, start, end) -> list:
    """R8/R9: Σ bruto, parte Trust y neto de sus abonos con venta de hoy, por crédito (con el asset del crédito)."""
    return db.execute(
        select(
            Repayment.loan_id,
            Loan.asset_code,
            Loan.asset_scale,
            _sum(Repayment.abono_amount),
            _sum(Repayment.trust_cut_amount),
            _sum(Repayment.lender_net_amount),
        )
        .select_from(Repayment)
        .join(Sale, Sale.id == Repayment.sale_id)
        .join(Loan, Loan.id == Repayment.loan_id)
        .where(Repayment.lender_id == lender_id, Sale.paid_at >= start, Sale.paid_at < end)
        .group_by(Repayment.loan_id, Loan.asset_code, Loan.asset_scale)
    ).all()


def _received_today(db: Session, lender_id: uuid.UUID, start, end) -> list:
    """R9: Σ `sent_total` de las patas `lender` de sus créditos con `completed_at` hoy, por asset del crédito."""
    return db.execute(
        select(Loan.asset_code, Loan.asset_scale, _sum(Payout.sent_total))
        .select_from(Payout)
        .join(Loan, Loan.id == Payout.loan_id)
        .where(
            Loan.lender_id == lender_id,
            Payout.leg == PayoutLeg.LENDER,
            Payout.completed_at >= start,
            Payout.completed_at < end,
        )
        .group_by(Loan.asset_code, Loan.asset_scale)
    ).all()


def _hourly(db: Session, lender_id: uuid.UUID, start, end) -> list[HourlyAbono]:
    """R10: Σ bruto por hora local de la venta, de la primera a la última hora con abonos (0 en las vacías).

    MVP: solo el asset de mercado. Sin abonos hoy → `[]`.
    """
    cfg = get_settings()
    hour = cast(extract("hour", func.timezone(cfg.timezone, Sale.paid_at)), Integer).label("hour")
    rows = db.execute(
        select(hour, _sum(Repayment.abono_amount))
        .select_from(Repayment)
        .join(Sale, Sale.id == Repayment.sale_id)
        .where(
            Repayment.lender_id == lender_id,
            Repayment.asset_code == cfg.market_asset_code,
            Repayment.asset_scale == cfg.market_asset_scale,
            Sale.paid_at >= start,
            Sale.paid_at < end,
        )
        .group_by(hour)
    ).all()
    by_hour = {int(h): int(total) for h, total in rows}
    if not by_hour:
        return []
    return [
        HourlyAbono(hour=f"{h:02d}:00", amount=MoneySchema.of(market_money(by_hour.get(h, 0))))
        for h in range(min(by_hour), max(by_hour) + 1)
    ]


def _last_repayments(db: Session, lender_id: uuid.UUID) -> list[LastRepayment]:
    """R11: los 5 más recientes por `paid_at` de la venta (de cualquier día)."""
    rows = db.execute(
        select(Repayment, Sale.paid_at, User)
        .join(Sale, Sale.id == Repayment.sale_id)
        .join(User, User.id == Repayment.merchant_id)
        .where(Repayment.lender_id == lender_id)
        .order_by(Sale.paid_at.desc(), Repayment.id)
        .limit(LAST_REPAYMENTS)
    ).all()
    result = []
    for repayment, paid_at, merchant in rows:
        asset = (repayment.asset_code, repayment.asset_scale)
        result.append(
            LastRepayment(
                id=repayment.id,
                paid_at=paid_at,
                merchant=merchant_ref(merchant),
                gross=_money(repayment.abono_amount, asset),
                trust_cut=_money(repayment.trust_cut_amount, asset),
                net=_money(repayment.lender_net_amount, asset),
            )
        )
    return result


def _mi_rango(lender: User) -> Range | None:
    """R12: Money del asset de mercado; `null` si falta alguno de los dos."""
    if lender.lender_range_min is None or lender.lender_range_max is None:
        return None
    return Range(
        min=MoneySchema.of(market_money(lender.lender_range_min)),
        max=MoneySchema.of(market_money(lender.lender_range_max)),
    )


def lender_dashboard(db: Session, lender: User) -> LenderDashboard:
    cfg = get_settings()
    market: Asset = (cfg.market_asset_code, cfg.market_asset_scale)
    start, end = clock.local_day_bounds(clock.today_local())

    # R8: sus créditos no terminados con el negocio, `active` primero y luego `created_at` desc.
    loan_rows = db.execute(
        select(Loan, User)
        .join(User, User.id == Loan.merchant_id)
        .where(Loan.lender_id == lender.id, Loan.status.in_(NON_TERMINAL_LOAN_STATUSES))
        .order_by(case((Loan.status == LoanStatus.ACTIVE, 0), else_=1), Loan.created_at.desc(), Loan.id.desc())
    ).all()

    # R9: totales por asset del crédito; el de mercado siempre, los demás solo si tienen datos.
    totals: dict[Asset, _Totals] = defaultdict(_Totals)
    totals[market] = _Totals()
    for loan, _ in loan_rows:
        if loan.status in LENT_STATUSES:
            totals[(loan.asset_code, loan.asset_scale)].total_prestado += loan.principal
    today_gross: dict[uuid.UUID, int] = {}
    for loan_id, code, scale, gross, trust_cut, net in _today_by_loan(db, lender.id, start, end):
        today_gross[loan_id] = int(gross)
        entry = totals[(code, scale)]
        entry.gross += int(gross)
        entry.trust_cut += int(trust_cut)
        entry.net += int(net)
    for code, scale, sent in _received_today(db, lender.id, start, end):
        totals[(code, scale)].recibido += int(sent)

    ordered = [market] + sorted(asset for asset in totals if asset != market)
    return LenderDashboard(
        greeting=LenderGreeting(
            first_name=lender.first_name, public_name=public_name(lender.first_name, lender.last_name)
        ),
        unread_notifications=unread_count(db, lender.id),  # R7
        active_loans_count=sum(1 for loan, _ in loan_rows if loan.status == LoanStatus.ACTIVE),
        totals=[
            AssetTotals(
                asset_code=asset[0],
                asset_scale=asset[1],
                total_prestado=_money(totals[asset].total_prestado, asset),
                abonos_hoy=AbonosHoy(
                    gross=_money(totals[asset].gross, asset),
                    trust_cut=_money(totals[asset].trust_cut, asset),
                    net=_money(totals[asset].net, asset),
                ),
                recibido_hoy=_money(totals[asset].recibido, asset),
            )
            for asset in ordered
        ],
        mi_rango=_mi_rango(lender),
        abonos_por_hora=_hourly(db, lender.id, start, end),
        loans=[
            LenderLoanRow(
                id=loan.id,
                merchant=merchant_ref(merchant),
                principal=_money(loan.principal, (loan.asset_code, loan.asset_scale)),
                pct_bps=loan.pct_bps,
                pct_paid_bps=pct_paid_bps(loan),
                today_gross=_money(today_gross.get(loan.id, 0), (loan.asset_code, loan.asset_scale)),
                status=loan.status,
                is_late=loan.is_late,
            )
            for loan, merchant in loan_rows
        ],
        last_repayments=_last_repayments(db, lender.id),
    )
