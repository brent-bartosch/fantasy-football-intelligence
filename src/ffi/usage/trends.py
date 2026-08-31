"""Role-change rules over usage history -> ASCENDING / FALLING / WATCH.

Each rule declares `min_weeks` (how much history it needs) and `requires`
(which metrics must exist). A rule whose window or inputs are unavailable is
DISABLED and named in TrendResult.disabled_rules — never silently skipped
and never evaluated against nulls (R27).

Every signal carries a rule_id and an evidence string. ADR Domain 5: a false
ASCENDING in week 6 must be traceable to the rule and the threshold that
fired it, which is what makes the Plan 2 precision work possible at all.

THRESHOLDS HERE ARE STARTING POINTS, NOT TUNED VALUES. Plan 2 tunes them on
2024 and gates on 2025 (never on the gate season). R10 is the risk being
managed: round numbers tuned on one positive fixture produce 40+ flags a
week against a 5-move budget.
"""

from collections import Counter
from collections.abc import Sequence

import structlog

from ffi.usage import (
    ASCENDING,
    FALLING,
    Rule,
    TrendResult,
    TrendSignal,
    UsageFrame,
    UsageRow,
)
from ffi.usage.coldstart import COLD_START_MAX_WEEK, evaluate_cold_start

log = structlog.get_logger()

SNAP_THRESHOLD = 0.55
TARGET_SHARE_STEP = 0.05  # +5pp vs the 3-week baseline
ROUTE_COLLAPSE_RATIO = 0.60  # <= 60% of baseline is a -40% collapse
BASELINE_WEEKS = 3


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _snap_rise_2wk(hist: list[UsageRow]) -> str | None:
    """Snap share above threshold in both of the last two weeks, having been
    at or below it before. The crossing guard is what stops every workhorse
    in the league from re-firing every week.

    Requires 3 observed weeks (min_weeks=3), not 2: with only two weeks the
    prior week is unobserved and the crossing cannot be verified, so an
    established starter sliding 91% -> 80% would fire ASCENDING. That is a
    falsifiability precondition, not a threshold — an unverifiable crossing
    is a loud min_weeks disablement, never a wrong-direction signal.
    """
    last_two = [r.snap_share for r in hist[-2:]]
    if any(v is None or v <= SNAP_THRESHOLD for v in last_two):
        return None
    prior = hist[-3].snap_share  # guaranteed to exist by min_weeks=3
    if prior is None or prior > SNAP_THRESHOLD:
        return None
    return (
        f"snap share {last_two[0]:.0%} -> {last_two[1]:.0%} in wk{hist[-2].week}-"
        f"wk{hist[-1].week}, both above {SNAP_THRESHOLD:.0%} "
        f"(was {prior:.0%} in wk{hist[-3].week})"
    )


def _target_share_step(hist: list[UsageRow]) -> str | None:
    baseline_rows = hist[-(BASELINE_WEEKS + 1) : -1]
    current = hist[-1].target_share
    values = [r.target_share for r in baseline_rows]
    if current is None or any(v is None for v in values):
        return None
    baseline = _mean(values)
    if current < baseline + TARGET_SHARE_STEP:
        return None
    return (
        f"target share {current:.1%} in wk{hist[-1].week} vs {baseline:.1%} "
        f"{BASELINE_WEEKS}wk baseline (+{(current - baseline) * 100:.1f}pp, "
        f"threshold +{TARGET_SHARE_STEP * 100:.0f}pp)"
    )


def _rz_climb_3wk(hist: list[UsageRow]) -> str | None:
    last_three = [r.rz_touches for r in hist[-3:]]
    if any(v is None for v in last_three):
        return None
    if not (last_three[0] < last_three[1] < last_three[2]):
        return None
    return (
        f"red-zone touches {last_three[0]} -> {last_three[1]} -> {last_three[2]} "
        f"across wk{hist[-3].week}-wk{hist[-1].week} (strictly increasing)"
    )


def _route_collapse(hist: list[UsageRow]) -> str | None:
    baseline_rows = hist[-(BASELINE_WEEKS + 1) : -1]
    current = hist[-1].route_share
    values = [r.route_share for r in baseline_rows]
    if current is None or any(v is None for v in values):
        return None
    baseline = _mean(values)
    if baseline <= 0 or current > baseline * ROUTE_COLLAPSE_RATIO:
        return None
    return (
        f"route share {current:.0%} in wk{hist[-1].week} vs {baseline:.0%} "
        f"{BASELINE_WEEKS}wk baseline ({(current / baseline - 1) * 100:.0f}%, "
        f"threshold {(ROUTE_COLLAPSE_RATIO - 1) * 100:.0f}%)"
    )


_STANDARD_FNS = {
    "snap_rise_2wk": _snap_rise_2wk,
    "target_share_step": _target_share_step,
    "rz_climb_3wk": _rz_climb_3wk,
    "route_collapse": _route_collapse,
}

STANDARD_RULES = (
    Rule(
        rule_id="snap_rise_2wk",
        direction=ASCENDING,
        min_weeks=3,  # 2 scored weeks + 1 prior week to verify the crossing
        requires=frozenset({"snap_share"}),
        cold_start=False,
        describe=(
            f"snap share > {SNAP_THRESHOLD:.0%} in the last 2 OBSERVED weeks, newly crossed "
            "vs a third observed week before them "
            "(a missed or incomplete week is skipped, not treated as zero)"
        ),
    ),
    Rule(
        rule_id="target_share_step",
        direction=ASCENDING,
        min_weeks=BASELINE_WEEKS + 1,
        requires=frozenset({"target_share"}),
        cold_start=False,
        describe=f"target share +{TARGET_SHARE_STEP:.0%} vs its own {BASELINE_WEEKS}wk baseline",
    ),
    Rule(
        rule_id="rz_climb_3wk",
        direction=ASCENDING,
        min_weeks=3,
        requires=frozenset({"rz_touches"}),
        cold_start=False,
        describe="red-zone touches strictly increasing over 3 weeks",
    ),
    Rule(
        rule_id="route_collapse",
        direction=FALLING,
        min_weeks=BASELINE_WEEKS + 1,
        requires=frozenset({"route_share"}),
        cold_start=False,
        describe=f"route share <= {ROUTE_COLLAPSE_RATIO:.0%} of its {BASELINE_WEEKS}wk baseline",
    ),
)


def _validate(frames: Sequence[UsageFrame], week: int) -> None:
    if not frames:
        raise ValueError("classify: frames is empty — nothing to classify")
    weeks = [f.week for f in frames]
    if weeks != sorted(weeks) or len(set(weeks)) != len(weeks):
        raise ValueError(
            f"classify: frames must be oldest-to-newest and distinct, got {weeks}"
        )
    if frames[-1].week != week:
        raise ValueError(
            f"classify: newest frame is week {frames[-1].week}, requested week {week}"
        )


def _histories(frames: Sequence[UsageFrame]) -> dict[str, list[UsageRow]]:
    """gsis_id -> rows oldest-to-newest, INCOMPLETE TEAM-WEEKS EXCLUDED.

    A row whose team-week failed the completeness floor carries NULL shares
    (R5); including it would make a rule's window silently shorter than its
    declared min_weeks."""
    out: dict[str, list[UsageRow]] = {}
    for frame in frames:
        for row in frame.rows:
            if row.games_complete == 1:
                out.setdefault(row.gsis_id, []).append(row)
    return out


def classify(frames: Sequence[UsageFrame], week: int) -> TrendResult:
    """Rules over `frames` (oldest -> newest, ending at `week`).

    Weeks 1-3 run the cold-start catalogue INSTEAD of the standard one: a
    standard rule needing a 3-week baseline cannot run in week 2, and an
    empty ASC/FALL section in the highest-value waiver weeks of the season
    is not an acceptable output (R7).
    """
    _validate(frames, week)
    available = frozenset.intersection(*[f.available_metrics for f in frames])
    histories = _histories(frames)

    if week <= COLD_START_MAX_WEEK:
        cs_signals, cs_disabled = evaluate_cold_start(
            histories, available, len(frames), week
        )
        # The disabled_rules contract is "every catalogued rule that did not
        # run is named with a reason" (R27). In weeks 1-3 the ENTIRE standard
        # catalogue is skipped, so it has to announce itself here — otherwise
        # a reader of a week-2 report sees four rules that simply vanished and
        # cannot tell suppression from a silent bug.
        cs_disabled = cs_disabled + [
            (
                rule.rule_id,
                "cold-start mode: standard catalogue inactive through "
                f"week {COLD_START_MAX_WEEK}",
            )
            for rule in STANDARD_RULES
        ]
        return TrendResult(
            season=frames[-1].season,
            week=week,
            signals=tuple(cs_signals),
            disabled_rules=tuple(sorted(cs_disabled)),
        )

    signals: list[TrendSignal] = []
    disabled: list[tuple[str, str]] = []
    # NOTE: this loop is intentionally duplicated in coldstart.py:evaluate_cold_start
    # — a fix here almost certainly applies there; extraction to a shared
    # _engine leaf is a Plan 2 decision (needs ARCHITECTURE §1b row).
    for rule in STANDARD_RULES:
        missing = sorted(rule.requires - available)
        if missing:
            disabled.append(
                (rule.rule_id, f"requires unavailable metric(s): {missing}")
            )
            continue
        if len(frames) < rule.min_weeks:
            disabled.append(
                (
                    rule.rule_id,
                    f"min_weeks={rule.min_weeks} but only {len(frames)} frame(s) supplied",
                )
            )
            continue
        fn = _STANDARD_FNS[rule.rule_id]
        for gsis_id, hist in histories.items():
            # hist[-1].week != week => the player has no complete row THIS week
            # (inactive, or his team-week failed the completeness floor): skip
            # rather than re-fire last week's reading. Twin of the guard in
            # coldstart.py:evaluate_cold_start.
            if len(hist) < rule.min_weeks or hist[-1].week != week:
                continue
            evidence = fn(hist)
            if evidence is None:
                continue
            signals.append(
                TrendSignal(
                    gsis_id=gsis_id,
                    direction=rule.direction,
                    rule_id=rule.rule_id,
                    evidence=evidence,
                    cold_start=False,
                )
            )
    for rule_id, reason in disabled:
        log.warning("usage.rule_disabled", week=week, rule_id=rule_id, reason=reason)
    # One line per rule, not per signal: a 40-flag week would otherwise bury
    # the disablement warnings above it. Per-signal evidence stays on the
    # TrendResult, which is the auditable artefact (ADR Domain 5).
    for rule_id, count in sorted(Counter(s.rule_id for s in signals).items()):
        log.info("usage.signals", week=week, rule_id=rule_id, count=count)
    signals.sort(key=lambda s: (s.gsis_id, s.rule_id))
    return TrendResult(
        season=frames[-1].season,
        week=week,
        signals=tuple(signals),
        disabled_rules=tuple(sorted(disabled)),
    )
