import datetime
import pathlib
from decimal import Decimal

import pytest

from ffi.health import (
    SourceState,
    UnknownSourceError,
    is_alarming,
    load_clock,
    render_or_refuse,
    state,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CLOCK = load_clock(REPO_ROOT / "config" / "source_clock.yaml")


def test_r1_regression_stale_but_successful_is_not_ok():
    """THE bug this module exists to kill.

    On 2026-08-29 the briefing rendered `[OK] nflverse_player_week: last run
    1209h ago (success)`. The health loop tested `status == 'success'` and
    never tested age — on the same line that computed and printed the age.
    A successful run that is 50 days old is BROKEN, not OK.
    """
    assert state("nflverse_player_week", "success", 1209.0, CLOCK) is SourceState.BROKEN
    assert is_alarming(SourceState.BROKEN) is True


def test_fresh_success_is_ok():
    assert state("nflverse_player_week", "success", 12.0, CLOCK) is SourceState.OK


def test_structural_lag_is_known_lagging_not_alarming():
    # nflverse: OK to 60h (24 + 36), KNOWN-LAGGING to 96h. The Tuesday-after-
    # MNF publish delay must NOT produce a banner (R12).
    assert (
        state("nflverse_player_week", "success", 72.0, CLOCK)
        is SourceState.KNOWN_LAGGING
    )
    assert is_alarming(SourceState.KNOWN_LAGGING) is False


def test_deadline_boundary_is_inclusive_of_known_lagging():
    assert (
        state("nflverse_player_week", "success", 96.0, CLOCK)
        is SourceState.KNOWN_LAGGING
    )
    assert state("nflverse_player_week", "success", 96.1, CLOCK) is SourceState.BROKEN


def test_sleeper_has_a_tighter_contract_than_nflverse():
    # The old global STALE_HOURS = 36 applied one number to every source.
    assert (
        state("sleeper_projections", "success", 40.0, CLOCK)
        is SourceState.KNOWN_LAGGING
    )
    assert state("nflverse_player_week", "success", 40.0, CLOCK) is SourceState.OK


def test_failed_status_is_broken_regardless_of_age():
    assert state("sleeper_projections", "failed", 0.5, CLOCK) is SourceState.BROKEN
    assert state("sleeper_projections", "running", 0.5, CLOCK) is SourceState.BROKEN
    assert (
        state("sleeper_projections", "sanity_failed", 0.5, CLOCK) is SourceState.BROKEN
    )


def test_sanity_warned_is_never_better_than_known_lagging():
    # Observe-and-log mode stores the payload, so the run is not BROKEN — but
    # a gate fired, so it is never OK either.
    assert (
        state("sleeper_trending", "sanity_warned", 1.0, CLOCK)
        is SourceState.KNOWN_LAGGING
    )
    assert state("sleeper_trending", "sanity_warned", 99.0, CLOCK) is SourceState.BROKEN


def test_unknown_source_raises_rather_than_defaulting():
    with pytest.raises(UnknownSourceError, match="yahoo_probe"):
        state("yahoo_probe", "success", 1.0, CLOCK)


def test_negative_age_raises():
    with pytest.raises(ValueError, match="age_h"):
        state("sleeper_projections", "success", -1.0, CLOCK)


def test_decimal_age_from_psycopg2_is_accepted():
    """Task 4 reads age via `extract(epoch FROM now() - started_at)/3600`,
    which psycopg2 hands back as Decimal, not float. age_h must stay
    duck-typed: narrowing it to isinstance(age_h, (int, float)) would reject
    the only real caller while every float-based test kept passing.
    """
    assert (
        state("nflverse_player_week", "success", Decimal("1209"), CLOCK)
        is SourceState.BROKEN
    )
    assert (
        state("nflverse_player_week", "success", Decimal("12"), CLOCK) is SourceState.OK
    )
    with pytest.raises(ValueError, match="age_h"):
        state("sleeper_projections", "success", Decimal("-1"), CLOCK)


def test_clock_carries_as_of_and_expected_season():
    assert CLOCK.as_of == datetime.date(2026, 8, 31)
    assert CLOCK.contract("nflverse_player_week").expected_season == 2025
    assert CLOCK.contract("sleeper_projections").expected_season is None


def test_artifact_contracts_parse():
    trends = CLOCK.artifacts["trends"]
    assert trends.glob == "reports/trends-*.md"
    assert trends.due_weekday == "tuesday"
    assert trends.active_from == datetime.date(2026, 9, 15)


def test_missing_contract_field_fails_loud(tmp_path):
    bad = tmp_path / "clock.yaml"
    bad.write_text(
        "as_of: 2026-08-31\n"
        "sources:\n"
        "  x:\n"
        "    expected_interval_h: 24\n"
        "    owner: nobody\n"
        "artifacts: {}\n"
    )
    with pytest.raises(ValueError, match="lag_window_h"):
        load_clock(bad)


def test_render_or_refuse_refuses_on_any_broken_input():
    out = render_or_refuse(
        {"nflverse": SourceState.OK, "sleeper": SourceState.BROKEN},
        lambda: "COMPUTED",
    )
    assert out == "NO SIGNAL — sleeper BROKEN"


def test_render_or_refuse_renders_when_all_ok():
    out = render_or_refuse({"sleeper": SourceState.OK}, lambda: "COMPUTED")
    assert out == "COMPUTED"


def test_render_or_refuse_renders_on_known_lagging():
    # Structural lag is NOT alarming (R12) — a KNOWN-LAGGING input still renders.
    out = render_or_refuse({"sleeper": SourceState.KNOWN_LAGGING}, lambda: "X")
    assert out == "X"
