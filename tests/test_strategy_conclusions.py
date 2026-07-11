"""Tests for scripts/strategy_conclusions.py's one piece of computational
logic: `spearman_agreement`, the R7 sim-vs-backtest rank-correlation stat.
Pure SQL/markdown formatting is deliberately not tested (per the task brief)."""
import math

import pytest

from strategy_conclusions import spearman_agreement


def test_perfect_agreement_rho_one():
    """Identical orderings -> rho == 1.0, and rank 1 == worst on both sides."""
    farm = {0: 0.60, 1: 0.65, 2: 0.70, 3: 0.72}
    backtest = {0: 0.50, 1: 0.53, 2: 0.55, 3: 0.58}
    out = spearman_agreement(farm, backtest)
    assert out["rho"] == pytest.approx(1.0)
    assert out["plans"] == [0, 1, 2, 3]
    assert out["farm_ranks"] == {0: 1, 1: 2, 2: 3, 3: 4}
    assert out["backtest_ranks"] == {0: 1, 1: 2, 2: 3, 3: 4}


def test_perfect_disagreement_rho_minus_one():
    """Exactly reversed orderings -> rho == -1.0."""
    farm = {0: 0.60, 1: 0.65, 2: 0.70, 3: 0.72}
    backtest = {0: 0.58, 1: 0.55, 2: 0.53, 3: 0.50}
    out = spearman_agreement(farm, backtest)
    assert out["rho"] == pytest.approx(-1.0)
    assert out["farm_ranks"] == {0: 1, 1: 2, 2: 3, 3: 4}
    assert out["backtest_ranks"] == {0: 4, 1: 3, 2: 2, 3: 1}


def test_matches_live_r7_ordering():
    """The exact 6-plan orderings this deliverable reports (farm defk=18 tb=0
    vs the in-memory backtest composite) must reduce to rho ~= 0.60 -- the
    number the doc's Section 4 / post-calibration addendum is written around.
    Guards against a silent regression in the stat if the tables are ever
    regenerated. Values are the calibrated-opponent regeneration (Phase 4
    Task 5, farm git ece1500); the pre-calibration ordering gave rho 0.20."""
    farm = {
        0: 0.700734,
        1: 0.710938,
        2: 0.723679,
        3: 0.723714,
        4: 0.700885,
        5: 0.711323,
    }
    backtest = {
        0: 0.530563,
        1: 0.560195,
        2: 0.570022,
        3: 0.554870,
        4: 0.550844,
        5: 0.604026,
    }
    out = spearman_agreement(farm, backtest)
    assert out["rho"] == pytest.approx(0.60, abs=1e-9)
    # Both methods still rank the front-loaded plan 0 WORST (rank 1) -- the one
    # cross-method agreement the QB conclusion leans on, unchanged by calibration.
    assert out["farm_ranks"][0] == 1
    assert out["backtest_ranks"][0] == 1


def test_only_common_plans_are_correlated():
    """Plans present on only one side are dropped; the stat uses the
    intersection so a missing cell can't silently misalign the ranks."""
    farm = {0: 0.60, 1: 0.65, 2: 0.70, 9: 0.99}
    backtest = {0: 0.50, 1: 0.53, 2: 0.55}
    out = spearman_agreement(farm, backtest)
    assert out["plans"] == [0, 1, 2]
    assert 9 not in out["farm_ranks"]
    assert out["rho"] == pytest.approx(1.0)


def test_fails_loud_on_too_few_common_plans():
    with pytest.raises(ValueError, match="need >=3 common plans"):
        spearman_agreement({0: 0.1, 1: 0.2}, {0: 0.3, 1: 0.4})


def test_rho_is_finite_json_serializable_float():
    """rho/p must be plain floats (not numpy scalars) so the doc's evidence
    block and any JSON consumer stay clean."""
    out = spearman_agreement({0: 0.60, 1: 0.65, 2: 0.70}, {0: 0.50, 1: 0.55, 2: 0.53})
    assert type(out["rho"]) is float
    assert type(out["p"]) is float
    assert math.isfinite(out["rho"])
