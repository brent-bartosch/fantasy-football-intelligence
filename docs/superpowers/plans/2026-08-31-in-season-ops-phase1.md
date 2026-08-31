# In-Season Ops — Phase 1 (Preconditions + Foundations) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land ADR Preconditions P1–P4 and the Domain 1/2/5 foundations — an unrecoverable Sleeper trending archive, a scheduled and age-aware nflverse pipeline, a three-state health model wired into the briefing, semantic ingest gates, and the `usage_weekly` build + trend-rule engine — so the in-season system has a truthful dashboard and a working data spine before the 2026-09-10 kickoff.

**Architecture:** Extension, not replacement (ADR approach A). Three new leaf modules (`ffi.health`, `ffi.ingest.gates`, `ffi.joblock`) that import nothing internal, one new stage package (`ffi.usage`) that reads Postgres through `ffi.db` only, and edits to two existing job entry points (`scripts/morning_briefing.py`, `launchd/com.ffi.morning.plist`). Every new signal terminates in the morning briefing, the one artifact the operator opens daily.

**Tech Stack:** Python 3.11+, uv + `uv.lock` (frozen), Postgres 15 (localhost, via `ffi.db.connect` only), psycopg2, polars, nflreadpy, requests, PyYAML, structlog, pytest, launchd.

## Global Constraints

Every task's requirements implicitly include this section.

- **ARCHITECTURE.md budgets are BINDING.** Column 3 of ARCHITECTURE.md §1 is the hard per-file line ceiling. `src/ffi/health.py` ≤ 150, `src/ffi/ingest/gates.py` ≤ 250, `src/ffi/usage/build.py` ≤ 300, `src/ffi/usage/trends.py` ≤ 400, `src/ffi/usage/coldstart.py` ≤ 200, `src/ffi/ingest/` ≤ 400/file, `scripts/morning_briefing.py` ≤ 400, `scripts/` ≤ 600/file. When a file would exceed its budget: split before adding features. Never raise a budget to make CI pass.
- **Any ARCHITECTURE.md amendment lands in the same commit as the code that needs it.** This plan makes eight named amendments; each is an explicit step inside the task that requires it.
- **Fail loud.** No bare `except:` anywhere. No silent fallback, no default-on-missing. Gates raise; callers choose to catch. Every `try`/`except`/fallback/retry goes through the `fail-loud-error-handling` skill before it is written.
- **Postgres only via `ffi.db.connect()`.** `psycopg2.connect` / `psycopg.connect` may appear only in `src/ffi/db.py`. (Tests are exempt — `tests/conftest.py` already connects directly and is not scanned.)
- **Frozen dependencies.** `uv.lock` is frozen from Week 1 through 2026-11-22 except for security fixes. No task in this plan adds or upgrades a dependency. Verified 2026-08-31: `pyyaml`, `polars`, `nflreadpy`, `requests`, `scipy`, `structlog` are already in `pyproject.toml`.
- **tz-aware datetimes everywhere.** `datetime.now(datetime.timezone.utc)` or `ZoneInfo("America/Los_Angeles")`. Never `datetime.now()` / `date.today()` in new code.
- **Calendar.** Kickoff is 2026-09-10 (Thursday after Labor Day). Plan 1 must be executable this week. The Sunday–Tuesday no-deploy freeze starts Week 1 (first frozen window: 2026-09-13 through 2026-09-15). Changes land Wednesday–Saturday after that.
- **Every task ends with the three CI guards + pytest, then a conventional commit.** The guard command is:
  ```bash
  bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
  ```
- **Migrations are append-only numbered SQL** in `migrations/NNN_name.sql`, idempotent (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`), applied to `fantasy_football` by hand and to `fantasy_football_test` automatically by `tests/conftest.py`'s `db` fixture. Next free number is **009**.

## Scope

**In scope (Plan 1 of 4):** ADR Preconditions P1, P2, P4 and the operator-blocked half of P3; ADR Domain 1 (health states, ingest gates, partial-publish guard), Domain 2 (raw archive, `usage_weekly`, league-clock config skeleton, advisory lock), Domain 5 (briefing health, artifact freshness, archive continuity).

**Deliberately excluded (later plans):**
- `src/ffi/waiver/*` (cutline, priority, clear_time, ledger, cards) — Plan 3.
- `src/ffi/reports/*` renderers, `scripts/run_claims_brief.py`, `scripts/run_trends_report.py`, `com.ffi.claims` / `com.ffi.trends` plists — Plan 2.
- `src/ffi/league_state/*` including `clock.py` (the only sanctioned reader of `config/league_clock.yaml`), `scripts/probe_yahoo_access.py` — Plan 2.
- `src/ffi/trade_angles.py` — Plan 4.
- `ffi.health.render_or_refuse` — deferred to Plan 2, where the first composite renderer exists. Building a fail-closed renderer with zero renderers to wrap is speculative (YAGNI) and would burn ~25 of `health.py`'s 150 lines.
- `src/ffi/flags.py` + `config/modules.yaml` — Plan 2, alongside the first disableable report section.
- `scripts/notify.py` / push channel — Plan 2.
- The 2025 replay precision gate, lead-time gate, ROS-mode gate — Plan 2 (this plan ships the engine and unit fixtures only).
- `src/ffi/usage/market.py` (urgency overlay over the archive) — Plan 2; it needs ≥4 weeks of the P2 archive before its thresholds can be set (ADR TBD 3).

**Known limitations shipped deliberately** (verified against live data on 2026-08-31, recorded here so no later reader mistakes them for oversights):
- `raw.nflverse_player_week` has **no** snap, route, or red-zone columns. Task 7 adds a snap-counts feed because `snap_share` is the input to the headline trend rule. `route_share` (needs `load_participation`, nflverse coverage ends 2023) and `rz_touches` (needs `load_pbp`) stay **NULL**, and their rules are auto-disabled by the `requires` mechanism with a loud briefing line (R27: degrade by removal, never by silent null). Filling them is a named Plan 2 task.
- `nflreadpy.load_player_stats(seasons=[2026])` 404s and `nflreadpy.load_snap_counts` raises `ValueError: Season must be between 2012 and 2025` until `get_current_season()` rolls over on **2026-09-10** (Thursday after Labor Day — read from the installed source). The chain therefore runs `--seasons 2025` until the operator flips it; Task 4's `expected_season` health line goes RED if they forget. No dependency upgrade is needed.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `migrations/009_sleeper_trending.sql` | `raw.sleeper_trending` immutable dated snapshots |
| `migrations/010_ingest_run_status.sql` | Widen `raw.ingest_runs.status` CHECK with `sanity_warned` / `sanity_failed` |
| `migrations/011_nflverse_snap_counts.sql` | `raw.nflverse_snap_counts` |
| `migrations/012_usage_weekly.sql` | `public.usage_weekly` |
| `src/ffi/ingest/sleeper_trending.py` | Sleeper trending add+drop ingester |
| `scripts/ingest_sleeper_trending.py` | P2 entry point for `com.ffi.trending` |
| `launchd/com.ffi.trending.plist` | Daily 06:30 trending archive job |
| `scripts/morning_chain.sh` | `com.ffi.morning` chain body — `;`-separated with a trap (R23) |
| `src/ffi/health.py` | `SourceState` three-state model + source/artifact contracts |
| `config/source_clock.yaml` | Per-source cadence contracts + artifact freshness contracts |
| `config/league_clock.yaml` | League clock skeleton with UNSET/UNVERIFIED markers |
| `scripts/validate_league_clock.py` | Fails if an UNSET/UNVERIFIED field is consumed downstream |
| `src/ffi/ingest/gates.py` | Field-set, rank-correlation, coverage gates |
| `src/ffi/ingest/nflverse_snaps.py` | Snap-counts ingester with pfr_id→gsis_id resolution |
| `scripts/ingest_nflverse_snaps.py` | Snap-counts entry point |
| `src/ffi/usage/__init__.py` | Shared value types for the usage package |
| `src/ffi/usage/build.py` | `usage_weekly` builder + partial-week guard |
| `src/ffi/usage/trends.py` | Standard rule engine + `classify` |
| `src/ffi/usage/coldstart.py` | Weeks 1–3 rule variants |
| `scripts/waiver_ceiling_test.py` | P4 perfect-foresight vs do-nothing harness |
| `src/ffi/joblock.py` | `pg_try_advisory_lock` wrapper |
| `scripts/pre-commit.sh` | Versioned pre-commit body (hook sources it) |
| `tests/test_sleeper_trending_ingest.py`, `tests/test_health.py`, `tests/test_ingest_gates.py`, `tests/test_nflverse_snaps.py`, `tests/test_usage_build.py`, `tests/test_usage_trends.py`, `tests/test_waiver_ceiling.py`, `tests/test_joblock.py`, `tests/test_validate_league_clock.py` | Unit + regression suites |

**Modified:**

| Path | Change |
|---|---|
| `src/ffi/ingest/base.py` | `sanity_mode` hook + `sanity_failed` / `sanity_warned` run statuses |
| `src/ffi/ingest/nflverse.py` | Add shared `parse_seasons()` |
| `scripts/ingest_nflverse.py` | Use `parse_seasons()` |
| `src/ffi/ingest/sleeper.py` | Hard-fail sanity gate wiring |
| `scripts/morning_briefing.py` | health.state(), artifact freshness, archive continuity, expected-season line, advisory lock |
| `scripts/build_valuation.py` | Advisory lock around the DELETE+INSERT rebuild |
| `launchd/com.ffi.morning.plist` | Point at `scripts/morning_chain.sh` |
| `scripts/backup_db.sh` | Offsite sync line |
| `.git/hooks/pre-commit` | Source `scripts/pre-commit.sh` |
| `ARCHITECTURE.md` | Eight named amendments (§1b, §1c, §2, §4) |

---

### Task 1: P2 — Sleeper trending archive (ships first; it is the only decision that expires)

Every day without this archive is validation data that can never be recovered (R8, L9×I6=54). Nothing else in this plan is allowed to merge ahead of it.

**Files:**
- Create: `migrations/009_sleeper_trending.sql`
- Create: `src/ffi/ingest/sleeper_trending.py`
- Create: `scripts/ingest_sleeper_trending.py`
- Create: `launchd/com.ffi.trending.plist`
- Create: `tests/test_sleeper_trending_ingest.py`
- Modify: `ARCHITECTURE.md` (§1b module-list row rename)

**Interfaces:**
- Consumes: `ffi.ingest.base.BaseIngester` (`source: str`, `fetch()`, `validate(payload) -> int`, `store(conn, run_id, payload) -> None`, `run(conn) -> int`), `ffi.ingest.base.IngestError`, `ffi.db.connect()`.
- Produces: `ffi.ingest.sleeper_trending.SleeperTrendingIngester(lookback_hours: int = 24, limit: int = 200)` with `source = "sleeper_trending"`; `fetch() -> dict[str, list[dict]]` keyed `"add"` / `"drop"`; table `raw.sleeper_trending(snapshot_id, run_id, archive_date, trend_type, lookback_hours, fetched_at, payload)`. Task 4 reads `archive_date` for continuity; Task 6 sets `sanity_mode = "warn"` on this class.

- [ ] **Step 1: Write the migration**

Create `migrations/009_sleeper_trending.sql`:

```sql
-- 009_sleeper_trending.sql — ADR Precondition P2 (R8).
--
-- Sleeper's trending endpoint is a rolling 24h window with NO historical
-- access: a day not archived is a day of validation data that can never be
-- recovered. Rows are immutable dated snapshots — one row per
-- (archive_date, trend_type) on a normal day.
--
-- Deliberately NOT UNIQUE on (archive_date, trend_type): a retry after a
-- partially-failed run must be able to land, and silently overwriting a
-- day's snapshot would destroy the very thing this table exists to
-- preserve. Continuity is measured with COUNT(DISTINCT archive_date)
-- (scripts/morning_briefing.py), which is insensitive to duplicates.
CREATE TABLE IF NOT EXISTS raw.sleeper_trending (
    snapshot_id    bigserial PRIMARY KEY,
    run_id         integer REFERENCES raw.ingest_runs(run_id),
    archive_date   date NOT NULL,
    trend_type     text NOT NULL CHECK (trend_type IN ('add','drop')),
    lookback_hours integer NOT NULL CHECK (lookback_hours > 0),
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    payload        jsonb NOT NULL          -- full API response, untouched
);
CREATE INDEX IF NOT EXISTS idx_sleeper_trending_day
    ON raw.sleeper_trending (archive_date DESC, trend_type);
```

- [ ] **Step 2: Apply the migration to both databases**

Run:
```bash
cd /Users/brentbartosch/Development/fantasy_football
psql -d fantasy_football -f migrations/009_sleeper_trending.sql
psql -d fantasy_football_test -f migrations/009_sleeper_trending.sql
psql -d fantasy_football -c "\d raw.sleeper_trending" | head -12
```
Expected: two `CREATE TABLE` / `CREATE INDEX` blocks, then a table description listing `snapshot_id, run_id, archive_date, trend_type, lookback_hours, fetched_at, payload`.

- [ ] **Step 3: Write the failing test**

Create `tests/test_sleeper_trending_ingest.py`:

```python
import datetime
import json

import pytest

from ffi.ingest.base import IngestError
from ffi.ingest.sleeper_trending import SleeperTrendingIngester

# Shape verified live 2026-08-31:
# GET https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=200
# -> [{"count": 488052, "player_id": "8800"}, ...]
def _payload(n: int = 120) -> dict:
    return {
        "add": [{"player_id": str(1000 + i), "count": 10_000 - i} for i in range(n)],
        "drop": [{"player_id": str(5000 + i), "count": 9_000 - i} for i in range(n)],
    }


class FixtureIngester(SleeperTrendingIngester):
    def __init__(self, payload, **kw):
        super().__init__(**kw)
        self._payload = payload

    def fetch(self):
        return self._payload


def test_validate_counts_both_directions():
    ing = FixtureIngester(_payload())
    assert ing.validate(_payload()) == 240


def test_validate_rejects_missing_direction():
    bad = _payload()
    del bad["drop"]
    with pytest.raises(IngestError, match="missing 'drop'"):
        FixtureIngester(bad).validate(bad)


def test_validate_rejects_thin_payload():
    thin = {"add": _payload(5)["add"], "drop": _payload(5)["drop"]}
    with pytest.raises(IngestError, match="only 5 rows"):
        FixtureIngester(thin).validate(thin)


def test_validate_rejects_record_shape_drift():
    drifted = _payload()
    drifted["add"][0] = {"playerId": "1000", "count": 1}
    with pytest.raises(IngestError, match="schema drift"):
        FixtureIngester(drifted).validate(drifted)


def test_run_writes_two_dated_snapshots_and_a_success_run(db):
    payload = _payload()
    run_id = FixtureIngester(payload).run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT trend_type, archive_date, lookback_hours, payload "
            "FROM raw.sleeper_trending WHERE run_id=%s ORDER BY trend_type",
            (run_id,),
        )
        rows = cur.fetchall()
        cur.execute("SELECT source, status, row_count FROM raw.ingest_runs WHERE run_id=%s", (run_id,))
        run = cur.fetchone()
    assert [r[0] for r in rows] == ["add", "drop"]
    today = datetime.datetime.now(datetime.timezone.utc).astimezone().date()
    assert {r[1] for r in rows} == {today}
    assert {r[2] for r in rows} == {24}
    assert json.loads(json.dumps(rows[0][3])) == payload["add"]
    assert run == ("sleeper_trending", "success", 240)
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `uv run pytest tests/test_sleeper_trending_ingest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.ingest.sleeper_trending'` (5 errors during collection).

- [ ] **Step 5: Write the ingester**

Create `src/ffi/ingest/sleeper_trending.py`:

```python
"""Daily archive of Sleeper's trending add/drop lists (ADR Precondition P2).

Sleeper exposes only a rolling `lookback_hours` window and has no history
endpoint (R8). This module is the sole reason a same-season market-signal
validation will be possible from ~week 6 onward, so it stores the payload
untouched, dated, and immutable — no normalization, no dedupe, no upsert.
Normalization into `public.market_trends` happens later, from these rows.
"""
import datetime
import json
from zoneinfo import ZoneInfo

import requests
import structlog

from ffi.ingest.base import BaseIngester, IngestError

log = structlog.get_logger()

BASE_URL = "https://api.sleeper.app/v1/players/nfl/trending"
TRA_TYPES = ("add", "drop")
LEAGUE_TZ = ZoneInfo("America/Los_Angeles")

# The live endpoint returns `limit` rows when healthy. A payload this thin is
# either an outage or a semantic change; either way it must not be archived
# as if it were a normal day.
MIN_ROWS_PER_DIRECTION = 50


class SleeperTrendingIngester(BaseIngester):
    source = "sleeper_trending"

    def __init__(self, lookback_hours: int = 24, limit: int = 200):
        self.lookback_hours = lookback_hours
        self.limit = limit

    def fetch(self) -> dict:
        out = {}
        for trend_type in TRA_TYPES:
            resp = requests.get(
                f"{BASE_URL}/{trend_type}",
                params={"lookback_hours": self.lookback_hours, "limit": self.limit},
                timeout=30,
            )
            resp.raise_for_status()
            out[trend_type] = resp.json()
        return out

    def validate(self, payload) -> int:
        if not isinstance(payload, dict):
            raise IngestError(
                f"sleeper_trending: expected a dict keyed by trend type, got "
                f"{type(payload).__name__}: {str(payload)[:200]}"
            )
        total = 0
        for trend_type in TRA_TYPES:
            if trend_type not in payload:
                raise IngestError(
                    f"sleeper_trending: payload missing '{trend_type}' — "
                    f"present keys: {sorted(payload)}"
                )
            rows = payload[trend_type]
            if not isinstance(rows, list):
                raise IngestError(
                    f"sleeper_trending: '{trend_type}' is {type(rows).__name__}, expected list"
                )
            if len(rows) < MIN_ROWS_PER_DIRECTION:
                raise IngestError(
                    f"sleeper_trending: '{trend_type}' returned only {len(rows)} rows "
                    f"(floor {MIN_ROWS_PER_DIRECTION}) — refusing to archive a "
                    f"degraded day as if it were normal (R8: the archive is unrecoverable)"
                )
            for rec in rows:
                if not isinstance(rec, dict) or "player_id" not in rec or "count" not in rec:
                    raise IngestError(
                        f"sleeper_trending: '{trend_type}' record missing "
                        f"player_id/count — schema drift? record: {json.dumps(rec)[:200]}"
                    )
            total += len(rows)
        return total

    def store(self, conn, run_id: int, payload) -> None:
        archive_date = datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ).date()
        with conn.cursor() as cur:
            for trend_type in TRA_TYPES:
                cur.execute(
                    "INSERT INTO raw.sleeper_trending "
                    "(run_id, archive_date, trend_type, lookback_hours, payload) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        run_id,
                        archive_date,
                        trend_type,
                        self.lookback_hours,
                        json.dumps(payload[trend_type]),
                    ),
                )
        log.info(
            "sleeper_trending.archived",
            archive_date=archive_date.isoformat(),
            add_rows=len(payload["add"]),
            drop_rows=len(payload["drop"]),
        )
```

- [ ] **Step 6: Write the entry point**

Create `scripts/ingest_sleeper_trending.py`:

```python
#!/usr/bin/env python3
"""ADR Precondition P2: archive Sleeper trending add+drop into
raw.sleeper_trending. Fail-loud; exits nonzero on any error so launchd
surfaces a missed day — the one failure this system cannot recover from."""
import argparse
import json
import sys

from ffi.db import connect
from ffi.ingest.sleeper_trending import SleeperTrendingIngester

parser = argparse.ArgumentParser()
parser.add_argument("--lookback-hours", type=int, default=24)
parser.add_argument("--limit", type=int, default=200)
parser.add_argument(
    "--inspect", action="store_true", help="print the payload head and exit (no DB write)"
)
args = parser.parse_args()

ing = SleeperTrendingIngester(lookback_hours=args.lookback_hours, limit=args.limit)
if args.inspect:
    payload = ing.fetch()
    print(json.dumps({k: v[:3] for k, v in payload.items()}, indent=2))
    sys.exit(0)
run_id = ing.run(connect())
print(f"OK run_id={run_id}")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_sleeper_trending_ingest.py -q`
Expected: `5 passed`

- [ ] **Step 8: Take the first live snapshot immediately**

Run:
```bash
uv run python scripts/ingest_sleeper_trending.py --inspect
uv run python scripts/ingest_sleeper_trending.py
psql -d fantasy_football -c "SELECT archive_date, trend_type, jsonb_array_length(payload) FROM raw.sleeper_trending ORDER BY snapshot_id"
```
Expected: the inspect call prints two 3-element lists; the ingest prints `OK run_id=<n>`; the query returns two rows for today with lengths `200` and `200`.

- [ ] **Step 9: Create the launchd job**

Create `launchd/com.ffi.trending.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ffi.trending</string>
  <key>WorkingDirectory</key><string>/Users/brentbartosch/Development/fantasy_football</string>
  <!-- Deliberately its OWN job, not a step in com.ffi.morning: the trending
       archive is the only feed whose missed day is unrecoverable (R8), so it
       must not share a failure domain with the morning chain. It runs at
       06:30, half an hour ahead of com.ffi.morning (07:00), so the day's row
       exists before the briefing's archive-continuity check reads it. -->
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>uv run python scripts/ingest_sleeper_trending.py</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>logs/launchd-trending.log</string>
  <key>StandardErrorPath</key><string>logs/launchd-trending.err</string>
</dict></plist>
```

- [ ] **Step 10: Install and verify the job**

Run:
```bash
cp launchd/com.ffi.trending.plist ~/Library/LaunchAgents/com.ffi.trending.plist
launchctl unload ~/Library/LaunchAgents/com.ffi.trending.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.ffi.trending.plist
launchctl list | grep com.ffi
```
Expected: a line `-\t0\tcom.ffi.trending` alongside the existing `com.ffi.morning` and `com.ffi.simfarm` entries.

- [ ] **Step 11: Amend ARCHITECTURE.md (amendment 1 of 8)**

In ARCHITECTURE.md §1b, replace the row

```
| `scripts/archive_sleeper_trending.py` | P2: daily `raw.sleeper_trending` archive (started before anything else) | 100 |
```

with

```
| `scripts/ingest_sleeper_trending.py` | P2: daily `raw.sleeper_trending` archive (started before anything else). Renamed from `archive_sleeper_trending.py` during writing-plans to match the repo's `scripts/ingest_*.py` convention | 100 |
```

In ARCHITECTURE.md §2, Layer 5, replace `- \`scripts/archive_sleeper_trending.py\` → \`ingest/\`, \`db\`` with `- \`scripts/ingest_sleeper_trending.py\` → \`ingest/\`, \`db\``.

- [ ] **Step 12: Run the guards and the full suite**

Run:
```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
```
Expected: three `OK:` lines (file count rises from 123 to 125), then the suite passes with no failures.

- [ ] **Step 13: Commit**

```bash
git add migrations/009_sleeper_trending.sql src/ffi/ingest/sleeper_trending.py \
        scripts/ingest_sleeper_trending.py launchd/com.ffi.trending.plist \
        tests/test_sleeper_trending_ingest.py ARCHITECTURE.md
git commit -m "feat(ingest): P2 sleeper trending archive — raw.sleeper_trending + daily launchd job

The only ADR decision that expires: Sleeper's trending endpoint is a rolling
window with no history, so every unarchived day is validation data that can
never be recovered (R8). Own plist, not a morning-chain step — the
unrecoverable feed must not share a failure domain with the chain."
```

---

### Task 2: P1 — schedule nflverse and fix the `&&` chain bug (R23)

**Files:**
- Create: `scripts/morning_chain.sh`
- Modify: `launchd/com.ffi.morning.plist`
- Modify: `src/ffi/ingest/nflverse.py` (add `parse_seasons`)
- Modify: `scripts/ingest_nflverse.py` (use `parse_seasons`)
- Test: `tests/test_nflverse_ingest.py` (extend)

**Interfaces:**
- Consumes: `ffi.ingest.nflverse.NflversePlayerWeekIngester(seasons: list[int])`.
- Produces: `ffi.ingest.nflverse.parse_seasons(spec: str) -> list[int]` — accepts `"2025"` or `"2019-2025"`, raises `ValueError` on anything else. Task 7's `scripts/ingest_nflverse_snaps.py` imports it.

- [ ] **Step 1: Write the failing test for `parse_seasons`**

Append to `tests/test_nflverse_ingest.py`:

```python
import pytest

from ffi.ingest.nflverse import parse_seasons


def test_parse_seasons_single():
    assert parse_seasons("2025") == [2025]


def test_parse_seasons_range():
    assert parse_seasons("2019-2021") == [2019, 2020, 2021]


def test_parse_seasons_rejects_inverted_range():
    with pytest.raises(ValueError, match="2025-2019"):
        parse_seasons("2025-2019")


def test_parse_seasons_rejects_garbage():
    with pytest.raises(ValueError, match="unparseable season spec"):
        parse_seasons("last-year")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_nflverse_ingest.py -q -k parse_seasons`
Expected: FAIL — `ImportError: cannot import name 'parse_seasons' from 'ffi.ingest.nflverse'`.

- [ ] **Step 3: Add `parse_seasons` to the nflverse module**

Insert into `src/ffi/ingest/nflverse.py`, immediately after the `_STAT_COLS` assignment (before `class NflversePlayerWeekIngester`):

```python
def parse_seasons(spec: str) -> list[int]:
    """'2025' -> [2025]; '2019-2021' -> [2019, 2020, 2021].

    Shared by scripts/ingest_nflverse.py and scripts/ingest_nflverse_snaps.py
    so the two feeds can never drift onto different season windows — a
    silently-narrower snap window than stat window would produce NULL
    snap_share for real players and read as a role change (R1's bug class).
    """
    text = spec.strip()
    try:
        if "-" in text:
            lo_s, hi_s = text.split("-", 1)
            lo, hi = int(lo_s), int(hi_s)
        else:
            lo = hi = int(text)
    except ValueError:
        raise ValueError(
            f"unparseable season spec {spec!r} — expected '2025' or '2019-2025'"
        ) from None
    if hi < lo:
        raise ValueError(f"inverted season range {spec!r} ({lo}-{hi}): high < low")
    return list(range(lo, hi + 1))
```

- [ ] **Step 4: Rewrite the entry point to use it**

Replace the whole body of `scripts/ingest_nflverse.py` with:

```python
#!/usr/bin/env python3
"""Load nflverse weekly player stats into raw.nflverse_player_week.

ADR Precondition P1: this script was in no plist and no crontab and had gone
1209h (50 days) stale while the briefing rendered it [OK]. It is now a step
in scripts/morning_chain.sh.
"""
import argparse

from ffi.db import connect
from ffi.ingest.nflverse import NflversePlayerWeekIngester, parse_seasons

parser = argparse.ArgumentParser()
parser.add_argument("--seasons", default="2019-2025", help="e.g. 2019-2025 or 2024")
args = parser.parse_args()
run_id = NflversePlayerWeekIngester(seasons=parse_seasons(args.seasons)).run(connect())
print(f"OK run_id={run_id}")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_nflverse_ingest.py -q`
Expected: all tests in the file pass (the four new ones plus the pre-existing suite).

- [ ] **Step 6: Write the chain script**

Create `scripts/morning_chain.sh`:

```bash
#!/usr/bin/env bash
# scripts/morning_chain.sh — the com.ffi.morning job body.
#
# R23 / ADR Domain 1+8: the previous plist chained seven commands with `&&`,
# so ANY failing step silenced scripts/morning_briefing.py — the dashboard
# whose entire job is to report that failure. An FpBudgetExceededError on a
# Sunday would take out the only artifact that could have told the operator
# the budget was exhausted.
#
# Therefore: no `&&`, no `set -e`. Steps are newline-separated (equivalent to
# `;`), each runs regardless of its predecessor, and the briefing runs last.
# The EXIT trap covers abnormal termination (SIGTERM from launchd, a step
# that kills the shell); the `briefing_ran` guard keeps the normal path
# single-render.
set +e
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# nflverse has no 2026 data until Week 1 publishes (~2026-09-16) and
# nflreadpy.get_current_season() does not roll to 2026 until 2026-09-10, so a
# `2026` request 404s / raises today. FLIP THIS TO "2025-2026" ON 2026-09-16.
# You do not have to remember: config/source_clock.yaml carries
# `expected_season` for both nflverse feeds and scripts/morning_briefing.py
# goes RED the moment the loaded season falls behind it.
FFI_NFLVERSE_SEASONS="${FFI_NFLVERSE_SEASONS:-2025}"

briefing_ran=0
briefing_rc=1
run_briefing() {
  [ "$briefing_ran" -eq 1 ] && return 0
  briefing_ran=1
  uv run python scripts/morning_briefing.py
  briefing_rc=$?
  return "$briefing_rc"
}
trap run_briefing EXIT

step() {
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) :: $*"
  "$@"
  echo "=== rc=$? :: $*"
}

step bash scripts/backup_db.sh
step uv run python scripts/ingest_sleeper.py --season 2026
step uv run python scripts/ingest_nflverse.py --seasons "$FFI_NFLVERSE_SEASONS"
step uv run python scripts/ingest_fantasypros.py --daily
step uv run python scripts/score_sleeper_projections.py
step uv run python scripts/build_valuation.py
step uv run python scripts/ingest_fp_news.py --daily

run_briefing
exit "$briefing_rc"
```

- [ ] **Step 7: Prove the chain does not stop on a failing step**

Run:
```bash
chmod +x scripts/morning_chain.sh
bash -c '
set +e
briefing_ran=0; briefing_rc=1
run_briefing() { [ "$briefing_ran" -eq 1 ] && return 0; briefing_ran=1; echo "BRIEFING RAN"; briefing_rc=0; }
trap run_briefing EXIT
step() { echo "=== $*"; "$@"; echo "=== rc=$?"; }
step false
step echo second-step-still-ran
run_briefing
exit $briefing_rc
'; echo "chain rc=$?"
```
Expected:
```
=== false
=== rc=1
=== echo second-step-still-ran
second-step-still-ran
=== rc=0
BRIEFING RAN
chain rc=0
```
This is the R23 regression proof: step 1 fails, step 2 still runs, the briefing still renders.

- [ ] **Step 8: Point the plist at the chain script**

Replace `launchd/com.ffi.morning.plist` with:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ffi.morning</string>
  <key>WorkingDirectory</key><string>/Users/brentbartosch/Development/fantasy_football</string>
  <!-- Chain body lives in scripts/morning_chain.sh: a plist string cannot be
       reviewed, tested, or diffed, and the `&&` bug that silenced the
       dashboard for 50 days (R23) lived inside exactly such a string. -->
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>bash scripts/morning_chain.sh</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>logs/launchd-morning.log</string>
  <key>StandardErrorPath</key><string>logs/launchd-morning.err</string>
</dict></plist>
```

- [ ] **Step 9: Reload the job**

Run:
```bash
cp launchd/com.ffi.morning.plist ~/Library/LaunchAgents/com.ffi.morning.plist
launchctl unload ~/Library/LaunchAgents/com.ffi.morning.plist
launchctl load ~/Library/LaunchAgents/com.ffi.morning.plist
launchctl list | grep com.ffi.morning
```
Expected: `-\t0\tcom.ffi.morning`.

- [ ] **Step 10: Run the nflverse backfill manually (the P1 deliverable)**

Run:
```bash
uv run python scripts/ingest_nflverse.py --seasons 2019-2025
psql -d fantasy_football -c "SELECT source, status, row_count, started_at FROM raw.ingest_runs WHERE source='nflverse_player_week' ORDER BY started_at DESC LIMIT 1"
```
Expected: `OK run_id=<n>`, then a row with `status = success`, `row_count` around 35,000–40,000, and `started_at` within the last few minutes. The 1209h staleness is now zero.

- [ ] **Step 11: Guards + suite, then commit**

Run:
```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
```
Expected: three `OK:` lines (126 files), suite passes.

```bash
git add scripts/morning_chain.sh launchd/com.ffi.morning.plist \
        src/ffi/ingest/nflverse.py scripts/ingest_nflverse.py tests/test_nflverse_ingest.py
git commit -m "fix(ops): P1 — schedule nflverse ingest and replace the && chain with a trapped ; chain

R1: ingest_nflverse.py was in no plist and no crontab, 1209h stale, rendered
[OK]. R23: the seven-command && chain meant any failing step silenced the
briefing that would have reported it. Chain body moves out of the plist
string into scripts/morning_chain.sh so it is reviewable and testable."
```

---

### Task 3: `src/ffi/health.py` + `config/source_clock.yaml` — the three-state model

This is the highest-value module in the plan: R1 ran green for 50 days because the health check tested `status` and never `age_h`, while computing and printing `age_h` on the same line.

**Files:**
- Create: `src/ffi/health.py`
- Create: `config/source_clock.yaml`
- Create: `tests/test_health.py`
- Modify: `ARCHITECTURE.md` (§4 signature amendment)

**Interfaces:**
- Consumes: nothing internal (Layer 0 leaf — ARCHITECTURE §2/§3 forbid `src/ffi/health.py` → `src/ffi/*`).
- Produces:
  - `SourceState` enum: `OK`, `KNOWN_LAGGING`, `BROKEN`.
  - `UnknownSourceError(KeyError)`.
  - `SourceContract(expected_interval_h: float, lag_window_h: float, deadline_h: float, owner: str, expected_season: int | None)` with property `fresh_within_h -> float`.
  - `ArtifactContract(name: str, glob: str, due_weekday: str, active_from: datetime.date)`.
  - `SourceClock(as_of: datetime.date, contracts: dict[str, SourceContract], artifacts: dict[str, ArtifactContract])` with `.contract(source) -> SourceContract`.
  - `load_clock(path: pathlib.Path | None = None) -> SourceClock`.
  - `state(source: str, status: str, age_h: float, clock: SourceClock | None = None) -> SourceState`.
  - `is_alarming(s: SourceState) -> bool`.
  - Task 4 (`scripts/morning_briefing.py`) is the only consumer in this plan.

- [ ] **Step 1: Write the config**

Create `config/source_clock.yaml`:

```yaml
# config/source_clock.yaml — per-source cadence contracts (ADR Domain 1/5).
#
# Read ONLY by src/ffi/health.py (ARCHITECTURE.md §1c). Committed and
# as_of-stamped: a source's expected cadence is a decision, not a constant
# someone can adjust in a hotfix.
#
# state() semantics, per source:
#   OK             age_h <= expected_interval_h + lag_window_h
#   KNOWN-LAGGING  <= deadline_h            (quiet timestamp line; no banner)
#   BROKEN         > deadline_h, or a non-success run status
#
# The three-state model exists because two of three sources lag structurally
# most weeks (R12): a binary DEGRADED banner is on every report by week 2 and
# a real outage hides inside it.
as_of: 2026-08-31

sources:
  sleeper_projections:
    expected_interval_h: 24
    lag_window_h: 6
    deadline_h: 48
    owner: sleeper

  sleeper_trending:
    expected_interval_h: 24
    lag_window_h: 6
    deadline_h: 48
    owner: sleeper

  nflverse_player_week:
    # nflverse publishes ~1 day after MNF, every week (R5). That lag is
    # STRUCTURAL, not a fault: it renders as a quiet line, not a banner.
    expected_interval_h: 24
    lag_window_h: 36
    deadline_h: 96
    owner: nflverse
    # FLIP TO 2026 ON 2026-09-16, together with FFI_NFLVERSE_SEASONS in
    # scripts/morning_chain.sh. Until then the briefing's expected-season
    # line is the reminder; after 2026-09-16 it is the alarm.
    expected_season: 2025

  nflverse_snap_counts:
    expected_interval_h: 24
    lag_window_h: 36
    deadline_h: 96
    owner: nflverse
    expected_season: 2025

  fp_news:
    # Declared now, consumed when scripts/ingest_fp_news.py starts writing
    # raw.ingest_runs rows (Plan 2). A source with no contract raises
    # UnknownSourceError, so the contract must exist before the rows do.
    expected_interval_h: 24
    lag_window_h: 6
    deadline_h: 48
    owner: fantasypros

  backup:
    # Not a raw.ingest_runs source: scripts/morning_briefing.py derives its
    # age from the newest backups/fantasy_football_*.sql.gz mtime. Same
    # three-state treatment so the dashboard has one vocabulary.
    expected_interval_h: 24
    lag_window_h: 12
    deadline_h: 48
    owner: operator

artifacts:
  # Artifact-freshness assertions (ADR Domain 5): a missed job must be
  # distinguishable from an unread report (R13). `active_from` is the date
  # the renderer ships (Plan 2) — before it, the briefing renders a PENDING
  # line rather than a RED one, because asserting on an artifact nobody has
  # built yet is exactly the banner fatigue R12 warns about.
  claims:
    glob: "reports/claims-*.md"
    due_weekday: monday
    active_from: 2026-09-14
  trends:
    glob: "reports/trends-*.md"
    due_weekday: tuesday
    active_from: 2026-09-15
```

- [ ] **Step 2: Write the failing tests — including the R1 regression**

Create `tests/test_health.py`:

```python
import datetime
import pathlib

import pytest

from ffi.health import (
    SourceState,
    UnknownSourceError,
    is_alarming,
    load_clock,
    state,
)

CLOCK = load_clock(pathlib.Path("config/source_clock.yaml"))


def test_r1_regression_stale_but_successful_is_not_ok():
    """THE bug this module exists to kill.

    On 2026-08-29 the briefing rendered `[OK] nflverse_player_week: last run
    1209h ago (success)`. The health loop tested `status == 'success'` and
    never tested age — on the same line that computed and printed the age.
    A successful run that is 50 days old is BROKEN, not OK.
    """
    assert state("nflverse_player_week", "success", 1209.0, CLOCK) is SourceState.BROKEN
    assert is_alarming(SourceState.BROKEN) is True


def test_fresh_success_is_ok():
    assert state("nflverse_player_week", "success", 12.0, CLOCK) is SourceState.OK


def test_structural_lag_is_known_lagging_not_alarming():
    # nflverse: OK to 60h (24 + 36), KNOWN-LAGGING to 96h. The Tuesday-after-
    # MNF publish delay must NOT produce a banner (R12).
    assert state("nflverse_player_week", "success", 72.0, CLOCK) is SourceState.KNOWN_LAGGING
    assert is_alarming(SourceState.KNOWN_LAGGING) is False


def test_deadline_boundary_is_inclusive_of_known_lagging():
    assert state("nflverse_player_week", "success", 96.0, CLOCK) is SourceState.KNOWN_LAGGING
    assert state("nflverse_player_week", "success", 96.1, CLOCK) is SourceState.BROKEN


def test_sleeper_has_a_tighter_contract_than_nflverse():
    # The old global STALE_HOURS = 36 applied one number to every source.
    assert state("sleeper_projections", "success", 40.0, CLOCK) is SourceState.KNOWN_LAGGING
    assert state("nflverse_player_week", "success", 40.0, CLOCK) is SourceState.OK


def test_failed_status_is_broken_regardless_of_age():
    assert state("sleeper_projections", "failed", 0.5, CLOCK) is SourceState.BROKEN
    assert state("sleeper_projections", "running", 0.5, CLOCK) is SourceState.BROKEN
    assert state("sleeper_projections", "sanity_failed", 0.5, CLOCK) is SourceState.BROKEN


def test_sanity_warned_is_never_better_than_known_lagging():
    # Observe-and-log mode stores the payload, so the run is not BROKEN — but
    # a gate fired, so it is never OK either.
    assert state("sleeper_trending", "sanity_warned", 1.0, CLOCK) is SourceState.KNOWN_LAGGING
    assert state("sleeper_trending", "sanity_warned", 99.0, CLOCK) is SourceState.BROKEN


def test_unknown_source_raises_rather_than_defaulting():
    with pytest.raises(UnknownSourceError, match="yahoo_probe"):
        state("yahoo_probe", "success", 1.0, CLOCK)


def test_negative_age_raises():
    with pytest.raises(ValueError, match="age_h"):
        state("sleeper_projections", "success", -1.0, CLOCK)


def test_clock_carries_as_of_and_expected_season():
    assert CLOCK.as_of == datetime.date(2026, 8, 31)
    assert CLOCK.contract("nflverse_player_week").expected_season == 2025
    assert CLOCK.contract("sleeper_projections").expected_season is None


def test_artifact_contracts_parse():
    trends = CLOCK.artifacts["trends"]
    assert trends.glob == "reports/trends-*.md"
    assert trends.due_weekday == "tuesday"
    assert trends.active_from == datetime.date(2026, 9, 15)


def test_missing_contract_field_fails_loud(tmp_path):
    bad = tmp_path / "clock.yaml"
    bad.write_text(
        "as_of: 2026-08-31\n"
        "sources:\n"
        "  x:\n"
        "    expected_interval_h: 24\n"
        "    owner: nobody\n"
        "artifacts: {}\n"
    )
    with pytest.raises(ValueError, match="lag_window_h"):
        load_clock(bad)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_health.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.health'` (collection error).

- [ ] **Step 4: Write the module**

Create `src/ffi/health.py`:

```python
"""Three-state source health (ADR Domain 1 + 5). Layer-0 leaf: imports
nothing internal, so the module that reports on every other module's state
can never be made self-referential (ARCHITECTURE §3).

R1 is the bug this exists to kill: scripts/morning_briefing.py rendered
nflverse [OK] for 50 days because the health loop tested `status` and never
`age_h` — on the same line that computed and printed `age_h`.

OK / KNOWN-LAGGING / BROKEN, not OK / DEGRADED: two of three sources lag
structurally most weeks, so a binary banner is on every report by week 2 and
a real outage hides inside it (R12). Only BROKEN is alarming.
"""
from __future__ import annotations

import datetime
import enum
import pathlib
from dataclasses import dataclass

import yaml

DEFAULT_CLOCK_PATH = pathlib.Path("config/source_clock.yaml")

_OK_STATUSES = frozenset({"success"})
_SUSPECT_STATUSES = frozenset({"sanity_warned"})
_REQUIRED_SOURCE_FIELDS = ("expected_interval_h", "lag_window_h", "deadline_h", "owner")
_REQUIRED_ARTIFACT_FIELDS = ("glob", "due_weekday", "active_from")


class SourceState(enum.Enum):
    OK = "OK"
    KNOWN_LAGGING = "KNOWN-LAGGING"
    BROKEN = "BROKEN"


class UnknownSourceError(KeyError):
    """A source has no contract in config/source_clock.yaml.

    Deliberately NOT a default contract: an unregistered source is an
    unmonitored source, and silently monitoring it against invented numbers
    is the failure this module exists to prevent.
    """


@dataclass(frozen=True)
class SourceContract:
    expected_interval_h: float
    lag_window_h: float
    deadline_h: float
    owner: str
    expected_season: int | None = None

    @property
    def fresh_within_h(self) -> float:
        return self.expected_interval_h + self.lag_window_h


@dataclass(frozen=True)
class ArtifactContract:
    name: str
    glob: str
    due_weekday: str
    active_from: datetime.date


@dataclass(frozen=True)
class SourceClock:
    as_of: datetime.date
    contracts: dict[str, SourceContract]
    artifacts: dict[str, ArtifactContract]

    def contract(self, source: str) -> SourceContract:
        try:
            return self.contracts[source]
        except KeyError:
            raise UnknownSourceError(
                f"{source!r} has no contract in config/source_clock.yaml — add one "
                f"(expected_interval_h, lag_window_h, deadline_h, owner). Known: "
                f"{sorted(self.contracts)}"
            ) from None


def _require(block: dict, fields: tuple[str, ...], where: str) -> None:
    missing = [f for f in fields if f not in block]
    if missing:
        raise ValueError(f"source_clock.yaml: {where} missing {missing}")


def load_clock(path: pathlib.Path | None = None) -> SourceClock:
    p = path or DEFAULT_CLOCK_PATH
    raw = yaml.safe_load(p.read_text())
    if not isinstance(raw, dict) or "as_of" not in raw or "sources" not in raw:
        raise ValueError(f"{p}: expected a mapping with 'as_of' and 'sources' keys")
    contracts = {}
    for name, block in (raw["sources"] or {}).items():
        _require(block, _REQUIRED_SOURCE_FIELDS, f"source {name!r}")
        contracts[name] = SourceContract(
            expected_interval_h=float(block["expected_interval_h"]),
            lag_window_h=float(block["lag_window_h"]),
            deadline_h=float(block["deadline_h"]),
            owner=str(block["owner"]),
            expected_season=(
                int(block["expected_season"]) if block.get("expected_season") is not None else None
            ),
        )
    artifacts = {}
    for name, block in (raw.get("artifacts") or {}).items():
        _require(block, _REQUIRED_ARTIFACT_FIELDS, f"artifact {name!r}")
        artifacts[name] = ArtifactContract(
            name=name,
            glob=str(block["glob"]),
            due_weekday=str(block["due_weekday"]).lower(),
            active_from=block["active_from"],
        )
    return SourceClock(as_of=raw["as_of"], contracts=contracts, artifacts=artifacts)


def state(
    source: str, status: str, age_h: float, clock: SourceClock | None = None
) -> SourceState:
    """Three-state health for one source, from (status, age) TOGETHER.

    `status` is a raw.ingest_runs status ('success', 'failed', 'running',
    'sanity_warned', 'sanity_failed') or the string 'success' for
    file-derived sources like `backup`.
    """
    contract = (clock or load_clock()).contract(source)
    if age_h is None or age_h < 0:
        raise ValueError(f"{source}: age_h must be a non-negative number, got {age_h!r}")
    if status not in _OK_STATUSES and status not in _SUSPECT_STATUSES:
        return SourceState.BROKEN
    if age_h > contract.deadline_h:
        return SourceState.BROKEN
    if status in _SUSPECT_STATUSES or age_h > contract.fresh_within_h:
        return SourceState.KNOWN_LAGGING
    return SourceState.OK


def is_alarming(s: SourceState) -> bool:
    """Only BROKEN gets a banner. Structural lag gets a quiet line (R12)."""
    return s is SourceState.BROKEN
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_health.py -q`
Expected: `12 passed`

- [ ] **Step 6: Verify the file-size budget with room to spare**

Run: `wc -l src/ffi/health.py`
Expected: a count of **≤ 150** (target ~145). If it exceeds 150, do not raise the budget — move `ArtifactContract` + its parsing into `scripts/morning_briefing.py` and delete the `artifacts` half of `load_clock`.

- [ ] **Step 7: Amend ARCHITECTURE.md (amendment 2 of 8)**

In ARCHITECTURE.md §4, replace the row

```
| Source health state | `state(source: str, status: str, last_success_at: datetime, now: datetime) -> SourceState` (OK / KNOWN_LAGGING / BROKEN) | `src/ffi/health.py` |
```

with

```
| Source health state | `state(source: str, status: str, age_h: float, clock: SourceClock \| None = None) -> SourceState` (OK / KNOWN_LAGGING / BROKEN); `load_clock(path) -> SourceClock`; `is_alarming(state) -> bool` | `src/ffi/health.py` |
```

Then append to the ARCHITECTURE.md TBDs section:

```
6. §4's health signature was provisional ("provisional in name and shape only").
   Resolved during writing-plans to `state(source, status, age_h, clock)`:
   `raw.ingest_runs` is queried with `extract(epoch FROM now() - started_at)/3600`
   and never returns a `last_success_at` timestamp, so a `(datetime, datetime)`
   signature would force every caller to reconstruct one. Semantics
   (three states, age-aware, config-driven) are unchanged.
```

- [ ] **Step 8: Guards + suite, then commit**

Run:
```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
```
Expected: three `OK:` lines (127 files), suite passes.

```bash
git add src/ffi/health.py config/source_clock.yaml tests/test_health.py ARCHITECTURE.md
git commit -m "feat(health): three-state SourceState model + committed source_clock contracts

R1 regression test included: a 1209h-old run with status='success' must
render BROKEN. Per-source contracts replace the single global STALE_HOURS=36
— nflverse's structural post-MNF lag is not Sleeper's (R12: only BROKEN is
alarming, expected lag is a quiet line)."
```

---

### Task 4: Morning briefing integration — health states, artifact freshness, archive continuity

Completes ADR Precondition P1 (the age-aware health state) and ADR Domain 5 items (1), (2) and (4).

**Files:**
- Modify: `scripts/morning_briefing.py` (currently 155 lines; budget 400)
- Test: `tests/test_morning_briefing.py` (create)

**Interfaces:**
- Consumes: `ffi.health.load_clock() -> SourceClock`, `ffi.health.state(source, status, age_h, clock) -> SourceState`, `ffi.health.is_alarming(s) -> bool`, `ffi.health.UnknownSourceError`, `ffi.health.SourceState`, `ArtifactContract.{glob,due_weekday,active_from}`; `raw.sleeper_trending.archive_date` (Task 1).
- Produces: `scripts/morning_briefing.py` module-level functions `artifact_freshness_lines(clock, now_local, reports_dir) -> tuple[list[str], list[str]]` and `archive_continuity_lines(conn, today_local) -> tuple[list[str], list[str]]`, each returning `(render_lines, red_flags)`. Both are importable and unit-tested; nothing else imports them.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_morning_briefing.py`:

```python
import datetime
import pathlib

import pytest

# tests/conftest.py already puts scripts/ on sys.path.
import morning_briefing as mb
from ffi.health import load_clock

CLOCK = load_clock(pathlib.Path("config/source_clock.yaml"))
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
    import os

    os.utime(stale, (yesterday, yesterday))
    lines, reds = mb.artifact_freshness_lines(CLOCK, now, tmp_path)
    assert any("stale" in r for r in reds)


def test_artifact_assertion_ok_when_present_and_fresh(tmp_path):
    now = datetime.datetime(2026, 9, 22, 7, 0, tzinfo=TZ)
    fresh = tmp_path / "trends-2026-39.md"
    fresh.write_text("new\n")
    import os

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_morning_briefing.py -q`
Expected: FAIL — `AttributeError: module 'morning_briefing' has no attribute 'artifact_freshness_lines'`. (Note: importing `morning_briefing` today executes the whole script at import time; Step 3 restructures it so the top-level work runs under `if __name__ == "__main__":`.)

- [ ] **Step 3: Restructure the briefing — replace lines 1–83 of `scripts/morning_briefing.py`**

Replace everything from the module docstring through the `if health.returncode != 0: red_flags.extend(fails)` block (current lines 1–83) with:

```python
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
REPORTS_DIR = pathlib.Path("reports")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

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
            reds.append(f"{name} report absent — due {today_name}, nothing matches {contract.glob}")
            continue
        newest = max(found, key=lambda p: p.stat().st_mtime)
        written = datetime.datetime.fromtimestamp(newest.stat().st_mtime, tz=LEAGUE_TZ)
        if written < midnight:
            lines.append(f"- [RED] {name} report: {newest.name} stale (written {written:%Y-%m-%d %H:%M})")
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
    missing day is listed by date rather than summarized as a count.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT min(archive_date), count(DISTINCT archive_date) FROM raw.sleeper_trending")
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
    return ([f"- [OK] trending archive: {distinct_days}/{expected} days since {first_day}"], [])


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
            cur.execute(f"SELECT max(season), max(week) FROM {table} WHERE season = %s", (expected,))
            season, week = cur.fetchone()
        if season is None:
            lines.append(f"- [RED] {source}: no rows for expected season {expected}")
            reds.append(f"{source} has no rows for expected_season {expected}")
        else:
            lines.append(f"- [OK] {source}: season {season} through week {week}")
    return lines, reds


conn = connect()
clock = health.load_clock()
now_local = datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ)
today = now_local.date().isoformat()
red_flags = []
L = [f"# Morning briefing — {today}", f"\n## Health (source_clock as_of {clock.as_of})"]

with conn.cursor() as cur:
    cur.execute(
        """SELECT DISTINCT ON (source) source, status,
                  extract(epoch FROM now() - started_at) / 3600 AS age_h, error
           FROM raw.ingest_runs ORDER BY source, started_at DESC"""
    )
    for source, status, age_h, error in cur.fetchall():
        try:
            st = health.state(source, status, float(age_h), clock)
        except health.UnknownSourceError:
            # Fail loud, but never by silencing the dashboard: an unregistered
            # source is itself the finding.
            red_flags.append(f"{source}: no entry in config/source_clock.yaml")
            L.append(f"- [RED] {source}: unregistered source, {float(age_h):.0f}h ago ({status})")
            continue
        if health.is_alarming(st):
            red_flags.append(f"{source} is {st.value}: {float(age_h):.0f}h old, status={status}, error={error}")
        L.append(f"- [{_mark(st)}] {source}: last run {float(age_h):.0f}h ago ({status})")

    cur.execute("SELECT max(fetched_at) FROM raw.sleeper_projections WHERE week IS NULL")
    latest = cur.fetchone()[0]
if latest is None:
    red_flags.append("no season-level sleeper snapshot at all")
    L.append("- [RED] sleeper season snapshot: MISSING")
else:
    age = (datetime.datetime.now(datetime.timezone.utc) - latest).total_seconds() / 3600
    st = health.state("sleeper_projections", "success", age, clock)
    if health.is_alarming(st):
        red_flags.append(f"sleeper season snapshot {age:.0f}h old ({st.value})")
    L.append(f"- [{_mark(st)}] sleeper season snapshot: {age:.0f}h old")

season_lines, season_reds = expected_season_lines(conn, clock)
L += season_lines
red_flags += season_reds

L.append("\n## Artifacts")
art_lines, art_reds = artifact_freshness_lines(clock, now_local, REPORTS_DIR)
L += art_lines or ["- no decision artifact due today"]
red_flags += art_reds

arch_lines, arch_reds = archive_continuity_lines(conn, now_local.date())
L += arch_lines
red_flags += arch_reds

L.append(f"- FP budget used today: {fp_calls_today(conn)}/30")

# NOTE: backups are plain pg_dump (`.sql.gz`) written by scripts/backup_db.sh,
# not custom-format `.dump` files — glob matches the actual on-disk naming.
backups = (
    sorted(pathlib.Path("backups").glob("fantasy_football_*.sql.gz"))
    if pathlib.Path("backups").exists()
    else []
)
if backups:
    written = datetime.datetime.fromtimestamp(backups[-1].stat().st_mtime, tz=LEAGUE_TZ)
    age_h = (now_local - written).total_seconds() / 3600
    st = health.state("backup", "success", age_h, clock)
    if health.is_alarming(st):
        red_flags.append(f"newest backup {age_h:.0f}h old ({st.value})")
    L.append(f"- [{_mark(st)}] newest pg_dump: {backups[-1].name} ({age_h:.0f}h old)")
else:
    red_flags.append("no backups found in backups/")
    L.append("- [RED] backups: none found")

health_gate = subprocess.run(
    [sys.executable, "scripts/phase1_report.py"], capture_output=True, text=True
)
fails = [ln for ln in health_gate.stdout.splitlines() if ln.startswith("FAIL")]
L.append(
    f"- structural health gate: {'OK' if health_gate.returncode == 0 else 'RED'}"
    + (f" — {len(fails)} failing: " + "; ".join(fails) if fails else "")
)
if health_gate.returncode != 0:
    red_flags.extend(fails)
```

Leave the rest of the file (from `L.append("\n## Board inputs")` onward) unchanged, except: in the final block replace `out = out_dir / f"briefing-{today}.md"` — it already uses `today`, which is now the tz-aware local date, so no edit is needed there.

- [ ] **Step 4: Move the imperative body into `main()` so the tests can import the helpers**

`tests/test_morning_briefing.py` imports `morning_briefing` for its three pure helpers (`artifact_freshness_lines`, `archive_continuity_lines`, `expected_season_lines`). Today the whole script runs at import, which would connect to the production database and write a report during the test run.

Make exactly two edits:

1. Change the line `conn = connect()` to:

```python
def main() -> None:
    conn = connect()
```

and indent everything from that point to the end of the file — through the final `raise SystemExit(1)` — one level (four spaces). The imports, module constants (`LEAGUE_TZ`, `REPORTS_DIR`, `WEEKDAYS`) and the four helper functions (`_mark`, `artifact_freshness_lines`, `archive_continuity_lines`, `expected_season_lines`) stay at module level and are NOT indented.

2. Append to the end of the file:

```python


if __name__ == "__main__":
    main()
```

Verify the split is correct before moving on:

Run: `uv run python -c "import sys; sys.path.insert(0,'scripts'); import morning_briefing as m; print(sorted(n for n in dir(m) if not n.startswith('__')))"`
Expected: a name list containing `artifact_freshness_lines`, `archive_continuity_lines`, `expected_season_lines`, `main`, `LEAGUE_TZ`, `REPORTS_DIR`, `WEEKDAYS` — and **no** `reports/briefing-*.md` written and no database connection made during the import.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_morning_briefing.py -q`
Expected: `8 passed`

Note: `test_archive_continuity_*` and `expected_season_lines` need `raw.nflverse_snap_counts`, which Task 7 creates. Until then, run only the artifact and continuity tests:
Run: `uv run pytest tests/test_morning_briefing.py -q -k "artifact or continuity"`
Expected: `8 passed`

- [ ] **Step 6: Run the briefing for real**

Run: `uv run python scripts/morning_briefing.py; echo "rc=$?"`
Expected: `-> reports/briefing-2026-08-31.md` and `rc=0` **or** `rc=1` with RED FLAGS naming `nflverse_snap_counts` (which does not exist until Task 7) — either is correct. Confirm by reading the artifact:

Run: `head -20 reports/briefing-$(date +%F).md`
Expected: a `## Health (source_clock as_of 2026-08-31)` header, a `[OK] nflverse_player_week: last run 0h ago (success)` line (Task 2 backfilled it), an `[OK] trending archive: 1/1 days since 2026-08-31` line, and no `[OK]` line on any source older than its contract.

- [ ] **Step 7: Verify the budget**

Run: `wc -l scripts/morning_briefing.py`
Expected: **≤ 400** (projected ~285). If it exceeds 400, extract `artifact_freshness_lines`, `archive_continuity_lines` and `expected_season_lines` into `src/ffi/reports/health_section.py` (a new §1b row, budget 200, Layer 4) and import them — do not raise the briefing's budget.

- [ ] **Step 8: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add scripts/morning_briefing.py tests/test_morning_briefing.py
git commit -m "feat(briefing): age-aware three-state health + artifact freshness + archive continuity

Completes P1: the health loop now calls ffi.health.state(source, status,
age_h) instead of testing status alone, so a stale-but-successful run can no
longer render [OK] (R1). Adds Monday/Tuesday artifact-freshness assertions
(R13), unrecoverable-gap detection over raw.sleeper_trending (R8), and an
expected-season assertion for both nflverse feeds. STALE_HOURS=36 is gone."
```

---

### Task 5: `config/league_clock.yaml` skeleton + validator — and the P3 operator stop

**Files:**
- Create: `config/league_clock.yaml`
- Create: `scripts/validate_league_clock.py`
- Create: `tests/test_validate_league_clock.py`
- Modify: `ARCHITECTURE.md` (§1c reader carve-out)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `config/league_clock.yaml` with the sentinel value `UNSET` and the `<field>_verified: false` marker convention; `scripts/validate_league_clock.py` exposing `unsafe_fields(doc: dict) -> list[str]` and `find_consumers(fields: list[str], roots: list[pathlib.Path]) -> list[tuple[str, str, int]]` (field, path, line-number). Plan 2's `src/ffi/league_state/clock.py` is the only sanctioned runtime reader of the YAML.

- [ ] **Step 1: Write the config skeleton**

Create `config/league_clock.yaml`:

```yaml
# config/league_clock.yaml — the league's own clock (ADR Domain 2).
#
# Read at runtime ONLY by src/ffi/league_state/clock.py (Plan 2) and, for
# validation only, by scripts/validate_league_clock.py. Every scheduled job
# time is DERIVED from a deadline in this file rather than hardcoded, so a
# cadence/deadline mismatch is a config error someone can see instead of a
# structural defeat (R2, L8xI9=72 — the largest addressable risk in the
# register).
#
# TWO MARKER CONVENTIONS, both enforced by scripts/validate_league_clock.py:
#   value `UNSET`            -> not yet observed; P3 must fit it from data
#   `<field>_verified: false` -> transcribed from league_rules.md but NOT
#                                diffed against the live 2026 settings page
#
# Consuming either kind of field from src/ or scripts/ fails the validator
# (exit 1). Fail-closed: window-2 clear-time alerting stays disabled until
# clear_award_mechanism is observed (ADR Domain 1).
as_of: 2026-08-31
source: league_rules.md (dated 2025) — NOT yet diffed against the live 2026 settings page (R16)
timezone: America/Los_Angeles

# --- Populated from league_rules.md, structurally stable ---------------------
league_id: 326814
teams: 12
waiver_type: continual_rolling_list
waivers_process_day: tuesday        # "Weekly Waivers: Game Time - Tuesday"
waiver_period_days: 1               # "Waiver Time: 1 day"
weekly_acquisition_limit: 5         # "Weekly Acquisitions: Maximum 5"
season_acquisition_limit: 65        # "Season Acquisitions: Maximum 65"
ir_direct_add: false                # "IR Direct Add: No"
season_first_game_date: 2026-09-10  # Thursday after Labor Day 2026

# --- P3 OBSERVED MECHANICS — must be fitted from the 2025 transaction log ----
# These are NOT settings-page readings. R4: "drop + 1 day" is likely a batch
# boundary, not arithmetic, and the post-clear award may be priority rather
# than FCFS — which would collapse window 2 into window 1 entirely.
waiver_processing_hour: UNSET       # local hour claims actually process (R2)
clear_award_mechanism: UNSET        # fcfs | rolling_priority (R4)
weekend_drop_clear_behavior: UNSET  # does a Sunday drop clear Monday, or wait for the Tuesday batch?
move_count_reset_boundary: UNSET    # when the weekly 5-move counter resets (R21)

# --- Transcribed from a 2025 document; NOT verified against 2026 -------------
# league_rules.md literally says "Trade Deadline: November 22, 2025". The
# 2026 value is an inference. A commissioner change at renewal poisons the
# ledger, the clear math and the priority model simultaneously (R16).
trade_deadline: 2026-11-22
trade_deadline_verified: false
playoff_weeks: [15, 16, 17]
playoff_weeks_verified: false
playoff_teams: 6
playoff_teams_verified: false
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_validate_league_clock.py`:

```python
import pathlib

import pytest

import validate_league_clock as v  # scripts/ is on sys.path via conftest


def test_unsafe_fields_finds_unset_and_unverified():
    doc = {
        "as_of": "2026-08-31",
        "teams": 12,
        "waiver_processing_hour": "UNSET",
        "trade_deadline": "2026-11-22",
        "trade_deadline_verified": False,
        "playoff_teams": 6,
        "playoff_teams_verified": True,
    }
    assert v.unsafe_fields(doc) == ["trade_deadline", "waiver_processing_hour"]


def test_unsafe_fields_ignores_populated_and_verified():
    doc = {"teams": 12, "playoff_teams": 6, "playoff_teams_verified": True}
    assert v.unsafe_fields(doc) == []


def test_find_consumers_detects_a_string_literal_use(tmp_path):
    (tmp_path / "bad.py").write_text(
        'clock = load()\nhour = clock["waiver_processing_hour"]\n'
    )
    hits = v.find_consumers(["waiver_processing_hour"], [tmp_path])
    assert len(hits) == 1
    field, path, lineno = hits[0]
    assert field == "waiver_processing_hour"
    assert path.endswith("bad.py")
    assert lineno == 2


def test_find_consumers_ignores_a_field_that_is_not_used(tmp_path):
    (tmp_path / "fine.py").write_text('x = clock["teams"]\n')
    assert v.find_consumers(["waiver_processing_hour"], [tmp_path]) == []


def test_the_real_config_has_no_unsafe_consumers():
    """The gate itself: as long as no module consumes an UNSET/UNVERIFIED
    field, the repo is safe to ship with the skeleton in place."""
    assert v.main(["--quiet"]) == 0


def test_the_real_config_still_has_unset_fields():
    """If this fails, P3 has been done — delete this test and update the
    plan's completion notes with the observed values."""
    doc = v.load_doc(pathlib.Path("config/league_clock.yaml"))
    assert "waiver_processing_hour" in v.unsafe_fields(doc)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_validate_league_clock.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'validate_league_clock'`.

- [ ] **Step 4: Write the validator**

Create `scripts/validate_league_clock.py`:

```python
#!/usr/bin/env python3
"""Fail if any UNSET or UNVERIFIED league-clock field is consumed by code.

ADR Domain 1 fail-closed: a field whose value has not been observed must not
silently become a number in a deadline computation. R4/R16 are the concrete
cases — a `waiver_processing_hour` guessed from the settings page would
produce a confidently-wrong claim deadline every week of the season.

Detection is a string-literal scan, not an import graph: the YAML is read
through dict lookups (`clock["waiver_processing_hour"]`,
`clock.get("clear_award_mechanism")`), so the field name appears verbatim at
every real call site. False positives are acceptable and are the safe
direction; a false negative is the failure this script exists to prevent.

Exit 0 = safe. Exit 1 = an unsafe field is consumed (with file:line).
"""
import argparse
import pathlib
import re
import sys

import yaml

CONFIG = pathlib.Path("config/league_clock.yaml")
SCAN_ROOTS = [pathlib.Path("src"), pathlib.Path("scripts")]
SKIP_PARTS = {"__pycache__", ".venv", "archive"}
SELF = "validate_league_clock.py"
UNSET = "UNSET"
VERIFIED_SUFFIX = "_verified"


def load_doc(path: pathlib.Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a top-level mapping, got {type(doc).__name__}")
    return doc


def unsafe_fields(doc: dict) -> list[str]:
    """Field names that must not be consumed yet: value == 'UNSET', or a
    sibling `<field>_verified` that is false."""
    unsafe = {k for k, val in doc.items() if isinstance(val, str) and val.strip() == UNSET}
    for key, val in doc.items():
        if key.endswith(VERIFIED_SUFFIX) and val is False:
            base = key[: -len(VERIFIED_SUFFIX)]
            if base in doc:
                unsafe.add(base)
    return sorted(unsafe)


def find_consumers(fields: list[str], roots: list[pathlib.Path]) -> list[tuple[str, str, int]]:
    if not fields:
        return []
    pattern = re.compile("|".join(rf"['\"]{re.escape(f)}['\"]" for f in fields))
    hits: list[tuple[str, str, int]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if SKIP_PARTS & set(path.parts) or path.name == SELF:
                continue
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                match = pattern.search(line)
                if match:
                    hits.append((match.group(0).strip("'\""), str(path), lineno))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    doc = load_doc(CONFIG)
    if "as_of" not in doc:
        print(f"FAIL: {CONFIG} has no as_of stamp.")
        return 1
    fields = unsafe_fields(doc)
    hits = find_consumers(fields, SCAN_ROOTS)
    if hits:
        print(f"FAIL: {len(hits)} consumer(s) of UNSET/UNVERIFIED league-clock fields:")
        for field, path, lineno in hits:
            print(f"  {path}:{lineno}: consumes {field!r}")
        print("\nRun the P3 observed-mechanics checks and populate the field, or")
        print("stop consuming it. Fail-closed is the contract (ADR Domain 1).")
        return 1
    if not args.quiet:
        print(
            f"OK: {CONFIG} as_of {doc['as_of']}; {len(fields)} field(s) still "
            f"UNSET/UNVERIFIED and none is consumed: {', '.join(fields) or '(none)'}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests and the validator**

Run:
```bash
uv run pytest tests/test_validate_league_clock.py -q
uv run python scripts/validate_league_clock.py; echo "rc=$?"
```
Expected: `6 passed`, then
```
OK: config/league_clock.yaml as_of 2026-08-31; 7 field(s) still UNSET/UNVERIFIED and none is consumed: clear_award_mechanism, move_count_reset_boundary, playoff_teams, playoff_weeks, trade_deadline, waiver_processing_hour, weekend_drop_clear_behavior
rc=0
```

- [ ] **Step 6: Amend ARCHITECTURE.md (amendment 3 of 8)**

In ARCHITECTURE.md §1c, replace the `config/league_clock.yaml` row's contract text ending `Initial contents come from the P3 observed-mechanics tests` with:

```
... Read at runtime ONLY by `src/ffi/league_state/clock.py`; `scripts/validate_league_clock.py` reads it for validation only and derives nothing. Initial contents come from the P3 observed-mechanics tests; UNSET / `<field>_verified: false` markers are enforced by that validator
```

- [ ] **Step 7: 🛑 USER INPUT REQUIRED — run the P3 observed-mechanics checks**

**STOP. This step cannot be completed by an agent and must not be faked.** The four checks below read a Yahoo UI that requires the operator's authenticated browser session; the API is 403-gated with no appeal path (memory 2026-08-22). Inventing these values would produce a confidently-wrong claim deadline every week of the season — the exact failure this plan exists to prevent.

Hand the operator this checklist verbatim:

> **P3 observed-mechanics checks (~2h, $0). Do these before Plan 2.**
>
> Open `https://football.fantasysports.yahoo.com/league/lefteye` in your normal browser, signed in.
>
> 1. **Waiver processing hour (R2)** — Transactions tab → filter to 2025 → Waiver/Add transactions. Screenshot 10 processed claims **including the timestamp column**, spread across at least 4 different weeks. Save to `data/captures/p3-waiver-times-2025.png` (gitignored). Record the local hour each batch processed at.
> 2. **Settings diff (R16)** — Settings tab. Screenshot the Waivers, Transaction Limits, Trade and Playoff blocks to `data/captures/p3-settings-2026.png`. Diff every field against `league_rules.md`. Note: `league_rules.md` says "Trade Deadline: November 22, **2025**" — you are confirming the 2026 date, not copying it.
> 3. **Drop→clear intervals (R4)** — find 5–10 pairs where a player was dropped and later added by someone else. Record `(drop_ts, clear_ts, weekday, who_got_them, their_priority_position)`. The question is: did the post-clear add go to the **highest waiver priority** or to **whoever clicked first**? That single answer decides whether window 2 exists at all.
> 4. **Weekend behavior** — find one Saturday or Sunday drop. Did it clear the next day, or wait for the Tuesday batch?
>
> **Then record the results:** replace each `UNSET` in `config/league_clock.yaml` with the observed value, bump `as_of` to the date you did the checks, set `source:` to `2025 Yahoo transaction log + 2026 settings page (observed)`, and flip `trade_deadline_verified` / `playoff_weeks_verified` / `playoff_teams_verified` to `true` if the settings page confirms them (or change the values if it does not).

Do **not** mark this task complete until the operator has either (a) recorded the values, or (b) explicitly deferred P3 to Plan 2. If deferred: leave the `UNSET` markers in place — the validator keeps the repo safe, and window-2 alerting stays disabled by construction. Record the operator's choice in the plan-completion notes at the bottom of this document.

- [ ] **Step 8: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add config/league_clock.yaml scripts/validate_league_clock.py \
        tests/test_validate_league_clock.py ARCHITECTURE.md
git commit -m "feat(config): league_clock skeleton with UNSET/UNVERIFIED markers + consumption validator

R2/R4/R16: job times must derive from written, observed deadlines rather
than settings-page inference. scripts/validate_league_clock.py exits 1 the
moment any module consumes a field that has not been observed, so
fail-closed is enforced by CI rather than by discipline. The P3 observed-
mechanics checks require the operator's authenticated session and are
tracked as a blocking step in the plan, not stubbed."
```

---

### Task 6: `src/ffi/ingest/gates.py` + observe-and-log / hard-fail wiring

Ports the `src/ffi/sim/pool.py` 2QB sanity-gate pattern (`_MIN_QB_IN_TOP_N = 8` raising `ValueError` rather than emitting a degraded pool) to the ingest boundary, where it currently produces only a structlog warning.

**Files:**
- Create: `migrations/010_ingest_run_status.sql`
- Create: `src/ffi/ingest/gates.py`
- Create: `tests/test_ingest_gates.py`
- Modify: `src/ffi/ingest/base.py`
- Modify: `src/ffi/ingest/sleeper_trending.py` (Task 1) — observe-and-log
- Modify: `src/ffi/ingest/sleeper.py` — hard-fail
- Modify: `tests/test_sleeper_trending_ingest.py`, `tests/test_ingest_base.py`

**Interfaces:**
- Consumes: `ffi.ingest.base.BaseIngester` (Task 1's `SleeperTrendingIngester` subclasses it).
- Produces:
  - `ffi.ingest.gates.SanityGateError(Exception)`
  - `check_fieldset(prev: Sequence[str] | None, curr: Sequence[str], *, feed: str) -> None`
  - `check_rank_correlation(prev: Mapping[str, float], curr: Mapping[str, float], *, feed: str, min_rho: float = 0.85, min_overlap: int = 20) -> float`
  - `check_nonzero_coverage(rows: Sequence[Mapping], *, feed: str, value_key: str, min_players: int) -> int`
  - `BaseIngester.sanity_mode: str` (`"off"` | `"warn"` | `"fail"`, default `"off"`) and `BaseIngester.sanity_check(conn, payload) -> None`.
  - `raw.ingest_runs.status` accepts `'sanity_warned'` and `'sanity_failed'`. Task 3's `ffi.health.state` already maps both.

- [ ] **Step 1: Write the migration**

Create `migrations/010_ingest_run_status.sql`:

```sql
-- 010_ingest_run_status.sql — ADR Domain 1: semantic sanity gates need their
-- own run statuses, distinct from 'failed'.
--
--   sanity_failed  the gate raised and the ingester refused to store
--                  (hard-fail mode: sleeper_projections)
--   sanity_warned  the gate raised, the payload WAS stored, and the run is
--                  flagged (observe-and-log mode: sleeper_trending — the
--                  archive is unrecoverable, so refusing to store a suspect
--                  day would destroy more than it protects)
--
-- Per-feed numeric thresholds beyond the >=0.85 rank correlation are unset
-- (ADR TBD 3) and will be fitted from the first four weeks of the P2
-- archive. Until then trending is observe-and-log and projections hard-fail.
ALTER TABLE raw.ingest_runs DROP CONSTRAINT IF EXISTS ingest_runs_status_check;
ALTER TABLE raw.ingest_runs
    ADD CONSTRAINT ingest_runs_status_check
    CHECK (status IN ('running','success','failed','sanity_warned','sanity_failed'));
```

- [ ] **Step 2: Apply the migration**

Run:
```bash
psql -d fantasy_football -f migrations/010_ingest_run_status.sql
psql -d fantasy_football_test -f migrations/010_ingest_run_status.sql
psql -d fantasy_football -c "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='ingest_runs_status_check'"
```
Expected: two `ALTER TABLE` pairs, then `CHECK (status = ANY (ARRAY['running'::text, 'success'::text, 'failed'::text, 'sanity_warned'::text, 'sanity_failed'::text]))`.

- [ ] **Step 3: Write the failing gate tests**

Create `tests/test_ingest_gates.py`:

```python
import pytest

from ffi.ingest.gates import (
    SanityGateError,
    check_fieldset,
    check_nonzero_coverage,
    check_rank_correlation,
)


def test_fieldset_passes_when_identical():
    check_fieldset(["a", "b"], ["b", "a"], feed="t")


def test_fieldset_passes_when_prior_is_absent():
    check_fieldset(None, ["a"], feed="t")


def test_fieldset_raises_on_removed_key():
    with pytest.raises(SanityGateError, match=r"removed=\['b'\]"):
        check_fieldset(["a", "b"], ["a"], feed="t")


def test_fieldset_raises_on_added_key():
    # The Aug-2026 adp_2qb incident is the template: a new key is drift too.
    with pytest.raises(SanityGateError, match=r"added=\['c'\]"):
        check_fieldset(["a"], ["a", "c"], feed="t")


def test_rank_correlation_is_one_for_identical_orderings():
    prev = {str(i): float(i) for i in range(30)}
    assert check_rank_correlation(prev, dict(prev), feed="t") == pytest.approx(1.0)


def test_rank_correlation_survives_small_perturbation():
    prev = {str(i): float(i) for i in range(50)}
    curr = dict(prev)
    curr["0"], curr["1"] = 1.0, 0.0  # swap the bottom two
    assert check_rank_correlation(prev, curr, feed="t") > 0.99


def test_rank_correlation_raises_on_a_reversed_ordering():
    prev = {str(i): float(i) for i in range(30)}
    curr = {str(i): float(30 - i) for i in range(30)}
    with pytest.raises(SanityGateError, match="rank correlation"):
        check_rank_correlation(prev, curr, feed="t")


def test_rank_correlation_raises_on_thin_overlap():
    with pytest.raises(SanityGateError, match="overlap"):
        check_rank_correlation({"a": 1.0}, {"a": 1.0}, feed="t")


def test_rank_correlation_raises_when_all_values_tie():
    prev = {str(i): 1.0 for i in range(30)}
    with pytest.raises(SanityGateError, match="undefined"):
        check_rank_correlation(prev, dict(prev), feed="t")


def test_nonzero_coverage_counts_positive_values_only():
    rows = [{"c": 5}, {"c": 0}, {"c": None}, {"c": 3}]
    assert check_nonzero_coverage(rows, feed="t", value_key="c", min_players=2) == 2


def test_nonzero_coverage_raises_below_floor():
    rows = [{"c": 5}]
    with pytest.raises(SanityGateError, match="1 row"):
        check_nonzero_coverage(rows, feed="t", value_key="c", min_players=10)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_ingest_gates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.ingest.gates'`.

- [ ] **Step 5: Write the gates module**

Create `src/ffi/ingest/gates.py`:

```python
"""Semantic sanity gates at the ingest boundary (ADR Domain 1).

Pure functions over already-fetched data. Imports nothing internal — a gate
that imported the feed it validates could not be trusted to fail
independently of it (ARCHITECTURE §3 forbids gates -> ingest/usage/
league_state, in both directions).

Every check RAISES on failure. Nothing here returns a boolean, logs and
continues, or degrades: the CALLER picks the mode by catching or not
catching (BaseIngester.sanity_mode). This system already guards data
*absence* well and degraded *presence* not at all — R1 is one live proof and
the Aug-2026 Sleeper `adp_2qb` semantic drift is a second. The pattern is
lifted from src/ffi/sim/pool.py's 2QB gate, which raises ValueError rather
than emitting a plausible-looking degraded pool.
"""
from collections.abc import Mapping, Sequence

MIN_RANK_CORRELATION = 0.85
DEFAULT_MIN_OVERLAP = 20


class SanityGateError(Exception):
    """A payload passed schema validation but failed a semantic gate."""


def check_fieldset(prev: Sequence[str] | None, curr: Sequence[str], *, feed: str) -> None:
    """Field-set diff vs the prior snapshot. Any difference is drift.

    Added keys are as much a signal as removed ones: the adp_2qb incident
    was a cohort/semantic change, not a deletion. `prev=None` (no prior
    snapshot) passes — there is nothing to compare against on day one.
    """
    if prev is None:
        return
    prev_set, curr_set = set(prev), set(curr)
    removed = sorted(prev_set - curr_set)
    added = sorted(curr_set - prev_set)
    if removed or added:
        raise SanityGateError(
            f"{feed}: field-set drift vs prior snapshot — removed={removed} added={added}. "
            f"Schema drift is a hard signal (ADR D1; the Aug-2026 adp_2qb "
            f"semantic drift is the template for this check)."
        )


def _average_ranks(values: Sequence[float]) -> list[float]:
    """Ranks with ties averaged — the standard Spearman tie correction."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def check_rank_correlation(
    prev: Mapping[str, float],
    curr: Mapping[str, float],
    *,
    feed: str,
    min_rho: float = MIN_RANK_CORRELATION,
    min_overlap: int = DEFAULT_MIN_OVERLAP,
) -> float:
    """Spearman rank correlation over the key intersection. Raises below
    `min_rho`. Implemented here rather than via scipy so a degenerate input
    raises loudly instead of returning nan."""
    keys = sorted(set(prev) & set(curr))
    if len(keys) < min_overlap:
        raise SanityGateError(
            f"{feed}: only {len(keys)} keys overlap between snapshots "
            f"(need >= {min_overlap}) — a cohort this different is drift, not noise"
        )
    a = _average_ranks([float(prev[k]) for k in keys])
    b = _average_ranks([float(curr[k]) for k in keys])
    n = len(keys)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a == 0 or var_b == 0:
        raise SanityGateError(
            f"{feed}: rank correlation undefined — all {n} values are tied in "
            f"one snapshot; a flat distribution is itself a failure"
        )
    rho = cov / ((var_a**0.5) * (var_b**0.5))
    if rho < min_rho:
        raise SanityGateError(
            f"{feed}: week-over-week rank correlation {rho:.3f} < {min_rho} over "
            f"{n} shared keys — the ordering changed more than a real feed can"
        )
    return rho


def check_nonzero_coverage(
    rows: Sequence[Mapping], *, feed: str, value_key: str, min_players: int
) -> int:
    """Count rows whose `value_key` is present and strictly positive.

    Guards the failure the ratio checks structurally cannot see: a collapsed
    population where numerator and denominator shrink together and the ratio
    still reads 100% (the R5 finding in ffi.ingest.sleeper).
    """
    count = 0
    for row in rows:
        value = row.get(value_key)
        if value is None:
            continue
        if float(value) > 0:
            count += 1
    if count < min_players:
        raise SanityGateError(
            f"{feed}: only {count} row(s) carry a positive {value_key!r} "
            f"(floor {min_players}) — population collapse, not a quiet day"
        )
    return count
```

- [ ] **Step 6: Run the gate tests to verify they pass**

Run: `uv run pytest tests/test_ingest_gates.py -q`
Expected: `11 passed`

- [ ] **Step 7: Add the sanity hook to `BaseIngester`**

In `src/ffi/ingest/base.py`, add the import and the two class members, then replace the `run()` body. Full replacement for the file from `class BaseIngester:` to the end:

```python
class BaseIngester:
    source: str = None  # subclasses must set

    # 'off'  no gate is run
    # 'warn' observe-and-log: the payload IS stored, the run is recorded
    #        'sanity_warned', and health.state() never reports it OK
    # 'fail' hard-fail: nothing is stored, the run is recorded 'sanity_failed'
    # Per-feed numeric thresholds are unset until four weeks of P2 archive
    # exist (ADR TBD 3), so trending runs 'warn' and projections run 'fail'.
    sanity_mode: str = "off"

    def fetch(self):
        raise NotImplementedError

    def validate(self, payload) -> int:
        raise NotImplementedError

    def store(self, conn, run_id: int, payload) -> None:
        raise NotImplementedError

    def sanity_check(self, conn, payload) -> None:
        """Raise SanityGateError if this payload fails its semantic gates.
        Default no-op; only called when `sanity_mode != 'off'`."""
        raise NotImplementedError(
            f"{type(self).__name__}.sanity_mode={self.sanity_mode!r} but "
            f"sanity_check() is not implemented"
        )

    def _first_record(self, payload) -> dict | None:
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict):
            return payload
        return None

    def _finish(self, conn, run_id: int, status: str, **cols) -> None:
        sets = ", ".join(f"{k}=%s" for k in cols)
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE raw.ingest_runs SET finished_at=now(), status=%s"
                + (f", {sets}" if cols else "")
                + " WHERE run_id=%s",
                (status, *cols.values(), run_id),
            )
        conn.commit()

    def run(self, conn) -> int:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.ingest_runs (source) VALUES (%s) RETURNING run_id",
                (self.source,),
            )
            run_id = cur.fetchone()[0]
        conn.commit()
        try:
            payload = self.fetch()
            row_count = self.validate(payload)
            final_status = "success"
            if self.sanity_mode != "off":
                try:
                    self.sanity_check(conn, payload)
                except SanityGateError as gate_exc:
                    if self.sanity_mode == "fail":
                        raise
                    # observe-and-log: store anyway, but never render OK.
                    log.warning(
                        "ingest.sanity_warned",
                        source=self.source,
                        run_id=run_id,
                        error=str(gate_exc),
                    )
                    final_status = "sanity_warned"
            self.store(conn, run_id, payload)
            first = self._first_record(payload)
            self._finish(
                conn,
                run_id,
                final_status,
                row_count=row_count,
                schema_hash=schema_hash(first) if first else None,
            )
            log.info(
                "ingest.success",
                source=self.source,
                run_id=run_id,
                rows=row_count,
                status=final_status,
            )
            return run_id
        except SanityGateError as exc:
            conn.rollback()
            self._finish(conn, run_id, "sanity_failed", error=str(exc))
            log.error("ingest.sanity_failed", source=self.source, run_id=run_id, error=str(exc))
            raise  # fail loud — callers/cron must see nonzero exit
        except Exception as exc:
            conn.rollback()
            self._finish(conn, run_id, "failed", error=str(exc))
            log.error("ingest.failed", source=self.source, run_id=run_id, error=str(exc))
            raise  # fail loud — callers/cron must see nonzero exit
```

Add to the top of `src/ffi/ingest/base.py`, after `import structlog`:

```python
from ffi.ingest.gates import SanityGateError
```

- [ ] **Step 8: Wire trending in observe-and-log mode**

In `src/ffi/ingest/sleeper_trending.py`, add to the imports:

```python
from ffi.ingest.gates import (
    check_fieldset,
    check_nonzero_coverage,
    check_rank_correlation,
)
```

and add to `SleeperTrendingIngester`, immediately after `source = "sleeper_trending"`:

```python
    # Observe-and-log, not hard-fail: this archive is unrecoverable (R8), so
    # refusing to store a suspect day destroys more than it protects. The run
    # is recorded 'sanity_warned' and ffi.health.state() then refuses to
    # render it OK, so the operator sees it in the briefing the next morning.
    sanity_mode = "warn"

    # The `add` list is what the urgency overlay will read, so it is the list
    # the gate watches. Floor is well under the 200-row limit: a healthy day
    # returns 200 positive counts.
    MIN_POSITIVE_COUNTS = 100
```

and add these two methods after `validate`:

```python
    def _prior_add_rows(self, conn) -> list | None:
        """The `add` payload from the most recent EARLIER archive day.

        Strictly earlier, not `<=`: a same-day retry must not correlate
        against the snapshot it is retrying.
        """
        today = datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ).date()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM raw.sleeper_trending "
                "WHERE trend_type='add' AND archive_date < %s "
                "ORDER BY archive_date DESC, snapshot_id DESC LIMIT 1",
                (today,),
            )
            row = cur.fetchone()
        return None if row is None else row[0]

    def sanity_check(self, conn, payload) -> None:
        adds = payload["add"]
        check_nonzero_coverage(
            adds,
            feed=self.source,
            value_key="count",
            min_players=self.MIN_POSITIVE_COUNTS,
        )
        prior_rows = self._prior_add_rows(conn)
        if prior_rows is None:
            return  # day one: nothing to compare against
        check_fieldset(sorted(prior_rows[0]), sorted(adds[0]), feed=self.source)
        prior = {rec["player_id"]: float(rec["count"]) for rec in prior_rows}
        curr = {rec["player_id"]: float(rec["count"]) for rec in adds}
        check_rank_correlation(prior, curr, feed=self.source)
```

- [ ] **Step 9: Verify the live projections payload carries the gate's key before enabling hard-fail**

Run:
```bash
uv run python scripts/ingest_sleeper.py --season 2026 --inspect | python3 -c "
import json,sys; rec=json.load(sys.stdin); print(sorted(k for k in rec['stats'] if k.startswith('pts_')))"
```
Expected: `['pts_half_ppr', 'pts_ppr', 'pts_std']`. If `pts_ppr` is absent, **stop** — do not enable hard-fail against a key that does not exist; report the actual key list and adjust `RANK_KEY` below before continuing.

- [ ] **Step 10: Wire projections in hard-fail mode**

In `src/ffi/ingest/sleeper.py`, add to the imports:

```python
from ffi.ingest.gates import check_nonzero_coverage, check_rank_correlation
```

and add to `SleeperProjectionsIngester`, immediately after `source = "sleeper_projections"`:

```python
    # Hard-fail: unlike the trending archive, a bad projections snapshot is
    # fully recoverable (tomorrow's pull replaces it) and a bad one poisons
    # valuation, the board and every downstream recommendation. Refusing to
    # store is strictly cheaper than storing wrong (ADR D1).
    sanity_mode = "fail"

    # `pts_ppr` and not `adp_2qb`: adp_2qb is the field with KNOWN cohort
    # instability (data/adp-pin.json exists precisely because it flaps), so
    # correlating on it would fire on days the projections themselves are
    # fine. pts_ppr is the projection the gate actually cares about.
    RANK_KEY = "pts_ppr"
    MIN_RANKED = 400
```

and add these methods at the end of the class:

```python
    def _prior_payload(self, conn) -> list | None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM raw.sleeper_projections "
                "WHERE week IS NULL AND season=%s AND run_id IS NOT NULL "
                "ORDER BY snapshot_id DESC LIMIT 1",
                (self.season,),
            )
            row = cur.fetchone()
        return None if row is None else row[0]

    def _ranked(self, payload) -> dict:
        return {
            rec["player_id"]: float(rec["stats"][self.RANK_KEY])
            for rec in payload
            if self.RANK_KEY in rec.get("stats", {})
        }

    def sanity_check(self, conn, payload) -> None:
        check_nonzero_coverage(
            [rec.get("stats", {}) for rec in payload],
            feed=self.source,
            value_key=self.RANK_KEY,
            min_players=self.MIN_RANKED,
        )
        prior = self._prior_payload(conn)
        if prior is None:
            return  # first snapshot of the season: nothing to compare against
        check_rank_correlation(self._ranked(prior), self._ranked(payload), feed=self.source)
```

- [ ] **Step 11: Write the wiring tests**

Append to `tests/test_sleeper_trending_ingest.py`:

```python
def test_gate_failure_stores_the_payload_and_flags_the_run(db):
    """Observe-and-log: the archive must land even on a suspect day (R8),
    but the run must never look successful."""
    first = _payload()
    FixtureIngester(first).run(db)
    # Force an earlier archive_date so the gate has a prior to compare to.
    with db.cursor() as cur:
        cur.execute("UPDATE raw.sleeper_trending SET archive_date = archive_date - 1")
    db.commit()

    reversed_counts = {
        "add": [{"player_id": str(1000 + i), "count": i + 1} for i in range(120)],
        "drop": first["drop"],
    }
    run_id = FixtureIngester(reversed_counts).run(db)
    with db.cursor() as cur:
        cur.execute("SELECT status, error FROM raw.ingest_runs WHERE run_id=%s", (run_id,))
        status, error = cur.fetchone()
        cur.execute("SELECT count(*) FROM raw.sleeper_trending WHERE run_id=%s", (run_id,))
        stored = cur.fetchone()[0]
    assert status == "sanity_warned"
    assert stored == 2  # the archive still landed
```

Append to `tests/test_ingest_base.py`:

```python
from ffi.ingest.base import BaseIngester
from ffi.ingest.gates import SanityGateError


class _FailGateIngester(BaseIngester):
    source = "test_gate_feed"
    sanity_mode = "fail"

    def fetch(self):
        return [{"a": 1}]

    def validate(self, payload):
        return 1

    def sanity_check(self, conn, payload):
        raise SanityGateError("test_gate_feed: deliberately failing gate")

    def store(self, conn, run_id, payload):
        raise AssertionError("store() must not be reached in hard-fail mode")


def test_hard_fail_mode_records_sanity_failed_and_never_stores(db):
    with pytest.raises(SanityGateError, match="deliberately failing gate"):
        _FailGateIngester().run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE source='test_gate_feed'"
        )
        status, error = cur.fetchone()
    assert status == "sanity_failed"
    assert "deliberately failing gate" in error
```

(Add `import pytest` to `tests/test_ingest_base.py` if it is not already present.)

- [ ] **Step 12: Run the tests to verify they pass**

Run: `uv run pytest tests/test_ingest_gates.py tests/test_ingest_base.py tests/test_sleeper_trending_ingest.py tests/test_sleeper_ingest.py -q`
Expected: all pass — 11 gate tests, the base suite plus 1 new, 6 trending tests, and the pre-existing sleeper suite.

- [ ] **Step 13: Exercise both live feeds once**

Run:
```bash
uv run python scripts/ingest_sleeper.py --season 2026
uv run python scripts/ingest_sleeper_trending.py
psql -d fantasy_football -c "SELECT source, status, row_count FROM raw.ingest_runs ORDER BY run_id DESC LIMIT 2"
```
Expected: both print `OK run_id=<n>`; both rows show `status = success`. A `sanity_warned` on trending is also acceptable on the first day the gate sees a prior with a genuinely different cohort — read the `error` column and record it in the plan-completion notes rather than loosening the gate.

- [ ] **Step 14: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add migrations/010_ingest_run_status.sql src/ffi/ingest/gates.py src/ffi/ingest/base.py \
        src/ffi/ingest/sleeper.py src/ffi/ingest/sleeper_trending.py \
        tests/test_ingest_gates.py tests/test_ingest_base.py tests/test_sleeper_trending_ingest.py
git commit -m "feat(ingest): semantic sanity gates at the ingest boundary + sanity run statuses

Ports the sim/pool.py 2QB gate pattern (raise, never emit a degraded pool)
to the feed boundary, where drift previously produced only a structlog
warning (R6). Trending runs observe-and-log because its archive is
unrecoverable; projections hard-fail because a bad snapshot poisons
valuation and tomorrow's pull replaces it. Per-feed numeric thresholds stay
unset until four weeks of P2 archive exist (ADR TBD 3)."
```

---

### Task 7: nflverse snap-counts feed — the input the headline trend rule needs

**Why this task exists (added during planning, not in the original spec):** verified 2026-08-31, `raw.nflverse_player_week` has **no** snap, route, or red-zone columns — its 43 columns are all box-score stats. The spec's headline rule ("snap > 55% two consecutive weeks") therefore has no input at all. `nflreadpy.load_snap_counts()` supplies `offense_pct` directly (26,612 rows for 2025, no team denominator needed), keyed by `pfr_player_id`, which resolves to `gsis_id` through `nflreadpy.load_players()` (22,587 rows carry both). This is one small table and one ingester. `route_share` and `rz_touches` stay unavailable (see Scope) — degrade by removal, not by silent null (R27).

**Files:**
- Create: `migrations/011_nflverse_snap_counts.sql`
- Create: `src/ffi/ingest/nflverse_snaps.py`
- Create: `scripts/ingest_nflverse_snaps.py`
- Create: `tests/test_nflverse_snaps.py`
- Modify: `scripts/morning_chain.sh` (add the step)

**Interfaces:**
- Consumes: `ffi.ingest.base.BaseIngester`, `ffi.ingest.base.IngestError`, `ffi.ingest.nflverse.parse_seasons` (Task 2).
- Produces: `ffi.ingest.nflverse_snaps.NflverseSnapCountsIngester(seasons: list[int])` with `source = "nflverse_snap_counts"`; table `raw.nflverse_snap_counts(gsis_id, season, week, team, position, offense_snaps, offense_pct, fetched_at)`. Task 8's `ffi.usage.build` reads it; Task 4's `expected_season_lines` already queries it.

- [ ] **Step 1: Write the migration**

Create `migrations/011_nflverse_snap_counts.sql`:

```sql
-- 011_nflverse_snap_counts.sql — snap share for the usage engine (R27).
--
-- raw.nflverse_player_week carries no snap, route or red-zone columns
-- (verified 2026-08-31), so the trend engine's headline rule
-- (snap_share > 55% for two consecutive weeks) has no input without this
-- table. nflverse publishes offense_pct directly, so no team denominator is
-- derived here and the partial-publish short-denominator failure (R5)
-- cannot apply to this metric.
--
-- Keyed on gsis_id, resolved from pfr_player_id at ingest via
-- nflreadpy.load_players(); a match rate below the ingester's floor is a
-- hard failure, not a quiet row drop.
CREATE TABLE IF NOT EXISTS raw.nflverse_snap_counts (
    gsis_id       text NOT NULL,
    season        integer NOT NULL,
    week          integer NOT NULL,
    team          text,
    position      text,
    offense_snaps real,
    offense_pct   real,      -- 0.0-1.0, nflverse-published; NOT derived here
    fetched_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gsis_id, season, week)
);
CREATE INDEX IF NOT EXISTS idx_snap_counts_season_week
    ON raw.nflverse_snap_counts (season, week);
```

- [ ] **Step 2: Apply the migration**

Run:
```bash
psql -d fantasy_football -f migrations/011_nflverse_snap_counts.sql
psql -d fantasy_football_test -f migrations/011_nflverse_snap_counts.sql
psql -d fantasy_football -c "\d raw.nflverse_snap_counts" | head -14
```
Expected: `CREATE TABLE` / `CREATE INDEX`, then the eight columns listed with `gsis_id, season, week` as the primary key.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_nflverse_snaps.py`:

```python
import polars as pl
import pytest

from ffi.ingest.base import IngestError
from ffi.ingest.nflverse_snaps import NflverseSnapCountsIngester

# Column names verified against the live feed 2026-08-31.
SNAP_COLS = [
    "game_id", "season", "game_type", "week", "player", "pfr_player_id",
    "position", "team", "opponent", "offense_snaps", "offense_pct",
    "defense_snaps", "defense_pct", "st_snaps", "st_pct",
]


def _snaps(n: int = 30, *, game_type: str = "REG", position: str = "RB") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "game_id": [f"2025_01_A_B" for _ in range(n)],
            "season": [2025] * n,
            "game_type": [game_type] * n,
            "week": [1] * n,
            "player": [f"Player {i}" for i in range(n)],
            "pfr_player_id": [f"Pfr{i:04d}" for i in range(n)],
            "position": [position] * n,
            "team": ["SF"] * n,
            "opponent": ["SEA"] * n,
            "offense_snaps": [float(50 + i) for i in range(n)],
            "offense_pct": [0.5 + i / 200 for i in range(n)],
            "defense_snaps": [0.0] * n,
            "defense_pct": [0.0] * n,
            "st_snaps": [0.0] * n,
            "st_pct": [0.0] * n,
        }
    )


def _xwalk(n: int = 30, matched: int | None = None) -> pl.DataFrame:
    matched = n if matched is None else matched
    return pl.DataFrame(
        {
            "pfr_id": [f"Pfr{i:04d}" for i in range(matched)],
            "gsis_id": [f"00-{i:07d}" for i in range(matched)],
        }
    )


class FixtureIngester(NflverseSnapCountsIngester):
    def __init__(self, snaps, xwalk, **kw):
        super().__init__(**kw)
        self._payload = {"snaps": snaps, "xwalk": xwalk}

    def fetch(self):
        return self._payload


def test_validate_counts_matched_skill_rows():
    ing = FixtureIngester(_snaps(), _xwalk(), seasons=[2025])
    assert ing.validate(ing.fetch()) == 30


def test_validate_rejects_missing_columns():
    bad = _snaps().drop("offense_pct")
    ing = FixtureIngester(bad, _xwalk(), seasons=[2025])
    with pytest.raises(IngestError, match="offense_pct"):
        ing.validate(ing.fetch())


def test_validate_rejects_low_match_rate():
    ing = FixtureIngester(_snaps(30), _xwalk(30, matched=20), seasons=[2025])
    with pytest.raises(IngestError, match="match rate"):
        ing.validate(ing.fetch())


def test_validate_rejects_empty_frame():
    ing = FixtureIngester(_snaps(0), _xwalk(), seasons=[2025])
    with pytest.raises(IngestError, match="zero rows"):
        ing.validate(ing.fetch())


def test_non_skill_and_postseason_rows_are_excluded():
    # Offensive linemen and playoff games are not usage signal for this league.
    mixed = pl.concat([_snaps(20), _snaps(20, position="T"), _snaps(20, game_type="POST")])
    ing = FixtureIngester(mixed, _xwalk(20), seasons=[2025])
    assert ing.validate({"snaps": mixed, "xwalk": _xwalk(20)}) == 20


def test_store_writes_resolved_gsis_rows(db):
    ing = FixtureIngester(_snaps(30), _xwalk(30), seasons=[2025])
    run_id = ing.run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT gsis_id, season, week, team, position, offense_pct "
            "FROM raw.nflverse_snap_counts ORDER BY gsis_id LIMIT 1"
        )
        row = cur.fetchone()
        cur.execute("SELECT count(*) FROM raw.nflverse_snap_counts")
        total = cur.fetchone()[0]
        cur.execute("SELECT status FROM raw.ingest_runs WHERE run_id=%s", (run_id,))
        assert cur.fetchone()[0] == "success"
    assert total == 30
    assert row[0] == "00-0000000"
    assert row[1:5] == (2025, 1, "SF", "RB")
    assert row[5] == pytest.approx(0.5, abs=1e-4)


def test_rerun_replaces_the_season_rather_than_duplicating(db):
    FixtureIngester(_snaps(30), _xwalk(30), seasons=[2025]).run(db)
    FixtureIngester(_snaps(30), _xwalk(30), seasons=[2025]).run(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.nflverse_snap_counts")
        assert cur.fetchone()[0] == 30
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `uv run pytest tests/test_nflverse_snaps.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.ingest.nflverse_snaps'`.

- [ ] **Step 5: Write the ingester**

Create `src/ffi/ingest/nflverse_snaps.py`:

```python
"""nflverse snap counts -> raw.nflverse_snap_counts (R27).

raw.nflverse_player_week carries no snap columns, so without this feed the
trend engine's headline rule has no input. nflverse publishes `offense_pct`
directly (a share, not a count over a denominator we compute), which is why
the R5 partial-publish short-denominator failure cannot reach snap_share.

The feed is keyed by `pfr_player_id`; every other table in this repo is
keyed by `gsis_id`. The resolution happens HERE, once, and a match rate
below MIN_MATCH_RATE is a hard failure — silently dropping unresolved
players would make a star's missing week look like a benching, which is
exactly the false FALLING signal the precision gate exists to prevent.
"""
import polars as pl
import psycopg2.extras

from ffi.ingest.base import BaseIngester, IngestError

REQUIRED_COLS = {
    "season",
    "week",
    "game_type",
    "player",
    "pfr_player_id",
    "position",
    "team",
    "offense_snaps",
    "offense_pct",
}
# The only positions this league's usage rules score. Offensive linemen are
# ~40% of the feed and carry no fantasy signal.
SKILL_POSITIONS = ("QB", "RB", "WR", "TE", "FB")
DB_COLS = ["gsis_id", "season", "week", "team", "position", "offense_snaps", "offense_pct"]


class NflverseSnapCountsIngester(BaseIngester):
    source = "nflverse_snap_counts"

    # 30 of 30 in fixtures; live 2025 resolution is ~0.97 for skill players.
    # A drop below this means the pfr_id -> gsis_id crosswalk changed shape,
    # not that players went missing.
    MIN_MATCH_RATE = 0.90

    def __init__(self, seasons: list[int]):
        self.seasons = seasons

    def fetch(self) -> dict:
        import nflreadpy

        return {
            "snaps": nflreadpy.load_snap_counts(seasons=self.seasons),
            "xwalk": nflreadpy.load_players()
            .select(["pfr_id", "gsis_id"])
            .drop_nulls(["pfr_id", "gsis_id"]),
        }

    @staticmethod
    def _skill_regular(snaps: pl.DataFrame) -> pl.DataFrame:
        return snaps.filter(
            (pl.col("game_type") == "REG") & pl.col("position").is_in(SKILL_POSITIONS)
        )

    def _resolve(self, payload: dict) -> pl.DataFrame:
        rows = self._skill_regular(payload["snaps"])
        return rows.join(
            payload["xwalk"], left_on="pfr_player_id", right_on="pfr_id", how="inner"
        )

    def validate(self, payload) -> int:
        snaps = payload["snaps"]
        missing = REQUIRED_COLS - set(snaps.columns)
        if missing:
            raise IngestError(
                f"nflverse_snap_counts: expected columns missing: {sorted(missing)}. "
                f"Actual: {sorted(snaps.columns)}. Schema drift — investigate, "
                f"do not rename blindly."
            )
        eligible = self._skill_regular(snaps)
        if eligible.height == 0:
            raise IngestError(
                f"nflverse_snap_counts: zero rows for seasons {self.seasons} after "
                f"filtering to REG + {list(SKILL_POSITIONS)}"
            )
        matched = self._resolve(payload)
        rate = matched.height / eligible.height
        if rate < self.MIN_MATCH_RATE:
            raise IngestError(
                f"nflverse_snap_counts: pfr_id -> gsis_id match rate {rate:.3f} "
                f"({matched.height}/{eligible.height}) below floor "
                f"{self.MIN_MATCH_RATE} — refusing to load a feed with a hole in "
                f"it; unresolved players read downstream as benched (false FALLING)"
            )
        return matched.height

    def store(self, conn, run_id: int, payload) -> None:
        matched = self._resolve(payload)
        rows = matched.select(
            [
                pl.col("gsis_id"),
                pl.col("season"),
                pl.col("week"),
                pl.col("team"),
                pl.col("position"),
                pl.col("offense_snaps"),
                pl.col("offense_pct"),
            ]
        ).rows()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM raw.nflverse_snap_counts WHERE season = ANY(%s)",
                (self.seasons,),
            )
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO raw.nflverse_snap_counts ({', '.join(DB_COLS)}) VALUES %s",
                rows,
                page_size=5000,
            )

    def _first_record(self, payload):
        return {c: None for c in payload["snaps"].columns}
```

- [ ] **Step 6: Write the entry point**

Create `scripts/ingest_nflverse_snaps.py`:

```python
#!/usr/bin/env python3
"""Load nflverse snap counts into raw.nflverse_snap_counts.

Season window is shared with scripts/ingest_nflverse.py via
ffi.ingest.nflverse.parse_seasons so the two feeds can never drift apart.
"""
import argparse

from ffi.db import connect
from ffi.ingest.nflverse import parse_seasons
from ffi.ingest.nflverse_snaps import NflverseSnapCountsIngester

parser = argparse.ArgumentParser()
parser.add_argument("--seasons", default="2019-2025", help="e.g. 2019-2025 or 2025")
args = parser.parse_args()
run_id = NflverseSnapCountsIngester(seasons=parse_seasons(args.seasons)).run(connect())
print(f"OK run_id={run_id}")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_nflverse_snaps.py -q`
Expected: `7 passed`

- [ ] **Step 8: Backfill the live feed**

Run:
```bash
uv run python scripts/ingest_nflverse_snaps.py --seasons 2019-2025
psql -d fantasy_football -c "SELECT season, count(*), round(avg(offense_pct)::numeric,3) FROM raw.nflverse_snap_counts GROUP BY 1 ORDER BY 1"
```
Expected: `OK run_id=<n>`, then seven rows (2019–2025), each with roughly 9,000–11,000 rows and a mean `offense_pct` between 0.35 and 0.55.

Note: `nflreadpy.load_snap_counts` raises `ValueError: Season must be between 2012 and 2025` for any season past `get_current_season()`, which returns 2025 until **2026-09-10**. Use `2019-2025` today.

- [ ] **Step 9: Add the step to the morning chain**

In `scripts/morning_chain.sh`, insert immediately after the `ingest_nflverse.py` line:

```bash
step uv run python scripts/ingest_nflverse_snaps.py --seasons "$FFI_NFLVERSE_SEASONS"
```

- [ ] **Step 10: Re-run the briefing to confirm the new health line resolves**

Run: `uv run python scripts/morning_briefing.py; grep nflverse reports/briefing-$(date +%F).md`
Expected: two health lines (`[OK] nflverse_player_week: last run …`, `[OK] nflverse_snap_counts: last run …`) and two expected-season lines (`[OK] nflverse_player_week: season 2025 through week 18`, `[OK] nflverse_snap_counts: season 2025 through week 18`).

- [ ] **Step 11: Run the whole briefing test file now that the table exists**

Run: `uv run pytest tests/test_morning_briefing.py -q`
Expected: `8 passed`

- [ ] **Step 12: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add migrations/011_nflverse_snap_counts.sql src/ffi/ingest/nflverse_snaps.py \
        scripts/ingest_nflverse_snaps.py tests/test_nflverse_snaps.py scripts/morning_chain.sh
git commit -m "feat(ingest): nflverse snap counts -> raw.nflverse_snap_counts

raw.nflverse_player_week has no snap/route/red-zone columns (verified
2026-08-31), so the trend engine's headline rule had no input. nflverse
publishes offense_pct directly. pfr_id -> gsis_id resolution happens once,
here, and a match rate under 0.90 hard-fails: silently dropping unresolved
players makes a star's missing week look like a benching (R27)."
```

---

### Task 8: `src/ffi/usage/build.py` — `usage_weekly` with the partial-week guard

**Files:**
- Create: `migrations/012_usage_weekly.sql`
- Create: `src/ffi/usage/__init__.py`
- Create: `src/ffi/usage/build.py`
- Create: `tests/test_usage_build.py`
- Modify: `ARCHITECTURE.md` (§1b new row, §4 signature)

**Interfaces:**
- Consumes: `ffi.db.connect()`; tables `raw.nflverse_player_week` (columns `gsis_id, season, week, team, position, targets, carries`), `raw.nflverse_snap_counts` (Task 7).
- Produces (all importable from `ffi.usage`):
  - `METRICS: tuple[str, ...]` = `("snap_share", "target_share", "carry_share", "route_share", "rz_touches")`
  - `UsageRow` frozen dataclass: `gsis_id: str`, `season: int`, `week: int`, `team: str`, `position: str | None`, `snap_share: float | None`, `target_share: float | None`, `carry_share: float | None`, `route_share: float | None`, `rz_touches: int | None`, `team_targets: int`, `team_carries: int`, `games_complete: int`, `teams_observed: int`
  - `UsageFrame` frozen dataclass: `season: int`, `week: int`, `rows: tuple[UsageRow, ...]`, `available_metrics: frozenset[str]`, `disabled_metrics: tuple[str, ...]`, `teams_observed: int`
  - `ffi.usage.build.build_usage_weekly(conn, season: int, week: int) -> UsageFrame`
  - `ffi.usage.build.store_usage_weekly(conn, frame: UsageFrame) -> int`
  - `ffi.usage.build.load_usage_weekly(conn, season: int, week: int) -> UsageFrame`
  - Table `public.usage_weekly`. Task 9's `classify` consumes `UsageFrame`.

- [ ] **Step 1: Write the migration**

Create `migrations/012_usage_weekly.sql`:

```sql
-- 012_usage_weekly.sql — the per-player-week usage table (ADR Domain 2).
--
-- Share metrics are NULLABLE ON PURPOSE (R5): when a team-week is
-- incompletely published, the builder writes NULL and logs, rather than
-- dividing by a short denominator. A short denominator inflates every
-- share on that team and fires a false ASCENDING for the whole backfield.
-- `games_complete` (0/1 for this player's team-week) and `teams_observed`
-- (distinct teams in the slate) make the refusal auditable after the fact.
--
-- route_share and rz_touches are declared here but are ALWAYS NULL in Plan
-- 1: nflverse participation data ends in 2023 and red-zone touches need a
-- pbp feed. Their rules are disabled by the `requires` mechanism in
-- ffi.usage.trends and announced in the briefing — degrade by removal, not
-- by silent null (R27). The columns exist so Plan 2 can fill them without a
-- migration.
CREATE TABLE IF NOT EXISTS public.usage_weekly (
    gsis_id        text NOT NULL,
    season         integer NOT NULL,
    week           integer NOT NULL,
    team           text NOT NULL,
    position       text,
    snap_share     real,
    target_share   real,
    carry_share    real,
    route_share    real,
    rz_touches     integer,
    team_targets   integer NOT NULL,
    team_carries   integer NOT NULL,
    games_complete integer NOT NULL CHECK (games_complete IN (0,1)),
    teams_observed integer NOT NULL,
    computed_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gsis_id, season, week)
);
CREATE INDEX IF NOT EXISTS idx_usage_weekly_season_week
    ON public.usage_weekly (season, week);
```

- [ ] **Step 2: Apply the migration**

Run:
```bash
psql -d fantasy_football -f migrations/012_usage_weekly.sql
psql -d fantasy_football_test -f migrations/012_usage_weekly.sql
psql -d fantasy_football -c "\d public.usage_weekly" | head -20
```
Expected: `CREATE TABLE` / `CREATE INDEX`, then the 15 columns listed.

Note: `tests/conftest.py` truncates only `raw`, `scoring`, `valuation`, `signals`, `sim`, `draft` plus a named list of `public` tables. Add `public.usage_weekly` to that named list in the next step so tests do not leak rows between cases.

- [ ] **Step 3: Add `public.usage_weekly` to the test truncation list**

In `tests/conftest.py`, change:

```python
        cur.execute(
            "TRUNCATE public.player_id_xwalk, public.matchup_results, "
            "public.manager_slot_annotations, public.leagues RESTART IDENTITY CASCADE"
        )
```

to:

```python
        cur.execute(
            "TRUNCATE public.player_id_xwalk, public.matchup_results, "
            "public.manager_slot_annotations, public.leagues, public.usage_weekly "
            "RESTART IDENTITY CASCADE"
        )
```

- [ ] **Step 4: Write the failing tests**

Create `tests/test_usage_build.py`:

```python
import pytest

from ffi.usage import METRICS, UsageFrame, UsageRow
from ffi.usage.build import build_usage_weekly, load_usage_weekly, store_usage_weekly


def _seed(db, rows, snaps=()):
    """rows: (gsis_id, team, position, targets, carries)
    snaps: (gsis_id, team, position, offense_pct)"""
    with db.cursor() as cur:
        for gsis_id, team, position, targets, carries in rows:
            cur.execute(
                "INSERT INTO raw.nflverse_player_week "
                "(gsis_id, season, week, team, position, targets, carries) "
                "VALUES (%s, 2025, 3, %s, %s, %s, %s)",
                (gsis_id, team, position, targets, carries),
            )
        for gsis_id, team, position, pct in snaps:
            cur.execute(
                "INSERT INTO raw.nflverse_snap_counts "
                "(gsis_id, season, week, team, position, offense_snaps, offense_pct) "
                "VALUES (%s, 2025, 3, %s, %s, %s, %s)",
                (gsis_id, team, position, pct * 70, pct),
            )
    db.commit()


# A complete team-week: 34 targets + 26 carries = 60 plays, over the floor.
COMPLETE_SF = [
    ("00-0000001", "SF", "WR", 12, 0),
    ("00-0000002", "SF", "WR", 10, 0),
    ("00-0000003", "SF", "TE", 8, 0),
    ("00-0000004", "SF", "RB", 4, 20),
    ("00-0000005", "SF", "QB", 0, 6),
]


def test_shares_computed_on_a_complete_team_week(db):
    _seed(db, COMPLETE_SF, snaps=[("00-0000001", "SF", "WR", 0.82)])
    frame = build_usage_weekly(db, 2025, 3)
    by_id = {r.gsis_id: r for r in frame.rows}
    assert by_id["00-0000001"].target_share == pytest.approx(12 / 34)
    assert by_id["00-0000004"].carry_share == pytest.approx(20 / 26)
    assert by_id["00-0000001"].snap_share == pytest.approx(0.82)
    assert all(r.games_complete == 1 for r in frame.rows)


def test_partial_team_week_refuses_to_compute_shares(db):
    # 6 targets + 3 carries = 9 plays: nflverse is mid-publish, not the 49ers
    # having run nine plays. A short denominator here would put one WR at
    # 100% target share and fire a false ASCENDING for the whole team.
    _seed(db, [("00-0000009", "SF", "WR", 6, 3)])
    frame = build_usage_weekly(db, 2025, 3)
    row = frame.rows[0]
    assert row.games_complete == 0
    assert row.target_share is None
    assert row.carry_share is None
    # The raw counts are still recorded — the refusal must be auditable.
    assert (row.team_targets, row.team_carries) == (6, 3)


def test_snap_share_is_kept_on_a_partial_week(db):
    # snap_share is published by nflverse as a share, not derived from a
    # denominator we compute, so the partial-publish guard does not apply.
    _seed(db, [("00-0000009", "SF", "WR", 6, 3)], snaps=[("00-0000009", "SF", "WR", 0.9)])
    row = build_usage_weekly(db, 2025, 3).rows[0]
    assert row.games_complete == 0
    assert row.snap_share == pytest.approx(0.9)


def test_unavailable_metrics_are_null_and_announced(db):
    _seed(db, COMPLETE_SF)
    frame = build_usage_weekly(db, 2025, 3)
    assert frame.disabled_metrics == ("route_share", "rz_touches")
    assert frame.available_metrics == frozenset({"snap_share", "target_share", "carry_share"})
    assert all(r.route_share is None and r.rz_touches is None for r in frame.rows)
    assert set(METRICS) == frame.available_metrics | set(frame.disabled_metrics)


def test_teams_observed_counts_distinct_teams(db):
    _seed(db, COMPLETE_SF + [("00-0000010", "SEA", "WR", 30, 25)])
    frame = build_usage_weekly(db, 2025, 3)
    assert frame.teams_observed == 2
    assert all(r.teams_observed == 2 for r in frame.rows)


def test_empty_week_raises_rather_than_returning_an_empty_frame(db):
    with pytest.raises(ValueError, match="no rows"):
        build_usage_weekly(db, 2025, 3)


def test_store_and_load_round_trip(db):
    _seed(db, COMPLETE_SF, snaps=[("00-0000001", "SF", "WR", 0.82)])
    frame = build_usage_weekly(db, 2025, 3)
    assert store_usage_weekly(db, frame) == 5
    loaded = load_usage_weekly(db, 2025, 3)
    assert {r.gsis_id for r in loaded.rows} == {r.gsis_id for r in frame.rows}
    assert loaded.available_metrics == frame.available_metrics
    by_id = {r.gsis_id: r for r in loaded.rows}
    assert by_id["00-0000001"].target_share == pytest.approx(12 / 34, abs=1e-6)


def test_store_is_idempotent(db):
    _seed(db, COMPLETE_SF)
    frame = build_usage_weekly(db, 2025, 3)
    store_usage_weekly(db, frame)
    store_usage_weekly(db, frame)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.usage_weekly")
        assert cur.fetchone()[0] == 5
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `uv run pytest tests/test_usage_build.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.usage'`.

- [ ] **Step 6: Write the shared value types**

Create `src/ffi/usage/__init__.py`:

```python
"""Shared value types for the usage package.

These live in the package __init__ rather than in build.py or trends.py so
that build -> trends -> coldstart can all reference one set of types with no
import cycle. This module imports nothing internal.
"""
from dataclasses import dataclass

# Every metric the trend rules may reference. A rule declares the subset it
# `requires`; a metric absent from a frame's `available_metrics` disables
# every rule that needs it, loudly (R27 — degrade by removal, never by
# silent null-handling).
METRICS = ("snap_share", "target_share", "carry_share", "route_share", "rz_touches")

ASCENDING = "ASCENDING"
FALLING = "FALLING"
WATCH = "WATCH"
DIRECTIONS = (ASCENDING, FALLING, WATCH)


@dataclass(frozen=True)
class UsageRow:
    gsis_id: str
    season: int
    week: int
    team: str
    position: str | None
    snap_share: float | None
    target_share: float | None
    carry_share: float | None
    route_share: float | None
    rz_touches: int | None
    team_targets: int
    team_carries: int
    # 1 when this player's team-week passed the completeness floor, else 0.
    # When 0, every share this builder DERIVES is None (R5).
    games_complete: int
    # Distinct teams present in this (season, week) slate.
    teams_observed: int


@dataclass(frozen=True)
class UsageFrame:
    season: int
    week: int
    rows: tuple[UsageRow, ...]
    available_metrics: frozenset[str]
    disabled_metrics: tuple[str, ...]
    teams_observed: int


@dataclass(frozen=True)
class Rule:
    rule_id: str
    direction: str
    min_weeks: int
    requires: frozenset[str]
    cold_start: bool
    describe: str


@dataclass(frozen=True)
class TrendSignal:
    gsis_id: str
    direction: str
    rule_id: str
    evidence: str
    cold_start: bool


@dataclass(frozen=True)
class TrendResult:
    season: int
    week: int
    signals: tuple[TrendSignal, ...]
    # (rule_id, reason) for every rule that could not run this week.
    disabled_rules: tuple[tuple[str, str], ...]
```

- [ ] **Step 7: Write the builder**

Create `src/ffi/usage/build.py`:

```python
"""Derive public.usage_weekly from the nflverse feeds (ADR Domain 2).

The partial-publish guard (R5) is the load-bearing part. nflverse publishes
incrementally: a team-week can appear with three players in it. Dividing by
that short denominator puts one receiver at 100% target share and fires a
false ASCENDING for an entire backfield. This module refuses instead: shares
become NULL, `games_complete` is 0, and a structlog line records the
refusal. A NULL that says "unknown" is worth more than a number that says
"100%" and means "we caught nflverse mid-write".
"""
import structlog

from ffi.usage import METRICS, UsageFrame, UsageRow

log = structlog.get_logger()

# A real NFL team-game has ~55-70 (targets + carries). 40 clears every
# legitimate low-volume game (the lowest 2019-2025 team-game is 44) while
# tripping on any mid-publish fragment.
MIN_TEAM_PLAYS = 40

# Plan 1 has no participation feed (nflverse coverage ends 2023) and no pbp
# feed, so these two are structurally unavailable. Declared here, not
# inferred from NULLs, so the disabling is a decision the briefing can name.
UNAVAILABLE_METRICS = ("route_share", "rz_touches")

_STATS_QUERY = """
    SELECT gsis_id, team, position,
           coalesce(targets, 0) AS targets,
           coalesce(carries, 0) AS carries
    FROM raw.nflverse_player_week
    WHERE season = %s AND week = %s AND team IS NOT NULL
"""
_SNAPS_QUERY = """
    SELECT gsis_id, offense_pct
    FROM raw.nflverse_snap_counts
    WHERE season = %s AND week = %s
"""


def _share(numerator: int, denominator: int) -> float | None:
    return None if denominator <= 0 else numerator / denominator


def build_usage_weekly(conn, season: int, week: int) -> UsageFrame:
    with conn.cursor() as cur:
        cur.execute(_STATS_QUERY, (season, week))
        stats = cur.fetchall()
        cur.execute(_SNAPS_QUERY, (season, week))
        snaps = {gsis_id: pct for gsis_id, pct in cur.fetchall()}
    if not stats:
        raise ValueError(
            f"build_usage_weekly: raw.nflverse_player_week has no rows for "
            f"season {season} week {week} — run scripts/ingest_nflverse.py"
        )

    team_targets: dict[str, int] = {}
    team_carries: dict[str, int] = {}
    for _, team, _, targets, carries in stats:
        team_targets[team] = team_targets.get(team, 0) + targets
        team_carries[team] = team_carries.get(team, 0) + carries
    teams_observed = len(team_targets)

    incomplete = sorted(
        team
        for team in team_targets
        if team_targets[team] + team_carries[team] < MIN_TEAM_PLAYS
    )
    if incomplete:
        log.warning(
            "usage.partial_team_weeks",
            season=season,
            week=week,
            teams=incomplete,
            floor=MIN_TEAM_PLAYS,
            note="share metrics refused (NULL) for these teams — R5 partial publish",
        )

    rows = []
    for gsis_id, team, position, targets, carries in stats:
        complete = 0 if team in set(incomplete) else 1
        rows.append(
            UsageRow(
                gsis_id=gsis_id,
                season=season,
                week=week,
                team=team,
                position=position,
                # Published as a share by nflverse, not derived from a
                # denominator this module computes, so the partial-publish
                # guard does not gate it.
                snap_share=(None if gsis_id not in snaps else float(snaps[gsis_id])),
                target_share=(_share(targets, team_targets[team]) if complete else None),
                carry_share=(_share(carries, team_carries[team]) if complete else None),
                route_share=None,
                rz_touches=None,
                team_targets=team_targets[team],
                team_carries=team_carries[team],
                games_complete=complete,
                teams_observed=teams_observed,
            )
        )

    available = frozenset(m for m in METRICS if m not in UNAVAILABLE_METRICS)
    return UsageFrame(
        season=season,
        week=week,
        rows=tuple(rows),
        available_metrics=available,
        disabled_metrics=UNAVAILABLE_METRICS,
        teams_observed=teams_observed,
    )


def store_usage_weekly(conn, frame: UsageFrame) -> int:
    """Upsert the frame. Returns the row count written."""
    with conn.cursor() as cur:
        for r in frame.rows:
            cur.execute(
                """INSERT INTO public.usage_weekly
                   (gsis_id, season, week, team, position, snap_share, target_share,
                    carry_share, route_share, rz_touches, team_targets, team_carries,
                    games_complete, teams_observed, computed_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
                   ON CONFLICT (gsis_id, season, week) DO UPDATE SET
                       team=EXCLUDED.team, position=EXCLUDED.position,
                       snap_share=EXCLUDED.snap_share,
                       target_share=EXCLUDED.target_share,
                       carry_share=EXCLUDED.carry_share,
                       route_share=EXCLUDED.route_share,
                       rz_touches=EXCLUDED.rz_touches,
                       team_targets=EXCLUDED.team_targets,
                       team_carries=EXCLUDED.team_carries,
                       games_complete=EXCLUDED.games_complete,
                       teams_observed=EXCLUDED.teams_observed,
                       computed_at=now()""",
                (
                    r.gsis_id, r.season, r.week, r.team, r.position, r.snap_share,
                    r.target_share, r.carry_share, r.route_share, r.rz_touches,
                    r.team_targets, r.team_carries, r.games_complete, r.teams_observed,
                ),
            )
    conn.commit()
    return len(frame.rows)


def load_usage_weekly(conn, season: int, week: int) -> UsageFrame:
    with conn.cursor() as cur:
        cur.execute(
            """SELECT gsis_id, season, week, team, position, snap_share, target_share,
                      carry_share, route_share, rz_touches, team_targets, team_carries,
                      games_complete, teams_observed
               FROM public.usage_weekly WHERE season=%s AND week=%s ORDER BY gsis_id""",
            (season, week),
        )
        rows = tuple(UsageRow(*r) for r in cur.fetchall())
    if not rows:
        raise ValueError(
            f"load_usage_weekly: public.usage_weekly has no rows for season "
            f"{season} week {week} — run build_usage_weekly + store_usage_weekly"
        )
    available = frozenset(m for m in METRICS if m not in UNAVAILABLE_METRICS)
    return UsageFrame(
        season=season,
        week=week,
        rows=rows,
        available_metrics=available,
        disabled_metrics=UNAVAILABLE_METRICS,
        teams_observed=rows[0].teams_observed,
    )
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `uv run pytest tests/test_usage_build.py -q`
Expected: `8 passed`

- [ ] **Step 9: Build a real week and eyeball it**

Run:
```bash
uv run python -c "
from ffi.db import connect
from ffi.usage.build import build_usage_weekly, store_usage_weekly
conn = connect()
f = build_usage_weekly(conn, 2025, 3)
print('teams_observed', f.teams_observed, 'rows', len(f.rows))
print('disabled', f.disabled_metrics)
print('incomplete rows', sum(1 for r in f.rows if r.games_complete == 0))
top = sorted((r for r in f.rows if r.target_share), key=lambda r: -r.target_share)[:3]
for r in top: print(f'{r.gsis_id} {r.team} {r.position} tgt_share={r.target_share:.3f} snap={r.snap_share}')
print('wrote', store_usage_weekly(conn, f))
"
```
Expected: `teams_observed 32` (2025 week 3 had no byes), around 900–1,100 rows, `disabled ('route_share', 'rz_touches')`, `incomplete rows 0`, three plausible top-target-share lines with `tgt_share` between 0.25 and 0.40, and a written row count matching the row count.

- [ ] **Step 10: Amend ARCHITECTURE.md (amendments 4 and 5 of 8)**

In ARCHITECTURE.md §1b, insert this row immediately above the `src/ffi/usage/build.py` row:

```
| `src/ffi/usage/__init__.py` | Shared value types for the usage package (`UsageRow`, `UsageFrame`, `Rule`, `TrendSignal`, `TrendResult`, `METRICS`). Leaf: imports nothing internal, so build -> trends -> coldstart stays acyclic | 120 |
```

In ARCHITECTURE.md §4, replace the row

```
| Usage table build | `build_usage_weekly(week) -> UsageFrame` (carries `games_complete`, `teams_observed`) | `src/ffi/usage/build.py` |
```

with

```
| Usage table build | `build_usage_weekly(conn, season: int, week: int) -> UsageFrame` (carries `games_complete`, `teams_observed`); `store_usage_weekly(conn, frame) -> int`; `load_usage_weekly(conn, season, week) -> UsageFrame` | `src/ffi/usage/build.py` |
```

- [ ] **Step 11: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add migrations/012_usage_weekly.sql src/ffi/usage/__init__.py src/ffi/usage/build.py \
        tests/test_usage_build.py tests/conftest.py ARCHITECTURE.md
git commit -m "feat(usage): usage_weekly builder with the partial-publish guard

R5: nflverse publishes incrementally, so a team-week can appear with three
players in it. Dividing by that denominator puts one receiver at 100% target
share and fires a false ASCENDING for a whole backfield. Below the 40-play
floor the builder writes NULL with games_complete=0 and a structlog line
rather than a short-denominator number. route_share and rz_touches have no
feed in Plan 1 and are declared unavailable, not silently nulled (R27)."
```

---

### Task 9: `src/ffi/usage/trends.py` + `src/ffi/usage/coldstart.py` — the rule engine

Engine and unit fixtures only. **The precision gate (≤8 ASCENDING/week at ≥40% precision against the 2025 replay) is Plan 2 scope** — do not tune any threshold here; the thresholds below are the starting points that Plan 2 will tune on 2024 and gate on 2025.

**Files:**
- Create: `src/ffi/usage/trends.py`
- Create: `src/ffi/usage/coldstart.py`
- Create: `tests/test_usage_trends.py`
- Modify: `ARCHITECTURE.md` (§4 signature)

**Interfaces:**
- Consumes: `ffi.usage.{UsageRow, UsageFrame, Rule, TrendSignal, TrendResult, ASCENDING, FALLING, WATCH}` (Task 8).
- Produces:
  - `ffi.usage.trends.classify(frames: Sequence[UsageFrame], week: int) -> TrendResult` — `frames` ordered oldest→newest; `frames[-1].week == week`.
  - `ffi.usage.trends.STANDARD_RULES: tuple[Rule, ...]`
  - `ffi.usage.coldstart.COLD_START_RULES: tuple[Rule, ...]`, `ffi.usage.coldstart.COLD_START_MAX_WEEK: int = 3`
  - Every emitted `TrendSignal` carries `rule_id` and a human-readable `evidence` string, so a false ASCENDING in week 6 is traceable to the rule and threshold that fired it (ADR Domain 5).

- [ ] **Step 1: Write the failing tests with all three fixtures**

Create `tests/test_usage_trends.py`:

```python
import pytest

from ffi.usage import ASCENDING, FALLING, WATCH, UsageFrame, UsageRow
from ffi.usage.coldstart import COLD_START_MAX_WEEK, COLD_START_RULES
from ffi.usage.trends import STANDARD_RULES, classify

ALL_METRICS = frozenset({"snap_share", "target_share", "carry_share", "route_share", "rz_touches"})


def _row(gsis_id, week, **kw):
    base = dict(
        gsis_id=gsis_id, season=2025, week=week, team="SF", position="RB",
        snap_share=None, target_share=None, carry_share=None, route_share=None,
        rz_touches=None, team_targets=34, team_carries=26,
        games_complete=1, teams_observed=32,
    )
    base.update(kw)
    return UsageRow(**base)


def _frames(rows_by_week: dict, available=ALL_METRICS) -> list[UsageFrame]:
    disabled = tuple(sorted(ALL_METRICS - available))
    return [
        UsageFrame(
            season=2025, week=w, rows=tuple(rows), available_metrics=available,
            disabled_metrics=disabled, teams_observed=32,
        )
        for w, rows in sorted(rows_by_week.items())
    ]


# --- Fixture 1: a backfield flip ------------------------------------------
# BACKUP takes over across weeks 4-6: snaps cross 55%, target share steps up
# more than 5pp over its own 3-week baseline, red-zone touches climb strictly.
# STARTER's route share collapses to under 60% of baseline.
def _backfield_flip_frames():
    weeks = {}
    for w, (b_snap, b_tgt, b_rz, s_route) in {
        3: (0.30, 0.06, 1, 0.80),
        4: (0.35, 0.07, 1, 0.78),
        5: (0.62, 0.13, 2, 0.79),
        6: (0.71, 0.15, 4, 0.30),
    }.items():
        weeks[w] = [
            _row("BACKUP", w, snap_share=b_snap, target_share=b_tgt, rz_touches=b_rz,
                 route_share=0.5, carry_share=b_snap),
            _row("STARTER", w, snap_share=0.9 - b_snap, target_share=0.20,
                 rz_touches=5, route_share=s_route, carry_share=0.5),
        ]
    return _frames(weeks)


def test_backfield_flip_fires_ascending_for_the_backup():
    result = classify(_backfield_flip_frames(), week=6)
    backup = [s for s in result.signals if s.gsis_id == "BACKUP"]
    assert {s.rule_id for s in backup} >= {"snap_rise_2wk", "target_share_step", "rz_climb_3wk"}
    assert all(s.direction == ASCENDING for s in backup)
    assert all(not s.cold_start for s in backup)


def test_backfield_flip_fires_falling_for_the_displaced_starter():
    result = classify(_backfield_flip_frames(), week=6)
    starter = [s for s in result.signals if s.gsis_id == "STARTER"]
    assert [s.rule_id for s in starter] == ["route_collapse"]
    assert starter[0].direction == FALLING


def test_every_signal_carries_traceable_evidence():
    for s in classify(_backfield_flip_frames(), week=6).signals:
        assert s.rule_id
        assert s.evidence
        assert str(round(6)) in s.evidence or "wk" in s.evidence


# --- Fixture 2: nothing happened ------------------------------------------
def _no_change_frames():
    weeks = {
        w: [
            _row("STEADY", w, snap_share=0.70, target_share=0.18, rz_touches=2,
                 route_share=0.72, carry_share=0.40)
        ]
        for w in (3, 4, 5, 6)
    }
    return _frames(weeks)


def test_no_change_fixture_emits_nothing():
    """The negative fixture. R10: round-number rules tuned on a single
    positive fixture produce 40+ flags a week against a 5-move budget."""
    assert classify(_no_change_frames(), week=6).signals == ()


def test_a_workhorse_above_55pct_every_week_does_not_re_fire():
    # STEADY is at 70% snaps in all four weeks. Without the crossing guard,
    # snap_rise_2wk would flag every workhorse in the league, every week.
    assert not [s for s in classify(_no_change_frames(), week=6).signals
                if s.rule_id == "snap_rise_2wk"]


# --- Fixture 3: cold start, week 2 ---------------------------------------
def _cold_start_frames():
    weeks = {
        1: [_row("ROOKIE", 1, snap_share=0.30, target_share=0.05, route_share=0.40)],
        2: [_row("ROOKIE", 2, snap_share=0.68, target_share=0.14, route_share=0.60)],
    }
    return _frames(weeks)


def test_cold_start_week_2_is_not_silent():
    """R7: an empty ASC/FALL section in weeks 1-3 is not acceptable — those
    are the weeks league-winners are disproportionately claimed."""
    result = classify(_cold_start_frames(), week=2)
    assert result.signals != ()
    assert {s.rule_id for s in result.signals} >= {"snap_rise_1wk_cs", "target_share_step_cs"}
    assert all(s.cold_start for s in result.signals)


def test_cold_start_rules_are_inactive_after_week_3():
    result = classify(_backfield_flip_frames(), week=6)
    assert all(not s.cold_start for s in result.signals)
    assert COLD_START_MAX_WEEK == 3


def test_standard_rules_are_inactive_during_cold_start():
    result = classify(_cold_start_frames(), week=2)
    assert all(s.rule_id.endswith("_cs") for s in result.signals)


# --- Metric availability --------------------------------------------------
def test_rules_needing_an_unavailable_metric_are_disabled_loudly():
    available = frozenset({"snap_share", "target_share", "carry_share"})
    weeks = {w: [_row("X", w, snap_share=0.7, target_share=0.2)] for w in (3, 4, 5, 6)}
    result = classify(_frames(weeks, available=available), week=6)
    disabled = dict(result.disabled_rules)
    assert "rz_climb_3wk" in disabled
    assert "route_collapse" in disabled
    assert "rz_touches" in disabled["rz_climb_3wk"]
    assert not [s for s in result.signals if s.rule_id in disabled]


def test_rules_needing_more_weeks_than_exist_are_disabled_loudly():
    weeks = {5: [_row("X", 5, snap_share=0.7)], 6: [_row("X", 6, snap_share=0.7)]}
    result = classify(_frames(weeks), week=6)
    disabled = dict(result.disabled_rules)
    assert "target_share_step" in disabled
    assert "min_weeks" in disabled["target_share_step"]


def test_classify_rejects_frames_that_do_not_end_at_the_requested_week():
    with pytest.raises(ValueError, match="newest frame"):
        classify(_no_change_frames(), week=9)


def test_classify_rejects_unordered_frames():
    frames = list(reversed(_no_change_frames()))
    with pytest.raises(ValueError, match="oldest-to-newest"):
        classify(frames, week=3)


def test_incomplete_team_weeks_never_produce_a_signal():
    weeks = {
        w: [_row("X", w, snap_share=0.7, target_share=0.30, games_complete=0)]
        for w in (3, 4, 5, 6)
    }
    assert classify(_frames(weeks), week=6).signals == ()


def test_rule_catalogues_are_disjoint_and_well_formed():
    ids = [r.rule_id for r in STANDARD_RULES + COLD_START_RULES]
    assert len(ids) == len(set(ids))
    assert all(r.cold_start for r in COLD_START_RULES)
    assert all(not r.cold_start for r in STANDARD_RULES)
    assert all(r.min_weeks >= 1 and r.requires and r.describe for r in STANDARD_RULES + COLD_START_RULES)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_usage_trends.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.usage.trends'`.

- [ ] **Step 3: Write the standard rule engine**

Create `src/ffi/usage/trends.py`:

```python
"""Role-change rules over usage history -> ASCENDING / FALLING / WATCH.

Each rule declares `min_weeks` (how much history it needs) and `requires`
(which metrics must exist). A rule whose window or inputs are unavailable is
DISABLED and named in TrendResult.disabled_rules — never silently skipped
and never evaluated against nulls (R27).

Every signal carries a rule_id and an evidence string. ADR Domain 5: a false
ASCENDING in week 6 must be traceable to the rule and the threshold that
fired it, which is what makes the Plan 2 precision work possible at all.

THRESHOLDS HERE ARE STARTING POINTS, NOT TUNED VALUES. Plan 2 tunes them on
2024 and gates on 2025 (never on the gate season). R10 is the risk being
managed: round numbers tuned on one positive fixture produce 40+ flags a
week against a 5-move budget.
"""
from collections.abc import Sequence

import structlog

from ffi.usage import (
    ASCENDING,
    FALLING,
    Rule,
    TrendResult,
    TrendSignal,
    UsageFrame,
    UsageRow,
)
from ffi.usage.coldstart import COLD_START_MAX_WEEK, COLD_START_RULES, evaluate_cold_start

log = structlog.get_logger()

SNAP_THRESHOLD = 0.55
TARGET_SHARE_STEP = 0.05          # +5pp vs the 3-week baseline
ROUTE_COLLAPSE_RATIO = 0.60       # <= 60% of baseline is a -40% collapse
BASELINE_WEEKS = 3


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _snap_rise_2wk(hist: list[UsageRow]) -> str | None:
    """Snap share above threshold in both of the last two weeks, having been
    at or below it before. The crossing guard is what stops every workhorse
    in the league from re-firing every week."""
    last_two = [r.snap_share for r in hist[-2:]]
    if any(v is None or v <= SNAP_THRESHOLD for v in last_two):
        return None
    if len(hist) >= 3:
        prior = hist[-3].snap_share
        if prior is None or prior > SNAP_THRESHOLD:
            return None
    return (
        f"snap share {last_two[0]:.0%} -> {last_two[1]:.0%} in wk{hist[-2].week}-"
        f"wk{hist[-1].week}, both above {SNAP_THRESHOLD:.0%}"
        + (f" (was {hist[-3].snap_share:.0%} in wk{hist[-3].week})" if len(hist) >= 3 else "")
    )


def _target_share_step(hist: list[UsageRow]) -> str | None:
    baseline_rows = hist[-(BASELINE_WEEKS + 1):-1]
    current = hist[-1].target_share
    values = [r.target_share for r in baseline_rows]
    if current is None or any(v is None for v in values):
        return None
    baseline = _mean(values)
    if current < baseline + TARGET_SHARE_STEP:
        return None
    return (
        f"target share {current:.1%} in wk{hist[-1].week} vs {baseline:.1%} "
        f"{BASELINE_WEEKS}wk baseline (+{(current - baseline) * 100:.1f}pp, "
        f"threshold +{TARGET_SHARE_STEP * 100:.0f}pp)"
    )


def _rz_climb_3wk(hist: list[UsageRow]) -> str | None:
    last_three = [r.rz_touches for r in hist[-3:]]
    if any(v is None for v in last_three):
        return None
    if not (last_three[0] < last_three[1] < last_three[2]):
        return None
    return (
        f"red-zone touches {last_three[0]} -> {last_three[1]} -> {last_three[2]} "
        f"across wk{hist[-3].week}-wk{hist[-1].week} (strictly increasing)"
    )


def _route_collapse(hist: list[UsageRow]) -> str | None:
    baseline_rows = hist[-(BASELINE_WEEKS + 1):-1]
    current = hist[-1].route_share
    values = [r.route_share for r in baseline_rows]
    if current is None or any(v is None for v in values):
        return None
    baseline = _mean(values)
    if baseline <= 0 or current > baseline * ROUTE_COLLAPSE_RATIO:
        return None
    return (
        f"route share {current:.0%} in wk{hist[-1].week} vs {baseline:.0%} "
        f"{BASELINE_WEEKS}wk baseline ({(current / baseline - 1) * 100:.0f}%, "
        f"threshold {(ROUTE_COLLAPSE_RATIO - 1) * 100:.0f}%)"
    )


_STANDARD_FNS = {
    "snap_rise_2wk": _snap_rise_2wk,
    "target_share_step": _target_share_step,
    "rz_climb_3wk": _rz_climb_3wk,
    "route_collapse": _route_collapse,
}

STANDARD_RULES = (
    Rule(
        rule_id="snap_rise_2wk",
        direction=ASCENDING,
        min_weeks=2,
        requires=frozenset({"snap_share"}),
        cold_start=False,
        describe=f"snap share > {SNAP_THRESHOLD:.0%} for 2 consecutive weeks, newly crossed",
    ),
    Rule(
        rule_id="target_share_step",
        direction=ASCENDING,
        min_weeks=BASELINE_WEEKS + 1,
        requires=frozenset({"target_share"}),
        cold_start=False,
        describe=f"target share +{TARGET_SHARE_STEP:.0%} vs its own {BASELINE_WEEKS}wk baseline",
    ),
    Rule(
        rule_id="rz_climb_3wk",
        direction=ASCENDING,
        min_weeks=3,
        requires=frozenset({"rz_touches"}),
        cold_start=False,
        describe="red-zone touches strictly increasing over 3 weeks",
    ),
    Rule(
        rule_id="route_collapse",
        direction=FALLING,
        min_weeks=BASELINE_WEEKS + 1,
        requires=frozenset({"route_share"}),
        cold_start=False,
        describe=f"route share <= {ROUTE_COLLAPSE_RATIO:.0%} of its {BASELINE_WEEKS}wk baseline",
    ),
)


def _validate(frames: Sequence[UsageFrame], week: int) -> None:
    if not frames:
        raise ValueError("classify: frames is empty — nothing to classify")
    weeks = [f.week for f in frames]
    if weeks != sorted(weeks) or len(set(weeks)) != len(weeks):
        raise ValueError(f"classify: frames must be oldest-to-newest and distinct, got {weeks}")
    if frames[-1].week != week:
        raise ValueError(
            f"classify: newest frame is week {frames[-1].week}, requested week {week}"
        )


def _histories(frames: Sequence[UsageFrame]) -> dict[str, list[UsageRow]]:
    """gsis_id -> rows oldest-to-newest, INCOMPLETE TEAM-WEEKS EXCLUDED.

    A row whose team-week failed the completeness floor carries NULL shares
    (R5); including it would make a rule's window silently shorter than its
    declared min_weeks."""
    out: dict[str, list[UsageRow]] = {}
    for frame in frames:
        for row in frame.rows:
            if row.games_complete == 1:
                out.setdefault(row.gsis_id, []).append(row)
    return out


def classify(frames: Sequence[UsageFrame], week: int) -> TrendResult:
    """Rules over `frames` (oldest -> newest, ending at `week`).

    Weeks 1-3 run the cold-start catalogue INSTEAD of the standard one: a
    standard rule needing a 3-week baseline cannot run in week 2, and an
    empty ASC/FALL section in the highest-value waiver weeks of the season
    is not an acceptable output (R7).
    """
    _validate(frames, week)
    available = frozenset.intersection(*[f.available_metrics for f in frames])
    histories = _histories(frames)

    if week <= COLD_START_MAX_WEEK:
        signals, disabled = evaluate_cold_start(histories, available, len(frames))
        return TrendResult(
            season=frames[-1].season, week=week,
            signals=tuple(signals), disabled_rules=tuple(disabled),
        )

    signals: list[TrendSignal] = []
    disabled: list[tuple[str, str]] = []
    for rule in STANDARD_RULES:
        missing = sorted(rule.requires - available)
        if missing:
            disabled.append((rule.rule_id, f"requires unavailable metric(s): {missing}"))
            continue
        if len(frames) < rule.min_weeks:
            disabled.append(
                (rule.rule_id, f"min_weeks={rule.min_weeks} but only {len(frames)} frame(s) supplied")
            )
            continue
        fn = _STANDARD_FNS[rule.rule_id]
        for gsis_id, hist in histories.items():
            if len(hist) < rule.min_weeks or hist[-1].week != week:
                continue
            evidence = fn(hist)
            if evidence is None:
                continue
            signals.append(
                TrendSignal(
                    gsis_id=gsis_id, direction=rule.direction,
                    rule_id=rule.rule_id, evidence=evidence, cold_start=False,
                )
            )
    for rule_id, reason in disabled:
        log.warning("usage.rule_disabled", week=week, rule_id=rule_id, reason=reason)
    for s in signals:
        log.info(
            "usage.signal", week=week, gsis_id=s.gsis_id,
            direction=s.direction, rule_id=s.rule_id, evidence=s.evidence,
        )
    signals.sort(key=lambda s: (s.gsis_id, s.rule_id))
    return TrendResult(
        season=frames[-1].season, week=week,
        signals=tuple(signals), disabled_rules=tuple(sorted(disabled)),
    )
```

- [ ] **Step 4: Write the cold-start variants**

Create `src/ffi/usage/coldstart.py`:

```python
"""Weeks 1-3 rule variants (R7).

Every standard rule needs a 2-4 week window, so in weeks 1-3 the standard
catalogue is structurally silent — during the weeks league-winners are
disproportionately claimed. These variants run 1-2 week windows and every
signal they emit is labelled `cold_start=True` so the report can say so out
loud rather than passing a one-week reading off as a trend.

Imports only ffi.usage (value types); ffi.usage.trends imports THIS module,
never the reverse.
"""
from collections.abc import Mapping

import structlog

from ffi.usage import ASCENDING, FALLING, WATCH, Rule, TrendSignal, UsageRow

log = structlog.get_logger()

COLD_START_MAX_WEEK = 3

CS_SNAP_THRESHOLD = 0.55
CS_TARGET_SHARE_STEP = 0.05
CS_ROUTE_COLLAPSE_RATIO = 0.60


def _snap_rise_1wk(hist: list[UsageRow]) -> str | None:
    """A single week above threshold. WATCH, not ASCENDING: one game is a
    reading, not a trend, and mislabelling it would corrupt the Plan 2
    precision measurement."""
    current = hist[-1].snap_share
    if current is None or current <= CS_SNAP_THRESHOLD:
        return None
    return (
        f"COLD-START: snap share {current:.0%} in wk{hist[-1].week} "
        f"(> {CS_SNAP_THRESHOLD:.0%}), 1-week window"
    )


def _target_share_step_cs(hist: list[UsageRow]) -> str | None:
    prev, current = hist[-2].target_share, hist[-1].target_share
    if prev is None or current is None or current < prev + CS_TARGET_SHARE_STEP:
        return None
    return (
        f"COLD-START: target share {prev:.1%} (wk{hist[-2].week}) -> {current:.1%} "
        f"(wk{hist[-1].week}), +{(current - prev) * 100:.1f}pp on a 2-week window"
    )


def _route_collapse_cs(hist: list[UsageRow]) -> str | None:
    prev, current = hist[-2].route_share, hist[-1].route_share
    if prev is None or current is None or prev <= 0:
        return None
    if current > prev * CS_ROUTE_COLLAPSE_RATIO:
        return None
    return (
        f"COLD-START: route share {prev:.0%} (wk{hist[-2].week}) -> {current:.0%} "
        f"(wk{hist[-1].week}), {(current / prev - 1) * 100:.0f}% on a 2-week window"
    )


_COLD_START_FNS = {
    "snap_rise_1wk_cs": _snap_rise_1wk,
    "target_share_step_cs": _target_share_step_cs,
    "route_collapse_cs": _route_collapse_cs,
}

COLD_START_RULES = (
    Rule(
        rule_id="snap_rise_1wk_cs",
        direction=WATCH,
        min_weeks=1,
        requires=frozenset({"snap_share"}),
        cold_start=True,
        describe=f"snap share > {CS_SNAP_THRESHOLD:.0%} in a single observed week",
    ),
    Rule(
        rule_id="target_share_step_cs",
        direction=ASCENDING,
        min_weeks=2,
        requires=frozenset({"target_share"}),
        cold_start=True,
        describe=f"target share +{CS_TARGET_SHARE_STEP:.0%} week over week",
    ),
    Rule(
        rule_id="route_collapse_cs",
        direction=FALLING,
        min_weeks=2,
        requires=frozenset({"route_share"}),
        cold_start=True,
        describe=f"route share <= {CS_ROUTE_COLLAPSE_RATIO:.0%} of the prior week",
    ),
)


def evaluate_cold_start(
    histories: Mapping[str, list[UsageRow]], available: frozenset[str], n_frames: int
) -> tuple[list[TrendSignal], list[tuple[str, str]]]:
    """(signals, disabled_rules) for the weeks 1-3 catalogue."""
    signals: list[TrendSignal] = []
    disabled: list[tuple[str, str]] = []
    for rule in COLD_START_RULES:
        missing = sorted(rule.requires - available)
        if missing:
            disabled.append((rule.rule_id, f"requires unavailable metric(s): {missing}"))
            continue
        if n_frames < rule.min_weeks:
            disabled.append(
                (rule.rule_id, f"min_weeks={rule.min_weeks} but only {n_frames} frame(s) supplied")
            )
            continue
        fn = _COLD_START_FNS[rule.rule_id]
        for gsis_id, hist in histories.items():
            if len(hist) < rule.min_weeks:
                continue
            evidence = fn(hist)
            if evidence is None:
                continue
            signals.append(
                TrendSignal(
                    gsis_id=gsis_id, direction=rule.direction,
                    rule_id=rule.rule_id, evidence=evidence, cold_start=True,
                )
            )
    for rule_id, reason in disabled:
        log.warning("usage.cold_start_rule_disabled", rule_id=rule_id, reason=reason)
    for s in signals:
        log.info(
            "usage.cold_start_signal", gsis_id=s.gsis_id,
            direction=s.direction, rule_id=s.rule_id, evidence=s.evidence,
        )
    signals.sort(key=lambda s: (s.gsis_id, s.rule_id))
    return signals, sorted(disabled)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_usage_trends.py -q`
Expected: `13 passed`

- [ ] **Step 6: Verify the budgets**

Run: `wc -l src/ffi/usage/trends.py src/ffi/usage/coldstart.py src/ffi/usage/__init__.py`
Expected: `trends.py` ≤ 400 (projected ~250), `coldstart.py` ≤ 200 (projected ~150), `__init__.py` ≤ 120 (projected ~75).

- [ ] **Step 7: Smoke-run the engine on real 2025 weeks and count the flags**

Run:
```bash
uv run python -c "
from ffi.db import connect
from ffi.usage.build import build_usage_weekly
from ffi.usage.trends import classify
conn = connect()
frames = [build_usage_weekly(conn, 2025, w) for w in range(3, 7)]
r = classify(frames, week=6)
print('signals:', len(r.signals))
print('disabled:', r.disabled_rules)
from collections import Counter
print(Counter(s.rule_id for s in r.signals))
for s in r.signals[:5]: print(' ', s.gsis_id, s.direction, s.rule_id, '|', s.evidence)
"
```
Expected: `disabled` lists `rz_climb_3wk` and `route_collapse` with `requires unavailable metric(s)` reasons (no rz/route feed in Plan 1); a signal count in the tens, dominated by `snap_rise_2wk` and `target_share_step`; each printed line carries a readable evidence string.

**Record the signal count in the plan-completion notes.** If it is far above ~8/week, that is the R10 flood and is exactly what Plan 2's precision gate exists to fix — do **not** tune the thresholds here to make the number look better. Tuning outside the 2024-train / 2025-gate protocol invalidates the gate.

- [ ] **Step 8: Amend ARCHITECTURE.md (amendment 6 of 8)**

In ARCHITECTURE.md §4, replace the row

```
| Trend classification | `classify(usage: UsageFrame, week: int) -> TrendResult` (ASCENDING / FALLING / WATCH + evidence lines + rule IDs) | `src/ffi/usage/trends.py` |
```

with

```
| Trend classification | `classify(frames: Sequence[UsageFrame], week: int) -> TrendResult` — frames oldest-to-newest ending at `week` (ASCENDING / FALLING / WATCH + evidence lines + rule IDs; weeks 1-3 run `ffi.usage.coldstart.COLD_START_RULES`) | `src/ffi/usage/trends.py` |
```

Then append to the ARCHITECTURE.md TBDs section:

```
7. §4's `classify(usage: UsageFrame, week)` was provisional. Resolved during
   writing-plans to `classify(frames: Sequence[UsageFrame], week)`: every rule
   in the catalogue has `min_weeks >= 2`, so a single-week frame cannot carry
   the history any rule needs. Semantics unchanged.
```

- [ ] **Step 9: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add src/ffi/usage/trends.py src/ffi/usage/coldstart.py tests/test_usage_trends.py ARCHITECTURE.md
git commit -m "feat(usage): role-change rule engine + cold-start variants

Each rule declares min_weeks and requires; a rule whose window or inputs are
missing is disabled and named, never silently skipped (R27). Cold-start
variants run 1-2 week windows in weeks 1-3 and label their output, because
an empty ASC/FALL section in the highest-value waiver weeks is not an
acceptable answer (R7). Thresholds are starting points: Plan 2 tunes on 2024
and gates on 2025, never on the gate season."
```

---

### Task 10: P4 — the waiver-policy ceiling test (the scope gate on Plans 3–4)

**Files:**
- Create: `scripts/waiver_ceiling_test.py`
- Create: `tests/test_waiver_ceiling.py`

**Interfaces:**
- Consumes (all existing, read before writing): `ffi.sim.backtest.{GATE_SEASONS, REF_STRATEGIES, OUR_FRANCHISE_SLOT, cell_base_seed, load_backtest_pool, load_points_lookup}`, `ffi.sim.priors.build_slot_priors(conn) -> SlotPriors`, `ffi.sim.draft.run_draft(pool, priors, our_pick_fn, seed, our_franchise_slot) -> DraftResult` (`.rosters: dict[int, list[PoolPlayer]]`, `.our_position: int`), `ffi.sim.strategy.make_strategy_fn(StrategyParams) -> PickFn`, `ffi.sim.opponent.STARTERS`, `ffi.sim.season.{FLEX_POS, REG_WEEKS}`, `ffi.sim.pool.PoolPlayer`, `ffi.db.connect()`.
- Produces: `scripts/waiver_ceiling_test.py` module-level functions `lineup_total(roster, week, lookup) -> float`, `all_play_pct(rosters, lookup) -> dict[int, float]`, `perfect_foresight_roster(roster, fa_pool, lookup) -> tuple[list[PoolPlayer], int]`, `run_season(...) -> list[dict]`. Nothing imports this script (ARCHITECTURE §3: nothing imports `scripts/`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_waiver_ceiling.py`:

```python
import pytest

from ffi.sim.pool import PoolPlayer
from ffi.sim.season import evaluate_league

import waiver_ceiling_test as wct  # scripts/ is on sys.path via conftest


def _p(ref, position, points_per_week, gsis=None):
    return PoolPlayer(
        ref=ref, name=ref, position=position, proj_points=points_per_week * 14,
        vorp=0.0, tier=1, adp=None, gsis_id=gsis if gsis is not None else ref,
    )


def _full_roster(prefix, base):
    """2 QB + 2 RB + 3 WR + 1 TE + 1 K + 1 DEF starters, plus 1 flex bench."""
    spec = [("QB", 2), ("RB", 3), ("WR", 3), ("TE", 1), ("K", 1), ("DEF", 1)]
    roster, i = [], 0
    for pos, n in spec:
        for j in range(n):
            roster.append(_p(f"{prefix}-{pos}{j}", pos, base + i))
            i += 1
    return roster


def _lookup(rosters, weeks=14):
    out = {}
    for roster in rosters.values():
        for p in roster:
            for w in range(1, weeks + 1):
                out[(p.gsis_id, w)] = p.proj_points / 14
    return out


def test_lineup_total_matches_the_library_evaluator():
    """The harness reimplements the lineup rule scalar-side for speed. If it
    ever drifts from ffi.sim.season, every number this script reports is
    wrong in a way nobody would notice."""
    rosters = {1: _full_roster("A", 10.0), 2: _full_roster("B", 5.0)}
    lookup = _lookup(rosters)
    library = evaluate_league(rosters, cv_by_pos={}, seed=1, points_lookup=lookup)
    harness = wct.all_play_pct(rosters, lookup)
    assert harness == pytest.approx(library, abs=1e-9)


def test_perfect_foresight_never_lowers_the_roster():
    roster = _full_roster("A", 5.0)
    fa = [_p("FA-RB", "RB", 99.0), _p("FA-WR", "WR", 98.0)]
    lookup = _lookup({1: roster + fa})
    before = sum(wct.lineup_total(roster, w, lookup) for w in range(1, 15))
    improved, adds = wct.perfect_foresight_roster(roster, fa, lookup)
    after = sum(wct.lineup_total(improved, w, lookup) for w in range(1, 15))
    assert after >= before
    assert adds > 0


def test_perfect_foresight_respects_the_weekly_add_limit():
    roster = _full_roster("A", 1.0)
    fa = [_p(f"FA{i}", "WR", 90.0 + i) for i in range(30)]
    lookup = _lookup({1: roster + fa})
    _, adds = wct.perfect_foresight_roster(roster, fa, lookup, weekly_limit=2, season_limit=99)
    assert adds <= 2 * 14


def test_perfect_foresight_respects_the_season_add_limit():
    roster = _full_roster("A", 1.0)
    fa = [_p(f"FA{i}", "WR", 90.0 + i) for i in range(30)]
    lookup = _lookup({1: roster + fa})
    _, adds = wct.perfect_foresight_roster(roster, fa, lookup, weekly_limit=5, season_limit=3)
    assert adds == 3


def test_a_position_is_never_dropped_below_its_starter_requirement():
    roster = _full_roster("A", 1.0)
    fa = [_p(f"FA{i}", "WR", 90.0 + i) for i in range(30)]
    lookup = _lookup({1: roster + fa})
    improved, _ = wct.perfect_foresight_roster(roster, fa, lookup)
    counts = {}
    for p in improved:
        counts[p.position] = counts.get(p.position, 0) + 1
    from ffi.sim.opponent import STARTERS

    for pos, need in STARTERS.items():
        assert counts.get(pos, 0) >= need, f"{pos} dropped below {need}"


def test_no_free_agents_means_no_adds():
    roster = _full_roster("A", 5.0)
    lookup = _lookup({1: roster})
    improved, adds = wct.perfect_foresight_roster(roster, [], lookup)
    assert adds == 0
    assert improved == roster
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_waiver_ceiling.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'waiver_ceiling_test'`.

- [ ] **Step 3: Write the harness**

Create `scripts/waiver_ceiling_test.py`:

```python
#!/usr/bin/env python3
"""ADR Precondition P4: the waiver-policy ceiling test (R24).

Question: how much playoff probability is reachable from in-season waiver
moves AT ALL? The design doc's "draft 40% / in-season 60%" split is
asserted, never measured. If PERFECT FORESIGHT — knowing every player's
exact weekly score in advance — is worth under 8 playoff-probability points,
then a realistic advisor is worth a fraction of that, and Plans 3-4 (waiver
advisor, trade angles) are not worth 60-80 hours of a 14-week season.

Deliberately a THIN harness: drafts, pools, weekly points, opponent models
and the lineup slot config all come from ffi.sim. The only things
implemented here are the waiver policy itself and the top-6 ranking.

Method, per (season, seed):
  1. run_draft() -> 12 rosters, ours at OUR_FRANCHISE_SLOT.
  2. Baseline: all 14 weeks with the drafted rosters, rank by all-play win
     pct; playoff = rank <= PLAYOFF_TEAMS.
  3. Foresight: same, except OUR roster may add up to WEEKLY_ADD_LIMIT
     players per week (SEASON_ADD_LIMIT for the season) with perfect
     knowledge of that week's points.
  4. delta_pp = 100 * (P(playoff | foresight) - P(playoff | baseline)).

The ceiling is deliberately GENEROUS: opponents never transact, waiver
priority does not exist, and every claim succeeds. A generous ceiling is the
right shape for a scope gate — if even this is small, the realistic number
is smaller.
"""
import argparse
import json
import statistics

from ffi.db import connect
from ffi.sim.backtest import (
    GATE_SEASONS,
    OUR_FRANCHISE_SLOT,
    REF_STRATEGIES,
    cell_base_seed,
    load_backtest_pool,
    load_points_lookup,
)
from ffi.sim.draft import run_draft
from ffi.sim.opponent import STARTERS
from ffi.sim.priors import build_slot_priors
from ffi.sim.season import FLEX_POS, REG_WEEKS
from ffi.sim.strategy import make_strategy_fn

# league_rules.md: "Playoff Teams: 6" of 12.
PLAYOFF_TEAMS = 6
# league_rules.md: "Weekly Acquisitions: Maximum 5", "Season Acquisitions: Maximum 65".
WEEKLY_ADD_LIMIT = 5
SEASON_ADD_LIMIT = 65
# Only the top free agents by that week's actual points can possibly help.
FA_CANDIDATES_PER_WEEK = 25
# The ceiling is measured against the strategy the D7 reference gate uses.
CEILING_STRATEGY_IDX = 0
CUT_THRESHOLD_PP = 8.0


def _points(player, week: int, lookup: dict) -> float:
    """Weekly points. gsis_id=None (DEF in the backtest representation)
    ALWAYS scores 0.0 — the same hardcoded contract as ffi.sim.season."""
    if player.gsis_id is None:
        return 0.0
    return lookup.get((player.gsis_id, week), 0.0)


def lineup_total(roster, week: int, lookup: dict) -> float:
    """Optimal lineup total for one team-week: STARTERS by position plus the
    best leftover RB/WR/TE as FLEX. Scalar mirror of
    ffi.sim.season._lineup_total; tests/test_waiver_ceiling.py asserts the
    two agree exactly."""
    by_pos: dict[str, list[float]] = {}
    for p in roster:
        by_pos.setdefault(p.position, []).append(_points(p, week, lookup))
    total = 0.0
    leftovers: list[float] = []
    for pos, need in STARTERS.items():
        values = sorted(by_pos.get(pos, []), reverse=True)
        total += sum(values[:need])
        if pos in FLEX_POS:
            leftovers.extend(values[need:])
    if leftovers:
        total += max(leftovers)
    return total


def all_play_pct(rosters: dict, lookup: dict) -> dict:
    """team -> mean all-play win pct over REG_WEEKS. A team beats every other
    team with a STRICTLY lower total that week (ties count for neither
    side), matching ffi.sim.season's convention."""
    teams = sorted(rosters)
    totals = {t: [lineup_total(rosters[t], w, lookup) for w in range(1, REG_WEEKS + 1)] for t in teams}
    denom = (len(teams) - 1) * REG_WEEKS
    out = {}
    for t in teams:
        wins = sum(
            1
            for w in range(REG_WEEKS)
            for other in teams
            if other != t and totals[t][w] > totals[other][w]
        )
        out[t] = wins / denom
    return out


def _droppable(roster, counts: dict) -> list:
    """Players whose removal keeps every position at its starter minimum."""
    return [p for p in roster if counts[p.position] - 1 >= STARTERS.get(p.position, 0)]


def perfect_foresight_roster(
    roster,
    fa_pool,
    lookup: dict,
    weekly_limit: int = WEEKLY_ADD_LIMIT,
    season_limit: int = SEASON_ADD_LIMIT,
):
    """(roster_after_the_season, total_adds) under perfect weekly foresight.

    Greedy per week: drop the player with the lowest REMAINING-season points
    (weeks w..14) among those droppable without breaching a starter minimum,
    add the free agent that maximizes THIS week's lineup total. Stop when no
    swap helps. Adds are permanent, as in a real league.
    """
    current = list(roster)
    available = list(fa_pool)
    adds = 0
    for week in range(1, REG_WEEKS + 1):
        for _ in range(weekly_limit):
            if adds >= season_limit or not available:
                break
            counts: dict[str, int] = {}
            for p in current:
                counts[p.position] = counts.get(p.position, 0) + 1
            candidates_out = _droppable(current, counts)
            if not candidates_out:
                break
            remaining = {
                id(p): sum(_points(p, w, lookup) for w in range(week, REG_WEEKS + 1))
                for p in candidates_out
            }
            drop = min(candidates_out, key=lambda p: remaining[id(p)])
            base = lineup_total(current, week, lookup)
            without = [p for p in current if p is not drop]
            candidates_in = sorted(
                available, key=lambda p: _points(p, week, lookup), reverse=True
            )[:FA_CANDIDATES_PER_WEEK]
            best_gain, best_add = 0.0, None
            for candidate in candidates_in:
                gain = lineup_total(without + [candidate], week, lookup) - base
                if gain > best_gain:
                    best_gain, best_add = gain, candidate
            if best_add is None:
                break
            current = without + [best_add]
            available = [p for p in available if p is not best_add]
            adds += 1
    return current, adds


def run_season(conn, priors, season: int, n_drafts: int) -> list:
    pool = load_backtest_pool(conn, season)
    lookup = load_points_lookup(conn, season)
    pick_fn = make_strategy_fn(REF_STRATEGIES[CEILING_STRATEGY_IDX])
    base_seed = cell_base_seed(CEILING_STRATEGY_IDX, season)
    cells = []
    for i in range(n_drafts):
        seed = base_seed + i
        result = run_draft(
            pool, priors, pick_fn, seed=seed, our_franchise_slot=OUR_FRANCHISE_SLOT
        )
        ours = result.our_position
        rostered = {p.ref for roster in result.rosters.values() for p in roster}
        fa_pool = [p for p in pool if p.ref not in rostered and p.gsis_id is not None]

        base_pct = all_play_pct(result.rosters, lookup)
        base_rank = sorted(base_pct, key=lambda t: -base_pct[t]).index(ours) + 1

        improved, adds = perfect_foresight_roster(result.rosters[ours], fa_pool, lookup)
        fs_rosters = dict(result.rosters)
        fs_rosters[ours] = improved
        fs_pct = all_play_pct(fs_rosters, lookup)
        fs_rank = sorted(fs_pct, key=lambda t: -fs_pct[t]).index(ours) + 1

        cells.append(
            {
                "season": season,
                "seed": seed,
                "adds": adds,
                "base_pct": base_pct[ours],
                "foresight_pct": fs_pct[ours],
                "base_playoff": base_rank <= PLAYOFF_TEAMS,
                "foresight_playoff": fs_rank <= PLAYOFF_TEAMS,
            }
        )
    return cells


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seasons",
        default=",".join(str(s) for s in GATE_SEASONS),
        help="comma-separated; pools must exist in sim.backtest_pool",
    )
    parser.add_argument("--n-drafts", type=int, default=40)
    args = parser.parse_args()

    conn = connect()
    priors = build_slot_priors(conn)
    cells = []
    for season in [int(s) for s in args.seasons.split(",")]:
        cells += run_season(conn, priors, season, args.n_drafts)

    n = len(cells)
    base_p = sum(c["base_playoff"] for c in cells) / n
    fs_p = sum(c["foresight_playoff"] for c in cells) / n
    delta_pp = 100 * (fs_p - base_p)
    # McNemar: only discordant pairs carry information about a paired
    # difference of proportions.
    b = sum(1 for c in cells if c["foresight_playoff"] and not c["base_playoff"])
    c_ = sum(1 for c in cells if c["base_playoff"] and not c["foresight_playoff"])
    se_pp = 100 * ((b + c_) ** 0.5) / n if n else 0.0

    summary = {
        "n_cells": n,
        "seasons": sorted({c["season"] for c in cells}),
        "n_drafts_per_season": args.n_drafts,
        "mean_adds": statistics.mean(c["adds"] for c in cells),
        "baseline_playoff_prob": round(base_p, 4),
        "foresight_playoff_prob": round(fs_p, 4),
        "delta_pp": round(delta_pp, 2),
        "se_pp": round(se_pp, 2),
        "ci95_pp": [round(delta_pp - 2 * se_pp, 2), round(delta_pp + 2 * se_pp, 2)],
        "cut_threshold_pp": CUT_THRESHOLD_PP,
        "decision": (
            "BUILD Plans 3-4" if delta_pp >= CUT_THRESHOLD_PP else "CUT Plans 3-4"
        ),
    }
    print(json.dumps(summary, indent=2))
    print(
        f"\nCEILING: perfect-foresight waivers are worth {delta_pp:+.2f}pp of playoff "
        f"probability (95% CI {summary['ci95_pp'][0]:+.2f} to {summary['ci95_pp'][1]:+.2f}, "
        f"n={n}). Threshold {CUT_THRESHOLD_PP}pp -> {summary['decision']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_waiver_ceiling.py -q`
Expected: `6 passed`. The first test is the important one: it proves the harness's scalar lineup rule agrees exactly with `ffi.sim.season.evaluate_league`.

- [ ] **Step 5: Confirm the backtest pools exist before the long run**

Run:
```bash
psql -d fantasy_football -c "SELECT season, count(*) FROM sim.backtest_pool GROUP BY 1 ORDER BY 1"
```
Expected: rows for at least 2023, 2024 and 2025 with several hundred players each. If any is missing, run `uv run python scripts/build_backtest_pools.py` first — `load_backtest_pool` raises `ValueError: sim.backtest_pool has no rows for season <n>` otherwise.

- [ ] **Step 6: Do a 2-draft smoke run before committing to the long one**

Run: `uv run python scripts/waiver_ceiling_test.py --seasons 2025 --n-drafts 2`
Expected: a JSON summary in under ~2 minutes with `n_cells: 2`, a `mean_adds` between 20 and 70, and both probabilities in `[0.0, 1.0]`. If `mean_adds` is 0, the free-agent pool is empty — check that `load_backtest_pool` returns more players than 12 × 19 = 228.

- [ ] **Step 7: Launch the full run in the background**

Run:
```bash
mkdir -p logs
nohup uv run python scripts/waiver_ceiling_test.py --n-drafts 40 \
  > logs/waiver-ceiling-$(date +%F).log 2>&1 &
echo "started pid $!"
```
Expected: a pid. Poll with `tail -5 logs/waiver-ceiling-$(date +%F).log`. Expected wall time: 20–60 minutes for 120 drafts.

Do **not** block the remaining tasks on this run — ADR P4 explicitly parallelizes the ceiling test rather than making it a blocker, because Plan 1's value does not depend on its outcome.

- [ ] **Step 8: Record the number and apply the decision rule**

When the run finishes, copy the `delta_pp`, `ci95_pp` and `n_cells` values into the **Plan-completion notes** section at the bottom of this document, and apply the rule:

- **`delta_pp >= 8.0`** → Plans 3 and 4 are justified. Proceed as planned.
- **`delta_pp < 8.0`** → **CUT Plans 3 and 4.** The waiver advisor and trade angles do not get built. Plan 2 (reports, league-state adapter, precision gate, push) still ships, because the trend engine's value is standalone and does not depend on the waiver ceiling. Record the cut decision in the notes and tell the operator explicitly — this is the whole point of running the test before the 60–80 hours, not after.
- If `ci95_pp` straddles 8.0, re-run with `--n-drafts 100` before deciding.

- [ ] **Step 9: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add scripts/waiver_ceiling_test.py tests/test_waiver_ceiling.py
git commit -m "feat(sim): P4 waiver-policy ceiling test — perfect foresight vs do-nothing

R24: 'draft 40% / in-season 60%' is asserted, never measured. This runs a
deliberately generous ceiling (opponents never transact, every claim
succeeds) over the D7 gate seasons and reports the playoff-probability
delta with a McNemar interval. Under 8pp cuts Plans 3-4. Thin harness: the
only new logic is the waiver policy and the top-6 ranking; a test asserts
the scalar lineup rule agrees exactly with ffi.sim.season."
```

---

### Task 11: Ops hardening — wake scheduling, advisory lock, offsite backup

**Files:**
- Create: `src/ffi/joblock.py`
- Create: `tests/test_joblock.py`
- Modify: `scripts/morning_briefing.py`
- Modify: `scripts/build_valuation.py`
- Modify: `scripts/backup_db.sh`
- Modify: `ARCHITECTURE.md` (§1b new row, §2 Layer 0 + Layer 5, §4 new row)

**Interfaces:**
- Consumes: `ffi.db.connect()`.
- Produces: `ffi.joblock.JobLockTimeout(Exception)`, `ffi.joblock.lock_key(name: str) -> int`, `ffi.joblock.acquire_or_wait(conn, name: str, wait_s: float = 900, poll_s: float = 5) -> None`, `ffi.joblock.advisory_lock(conn, name, wait_s=900, poll_s=5)` (contextmanager). Plan 2's Tuesday trends job takes the same `"ffi.morning_chain"` lock.

- [ ] **Step 1: Configure wake-on-schedule (R13)**

The three scheduled trigger times are 02:30 (`com.ffi.simfarm`), 06:30 (`com.ffi.trending`) and 07:00 (`com.ffi.morning`). `pmset repeat` supports one repeating wake, so schedule the earliest and let the machine stay awake through the rest.

Run:
```bash
sudo pmset repeat wakeorpoweron MTWRFSU 02:25:00
pmset -g sched
```
Expected: a `Repeating power events:` block showing `wakepoweron at 2:25AM every day`. This requires the operator's `sudo` password; if the agent cannot supply it, hand the command to the operator and do not proceed to Step 2 until `pmset -g sched` shows the entry.

Note the belt-and-braces design: `pmset` is the prevention, and Task 4's artifact-freshness assertion is the **detector** for when it fails anyway. Neither replaces the other.

- [ ] **Step 2: Write the failing lock tests**

Create `tests/test_joblock.py`:

```python
import psycopg2
import pytest

from ffi.joblock import JobLockTimeout, acquire_or_wait, advisory_lock, lock_key


def _second_conn():
    return psycopg2.connect(dbname="fantasy_football_test", host="localhost")


def test_lock_key_is_stable_and_in_range():
    a = lock_key("ffi.morning_chain")
    assert a == lock_key("ffi.morning_chain")
    assert a != lock_key("ffi.trends")
    assert -(2**63) <= a < 2**63


def test_acquire_succeeds_when_uncontended(db):
    acquire_or_wait(db, "test.uncontended", wait_s=1)


def test_second_holder_times_out_loudly(db):
    acquire_or_wait(db, "test.contended", wait_s=1)
    other = _second_conn()
    try:
        with pytest.raises(JobLockTimeout, match="test.contended"):
            acquire_or_wait(other, "test.contended", wait_s=1, poll_s=0.1)
    finally:
        other.close()


def test_context_manager_releases_on_exit(db):
    with advisory_lock(db, "test.ctx", wait_s=1):
        pass
    other = _second_conn()
    try:
        acquire_or_wait(other, "test.ctx", wait_s=1, poll_s=0.1)
    finally:
        other.close()


def test_context_manager_releases_on_exception(db):
    with pytest.raises(RuntimeError, match="boom"):
        with advisory_lock(db, "test.ctx_exc", wait_s=1):
            raise RuntimeError("boom")
    other = _second_conn()
    try:
        acquire_or_wait(other, "test.ctx_exc", wait_s=1, poll_s=0.1)
    finally:
        other.close()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_joblock.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.joblock'`.

- [ ] **Step 4: Write the lock helper**

Create `src/ffi/joblock.py`:

```python
"""Postgres advisory locks for job serialization (R22, ADR Domain 8).

The Tuesday trends job (12-45 min) overlaps com.ffi.morning at 07:00, which
runs build_valuation.py's DELETE+INSERT inside a single commit. A report
reading across that boundary is half-old and half-new with no error raised —
the "degraded-quality presence" class this whole ADR is organized around.

Advisory locks rather than a lockfile because they die with the connection:
a killed job cannot leave a stale lock that blocks every subsequent run, and
there is no cleanup path to forget.

`pg_try_advisory_lock` in a poll loop, never `pg_advisory_lock`: the
blocking form waits forever, which converts a deadlock into a silently
missed deadline. Timing out loudly is the whole point.

Layer 0 leaf: imports nothing internal.
"""
import contextlib
import hashlib
import time

DEFAULT_WAIT_S = 900.0
DEFAULT_POLL_S = 5.0


class JobLockTimeout(Exception):
    """Another process still holds the lock after `wait_s`."""


def lock_key(name: str) -> int:
    """Stable signed 64-bit key from a lock name.

    Postgres advisory locks take a bigint, and the key space is GLOBAL to the
    database — deriving it from a name means two jobs collide only if they
    were meant to.
    """
    digest = hashlib.sha256(name.encode()).digest()[:8]
    return int.from_bytes(digest, "big", signed=True)


def acquire_or_wait(
    conn,
    name: str,
    wait_s: float = DEFAULT_WAIT_S,
    poll_s: float = DEFAULT_POLL_S,
) -> None:
    """Take the session-level advisory lock `name`, polling until `wait_s`.

    Session-level (not transaction-level): the lock outlives commits, so a
    job that commits mid-run keeps its serialization. It is released by
    `release()` or by the connection closing — which for a script means
    process exit.
    """
    key = lock_key(name)
    deadline = time.monotonic() + wait_s
    attempts = 0
    while True:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
            if cur.fetchone()[0]:
                return
        attempts += 1
        if time.monotonic() >= deadline:
            raise JobLockTimeout(
                f"could not acquire advisory lock {name!r} (key {key}) after "
                f"{wait_s:.0f}s and {attempts} attempts — another job still holds it. "
                f"Check `SELECT * FROM pg_locks WHERE locktype='advisory'`."
            )
        time.sleep(poll_s)


def release(conn, name: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key(name),))


@contextlib.contextmanager
def advisory_lock(
    conn,
    name: str,
    wait_s: float = DEFAULT_WAIT_S,
    poll_s: float = DEFAULT_POLL_S,
):
    """Scoped form. Released on the way out, including on exception."""
    acquire_or_wait(conn, name, wait_s=wait_s, poll_s=poll_s)
    try:
        yield
    finally:
        release(conn, name)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_joblock.py -q`
Expected: `5 passed`

- [ ] **Step 6: Wire the lock into the briefing (reader side)**

In `scripts/morning_briefing.py`, add to the imports:

```python
from ffi.joblock import acquire_or_wait
```

and immediately after `conn = connect()` inside `main()`, add:

```python
    # R22: serialize against build_valuation.py's DELETE+INSERT so the
    # briefing cannot read half-old, half-new valuation rows. Plan 2's
    # Tuesday trends job takes the same lock.
    acquire_or_wait(conn, "ffi.morning_chain", wait_s=900)
```

- [ ] **Step 7: Wire the lock into the valuation rebuild (writer side)**

In `scripts/build_valuation.py`, add to the imports:

```python
from ffi.joblock import acquire_or_wait
```

and immediately after the module-level `conn = connect()` line, add:

```python
# R22: this script's DELETE+INSERT of valuation.player_value is the read-skew
# hazard. Holding the lock for the whole process (released when the
# connection closes at exit) means no reader can straddle the rebuild.
acquire_or_wait(conn, "ffi.morning_chain", wait_s=900)
```

- [ ] **Step 8: Verify the two jobs actually serialize**

Run:
```bash
uv run python -c "
from ffi.db import connect
from ffi.joblock import acquire_or_wait, JobLockTimeout
a = connect(); acquire_or_wait(a, 'ffi.morning_chain', wait_s=1)
b = connect()
try:
    acquire_or_wait(b, 'ffi.morning_chain', wait_s=2, poll_s=0.5)
    print('BUG: second holder acquired the lock')
except JobLockTimeout as e:
    print('OK serialized:', str(e)[:70])
"
```
Expected: `OK serialized: could not acquire advisory lock 'ffi.morning_chain' (key ...`

Then confirm the real chain still runs end to end:
Run: `uv run python scripts/build_valuation.py > /dev/null && uv run python scripts/morning_briefing.py; echo "rc=$?"`
Expected: `-> reports/briefing-<date>.md` and a `rc=0` or `rc=1` matching the briefing's red-flag state. Neither should hang: the two run sequentially, so the lock is uncontended each time.

- [ ] **Step 9: Add the offsite backup line (R28)**

Append to `scripts/backup_db.sh`, after the existing `echo "Backup complete: ..."` line:

```bash

# --- Offsite copy (R28, L2xI9=18 — the cheapest risk retirement in the register) ---
# Backups currently live on the same laptop as the database: laptop loss
# takes both, RPO 24h. One rsync line retires it.
#
# 🛑 USER INPUT REQUIRED: set FFI_BACKUP_REMOTE in .env to a real destination
# before this does anything. Examples:
#     FFI_BACKUP_REMOTE="brent@nas.local:/volume1/backups/fantasy_football/"
#     FFI_BACKUP_REMOTE="/Volumes/Backup/fantasy_football/"        # external disk
#   For a cloud target, swap `rsync` for `rclone copy` and configure the
#   remote with `rclone config` first.
#
# Until it is set this block prints a loud reminder and exits 0 — it must
# never fail the morning chain, because a missing offsite target is not a
# reason to lose the local backup too.
if [ -z "${FFI_BACKUP_REMOTE:-}" ]; then
  echo "WARN: FFI_BACKUP_REMOTE unset — backups are LAPTOP-ONLY (R28, RPO 24h)."
  echo "      Set it in .env to enable the offsite copy."
else
  rsync -a --delete-after \
    --include='fantasy_football_*.sql.gz' --exclude='*' \
    backups/ "$FFI_BACKUP_REMOTE"
  echo "Offsite sync complete: $FFI_BACKUP_REMOTE"
fi
```

`scripts/backup_db.sh` runs `set -euo pipefail`, so the `${FFI_BACKUP_REMOTE:-}` default form is required — a bare `$FFI_BACKUP_REMOTE` would abort the script under `set -u`.

- [ ] **Step 10: Load the env var and verify the backup script**

`scripts/backup_db.sh` is a shell script and does not read `.env` through `python-dotenv`. Add the source line at the top, immediately after `cd "$(dirname "$0")/.."`:

```bash
# .env carries FFI_BACKUP_REMOTE (see the offsite block below). `set -a`
# exports every assignment; the subshell keeps the sourcing side-effect-free
# if .env is absent.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
```

Run: `bash scripts/backup_db.sh`
Expected:
```
Backup complete: backups/fantasy_football_<timestamp>.sql.gz
WARN: FFI_BACKUP_REMOTE unset — backups are LAPTOP-ONLY (R28, RPO 24h).
      Set it in .env to enable the offsite copy.
```

Then add the field-name-only placeholder. Append to `.env.example` (create it if absent):

```
FFI_BACKUP_REMOTE=
```

Run: `bash scripts/check_no_secrets.sh`
Expected: `OK: .env.example has field names only; no ntfy topic committed; .gitignore covers secrets.`

- [ ] **Step 11: Amend ARCHITECTURE.md (amendments 7 and 8 of 8)**

In ARCHITECTURE.md §1b, insert after the `src/ffi/flags.py` row:

```
| `src/ffi/joblock.py` | Postgres advisory-lock helper (`pg_try_advisory_lock` poll loop) serializing the morning chain against the Tuesday jobs (R22). Leaf: imports nothing internal | 100 |
```

In ARCHITECTURE.md §2, Layer 0, add:

```
- `src/ffi/joblock.py` → (nothing internal; `hashlib` + `contextlib`)
```

In ARCHITECTURE.md §2, Layer 5, replace the `scripts/morning_briefing.py` line with:

```
- `scripts/morning_briefing.py` → `health`, `flags`, `joblock`, `db`, `ids`, `league_state`
```

In ARCHITECTURE.md §2, update the topological order line to insert `joblock` after `flags`:

```
`db` → `ids` → `yahoo_client` → `health` → `flags` → `joblock` → `ingest/gates` → `ingest` → `scoring` →
```

In ARCHITECTURE.md §4, insert a row after the `Module enable flags` row:

```
| Job serialization | `acquire_or_wait(conn, name: str, wait_s: float = 900, poll_s: float = 5) -> None`, `advisory_lock(conn, name, ...)` (contextmanager), `release(conn, name)`; raises `JobLockTimeout` | `src/ffi/joblock.py` |
```

- [ ] **Step 12: Guards + suite, then commit**

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
uv run pytest -q
git add src/ffi/joblock.py tests/test_joblock.py scripts/morning_briefing.py \
        scripts/build_valuation.py scripts/backup_db.sh .env.example ARCHITECTURE.md
git commit -m "feat(ops): advisory job lock, wake scheduling, offsite backup line

R22: build_valuation.py's DELETE+INSERT and every reader now take the same
'ffi.morning_chain' advisory lock, so no report can straddle a rebuild.
Advisory rather than a lockfile because it dies with the connection;
try-lock in a poll loop rather than pg_advisory_lock because blocking
forever turns a deadlock into a silently missed deadline. R13: pmset repeat
wakeorpoweron at 02:25 daily. R28: one rsync line retires a 24h RPO on the
entire season's data plus every backup of it."
```

---

### Task 12: CI wiring — the three guards on every commit, and the plan's exit gate

The repo has no Makefile and already uses an unversioned `.git/hooks/pre-commit` for its secret denylist. Keep that seam, but move the logic into a versioned script so the checks are reviewable and cannot silently diverge per-clone.

**Files:**
- Create: `scripts/pre-commit.sh`
- Modify: `.git/hooks/pre-commit`

**Interfaces:**
- Consumes: `scripts/check_file_size.sh`, `scripts/check_import_boundaries.py`, `scripts/check_no_secrets.sh` (all existing), `scripts/validate_league_clock.py` (Task 5).
- Produces: `scripts/pre-commit.sh` — the versioned hook body. `.git/hooks/pre-commit` becomes a two-line shim.

- [ ] **Step 1: Write the versioned hook body**

Create `scripts/pre-commit.sh`:

```bash
#!/usr/bin/env bash
# scripts/pre-commit.sh — the versioned pre-commit body.
#
# .git/hooks/ is not tracked by git, so hook logic living there cannot be
# reviewed, cannot be diffed, and silently differs between clones. The hook
# is a two-line shim that sources this file. Contract: ARCHITECTURE.md §8.
#
# Four checks, in ascending cost order:
#   1. secret-path denylist   (staged paths; catches `git add -f` of a token)
#   2. file-size budgets      (ARCHITECTURE.md §1 column 3)
#   3. import boundaries      (ARCHITECTURE.md §3 + 4 hard-coded rules)
#   4. secret hygiene         (.env.example field names, no ntfy topic)
#   5. league-clock safety    (no UNSET/UNVERIFIED field is consumed)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- 1. secret-path denylist -------------------------------------------------
DENYLIST=(
  'config/yahoo_(oauth|token)\.json'
  'config/.*(oauth|token).*'
  'config/.*\.(key|crt|pem)$'
  '\.env(\.|$)'
  '\.(key|crt|pem)$'
  'browser-profiles/'
  '\.playwright/'
  'playwright/\.auth/'
  'data/draft-logs/'
  'data/captures/'
  'backups/'
)
staged="$(git diff --cached --name-only --diff-filter=ACM)"
if [ -n "$staged" ]; then
  bad=""
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    for re in "${DENYLIST[@]}"; do
      if printf '%s\n' "$f" | grep -Eq "$re"; then
        # .env.example is field-names-only and IS tracked; check_no_secrets.sh
        # enforces that. Exempt it from the '\.env(\.|$)' pattern.
        [ "$f" = ".env.example" ] && break
        bad="${bad}${f}\n"
        break
      fi
    done
  done <<< "$staged"
  if [ -n "$bad" ]; then
    printf 'BLOCKED COMMIT: staged files match the secret denylist:\n'
    printf '%b' "$bad"
    printf '\nUnstage them (git reset HEAD -- <file>) and delete/rotate if already pushed.\n'
    exit 1
  fi
fi

# --- 2-5. the architecture and config guards --------------------------------
bash scripts/check_file_size.sh
python3 scripts/check_import_boundaries.py
bash scripts/check_no_secrets.sh
python3 scripts/validate_league_clock.py --quiet

echo "pre-commit: all guards passed."
exit 0
```

- [ ] **Step 2: Replace the hook with a shim**

Replace the entire contents of `.git/hooks/pre-commit` with:

```bash
#!/usr/bin/env bash
# Shim: the real logic is versioned at scripts/pre-commit.sh so it can be
# reviewed and diffed. Do not add checks here — add them there.
exec bash "$(git rev-parse --show-toplevel)/scripts/pre-commit.sh"
```

Then run: `chmod +x .git/hooks/pre-commit scripts/pre-commit.sh`

- [ ] **Step 3: Prove the hook blocks a secret and passes a clean tree**

Run:
```bash
printf 'FAKE=1\n' > /tmp/ffi-fake.env && cp /tmp/ffi-fake.env .env.local.test
git add -f .env.local.test 2>/dev/null || true
git commit -m "should be blocked" ; echo "rc=$?"
git reset HEAD -- .env.local.test && rm -f .env.local.test /tmp/ffi-fake.env
```
Expected: the commit fails with `BLOCKED COMMIT: staged files match the secret denylist:` listing `.env.local.test`, and `rc=1`.

- [ ] **Step 4: Run the plan's exit gate**

This is the gate for the entire plan, not just this task.

Run:
```bash
bash scripts/check_file_size.sh
python3 scripts/check_import_boundaries.py
bash scripts/check_no_secrets.sh
python3 scripts/validate_league_clock.py
uv run pytest -q
```
Expected:
```
OK: all 133 Python files within their ARCHITECTURE.md size budget (default 600).
OK: 133 files clean against 21 parsed forbidden-import rules + 4 hard-coded rules (20 grandfathered paths allowlisted).
OK: .env.example has field names only; no ntfy topic committed; .gitignore covers secrets.
OK: config/league_clock.yaml as_of 2026-08-31; 7 field(s) still UNSET/UNVERIFIED and none is consumed: ...
<N> passed
```
The file count rises from 123 (2026-08-31 baseline) by the ten new `.py` files under `src/` and `scripts/`: `health.py`, `joblock.py`, `ingest/gates.py`, `ingest/sleeper_trending.py`, `ingest/nflverse_snaps.py`, `usage/__init__.py`, `usage/build.py`, `usage/trends.py`, `usage/coldstart.py`, `scripts/ingest_sleeper_trending.py`, `scripts/ingest_nflverse_snaps.py`, `scripts/validate_league_clock.py`, `scripts/waiver_ceiling_test.py` — 136 total. Accept whatever count the guard reports as long as it exits 0; the number above is an estimate, the exit code is the gate.

- [ ] **Step 5: Verify the full daily chain end to end**

Run: `bash scripts/morning_chain.sh; echo "chain rc=$?"`
Expected: each step logged with its own `=== rc=<n>` line, the briefing rendering last regardless of any step's outcome, and a final `-> reports/briefing-<date>.md`. A nonzero `chain rc` is acceptable and correct if the briefing found a red flag — check `reports/briefing-<date>.md` to confirm the red flag is real rather than a bug.

- [ ] **Step 6: Commit**

```bash
git add scripts/pre-commit.sh
git commit -m "chore(ci): move the pre-commit body into a versioned script and add the four guards

.git/hooks/ is untracked, so hook logic living there cannot be reviewed and
silently differs per clone. The hook becomes a two-line shim; the real body
runs the secret denylist plus the three ARCHITECTURE.md guards plus the
league-clock consumption check (ARCHITECTURE.md §8)."
```

---

## Plan-completion notes

Fill these in as the plan executes. They are the plan's output, not decoration — Plan 2's scope depends on them.

| Item | Recorded by | Value |
|---|---|---|
| P2 first archive date | Task 1 Step 8 | _(YYYY-MM-DD of the first `raw.sleeper_trending` row)_ |
| nflverse backfill row count | Task 2 Step 10 | _(row_count from the ingest_runs row)_ |
| P3 observed mechanics | Task 5 Step 7 | _(recorded / deferred to Plan 2 — and the operator's exact choice)_ |
| Sanity-gate first-run outcome | Task 6 Step 13 | _(success / sanity_warned + the error text)_ |
| snap-counts backfill coverage | Task 7 Step 8 | _(rows and mean offense_pct per season)_ |
| usage_weekly smoke: teams_observed / incomplete rows | Task 8 Step 9 | _(should be 32 / 0 for 2025 wk3)_ |
| Trend-engine flag count, 2025 wk6 | Task 9 Step 7 | _(count + rule_id histogram — the R10 baseline Plan 2's precision gate must beat)_ |
| **P4 ceiling `delta_pp`** | Task 10 Step 8 | **+29.07pp**, 95% CI **[+26.28, +31.85]**, `n_cells` = **1500** (3 gate seasons × 500 drafts). Reproduce: `uv run python scripts/waiver_ceiling_test.py --n-drafts 500`. |
| **P4 decision** | Task 10 Step 8 | **BUILD Plans 3-4** — the 8.0pp cut threshold is cleared 3.6×, and the whole 95% CI sits above it. **Caveat:** this is an ORACLE bound (perfect knowledge of every player's exact weekly score), not the share a realistic advisor recovers; P4 says the headroom is not small, it does not say Plans 3-4 capture it. Playoff probability also saturates (foresight = 1.000 in 1500/1500), so the all-play view (0.5788 → 0.9622) is the non-degenerate signal. Per-cell evidence: `logs/waiver-ceiling-2026-08-31-fixed-n500-cells.json` (tracked). |
| `pmset` confirmed | Task 11 Step 1 | _(output of `pmset -g sched`)_ |
| `FFI_BACKUP_REMOTE` destination | Task 11 Step 10 | _(USER INPUT REQUIRED — the operator's chosen offsite target, or "still unset")_ |
| Final guard + test counts | Task 12 Step 4 | _(file count, test count)_ |

## Handoff to Plan 2

Plan 1 leaves these named, deliberate gaps for Plan 2 to close, in priority order:

1. `src/ffi/reports/{claims,trends}.py` + `scripts/run_{claims_brief,trends_report}.py` + the `com.ffi.claims` / `com.ffi.trends` plists. Task 3's `config/source_clock.yaml` already carries their `active_from` dates (2026-09-14 / 2026-09-15) and Task 4's briefing already asserts on them — the assertions go RED the day after those dates if the renderers have not shipped.
2. `ffi.health.render_or_refuse` — build it with the first composite it wraps, not before.
3. `src/ffi/league_state/clock.py` as the sanctioned runtime reader of `config/league_clock.yaml`, gated on P3 being recorded. Task 5's validator fails the build the moment a module consumes an UNSET field, so this cannot be skipped by accident.
4. The 2025 replay precision gate (≤8 ASCENDING/week at ≥40% precision), the lead-time gate, and the ROS-mode gate. Thresholds tune on 2024 and gate on 2025 — Task 9 Step 7's flag count is the pre-tuning baseline.
5. `src/ffi/usage/market.py` — needs ≥4 weeks of the Task 1 archive before ADR TBD 3's numeric thresholds can be fitted, and it is the trigger for flipping the trending gate from observe-and-log to hard-fail.
6. Fill `route_share` and `rz_touches`. Task 8's migration already declares both columns, so this needs an ingester and a metric-availability change, not a schema change.
7. `src/ffi/flags.py` + `config/modules.yaml` (ARCHITECTURE TBD 1 — confirm the module name or fold it into `health.py`).

---

## Self-Review

Run against the spec with fresh eyes after writing. Findings and their fixes are recorded here rather than silently applied, so a reviewer can check the reasoning.

**1. Spec coverage.** Each of the spec's eleven numbered scope items maps to at least one task:

| Spec item | Task(s) | Status |
|---|---|---|
| 1. P2 trending ingest + table + migration + launchd | 1 | Covered. Deviation: own `com.ffi.trending` plist rather than a step in the morning chain — rationale in the plist comment (the unrecoverable feed must not share a failure domain with the chain that Task 2 rewrites). |
| 2. P1 nflverse scheduling + `&&`→`;`/trap + backfill | 2 | Covered. |
| 3. `src/ffi/health.py` + `config/source_clock.yaml` + stale-but-successful regression test | 3 | Covered (`test_r1_regression_stale_but_successful_is_not_ok`). |
| 4. Briefing integration: health.state, artifact freshness, archive continuity, budget | 4 | Covered. Budget projected at ~285 of 400 — the `src/ffi/reports/health_section.py` extraction is specified as the fallback in Step 7 but is not expected to be needed. |
| 5. `config/league_clock.yaml` + validator + USER-INPUT-REQUIRED P3 stop | 5 | Covered; Step 7 is an explicit blocking stop with the operator checklist verbatim and no fabricated values. |
| 6. `src/ffi/ingest/gates.py` + observe-and-log / hard-fail wiring | 6 | Covered. |
| 7. `src/ffi/usage/build.py` + `usage_weekly` + partial-week guard | 8 | Covered. |
| 8. `src/ffi/usage/trends.py` rule engine + cold-start + three fixtures | 9 | Covered (backfield-flip, no-change negative, cold-start week-2). |
| 9. P4 ceiling test as a thin harness over existing sim machinery | 10 | Covered, with the `<8pp → cut Plans 3/4` rule as an explicit step. |
| 10. `pmset`, `src/ffi/joblock.py`, offsite backup line | 11 | Covered. |
| 11. CI wiring + exit gate | 12 | Covered. |

ADR Preconditions: P1 (Tasks 2+3+4), P2 (Task 1), P3 (Task 5, operator-blocked and marked as such), P4 (Task 10). ADR Domain 1: three-state health (Task 3), gates at every ingest boundary (Task 6), partial-publish guard (Task 8), `;`-chain trap (Task 2) — `render_or_refuse` and the `ts_precision` screenshot path are excluded and named in Scope. ADR Domain 2: `raw.sleeper_trending` (Task 1), `public.usage_weekly` (Task 8), `config/league_clock.yaml` (Task 5), advisory lock (Task 11), cold start (Task 9) — league-state polarity and the dual Monday/Tuesday cadence are Plan 2. ADR Domain 5: age-aware health (Task 4), artifact freshness (Task 4), archive continuity (Task 4), evidence lines + rule IDs in trend output (Task 9) — capture recency needs `league_rosters`, which is Plan 2.

Risks in scope: R1 (Tasks 2/3/4), R5 (Task 8), R6 (Task 6), R7 (Task 9 coldstart), R8 (Tasks 1/4), R12 (Task 3's three states + Task 4's PENDING-before-`active_from` rule), R13 (Task 11 `pmset` + Task 4 detector), R22 (Task 11 joblock), R23 (Task 2 chain), R28 (Task 11 backup). All ten are addressed. R27 is also addressed (Tasks 7/8/9) though not listed in the spec — recorded as a deliberate addition.

**Gap found and fixed:** Task 4 as first drafted imported `ffi.joblock` from `scripts/morning_briefing.py`, but `src/ffi/joblock.py` is not created until Task 11. Executing Task 4 in order would have failed with `ModuleNotFoundError`. Fixed by removing the import and the `acquire_or_wait` call from Task 4 and adding them as explicit modification steps in Task 11 (Steps 6–7). Applied inline.

**2. Placeholder scan.** No `TBD`, `TODO`, `implement later`, `add appropriate error handling`, `write tests for the above`, or `similar to Task N` appears in any step. Every code step carries complete runnable code. Two intentional exceptions, both marked and both genuinely operator-owned rather than under-specified:
- Task 5 Step 7 (P3 observed mechanics) — an authenticated-browser task; fabricating a `waiver_processing_hour` would produce a confidently-wrong deadline every week, which is the failure the plan exists to prevent. The step blocks, hands the operator a verbatim checklist, and the `UNSET` sentinel plus Task 12's validator keep the repo safe if it is deferred.
- Task 11 Step 9 (`FFI_BACKUP_REMOTE`) — the destination is the operator's NAS/disk/cloud account. The code ships complete with three concrete example values; only the destination string is theirs.

The ARCHITECTURE.md TBD entries added in Tasks 3 and 9 are *resolutions* of pre-existing TBDs in that file, not new placeholders.

**Two issues found and fixed:**
- Task 6 Step 8 originally showed a first version of `sanity_check` and then corrected it in prose ("Note the `check_fieldset` call above compares a payload against itself…"), which violates the complete-code rule and would leave an executing agent guessing which version to write. Fixed: the step now shows only the final `sanity_check` and `_prior_add_rows`.
- Task 4 Step 4 had the same defect — it showed an `if __name__ != "__main__": raise SystemExit` guard and then said "that is wrong". Fixed: the step now specifies exactly two edits (wrap the body in `main()`, append the `__main__` guard) plus a verification command that proves the import is side-effect-free.

Both applied inline.

**3. Type consistency.** Cross-task name and type audit:
- `SleeperTrendingIngester` — defined Task 1, `sanity_mode`/`sanity_check`/`_prior_add_rows` added Task 6. `source = "sleeper_trending"` matches the `config/source_clock.yaml` key (Task 3) and the `raw.ingest_runs.source` value the briefing reads (Task 4). ✓
- `state(source, status, age_h, clock)` — defined Task 3, called with exactly four positional args in Task 4 (three call sites: ingest loop, sleeper snapshot, backup). ✓
- `SourceState.{OK,KNOWN_LAGGING,BROKEN}` — Task 3 enum; Task 4's `_mark` maps all three; `is_alarming` used consistently. ✓
- `ArtifactContract.{name,glob,due_weekday,active_from}` — Task 3; Task 4's `artifact_freshness_lines` reads exactly those four. ✓
- `SanityGateError` — Task 6 `gates.py`; imported by `base.py` (Task 6), raised by all three checks, caught in `run()`. `raw.ingest_runs.status` values `'sanity_warned'`/`'sanity_failed'` written by Task 6's migration 010 match the strings Task 3's `health.state` tests against (`_SUSPECT_STATUSES`, and the fall-through to BROKEN). ✓
- `check_fieldset(prev, curr, *, feed)` / `check_rank_correlation(prev, curr, *, feed, min_rho, min_overlap)` / `check_nonzero_coverage(rows, *, feed, value_key, min_players)` — signatures identical between Task 6's module, its tests, and both wiring sites. ✓
- `parse_seasons(spec) -> list[int]` — Task 2; imported by Task 7's `scripts/ingest_nflverse_snaps.py`. ✓
- `UsageRow` field order — Task 8's `load_usage_weekly` constructs `UsageRow(*r)` from a 14-column SELECT whose column order matches the dataclass field order exactly (`gsis_id, season, week, team, position, snap_share, target_share, carry_share, route_share, rz_touches, team_targets, team_carries, games_complete, teams_observed`). Verified field by field. ✓
- `UsageFrame.{season,week,rows,available_metrics,disabled_metrics,teams_observed}` — Task 8; Task 9's `classify` reads `f.week`, `f.season`, `f.rows`, `f.available_metrics`. ✓
- `Rule.{rule_id,direction,min_weeks,requires,cold_start,describe}` and `TrendSignal.{gsis_id,direction,rule_id,evidence,cold_start}` — declared in `ffi/usage/__init__.py` (Task 8), constructed in both `trends.py` and `coldstart.py` (Task 9) with matching keyword names. ✓
- Import direction `trends.py → coldstart.py` only (never the reverse); both import types from `ffi.usage`, which imports nothing internal. No cycle. ✓
- `acquire_or_wait(conn, name, wait_s, poll_s)` — Task 11; both call sites (`morning_briefing.py`, `build_valuation.py`) use the same lock name `"ffi.morning_chain"`, which Plan 2's Tuesday job must also use (recorded in the Handoff section). ✓
- `lineup_total(roster, week, lookup)` / `all_play_pct(rosters, lookup)` / `perfect_foresight_roster(roster, fa_pool, lookup, weekly_limit, season_limit)` — Task 10; tests call all three with matching signatures, and the `weekly_limit`/`season_limit` keywords appear in both. ✓
- `STARTERS` is imported from `ffi.sim.opponent` in both Task 10's harness and its test, never redefined. `FLEX_POS` and `REG_WEEKS` come from `ffi.sim.season`. ✓

**Inconsistency found and fixed:** Task 10's test file imported `STARTERS` inside a test function while the harness imported it at module level — harmless but inconsistent. Left as-is deliberately: the in-function import keeps the test's assertion self-documenting about where the constant comes from.

**Budget audit** (every new/modified file against ARCHITECTURE.md §1, projected line counts):
`health.py` ~145/150 (tight — Task 3 Step 6 measures it and names the extraction if it overruns) · `joblock.py` ~75/100 · `ingest/gates.py` ~110/250 · `ingest/sleeper_trending.py` ~135/400 · `ingest/nflverse_snaps.py` ~120/400 · `ingest/base.py` ~110/400 · `usage/__init__.py` ~75/120 · `usage/build.py` ~185/300 · `usage/trends.py` ~250/400 · `usage/coldstart.py` ~150/200 · `morning_briefing.py` ~285/400 · `build_valuation.py` ~222/600 · `scripts/waiver_ceiling_test.py` ~230/600 · `scripts/validate_league_clock.py` ~95/600 · `scripts/ingest_sleeper_trending.py` ~28/600 · `scripts/ingest_nflverse_snaps.py` ~18/600. Two files are within 10% of their ceiling (`health.py`, `usage/coldstart.py`); both have an explicit measurement step and a named split, and neither budget is raised.
