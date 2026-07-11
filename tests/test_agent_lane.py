"""Tests for the advisory agent lane's pure context builder (Phase 4 / Task 16
-- EXPENDABLE per risk R3).

Only `build_annotation_context` is under test here. Everything else in
`scripts/draft_agent_lane.py` (the log tail, the `claude -p` subprocess call,
the atomic annotation write) is I/O/process-boundary code exercised by manual
`--dry-run` smoke only (the brief's Step 3) -- the lane is a separate,
disposable OS process that never touches the pick path, so its I/O plumbing
isn't unit-tested the way `DraftSession`'s is.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from simfixtures import synthetic_pool, synthetic_priors  # noqa: E402

from draft_agent_lane import build_annotation_context  # noqa: E402


def _synthetic_events():
    """A 3-pick synthetic log: overall 1 is OUR pick (draft position 1,
    franchise slot 1, a QB); overalls 2-3 are two other seats. Our next turn
    (position 1 again) doesn't come back around until round 2 (overall 24),
    so the on-clock window is non-trivial."""
    return [
        (
            "meta",
            {
                "league_key": "461.l.1",
                "our_franchise_slot": 1,
                "our_position": 1,
                "scenario": "qb_hoard_12",
                "board_vintage": None,
            },
        ),
        ("pick", {"overall": 1, "pos": "QB", "ref": "QB0", "franchise_slot": 1}),
        ("pick", {"overall": 2, "pos": "RB", "ref": "RB0", "franchise_slot": None}),
        ("pick", {"overall": 3, "pos": "WR", "ref": "WR0", "franchise_slot": None}),
    ]


def test_build_annotation_context_has_roster_on_clock_window_and_board():
    context = build_annotation_context(
        _synthetic_events(), synthetic_pool(), synthetic_priors()
    )

    # our roster: one QB drafted so far
    assert "## Our roster" in context
    assert "QB:1" in context

    # on-clock window: current overall (4) and our next turn (24, round 2)
    assert "## On the clock" in context
    assert "overall 4" in context
    assert "overall 24" in context

    # top-board lines, via the same recommend() the live assistant uses
    assert "## Top board" in context
    board_start = context.splitlines().index("## Top board")
    board_lines = context.splitlines()[board_start + 1 :]
    assert any(ln.strip() for ln in board_lines)


def test_build_annotation_context_is_pure_and_deterministic():
    events = _synthetic_events()
    pool = synthetic_pool()
    priors = synthetic_priors()

    first = build_annotation_context(events, pool, priors)
    second = build_annotation_context(events, pool, priors)

    assert first == second
