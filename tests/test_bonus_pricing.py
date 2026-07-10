import pytest

from ffi.scoring.bonus_pricing import bonus_ev_per_week, weekly_threshold_prob
from ffi.scoring.config import load_config_v1

TIERS = load_config_v1().offense.yardage_bonuses["rec_yards"]


def test_probability_monotone_in_mean():
    cv = 0.6
    p_low = weekly_threshold_prob(50, cv, 100)
    p_mid = weekly_threshold_prob(90, cv, 100)
    p_high = weekly_threshold_prob(130, cv, 100)
    assert p_low < p_mid < p_high


def test_mean_at_threshold_is_roughly_half():
    # gamma is right-skewed so P(X >= mean) is a bit under 0.5 — sanity band
    p = weekly_threshold_prob(100, 0.5, 100)
    assert 0.30 < p < 0.55


def test_zero_mean_prices_zero():
    assert bonus_ev_per_week(0, 0.6, TIERS) == 0.0


def test_ev_includes_all_tiers():
    # enormous mean -> hits all three tiers nearly every week -> EV ≈ 12
    assert bonus_ev_per_week(400, 0.3, TIERS) == pytest.approx(12.0, abs=0.2)


def test_invalid_cv_fails_loud():
    with pytest.raises(ValueError):
        weekly_threshold_prob(90, 0, 100)
