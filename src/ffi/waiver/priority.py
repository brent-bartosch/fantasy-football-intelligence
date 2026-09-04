"""Rolling-priority pricing as a finite-horizon expiring option (ADR R18).

A waiver claim in a rolling-priority league is an option that EXPIRES (someone
else claims the player) and that costs your priority position when exercised.
The stopping rule prices that: with a good priority position and weeks still
left, you can afford to WAIT for a bigger score; with a bad position or a short
horizon, you CLAIM on any real value.

These are STARTING-POINT hurdles, not tuned values (same posture as
ffi.usage.trends): tune on 2024, gate on 2025, never on the gate season.
"""
from __future__ import annotations

from ffi.waiver import Claim, Decision

# The hurdle a net value must clear to be worth spending priority. It slides
# from HURDLE_CEILING (first in line — selective) to HURDLE_FLOOR (last in line
# — act), because a #1 priority is expensive to spend and a #14 is cheap.
HURDLE_CEILING = 0.80
HURDLE_FLOOR = 0.40
# A long horizon raises the hurdle slightly: more weeks = more chances to find
# a better claim, so waiting is cheaper.
HORIZON_HURDLE_BOOST = 0.05
HORIZON_CAP_WEEKS = 10
# priority_pos is assumed to be 1..MAX_PRIORITY for the slide. Leagues here are
# 12 or 14 teams; the slide saturates beyond this bound rather than going
# negative.
MAX_PRIORITY = 20


def price(claim: Claim, priority_pos: int, weeks_remaining: int) -> Decision:
    """claim / wait / skip for one candidate claim.

    `priority_pos` is 1-indexed (1 = first in line, the best position).
    `weeks_remaining` is the horizon (regular-season weeks left).
    """
    if priority_pos < 1:
        raise ValueError(f"priority_pos must be >= 1 (1 = first), got {priority_pos}")
    net = claim.value - claim.drop_cost
    if net <= 0:
        return Decision(
            "skip",
            f"net value {net:.2f} is not positive (value {claim.value:.2f} - "
            f"drop cost {claim.drop_cost:.2f})",
        )
    if weeks_remaining <= 0:
        return Decision("claim", "no weeks remaining — the option expires now")
    # Good position (small pos) -> high hurdle (selective); bad -> low (act).
    span = HURDLE_CEILING - HURDLE_FLOOR
    pos = min(max(priority_pos, 1), MAX_PRIORITY)
    hurdle = HURDLE_CEILING - span * (pos - 1) / (MAX_PRIORITY - 1)
    hurdle += HORIZON_HURDLE_BOOST * min(weeks_remaining, HORIZON_CAP_WEEKS) / HORIZON_CAP_WEEKS
    if net >= hurdle:
        return Decision(
            "claim",
            f"net value {net:.2f} >= hurdle {hurdle:.2f} "
            f"(priority #{priority_pos}, {weeks_remaining}wk left)",
        )
    return Decision(
        "wait",
        f"net value {net:.2f} < hurdle {hurdle:.2f} — hold priority "
        f"(#{priority_pos}) for a bigger score with {weeks_remaining}wk left",
    )
