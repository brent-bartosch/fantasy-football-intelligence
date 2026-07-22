"""Tests for scripts/draft_console.py -- the Python side of the live-draft
console's drift guard (the JS side is covered by the in-browser self-test, which
this build verifies headlessly via node against the same golden trace).

The core contract: the embedded GOLDEN trace is a pure function of (baked POOL,
REMOVALS) -- so the browser can reproduce it exactly. `test_golden_replayable`
mirrors the JS drift-guard replay in Python. Uses the DB-free synthetic
pool/priors fixtures (no live valuation needed).
"""
from collections import Counter

from simfixtures import synthetic_pool, synthetic_priors

import draft_console
from ffi.sim.draft import ROUNDS, _avail_view, _build_sorted_pool


def _generate():
    pool = synthetic_pool()
    priors = synthetic_priors()
    removals, golden = draft_console.generate_golden(pool, priors)
    return pool, removals, golden


def test_golden_trace_has_19_self_consistent_picks():
    pool, removals, golden = _generate()
    assert len(golden) == 19  # our 19 rounds
    ids = {p.ref for p in pool}
    for g in golden:
        assert g["recommended"]["id"] in ids
        assert g["signature"]  # non-empty fingerprint
        assert 1 <= len(g["top5"]) <= 5
        assert g["signature"].startswith(g["recommended"]["id"] + "|")
    assert sum(1 for r in removals if r["mine"]) == 19  # exactly our picks flagged


def test_golden_trace_is_deterministic():
    pool, priors = synthetic_pool(), synthetic_priors()
    _, g1 = draft_console.generate_golden(pool, priors)
    _, g2 = draft_console.generate_golden(pool, priors)
    assert [g["signature"] for g in g1] == [g["signature"] for g in g2]


def test_golden_replayable_like_the_browser_drift_guard():
    """Recompute each of our picks' signature from (POOL, REMOVALS) alone --
    the exact contract the JS engine must satisfy on page load. Any drift
    between the recorded golden and a fresh engine replay fails here (Python)
    and would raise the RED banner in the browser."""
    pool, removals, golden = _generate()
    sorted_pool = _build_sorted_pool(pool)
    taken, counts, k = set(), {}, 0
    for rem in removals:
        if rem["mine"]:
            round_ = k + 1
            picks_left_after = ROUNDS - round_
            sug = draft_console.console_suggestions(
                _avail_view(sorted_pool, taken), round_, counts, picks_left_after
            )
            assert sug["signature"] == golden[k]["signature"]
            pos = golden[k]["recommended"]["pos"]
            counts[pos] = counts.get(pos, 0) + 1
            k += 1
        taken.add(rem["id"])
    assert k == 19


def test_golden_shape_respects_deployed_caps():
    _, _, golden = _generate()
    c = Counter(g["recommended"]["pos"] for g in golden)
    assert c["QB"] <= 3  # no QB4 (qb_by_round length cap + no force beyond 3)
    assert c["TE"] <= 2  # single-start insurance cap
    assert c["DEF"] <= 1 and c["K"] <= 1


def test_signature_is_stable_for_same_state():
    pool = synthetic_pool()
    sorted_pool = _build_sorted_pool(pool)
    avail = _avail_view(sorted_pool, set())
    a = draft_console.console_suggestions(avail, 1, {}, ROUNDS - 1)
    b = draft_console.console_suggestions(avail, 1, {}, ROUNDS - 1)
    assert a["signature"] == b["signature"]
    assert a["recommended"]["id"] == b["recommended"]["id"]
