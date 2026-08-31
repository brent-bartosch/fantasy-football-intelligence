"""Task 4: the briefing's health/artifact/continuity assertions.

The unit tests below cover the three pure helpers, but the test that actually
guards R1 is `test_broken_source_renders_red_in_the_briefing_text`: R1 lived in
the *render loop*, not in `state()`, so a green `ffi.health` unit suite proves
nothing about what the operator reads at 07:00.
"""

import datetime
import os
import pathlib
import subprocess
import sys

import pytest

# tests/conftest.py already puts scripts/ on sys.path.
import morning_briefing as mb
from ffi.health import load_clock

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CLOCK = load_clock(REPO_ROOT / "config" / "source_clock.yaml")
TZ = datetime.timezone(datetime.timedelta(hours=-7))  # America/Los_Angeles, PDT


def test_artifact_assertion_is_pending_before_active_from(tmp_path):
    # 2026-09-01 is a Tuesday, before the trends renderer ships (2026-09-15).
    now = datetime.datetime(2026, 9, 1, 7, 0, tzinfo=TZ)
    lines, reds = mb.artifact_freshness_lines(CLOCK, now, tmp_path)
    assert reds == []
    assert any("PENDING" in ln and "trends" in ln for ln in lines)


def test_artifact_assertion_reds_when_due_and_absent(tmp_path):
    # 2026-09-22 is a Tuesday, after active_from, with no reports/ artifact.
    now = datetime.datetime(2026, 9, 22, 7, 0, tzinfo=TZ)
    lines, reds = mb.artifact_freshness_lines(CLOCK, now, tmp_path)
    assert any("trends" in r and "absent" in r for r in reds)
    assert any("[RED]" in ln and "trends" in ln for ln in lines)


def test_artifact_assertion_reds_when_present_but_stale(tmp_path):
    now = datetime.datetime(2026, 9, 22, 7, 0, tzinfo=TZ)
    stale = tmp_path / "trends-2026-38.md"
    stale.write_text("old\n")
    yesterday = datetime.datetime(2026, 9, 21, 6, 0, tzinfo=TZ).timestamp()
    os.utime(stale, (yesterday, yesterday))
    lines, reds = mb.artifact_freshness_lines(CLOCK, now, tmp_path)
    assert any("stale" in r for r in reds)


def test_artifact_assertion_ok_when_present_and_fresh(tmp_path):
    now = datetime.datetime(2026, 9, 22, 7, 0, tzinfo=TZ)
    fresh = tmp_path / "trends-2026-39.md"
    fresh.write_text("new\n")
    written = datetime.datetime(2026, 9, 22, 5, 30, tzinfo=TZ).timestamp()
    os.utime(fresh, (written, written))
    lines, reds = mb.artifact_freshness_lines(CLOCK, now, tmp_path)
    assert reds == []
    assert any("[OK]" in ln and "trends" in ln for ln in lines)


def test_artifact_not_due_today_is_silent(tmp_path):
    # 2026-09-23 is a Wednesday: neither artifact is due.
    now = datetime.datetime(2026, 9, 23, 7, 0, tzinfo=TZ)
    lines, reds = mb.artifact_freshness_lines(CLOCK, now, tmp_path)
    assert reds == []
    assert all("[RED]" not in ln for ln in lines)


def _tmp_backups(tmp_path, now):
    """A throwaway backups/ holding one dump written an hour before `now`.

    The e2e tests must not glob the repo's real `backups/`: on a machine where
    the daily dump is stale, every briefing assertion in this file gets an
    extra red flag it never asked for, and on a machine with no backups at all
    the tests assert around "- [RED] backups: none found". Hermetic beats
    incidental.
    """
    d = tmp_path / "backups"
    d.mkdir()
    dump = d / "fantasy_football_20260922_060000.sql.gz"
    dump.write_bytes(b"")
    ts = (now - datetime.timedelta(hours=1)).timestamp()
    os.utime(dump, (ts, ts))
    return d


def _seed_trending(db, days: list[datetime.date]) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.ingest_runs (source, status) VALUES ('sleeper_trending','success') "
            "RETURNING run_id"
        )
        run_id = cur.fetchone()[0]
        for d in days:
            for t in ("add", "drop"):
                cur.execute(
                    "INSERT INTO raw.sleeper_trending "
                    "(run_id, archive_date, trend_type, lookback_hours, payload) "
                    "VALUES (%s,%s,%s,24,'[]'::jsonb)",
                    (run_id, d, t),
                )
    db.commit()


def test_archive_continuity_reports_no_gaps(db):
    today = datetime.date(2026, 9, 10)
    _seed_trending(db, [today - datetime.timedelta(days=i) for i in range(4)])
    lines, reds = mb.archive_continuity_lines(db, today)
    assert reds == []
    assert any("4/4 days" in ln for ln in lines)


def test_archive_continuity_lists_gaps_and_reds(db):
    today = datetime.date(2026, 9, 10)
    _seed_trending(db, [today - datetime.timedelta(days=i) for i in (0, 1, 3)])
    lines, reds = mb.archive_continuity_lines(db, today)
    assert any("2026-09-08" in r for r in reds)
    assert any("[RED]" in ln for ln in lines)


def test_archive_continuity_reds_when_empty(db):
    lines, reds = mb.archive_continuity_lines(db, datetime.date(2026, 9, 10))
    assert any("no rows" in r for r in reds)


def test_expected_season_reds_when_table_is_absent(db):
    """raw.nflverse_snap_counts does not exist until Task 7.

    A missing table must render as a RED line, not blow up mid-briefing with
    UndefinedTable and abort the transaction — a crashed briefing is a silent
    briefing, which is the R1 failure mode with extra steps.
    """
    with db.cursor() as cur:
        cur.execute("SELECT to_regclass('raw.nflverse_snap_counts')")
        if cur.fetchone()[0] is not None:
            pytest.skip("Task 7 has created raw.nflverse_snap_counts")
    lines, reds = mb.expected_season_lines(db, CLOCK)
    assert any("nflverse_snap_counts" in r for r in reds)
    assert any("[RED]" in ln and "nflverse_snap_counts" in ln for ln in lines)


def test_expected_season_reds_when_season_missing(db):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.nflverse_player_week (gsis_id, season, week) "
            "VALUES ('00-0000001', 2024, 3)"
        )
    db.commit()
    lines, reds = mb.expected_season_lines(db, CLOCK)
    # Contract says expected_season 2025; only 2024 rows exist.
    assert any("nflverse_player_week" in r and "2025" in r for r in reds)


def test_broken_source_renders_red_in_the_briefing_text(db, tmp_path, monkeypatch):
    """R1 end-to-end: a stale-but-successful run must render RED in the text
    the operator actually reads.

    Task 3's reviewer established that R1 lived in the render loop, not in
    `state()` — so this asserts on the rendered briefing body, not on a helper
    return value.

    The 2025 row is what makes the negative assertion mean something: without
    it `expected_season_lines` reds for "no rows for expected season 2025" and
    `"[OK] nflverse_player_week" not in text` passes for the wrong reason. With
    it, the season line has real data behind it and only the health-state
    suppression keeps it from rendering [OK] beside a [RED] staleness line.
    """
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.ingest_runs (source, status, started_at) VALUES "
            "('nflverse_player_week','success', now() - interval '1209 hours')"
        )
        cur.execute(
            "INSERT INTO raw.nflverse_player_week (gsis_id, season, week) "
            "VALUES ('00-0000002', 2025, 7)"
        )
    db.commit()
    # The structural gate shells out to scripts/phase1_report.py against the
    # production database; stub it so this test stays hermetic.
    monkeypatch.setattr(mb, "structural_gate_lines", lambda: ([], []))
    now = datetime.datetime(2026, 9, 23, 7, 0, tzinfo=TZ)
    lines, reds = mb.build_briefing(
        db, CLOCK, now, tmp_path, _tmp_backups(tmp_path, now)
    )
    text = "\n".join(lines)
    assert "[RED] nflverse_player_week: last run 1209h ago (success)" in text
    # The season line exists (2025 rows are loaded) and is RED, not OK.
    assert "[RED] nflverse_player_week: season 2025 through week 7" in text
    assert "[OK] nflverse_player_week" not in text
    assert any("nflverse_player_week is BROKEN" in r for r in reds)
    assert not any("backup" in r for r in reds)


def test_unregistered_source_is_red_not_silent(db, tmp_path, monkeypatch):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.ingest_runs (source, status) VALUES ('yahoo_probe','success')"
        )
    db.commit()
    monkeypatch.setattr(mb, "structural_gate_lines", lambda: ([], []))
    now = datetime.datetime(2026, 9, 23, 7, 0, tzinfo=TZ)
    lines, reds = mb.build_briefing(
        db, CLOCK, now, tmp_path, _tmp_backups(tmp_path, now)
    )
    assert any("[RED] yahoo_probe" in ln and "unregistered" in ln for ln in lines)
    assert any("yahoo_probe" in r and "source_clock.yaml" in r for r in reds)


def _briefing_snapshot() -> set:
    return {
        (p.name, p.stat().st_mtime_ns)
        for p in (REPO_ROOT / "reports").glob("briefing-*.md")
    }


def test_importing_the_briefing_writes_nothing():
    """Importing the module must not connect, render, or write a report.

    This used to `importlib.reload(mb)` in-process, which could not fail: the
    module was already imported by the test session, any connection would land
    on the same live DB the suite is already using, and a rewritten
    briefing-YYYY-MM-DD.md of the same name compared equal by name. Now it
    runs a FRESH interpreter with the DB_*/PG* environment poisoned, so an
    import-time `connect()` is a hard error and a nonzero exit, and diffs
    reports/ by (name, mtime_ns) so an in-place rewrite is caught too.
    """
    before = _briefing_snapshot()
    env = {
        **os.environ,
        # ffi.db reads DB_*; PG* are libpq's fallbacks. Poison both, and point
        # at a closed port so a connection attempt fails fast instead of
        # hanging this test out to the 60s timeout.
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "1",
        "DB_NAME": "ffi_no_such_db",
        "DB_USER": "ffi_no_such_user",
        "PGHOST": "127.0.0.1",
        "PGPORT": "1",
        "PGDATABASE": "ffi_no_such_db",
        "PGUSER": "ffi_no_such_user",
        "PGCONNECT_TIMEOUT": "2",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import morning_briefing"],
        cwd=REPO_ROOT / "scripts",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"import had side effects or failed:\n{proc.stderr}"
    assert _briefing_snapshot() == before
    assert callable(mb.main)


def test_poisoned_env_actually_breaks_a_connection():
    """The guard above is only meaningful if the poisoned env really does make
    `ffi.db.connect()` raise — otherwise it is a test that cannot fail."""
    env = {
        **os.environ,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "1",
        "PGCONNECT_TIMEOUT": "2",
    }
    proc = subprocess.run(
        [sys.executable, "-c", "from ffi.db import connect; connect()"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0


def test_structural_gate_reds_on_nonzero_exit_with_no_fail_lines(monkeypatch):
    """A gate that crashes prints a traceback on stderr, not FAIL lines. The
    briefing must still red — the RED line and the exit code cannot disagree."""
    monkeypatch.setattr(
        mb.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout="", stderr="Traceback: psycopg2.OperationalError\n"
        ),
    )
    lines, reds = mb.structural_gate_lines()
    assert any("RED" in ln for ln in lines)
    assert reds and "exited 1 with no FAIL lines" in reds[0]
    assert "psycopg2.OperationalError" in reds[0]


def test_structural_gate_timeout_is_a_red_flag(monkeypatch):
    def _boom(*a, **k):
        assert k.get("timeout") == 120, "the gate call must be time-bounded"
        raise subprocess.TimeoutExpired(cmd=a[0], timeout=120)

    monkeypatch.setattr(mb.subprocess, "run", _boom)
    lines, reds = mb.structural_gate_lines()
    assert any("timed out" in ln and "RED" in ln for ln in lines)
    assert any("timed out" in r for r in reds)


def test_negative_age_reds_instead_of_crashing_the_render(db):
    """Clock skew / a future-dated started_at makes health.state() raise
    ValueError. That must red the source, not kill the whole briefing."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.ingest_runs (source, status, started_at) VALUES "
            "('sleeper_trending','success', now() + interval '5 hours')"
        )
    db.commit()
    lines, reds, states = mb.health_lines(db, CLOCK)
    assert any("[RED] sleeper_trending" in ln and "unusable age" in ln for ln in lines)
    assert any("sleeper_trending" in r and "unusable age" in r for r in reds)
    assert states["sleeper_trending"] is mb.health.SourceState.BROKEN


def test_expected_season_never_renders_ok_for_a_broken_source(db):
    """A source the health loop marked BROKEN must not get an [OK] season line
    two lines later — one source, one verdict."""
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.nflverse_player_week (gsis_id, season, week) "
            "VALUES ('00-0000003', 2025, 4)"
        )
    db.commit()
    ok_lines, _ = mb.expected_season_lines(db, CLOCK)
    assert any("[OK] nflverse_player_week" in ln for ln in ok_lines)
    broken_lines, _ = mb.expected_season_lines(
        db, CLOCK, {"nflverse_player_week": mb.health.SourceState.BROKEN}
    )
    assert any("[RED] nflverse_player_week: season 2025" in ln for ln in broken_lines)
    assert not any("[OK] nflverse_player_week" in ln for ln in broken_lines)
