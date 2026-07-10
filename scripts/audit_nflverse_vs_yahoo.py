#!/usr/bin/env python3
"""2025 sanity triangle: nflverse-scored points vs Yahoo's official points for
crosswalked players. Expected divergence sources are exactly KNOWN_GAPS +
FD-definition differences; anything larger is a bug. Writes a dated report."""
import datetime
import pathlib
import statistics

from ffi.db import connect
from ffi.scoring.nflverse_adapter import KNOWN_GAPS

conn = connect()
with conn.cursor() as cur:
    cur.execute(
        """
        SELECT v.player_name, n.week, n.points AS nfl_pts, y.points AS yahoo_pts,
               (n.points - y.points) AS diff
        FROM scoring.player_week_points n
        JOIN public.player_id_xwalk x ON x.gsis_id = n.player_ref
        JOIN scoring.player_week_points y
          ON y.source = 'yahoo_engine' AND y.player_ref = x.yahoo_id
         AND y.season = n.season AND y.week = n.week AND y.config_version = n.config_version
        JOIN public.v_player_yahoo_ids v ON v.yahoo_id = x.yahoo_id
        WHERE n.source = 'nflverse' AND n.season = 2025
        """
    )
    rows = cur.fetchall()
if not rows:
    raise SystemExit(
        "no joined rows — crosswalk or scoring tables empty; fix before auditing"
    )

diffs = [float(r[4]) for r in rows]
abs_diffs = sorted(abs(d) for d in diffs)
median = statistics.median(abs_diffs)
p95 = abs_diffs[int(0.95 * len(abs_diffs))]
worst = sorted(rows, key=lambda r: -abs(float(r[4])))[:30]

today = datetime.date.today().isoformat()
report = pathlib.Path(f"docs/research/{today}-nflverse-scoring-divergence.md")
lines = [
    f"# nflverse-vs-Yahoo scoring divergence — {today}",
    f"\nJoined 2025 player-weeks: {len(rows)}",
    f"\nmedian |diff| = {median:.3f}   p95 |diff| = {p95:.3f}",
    "\nKnown structural gaps (nflverse_adapter.KNOWN_GAPS):",
    *[f"- `{k}`: {v}" for k, v in KNOWN_GAPS.items()],
    "\n## 30 largest divergences\n",
    "| player | week | nflverse | yahoo | diff |",
    "|---|---|---|---|---|",
    *[f"| {n} | {w} | {a} | {b} | {d} |" for n, w, a, b, d in worst],
]
report.write_text("\n".join(lines) + "\n")
print(f"median |diff|={median:.3f} p95={p95:.3f} -> {report}")
if median > 0.5 or p95 > 3.0:
    raise SystemExit(
        f"divergence above expectation (median>{0.5} or p95>{3.0}) — investigate the "
        "top-30 table before building on nflverse-scored history (R16 discipline)."
    )
