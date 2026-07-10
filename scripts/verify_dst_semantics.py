#!/usr/bin/env python3
"""One-shot (re-runnable) verification protocol for Sleeper DST season-
snapshot tier semantics (Task 3 / T7 carry-forward).

Steps (binding protocol — see .superpowers/sdd/task-3-brief.md):
  1. Enumerate the full DEF stat-key union in the latest Sleeper season
     snapshot (live query).
  2. Reconstruct Sleeper's own `pts_std` from those keys under Sleeper's
     documented standard DEF scoring and check it matches for >=28/32
     teams within 5%. FAILS LOUD (nonzero exit) if the gate is not met —
     do not guess past this point; a wrong bucket-semantics guess silently
     corrupts every DEF projection downstream.
  3. Insert the 32 DEF crosswalk rows (idempotent).
  4. Sanity-check the freshly-scored 2026 DEF season projections against
     2025 ground truth (yahoo_engine actuals) via Spearman rank
     correlation. Requires DEF projections to already be scored (run
     scripts/score_sleeper_projections.py first) — prints a clear skip
     message otherwise rather than crashing.
"""
import sys

from scipy.stats import spearmanr

from ffi.db import connect

RECONSTRUCTION_GATE = 28  # of 32 teams
RECONSTRUCTION_TOLERANCE = 0.05
SPEARMAN_GATE = 0.3

# Sleeper's documented standard DEF scoring weights for the counting stats
# it actually projects at season level (verified live: these 4 keys are the
# only real per-team-varying counting stats in the union).
_SLEEPER_STD_WEIGHTS = {"sack": 1, "int": 2, "fum_rec": 2, "blk_kick": 2}


def step1_enumerate_keys(conn) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT payload::jsonb p FROM raw.sleeper_projections
                WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1
            ),
            recs AS (SELECT jsonb_array_elements(p) rec FROM latest)
            SELECT jsonb_object_keys(rec->'stats') k, count(*)
            FROM recs WHERE rec->'player'->>'position'='DEF'
            GROUP BY 1 ORDER BY 1
            """
        )
        rows = cur.fetchall()
    print(f"\n=== Step 1: DEF stat-key union ({len(rows)} distinct keys) ===")
    for k, n in rows:
        print(f"  {k:<20} {n}/32")
    return dict(rows)


def step2_reconstruct(conn) -> list[tuple[str, float, float, float]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH latest AS (
                SELECT payload::jsonb p FROM raw.sleeper_projections
                WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1
            ),
            recs AS (SELECT jsonb_array_elements(p) rec FROM latest)
            SELECT rec->>'player_id' team,
                   coalesce((rec->'stats'->>'sack')::float, 0),
                   coalesce((rec->'stats'->>'int')::float, 0),
                   coalesce((rec->'stats'->>'fum_rec')::float, 0),
                   coalesce((rec->'stats'->>'blk_kick')::float, 0),
                   (rec->'stats'->>'pts_std')::float
            FROM recs WHERE rec->'player'->>'position'='DEF'
            ORDER BY team
            """
        )
        rows = cur.fetchall()
    results = []
    for team, sack, intr, fum_rec, blk, pts_std in rows:
        reconstructed = (
            sack * _SLEEPER_STD_WEIGHTS["sack"]
            + intr * _SLEEPER_STD_WEIGHTS["int"]
            + fum_rec * _SLEEPER_STD_WEIGHTS["fum_rec"]
            + blk * _SLEEPER_STD_WEIGHTS["blk_kick"]
        )
        pct_err = abs(reconstructed - pts_std) / pts_std if pts_std else float("inf")
        results.append((team, reconstructed, pts_std, pct_err))
    return results


def print_reconstruction_table(results) -> int:
    print(
        "\n=== Step 2: pts_std reconstruction (sack*1 + int*2 + fum_rec*2 + blk_kick*2) ==="
    )
    print(f"{'team':<6}{'reconstructed':>15}{'pts_std':>10}{'pct_err':>10}  pass")
    passing = 0
    for team, recon, pts_std, pct_err in results:
        ok = pct_err < RECONSTRUCTION_TOLERANCE
        passing += ok
        print(
            f"{team:<6}{recon:>15.2f}{pts_std:>10.2f}{pct_err:>9.2%}  {'PASS' if ok else 'FAIL'}"
        )
    print(
        f"\n{passing}/{len(results)} teams reconstructed within {RECONSTRUCTION_TOLERANCE:.0%}"
    )
    return passing


# Discovered live (Step 3, first run): public.team_def_map.team_abbr carries
# YAHOO's own team abbreviation (it's populated from Yahoo's players table —
# scripts/build_def_map.py), which for the Rams is "LA". Sleeper's
# raw.sleeper_projections player_id for the same team is "LAR". Verified this
# is the ONLY divergence among the 32 teams (a LEFT JOIN of all 32
# team_def_map rows against scored Sleeper season projections found exactly
# one unmatched team_abbr: "LA"). Fixing this in team_def_map itself would
# risk breaking other call sites that rely on Yahoo's own convention
# (scripts/backfill_def_k_weeks.py, phase1_report.py); the override is scoped
# to sleeper_id only, right where the two vocabularies actually need to meet.
_YAHOO_TO_SLEEPER_ABBR = {"LA": "LAR"}


def step3_insert_xwalk(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT yahoo_def_id, team_abbr, team_name FROM public.team_def_map"
        )
        teams = cur.fetchall()
        inserted = 0
        for yahoo_def_id, team_abbr, team_name in teams:
            sleeper_id = _YAHOO_TO_SLEEPER_ABBR.get(team_abbr, team_abbr)
            cur.execute(
                """
                INSERT INTO public.player_id_xwalk
                    (name, position, team, sleeper_id, yahoo_id, manual_override)
                SELECT %s, 'DEF', %s, %s, %s, true
                WHERE NOT EXISTS (
                    SELECT 1 FROM public.player_id_xwalk x
                    WHERE x.position='DEF' AND x.sleeper_id = %s
                )
                """,
                (f"{team_name} DEF", team_abbr, sleeper_id, yahoo_def_id, sleeper_id),
            )
            inserted += cur.rowcount
        cur.execute("SELECT count(*) FROM public.player_id_xwalk WHERE position='DEF'")
        total = cur.fetchone()[0]
    conn.commit()
    print(
        f"\n=== Step 3: DEF crosswalk rows — inserted {inserted}, total {total}/32 ==="
    )
    return total


def step4_sanity_correlation(conn) -> float | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) FROM scoring.projection_points pp
            JOIN public.player_id_xwalk x ON x.sleeper_id = pp.player_ref
            WHERE pp.source='sleeper' AND pp.horizon='season' AND x.position='DEF'
            """
        )
        if cur.fetchone()[0] == 0:
            print(
                "\n=== Step 4: sanity correlation — SKIPPED (no DEF season "
                "projections scored yet; run scripts/score_sleeper_projections.py) ==="
            )
            return None
        cur.execute(
            """
            SELECT x.team, pp.points::float AS proj_2026,
                   sum(pwp.points::float) AS actual_2025
            FROM scoring.projection_points pp
            JOIN public.player_id_xwalk x ON x.sleeper_id = pp.player_ref
            JOIN public.team_def_map m ON m.team_abbr = x.team
            JOIN scoring.player_week_points pwp
                ON pwp.player_ref = m.yahoo_def_id AND pwp.source = 'yahoo_engine'
                   AND pwp.season = 2025
            WHERE pp.source = 'sleeper' AND pp.horizon = 'season' AND x.position = 'DEF'
              AND pp.snapshot_id = (
                  SELECT max(snapshot_id) FROM raw.sleeper_projections WHERE week IS NULL
              )
            GROUP BY x.team, pp.points
            """
        )
        rows = cur.fetchall()
    if len(rows) < 10:
        print(
            f"\n=== Step 4: sanity correlation — only {len(rows)} DEF matched; skipping ==="
        )
        return None
    proj = [r[1] for r in rows]
    actual = [r[2] for r in rows]
    rho, pval = spearmanr(proj, actual)
    print(
        f"\n=== Step 4: Spearman(2026 projected, 2025 actual) over {len(rows)} DEF "
        f"= {rho:.3f} (p={pval:.3f}) ==="
    )
    return rho


def main():
    conn = connect()
    step1_enumerate_keys(conn)
    results = step2_reconstruct(conn)
    passing = print_reconstruction_table(results)
    if passing < RECONSTRUCTION_GATE:
        print(
            f"\nBLOCKED: only {passing}/{len(results)} teams reconstructed within "
            f"{RECONSTRUCTION_TOLERANCE:.0%} (gate: >= {RECONSTRUCTION_GATE}/32). "
            "Bucket semantics NOT confirmed — do not guess a mapping. See "
            "docs/research/2026-07-10-dst-semantics.md for the residual table."
        )
        sys.exit(1)
    print(f"\nCONFIRMED: {passing}/{len(results)} >= gate — semantics verified.")
    step3_insert_xwalk(conn)
    rho = step4_sanity_correlation(conn)
    if rho is not None and rho <= SPEARMAN_GATE:
        print(
            f"\nBLOCKED: Spearman {rho:.3f} <= {SPEARMAN_GATE} gate — investigate "
            "for a mapping bug before trusting DEF valuation."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
