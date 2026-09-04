"""Console profile resolution: turn a league name into the knobs the draft
console needs, so `scripts/draft_console.py` stays within its file-size budget.

Leaf-of-sim: imports only `ffi.league_profile` (Layer 0), `ffi.sim.strategy`
(Layer 2), and `ffi.valuation.starts` (Layer 2) — all are valid downstream
edges for `ffi/draft/`.
"""
from __future__ import annotations

from dataclasses import dataclass

from ffi.league_profile import get_profile
from ffi.sim.strategy import DEPLOYED_PARAMS, make_lmu_strategy
from ffi.valuation.starts import CANONICAL_TABLE_PATH

PLAYBOOK = (
    "RB scarce (steep cliff) — draft early. · WR deep & flat — wait, get volume. · "
    "QB deep — get 2 startable (forced R2/R5), QB3 R14+. · TE: 1 starter + 1 backup. · "
    "K/DEF last. · The panel is the deployed A′ engine; take its #1 unless you have a read."
)

LMU_PLAYBOOK = (
    "1-QB: QBs drop hard — first QB ~rank 36, wait until ~R10-12. · 14 teams = thinner "
    "depth: hammer RB/WR early. · Full PPR, no first-down bonus: volume over chain-movers. · "
    "TE: 1 starter + 1 backup. · K/DEF last. · The panel is the deployed LMU engine; take its #1 unless you have a read."
)


@dataclass(frozen=True)
class ConsoleConfig:
    profile: object
    params: object
    playbook: str
    roster_shape: str
    starts_path: object


def resolve(profile_name: str) -> ConsoleConfig:
    profile = get_profile(profile_name)
    lmu = profile_name == "lmu"
    return ConsoleConfig(
        profile=profile,
        params=make_lmu_strategy() if lmu else DEPLOYED_PARAMS,
        playbook=LMU_PLAYBOOK if lmu else PLAYBOOK,
        roster_shape=(
            "1QB/2RB/3WR/1TE/1FLEX/1K/1DEF/6BN/2IR (18 rounds)"
            if lmu
            else "2QB/2RB/3WR/1TE/1FLEX/1K/1DEF/8BN (19 rounds)"
        ),
        starts_path=(
            CANONICAL_TABLE_PATH.parent / "p_starts_lmu.json"
            if lmu
            else CANONICAL_TABLE_PATH
        ),
    )


def resolve_marks(pool, spec: dict | None = None) -> dict:
    """Turn a snapshot {slot, taken:[names], mine:[names]} into ref-based marks.
    `spec=None` -> empty marks (no pre-marking). Fail-loud on an unmatched or
    ambiguous player name (a silently-wrong board is the one failure the console
    exists to prevent)."""
    if not spec:
        return {"slot": 0, "taken": [], "mine": []}
    by_name: dict[str, list] = {}
    for p in pool:
        by_name.setdefault(p.name.lower(), []).append(p.ref)

    def one(name: str) -> str:
        refs = by_name.get(str(name).strip().lower())
        if not refs:
            raise ValueError(f"marks: no board player named {name!r}")
        if len(refs) > 1:
            raise ValueError(f"marks: ambiguous name {name!r} -> {refs}")
        return refs[0]

    return {
        "slot": int(spec.get("slot", 0)),
        "taken": [one(n) for n in spec.get("taken", [])],
        "mine": [one(n) for n in spec.get("mine", [])],
    }
