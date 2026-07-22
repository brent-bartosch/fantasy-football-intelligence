"""2021-25 backtest harness + ADR D7 regression gate (Phase 3 / Task 11).

Turns the simulator stack (Tasks 4-9) into the non-circular validator: draft
each backtest season with THAT year's archived preseason board, score the
resulting rosters with ACTUAL nflverse weekly league points (not another
projection), and reduce the twelve (4 strategy x 3 season) reference cells to
one composite all-play% -- the number every later strategy/valuation change
is gated against (`run_backtests.py --gate`).

FIXED DECISIONS (binding, carried from task-11-brief.md / Task 10's sourcing
doc, docs/research/2026-07-10-backtest-archive-sourcing.md):

1. Archived `fpts` is NEVER used as `proj_points` -- it mixes standard and
   PPR scoring across pages (verified, Task 10). Real projections are always
   rebuilt from the stored stat lines through the *same* engine path used
   for every other projection source: `ffi.scoring.engine.score_components`
   plus `ffi.scoring.projection_bonus.season_bonus_ev` for the weekly
   threshold-bonus EV, with `gsis_id=None` passed to `season_bonus_ev` so it
   always falls back to POSITION-level CVs (`_ARCHIVE_STAT_MAP` below) --
   archive players may have thin or no nflverse weekly history to fit a
   per-player CV against. `statline_from_archive` maps archive stat-field
   names to `StatLine` fields EXPLICITLY and raises on anything unrecognized
   (`_ARCHIVE_STAT_MAP` / `_ARCHIVE_IGNORED` are the only vocabulary).

2. Per-position real-projection coverage is derived from the payload's own
   position census (`_real_projection_positions`), never from the doc or
   row counts alone -- verified live: 2023 has real stat lines for all of
   QB/RB/WR/TE (+K, but see point 5); 2024 QB only; 2025 QB/RB/WR/TE (no K).

3. Degraded fallback (a season x position lacking real stat lines): synthetic
   `proj_points` = the CURRENT (2026) `qb_hoard_12` pool's points-at-rank
   curve for that position (`build_synthetic_curve`), applied to that
   season's real ECR rank order (`synthetic_proj_points`, clamped/repeated
   past the curve's own length). Every such row is `degraded=True`.

4. DEF is neutralized in every backtest season: nflverse carries no DEF
   stat lines at all, so there is no archived DEF signal of any kind (ADP,
   ECR, or projections). Rather than inventing one, every season's DEF pool
   is 32 IDENTICAL dummy rows (`proj_points=vorp=0`, `tier=1`, `adp=None`,
   `degraded=True`) -- enough for all 12 teams to legally fill DEF, and,
   because they are all-zero, every team scores 0 for that slot every week.
   A uniform 0 for every team is equivalent to a uniform nonzero constant
   for ALL-PLAY RANKING purposes (all-play compares teams to each other, and
   adding the same constant to every team's DEF slot every week doesn't
   change who out-scores whom) -- so this is a documented simplification,
   not a bug, and `defk_round` is fixed at 18 for every reference strategy so
   DEF/K timing itself isn't adjudicated by these backtests (the sim farm +
   Phase 2 streaming baselines own that question).

5. K is ALSO always degraded, in every season including 2023 -- a discovery
   made during Task 11 that the brief's carry-forward facts did not
   anticipate (documented here per the brief's "stop and report" guidance,
   rather than a silent workaround): FantasyPros' preseason K projection
   page only ever exposes AGGREGATE `FG`/`FGA`/`XPT` (made/attempted field
   goals and made extra points), never the distance-bucketed breakdown
   (`fg_0_19`...`fg_50_plus`, `fg_miss_0_19`...`fg_miss_30_39`) our v1
   scoring config prices differently per bucket (3/3/3/4/5 for makes,
   -3/-2/-1 for misses). There is no lossless way to route 2023's real K
   stat lines through `statline_from_archive` without guessing a distance
   split, so `statline_from_archive` never receives K fields at all (if it
   ever did, they'd raise as unknown fields -- a deliberate safety net, see
   `_ARCHIVE_STAT_MAP`). Instead, EVERY backtest season's K pool is the
   CURRENT (2026) `qb_hoard_12` pool's kicker roster verbatim (identity,
   `proj_points`, `vorp`, `tier` all copied straight from `build_pool`'s
   output), `degraded=True`. This is the same "near-noise" simplification
   the Task 10 sourcing doc already anticipated for K generally
   ("K is near-noise in draft value anyway").

6. Opponents draft from `build_slot_priors`, which recency-weights over ALL
   16 seasons of history rather than being rebuilt per backtest year --
   an accepted, documented simplification (same one Task 5 already made).

7. Actuals: `points_lookup` keyed `(gsis_id, week)` from
   `scoring.player_week_points WHERE source='nflverse' AND season=%s AND
   week BETWEEN 1 AND 14`; missing = 0.0 (bye/inactive/injury is real
   signal, per `ffi.sim.season.evaluate_league`'s own contract).

8. VORP/tiers: computed ONLY for QB/RB/WR/TE via the `qb_hoard_12` scenario
   shape (`compute_replacement_ranks`/`compute_baselines`, ranks filtered to
   the positions actually present -- same pattern `build_valuation.py`
   uses) and `gmm_tiers` (falling back to all-tier-1 below 4 players). K and
   DEF are handled entirely outside this path (points 4-5 above).

9. Name matching is a THREE-TIER cascade, strictly more robust than the
   brief's literal "name+position vs xwalk" framing (another discovery worth
   recording): every archived row (both `ecr` and `projections` kinds)
   carries FantasyPros' own numeric `fp_id`, and `public.player_id_xwalk`
   already carries `fantasypros_id` for ~4.8k players (verified live:
   100% of the top-150 ECR names resolve via `fp_id` alone, every season).
   So `match_row` tries, in order: (a) `fp_id -> xwalk.fantasypros_id`
   (exact, position-agnostic); (b) `lower(name)+position -> xwalk` (K
   aliased to xwalk's `PK`), normalized (suffixes Jr/Sr/II/III/IV/V
   stripped, punctuation stripped) -- the fallback the brief's fact #5
   describes; (c) `data/backtest_name_overrides.json` (keyed
   `"{normalized_name}|{position}"` -> gsis_id). A row that resolves to a
   xwalk match with a NULL `gsis_id` is treated as unmatched -- gsis is the
   only thing this pipeline needs a match for. The match-rate gate
   (`enforce_match_gate`) checks the top 150 ECR-ranked names (rank is
   overall, not per-position -- the source page is a single overall
   superflex cheatsheet) resolve at >= 85%, else `SystemExit` naming the
   misses (R6 discipline). Unmatched players anywhere in the board (not just
   the top 150) are simply excluded from that season's pool -- the same
   real-world effect as them going undrafted.

10. NOT implemented: first-down imputation (`ffi.scoring.fd_impute`) for
    archive stat lines. Sleeper projections get FD imputed because the
    Sleeper adapter needs it for every projected player; the archive stat
    lines simply leave `rush_first_downs`/`rec_first_downs` as `None`
    (StatLine's "source doesn't carry this stat" semantic) -- a modest,
    roughly position-uniform undercount that doesn't meaningfully change
    *relative* VORP ordering within a position (first downs scale with
    rush/rec volume, already captured). Out of scope for this task; flagged
    here rather than silently applied.

11. NOT implemented: season-specific weekly-CV fitting for the bonus EV.
    `estimate_weekly_cv` is fit once over all available seasons (2019-2025)
    and reused for all three backtest seasons -- a small look-ahead (using
    2024/2025 variance to price a 2023 bonus) in the same spirit as point 6
    (slot priors over all 16 seasons): it's a variance estimate, not a point
    projection, and thin per-season samples would make single-season CVs
    noisier, not more honest.

Reference cells (ADR D7 gate): `REF_STRATEGIES` (4) x `GATE_SEASONS` (3, the
frozen 2023-25 baseline -- NOT the extended `BACKTEST_SEASONS`) x 100 seeded
drafts each = 12 cells / 1200 drafts. Composite = mean of the 12 cells' own
(100-draft) mean all-play%; band = 2 x SE across the 12 CELL MEANS (not the
pooled 1200 draws) -- `composite_and_band`.

BACKTEST EXTENSION (2026-07-21, starts-weighted valuation v2 spec): the
harness now builds pools for FIVE seasons -- 2021, 2022, 2023, 2024, 2025 --
so the strategy tournament can score over a 5-season composite. 2021/2022
preseason superflex ECR comes from the same dynastyprocess `db_fpecr.parquet`
archive (August snapshot nearest the draft window); FantasyPros preseason
projection stat lines are NOT available that far back (Wayback MISS, expected
and probed), so 2021/2022 run the DESIGNED synthetic-curve fallback (point 3)
on real ECR order at every REAL_POS -- `degraded=True` on every RB/WR/TE row,
and QB too unless a real QB projection snapshot was recovered. The D7 gate
stays on GATE_SEASONS; 2021/2022 never enter it.
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal

from ffi.scoring.config import ScoringConfig
from ffi.scoring.engine import score_components
from ffi.scoring.projection_bonus import season_bonus_ev
from ffi.scoring.statline import StatLine
from ffi.sim.draft import run_draft, snake_position
from ffi.sim.pool import PoolPlayer
from ffi.sim.priors import SlotPriors, build_slot_priors
from ffi.sim.season import evaluate_league
from ffi.sim.strategy import StrategyParams, make_strategy_fn
from ffi.valuation.baseline import compute_baselines, compute_replacement_ranks
from ffi.valuation.starts import load_starts_table, starts_replacement_ranks
from ffi.valuation.tiers import gmm_tiers

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
OVERRIDES_PATH = REPO_ROOT / "data" / "backtest_name_overrides.json"

BACKTEST_SEASONS = (2021, 2022, 2023, 2024, 2025)

# The ADR D7 regression gate is a FROZEN baseline: it is defined over the
# original three reference seasons ONLY (2023-25), never the extended set.
# `run_all_cells` (the shared computation behind `run_backtests.py`'s
# --reference/--gate) iterates GATE_SEASONS, so extending BACKTEST_SEASONS to
# 2021-2025 for the 5-season tournament does NOT move the gate. Do not fold
# 2021/2022 into this tuple -- those seasons are fully synthetic at RB/WR/TE
# (degraded curves on real ECR order) and were never part of the reference
# the deploy decision is gated against.
GATE_SEASONS = (2023, 2024, 2025)

# Positions ever eligible for real archive stat-line scoring. K and DEF are
# deliberately excluded -- see module docstring points 4-5.
REAL_POS = ("QB", "RB", "WR", "TE")

# A season x position needs at least this many real stat-line rows in the
# wayback_fp payload to count as "covered" (position census, fact #2).
MIN_REAL_PROJ_ROWS = 30

# hoard_12 scenario shape (matches scripts/build_valuation.py's SCENARIOS["qb_hoard_12"]).
VORP_SCENARIO = {"teams": 12, "qb_extra_rostered": 12}

# Pool-adequacy floor: enough of each position for 12 teams to fill legal
# rosters with slack (module docstring / brief carry-forward fact #10).
MIN_POOL_COUNTS = {"QB": 30, "RB": 40, "WR": 50, "TE": 20, "K": 15}
MIN_DEF_ROWS = 12  # 1 starter x 12 teams, minimum
N_DUMMY_DEF = 32

TOP_N_MATCH_GATE = 150
MATCH_GATE_THRESHOLD = 0.85

# 4 reference strategies x 3 seasons x 100 seeded drafts (task-11-brief.md).
REF_STRATEGIES = (
    StrategyParams(qb_by_round=(1, 4, 9), defk_round=18),
    StrategyParams(qb_by_round=(2, 5, 9), defk_round=18),
    StrategyParams(qb_by_round=(3, 6, 10), defk_round=18),
    StrategyParams(qb_by_round=(2, 4, 6), defk_round=18),
)
N_DRAFTS_PER_CELL = 100
OUR_FRANCHISE_SLOT = 12


# ---------------------------------------------------------------------------
# Name matching
# ---------------------------------------------------------------------------

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
_POS_ALIAS = {"K": "PK"}  # archive 'K' -> xwalk 'PK', name-match fallback only


def normalize_name(name: str) -> str:
    """lower + strip punctuation + drop generational suffixes (Jr/Sr/II-V).
    Applied identically to archive names, xwalk names, and override keys so
    all three vocabularies compare equal."""
    n = name.lower().strip()
    for ch in (".", "'", "-"):
        n = n.replace(ch, " " if ch == "-" else "")
    parts = [p for p in n.split() if p not in _SUFFIXES]
    return " ".join(parts)


def _override_key(name: str, position: str) -> str:
    return f"{normalize_name(name)}|{position}"


def load_overrides() -> dict:
    if not OVERRIDES_PATH.exists():
        return {}
    return json.loads(OVERRIDES_PATH.read_text())


@dataclass(frozen=True)
class MatchResult:
    gsis_id: str | None
    method: str  # 'fp_id' | 'name' | 'override' | 'unmatched'


def load_xwalk_lookup(conn) -> tuple[dict, dict]:
    """(fp_id -> gsis_id, (normalized_name, position) -> gsis_id). Only rows
    with a non-NULL gsis_id are indexed (a match without gsis_id can't feed
    the actuals lookup, so it isn't useful here). A (normalized_name,
    position) key shared by more than one distinct gsis_id is AMBIGUOUS and
    dropped from the name index entirely -- unsafe to silently pick one;
    fp_id or the override file must resolve those rows instead. A duplicate
    fp_id mapping to two different gsis_ids raises loud (data-integrity
    break in player_id_xwalk; verified absent live at Task 11 time)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT fantasypros_id, gsis_id, name, position "
            "FROM public.player_id_xwalk WHERE gsis_id IS NOT NULL"
        )
        rows = cur.fetchall()

    by_fpid: dict = {}
    for fp_id, gsis_id, _name, _pos in rows:
        if not fp_id:
            continue
        if fp_id in by_fpid and by_fpid[fp_id] != gsis_id:
            raise ValueError(
                f"player_id_xwalk.fantasypros_id {fp_id!r} maps to >1 distinct "
                f"gsis_id ({by_fpid[fp_id]!r}, {gsis_id!r}) -- xwalk integrity break"
            )
        by_fpid[fp_id] = gsis_id

    candidates: dict = defaultdict(set)
    for _fp_id, gsis_id, name, pos in rows:
        candidates[(normalize_name(name), pos)].add(gsis_id)
    by_namepos = {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}
    return by_fpid, by_namepos


def match_row(
    row: dict, by_fpid: dict, by_namepos: dict, overrides: dict
) -> MatchResult:
    """`row` needs 'name' and 'position' (archive vocabulary: 'K' not 'PK');
    'fp_id' is optional/absent-tolerant."""
    fp_id = row.get("fp_id")
    if fp_id and fp_id in by_fpid:
        return MatchResult(by_fpid[fp_id], "fp_id")
    pos = _POS_ALIAS.get(row["position"], row["position"])
    key = (normalize_name(row["name"]), pos)
    if key in by_namepos:
        return MatchResult(by_namepos[key], "name")
    okey = _override_key(row["name"], row["position"])
    if okey in overrides:
        return MatchResult(overrides[okey], "override")
    return MatchResult(None, "unmatched")


def enforce_match_gate(top_rows: list, results: list, season: int) -> None:
    """`top_rows`/`results` are the top `TOP_N_MATCH_GATE` ECR-ranked rows
    (overall rank, not per-position) and their parallel `MatchResult`s.
    Raises `SystemExit` listing the misses if the resolved fraction is below
    `MATCH_GATE_THRESHOLD` (R6 discipline)."""
    n = len(top_rows)
    matched = sum(1 for r in results if r.gsis_id is not None)
    rate = matched / n if n else 0.0
    if rate < MATCH_GATE_THRESHOLD:
        misses = [row["name"] for row, r in zip(top_rows, results) if r.gsis_id is None]
        raise SystemExit(
            f"backtest pool build ({season}): only {matched}/{n} ({rate:.1%}) of the "
            f"top {n} ECR names resolved to a gsis_id (need >= "
            f"{MATCH_GATE_THRESHOLD:.0%}). Unmatched: {misses}"
        )


# ---------------------------------------------------------------------------
# Degraded synthetic curve
# ---------------------------------------------------------------------------


def build_synthetic_curve(current_pool: list, position: str) -> list:
    """The CURRENT (2026) pool's points-at-rank curve for `position`: its
    `proj_points`, sorted descending. Monotone non-increasing by
    construction; `synthetic_proj_points` only ever indexes into it."""
    pts = sorted(
        (p.proj_points for p in current_pool if p.position == position), reverse=True
    )
    if not pts:
        raise ValueError(
            f"build_synthetic_curve: no current-pool players at position {position!r}"
        )
    return pts


def synthetic_proj_points(curve: list, rank_idx: int) -> float:
    """0-indexed rank within a season's real rank order (ECR ascending) ->
    synthetic proj_points. Clamped to the curve's last (lowest, replacement-
    level) value once `rank_idx` runs past the current pool's own depth at
    that position."""
    if rank_idx < 0:
        raise ValueError("synthetic_proj_points: rank_idx must be >= 0")
    return curve[rank_idx] if rank_idx < len(curve) else curve[-1]


# ---------------------------------------------------------------------------
# Archive stat-line -> StatLine (module docstring point 1)
# ---------------------------------------------------------------------------

_ARCHIVE_STAT_MAP = {
    "PASSING_CMP": "pass_completions",
    "PASSING_YDS": "pass_yards",
    "PASSING_TDS": "pass_tds",
    "PASSING_INTS": "interceptions",
    "RUSHING_ATT": "rush_attempts",
    "RUSHING_YDS": "rush_yards",
    "RUSHING_TDS": "rush_tds",
    "RECEIVING_REC": "receptions",
    "RECEIVING_YDS": "rec_yards",
    "RECEIVING_TDS": "rec_tds",
    "MISC_FL": "fumbles_lost",
}
_ARCHIVE_CONSUMED_SPECIALLY = {"PASSING_ATT"}  # -> pass_incompletions = ATT - CMP
_ARCHIVE_IGNORED = {"MISC_FPTS"}  # the page's own untrusted scoring total; never used


def statline_from_archive(stats: dict, position: str) -> StatLine:
    known = set(_ARCHIVE_STAT_MAP) | _ARCHIVE_CONSUMED_SPECIALLY | _ARCHIVE_IGNORED
    unknown = sorted(set(stats) - known)
    if unknown:
        raise ValueError(
            f"statline_from_archive: unknown archive stat field(s) {unknown} for "
            f"position {position!r} -- map them explicitly (_ARCHIVE_STAT_MAP / "
            "_ARCHIVE_IGNORED) before scoring; never silently score an unrecognized stat"
        )
    kwargs = {
        sl: float(stats[src]) for src, sl in _ARCHIVE_STAT_MAP.items() if src in stats
    }
    if "PASSING_ATT" in stats:
        kwargs["pass_incompletions"] = float(stats["PASSING_ATT"]) - float(
            stats.get("PASSING_CMP", 0.0)
        )
    return StatLine(**kwargs)


def score_archive_projection(
    stats: dict, position: str, cfg: ScoringConfig, cv: dict
) -> float:
    """Season points for one archived stat line: `score_components`'s naive
    once-per-season bonus is REPLACED by the weekly threshold-bonus EV
    (`season_bonus_ev`, `gsis_id=None` -> position-level CV), matching the
    same pattern `scripts/score_sleeper_projections.py` uses for season
    horizons."""
    line = statline_from_archive(stats, position)
    comps = score_components(line, cfg)
    comps["bonuses"] = Decimal(
        repr(round(season_bonus_ev(line, cfg, cv, position, None), 4))
    )
    return float(sum(comps.values()))


def real_projection_positions(payload: list, min_rows: int = MIN_REAL_PROJ_ROWS) -> set:
    """Per-position coverage derived from the payload's own position census
    (module docstring point 2) -- never from row counts alone or the doc.
    K is deliberately excluded even if it clears the threshold: see module
    docstring point 5."""
    census = Counter(row["position"] for row in payload)
    return {pos for pos, n in census.items() if n >= min_rows and pos in REAL_POS}


# ---------------------------------------------------------------------------
# Pool adequacy
# ---------------------------------------------------------------------------


def validate_pool_adequacy(rows_by_position: dict) -> None:
    """Fail loud if any position can't fill 12 teams' minimums with slack
    (module docstring / brief carry-forward fact #10)."""
    for pos, min_n in MIN_POOL_COUNTS.items():
        n = len(rows_by_position.get(pos, []))
        if n < min_n:
            raise ValueError(
                f"backtest pool position {pos!r} has only {n} players (need >= {min_n})"
            )
    n_def = len(rows_by_position.get("DEF", []))
    if n_def < MIN_DEF_ROWS:
        raise ValueError(
            f"backtest pool DEF has only {n_def} rows (need >= {MIN_DEF_ROWS} for "
            "12 legal starters)"
        )


# ---------------------------------------------------------------------------
# Season pool assembly
# ---------------------------------------------------------------------------


def build_season_pool(
    conn, season: int, current_pool: list, cfg: ScoringConfig, cv: dict, overrides=None
):
    """Assemble one backtest season's `sim.backtest_pool` rows. Returns
    (rows: list[dict], report: dict) -- `rows` are ready for
    `upsert_season_pool`; `report` is a printable summary (counts, match
    rate, which positions were degraded)."""
    if overrides is None:
        overrides = load_overrides()
    by_fpid, by_namepos = load_xwalk_lookup(conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM raw.backtest_sources "
            "WHERE source='dynastyprocess' AND season=%s AND kind='ecr'",
            (season,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(
                f"build_season_pool: no dynastyprocess ecr payload for {season}"
            )
        ecr_payload = row[0]

        cur.execute(
            "SELECT payload FROM raw.backtest_sources "
            "WHERE source='wayback_fp' AND season=%s AND kind='projections'",
            (season,),
        )
        row = cur.fetchone()
        proj_payload = row[0] if row else []

    real_positions = real_projection_positions(proj_payload)
    proj_by_fpid = {r["fp_id"]: r for r in proj_payload if r.get("fp_id")}

    ecr_sorted_all = sorted(ecr_payload, key=lambda r: r["ecr"])
    top_rows = ecr_sorted_all[:TOP_N_MATCH_GATE]
    top_matches = [match_row(r, by_fpid, by_namepos, overrides) for r in top_rows]
    enforce_match_gate(top_rows, top_matches, season)

    rows_by_pos: dict = {}
    unmatched_names: list = []
    total_seen = 0
    total_matched = 0
    for pos in REAL_POS:
        pos_rows = sorted(
            (r for r in ecr_payload if r["position"] == pos), key=lambda r: r["ecr"]
        )
        curve = build_synthetic_curve(current_pool, pos)
        out = []
        for rank_idx, erow in enumerate(pos_rows):
            total_seen += 1
            m = match_row(erow, by_fpid, by_namepos, overrides)
            if m.gsis_id is None:
                unmatched_names.append(erow["name"])
                continue
            total_matched += 1
            proj_row = (
                proj_by_fpid.get(erow.get("fp_id")) if pos in real_positions else None
            )
            if proj_row is not None:
                proj_points = score_archive_projection(proj_row["stats"], pos, cfg, cv)
                degraded = False
                provenance = {"match_method": m.method, "source": "archive_statline"}
            else:
                proj_points = synthetic_proj_points(curve, rank_idx)
                degraded = True
                provenance = {"match_method": m.method, "source": "synthetic_curve"}
            out.append(
                {
                    "ref": m.gsis_id,
                    "name": erow["name"],
                    "position": pos,
                    "proj_points": proj_points,
                    "adp": float(erow["ecr"]),
                    "degraded": degraded,
                    "provenance": provenance,
                }
            )
        rows_by_pos[pos] = out

    points_by_pos = {
        pos: sorted((r["proj_points"] for r in rows), reverse=True)
        for pos, rows in rows_by_pos.items()
    }
    # Starts-based replacement (design 2026-07-21, Phase B): QB/RB/WR/TE
    # replacement rank = round(12 x sum P_start) (QB24/RB36/WR36/TE12), matching
    # the deployed live valuation so `vorp = proj - starts_baseline` is exactly
    # A''s (proj - baseline) term and the LIVE strategy reproduces on these pools.
    ranks = compute_replacement_ranks(VORP_SCENARIO)
    ranks.update(starts_replacement_ranks(load_starts_table()))
    ranks = {p: r for p, r in ranks.items() if p in points_by_pos}
    baselines = compute_baselines(points_by_pos, ranks)

    final_rows: list = []
    for pos, rows in rows_by_pos.items():
        rows_sorted = sorted(rows, key=lambda r: -r["proj_points"])
        pts = [r["proj_points"] for r in rows_sorted]
        tiers = gmm_tiers(pts) if len(pts) >= 4 else [1] * len(pts)
        for r, tier in zip(rows_sorted, tiers):
            r["vorp"] = r["proj_points"] - baselines[pos]
            r["tier"] = tier
            final_rows.append(r)

    # K: borrow the CURRENT (2026) pool's kicker roster verbatim -- module
    # docstring point 5.
    for p in current_pool:
        if p.position != "K" or p.gsis_id is None:
            continue
        final_rows.append(
            {
                "ref": p.gsis_id,
                "name": p.name,
                "position": "K",
                "proj_points": p.proj_points,
                "vorp": p.vorp,
                "tier": p.tier,
                "adp": None,
                "degraded": True,
                "provenance": {"source": "current_pool_2026_verbatim"},
            }
        )

    # DEF: synthetic dummy rows so drafts stay legal -- module docstring point 4.
    for i in range(N_DUMMY_DEF):
        final_rows.append(
            {
                "ref": f"DEF_DUMMY_{i:02d}",
                "name": f"Backtest Replacement DEF {i:02d}",
                "position": "DEF",
                "proj_points": 0.0,
                "vorp": 0.0,
                "tier": 1,
                "adp": None,
                "degraded": True,
                "provenance": {"source": "dummy"},
            }
        )

    refs = [r["ref"] for r in final_rows]
    dupes = sorted({x for x in refs if refs.count(x) > 1})
    if dupes:
        raise ValueError(
            f"build_season_pool ({season}): duplicate refs after assembly: {dupes[:10]}"
        )

    rows_by_position_final = defaultdict(list)
    for r in final_rows:
        rows_by_position_final[r["position"]].append(r)
    validate_pool_adequacy(rows_by_position_final)

    degraded_positions = sorted((set(REAL_POS) - real_positions) | {"K", "DEF"})
    report = {
        "season": season,
        "real_projection_positions": sorted(real_positions),
        "degraded_positions": degraded_positions,
        "match_resolved": total_matched,
        "match_total": total_seen,
        "unmatched_count": len(unmatched_names),
        "counts": {pos: len(rows) for pos, rows in rows_by_position_final.items()},
    }
    return final_rows, report


def upsert_season_pool(conn, season: int, rows: list) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM sim.backtest_pool WHERE season=%s", (season,))
        for r in rows:
            cur.execute(
                """INSERT INTO sim.backtest_pool
                   (season, ref, name, position, proj_points, vorp, tier, adp, degraded, provenance)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    season,
                    r["ref"],
                    r["name"],
                    r["position"],
                    r["proj_points"],
                    r["vorp"],
                    r["tier"],
                    r["adp"],
                    r["degraded"],
                    json.dumps(r.get("provenance", {})),
                ),
            )
    conn.commit()


def season_data_vintage(conn, season: int) -> dict:
    """Per-position degraded flag for `season`, read straight back off
    `sim.backtest_pool` (per-row truth, not re-derived) -- for
    `sim.batches.data_vintage` so every downstream conclusion carries which
    positions in that season's board were synthetic (module docstring
    points 3-5, carry-forward fact #3: "conclusions must carry the flag")."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT position, bool_or(degraded), avg(degraded::int) FROM sim.backtest_pool "
            "WHERE season=%s GROUP BY position",
            (season,),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError(
            f"season_data_vintage: no sim.backtest_pool rows for season {season}"
        )
    return {
        "season": season,
        "degraded_by_position": {pos: bool(any_d) for pos, any_d, _ in rows},
        "degraded_fraction_by_pos": {pos: float(frac) for pos, _, frac in rows},
    }


def load_backtest_pool(conn, season: int) -> list:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ref, name, position, proj_points, vorp, tier, adp "
            "FROM sim.backtest_pool WHERE season=%s "
            "ORDER BY (adp IS NULL), adp, proj_points DESC, ref",
            (season,),
        )
        rows = cur.fetchall()
    if not rows:
        raise ValueError(
            f"load_backtest_pool: sim.backtest_pool has no rows for season {season} -- "
            "run scripts/build_backtest_pools.py first"
        )
    pool = []
    for ref, name, position, proj_points, vorp, tier, adp in rows:
        gsis_id = (
            None if position == "DEF" else ref
        )  # ref IS the gsis_id (module docstring)
        pool.append(
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
    return pool


def load_points_lookup(conn, season: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT player_ref, week, points FROM scoring.player_week_points
               WHERE source='nflverse' AND season=%s AND week BETWEEN 1 AND 14""",
            (season,),
        )
        return {(ref, week): float(pts) for ref, week, pts in cur.fetchall()}


# ---------------------------------------------------------------------------
# Reference cells + composite/gate
# ---------------------------------------------------------------------------


def cell_base_seed(strategy_idx: int, season: int) -> int:
    """Deterministic, collision-free base seed per (strategy, season) cell."""
    return 1_000_000 * season + strategy_idx * 100_000 + 1


def run_cell(
    pool: list,
    priors: SlotPriors,
    points_lookup: dict,
    strategy: StrategyParams,
    base_seed: int,
    n_drafts: int = N_DRAFTS_PER_CELL,
) -> dict:
    """`n_drafts` seeded drafts (seeds `base_seed..base_seed+n_drafts-1`) for
    one (strategy, season) cell, evaluated against `points_lookup`. Returns
    {'all_play_pct','all_play_se','qb1_round_mean','n_drafts'}."""
    pick_fn = make_strategy_fn(strategy)
    all_play: list = []
    qb1_rounds: list = []
    for i in range(n_drafts):
        seed = base_seed + i
        result = run_draft(
            pool, priors, pick_fn, seed=seed, our_franchise_slot=OUR_FRANCHISE_SLOT
        )
        pct_map = evaluate_league(
            result.rosters, cv_by_pos={}, seed=seed, points_lookup=points_lookup
        )
        all_play.append(pct_map[result.our_position])
        qb_picks = [
            p
            for p in result.picks
            if p["position_slot"] == result.our_position and p["pos"] == "QB"
        ]
        if qb_picks:
            first_overall = min(p["overall"] for p in qb_picks)
            qb1_rounds.append(snake_position(first_overall)[0])

    n = len(all_play)
    mean_pct = statistics.mean(all_play)
    se_pct = statistics.stdev(all_play) / math.sqrt(n) if n > 1 else 0.0
    qb1_mean = statistics.mean(qb1_rounds) if qb1_rounds else None
    return {
        "all_play_pct": mean_pct,
        "all_play_se": se_pct,
        "qb1_round_mean": qb1_mean,
        "n_drafts": n,
    }


def run_all_cells(conn) -> list:
    """Run the 12 (strategy, season) reference cells -- the shared computation
    behind both `--reference` and `--gate`. Iterates GATE_SEASONS (2023-25),
    NOT BACKTEST_SEASONS: the D7 gate is a frozen 3-season baseline and must
    not shift when the tournament seasons (2021-2025) grow. See GATE_SEASONS."""
    priors = build_slot_priors(conn)
    results = []
    for season in GATE_SEASONS:
        pool = load_backtest_pool(conn, season)
        lookup = load_points_lookup(conn, season)
        for idx, strat in enumerate(REF_STRATEGIES):
            base_seed = cell_base_seed(idx, season)
            metrics = run_cell(pool, priors, lookup, strat, base_seed)
            results.append({"strategy_idx": idx, "season": season, **metrics})
    return results


def composite_and_band(cell_means: list) -> tuple:
    """composite = mean of the cells' own means; band = 2 x SE across the
    cell means themselves (not the pooled per-draft observations)."""
    n = len(cell_means)
    if n < 2:
        raise ValueError("composite_and_band: need >= 2 cells")
    composite = statistics.mean(cell_means)
    se = statistics.stdev(cell_means) / math.sqrt(n)
    return composite, 2 * se


def evaluate_gate(composite: float, reference) -> None:
    """`reference` = (ref_composite, ref_band) tuple, or None if no active
    `sim.backtest_reference` row exists. Raises `SystemExit` (nonzero) on
    failure or a missing reference; returns None (silently) on pass."""
    if reference is None:
        raise SystemExit(
            "run_backtests.py --gate: no active sim.backtest_reference row -- run "
            "`run_backtests.py --reference` first to establish one"
        )
    ref_composite, ref_band = reference
    threshold = ref_composite - ref_band
    if composite < threshold:
        raise SystemExit(
            f"ADR D7 gate FAILED: composite {composite:.4f} < threshold {threshold:.4f} "
            f"(reference {ref_composite:.4f} - band {ref_band:.4f})"
        )
