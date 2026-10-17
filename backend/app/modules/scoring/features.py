"""Insumos del scoring (09 R1): `MerchantFeatures` de 05 R13 a partir de las ventas del negocio.

Lee `sales` con `source` `live` y `seed` y cualquier `status`. Los filtros van por `paid_at` en UTC (usan el índice
`(merchant_id, paid_at)`) y las agrupaciones por mes, semana (lunes a domingo) y día se hacen en SQL sobre
`paid_at AT TIME ZONE TIMEZONE`. Los montos solo suman ventas del asset de mercado.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import BigInteger, Date, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.loans.models import Loan, LoanPctChange, LoanStatus, PctChangeReason
from app.modules.sales.models import Sale
from app.policies.scoring import MerchantFeatures

AVG_MONTHS = 3  # avg_monthly_sales_3m: 3 últimos meses cerrados
CONSISTENCY_WEEKS = 12  # consistency_bps: 12 últimas semanas cerradas
ACTIVE_DAYS_WINDOW = 90  # active_days_bps: los 90 días anteriores a hoy


def _month_index(year: int, month: int) -> int:
    return year * 12 + month - 1


def _month_start(index: int, tz: ZoneInfo) -> datetime:
    """00:00 local del día 1 del mes `index` (año·12 + mes − 1), en UTC."""
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=tz).astimezone(UTC)


def _day_start(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def _full_months(start: datetime, end: datetime) -> int:
    """Meses completos entre dos instantes locales (como una edad: del 15 de junio al 14 de octubre son 3)."""
    months = _month_index(end.year, end.month) - _month_index(start.year, start.month)
    if (end.day, end.timetz().replace(tzinfo=None)) < (start.day, start.timetz().replace(tzinfo=None)):
        months -= 1
    return max(0, months)


def build_features(db: Session, merchant_id: uuid.UUID, now: datetime) -> MerchantFeatures:
    """05 R13 con `as_of = now`. Solo cuentan las ventas con `paid_at <= now`."""
    cfg = get_settings()
    tz = ZoneInfo(cfg.timezone)
    as_of = now.astimezone(UTC)
    local_now = as_of.astimezone(tz)
    today = local_now.date()

    local_paid_at = func.timezone(cfg.timezone, Sale.paid_at)
    market_amount = case(
        (
            (Sale.asset_code == cfg.market_asset_code) & (Sale.asset_scale == cfg.market_asset_scale),
            Sale.amount,
        ),
        else_=0,
    )
    market_total = cast(func.coalesce(func.sum(market_amount), 0), BigInteger)  # SUM(bigint) es numeric
    of_merchant = (Sale.merchant_id == merchant_id, Sale.paid_at <= as_of)

    first_sale_at = db.scalar(select(func.min(Sale.paid_at)).where(*of_merchant))

    # Meses cerrados: todo lo anterior al mes de `as_of`, agrupado por mes local.
    current_month = _month_index(local_now.year, local_now.month)
    month = func.date_trunc("month", local_paid_at)
    monthly = db.execute(
        select(month, func.count(), market_total)
        .where(*of_merchant, Sale.paid_at < _month_start(current_month, tz))
        .group_by(month)
    ).all()
    counts_by_month = {_month_index(m.year, m.month): (count, total) for m, count, total in monthly}
    active_months = sum(1 for count, _ in counts_by_month.values() if count >= cfg.min_sales_per_month)
    last_3 = range(current_month - AVG_MONTHS, current_month)
    avg_monthly_sales_3m = sum(counts_by_month.get(i, (0, 0))[1] for i in last_3) // AVG_MONTHS

    # Semanas cerradas (lunes a domingo): las 12 anteriores a la semana de hoy.
    week_start = today - timedelta(days=today.weekday())
    window_start = week_start - timedelta(weeks=CONSISTENCY_WEEKS)
    week = func.date_trunc("week", local_paid_at)
    weekly = db.execute(
        select(week, market_total)
        .where(*of_merchant, Sale.paid_at >= _day_start(window_start, tz), Sale.paid_at < _day_start(week_start, tz))
        .group_by(week)
    ).all()
    week_totals = {w.date(): total for w, total in weekly}
    weeks = [week_totals.get(window_start + timedelta(weeks=i), 0) for i in range(CONSISTENCY_WEEKS)]
    total_12w = sum(weeks)
    if total_12w == 0:
        consistency_bps = 0
    else:
        # Semana con ventas ≥ 50 % del promedio semanal: total_semana ≥ (total / 12) / 2, en enteros.
        steady = sum(1 for amount in weeks if amount * 2 * CONSISTENCY_WEEKS >= total_12w)
        consistency_bps = steady * 10_000 // CONSISTENCY_WEEKS

    # Días con al menos una venta entre los 90 anteriores a hoy (local).
    active_days = db.scalar(
        select(func.count(func.distinct(cast(local_paid_at, Date)))).where(
            *of_merchant,
            Sale.paid_at >= _day_start(today - timedelta(days=ACTIVE_DAYS_WINDOW), tz),
            Sale.paid_at < _day_start(today, tz),
        )
    )
    active_days_bps = (active_days or 0) * 10_000 // ACTIVE_DAYS_WINDOW

    months_on_platform = _full_months(first_sale_at.astimezone(tz), local_now) if first_sale_at else 0

    loans_paid = db.scalar(
        select(func.count()).select_from(Loan).where(Loan.merchant_id == merchant_id, Loan.status == LoanStatus.PAID)
    )
    pct_increases = db.scalar(
        select(func.count())
        .select_from(LoanPctChange)
        .join(Loan, Loan.id == LoanPctChange.loan_id)
        .where(Loan.merchant_id == merchant_id, LoanPctChange.reason == PctChangeReason.MISSED_MINIMUM)
    )

    return MerchantFeatures(
        as_of=as_of,
        first_sale_at=first_sale_at,
        active_months=active_months,
        avg_monthly_sales_3m=avg_monthly_sales_3m,
        consistency_bps=consistency_bps,
        active_days_bps=active_days_bps,
        months_on_platform=months_on_platform,
        loans_paid=loans_paid or 0,
        pct_increases=pct_increases or 0,
    )
