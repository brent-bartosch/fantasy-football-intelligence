"""Reports: claims + trends render through render_or_refuse."""
from ffi.health import SourceState
from ffi.reports.claims import render_claims
from ffi.reports.trends import render_trends


def test_claims_refuses_on_broken_input():
    out = render_claims(1, inputs={"sleeper_trending": SourceState.BROKEN})
    assert out.startswith("NO SIGNAL —")
    assert "sleeper_trending BROKEN" in out


def test_claims_renders_header_when_all_ok():
    out = render_claims(1, inputs={"sleeper_trending": SourceState.OK}, conn=None)
    assert "# Claims Brief — Week 1" in out
    assert "Waiver deadline:" in out


def test_claims_requires_conn_or_inputs():
    import pytest

    with pytest.raises(ValueError, match="conn or an explicit inputs"):
        render_claims(1)


def test_trends_refuses_on_broken_usage():
    out = render_trends(1, inputs={"nflverse_player_week": SourceState.BROKEN})
    assert out.startswith("NO SIGNAL —")
    assert "nflverse_player_week BROKEN" in out


def test_trends_renders_signals_and_angles():
    from ffi.trade_angles import Angle
    from ffi.usage import ASCENDING, TrendSignal

    sig = TrendSignal("G1", ASCENDING, "snap_rise_2wk", "snap 60% -> 70%", False)
    angle = Angle("opponent_need", "RB", None, 3, "team 3 thin at RB")
    out = render_trends(
        2, inputs={"nflverse_player_week": SourceState.OK}, signals=[sig], angles=[angle]
    )
    assert "# Trends & Targets — Week 2" in out
    assert "[ASCENDING] G1" in out
    assert "[opponent_need]" in out
