"""Weeks 1-3 rule variants (R7).

Every standard rule needs a 2-4 week window, so in weeks 1-3 the standard
catalogue is structurally silent — during the weeks league-winners are
disproportionately claimed. These variants run 1-2 week windows and every
signal they emit is labelled `cold_start=True` so the report can say so out
loud rather than passing a one-week reading off as a trend.

Imports only ffi.usage (value types); ffi.usage.trends imports THIS module,
never the reverse.
"""

from collections import Counter
from collections.abc import Mapping

import structlog

from ffi.usage import ASCENDING, FALLING, WATCH, Rule, TrendSignal, UsageRow

log = structlog.get_logger()

COLD_START_MAX_WEEK = 3

CS_SNAP_THRESHOLD = 0.55
CS_TARGET_SHARE_STEP = 0.05
CS_ROUTE_COLLAPSE_RATIO = 0.60


def _snap_rise_1wk(hist: list[UsageRow]) -> str | None:
    """A single week above threshold. WATCH, not ASCENDING: one game is a
    reading, not a trend, and mislabelling it would corrupt the Plan 2
    precision measurement."""
    current = hist[-1].snap_share
    if current is None or current <= CS_SNAP_THRESHOLD:
        return None
    return (
        f"COLD-START: snap share {current:.0%} in wk{hist[-1].week} "
        f"(> {CS_SNAP_THRESHOLD:.0%}), 1-week window"
    )


def _target_share_step_cs(hist: list[UsageRow]) -> str | None:
    prev, current = hist[-2].target_share, hist[-1].target_share
    if prev is None or current is None or current < prev + CS_TARGET_SHARE_STEP:
        return None
    return (
        f"COLD-START: target share {prev:.1%} (wk{hist[-2].week}) -> {current:.1%} "
        f"(wk{hist[-1].week}), +{(current - prev) * 100:.1f}pp on a 2-week window"
    )


def _route_collapse_cs(hist: list[UsageRow]) -> str | None:
    prev, current = hist[-2].route_share, hist[-1].route_share
    if prev is None or current is None or prev <= 0:
        return None
    if current > prev * CS_ROUTE_COLLAPSE_RATIO:
        return None
    return (
        f"COLD-START: route share {prev:.0%} (wk{hist[-2].week}) -> {current:.0%} "
        f"(wk{hist[-1].week}), {(current / prev - 1) * 100:.0f}% on a 2-week window"
    )


_COLD_START_FNS = {
    "snap_rise_1wk_cs": _snap_rise_1wk,
    "target_share_step_cs": _target_share_step_cs,
    "route_collapse_cs": _route_collapse_cs,
}

COLD_START_RULES = (
    Rule(
        rule_id="snap_rise_1wk_cs",
        direction=WATCH,
        min_weeks=1,
        requires=frozenset({"snap_share"}),
        cold_start=True,
        describe=f"snap share > {CS_SNAP_THRESHOLD:.0%} in a single observed week",
    ),
    Rule(
        rule_id="target_share_step_cs",
        direction=ASCENDING,
        min_weeks=2,
        requires=frozenset({"target_share"}),
        cold_start=True,
        describe=f"target share +{CS_TARGET_SHARE_STEP:.0%} week over week",
    ),
    Rule(
        rule_id="route_collapse_cs",
        direction=FALLING,
        min_weeks=2,
        requires=frozenset({"route_share"}),
        cold_start=True,
        describe=f"route share <= {CS_ROUTE_COLLAPSE_RATIO:.0%} of the prior week",
    ),
)


def evaluate_cold_start(
    histories: Mapping[str, list[UsageRow]],
    available: frozenset[str],
    n_frames: int,
    week: int,
) -> tuple[list[TrendSignal], list[tuple[str, str]]]:
    """(signals, disabled_rules) for the weeks 1-3 catalogue.

    `week` is the week being classified: a history whose newest complete row
    is older than `week` (the player was inactive, or his team-week failed the
    completeness floor) is skipped rather than re-fired on stale readings.
    """
    signals: list[TrendSignal] = []
    disabled: list[tuple[str, str]] = []
    # NOTE: this loop is intentionally duplicated in trends.py:classify — a fix
    # here almost certainly applies there; extraction to a shared _engine leaf
    # is a Plan 2 decision (needs ARCHITECTURE §1b row).
    for rule in COLD_START_RULES:
        missing = sorted(rule.requires - available)
        if missing:
            disabled.append(
                (rule.rule_id, f"requires unavailable metric(s): {missing}")
            )
            continue
        if n_frames < rule.min_weeks:
            disabled.append(
                (
                    rule.rule_id,
                    f"min_weeks={rule.min_weeks} but only {n_frames} frame(s) supplied",
                )
            )
            continue
        fn = _COLD_START_FNS[rule.rule_id]
        for gsis_id, hist in histories.items():
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
                    cold_start=True,
                )
            )
    for rule_id, reason in disabled:
        log.warning(
            "usage.cold_start_rule_disabled", week=week, rule_id=rule_id, reason=reason
        )
    # One line per rule, not per signal — see trends.py:classify for why.
    for rule_id, count in sorted(Counter(s.rule_id for s in signals).items()):
        log.info("usage.cold_start_signals", week=week, rule_id=rule_id, count=count)
    signals.sort(key=lambda s: (s.gsis_id, s.rule_id))
    return signals, sorted(disabled)
