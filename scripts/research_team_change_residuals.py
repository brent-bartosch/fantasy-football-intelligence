#!/usr/bin/env python3
"""GATE study: does an offseason NFL team change earn a residual over/under
the preseason market's own valuation, under THIS league's scoring?

Read-only research script -- no writes to any table, no schema changes. See
`docs/research/2026-07-10-team-change-residuals.md` for the full method,
tables, and verdict. Re-run: `uv run python scripts/research_team_change_residuals.py`.

Method summary (kept intentionally simple -- this is a gate, not a model):

1. Per season 2023-2025, positions QB/RB/WR/TE: match `raw.backtest_sources`
   (dynastyprocess, kind='ecr') rows to a gsis_id via the SAME three-tier
   matcher `ffi.sim.backtest` already uses for the Phase 3 backtest pools
   (`fp_id` -> xwalk, then normalized name+position, then the manual
   override file) -- reused, not reimplemented. Unmatched rows are dropped
   from the study (rate reported).
2. Each position's within-season rank is assigned over ALL ecr rows at that
   position (matched or not) so the market-rank order isn't distorted by
   which rows happen to resolve to a gsis_id; only matched rows carry
   forward into the analysis. Rank -> bucket of BUCKET_SIZE (6) consecutive
   ranks (bucket 1 = ranks 1-6, the top of the position, etc).
3. Expectation curve: for a held-out season S, `expected(position, bucket)`
   = the MEDIAN actual league points (weeks 1-14, `scoring.player_week_points`,
   source='nflverse', config_version=1; missing weeks/players = 0.0, same
   "absence is signal" convention `ffi.sim.backtest.load_points_lookup`
   uses) among matched players at that (position, bucket) in the OTHER TWO
   seasons only (leave-one-season-out -- S's own outcomes never feed its own
   expectation, so the residual isn't self-referential). A (position,
   bucket) combination absent from the training seasons (thin tail) falls
   back to the nearest bucket that IS present for that position.
4. residual = actual - expected(position, bucket).
5. Team-change classification (veterans only): modal team in season N vs
   modal team in season N-1, both from `raw.nflverse_player_week` (mode =
   most frequent (gsis_id, season) team across weeks played; ties broken by
   the team associated with the latest week played). A player with ZERO
   season N-1 rows is a ROOKIE -- excluded from the changer/stayer split
   (still counted in the expectation-curve fit; a different phenomenon, not
   this study's question). A player with ZERO season-N rows (hurt/cut
   before playing a snap) has no observable "landed at" team and is
   excluded from the split too (UNKNOWN_CURRENT_TEAM), reported separately.
6. Compare CHANGER vs STAYER residuals per position and pooled: n, mean,
   median, a seeded bootstrap 95% CI on the difference of means (changer -
   stayer), and a bust rate (fraction of each class below the pooled
   veteran bottom-quartile residual for that position/pool).
7. Verdict per position (and pooled): PRICED if the CI spans 0 or
   |diff| < ~10 pts/season; otherwise MISPRICED, with direction + magnitude.

Known conflation (documented, not fixed -- see docs): modal-team-N-vs-N-1
catches genuine offseason moves (FA/trade before Week 1, the phenomenon this
study is about) AND pure in-season trades (which the preseason market could
not have priced at all). In-season trades are rare and cut both directions,
so they add noise, not a systematic bias, to the CHANGER arm.
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from ffi.db import connect
from ffi.sim.backtest import load_overrides, load_xwalk_lookup, match_row

SEASONS = (2023, 2024, 2025)
POSITIONS = ("QB", "RB", "WR", "TE")
BUCKET_SIZE = 6
BOOTSTRAP_N = 10_000
BOOTSTRAP_SEED = 20260710  # deterministic, fixed
BUST_QUANTILE = 0.25
TOP_N_MATCH_REPORT = 200
PRICED_ABS_THRESHOLD = 10.0  # pts/season


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_ecr_rows(conn, season: int) -> list[dict]:
    """Raw dynastyprocess ECR rows for `season`, filtered to QB/RB/WR/TE
    (the only positions this payload ever carries, but filtered explicitly
    rather than assumed)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM raw.backtest_sources "
            "WHERE source='dynastyprocess' AND season=%s AND kind='ecr'",
            (season,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"no dynastyprocess ecr payload for season {season}")
    return [r for r in row[0] if r["position"] in POSITIONS]


def assign_position_ranks(pos_rows: list[dict]) -> list[dict]:
    """`pos_rows`: one position's ECR rows for one season (any order). Adds
    1-indexed `rank` (ascending ecr = best) and `bucket`
    (`ceil(rank / BUCKET_SIZE)`), computed over EVERY row so unmatched
    players still occupy their true market-rank slot."""
    ordered = sorted(pos_rows, key=lambda r: r["ecr"])
    out = []
    for i, r in enumerate(ordered):
        rank = i + 1
        bucket = (rank - 1) // BUCKET_SIZE + 1
        out.append({**r, "rank": rank, "bucket": bucket})
    return out


def match_ecr_rows(
    rows: list[dict], by_fpid: dict, by_namepos: dict, overrides: dict
) -> list[dict]:
    """Attach `gsis_id`/`match_method` (None/'unmatched' if no match) via the
    reused three-tier matcher from `ffi.sim.backtest`."""
    out = []
    for r in rows:
        m = match_row(r, by_fpid, by_namepos, overrides)
        out.append({**r, "gsis_id": m.gsis_id, "match_method": m.method})
    return out


def load_actual_points(conn, season: int) -> dict[str, float]:
    """gsis_id -> summed league points, weeks 1-14, config_version=1,
    source='nflverse'. A player absent entirely gets 0.0 via `.get` at the
    call site (bye/inactive/injury is real signal, not missing data)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT player_ref, points FROM scoring.player_week_points "
            "WHERE source='nflverse' AND season=%s AND config_version=1 "
            "AND week BETWEEN 1 AND 14",
            (season,),
        )
        rows = cur.fetchall()
    totals: dict = defaultdict(float)
    for ref, pts in rows:
        totals[ref] += float(pts)
    return dict(totals)


def load_modal_teams(conn, season: int) -> dict[str, str]:
    """gsis_id -> modal (most-frequent) team for `season` from
    `raw.nflverse_player_week`. Ties broken by the team associated with the
    latest week played (the more "current" team for a mid-season trade)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT gsis_id, week, team FROM raw.nflverse_player_week WHERE season=%s",
            (season,),
        )
        rows = cur.fetchall()
    by_player: dict = defaultdict(list)
    for gsis_id, week, team in rows:
        by_player[gsis_id].append((week, team))
    modal: dict = {}
    for gsis_id, weeks in by_player.items():
        counts = Counter(team for _week, team in weeks)
        max_count = max(counts.values())
        tied = [t for t, c in counts.items() if c == max_count]
        if len(tied) == 1:
            modal[gsis_id] = tied[0]
        else:
            last_week_for_team: dict = {}
            for week, team in weeks:
                if team in tied:
                    last_week_for_team[team] = max(
                        last_week_for_team.get(team, -1), week
                    )
            modal[gsis_id] = max(tied, key=lambda t: last_week_for_team[t])
    return modal


# ---------------------------------------------------------------------------
# Player records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerRecord:
    season: int
    position: str
    name: str
    gsis_id: str
    rank: int
    bucket: int
    actual: float
    classification: str  # 'rookie' | 'unknown_current_team' | 'changer' | 'stayer'


def classify(prev_team: str | None, this_team: str | None) -> str:
    if prev_team is None:
        return "rookie"
    if this_team is None:
        return "unknown_current_team"
    return "changer" if prev_team != this_team else "stayer"


def build_season_records(
    conn, season: int, by_fpid: dict, by_namepos: dict, overrides: dict
) -> tuple[list[PlayerRecord], dict]:
    """One season's matched PlayerRecords + a match-rate report dict."""
    ecr_rows = load_ecr_rows(conn, season)
    actual_points = load_actual_points(conn, season)
    modal_this = load_modal_teams(conn, season)
    modal_prev = load_modal_teams(conn, season - 1)

    records: list[PlayerRecord] = []
    match_total = 0
    match_resolved = 0

    for pos in POSITIONS:
        pos_rows = [r for r in ecr_rows if r["position"] == pos]
        ranked = assign_position_ranks(pos_rows)
        matched = match_ecr_rows(ranked, by_fpid, by_namepos, overrides)
        for r in matched:
            match_total += 1
            if r["gsis_id"] is None:
                continue
            match_resolved += 1
            gsis_id = r["gsis_id"]
            actual = actual_points.get(gsis_id, 0.0)
            prev_team = modal_prev.get(gsis_id)
            this_team = modal_this.get(gsis_id)
            records.append(
                PlayerRecord(
                    season=season,
                    position=pos,
                    name=r["name"],
                    gsis_id=gsis_id,
                    rank=r["rank"],
                    bucket=r["bucket"],
                    actual=actual,
                    classification=classify(prev_team, this_team),
                )
            )

    overall_sorted = sorted(ecr_rows, key=lambda r: r["ecr"])[:TOP_N_MATCH_REPORT]
    overall_matched = match_ecr_rows(overall_sorted, by_fpid, by_namepos, overrides)
    top_n = len(overall_matched)
    top_resolved = sum(1 for r in overall_matched if r["gsis_id"] is not None)

    report = {
        "season": season,
        "match_resolved": match_resolved,
        "match_total": match_total,
        "top_n": top_n,
        "top_resolved": top_resolved,
    }
    return records, report


# ---------------------------------------------------------------------------
# Leave-one-season-out expectation + residuals
# ---------------------------------------------------------------------------


def fit_expectation(train_records: list[PlayerRecord]) -> dict[tuple[str, int], float]:
    """(position, bucket) -> median actual points, from `train_records`."""
    buckets: dict = defaultdict(list)
    for r in train_records:
        buckets[(r.position, r.bucket)].append(r.actual)
    return {k: statistics.median(v) for k, v in buckets.items()}


def expected_points(position: str, bucket: int, expectation: dict) -> float:
    """`expectation[(position, bucket)]`, falling back to the nearest bucket
    trained for `position` if this exact bucket has no training data."""
    key = (position, bucket)
    if key in expectation:
        return expectation[key]
    available = sorted(b for (p, b) in expectation if p == position)
    if not available:
        raise ValueError(
            f"expected_points: no training data at all for position {position!r}"
        )
    nearest = min(available, key=lambda b: abs(b - bucket))
    return expectation[(position, nearest)]


def compute_residuals(all_records: dict[int, list[PlayerRecord]]) -> list[dict]:
    """Leave-one-season-out residuals for every matched player-season across
    all held-out seasons. Each dict carries season/position/name/gsis_id/
    rank/bucket/actual/expected/residual/classification."""
    out = []
    for held_out in SEASONS:
        train = [r for s, recs in all_records.items() if s != held_out for r in recs]
        expectation = fit_expectation(train)
        for r in all_records[held_out]:
            expected = expected_points(r.position, r.bucket, expectation)
            out.append(
                {
                    "season": r.season,
                    "position": r.position,
                    "name": r.name,
                    "gsis_id": r.gsis_id,
                    "rank": r.rank,
                    "bucket": r.bucket,
                    "actual": r.actual,
                    "expected": expected,
                    "residual": r.actual - expected,
                    "classification": r.classification,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Bootstrap + comparison stats
# ---------------------------------------------------------------------------


def bootstrap_diff_ci(
    a: list[float], b: list[float], seed: int, n_boot: int = BOOTSTRAP_N
) -> tuple[float, float, float]:
    """Deterministic (seeded) bootstrap 95% CI on mean(a) - mean(b). Returns
    (point_diff, ci_lo, ci_hi)."""
    if not a or not b:
        raise ValueError("bootstrap_diff_ci: both groups must be non-empty")
    arr_a = np.asarray(a, dtype=float)
    arr_b = np.asarray(b, dtype=float)
    rng = np.random.default_rng(seed)
    idx_a = rng.integers(0, arr_a.size, size=(n_boot, arr_a.size))
    idx_b = rng.integers(0, arr_b.size, size=(n_boot, arr_b.size))
    diffs = arr_a[idx_a].mean(axis=1) - arr_b[idx_b].mean(axis=1)
    diffs.sort()
    lo = float(diffs[int(0.025 * n_boot)])
    hi = float(diffs[min(int(0.975 * n_boot), n_boot - 1)])
    point = float(arr_a.mean() - arr_b.mean())
    return point, lo, hi


def compare_groups(records: list[dict], seed: int = BOOTSTRAP_SEED) -> dict:
    """`records`: residual dicts already scoped to one position (or pooled
    across positions), any number of seasons. Compares CHANGER vs STAYER
    residuals (rookies / unknown_current_team are excluded from this split
    by construction -- only 'changer'/'stayer' classifications enter)."""
    changers = [r["residual"] for r in records if r["classification"] == "changer"]
    stayers = [r["residual"] for r in records if r["classification"] == "stayer"]
    if not changers or not stayers:
        return {
            "n_changer": len(changers),
            "n_stayer": len(stayers),
            "insufficient": True,
        }
    veterans = changers + stayers
    threshold = float(np.quantile(veterans, BUST_QUANTILE))
    diff, lo, hi = bootstrap_diff_ci(changers, stayers, seed=seed)
    return {
        "insufficient": False,
        "n_changer": len(changers),
        "n_stayer": len(stayers),
        "mean_changer": statistics.mean(changers),
        "mean_stayer": statistics.mean(stayers),
        "median_changer": statistics.median(changers),
        "median_stayer": statistics.median(stayers),
        "diff_mean": diff,
        "ci_lo": lo,
        "ci_hi": hi,
        "bust_threshold": threshold,
        "bust_rate_changer": sum(1 for x in changers if x <= threshold) / len(changers),
        "bust_rate_stayer": sum(1 for x in stayers if x <= threshold) / len(stayers),
    }


def verdict(stats: dict) -> str:
    if stats["insufficient"]:
        return "INSUFFICIENT DATA"
    diff = stats["diff_mean"]
    ci_includes_zero = stats["ci_lo"] <= 0.0 <= stats["ci_hi"]
    if ci_includes_zero or abs(diff) < PRICED_ABS_THRESHOLD:
        return "PRICED"
    direction = "changers OUTPERFORM" if diff > 0 else "changers UNDERPERFORM"
    return f"MISPRICED ({direction}, diff={diff:+.1f} pts/season)"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    conn = connect()
    by_fpid, by_namepos = load_xwalk_lookup(conn)
    overrides = load_overrides()

    all_records: dict[int, list[PlayerRecord]] = {}
    print("=== match rates (dynastyprocess ECR -> gsis_id) ===")
    for season in SEASONS:
        records, report = build_season_records(
            conn, season, by_fpid, by_namepos, overrides
        )
        all_records[season] = records
        print(
            f"  {season}: overall {report['match_resolved']}/{report['match_total']} "
            f"({report['match_resolved'] / report['match_total']:.1%}); "
            f"top-{report['top_n']} {report['top_resolved']}/{report['top_n']} "
            f"({report['top_resolved'] / report['top_n']:.1%})"
        )

    residuals = compute_residuals(all_records)

    class_counts = Counter(r["classification"] for r in residuals)
    print("\n=== classification counts (pooled, 3 seasons) ===")
    for cls in ("stayer", "changer", "rookie", "unknown_current_team"):
        print(f"  {cls}: {class_counts.get(cls, 0)}")

    print("\n=== per-position: changer vs stayer residual (actual - LOSO-expected) ===")
    header = (
        f"{'pos':4} {'n_chg':>6} {'n_sty':>6} {'mean_chg':>9} {'mean_sty':>9} "
        f"{'diff':>8} {'ci_lo':>8} {'ci_hi':>8} {'bust_chg':>9} {'bust_sty':>9}  verdict"
    )
    print(header)
    per_position_stats = {}
    for pos in POSITIONS:
        pos_records = [r for r in residuals if r["position"] == pos]
        stats = compare_groups(pos_records)
        per_position_stats[pos] = stats
        if stats["insufficient"]:
            print(
                f"{pos:4} n_changer={stats['n_changer']} n_stayer={stats['n_stayer']} -- INSUFFICIENT DATA"
            )
            continue
        v = verdict(stats)
        print(
            f"{pos:4} {stats['n_changer']:>6} {stats['n_stayer']:>6} "
            f"{stats['mean_changer']:>9.2f} {stats['mean_stayer']:>9.2f} "
            f"{stats['diff_mean']:>8.2f} {stats['ci_lo']:>8.2f} {stats['ci_hi']:>8.2f} "
            f"{stats['bust_rate_changer']:>9.1%} {stats['bust_rate_stayer']:>9.1%}  {v}"
        )

    print("\n=== pooled (all positions) ===")
    pooled_stats = compare_groups(residuals)
    pooled_verdict = verdict(pooled_stats)
    print(header)
    print(
        f"{'ALL':4} {pooled_stats['n_changer']:>6} {pooled_stats['n_stayer']:>6} "
        f"{pooled_stats['mean_changer']:>9.2f} {pooled_stats['mean_stayer']:>9.2f} "
        f"{pooled_stats['diff_mean']:>8.2f} {pooled_stats['ci_lo']:>8.2f} {pooled_stats['ci_hi']:>8.2f} "
        f"{pooled_stats['bust_rate_changer']:>9.1%} {pooled_stats['bust_rate_stayer']:>9.1%}  {pooled_verdict}"
    )

    print("\n=== per-position medians (for reference) ===")
    for pos in POSITIONS:
        stats = per_position_stats[pos]
        if stats["insufficient"]:
            continue
        print(
            f"  {pos}: median_changer={stats['median_changer']:.2f} "
            f"median_stayer={stats['median_stayer']:.2f}"
        )

    print("\nresearch_team_change_residuals.py: done (read-only, no DB writes)")


if __name__ == "__main__":
    main()
