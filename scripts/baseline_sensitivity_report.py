#!/usr/bin/env python3
"""R16: is the board ORDERING stable across QB-hoarding scenarios? Reports
per-scenario QB baselines, top-24 overlap, and rank churn of the top 50.
High churn = the hoarding assumption is load-bearing -> flag it prominently."""
import datetime
import pathlib

from ffi.db import connect

conn = connect()
with conn.cursor() as cur:
    cur.execute(
        """SELECT scenario, position, replacement_rank, round(replacement_points,1)
           FROM valuation.replacement_baseline ORDER BY scenario, position"""
    )
    baselines = cur.fetchall()
    cur.execute(
        """SELECT scenario, x.name, row_number() OVER (PARTITION BY scenario ORDER BY vorp DESC) rk
           FROM valuation.player_value v JOIN public.player_id_xwalk x USING (xwalk_id)"""
    )
    ranks: dict[str, dict[str, int]] = {}
    for scen, name, rk in cur.fetchall():
        ranks.setdefault(scen, {})[name] = rk

scens = sorted(ranks)
pairs = [(a, b) for i, a in enumerate(scens) for b in scens[i + 1 :]]
lines = [
    f"# 2QB baseline sensitivity — {datetime.date.today().isoformat()}",
    "\n## Baselines\n",
    "| scenario | pos | repl rank | repl pts |",
    "|---|---|---|---|",
    *[f"| {s} | {p} | {r} | {pts} |" for s, p, r, pts in baselines],
    "\n## Ordering stability\n",
]
for a, b in pairs:
    top24_a = {n for n, r in ranks[a].items() if r <= 24}
    top24_b = {n for n, r in ranks[b].items() if r <= 24}
    overlap = len(top24_a & top24_b)
    churn = sorted(
        (
            (n, ranks[a][n], ranks[b][n])
            for n in set(ranks[a]) & set(ranks[b])
            if ranks[a][n] <= 50 or ranks[b][n] <= 50
        ),
        key=lambda t: -abs(t[1] - t[2]),
    )[:10]
    lines += [
        f"### {a} vs {b}: top-24 overlap {overlap}/24",
        "| player | rank A | rank B |",
        "|---|---|---|",
        *[f"| {n} | {ra} | {rb} |" for n, ra, rb in churn],
        "",
    ]
out = pathlib.Path(
    f"docs/research/{datetime.date.today().isoformat()}-baseline-sensitivity.md"
)
out.write_text("\n".join(lines) + "\n")
print(f"-> {out}")
