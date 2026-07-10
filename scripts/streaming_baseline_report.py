#!/usr/bin/env python3
"""Draft-early vs stream for DEF and K under OUR scoring (2025 season).
Elite = top-3 by season total (hindsight proxy for 'the DEF you'd draft early'
— stated caveat: real drafting can't pick the top-3 in advance, so this is an
UPPER BOUND on drafting's edge).
Streaming baselines from each week's rank distribution among ranks 13-32
(the un-rostered pool in a 12-team league):
  perfect  = best available (rank 13)   — upper bound on streaming
  realistic = median of ranks 13-20     — a decent-process streamer
If even elite-upper-bound minus realistic-streamer is small, streaming wins.

ONE SEASON OF EVIDENCE (2025 only). Extending to 2024 is a possible follow-up
a user can approve (~40 more throttled Yahoo calls) — not done here, not a
default.
"""
import datetime
import pathlib
import statistics

from ffi.db import connect

conn = connect()
out_lines = [f"# DEF/K: draft early vs stream — {datetime.date.today().isoformat()}"]
out_lines += [
    "",
    "**Evidence base: one season (2025) only.** Extending this analysis to "
    "2024 is a possible follow-up (~40 more throttled Yahoo calls) that "
    "requires explicit user approval — it is not done here and not assumed "
    "as a default next step.",
    "",
    "**Verdict rule:** elite (top-3 season-total hindsight, an UPPER BOUND "
    "on what real in-season drafting could capture — no one drafts knowing "
    "in advance who finishes top-3) minus the realistic streamer (median of "
    "weekly ranks 13-20 in the un-rostered pool) is compared to a 1.5 "
    "pts/wk threshold. If elite minus realistic is below that threshold, "
    "STREAM wins — because elite is already the best case for drafting, so "
    "a small edge there means the realistic (non-hindsight) edge is smaller "
    "still, or negative.",
]

for pos in ("DEF", "K"):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.week, v.player_name, s.points::float
            FROM scoring.player_week_points s
            JOIN public.v_player_yahoo_ids v ON v.yahoo_id = s.player_ref
            WHERE s.source='yahoo_engine' AND s.season=2025 AND v.position=%s
            """,
            (pos,),
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"no scored {pos} weeks — Task 12 Steps 1-2 incomplete")
    by_week: dict[int, list[tuple[str, float]]] = {}
    totals: dict[str, float] = {}
    for wk, name, pts in rows:
        by_week.setdefault(wk, []).append((name, pts))
        totals[name] = totals.get(name, 0.0) + pts
    n_pool = statistics.median(len(v) for v in by_week.values())
    elite3 = sorted(totals, key=lambda n: -totals[n])[:3]
    elite_weekly = statistics.mean(
        pts
        for wk, entries in by_week.items()
        for name, pts in entries
        if name in elite3
    )
    perfect, realistic = [], []
    for wk, entries in sorted(by_week.items()):
        ranked = sorted((p for _, p in entries), reverse=True)
        if len(ranked) < 20:
            raise SystemExit(
                f"{pos} week {wk}: only {len(ranked)} scored — pool incomplete, backfill"
            )
        perfect.append(ranked[12])  # rank 13
        realistic.append(statistics.median(ranked[12:20]))  # ranks 13-20
    e, pf, re_ = elite_weekly, statistics.mean(perfect), statistics.mean(realistic)
    verdict = "DRAFT EARLY" if e - re_ >= 1.5 else "STREAM"
    out_lines += [
        f"\n## {pos} (weekly pool ~{int(n_pool)} scored)",
        f"- elite (top-3 hindsight, upper bound): **{e:.2f} pts/wk** ({', '.join(elite3)})",
        f"- perfect streamer (best available): {pf:.2f} pts/wk",
        f"- realistic streamer (median of ranks 13-20): {re_:.2f} pts/wk",
        f"- elite minus realistic streamer: **{e - re_:+.2f} pts/wk** "
        f"(x14 regular-season weeks = {(e - re_) * 14:+.1f} pts/season)",
        f"- **Verdict: {verdict}** (threshold 1.5 pts/wk; elite is an upper bound, "
        "so a small edge here means streaming wins in practice)",
    ]

out = pathlib.Path(
    f"docs/research/{datetime.date.today().isoformat()}-def-k-streaming-baseline.md"
)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(out_lines) + "\n")
print(f"-> {out}")
