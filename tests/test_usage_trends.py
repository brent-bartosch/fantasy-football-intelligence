import pytest

from ffi.usage import ASCENDING, FALLING, WATCH, UsageFrame, UsageRow
from ffi.usage.coldstart import COLD_START_MAX_WEEK, COLD_START_RULES
from ffi.usage.trends import STANDARD_RULES, classify

ALL_METRICS = frozenset(
    {"snap_share", "target_share", "carry_share", "route_share", "rz_touches"}
)


def _row(gsis_id, week, **kw):
    base = dict(
        gsis_id=gsis_id,
        season=2025,
        week=week,
        team="SF",
        position="RB",
        snap_share=None,
        target_share=None,
        carry_share=None,
        route_share=None,
        rz_touches=None,
        team_targets=34,
        team_carries=26,
        games_complete=1,
        teams_observed=32,
    )
    base.update(kw)
    return UsageRow(**base)


def _frames(rows_by_week: dict, available=ALL_METRICS) -> list[UsageFrame]:
    disabled = tuple(sorted(ALL_METRICS - available))
    return [
        UsageFrame(
            season=2025,
            week=w,
            rows=tuple(rows),
            available_metrics=available,
            disabled_metrics=disabled,
            teams_observed=32,
        )
        for w, rows in sorted(rows_by_week.items())
    ]


# --- Fixture 1: a backfield flip ------------------------------------------
# BACKUP takes over across weeks 4-6: snaps cross 55%, target share steps up
# more than 5pp over its own 3-week baseline, red-zone touches climb strictly.
# STARTER's route share collapses to under 60% of baseline.
def _backfield_flip_frames():
    weeks = {}
    for w, (b_snap, b_tgt, b_rz, s_route) in {
        3: (0.30, 0.06, 1, 0.80),
        4: (0.35, 0.07, 1, 0.78),
        5: (0.62, 0.13, 2, 0.79),
        6: (0.71, 0.15, 4, 0.30),
    }.items():
        weeks[w] = [
            _row(
                "BACKUP",
                w,
                snap_share=b_snap,
                target_share=b_tgt,
                rz_touches=b_rz,
                route_share=0.5,
                carry_share=b_snap,
            ),
            _row(
                "STARTER",
                w,
                snap_share=0.9 - b_snap,
                target_share=0.20,
                rz_touches=5,
                route_share=s_route,
                carry_share=0.5,
            ),
        ]
    return _frames(weeks)


def test_backfield_flip_fires_ascending_for_the_backup():
    result = classify(_backfield_flip_frames(), week=6)
    backup = [s for s in result.signals if s.gsis_id == "BACKUP"]
    assert {s.rule_id for s in backup} >= {
        "snap_rise_2wk",
        "target_share_step",
        "rz_climb_3wk",
    }
    assert all(s.direction == ASCENDING for s in backup)
    assert all(not s.cold_start for s in backup)


def test_backfield_flip_fires_falling_for_the_displaced_starter():
    result = classify(_backfield_flip_frames(), week=6)
    starter = [s for s in result.signals if s.gsis_id == "STARTER"]
    assert [s.rule_id for s in starter] == ["route_collapse"]
    assert starter[0].direction == FALLING


def test_every_signal_carries_traceable_evidence():
    for s in classify(_backfield_flip_frames(), week=6).signals:
        assert s.rule_id
        assert s.evidence
        assert str(round(6)) in s.evidence or "wk" in s.evidence


# --- Fixture 2: nothing happened ------------------------------------------
def _no_change_frames():
    weeks = {
        w: [
            _row(
                "STEADY",
                w,
                snap_share=0.70,
                target_share=0.18,
                rz_touches=2,
                route_share=0.72,
                carry_share=0.40,
            )
        ]
        for w in (3, 4, 5, 6)
    }
    return _frames(weeks)


def test_no_change_fixture_emits_nothing():
    """The negative fixture. R10: round-number rules tuned on a single
    positive fixture produce 40+ flags a week against a 5-move budget."""
    assert classify(_no_change_frames(), week=6).signals == ()


def test_a_workhorse_above_55pct_every_week_does_not_re_fire():
    # STEADY is at 70% snaps in all four weeks. Without the crossing guard,
    # snap_rise_2wk would flag every workhorse in the league, every week.
    assert not [
        s
        for s in classify(_no_change_frames(), week=6).signals
        if s.rule_id == "snap_rise_2wk"
    ]


# --- Fixture 3: cold start, week 2 ---------------------------------------
def _cold_start_frames():
    weeks = {
        1: [_row("ROOKIE", 1, snap_share=0.30, target_share=0.05, route_share=0.40)],
        2: [_row("ROOKIE", 2, snap_share=0.68, target_share=0.14, route_share=0.60)],
    }
    return _frames(weeks)


def test_cold_start_week_2_is_not_silent():
    """R7: an empty ASC/FALL section in weeks 1-3 is not acceptable — those
    are the weeks league-winners are disproportionately claimed."""
    result = classify(_cold_start_frames(), week=2)
    assert result.signals != ()
    assert {s.rule_id for s in result.signals} >= {
        "snap_rise_1wk_cs",
        "target_share_step_cs",
    }
    assert all(s.cold_start for s in result.signals)


def test_cold_start_rules_are_inactive_after_week_3():
    result = classify(_backfield_flip_frames(), week=6)
    assert all(not s.cold_start for s in result.signals)
    assert COLD_START_MAX_WEEK == 3


def test_standard_rules_are_inactive_during_cold_start():
    result = classify(_cold_start_frames(), week=2)
    assert all(s.rule_id.endswith("_cs") for s in result.signals)


# --- Metric availability --------------------------------------------------
def test_rules_needing_an_unavailable_metric_are_disabled_loudly():
    available = frozenset({"snap_share", "target_share", "carry_share"})
    weeks = {w: [_row("X", w, snap_share=0.7, target_share=0.2)] for w in (3, 4, 5, 6)}
    result = classify(_frames(weeks, available=available), week=6)
    disabled = dict(result.disabled_rules)
    assert "rz_climb_3wk" in disabled
    assert "route_collapse" in disabled
    assert "rz_touches" in disabled["rz_climb_3wk"]
    assert not [s for s in result.signals if s.rule_id in disabled]


def test_rules_needing_more_weeks_than_exist_are_disabled_loudly():
    weeks = {5: [_row("X", 5, snap_share=0.7)], 6: [_row("X", 6, snap_share=0.7)]}
    result = classify(_frames(weeks), week=6)
    disabled = dict(result.disabled_rules)
    assert "target_share_step" in disabled
    assert "min_weeks" in disabled["target_share_step"]


def test_classify_rejects_frames_that_do_not_end_at_the_requested_week():
    with pytest.raises(ValueError, match="newest frame"):
        classify(_no_change_frames(), week=9)


def test_classify_rejects_unordered_frames():
    frames = list(reversed(_no_change_frames()))
    with pytest.raises(ValueError, match="oldest-to-newest"):
        classify(frames, week=3)


def test_incomplete_team_weeks_never_produce_a_signal():
    weeks = {
        w: [_row("X", w, snap_share=0.7, target_share=0.30, games_complete=0)]
        for w in (3, 4, 5, 6)
    }
    assert classify(_frames(weeks), week=6).signals == ()


def test_rule_catalogues_are_disjoint_and_well_formed():
    ids = [r.rule_id for r in STANDARD_RULES + COLD_START_RULES]
    assert len(ids) == len(set(ids))
    assert all(r.cold_start for r in COLD_START_RULES)
    assert all(not r.cold_start for r in STANDARD_RULES)
    assert all(
        r.min_weeks >= 1 and r.requires and r.describe
        for r in STANDARD_RULES + COLD_START_RULES
    )
