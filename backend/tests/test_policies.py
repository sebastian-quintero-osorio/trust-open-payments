"""05-policies: un test por criterio de aceptación (más reglas sin AC propio). Funciones puras: sin base ni red."""

import json
import random
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime

import pytest

from app.core.money import bps_of
from app.policies.matching import Proposal, fits
from app.policies.repricing import ReviewDecision, review_period
from app.policies.scoring import Assessment, MerchantFeatures, assess, grade_for
from app.policies.split import SaleSplit, SplitLoanState, split_sale
from app.policies.terms import Terms, build_terms
from tests.helpers import cop

# Crédito de ejemplo `L` de la spec y venta de $48.000.
PCT, TOTAL, FEE, SHARE = 1500, cop(2_100_000).value, cop(100_000).value, 2000
SALE = cop(48_000).value
AS_OF = datetime(2026, 10, 16, 15, 0, tzinfo=UTC)  # 16 oct 2026, 10:00 en Bogotá


def _loan(allocated: int = 0) -> SplitLoanState:
    return SplitLoanState(PCT, TOTAL, allocated, FEE, SHARE)


def _pay_off(amount: int, loan: SplitLoanState, policy: str = "prorated") -> list[SaleSplit]:
    """Divide ventas de `amount` hasta `paid_off`, verificando R5 en cada una."""
    splits: list[SaleSplit] = []
    allocated = loan.allocated_total
    for _ in range(100_000):
        state = SplitLoanState(loan.pct_bps, loan.total_repayable, allocated, loan.fee_amount, loan.trust_share_bps)
        split = split_sale(amount, state, policy=policy)
        assert split.merchant_amount + split.lender_net + split.trust_cut == amount
        assert 0 <= split.trust_cut <= split.abono
        assert split.allocated_after == allocated + split.abono
        splits.append(split)
        allocated = split.allocated_after
        if split.paid_off:
            return splits
    raise AssertionError("el crédito nunca se terminó de pagar")


@dataclass(frozen=True)
class _Range:
    min_amount: int
    max_amount: int
    pct_bps: int


def _range(low: int, high: int, pct: int) -> _Range:
    return _Range(cop(low).value, cop(high).value, pct)


def _features(**overrides) -> MerchantFeatures:
    values = {
        "as_of": AS_OF,
        "first_sale_at": datetime(2026, 4, 2, 15, 0, tzinfo=UTC),
        "active_months": 6,
        "avg_monthly_sales_3m": cop(16_000_000).value,
        "consistency_bps": 10_000,
        "active_days_bps": 8_000,
        "months_on_platform": 6,
        "loans_paid": 0,
        "pct_increases": 0,
    }
    return MerchantFeatures(**{**values, **overrides})


# ── AC ────────────────────────────────────────────────────────────────────────


def test_ac1_split_without_loan():
    split = split_sale(SALE, None)

    assert split == SaleSplit(abono=0, trust_cut=0, lender_net=0, merchant_amount=SALE, allocated_after=0,
                              paid_off=False)
    assert split.merchant_amount == cop(48_000).value


def test_ac2_split_basic():
    split = split_sale(SALE, _loan(0))

    assert split.abono == cop(7_200).value
    assert split.merchant_amount == cop(40_800).value
    assert split.trust_cut == cop("68,57").value
    assert split.lender_net == cop("7.131,43").value
    assert split.allocated_after == cop(7_200).value
    assert split.paid_off is False


def test_ac3_last_abono_capped():
    split = split_sale(SALE, _loan(cop(2_095_000).value))

    assert split.abono == cop(5_000).value
    assert split.merchant_amount == cop(43_000).value
    assert split.allocated_after == cop(2_100_000).value
    assert split.paid_off is True
    assert split.merchant_amount + split.lender_net + split.trust_cut == SALE


def test_ac4_prorated_sums_exactly():
    splits = _pay_off(SALE, _loan(0))

    assert sum(s.abono for s in splits) == cop(2_100_000).value
    assert sum(s.trust_cut for s in splits) == cop(20_000).value == bps_of(FEE, SHARE)
    assert sum(s.lender_net for s in splits) == cop(2_080_000).value

    rng = random.Random(20261017)
    for _ in range(1_000):
        principal = rng.randrange(50_000_000, 500_000_001, 100)
        fee_amount = bps_of(principal, rng.randint(0, 2_000))
        loan = SplitLoanState(
            pct_bps=rng.randint(100, 3_000),
            total_repayable=principal + fee_amount,
            allocated_total=0,
            fee_amount=fee_amount,
            trust_share_bps=rng.randint(0, 10_000),
        )
        allocated = trust = 0
        while True:
            amount = rng.choice((rng.randint(100, 10_000), rng.randint(100_000, 50_000_000)))
            state = SplitLoanState(loan.pct_bps, loan.total_repayable, allocated, loan.fee_amount,
                                   loan.trust_share_bps)
            split = split_sale(amount, state)
            assert split.merchant_amount + split.lender_net + split.trust_cut == amount  # R5
            assert 0 <= split.trust_cut <= split.abono
            allocated, trust = split.allocated_after, trust + split.trust_cut
            if split.paid_off:
                break
        assert allocated == loan.total_repayable
        assert trust == bps_of(loan.fee_amount, loan.trust_share_bps)


def test_ac5_literal_capped_at_fee():
    first = split_sale(SALE, _loan(0), policy="literal")
    assert first.trust_cut == cop(1_440).value
    assert first.lender_net == cop(7_200).value - cop(1_440).value

    splits = _pay_off(SALE, _loan(0), policy="literal")
    assert sum(s.trust_cut for s in splits) == cop(100_000).value == FEE
    assert sum(s.lender_net for s in splits) == cop(2_000_000).value  # = principal
    assert sum(s.abono for s in splits) == TOTAL


def test_ac6_build_terms(settings):
    terms = build_terms(200_000_000, 1500, 500, 3, 2000, settings)

    assert terms.principal == cop(2_000_000).value
    assert terms.fee_amount == cop(100_000).value
    assert terms.total_repayable == cop(2_100_000).value
    assert terms.monthly_minimum == cop(700_000).value
    assert (terms.pct_initial_bps, terms.pct_min_bps, terms.pct_max_bps) == (1500, 1000, 2000)
    assert terms.trust_share_bps == 2000
    assert (terms.fee_bps, terms.term_months) == (500, 3)

    other = build_terms(100_000_000, 1500, 1000, 3, None, settings)
    assert other.total_repayable == cop(1_100_000).value
    assert other.monthly_minimum == cop("366.666,67").value
    assert other.pct_max_bps == 2000  # 1500 + REPRICE_MAX_DELTA_BPS


def test_ac7_fits():
    request, offer = _range(1_500_000, 3_000_000, 1500), _range(1_000_000, 2_500_000, 1500)
    step = cop(50_000).value

    assert fits(request, offer, cop(6_000_000).value, step) == Proposal(
        amount=cop(2_000_000).value,
        overlap_min=cop(1_500_000).value,
        overlap_max=cop(2_500_000).value,
        pct_bps=1500,
    )
    assert fits(request, offer, cop(1_800_000).value, step).amount == cop(1_800_000).value
    assert fits(request, offer, cop(1_000_000).value, step) is None
    assert fits(request, _range(1_000_000, 2_500_000, 2000), cop(6_000_000).value, step) is None
    assert fits(_range(3_000_000, 4_000_000, 1500), offer, cop(6_000_000).value, step) is None


def test_ac8_review_period():
    def review(allocated: int, pct: int = 1500, balance: int = 2_100_000) -> ReviewDecision:
        return review_period(pct, 1000, 2000, cop(700_000).value, cop(balance).value, cop(allocated).value, 250)

    target = cop(700_000).value
    assert review(500_000) == ReviewDecision(1750, "missed_minimum", target, True)
    assert review(875_000) == ReviewDecision(1250, "ahead_of_schedule", target, False)
    assert review(700_000) == ReviewDecision(1500, "unchanged", target, False)
    assert review(100_000, pct=2000) == ReviewDecision(2000, "unchanged", target, True)
    assert review(100_000, balance=300_000).target_amount == cop(300_000).value
    assert review(0, balance=0) == ReviewDecision(1500, "unchanged", 0, False)


def test_ac9_assess_grade_a_cupo(settings):
    result = assess(_features(), settings)

    assert isinstance(result, Assessment)
    assert (result.policy_version, result.score, result.grade, result.eligible) == ("v1", 845, "A", True)
    assert result.cupo_total == cop(6_000_000).value
    assert result.unlocks_at is None
    assert result.reasons == [
        "Buen volumen de ventas en los últimos 3 meses.",
        "Ventas constantes semana a semana.",
        "Sin historial de créditos: cuenta como neutro.",
    ]

    capped = assess(_features(), settings.model_copy(update={"cupo_cap": 500_000_000}))
    assert capped.cupo_total == cop(5_000_000).value


def test_ac10_assess_not_eligible_and_grades(settings):
    result = assess(_features(active_months=1, first_sale_at=datetime(2026, 8, 20, 15, 0, tzinfo=UTC)), settings)

    assert result.eligible is False
    assert result.cupo_total == 0
    assert result.unlocks_at == datetime(2026, 12, 1, 5, 0, tzinfo=UTC)  # 2026-12-01T05:00:00Z
    assert result.reasons[0].startswith("Te faltan 2 meses")
    assert result.reasons[0] == "Te faltan 2 meses de ventas con el QR de Trust."

    assert [grade_for(s) for s in (1000, 750, 749, 600, 599, 450, 449, 0)] == ["A", "A", "B", "B", "C", "C", "D", "D"]
    # Grado D (elegible): factor 0 → cupo 0. Score 350 = solo volumen al máximo.
    grade_d = assess(_features(consistency_bps=0, active_days_bps=0, months_on_platform=0, pct_increases=5), settings)
    assert (grade_d.score, grade_d.grade, grade_d.eligible, grade_d.cupo_total) == (350, "D", True, 0)


# ── Reglas sin AC propio ──────────────────────────────────────────────────────


def test_r1_r5_split_validation():
    with pytest.raises(ValueError):
        split_sale(SALE, _loan(0), policy="otra")
    with pytest.raises(ValueError):
        split_sale(-1, None)
    with pytest.raises(FrozenInstanceError):
        _loan(0).pct_bps = 1000  # type: ignore[misc]
    # Crédito ya pagado: el abono es 0 y todo va al comercio.
    done = split_sale(SALE, _loan(TOTAL))
    assert (done.abono, done.trust_cut, done.merchant_amount) == (0, 0, SALE)


def test_r4_literal_never_exceeds_fee_and_respects_r5():
    rng = random.Random(7)
    for _ in range(200):
        principal = rng.randrange(50_000_000, 300_000_001, 100)
        fee_amount = bps_of(principal, rng.randint(0, 2_000))
        loan = SplitLoanState(rng.choice((1000, 1500, 2000)), principal + fee_amount, 0, fee_amount,
                              rng.randint(0, 10_000))
        splits = _pay_off(rng.randint(1_000_000, 40_000_000), loan, policy="literal")
        assert sum(s.trust_cut for s in splits) == min(bps_of(loan.total_repayable, loan.trust_share_bps), fee_amount)
        assert sum(s.lender_net for s in splits) >= principal  # el prestamista recupera el principal


def test_r6_r7_terms_validation_and_snapshot(settings):
    for args in ((0, 1500, 500, 3), (-1, 1500, 500, 3), (100_000_000, 1500, -1, 3), (100_000_000, 1500, 500, 0)):
        with pytest.raises(ValueError):
            build_terms(*args, None, settings)

    terms = build_terms(150_000_000, 1500, 600, 3, None, settings)
    snapshot = terms.to_dict()
    assert isinstance(terms, Terms)
    assert snapshot == {
        "principal": 150_000_000, "fee_bps": 600, "fee_amount": 9_000_000, "total_repayable": 159_000_000,
        "trust_share_bps": 2000, "term_months": 3, "monthly_minimum": 53_000_000,
        "pct_initial_bps": 1500, "pct_min_bps": 1000, "pct_max_bps": 2000,
    }
    assert json.loads(json.dumps(snapshot)) == snapshot  # va tal cual a `loans.terms` (JSONB)
    assert build_terms(100_000_000, 800, 0, 1, 700, settings).pct_min_bps == 500  # piso absoluto
    custom = settings.model_copy(update={"trust_share_bps": 1000, "reprice_max_delta_bps": 300})
    assert (build_terms(100_000_000, 1500, 0, 3, None, custom).trust_share_bps,
            build_terms(100_000_000, 1500, 0, 3, None, custom).pct_max_bps) == (1000, 1800)


def test_r9_amount_clamped_to_overlap():
    offer = _range(1_520_000, 1_530_000, 1000)  # punto medio redondeado al paso cae bajo el mínimo común
    proposal = fits(_range(1_520_000, 1_530_000, 1500), offer, cop(6_000_000).value, cop(50_000).value)
    assert proposal == Proposal(cop(1_520_000).value, cop(1_520_000).value, cop(1_530_000).value, 1000)
    with pytest.raises(ValueError):
        fits(offer, offer, cop(6_000_000).value, 0)


def test_r12_floor_keeps_unchanged():
    decision = review_period(1000, 1000, 2000, cop(700_000).value, cop(2_100_000).value, cop(900_000).value, 250)
    assert decision == ReviewDecision(1000, "unchanged", cop(700_000).value, False)


def test_r17_r18_unlocks_and_reasons(settings):
    no_sales = assess(_features(first_sale_at=None, active_months=0, avg_monthly_sales_3m=0, consistency_bps=0,
                                active_days_bps=0, months_on_platform=0), settings)
    assert (no_sales.eligible, no_sales.cupo_total, no_sales.unlocks_at) == (False, 0, None)
    assert no_sales.reasons == [
        "Empieza a cobrar con tu QR de Trust para construir historial.",
        "Volumen de ventas bajo en los últimos 3 meses.",
        "Ventas irregulares entre semanas.",
        "Sin historial de créditos: cuenta como neutro.",
    ]
    assert no_sales.features["first_sale_at"] is None

    december = datetime(2026, 12, 20, 15, 0, tzinfo=UTC)
    rollover = assess(_features(as_of=december, active_months=0), settings)
    assert rollover.unlocks_at == datetime(2027, 3, 1, 5, 0, tzinfo=UTC)  # cruza de año
    one_left = assess(_features(active_months=2, loans_paid=1), settings)
    assert one_left.reasons[0] == "Te falta 1 mes de ventas con el QR de Trust."
    assert one_left.reasons[-1] == "Has pagado 1 crédito con Trust."
    assert assess(_features(loans_paid=2), settings).reasons[-1] == "Has pagado 2 créditos con Trust."
    assert assess(_features(pct_increases=1), settings).reasons[-1] == "Hubo meses por debajo del abono mínimo."
    assert len(assess(_features(active_months=0), settings).reasons) <= 4


def test_r14_r19_components_and_features(settings):
    paid = assess(_features(loans_paid=3, pct_increases=1), settings)  # pay = min(10000, 5000+7500−1000)
    assert paid.score == (3500 * 10_000 + 2500 * 10_000 + 1500 * 8_000 + 1000 * 5_000 + 1500 * 10_000) // 100_000
    features = paid.features
    assert json.loads(json.dumps(features)) == features
    assert features["as_of"] == "2026-10-16T15:00:00Z" and features["active_months"] == 6
    assert features["avg_monthly_sales_3m"] == cop(16_000_000).value
    with pytest.raises(ValueError):
        assess(_features(), settings.model_copy(update={"scoring_policy": "v2"}))
