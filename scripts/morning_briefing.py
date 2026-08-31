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
import pathlib
import subprocess
import sys

from ffi import health
from ffi.db import connect
from ffi.ingest.fantasypros import fp_calls_today

# The health-section renderers live in src/ffi/reports/health_section.py: this
# file is at its ARCHITECTURE §1b budget and they are the part with no
# dashboard-specific glue. Re-imported here so `morning_briefing.X` stays the
# render entry point.
from ffi.reports.health_section import (
    LEAGUE_TZ,
    archive_continuity_lines,
    artifact_freshness_lines,
    expected_season_lines,
    mark,
)
from ffi.signals_apply import CUMULATIVE_CAP, cumulative_pct

# NOTE: the ffi.joblock import and the advisory-lock acquisition are added in
# Task 11, which creates src/ffi/joblock.py. Do not add them here — the module
# does not exist yet and this file must import cleanly at the end of Task 4.

# Paths resolve from the file, not the cwd: launchd runs this job from `/`.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS_DIR = REPO_ROOT / "reports"
BACKUPS_DIR = REPO_ROOT / "backups"
CLOCK_PATH = REPO_ROOT / "config" / "source_clock.yaml"

# Plan 1 deadline rule: a due artifact must have been written since local
# midnight of its due day. Plan 2 replaces this with
# ffi.league_state.clock.deadline(event, week), the only sanctioned reader of
# config/league_clock.yaml — this module must not read that file (ARCHITECTURE §1c).


def structural_gate_lines():
    """(render_lines, red_flags) from scripts/phase1_report.py. Isolated so a
    test can render a full briefing without shelling out.

    A nonzero exit ALWAYS produces a red flag, even when the gate printed no
    FAIL lines (crash, traceback, import error). Otherwise the briefing would
    render the RED line and still exit 0 — the RED mark and the exit code must
    never disagree. A hung gate is the same failure with a longer fuse, so the
    call is bounded and a timeout is itself a red flag.
    """
    try:
        gate = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "phase1_report.py")],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return (
            ["- structural health gate: RED — timed out after 120s"],
            ["phase1_report.py timed out after 120s (no structural gate result)"],
        )
    fails = [ln for ln in gate.stdout.splitlines() if ln.startswith("FAIL")]
    line = f"- structural health gate: {'OK' if gate.returncode == 0 else 'RED'}" + (
        f" — {len(fails)} failing: " + "; ".join(fails) if fails else ""
    )
    if gate.returncode == 0:
        return [line], []
    return [line], (
        fails
        or [
            f"phase1_report.py exited {gate.returncode} with no FAIL lines: "
            f"{gate.stderr.strip()[:200]}"
        ]
    )


def health_lines(conn, clock):
    """(render_lines, red_flags, states) for every ingest source + the sleeper
    season snapshot. This loop is where R1 lived: it now derives the mark from
    (status, age_h) together via health.state().

    `states` maps source -> SourceState so downstream sections (the
    expected-season assertions) cannot render [OK] for a source this loop
    already marked BROKEN.
    """
    lines, reds, states = [], [], {}
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
                states[source] = health.SourceState.BROKEN
                continue
            except ValueError as exc:
                # Negative age_h: clock skew or a future-dated started_at. Fail
                # loud on the dashboard rather than crashing the whole render.
                reds.append(f"{source}: unusable age from health.state() — {exc}")
                lines.append(f"- [RED] {source}: unusable age ({exc})")
                states[source] = health.SourceState.BROKEN
                continue
            states[source] = st
            if health.is_alarming(st):
                reds.append(
                    f"{source} is {st.value}: {float(age_h):.0f}h old, "
                    f"status={status}, error={error}"
                )
            lines.append(
                f"- [{mark(st)}] {source}: last run {float(age_h):.0f}h ago ({status})"
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
        lines.append(f"- [{mark(st)}] sleeper season snapshot: {age:.0f}h old")
    return lines, reds, states


def backup_lines(clock, now_local, backups_dir=BACKUPS_DIR):
    """(render_lines, red_flags) for the newest pg_dump.

    NOTE: backups are plain pg_dump (`.sql.gz`) written by scripts/backup_db.sh,
    not custom-format `.dump` files — glob matches the actual on-disk naming.
    """
    backups = (
        sorted(backups_dir.glob("fantasy_football_*.sql.gz"))
        if backups_dir.exists()
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
        [f"- [{mark(st)}] newest pg_dump: {backups[-1].name} ({age_h:.0f}h old)"],
        reds,
    )


def build_briefing(conn, clock, now_local, reports_dir, backups_dir=BACKUPS_DIR):
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
    h_lines, h_reds, states = health_lines(conn, clock)
    season_lines, season_reds = expected_season_lines(conn, clock, states)
    L += h_lines + season_lines
    red_flags += h_reds + season_reds

    L.append("\n## Artifacts")
    art_lines, art_reds = artifact_freshness_lines(clock, now_local, reports_dir)
    L += art_lines or ["- no decision artifact due today"]
    red_flags += art_reds

    arch_lines, arch_reds = archive_continuity_lines(conn, now_local.date())
    L += arch_lines
    red_flags += arch_reds

    L.append(f"- FP budget used today: {fp_calls_today(conn)}/30")

    for lines, reds in (
        backup_lines(clock, now_local, backups_dir),
        structural_gate_lines(),
    ):
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
