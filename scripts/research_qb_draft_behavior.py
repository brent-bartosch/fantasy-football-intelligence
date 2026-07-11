#!/usr/bin/env python3
"""QB draft-behavior study (2026-07-10): (1) is there QB-run contagion beyond
round-level QB density? (2) how well did the room's QB draft order predict
end-of-season finish? Deterministic (seeded permutation null); read-only.
Documented in docs/research/2026-07-10-qb-run-contagion.md — regenerate that
doc's tables by re-running this script."""
from collections import defaultdict

import numpy as np
from scipy.stats import spearmanr

from ffi.db import connect

BANDS = {"R1-3": (1, 3), "R4-8": (4, 8), "R9+": (9, 19)}
N_PERMUTATIONS = 500
SEED = 17


def qb_streaks(seq: list[bool]) -> list[int]:
    lengths, i = [], 0
    while i < len(seq):
        if seq[i]:
            j = i
            while j < len(seq) and seq[j]:
                j += 1
            lengths.append(j - i)
            i = j
        else:
            i += 1
    return lengths


def contagion_study(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT s.season, dp.overall_pick, dp.round_number, p.position
               FROM draft_picks dp
               JOIN players p ON p.player_id = dp.player_id
               JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
               WHERE dp.round_number BETWEEN 1 AND 19
               ORDER BY s.season, dp.overall_pick"""
        )
        rows = cur.fetchall()
    drafts: dict = defaultdict(list)
    for season, overall, rnd, pos in rows:
        drafts[season].append((overall, rnd, pos))

    stats = {
        b: [0, 0, 0, 0] for b in BANDS
    }  # qb_after_qb, after_qb, qb_after_non, after_non
    all_lengths: list[int] = []
    for picks in drafts.values():
        picks.sort()
        seq = [(rnd, pos == "QB") for _, rnd, pos in picks]
        all_lengths.extend(qb_streaks([q for _, q in seq]))
        for t in range(1, len(seq)):
            rnd, is_qb = seq[t]
            band = next(b for b, (lo, hi) in BANDS.items() if lo <= rnd <= hi)
            if seq[t - 1][1]:
                stats[band][1] += 1
                stats[band][0] += is_qb
            else:
                stats[band][3] += 1
                stats[band][2] += is_qb

    n_picks = sum(len(p) for p in drafts.values())
    print(
        f"1) QB pick probability conditional on previous pick ({len(drafts)} seasons, {n_picks} picks):"
    )
    for b in BANDS:
        a, n1, c, n2 = stats[b]
        p1, p2 = a / n1, c / n2
        print(
            f"   {b:5s}: after-QB {p1:.3f} (n={n1})  after-nonQB {p2:.3f} (n={n2})  raw lift x{p1 / p2:.2f}"
        )

    rl = np.array(all_lengths)
    obs3 = int((rl >= 3).sum())
    print(
        f"\n2) QB streaks: {len(rl)} runs; >=2: {(rl >= 2).sum()}, >=3: {obs3}, >=4: {(rl >= 4).sum()}, max {rl.max()}"
    )

    # Null: shuffle positions WITHIN each round of each draft — preserves every
    # round's QB density (the structural driver) while destroying pick-to-pick
    # contagion. Excess observed streaks vs this null = contagion evidence.
    rng = np.random.default_rng(SEED)
    null_ge3 = []
    for _ in range(N_PERMUTATIONS):
        tot3 = 0
        for picks in drafts.values():
            byround: dict = defaultdict(list)
            for _, rnd, pos in picks:
                byround[rnd].append(pos == "QB")
            seq = []
            for rnd in sorted(byround):
                v = byround[rnd]
                rng.shuffle(v)
                seq.extend(v)
            tot3 += sum(1 for L in qb_streaks(seq) if L >= 3)
        null_ge3.append(tot3)
    null = np.array(null_ge3)
    print(
        f"3) Runs of >=3 QBs: observed {obs3} vs within-round-shuffle null "
        f"{null.mean():.1f} ± {null.std():.1f}  (p ~ {(null >= obs3).mean():.3f})"
    )


def draft_order_vs_finish(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """WITH qbs AS (
                 SELECT s.season, p.yahoo_player_id, p.player_name,
                        row_number() OVER (PARTITION BY s.season ORDER BY dp.overall_pick) AS qb_draft_rank
                 FROM draft_picks dp
                 JOIN players p ON p.player_id = dp.player_id
                 JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
                 WHERE p.position = 'QB' AND s.season BETWEEN 2019 AND 2025
               ),
               pts AS (
                 SELECT x.yahoo_id, pw.season, sum(pw.points) AS season_pts
                 FROM scoring.player_week_points pw
                 JOIN public.player_id_xwalk x ON x.gsis_id = pw.player_ref
                 WHERE pw.source = 'nflverse' AND pw.week BETWEEN 1 AND 14
                 GROUP BY 1, 2
               )
               SELECT q.season, q.qb_draft_rank, q.player_name, coalesce(pts.season_pts, 0)::float
               FROM qbs q
               LEFT JOIN pts ON pts.yahoo_id = split_part(q.yahoo_player_id, '.p.', 2)
                            AND pts.season = q.season
               ORDER BY q.season, q.qb_draft_rank"""
        )
        rows = cur.fetchall()
    by_season: dict = defaultdict(list)
    for season, drank, name, pts in rows:
        by_season[season].append((drank, pts, name))

    print(
        "\n4) QB draft order vs end-of-season league points (weeks 1-14), drafted QBs only:"
    )
    print(
        "   season  n_QBs  spearman  top-5-drafted-still-top-5  busts (top-8 drafted, finished >QB16)"
    )
    for season in sorted(by_season):
        d = by_season[season]
        rho, _ = spearmanr([x[0] for x in d], [x[1] for x in d])
        finish_rank = {
            name: i + 1 for i, (_, _, name) in enumerate(sorted(d, key=lambda x: -x[1]))
        }
        top5_hold = sum(
            1 for drank, _, name in d if drank <= 5 and finish_rank[name] <= 5
        )
        busts = [name for drank, _, name in d if drank <= 8 and finish_rank[name] > 16]
        print(
            f"   {season}   {len(d):4d}   {-rho:6.3f}       {top5_hold}/5              {', '.join(busts) if busts else '-'}"
        )


if __name__ == "__main__":
    conn = connect()
    contagion_study(conn)
    draft_order_vs_finish(conn)
