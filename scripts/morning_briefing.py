#!/usr/bin/env python3
"""Morning briefing v2: health header (THE dashboard — ADR Domain 5), data
vintages, artifact freshness, archive continuity, FP budget, top board
movements. Exits nonzero if any health item is red, so launchd surfaces
failure (fail-loud).

v2 changes (ADR Domain 5, Preconditions P1/P2):
  1. Health is age-aware and three-state via ffi.health.state(). The v1 loop
     tested `status == 'success'` and never `age_h` — on the same line that
     computed and printed `age_h` — which is why a 1209h-stale nflverse
     ingest rendered [OK] for 50 days (R1).
  2. Artifact-freshness assertions: a missed Monday claims brief / Tuesday
     trends report is now distinguishable from an unread one (R13).
  3. Archive-continuity: distinct archived days in raw.sleeper_trending vs
     days elapsed, with every gap listed. A gap there is unrecoverable (R8).
  4. Expected-season assertion for the nflverse feeds — a feed that is
     current but pointed at last season is R1 wearing a different hat.

Everything below the helpers runs under main(); importing this module must
not connect to a database or write a report.
"""
import datetime
import fnmatch
import pathlib
import subprocess
import sys
from zoneinfo import ZoneInfo

from ffi import health
from ffi.db import connect
from ffi.ingest.fantasypros import fp_calls_today
from ffi.signals_apply import CUMULATIVE_CAP, cumulative_pct

# NOTE: the ffi.joblock import and the advisory-lock acquisition are added in
# Task 11, which creates src/ffi/joblock.py. Do not add them here — the module
# does not exist yet and this file must import cleanly at the end of Task 4.

LEAGUE_TZ = ZoneInfo("America/Los_Angeles")
# Paths resolve from the file, not the cwd: launchd runs this job from `/`.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "reports"
BACKUPS_DIR = REPO_ROOT / "backups"
CLOCK_PATH = REPO_ROOT / "config" / "source_clock.yaml"
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

# Plan 1 deadline rule: a due artifact must have been written since local
# midnight of its due day. Plan 2 replaces this with
# ffi.league_state.clock.deadline(event, week), the only sanctioned reader of
# config/league_clock.yaml — this module must not read that file (ARCHITECTURE §1c).


def _mark(state: health.SourceState) -> str:
    return {
        health.SourceState.OK: "OK",
        health.SourceState.KNOWN_LAGGING: "LAG",
        health.SourceState.BROKEN: "RED",
    }[state]


def artifact_freshness_lines(clock, now_local, reports_dir):
    """(render_lines, red_flags) for every artifact contract due today.

    Before an artifact's `active_from` the line renders PENDING and never
    reds: asserting on a report whose renderer has not shipped is the banner
    fatigue R12 warns about, not observability.
    """
    lines, reds = [], []
    today_name = WEEKDAYS[now_local.weekday()]
    midnight = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    for name, contract in sorted(clock.artifacts.items()):
        if contract.due_weekday != today_name:
            continue
        if now_local.date() < contract.active_from:
            lines.append(
                f"- [PENDING] {name} report: assertion active from {contract.active_from}"
            )
            continue
        pattern = pathlib.PurePath(contract.glob).name
        found = sorted(
            p for p in reports_dir.glob("*") if fnmatch.fnmatch(p.name, pattern)
        )
        if not found:
            lines.append(f"- [RED] {name} report: absent (due {today_name})")
            reds.append(
                f"{name} report absent — due {today_name}, nothing matches {contract.glob}"
            )
            continue
        newest = max(found, key=lambda p: p.stat().st_mtime)
        written = datetime.datetime.fromtimestamp(newest.stat().st_mtime, tz=LEAGUE_TZ)
        if written < midnight:
            lines.append(
                f"- [RED] {name} report: {newest.name} stale (written {written:%Y-%m-%d %H:%M})"
            )
            reds.append(
                f"{name} report stale — newest is {newest.name} written "
                f"{written:%Y-%m-%d %H:%M}, before today's deadline"
            )
        else:
            lines.append(f"- [OK] {name} report: {newest.name} ({written:%H:%M})")
    return lines, reds


def archive_continuity_lines(conn, today_local):
    """(render_lines, red_flags) for the P2 Sleeper trending archive.

    A gap in this table cannot be backfilled from any source (R8), so every
    missing day is listed by date rather than summarized as a count. One row
    per direction per day is a complete day: the 100-player server cap lives
    inside `payload`, and Task 1's sanity gate is what polices it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT min(archive_date), count(DISTINCT archive_date) FROM raw.sleeper_trending"
        )
        first_day, distinct_days = cur.fetchone()
    if first_day is None:
        return (
            ["- [RED] trending archive: no rows at all"],
            ["raw.sleeper_trending has no rows — the P2 archive is not running"],
        )
    expected = (today_local - first_day).days + 1
    with conn.cursor() as cur:
        cur.execute(
            """SELECT d::date FROM generate_series(%s::date, %s::date, interval '1 day') d
               WHERE NOT EXISTS (
                   SELECT 1 FROM raw.sleeper_trending t WHERE t.archive_date = d::date)
               ORDER BY d""",
            (first_day, today_local),
        )
        gaps = [r[0].isoformat() for r in cur.fetchall()]
    if gaps:
        return (
            [
                f"- [RED] trending archive: {distinct_days}/{expected} days since {first_day} "
                f"— {len(gaps)} gap(s): {', '.join(gaps)}"
            ],
            [f"trending archive gaps (unrecoverable): {', '.join(gaps)}"],
        )
    return (
        [f"- [OK] trending archive: {distinct_days}/{expected} days since {first_day}"],
        [],
    )


def expected_season_lines(conn, clock):
    """(render_lines, red_flags): is each nflverse feed loaded for the season
    its contract says it should be? A feed that is fresh but pointed at last
    season is R1 in a different costume."""
    lines, reds = [], []
    for source, table in (
        ("nflverse_player_week", "raw.nflverse_player_week"),
        ("nflverse_snap_counts", "raw.nflverse_snap_counts"),
    ):
        expected = clock.contract(source).expected_season
        if expected is None:
            continue
        with conn.cursor() as cur:
            # raw.nflverse_snap_counts does not exist until Task 7. Probing
            # first keeps a missing table a RED LINE rather than an
            # UndefinedTable that aborts the transaction and kills the whole
            # briefing — a crashed briefing is a silent one.
            cur.execute("SELECT to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                lines.append(f"- [RED] {source}: table {table} does not exist")
                reds.append(f"{source}: table {table} does not exist")
                continue
            cur.execute(
                f"SELECT max(season), max(week) FROM {table} WHERE season = %s",
                (expected,),
            )
            season, week = cur.fetchone()
        if season is None:
            lines.append(f"- [RED] {source}: no rows for expected season {expected}")
            reds.append(f"{source} has no rows for expected_season {expected}")
        else:
            lines.append(f"- [OK] {source}: season {season} through week {week}")
    return lines, reds


def structural_gate_lines():
    """(render_lines, red_flags) from scripts/phase1_report.py. Isolated so a
    test can render a full briefing without shelling out."""
    gate = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "phase1_report.py")],
        capture_output=True,
        text=True,
    )
    fails = [ln for ln in gate.stdout.splitlines() if ln.startswith("FAIL")]
    line = f"- structural health gate: {'OK' if gate.returncode == 0 else 'RED'}" + (
        f" — {len(fails)} failing: " + "; ".join(fails) if fails else ""
    )
    return [line], (fails if gate.returncode != 0 else [])


def health_lines(conn, clock):
    """(render_lines, red_flags) for every ingest source + the sleeper season
    snapshot. This loop is where R1 lived: it now derives the mark from
    (status, age_h) together via health.state()."""
    lines, reds = [], []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT ON (source) source, status,
                      extract(epoch FROM now() - started_at) / 3600 AS age_h, error
               FROM raw.ingest_runs ORDER BY source, started_at DESC"""
        )
        for source, status, age_h, error in cur.fetchall():
            try:
                st = health.state(source, status, age_h, clock)
            except health.UnknownSourceError:
                # Fail loud, but never by silencing the dashboard: an
                # unregistered source is itself the finding.
                reds.append(f"{source}: no entry in config/source_clock.yaml")
                lines.append(
                    f"- [RED] {source}: unregistered source, {float(age_h):.0f}h ago ({status})"
                )
                continue
            if health.is_alarming(st):
                reds.append(
                    f"{source} is {st.value}: {float(age_h):.0f}h old, "
                    f"status={status}, error={error}"
                )
            lines.append(
                f"- [{_mark(st)}] {source}: last run {float(age_h):.0f}h ago ({status})"
            )

        cur.execute(
            "SELECT max(fetched_at) FROM raw.sleeper_projections WHERE week IS NULL"
        )
        latest = cur.fetchone()[0]
    if latest is None:
        reds.append("no season-level sleeper snapshot at all")
        lines.append("- [RED] sleeper season snapshot: MISSING")
    else:
        age = (
            datetime.datetime.now(datetime.timezone.utc) - latest
        ).total_seconds() / 3600
        st = health.state("sleeper_projections", "success", age, clock)
        if health.is_alarming(st):
            reds.append(f"sleeper season snapshot {age:.0f}h old ({st.value})")
        lines.append(f"- [{_mark(st)}] sleeper season snapshot: {age:.0f}h old")
    return lines, reds


def backup_lines(clock, now_local):
    """(render_lines, red_flags) for the newest pg_dump.

    NOTE: backups are plain pg_dump (`.sql.gz`) written by scripts/backup_db.sh,
    not custom-format `.dump` files — glob matches the actual on-disk naming.
    """
    backups = (
        sorted(BACKUPS_DIR.glob("fantasy_football_*.sql.gz"))
        if BACKUPS_DIR.exists()
        else []
    )
    if not backups:
        return ["- [RED] backups: none found"], ["no backups found in backups/"]
    written = datetime.datetime.fromtimestamp(backups[-1].stat().st_mtime, tz=LEAGUE_TZ)
    age_h = (now_local - written).total_seconds() / 3600
    st = health.state("backup", "success", age_h, clock)
    reds = (
        [f"newest backup {age_h:.0f}h old ({st.value})"]
        if health.is_alarming(st)
        else []
    )
    return (
        [f"- [{_mark(st)}] newest pg_dump: {backups[-1].name} ({age_h:.0f}h old)"],
        reds,
    )


def build_briefing(conn, clock, now_local, reports_dir):
    """(render_lines, red_flags) for the whole briefing.

    Everything an operator reads is assembled here so the render path itself
    is testable: R1 was a render-loop bug, and a unit test on state() alone
    would not have caught it.
    """
    today = now_local.date().isoformat()
    red_flags = []
    L = [
        f"# Morning briefing — {today}",
        f"\n## Health (source_clock as_of {clock.as_of})",
    ]
    for lines, reds in (health_lines(conn, clock), expected_season_lines(conn, clock)):
        L += lines
        red_flags += reds

    L.append("\n## Artifacts")
    art_lines, art_reds = artifact_freshness_lines(clock, now_local, reports_dir)
    L += art_lines or ["- no decision artifact due today"]
    red_flags += art_reds

    arch_lines, arch_reds = archive_continuity_lines(conn, now_local.date())
    L += arch_lines
    red_flags += arch_reds

    L.append(f"- FP budget used today: {fp_calls_today(conn)}/30")

    for lines, reds in (backup_lines(clock, now_local), structural_gate_lines()):
        L += lines
        red_flags += reds

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
        L += [
            "Top 15 by VORP (qb_hoard_12):",
            *(f"- {n} ({p}): {v}" for n, p, v in rows),
        ]
    else:
        L.append("- valuation not yet built today")

    # --- Signals (Task 15: human confirm gate) ----------------------------
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
    return L, red_flags


def main() -> None:
    conn = connect()
    # Load the clock ONCE: state(clock=None) re-parses the YAML on every call.
    clock = health.load_clock(CLOCK_PATH)
    now_local = datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ)
    L, red_flags = build_briefing(conn, clock, now_local, REPORTS_DIR)

    REPORTS_DIR.mkdir(exist_ok=True)
    out = REPORTS_DIR / f"briefing-{now_local.date().isoformat()}.md"
    out.write_text("\n".join(L) + "\n")
    print(f"-> {out}")
    if red_flags:
        print("RED FLAGS:", *red_flags, sep="\n  - ")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
