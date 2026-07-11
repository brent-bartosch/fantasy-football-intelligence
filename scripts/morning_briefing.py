#!/usr/bin/env python3
"""Morning briefing v1: health header (THE dashboard — ADR Domain 5), data
vintages, FP budget, top board movements. Exits nonzero if any health item is
red, so launchd surfaces failure (fail-loud)."""
import datetime
import pathlib
import subprocess
import sys

from ffi.db import connect
from ffi.ingest.fantasypros import fp_calls_today
from ffi.signals_apply import CUMULATIVE_CAP, cumulative_pct

STALE_HOURS = 36  # ADR Domain 2: draft board refuses stale sources; briefing flags at the same line

conn = connect()
today = datetime.date.today().isoformat()
red_flags = []
L = [f"# Morning briefing — {today}", "\n## Health"]

with conn.cursor() as cur:
    cur.execute(
        """SELECT DISTINCT ON (source) source, status,
                  round(extract(epoch FROM now() - started_at) / 3600) AS age_h, error
           FROM raw.ingest_runs ORDER BY source, started_at DESC"""
    )
    for source, status, age_h, error in cur.fetchall():
        mark = "OK" if status == "success" else "RED"
        if status != "success":
            red_flags.append(f"{source} latest run {status}: {error}")
        L.append(f"- [{mark}] {source}: last run {int(age_h)}h ago ({status})")

    cur.execute(
        """SELECT max(fetched_at) FROM raw.sleeper_projections WHERE week IS NULL"""
    )
    latest = cur.fetchone()[0]
    if latest is None:
        red_flags.append("no season-level sleeper snapshot at all")
        L.append("- [RED] sleeper season snapshot: MISSING")
    else:
        age = (
            datetime.datetime.now(datetime.timezone.utc) - latest
        ).total_seconds() / 3600
        mark = "OK" if age <= STALE_HOURS else "STALE"
        if age > STALE_HOURS:
            red_flags.append(
                f"sleeper season snapshot {age:.0f}h old (> {STALE_HOURS}h)"
            )
        L.append(f"- [{mark}] sleeper season snapshot: {age:.0f}h old")

L.append(f"- FP budget used today: {fp_calls_today(conn)}/30")

# NOTE: backups are plain pg_dump (`.sql.gz`) written by scripts/backup_db.sh,
# not custom-format `.dump` files — glob matches the actual on-disk naming.
backups = (
    sorted(pathlib.Path("backups").glob("fantasy_football_*.sql.gz"))
    if pathlib.Path("backups").exists()
    else []
)
if backups:
    age_d = (
        datetime.datetime.now()
        - datetime.datetime.fromtimestamp(backups[-1].stat().st_mtime)
    ).days
    mark = "OK" if age_d <= 2 else "STALE"
    if age_d > 2:
        red_flags.append(f"newest backup {age_d}d old")
    L.append(f"- [{mark}] newest pg_dump: {backups[-1].name} ({age_d}d old)")
else:
    red_flags.append("no backups found in backups/")
    L.append("- [RED] backups: none found")

health = subprocess.run(
    [sys.executable, "scripts/phase1_report.py"], capture_output=True, text=True
)
fails = [ln for ln in health.stdout.splitlines() if ln.startswith("FAIL")]
L.append(
    f"- structural health gate: {'OK' if health.returncode == 0 else 'RED'}"
    + (f" — {len(fails)} failing: " + "; ".join(fails) if fails else "")
)
if health.returncode != 0:
    red_flags.extend(fails)

L.append("\n## Board inputs")
with conn.cursor() as cur:
    cur.execute(
        """SELECT x.name, v.position, round(v.vorp, 1)
           FROM valuation.player_value v JOIN public.player_id_xwalk x USING (xwalk_id)
           WHERE v.scenario = 'qb_hoard_12'
             AND v.computed_at = (SELECT max(computed_at) FROM valuation.player_value WHERE scenario='qb_hoard_12')
           ORDER BY v.vorp DESC LIMIT 15"""
    )
    rows = cur.fetchall()
if rows:
    L += ["Top 15 by VORP (qb_hoard_12):", *(f"- {n} ({p}): {v}" for n, p, v in rows)]
else:
    L.append("- valuation not yet built today")

# --- Signals (Task 15: human confirm gate) --------------------------------
# Informational only -- this section never touches red_flags/exit code
# (ADR D2/D4 health semantics are unchanged by Task 15).
L.append("\n## Signals")
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM signals.signals WHERE status = 'pending'")
    pending_count = cur.fetchone()[0]
    cur.execute(
        """SELECT title FROM signals.signals WHERE status = 'pending'
           ORDER BY fetched_at DESC LIMIT 5"""
    )
    top_titles = [r[0] for r in cur.fetchall()]
L.append(f"- pending signals: {pending_count}")
if top_titles:
    L += ["Top pending (most recent):", *(f"- {t}" for t in top_titles)]

with conn.cursor() as cur:
    cur.execute(
        """SELECT x.name, a.pct, s.title, s.evidence_url
           FROM signals.adjustments a
           JOIN signals.signals s USING (signal_id)
           JOIN public.player_id_xwalk x ON x.xwalk_id = a.xwalk_id
           WHERE a.applied_at::date = current_date - 1
           ORDER BY a.applied_at"""
    )
    yesterday_adj = cur.fetchall()
if yesterday_adj:
    L.append("Applied yesterday:")
    L += [
        f"- {name}: {pct:+.1%} -- {title} ({url})"
        for name, pct, title, url in yesterday_adj
    ]
else:
    L.append("- no adjustments applied yesterday")

cum = cumulative_pct(conn)
if cum:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT xwalk_id, name FROM public.player_id_xwalk WHERE xwalk_id = ANY(%s)",
            (list(cum.keys()),),
        )
        names = dict(cur.fetchall())
    L.append("Cumulative-cap utilization:")
    L += [
        f"- {names.get(xid, xid)}: {pct:+.1%} ({abs(pct) / CUMULATIVE_CAP:.0%} of ±{CUMULATIVE_CAP:.0%} cap)"
        for xid, pct in sorted(cum.items(), key=lambda kv: -abs(kv[1]))
    ]

out_dir = pathlib.Path("reports")
out_dir.mkdir(exist_ok=True)
out = out_dir / f"briefing-{today}.md"
out.write_text("\n".join(L) + "\n")
print(f"-> {out}")
if red_flags:
    print("RED FLAGS:", *red_flags, sep="\n  - ")
    raise SystemExit(1)
