"""Recency-weighted, annotation-aware opponent draft priors (Phase 3 / Task 5).

Built from the league's 16-season draft history (`draft_picks` joined through
`teams`/`players`/`raw.yahoo_league_settings` — the verified fact #6 join).
Produces, per stable franchise slot (the Yahoo team seat, NOT snake-draft
position) and round, a probability distribution over position taken.

Recency: each historical pick is weighted by `0.5 ** ((latest_season -
season) / HALF_LIFE)` — a 4-season half-life, so a pick from 8 seasons back
counts a quarter as much as one from this season.

Small-sample shrinkage: a slot's round-level share is blended toward its
band-level share (bands: R1-3, R4-8, R9+ — the mining report's bands) with
`SHRINK_M` pseudo-picks of weight, so an early round with few historical
picks for a slot doesn't overfit to one or two data points.

Annotation-aware: `manager_slot_annotations` (league_slot, human_label,
from_season, to_season, note) records when a franchise slot changed hands
between people. A slot's history is truncated at its most recent
`from_season` ONLY when that's a real signal of a human turnover — i.e. the
slot has >= 2 annotation rows (an explicit multi-era history) OR its (only)
`from_season` is after 2010 (the league's first season) — a from_season of
exactly 2010 means "held since the data began," which isn't a cut. Rows
before the floor belong to a different person's tendencies and are dropped
entirely (not down-weighted) before recency weighting and shrinkage are
applied. When the user's pending turnover annotation (handoff pending input
#1) lands in `manager_slot_annotations`, re-running `build_slot_priors`
picks it up automatically — zero code change required, since floors are
recomputed live from the annotation table on every build.

Position vocabulary: verified against the full 3,720-pick / 16-season
history (2026-07-09) to be exactly QB/RB/WR/TE/K/DEF — no stray values
(no NULLs, no 'W/R', no 'D' variants) were found. `build_slot_priors` still
guards against a future ingest introducing junk: positions outside
`POSITIONS` are dropped with a loud disclosure print if they're under 2% of
picks (rare/legitimate junk, per the plan's fail-loud-with-disclosure
convention), else the whole build raises. The pure `_pos_share_from_rows`
has no such tolerance — it raises on any unexpected position, since by the
time rows reach it they're expected to already be clean.
"""
from collections import defaultdict
from dataclasses import dataclass

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
HALF_LIFE = 4.0  # seasons; weight = 0.5 ** ((latest - season) / HALF_LIFE)
SHRINK_M = 8.0  # pseudo-picks of band-share blended into round-level share

# Junk-position tolerance in build_slot_priors: drop-with-disclosure below
# this fraction of total picks, fail loud (refuse to build) at or above it.
_JUNK_POSITION_TOLERANCE = 0.02

_MIN_ROUND, _MAX_ROUND = 1, 19

_HISTORY_QUERY = """
    SELECT t.slot, s.season, dp.round_number, p.position
    FROM draft_picks dp
    JOIN teams t ON t.team_id = dp.team_id
    JOIN players p ON p.player_id = dp.player_id
    JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
    WHERE dp.round_number BETWEEN 1 AND 19
"""

_FLOORS_QUERY = """
    SELECT league_slot, from_season
    FROM public.manager_slot_annotations
"""


@dataclass(frozen=True)
class SlotPriors:
    latest_season: int
    pos_share: dict  # (slot:int, round:int) -> {pos: float}, sums to ~1.0
    params: dict  # provenance: half_life, shrink_m, floors, n_picks_used


def _band(round_number: int) -> str:
    if round_number <= 3:
        return "R1-3"
    if round_number <= 8:
        return "R4-8"
    return "R9+"


def _weight(season: int, latest_season: int) -> float:
    return 0.5 ** ((latest_season - season) / HALF_LIFE)


def _pos_share_from_rows(rows, floors, latest_season) -> dict:
    """Pure. rows = (slot, season, round, position). floors = {slot: cutoff
    season} — rows with season < cutoff for that slot are dropped entirely
    (a real human-turnover cut, not down-weighting). Returns
    (slot, round) -> {pos: share}, one entry per round 1-19 for every slot
    that appears in the (post-floor) rows, each summing to ~1.0."""
    bad = sorted({pos for (_, _, _, pos) in rows if pos not in POSITIONS})
    if bad:
        raise ValueError(f"unexpected position(s) in draft history rows: {bad}")

    filtered = [
        (slot, season, rnd, pos)
        for (slot, season, rnd, pos) in rows
        if season >= floors.get(slot, -(10**9))
    ]

    slots = sorted({slot for slot, _, _, _ in filtered})

    band_pos_w: dict = defaultdict(lambda: defaultdict(float))
    band_total_w: dict = defaultdict(float)
    round_pos_w: dict = defaultdict(lambda: defaultdict(float))
    round_total_w: dict = defaultdict(float)

    for slot, season, rnd, pos in filtered:
        w = _weight(season, latest_season)
        band = _band(rnd)
        band_pos_w[(slot, band)][pos] += w
        band_total_w[(slot, band)] += w
        round_pos_w[(slot, rnd)][pos] += w
        round_total_w[(slot, rnd)] += w

    def band_share(slot, band):
        total = band_total_w.get((slot, band), 0.0)
        if total <= 0:
            # No data at all for this slot/band (edge case — real 16-season
            # history always has data for every slot's every band). Fall
            # back to a flat prior rather than dividing by zero / leaving
            # the (slot, round) entry summing to 0.
            return {pos: 1.0 / len(POSITIONS) for pos in POSITIONS}
        weighted = band_pos_w.get((slot, band), {})
        return {pos: weighted.get(pos, 0.0) / total for pos in POSITIONS}

    result = {}
    for slot in slots:
        for rnd in range(_MIN_ROUND, _MAX_ROUND + 1):
            bshare = band_share(slot, _band(rnd))
            total = round_total_w.get((slot, rnd), 0.0)
            weighted = round_pos_w.get((slot, rnd), {})
            result[(slot, rnd)] = {
                pos: (weighted.get(pos, 0.0) + SHRINK_M * bshare[pos])
                / (total + SHRINK_M)
                for pos in POSITIONS
            }
    return result


def _compute_floors(conn) -> dict:
    """{league_slot: cutoff_season} for slots where the annotation history
    represents a real human-turnover cut: >= 2 annotation rows for the slot,
    OR a single row whose from_season is after 2010 (the league's first
    season — from_season == 2010 means 'held since the data began', not a
    cut)."""
    with conn.cursor() as cur:
        cur.execute(_FLOORS_QUERY)
        rows = cur.fetchall()

    by_slot: dict = defaultdict(list)
    for slot, from_season in rows:
        by_slot[slot].append(from_season)

    floors = {}
    for slot, seasons in by_slot.items():
        latest_from = max(seasons)
        if len(seasons) >= 2 or latest_from > 2010:
            floors[slot] = latest_from
    return floors


def build_slot_priors(conn) -> SlotPriors:
    floors = _compute_floors(conn)
    with conn.cursor() as cur:
        cur.execute(_HISTORY_QUERY)
        raw_rows = cur.fetchall()
    if not raw_rows:
        raise ValueError("build_slot_priors: draft history query returned zero rows")

    bad_counts: dict = defaultdict(int)
    good_rows = []
    for slot, season, rnd, pos in raw_rows:
        if pos in POSITIONS:
            good_rows.append((slot, season, rnd, pos))
        else:
            bad_counts[pos] += 1

    if bad_counts:
        total_bad = sum(bad_counts.values())
        bad_frac = total_bad / len(raw_rows)
        if bad_frac >= _JUNK_POSITION_TOLERANCE:
            raise ValueError(
                f"build_slot_priors: unexpected position(s) {dict(bad_counts)} are "
                f"{bad_frac:.1%} of {len(raw_rows)} picks — at/above the "
                f"{_JUNK_POSITION_TOLERANCE:.0%} junk tolerance, refusing to "
                "silently drop; investigate the ingest before rebuilding priors"
            )
        print(
            f"build_slot_priors: dropping {total_bad}/{len(raw_rows)} picks "
            f"({bad_frac:.2%}) with unexpected position(s) {dict(bad_counts)} — "
            "below the 2% junk tolerance, treated as legitimate rare junk "
            "(fail-loud-with-disclosure)."
        )

    latest_season = max(season for _, season, _, _ in good_rows)
    pos_share = _pos_share_from_rows(good_rows, floors, latest_season)

    # Assert full 12x19 slot-round coverage for Task 6 sim.
    missing_keys = []
    for slot in range(1, 13):
        for rnd in range(_MIN_ROUND, _MAX_ROUND + 1):
            key = (slot, rnd)
            if key not in pos_share or not pos_share[key]:
                missing_keys.append(key)

    if missing_keys:
        missing_slots = sorted({slot for slot, _ in missing_keys})
        raise ValueError(
            f"build_slot_priors: incomplete slot-round coverage; "
            f"missing keys for slot(s) {missing_slots}. "
            "Likely cause: a slot's entire draft history was excluded by an annotation floor. "
            f"Missing: {missing_keys}"
        )

    params = {
        "half_life": HALF_LIFE,
        "shrink_m": SHRINK_M,
        "floors": dict(floors),
        "n_picks_used": len(good_rows),
    }
    return SlotPriors(latest_season=latest_season, pos_share=pos_share, params=params)
