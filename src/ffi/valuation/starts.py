"""Starts-based replacement + P(starts) weights (Phase B deploy, 2026-07-21).

The deployed valuation (design doc 2026-07-21-starts-weighted-valuation-v2) uses
a STARTS-based replacement level instead of the roster-based one:

    replacement_rank[pos] = round(12 x sum_slot P_start[pos][slot])

i.e. the last league-wide rank that still meaningfully STARTS (12 teams) --
QB24 / RB36 / WR36 / TE12 -- from the canonical byes+injuries P(starts) table
(`data/p_starts.json`, seed 7). The deployed pick engine (A', wired into
`ffi.sim.strategy`) scores rule-4 candidates `P_start[pos][k+1] x vorp`, where
`vorp = proj - points_at_rank(pos, replacement_rank)` already carries the
starts-based baseline (the design's thesis: "the fix is the BASELINE").

Fail-loud: the loader REFUSES a table whose `_meta.mode` isn't `byes+injuries`
(a byes-only table has QB3=.122 not .259 and would silently mis-weight every
deployed pick). Live code depends on the TRACKED `data/p_starts.json`, never on
a gitignored `reports/` artifact.
"""
from __future__ import annotations

import json
import pathlib

# QB/RB/WR/TE get starts-based replacement; K/DEF keep the roster-based rank
# (defk_round owns their timing -- design doc replacement table).
STARTS_POSITIONS = ("QB", "RB", "WR", "TE")

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
CANONICAL_TABLE_PATH = REPO_ROOT / "data" / "p_starts.json"

_REQUIRED_MODE = "byes+injuries"


def load_starts_table(path: pathlib.Path | str | None = None) -> dict:
    """Load + validate the canonical P_start[pos][slot] table. Returns
    {pos: {slot(int): prob(float)}, "_meta": {...}}. Fails loud on a missing
    file, a missing `_meta`, or a mode other than `byes+injuries`."""
    p = pathlib.Path(path) if path is not None else CANONICAL_TABLE_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"P(starts) table not found at {p} -- regenerate with "
            "`uv run python scripts/estimate_p_starts.py --out data/p_starts.json`"
        )
    raw = json.loads(p.read_text())
    meta = raw.get("_meta")
    if meta is None:
        raise ValueError(f"{p} has no `_meta` block -- regenerate (refusing to guess)")
    if meta.get("mode") != _REQUIRED_MODE:
        raise ValueError(
            f"{p} was generated in mode {meta.get('mode')!r}, not {_REQUIRED_MODE!r}"
            " -- the stale-file footgun; rerun estimate_p_starts.py (no --no-injuries)"
        )
    table: dict = {"_meta": meta}
    for pos, row in raw.items():
        if pos == "_meta":
            continue
        table[pos] = {int(slot): float(v) for slot, v in row.items()}
    return table


def starts_replacement_ranks(table: dict) -> dict:
    """{pos: round(12 x sum_slot P_start[pos][slot])} for QB/RB/WR/TE
    (QB24/RB36/WR36/TE12). Only the starts-based positions are returned."""
    return {
        pos: int(round(12 * sum(table[pos].values())))
        for pos in STARTS_POSITIONS
        if pos in table
    }


def pstart_weight(table: dict, pos: str, slot: int) -> float:
    """P_start for the `slot`-th (1-indexed) player at `pos`; 0.0 beyond the
    table's depth (a player past estimated depth never starts -> not drafted)."""
    return table.get(pos, {}).get(slot, 0.0)


def pstart_weight_tuples(table: dict) -> tuple:
    """The table as a hashable tuple `((pos, (w1, w2, ...)), ...)` for embedding
    in the frozen `StrategyParams` (A' deploy). Slots are dense 1..maxslot."""
    out = []
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        row = table.get(pos)
        if not row:
            continue
        maxslot = max(row)
        out.append((pos, tuple(row.get(s, 0.0) for s in range(1, maxslot + 1))))
    return tuple(out)
