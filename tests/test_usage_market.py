"""usage/market.py: normalization + fail-closed urgency."""
import pytest

from ffi.usage import ASCENDING, FALLING, WATCH, TrendSignal
from ffi.usage.market import (
    MarketState,
    MarketTrend,
    Refusal,
    aggregate,
    normalize,
    urgency,
)


def _signal(direction):
    return TrendSignal(
        gsis_id="G1",
        direction=direction,
        rule_id="snap_rise_2wk",
        evidence="snap share 60% -> 70%",
        cold_start=False,
    )


def test_normalize_builds_market_trends():
    rows = [
        {"player_id": "101", "count": 12, "player_name": "A"},
        {"player_id": "202", "count": 7},
    ]
    out = normalize(1, "2026-09-03", "add", rows)
    assert len(out) == 2
    assert out[0].trend_type == "add"
    assert out[0].count == 12


def test_normalize_rejects_schema_drift():
    with pytest.raises(ValueError, match="player_id"):
        normalize(1, "2026-09-03", "add", [{"count": 5}])


def test_aggregate_ranks_most_added_first():
    trends = [
        MarketTrend("A", "add", 10),
        MarketTrend("B", "add", 20),
        MarketTrend("A", "drop", 2),
    ]
    state = aggregate("A", trends)
    assert state.add_count == 10
    assert state.drop_count == 2
    assert state.add_rank == 2  # B (20) is #1, A (10) is #2


def test_urgency_refuses_on_failed_market_gate():
    state = MarketState(add_count=5, drop_count=0, gate_failed=True)
    result = urgency(_signal(ASCENDING), state)
    assert isinstance(result, Refusal)
    assert "gate failed" in result.reason


def test_urgency_falling_scores_zero():
    state = MarketState(add_count=5, drop_count=0)
    assert urgency(_signal(FALLING), state) == 0.0


def test_urgency_ascending_weights_higher_than_watch():
    state = MarketState(add_count=5, drop_count=0)
    asc = urgency(_signal(ASCENDING), state)
    watch = urgency(_signal(WATCH), state)
    assert asc > watch


def test_urgency_saturates_at_net_add_full():
    state = MarketState(add_count=10, drop_count=0, add_rank=1)
    assert urgency(_signal(ASCENDING), state) == 1.0


def test_urgency_zero_net_add_is_zero():
    state = MarketState(add_count=3, drop_count=3, add_rank=None)
    assert urgency(_signal(ASCENDING), state) == 0.0
