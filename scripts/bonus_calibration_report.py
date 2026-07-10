#!/usr/bin/env python3
"""Calibration (R16): for 2023-2025, predict each player-season's weekly bonus
hit rates from (season mean weekly yards, CV fitted on OTHER seasons), then
compare predicted vs actual hit frequencies in predicted-probability bins.
Also scores mean-pricing (the naive competitor) for the same weeks via Brier.
In-sample simplification (season mean as the 'projection') is documented in
the report header — this validates the DISTRIBUTION SHAPE, not projection skill."""
import datetime
import pathlib
from collections import defaultdict

from ffi.db import connect
from ffi.scoring.bonus_pricing import estimate_weekly_cv, weekly_threshold_prob
from ffi.scoring.config import load_config_v1

EVAL_SEASONS = [2023, 2024, 2025]
FIT_SEASONS = [2019, 2020, 2021, 2022]
STATS = {
    "rush_yards": "rushing_yards",
    "rec_yards": "receiving_yards",
    "pass_yards": "passing_yards",
}

conn = connect()
cfg = load_config_v1()
cvs = estimate_weekly_cv(conn, FIT_SEASONS)

bins = defaultdict(lambda: [0, 0.0, 0])  # bin -> [n, sum_pred, sum_actual]
brier_dist, brier_mean, n_obs = 0.0, 0.0, 0
for stat, col in STATS.items():
    tiers = cfg.offense.yardage_bonuses[stat]
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT gsis_id, max(position), season, avg({col}),
                       array_agg({col}) AS weeks
                FROM raw.nflverse_player_week
                WHERE season = ANY(%s) AND {col} > 0
                GROUP BY gsis_id, season HAVING count(*) >= 8""",
            (EVAL_SEASONS,),
        )
        for gsis, pos, season, mean, weeks in cur.fetchall():
            cv = cvs["players"].get(gsis, {}).get(stat) or cvs["positions"].get(
                pos, {}
            ).get(stat)
            if cv is None:
                continue
            for t in tiers:
                pred = weekly_threshold_prob(float(mean), cv, t.threshold)
                naive = 1.0 if float(mean) >= t.threshold else 0.0
                for y in weeks:
                    actual = 1.0 if float(y) >= t.threshold else 0.0
                    b = min(int(pred * 10), 9)
                    bins[b][0] += 1
                    bins[b][1] += pred
                    bins[b][2] += actual
                    brier_dist += (pred - actual) ** 2
                    brier_mean += (naive - actual) ** 2
                    n_obs += 1

if n_obs == 0:
    raise SystemExit("no calibration observations — check data load")
today = datetime.date.today().isoformat()
report = pathlib.Path(f"docs/research/{today}-bonus-calibration.md")
rows = []
for b in sorted(bins):
    n, sp, sa = bins[b]
    rows.append(f"| {b/10:.1f}-{(b+1)/10:.1f} | {n} | {sp/n:.3f} | {sa/n:.3f} |")
report.write_text(
    "\n".join(
        [
            f"# Threshold-bonus calibration — {today}",
            f"\nobs={n_obs} (player-week × tier), eval {EVAL_SEASONS}, CV fit {FIT_SEASONS}",
            "\nCaveat: season-mean-as-projection is in-sample for the mean; this validates the",
            "distribution SHAPE around a known mean, not projection accuracy.",
            f"\n**Brier (gamma model) = {brier_dist/n_obs:.4f}  vs  Brier (mean-pricing) = {brier_mean/n_obs:.4f}**",
            "\n| predicted-P bin | n | mean predicted | actual freq |",
            "|---|---|---|---|",
            *rows,
        ]
    )
    + "\n"
)
print(
    f"Brier gamma={brier_dist/n_obs:.4f} vs mean-pricing={brier_mean/n_obs:.4f} -> {report}"
)
if brier_dist >= brier_mean:
    raise SystemExit(
        "distribution pricing did NOT beat mean-pricing — R16 red flag; "
        "do not wire bonus_ev into valuation until resolved."
    )
