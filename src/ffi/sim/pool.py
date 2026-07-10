"""Draftable-pool builder (Phase 3 / Task 4).

Joins the valuation output (deduped, K+DEF included — Tasks 2-3) against the
crosswalk and the latest season-level Sleeper snapshot to produce a single,
ordered list of PoolPlayer the simulator (Tasks 6-9) drafts from. ADP 999 is
Sleeper's undrafted sentinel and is mapped to None here — anything downstream
that treats a bare 999 as a real draft slot is a bug (R5: silent semantic
drift is the worst case), so the mapping happens once, at the source.

Validation gates fail loud (ValueError) rather than silently emitting a
degraded pool: a partial pool would make every downstream simulation result
look plausible while being wrong.
"""
from dataclasses import dataclass

from ffi.scoring.config import load_config_v1

_REQUIRED_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
_MIN_REAL_ADP = 200
_TOP_N_FOR_QB_SANITY = 30
_MIN_QB_IN_TOP_N = 8
_MIN_DEF = 25
_MIN_K = 25

# ADP comes from the latest season-level (week IS NULL) Sleeper snapshot,
# keyed by player_id — which equals xwalk.sleeper_id for skill/K players AND
# for DEF rows (Task 3 set DEF sleeper_id = team abbreviation, e.g. 'LAR').
_POOL_QUERY = """
WITH latest_snapshot AS (
    SELECT payload
    FROM raw.sleeper_projections
    WHERE week IS NULL
    ORDER BY snapshot_id DESC
    LIMIT 1
),
adp AS (
    SELECT
        rec ->> 'player_id' AS player_id,
        CASE
            WHEN (rec -> 'stats' ->> 'adp_2qb')::float < 999
                THEN (rec -> 'stats' ->> 'adp_2qb')::float
        END AS adp
    FROM latest_snapshot, jsonb_array_elements(payload) AS rec
)
SELECT
    x.sleeper_id AS ref,
    x.name,
    pv.position,
    pv.proj_points,
    pv.vorp,
    pv.tier,
    x.gsis_id,
    adp.adp
FROM valuation.player_value pv
JOIN public.player_id_xwalk x ON x.xwalk_id = pv.xwalk_id
LEFT JOIN adp ON adp.player_id = x.sleeper_id
WHERE pv.config_version = %s AND pv.scenario = %s
"""


@dataclass(frozen=True)
class PoolPlayer:
    ref: str  # sleeper_id (skill/K) or team abbr (DEF) — unique in pool
    name: str
    position: str  # QB RB WR TE K DEF
    proj_points: float
    vorp: float
    tier: int
    adp: float | None  # adp_2qb when < 999, else None (undrafted sentinel)
    gsis_id: str | None  # for backtest/actuals joins


def build_pool(conn, scenario: str) -> list[PoolPlayer]:
    config_version = load_config_v1().version
    with conn.cursor() as cur:
        cur.execute(_POOL_QUERY, (config_version, scenario))
        rows = cur.fetchall()

    players = []
    for ref, name, position, proj_points, vorp, tier, gsis_id, adp in rows:
        # Defensive: valuation.player_value.position is already normalized
        # ('K' not 'PK' — Task 2), but map it here too in case a raw 'PK'
        # ever leaks through from an upstream source.
        position = "K" if position == "PK" else position
        players.append(
            PoolPlayer(
                ref=ref,
                name=name,
                position=position,
                proj_points=float(proj_points),
                vorp=float(vorp),
                tier=int(tier),
                adp=float(adp) if adp is not None else None,
                gsis_id=gsis_id,
            )
        )

    refs = [p.ref for p in players]
    dupes = sorted({r for r in refs if refs.count(r) > 1})
    if dupes:
        raise ValueError(f"pool has duplicate refs (scenario={scenario}): {dupes[:10]}")

    missing_positions = _REQUIRED_POSITIONS - {p.position for p in players}
    if missing_positions:
        raise ValueError(
            f"pool missing positions (scenario={scenario}): {sorted(missing_positions)}"
        )

    real_adp_players = [p for p in players if p.adp is not None]
    if len(real_adp_players) < _MIN_REAL_ADP:
        raise ValueError(
            f"pool has only {len(real_adp_players)} players with real ADP "
            f"(need >= {_MIN_REAL_ADP}, scenario={scenario})"
        )

    real_adp_sorted = sorted(real_adp_players, key=lambda p: p.adp)
    top_n = real_adp_sorted[:_TOP_N_FOR_QB_SANITY]
    qb_in_top_n = sum(1 for p in top_n if p.position == "QB")
    if qb_in_top_n < _MIN_QB_IN_TOP_N:
        raise ValueError(
            f"only {qb_in_top_n} QBs in ADP top {_TOP_N_FOR_QB_SANITY} "
            f"(need >= {_MIN_QB_IN_TOP_N}, 2QB sanity gate, scenario={scenario})"
        )

    def_count = sum(1 for p in players if p.position == "DEF")
    if def_count < _MIN_DEF:
        raise ValueError(
            f"pool has only {def_count} DEF (need >= {_MIN_DEF}, scenario={scenario})"
        )
    k_count = sum(1 for p in players if p.position == "K")
    if k_count < _MIN_K:
        raise ValueError(
            f"pool has only {k_count} K (need >= {_MIN_K}, scenario={scenario})"
        )

    none_adp_players = sorted(
        (p for p in players if p.adp is None), key=lambda p: p.vorp, reverse=True
    )
    return real_adp_sorted + none_adp_players
