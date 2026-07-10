#!/usr/bin/env python3
"""R16 validation: impute FD from Sleeper's own projected volumes and compare
against Sleeper's NATIVE FD projections, player by player. >15% divergence on
meaningful volume = investigate (report lists them); never silently prefer
either source."""
import datetime
import pathlib
import statistics

from ffi.db import connect
from ffi.scoring.fd_impute import fit_fd_rates, impute_fd

conn = connect()
rates = fit_fd_rates(conn, seasons=[2019, 2020, 2021, 2022, 2023, 2024, 2025])

with conn.cursor() as cur:
    cur.execute(
        """SELECT payload FROM raw.sleeper_projections
           WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1"""
    )
    row = cur.fetchone()
    if row is None:
        raise SystemExit("no season-level sleeper snapshot (Task 7 Step 1)")
    payload = row[0]
    # sleeper_id -> gsis_id for player-level rates
    cur.execute(
        "SELECT sleeper_id, gsis_id FROM public.player_id_xwalk WHERE sleeper_id IS NOT NULL"
    )
    sleeper_to_gsis = dict(cur.fetchall())

rows = []
for rec in payload:
    pos = (rec.get("player") or {}).get("position")
    if pos not in ("QB", "RB", "WR", "TE"):
        continue
    s = rec["stats"]
    native = {
        "rush": s.get("rush_fd"),
        "rec": s.get("rec_fd"),
        "pass": s.get("pass_fd"),
    }
    imputed = impute_fd(
        rates,
        pos,
        sleeper_to_gsis.get(str(rec["player_id"])),
        carries=float(s.get("rush_att", 0)),
        receptions=float(s.get("rec", 0)),
        completions=float(s.get("pass_cmp", 0)),
    )
    for kind, nat_key, imp_key in (
        ("rush", "rush", "rush_first_downs"),
        ("rec", "rec", "rec_first_downs"),
        ("pass", "pass", "pass_first_downs"),
    ):
        nat = native[nat_key]
        imp = imputed[imp_key]
        if nat is None or float(nat) < 10:  # only meaningful volume
            continue
        rows.append(
            {
                "name": f"{(rec['player'] or {}).get('first_name','?')} {(rec['player'] or {}).get('last_name','?')}",
                "pos": pos,
                "kind": kind,
                "native": float(nat),
                "imputed": imp,
                "pct": abs(imp - float(nat)) / float(nat),
            }
        )

if not rows:
    raise SystemExit(
        "no comparable FD rows — snapshot empty or volume filter too strict"
    )
pcts = [r["pct"] for r in rows]
median = statistics.median(pcts)
over15 = [r for r in rows if r["pct"] > 0.15]
today = datetime.date.today().isoformat()
report = pathlib.Path(f"docs/research/{today}-fd-imputation-divergence.md")
lines = [
    f"# FD imputation vs Sleeper native — {today}",
    f"\ncompared: {len(rows)} (player, kind) pairs with native FD >= 10",
    f"\nmedian divergence = {median:.1%}; over-15% pairs = {len(over15)} ({len(over15)/len(rows):.1%})",
    "\nMethod: see ffi/scoring/fd_impute.py docstring (pooled position rates 2019-2025 + "
    "empirical-Bayes player rates, k=50/30/100).",
    "\n## Pairs over the 15% investigation threshold\n",
    "| player | pos | kind | native | imputed | div% |",
    "|---|---|---|---|---|---|",
    *[
        f"| {r['name']} | {r['pos']} | {r['kind']} | {r['native']:.0f} | {r['imputed']:.1f} | {r['pct']:.0%} |"
        for r in sorted(over15, key=lambda r: -r["pct"])[:60]
    ],
]
report.write_text("\n".join(lines) + "\n")
print(f"median={median:.1%}, over-15%={len(over15)}/{len(rows)} -> {report}")
if median > 0.15:
    # Gate's failure was adjudicated 2026-07-09 (controller-decided design
    # amendment concluding Task 8): re-verified against nflverse 2019-2025
    # ground truth, this method's fitted rates are correct (they reproduce
    # known real-world FD conversion rates and are internally consistent by
    # construction). The divergence is diagnostic of a Sleeper-side native-FD
    # data-quality problem instead (native_fd exceeds native_volume for 53% of
    # rec pairs / 96% of pass pairs — mathematically impossible), so Sleeper's
    # native FD was rejected as a scoring input rather than this method being
    # revised. See docs/research/2026-07-09-fd-imputation-divergence.md
    # ("Resolution" section) and sleeper_adapter.py's _IGNORED_EXACT comment.
    # The gate itself is intentionally left in place — it documents the
    # finding and should keep firing on a re-run; it is not a bug to "fix."
    raise SystemExit(
        "MEDIAN divergence >15% — the imputation method itself is off (R16): "
        "investigate rate pooling/shrinkage before FP FD-imputation is trusted."
    )
