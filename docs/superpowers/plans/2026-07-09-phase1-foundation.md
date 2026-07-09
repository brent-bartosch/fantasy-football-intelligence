# Phase 1: Foundation & Data Revival — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revive the Postgres foundation and land all external data (Sleeper, nflverse, Yahoo history + 2025 season, ID crosswalk) with fail-loud ingestion plumbing, resolving the league-identity and era-drift questions the rest of the build depends on.

**Architecture:** New `src/ffi/` Python package (uv-managed) alongside the legacy `scripts/` (which stays untouched as reference). New Postgres named schemas (`raw`, `scoring`, `valuation`, `signals`, `sim`, `draft`) per ADR Domain 2; existing flat tables remain in `public` (the core layer). Every ingestion goes through a run-record + schema-hash framework that fails loud (ADR Domain 1).

**Tech Stack:** Python ≥3.11, uv, psycopg2-binary (matches legacy scripts), pydantic v2, structlog, `yahoo_fantasy_api` + `yahoo_oauth` (spilchen's maintained wrappers), `nflreadpy` (polars), pytest + hypothesis.

## Global Constraints

- **Fail-loud (ADR Domain 1):** parsers never paper over missing keys — no `.get(k, default)` for load-bearing fields; raise `IngestError` with the raw payload excerpt. All try/except written under the fail-loud-error-handling skill.
- **Yahoo throttle (R15):** bulk imports ≤ 1 request / 2 s, resumable, run overnight; on HTTP error 999 abort the job (no retry loop) and report.
- **Schema placement (ADR Domain 2):** new areas in named Postgres schemas; new core tables + crosswalk in `public`.
- **Dependencies (ADR Domain 6):** `uv` + `pyproject.toml` + `uv.lock` only. Never bare `pip install`.
- **Secrets (ADR Domain 3):** all credentials from `.env` / gitignored files; never committed, never logged.
- **Data vintage:** every raw table row carries `fetched_at`; every ingestion writes a run record.
- Databases: `fantasy_football` (real), `fantasy_football_test` (tests). Tests never touch the real DB.
- Legacy `scripts/*.py` are read-only reference — do not modify or delete in this phase.

## Phase sequence (this plan = Phase 1 only)

| Phase | Week | Plan status | Carried nice-to-fixes |
|---|---|---|---|
| 1 Foundation & data revival | 1 | **this document** | browser-profile gitignore (Task 1) |
| 2 Scoring engine + valuation | 2–3 | plan at week-2 start, informed by Phase 1 audit | scoring purity property test; pg_restore drill (week 3) |
| 3 Simulator + backtests | 3–4 | plan at week-3 start | sim-farm log policy: results → `sim` schema tables, logs errors-only |
| 4 Draft assistant + briefing | 4–5 | plan at week-4 start | agent-lane 8-min time-box + latency measurement in rehearsals |
| 5 Freeze + rehearsals | 6 | runbook, not plan | — |

---

### Task 1: uv project scaffolding + gitignore hardening

**Files:**
- Create: `pyproject.toml`
- Create: `src/ffi/__init__.py`
- Create: `tests/test_scaffold.py`
- Modify: `.gitignore` (append)

**Interfaces:**
- Produces: importable `ffi` package; `uv run pytest` works; later tasks add modules under `src/ffi/`.

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "ffi"
version = "0.1.0"
description = "Fantasy football draft intelligence (2026 rebuild)"
requires-python = ">=3.11"
dependencies = [
    "psycopg2-binary>=2.9",
    "python-dotenv>=1.0",
    "requests>=2.31",
    "pydantic>=2.7",
    "structlog>=24.1",
    "yahoo-fantasy-api>=2.9",
    "yahoo-oauth>=2.0",
    "nflreadpy>=0.1",
    "polars>=1.0",
]

[dependency-groups]
dev = ["pytest>=8.0", "hypothesis>=6.100"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/ffi"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create package + failing smoke test**

`src/ffi/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/test_scaffold.py`:
```python
def test_package_imports():
    import ffi
    assert ffi.__version__ == "0.1.0"
```

- [ ] **Step 3: Sync and run**

Run: `uv sync && uv run pytest tests/test_scaffold.py -v`
Expected: PASS (1 test). If `uv` is missing: `brew install uv` first.

- [ ] **Step 4: Append to .gitignore**

```gitignore

# Browser automation profiles (authenticated session state = secrets)
browser-profiles/
.playwright/
playwright/.auth/

# Yahoo oauth wrapper token file (Task 6)
config/yahoo_oauth.json

# Database backups
backups/
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/ffi/__init__.py tests/test_scaffold.py .gitignore
git commit -m "feat: uv project scaffold for ffi package; gitignore browser profiles and backups"
```

---

### Task 2: Postgres revival, foundation migration, backup script

**Files:**
- Create: `migrations/001_foundation.sql`
- Create: `scripts/backup_db.sh`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: schemas `raw, scoring, valuation, signals, sim, draft`; tables `raw.ingest_runs`, `raw.sleeper_projections`, `raw.nflverse_player_week`, `raw.yahoo_league_settings`, `raw.yahoo_player_week`, `public.player_id_xwalk`; pytest fixture `db` (connection to `fantasy_football_test` with migration applied).

- [ ] **Step 1: Start Postgres and verify the 17-year data survived**

```bash
brew services start postgresql@15
psql fantasy_football -c "SELECT count(*) AS picks FROM draft_picks; SELECT count(*) AS leagues FROM leagues;"
```
Expected: picks ≈ 3782, leagues ≈ 17. **If the database is missing or counts are zero, STOP and report to the user before proceeding** — recovery strategy changes the whole phase.

- [ ] **Step 2: Write the idempotent migration**

`migrations/001_foundation.sql`:
```sql
-- Phase 1 foundation: named schemas (ADR Domain 2) + ingestion plumbing (ADR Domain 1)
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS scoring;
CREATE SCHEMA IF NOT EXISTS valuation;
CREATE SCHEMA IF NOT EXISTS signals;
CREATE SCHEMA IF NOT EXISTS sim;
CREATE SCHEMA IF NOT EXISTS draft;

CREATE TABLE IF NOT EXISTS raw.ingest_runs (
    run_id      SERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','success','failed')),
    row_count   INTEGER,
    schema_hash TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ingest_runs_source ON raw.ingest_runs(source, started_at DESC);

CREATE TABLE IF NOT EXISTS raw.sleeper_projections (
    snapshot_id SERIAL PRIMARY KEY,
    run_id      INTEGER REFERENCES raw.ingest_runs(run_id),
    season      INTEGER NOT NULL,
    week        INTEGER,                -- NULL = season-level projection
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload     JSONB NOT NULL          -- full API response, untouched
);

CREATE TABLE IF NOT EXISTS raw.nflverse_player_week (
    gsis_id                 TEXT NOT NULL,
    season                  INTEGER NOT NULL,
    week                    INTEGER NOT NULL,
    player_name             TEXT,
    position                TEXT,
    team                    TEXT,
    completions             INTEGER,
    attempts                INTEGER,
    passing_yards           REAL,
    passing_tds             INTEGER,
    passing_first_downs     INTEGER,
    interceptions           INTEGER,
    carries                 INTEGER,
    rushing_yards           REAL,
    rushing_tds             INTEGER,
    rushing_first_downs     INTEGER,
    receptions              INTEGER,
    targets                 INTEGER,
    receiving_yards         REAL,
    receiving_tds           INTEGER,
    receiving_first_downs   INTEGER,
    punt_return_yards       REAL,
    kickoff_return_yards    REAL,
    fumbles_lost            INTEGER,
    fetched_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (gsis_id, season, week)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_league_settings (
    league_key  TEXT PRIMARY KEY,
    season      INTEGER NOT NULL,
    league_name TEXT,
    num_teams   INTEGER,
    renew       TEXT,      -- '{game_id}_{league_id}' of PREVIOUS season, '' if none
    renewed     TEXT,      -- next season's pointer
    qb_slots    INTEGER,   -- starting QB slots (2QB detection, risk R4)
    roster_positions JSONB,
    managers    JSONB,     -- {manager_guid: nickname} for the season (R9 continuity)
    settings_payload JSONB NOT NULL,   -- full settings response, untouched
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS raw.yahoo_player_week (
    league_key      TEXT NOT NULL,
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    yahoo_player_id TEXT NOT NULL,
    total_points    NUMERIC,
    stats           JSONB NOT NULL,    -- raw stat list from API
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, week, yahoo_player_id)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_standings (
    league_key   TEXT NOT NULL,
    team_key     TEXT NOT NULL,
    season       INTEGER NOT NULL,
    team_name    TEXT,
    final_rank   INTEGER,
    payload      JSONB NOT NULL,     -- full standings entry (W/L, PF/PA, manager info)
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, team_key)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_matchups (
    league_key   TEXT NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    payload      JSONB NOT NULL,     -- full scoreboard response; parsed in Phase 2
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, week)
);

CREATE TABLE IF NOT EXISTS raw.yahoo_transactions (
    league_key      TEXT NOT NULL,
    transaction_key TEXT NOT NULL,
    season          INTEGER NOT NULL,
    type            TEXT,             -- add, drop, add/drop, trade, commish
    ts              TIMESTAMPTZ,
    payload         JSONB NOT NULL,   -- full transaction incl players, teams, waiver detail
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_key, transaction_key)
);

CREATE TABLE IF NOT EXISTS public.player_id_xwalk (
    xwalk_id        SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    position        TEXT,
    team            TEXT,
    gsis_id         TEXT,
    sleeper_id      TEXT,
    yahoo_id        TEXT,
    fantasypros_id  TEXT,
    manual_override BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_xwalk_yahoo ON public.player_id_xwalk(yahoo_id);
CREATE INDEX IF NOT EXISTS idx_xwalk_sleeper ON public.player_id_xwalk(sleeper_id);
```

- [ ] **Step 3: Apply to real DB and create test DB**

```bash
psql fantasy_football -f migrations/001_foundation.sql
createdb fantasy_football_test 2>/dev/null || true
psql fantasy_football_test -f schema/create_tables.sql
psql fantasy_football_test -f migrations/001_foundation.sql
psql fantasy_football -c "\dn"
```
Expected: schemas `draft, public, raw, scoring, signals, sim, valuation` listed; no errors on re-run (idempotent).

- [ ] **Step 4: Write the pytest DB fixture**

`tests/conftest.py`:
```python
import pathlib
import psycopg2
import psycopg2.extras
import pytest

@pytest.fixture()
def db():
    conn = psycopg2.connect(dbname="fantasy_football_test", host="localhost")
    mig = pathlib.Path(__file__).parent.parent / "migrations" / "001_foundation.sql"
    with conn.cursor() as cur:
        cur.execute(mig.read_text())
    conn.commit()
    yield conn
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE raw.ingest_runs RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE raw.sleeper_projections, raw.nflverse_player_week, "
                    "raw.yahoo_league_settings, raw.yahoo_player_week, public.player_id_xwalk")
        cur.execute("TRUNCATE players CASCADE")  # tests seed players; keep runs idempotent
    conn.commit()
    conn.close()
```

- [ ] **Step 5: Write backup script and run it once**

`scripts/backup_db.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
pg_dump fantasy_football | gzip > "backups/fantasy_football_$(date +%Y%m%d_%H%M%S).sql.gz"
# keep newest 14
ls -t backups/fantasy_football_*.sql.gz | tail -n +15 | xargs -r rm
echo "Backup complete: $(ls -t backups/fantasy_football_*.sql.gz | head -1)"
```

Run: `chmod +x scripts/backup_db.sh && ./scripts/backup_db.sh`
Expected: prints `Backup complete: backups/fantasy_football_<timestamp>.sql.gz`; file is >100KB (17 years of data, not an empty DB).

- [ ] **Step 6: Commit**

```bash
git add migrations/001_foundation.sql scripts/backup_db.sh tests/conftest.py
git commit -m "feat: foundation migration (named schemas, ingest plumbing tables), test DB fixture, backup script"
```

---

### Task 3: Ingestion framework — run records + fail-loud BaseIngester

**Files:**
- Create: `src/ffi/db.py`
- Create: `src/ffi/ingest/__init__.py`
- Create: `src/ffi/ingest/base.py`
- Test: `tests/test_ingest_base.py`

**Interfaces:**
- Consumes: `db` fixture (Task 2).
- Produces:
  - `ffi.db.connect(dbname: str | None = None) -> psycopg2 connection` (reads `DB_*` env vars, defaults matching legacy scripts).
  - `ffi.ingest.base.IngestError(Exception)`
  - `ffi.ingest.base.BaseIngester` with `source: str` class attr; subclasses override `fetch() -> object`, `validate(payload) -> int` (row count; raises `IngestError`), `store(conn, run_id, payload) -> None`; framework method `run(conn) -> int` (returns run_id).
  - `ffi.ingest.base.schema_hash(record: dict) -> str` (sha256 of sorted key names).

- [ ] **Step 1: Write failing tests**

`tests/test_ingest_base.py`:
```python
import pytest
from ffi.ingest.base import BaseIngester, IngestError, schema_hash


class GoodIngester(BaseIngester):
    source = "test_good"
    def fetch(self):
        return [{"a": 1, "b": 2}]
    def validate(self, payload):
        return len(payload)
    def store(self, conn, run_id, payload):
        pass


class BadIngester(GoodIngester):
    source = "test_bad"
    def validate(self, payload):
        raise IngestError("missing field 'b'")


def _run_row(db, run_id):
    with db.cursor() as cur:
        cur.execute("SELECT source, status, row_count, error FROM raw.ingest_runs WHERE run_id=%s", (run_id,))
        return cur.fetchone()


def test_successful_run_records_success(db):
    run_id = GoodIngester().run(db)
    source, status, row_count, error = _run_row(db, run_id)
    assert (source, status, row_count, error) == ("test_good", "success", 1, None)


def test_failed_validation_records_failure_and_reraises(db):
    with pytest.raises(IngestError, match="missing field 'b'"):
        BadIngester().run(db)
    with db.cursor() as cur:
        cur.execute("SELECT status, error FROM raw.ingest_runs WHERE source='test_bad' ORDER BY run_id DESC LIMIT 1")
        status, error = cur.fetchone()
    assert status == "failed"
    assert "missing field 'b'" in error


def test_schema_hash_depends_on_keys_not_values():
    assert schema_hash({"a": 1, "b": 2}) == schema_hash({"b": 99, "a": 0})
    assert schema_hash({"a": 1}) != schema_hash({"a": 1, "c": 3})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ingest_base.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'ffi.ingest'`

- [ ] **Step 3: Implement**

`src/ffi/db.py`:
```python
import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def connect(dbname: str | None = None):
    return psycopg2.connect(
        dbname=dbname or os.getenv("DB_NAME", "fantasy_football"),
        user=os.getenv("DB_USER", "brentbartosch"),
        password=os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
    )
```

`src/ffi/ingest/__init__.py`: empty file.

`src/ffi/ingest/base.py`:
```python
import hashlib
import structlog

log = structlog.get_logger()


class IngestError(Exception):
    """Raised when a source's payload fails validation. Never swallowed."""


def schema_hash(record: dict) -> str:
    return hashlib.sha256("|".join(sorted(record.keys())).encode()).hexdigest()


class BaseIngester:
    source: str = None  # subclasses must set

    def fetch(self):
        raise NotImplementedError

    def validate(self, payload) -> int:
        raise NotImplementedError

    def store(self, conn, run_id: int, payload) -> None:
        raise NotImplementedError

    def _first_record(self, payload) -> dict | None:
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict):
            return payload
        return None

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
            self.store(conn, run_id, payload)
            first = self._first_record(payload)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE raw.ingest_runs SET finished_at=now(), status='success', "
                    "row_count=%s, schema_hash=%s WHERE run_id=%s",
                    (row_count, schema_hash(first) if first else None, run_id),
                )
            conn.commit()
            log.info("ingest.success", source=self.source, run_id=run_id, rows=row_count)
            return run_id
        except Exception as exc:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE raw.ingest_runs SET finished_at=now(), status='failed', error=%s WHERE run_id=%s",
                    (str(exc), run_id),
                )
            conn.commit()
            log.error("ingest.failed", source=self.source, run_id=run_id, error=str(exc))
            raise  # fail loud — callers/cron must see nonzero exit
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ingest_base.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/ffi/db.py src/ffi/ingest/ tests/test_ingest_base.py
git commit -m "feat: fail-loud ingestion framework with run records and schema hashing"
```

---

### Task 4: Sleeper projections ingester

**Files:**
- Create: `src/ffi/ingest/sleeper.py`
- Create: `tests/fixtures/sleeper_projections_sample.json`
- Create: `scripts/ingest_sleeper.py` (CLI entry)
- Test: `tests/test_sleeper_ingest.py`

**Interfaces:**
- Consumes: `BaseIngester`, `IngestError`, `ffi.db.connect`.
- Produces: `SleeperProjectionsIngester(season: int, week: int | None)` storing full payload to `raw.sleeper_projections`; module constant `REQUIRED_QB_FIELDS` / `REQUIRED_SKILL_FIELDS`. CLI: `uv run python scripts/ingest_sleeper.py --season 2025 --week 5`.

**Context for the engineer:** Endpoint (undocumented, verified live 2026-07-08):
`https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF`
Returns a JSON list; each record has `player_id`, `company` (currently `"rotowire"`), and a `stats` dict containing per-position keys like `pass_att, pass_cmp, pass_fd, rush_att, rush_fd, rec, rec_fd, rec_tgt, pts_ppr`. **First downs (`*_fd`) are the fields this whole project needs — validation must prove they're present.** If the response shape differs from this description, do NOT adapt silently: dump one record with `--inspect` and stop (fail-loud; risk R5).

- [ ] **Step 1: Create the fixture from the documented shape**

`tests/fixtures/sleeper_projections_sample.json`:
```json
[
  {"player_id": "4881", "company": "rotowire", "season": "2025", "week": "5",
   "stats": {"pass_att": 34.2, "pass_cmp": 22.9, "pass_fd": 11.8, "pass_yd": 259.0,
              "pass_td": 1.8, "pass_int": 0.7, "rush_att": 6.1, "rush_yd": 38.0,
              "rush_fd": 2.2, "pts_ppr": 21.4}},
  {"player_id": "9509", "company": "rotowire", "season": "2025", "week": "5",
   "stats": {"rush_att": 17.5, "rush_yd": 82.0, "rush_fd": 4.6, "rush_td": 0.6,
              "rec": 3.4, "rec_tgt": 4.3, "rec_yd": 24.0, "rec_fd": 1.2, "pts_ppr": 16.9}}
]
```

- [ ] **Step 2: Write failing tests**

`tests/test_sleeper_ingest.py`:
```python
import json
import pathlib
import pytest
from ffi.ingest.base import IngestError
from ffi.ingest.sleeper import SleeperProjectionsIngester

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "sleeper_projections_sample.json").read_text()
)


class FixtureIngester(SleeperProjectionsIngester):
    def fetch(self):
        return FIXTURE


def test_validate_passes_on_good_payload():
    ing = FixtureIngester(season=2025, week=5)
    assert ing.validate(FIXTURE) == 2


def test_validate_fails_when_first_downs_missing():
    broken = json.loads(json.dumps(FIXTURE))
    for rec in broken:
        rec["stats"].pop("pass_fd", None)
        rec["stats"].pop("rush_fd", None)
        rec["stats"].pop("rec_fd", None)
    ing = FixtureIngester(season=2025, week=5)
    with pytest.raises(IngestError, match="first-down"):
        ing.validate(broken)


def test_validate_fails_on_empty_payload():
    ing = FixtureIngester(season=2025, week=5)
    with pytest.raises(IngestError, match="empty"):
        ing.validate([])


def test_store_writes_snapshot(db):
    ing = FixtureIngester(season=2025, week=5)
    run_id = ing.run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT season, week, jsonb_array_length(payload) FROM raw.sleeper_projections WHERE run_id=%s",
            (run_id,),
        )
        assert cur.fetchone() == (2025, 5, 2)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_sleeper_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ffi.ingest.sleeper'`

- [ ] **Step 4: Implement**

`src/ffi/ingest/sleeper.py`:
```python
import json
import requests
from ffi.ingest.base import BaseIngester, IngestError

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
BASE_URL = "https://api.sleeper.app/projections/nfl"
# The project's core edge depends on first downs being projected. Fail loud if they vanish.
FIRST_DOWN_FIELDS = {"pass_fd", "rush_fd", "rec_fd"}


class SleeperProjectionsIngester(BaseIngester):
    source = "sleeper_projections"

    def __init__(self, season: int, week: int | None):
        self.season = season
        self.week = week

    def fetch(self):
        url = f"{BASE_URL}/{self.season}"
        if self.week is not None:
            url = f"{url}/{self.week}"
        params = [("season_type", "regular")] + [("position[]", p) for p in POSITIONS]
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def validate(self, payload) -> int:
        if not isinstance(payload, list) or not payload:
            raise IngestError(f"sleeper: empty or non-list payload: {str(payload)[:200]}")
        seen_fd = set()
        for rec in payload:
            if "stats" not in rec or "player_id" not in rec:
                raise IngestError(
                    f"sleeper: record missing 'stats'/'player_id' — schema drift? record: {json.dumps(rec)[:300]}"
                )
            seen_fd |= FIRST_DOWN_FIELDS & set(rec["stats"].keys())
        if seen_fd != FIRST_DOWN_FIELDS:
            raise IngestError(
                f"sleeper: first-down fields missing from entire payload: {FIRST_DOWN_FIELDS - seen_fd}. "
                "This breaks FD pricing (design 4.2). Inspect payload before proceeding."
            )
        return len(payload)

    def store(self, conn, run_id: int, payload) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.sleeper_projections (run_id, season, week, payload) VALUES (%s,%s,%s,%s)",
                (run_id, self.season, self.week, json.dumps(payload)),
            )
```

`scripts/ingest_sleeper.py`:
```python
#!/usr/bin/env python3
"""Snapshot Sleeper projections into raw.sleeper_projections. Fail-loud; exits nonzero on any error."""
import argparse
import json
import sys
from ffi.db import connect
from ffi.ingest.sleeper import SleeperProjectionsIngester

parser = argparse.ArgumentParser()
parser.add_argument("--season", type=int, required=True)
parser.add_argument("--week", type=int, default=None, help="omit for season-level projections")
parser.add_argument("--inspect", action="store_true", help="print first record and exit (no DB write)")
args = parser.parse_args()

ing = SleeperProjectionsIngester(season=args.season, week=args.week)
if args.inspect:
    payload = ing.fetch()
    print(json.dumps(payload[0] if payload else payload, indent=2))
    sys.exit(0)
conn = connect()
run_id = ing.run(conn)
print(f"OK run_id={run_id}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_sleeper_ingest.py -v`
Expected: 4 PASS

- [ ] **Step 6: Live verification against the real API**

```bash
uv run python scripts/ingest_sleeper.py --season 2025 --week 5 --inspect
uv run python scripts/ingest_sleeper.py --season 2025 --week 5
uv run python scripts/ingest_sleeper.py --season 2026 --week 1 || echo "2026 not yet published — acceptable in July, retry weekly"
```
Expected: inspect prints a record with `stats` containing `*_fd` keys; 2025 ingest prints `OK run_id=N`. The 2026 attempt may legitimately fail this early — the `||` branch documents that; do NOT weaken the ingester to tolerate it silently.

- [ ] **Step 7: Commit**

```bash
git add src/ffi/ingest/sleeper.py scripts/ingest_sleeper.py tests/test_sleeper_ingest.py tests/fixtures/sleeper_projections_sample.json
git commit -m "feat: Sleeper projections ingester with first-down validation"
```

---

### Task 5: nflverse historical actuals ingester

**Files:**
- Create: `src/ffi/ingest/nflverse.py`
- Create: `scripts/ingest_nflverse.py`
- Test: `tests/test_nflverse_ingest.py`

**Interfaces:**
- Consumes: `BaseIngester`, `IngestError`.
- Produces: `NflversePlayerWeekIngester(seasons: list[int])` filling `raw.nflverse_player_week`; module constant `REQUIRED_COLS: set[str]`. CLI: `uv run python scripts/ingest_nflverse.py --seasons 2019-2025`.

**Context:** `nflreadpy.load_player_stats(seasons=[...])` returns a polars DataFrame. Verified (research, 2026-07-08): weekly player stats include `passing_first_downs`, `rushing_first_downs`, `receiving_first_downs`, `completions`, `attempts`, `carries`, `punt_return_yards`, `kickoff_return_yards`. The join key to other sources is `player_id` (GSIS id, e.g. `00-0034796`). If column names differ from `REQUIRED_COLS` at runtime, that is schema drift → the ingester must raise, and the engineer reports the actual column list rather than renaming ad hoc.

- [ ] **Step 1: Write failing tests (validation logic only — no network in tests)**

`tests/test_nflverse_ingest.py`:
```python
import polars as pl
import pytest
from ffi.ingest.base import IngestError
from ffi.ingest.nflverse import NflversePlayerWeekIngester, REQUIRED_COLS


def _frame(cols):
    return pl.DataFrame({c: [None] for c in cols})


def test_validate_passes_with_required_columns():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _frame(REQUIRED_COLS)
    assert ing.validate(df) == 1


def test_validate_fails_on_missing_first_down_column():
    ing = NflversePlayerWeekIngester(seasons=[2024])
    df = _frame(REQUIRED_COLS - {"rushing_first_downs"})
    with pytest.raises(IngestError, match="rushing_first_downs"):
        ing.validate(df)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_nflverse_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/ffi/ingest/nflverse.py`:
```python
import polars as pl
import psycopg2.extras
from ffi.ingest.base import BaseIngester, IngestError

REQUIRED_COLS = {
    "player_id", "season", "week", "player_display_name", "position", "team",
    "completions", "attempts", "passing_yards", "passing_tds", "passing_first_downs",
    "interceptions", "carries", "rushing_yards", "rushing_tds", "rushing_first_downs",
    "receptions", "targets", "receiving_yards", "receiving_tds", "receiving_first_downs",
    "punt_return_yards", "kickoff_return_yards", "fumbles_lost",
}

_DB_COLS = [
    "gsis_id", "season", "week", "player_name", "position", "team",
    "completions", "attempts", "passing_yards", "passing_tds", "passing_first_downs",
    "interceptions", "carries", "rushing_yards", "rushing_tds", "rushing_first_downs",
    "receptions", "targets", "receiving_yards", "receiving_tds", "receiving_first_downs",
    "punt_return_yards", "kickoff_return_yards", "fumbles_lost",
]


class NflversePlayerWeekIngester(BaseIngester):
    source = "nflverse_player_week"

    def __init__(self, seasons: list[int]):
        self.seasons = seasons

    def fetch(self):
        import nflreadpy
        return nflreadpy.load_player_stats(seasons=self.seasons)

    def validate(self, df: pl.DataFrame) -> int:
        missing = REQUIRED_COLS - set(df.columns)
        if missing:
            raise IngestError(
                f"nflverse: expected columns missing: {sorted(missing)}. "
                f"Actual columns: {sorted(df.columns)[:40]}... Schema drift — investigate, do not rename blindly."
            )
        if df.height == 0:
            raise IngestError(f"nflverse: zero rows for seasons {self.seasons}")
        return df.height

    def store(self, conn, run_id: int, df: pl.DataFrame) -> None:
        ordered_src = [
            "player_id", "season", "week", "player_display_name", "position", "team",
            "completions", "attempts", "passing_yards", "passing_tds", "passing_first_downs",
            "interceptions", "carries", "rushing_yards", "rushing_tds", "rushing_first_downs",
            "receptions", "targets", "receiving_yards", "receiving_tds", "receiving_first_downs",
            "punt_return_yards", "kickoff_return_yards", "fumbles_lost",
        ]
        rows = df.select(ordered_src).rows()
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM raw.nflverse_player_week WHERE season = ANY(%s)", (self.seasons,)
            )
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO raw.nflverse_player_week ({', '.join(_DB_COLS)}) VALUES %s",
                rows,
                page_size=5000,
            )

    def _first_record(self, payload):
        return {c: None for c in payload.columns} if isinstance(payload, pl.DataFrame) else None
```

`scripts/ingest_nflverse.py`:
```python
#!/usr/bin/env python3
"""Load nflverse weekly player stats into raw.nflverse_player_week."""
import argparse
from ffi.db import connect
from ffi.ingest.nflverse import NflversePlayerWeekIngester

parser = argparse.ArgumentParser()
parser.add_argument("--seasons", default="2019-2025", help="e.g. 2019-2025 or 2024")
args = parser.parse_args()
if "-" in args.seasons:
    lo, hi = args.seasons.split("-")
    seasons = list(range(int(lo), int(hi) + 1))
else:
    seasons = [int(args.seasons)]
run_id = NflversePlayerWeekIngester(seasons=seasons).run(connect())
print(f"OK run_id={run_id}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_nflverse_ingest.py -v`
Expected: 2 PASS

- [ ] **Step 5: Live load 2019–2025 and sanity-query**

```bash
uv run python scripts/ingest_nflverse.py --seasons 2019-2025
psql fantasy_football -c "SELECT season, count(*) rows, sum(rushing_first_downs) rush_fd FROM raw.nflverse_player_week GROUP BY season ORDER BY season;"
```
Expected: 7 season rows, each with tens of thousands of rows and nonzero `rush_fd`. If `load_player_stats` raises on column drift, report actual columns to the user (do not patch silently).

- [ ] **Step 6: Commit**

```bash
git add src/ffi/ingest/nflverse.py scripts/ingest_nflverse.py tests/test_nflverse_ingest.py
git commit -m "feat: nflverse weekly actuals ingester (first downs, completions, return yards)"
```

---

### Task 6: Yahoo session adapter (token conversion + smoke test)

**Files:**
- Create: `src/ffi/yahoo_client.py`
- Create: `scripts/yahoo_smoke.py`

**Interfaces:**
- Consumes: `.env` (`YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`), legacy `config/yahoo_token.json` if present.
- Produces: `ffi.yahoo_client.get_session() -> yahoo_oauth.OAuth2` (auto-refreshing) and `get_league(session, league_key) -> yahoo_fantasy_api.league.League`. All later Yahoo tasks use only these two functions.

**Context:** We adopt spilchen's `yahoo_fantasy_api` + `yahoo_oauth` instead of the legacy hand-rolled OAuth (battle-tested refresh handling — ADR Domain 4). `yahoo_oauth.OAuth2` wants a JSON file with `consumer_key`, `consumer_secret`, `access_token`, `refresh_token`, `token_time`, `token_type`. The legacy token in `config/yahoo_token.json` is from Aug 2025 — its refresh token may still work (Yahoo refresh tokens are long-lived) or may not. **If refresh fails, run the legacy `python scripts/yahoo_manual_auth.py` to re-authorize, then re-run the converter.** `config/yahoo_oauth.json` is already gitignored (Task 1).

- [ ] **Step 1: Implement the adapter**

`src/ffi/yahoo_client.py`:
```python
import json
import os
import pathlib
import time
from dotenv import load_dotenv

load_dotenv()

OAUTH_FILE = pathlib.Path("config/yahoo_oauth.json")
LEGACY_TOKEN = pathlib.Path("config/yahoo_token.json")


class YahooAuthError(Exception):
    pass


def _build_oauth_file() -> None:
    consumer_key = os.getenv("YAHOO_CLIENT_ID")
    consumer_secret = os.getenv("YAHOO_CLIENT_SECRET")
    if not consumer_key or not consumer_secret:
        raise YahooAuthError("YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET missing from .env")
    if not LEGACY_TOKEN.exists():
        raise YahooAuthError(
            f"{LEGACY_TOKEN} not found. Run: python scripts/yahoo_manual_auth.py, then retry."
        )
    legacy = json.loads(LEGACY_TOKEN.read_text())
    if "refresh_token" not in legacy:
        raise YahooAuthError(
            f"{LEGACY_TOKEN} has keys {sorted(legacy.keys())} — expected 'refresh_token'. "
            "Re-authorize with scripts/yahoo_manual_auth.py."
        )
    OAUTH_FILE.write_text(json.dumps({
        "consumer_key": consumer_key,
        "consumer_secret": consumer_secret,
        "access_token": legacy.get("access_token", ""),
        "refresh_token": legacy["refresh_token"],
        "token_type": legacy.get("token_type", "bearer"),
        "token_time": 0.0,  # force immediate refresh on first use
    }))
    OAUTH_FILE.chmod(0o600)


def get_session():
    from yahoo_oauth import OAuth2
    if not OAUTH_FILE.exists():
        _build_oauth_file()
    sc = OAuth2(None, None, from_file=str(OAUTH_FILE))
    if not sc.token_is_valid():
        sc.refresh_access_token()
    if not sc.token_is_valid():
        raise YahooAuthError(
            "Yahoo token refresh failed — refresh token likely revoked. "
            "Run: python scripts/yahoo_manual_auth.py, delete config/yahoo_oauth.json, retry."
        )
    return sc


def get_league(session, league_key: str):
    import yahoo_fantasy_api as yfa
    return yfa.league.League(session, league_key)
```

- [ ] **Step 2: Write the smoke script**

`scripts/yahoo_smoke.py`:
```python
#!/usr/bin/env python3
"""Preflight: prove Yahoo auth + API access work. Part of the draft-day runbook later."""
from ffi.yahoo_client import get_session, get_league

LMU_2025 = "461.l.863132"   # from legacy import_all_lmu.py
session = get_session()
lg = get_league(session, LMU_2025)
settings = lg.settings()
print(f"OK: league '{settings.get('name')}' season {settings.get('season')} "
      f"num_teams={settings.get('num_teams')}")
```

- [ ] **Step 3: Run the smoke test (live)**

Run: `uv run python scripts/yahoo_smoke.py`
Expected: `OK: league '<name>' season 2025 num_teams=<N>`.
If it raises `YahooAuthError` about refresh failure: run `python scripts/yahoo_manual_auth.py` (interactive — needs the user in the loop), delete `config/yahoo_oauth.json`, retry. **This is the one step that may need the user; flag it early in the session, not at the end.**

- [ ] **Step 4: Commit**

```bash
git add src/ffi/yahoo_client.py scripts/yahoo_smoke.py
git commit -m "feat: Yahoo session adapter over yahoo_oauth/yahoo_fantasy_api with legacy token conversion"
```

---

### Task 7: League-identity + era audit (renew chain, 2QB detection) — CRITICAL

**Files:**
- Create: `scripts/audit_league_history.py`
- Test: `tests/test_league_audit.py`

**Interfaces:**
- Consumes: `get_session`, `get_league` (Task 6); `raw.yahoo_league_settings` (Task 2).
- Produces: populated `raw.yahoo_league_settings` for every season in the target league's renew chain; a printed audit report; function `walk_renew_chain(session, start_key: str) -> list[dict]` and `parse_settings(league_key: str, settings: dict) -> dict` (keys: `league_key, season, league_name, num_teams, renew, renewed, qb_slots, roster_positions`).

**Why critical (risks R4, R9):** Two identity questions gate everything downstream:
1. **RESOLVED by live probe 2026-07-09 (pre-execution):** NAJEE (`461.l.326814`) and LMU (`461.l.863132`) are confirmed different leagues, both active in 2025. The NAJEE renew chain runs **16 seasons, 2010–2025, always 12 teams, renamed annually** (SPEED RASHEE 2024, DARKNESS RETREAT 2023, … BEN RAPETHLISBERGER 2010) with **zero overlap** with the legacy-imported LMU chain. The target league's own history was never imported. The audit's zero-overlap warning is therefore *expected* — do not stop for it; the user has been informed. The audit still runs in full to persist per-season settings, managers, and the 2QB era boundary for the NAJEE chain.
2. Per-season `qb_slots` answers "when did this league become 2QB" — the era-segmentation input for opponent modeling (R4).
3. Manager GUID continuity across the NAJEE chain verifies the "same core managers" premise and locates the user's "Sports" GUID (R9).

NAJEE chain keys (from the probe; the audit re-derives and persists them):
`461.l.326814` 2025, `449.l.399828` 2024, `423.l.740979` 2023, `414.l.736361` 2022, `406.l.84455` 2021, `399.l.112777` 2020, `390.l.112947` 2019, `380.l.208647` 2018, `371.l.22301` 2017, `359.l.123809` 2016, `348.l.74399` 2015, `331.l.34421` 2014, `314.l.66686` 2013, `273.l.11224` 2012, `257.l.31534` 2011, `242.l.8015` 2010.

- [ ] **Step 1: Write failing tests for the pure parsing logic**

`tests/test_league_audit.py`:
```python
import pytest

from audit_league_history import parse_settings, renew_to_league_key


def test_renew_pointer_converts_to_league_key():
    assert renew_to_league_key("449_389359") == "449.l.389359"
    assert renew_to_league_key("") is None
    assert renew_to_league_key(None) is None


def test_parse_settings_extracts_qb_slots():
    settings = {
        "name": "NAJEE 'LEFT EYE' HARRIS",
        "season": "2025",
        "num_teams": 12,
        "renew": "449_123456",
        "renewed": "",
        "roster_positions": [
            {"roster_position": {"position": "QB", "count": 2}},
            {"roster_position": {"position": "WR", "count": 3}},
            {"roster_position": {"position": "BN", "count": 8}},
        ],
    }
    row = parse_settings("461.l.326814", settings)
    assert row["qb_slots"] == 2
    assert row["season"] == 2025
    assert row["num_teams"] == 12
    assert row["renew"] == "449_123456"


def test_parse_settings_fails_loud_on_missing_roster():
    with pytest.raises(KeyError):
        parse_settings("461.l.326814", {"name": "x", "season": "2025", "num_teams": 12, "renew": ""})


def test_extract_managers_handles_both_yahoo_shapes():
    from audit_league_history import extract_managers
    teams = {
        "461.t.1": {"managers": [{"manager": {"guid": "ABC123", "nickname": "Sports"}}]},
        "461.t.2": {"managers": {"manager": {"guid": "DEF456", "nickname": "Mike"}}},
    }
    assert extract_managers(teams) == {"ABC123": "Sports", "DEF456": "Mike"}
```

Note: `sys.path` — add `scripts/` via conftest so the script's functions are importable. Append to `tests/conftest.py`:
```python
import sys
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_league_audit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'audit_league_history'`

- [ ] **Step 3: Implement**

`scripts/audit_league_history.py`:
```python
#!/usr/bin/env python3
"""Walk the target league's renew chain; record per-season settings; detect 2QB era;
compare against the legacy-imported LMU chain. Risks R4/R9."""
import json
import time

# 2025 target league (league_rules.md). If the 2026 league exists by run time,
# start from its key instead and the chain will include 2025 automatically.
NAJEE_2025 = "461.l.326814"

# Legacy chain actually imported into Postgres (scripts/import_all_lmu.py)
LEGACY_LMU_KEYS = {
    "461.l.863132", "449.l.389359", "423.l.323988", "414.l.254390", "406.l.205166",
    "399.l.130335", "390.l.523677", "380.l.212373", "371.l.22647", "359.l.427482",
    "348.l.82093", "331.l.534456", "314.l.364382", "273.l.11353", "257.l.117805",
    "242.l.42939", "222.l.231759",
}


def renew_to_league_key(renew: str | None) -> str | None:
    if not renew:
        return None
    game_id, league_id = renew.split("_")
    return f"{game_id}.l.{league_id}"


def parse_settings(league_key: str, settings: dict) -> dict:
    # Load-bearing keys accessed directly: KeyError here = schema drift = stop (fail loud).
    roster = settings["roster_positions"]
    qb_slots = 0
    for slot in roster:
        pos = slot["roster_position"] if "roster_position" in slot else slot
        if pos["position"] == "QB":
            qb_slots += int(pos.get("count", 1))
    return {
        "league_key": league_key,
        "season": int(settings["season"]),
        "league_name": settings["name"],
        "num_teams": int(settings["num_teams"]),
        "renew": settings.get("renew", ""),
        "renewed": settings.get("renewed", ""),
        "qb_slots": qb_slots,
        "roster_positions": roster,
    }


def extract_managers(teams: dict) -> dict:
    """{manager_guid: nickname} from lg.teams(). Handles Yahoo's list-or-dict manager shapes."""
    out = {}
    for _, team in teams.items():
        mgrs = team["managers"]  # KeyError = schema drift = stop (fail loud)
        if isinstance(mgrs, dict):
            mgrs = [mgrs]
        for m in mgrs:
            mm = m["manager"] if "manager" in m else m
            out[mm.get("guid") or f"no-guid:{mm.get('manager_id')}"] = mm.get("nickname", "?")
    return out


def walk_renew_chain(session, start_key: str) -> list[dict]:
    from ffi.yahoo_client import get_league
    rows, key = [], start_key
    while key:
        lg = get_league(session, key)
        settings = lg.settings()
        row = parse_settings(key, settings)
        row["settings_payload"] = settings
        row["managers"] = extract_managers(lg.teams())
        rows.append(row)
        print(f"  {row['season']}: {row['league_name']!r} teams={row['num_teams']} "
              f"QB={row['qb_slots']} managers={len(row['managers'])} key={key}")
        key = renew_to_league_key(row["renew"])
        time.sleep(2)  # Yahoo throttle (R15) — two calls per season (settings + teams)
    return rows


def main():
    from ffi.db import connect
    from ffi.yahoo_client import get_session

    session = get_session()
    print(f"Walking renew chain from {NAJEE_2025} ...")
    rows = walk_renew_chain(session, NAJEE_2025)

    conn = connect()
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(
                """INSERT INTO raw.yahoo_league_settings
                   (league_key, season, league_name, num_teams, renew, renewed, qb_slots,
                    roster_positions, managers, settings_payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (league_key) DO UPDATE SET
                     settings_payload=EXCLUDED.settings_payload, qb_slots=EXCLUDED.qb_slots,
                     num_teams=EXCLUDED.num_teams, managers=EXCLUDED.managers, fetched_at=now()""",
                (r["league_key"], r["season"], r["league_name"], r["num_teams"],
                 r["renew"], r["renewed"], r["qb_slots"],
                 json.dumps(r["roster_positions"]), json.dumps(r["managers"]),
                 json.dumps(r["settings_payload"])),
            )
    conn.commit()

    chain_keys = {r["league_key"] for r in rows}
    print("\n=== AUDIT REPORT ===")
    print(f"Chain length: {len(rows)} seasons ({min(r['season'] for r in rows)}–{max(r['season'] for r in rows)})")
    two_qb_since = [r["season"] for r in rows if r["qb_slots"] >= 2]
    print(f"2QB seasons: {sorted(two_qb_since)}")
    overlap = chain_keys & LEGACY_LMU_KEYS
    print(f"Overlap with legacy-imported LMU chain: {len(overlap)}/{len(LEGACY_LMU_KEYS)}")
    if len(overlap) == 0:
        print("!! DIVERGENCE: the imported 17-year history is a DIFFERENT league than the NAJEE chain.")
        print("!! STOP: report both chains to the user before any tendency mining (risks R4/R9).")
    elif chain_keys != LEGACY_LMU_KEYS:
        print(f"!! PARTIAL overlap. In chain but not imported: {sorted(chain_keys - LEGACY_LMU_KEYS)}")
        print(f"!! Imported but not in chain: {sorted(LEGACY_LMU_KEYS - chain_keys)}")

    # R9: manager-continuity verification — GUIDs are the anchor (nicknames change annually)
    guid_names, guid_seasons = {}, {}
    for r in rows:
        for g, n in r["managers"].items():
            guid_names[g] = n  # latest nickname wins
            guid_seasons.setdefault(g, set()).add(r["season"])
    seasons_all = {r["season"] for r in rows}
    print("\nManager continuity (R9):")
    for g, seasons in sorted(guid_seasons.items(), key=lambda kv: -len(kv[1])):
        missing = sorted(seasons_all - seasons)
        gap = f", MISSING {missing}" if missing else ""
        print(f"  {guid_names[g]!r} ({g[:12]}…): {len(seasons)} seasons "
              f"{min(seasons)}–{max(seasons)}{gap}")
    sports = [g for g, n in guid_names.items() if n.lower() == "sports"]
    if sports:
        for g in sports:
            print(f"  -> user 'Sports' GUID {g}: seasons {sorted(guid_seasons[g])}")
    else:
        print("!! 'Sports' nickname not found in any season — identify the user's GUID manually (R9).")
    core = sum(1 for s in guid_seasons.values() if len(s) >= 10)
    print(f"  GUIDs spanning >=10 seasons: {core} "
          f"(expect ~10 if the 'same core managers' premise holds; 0 means GUIDs broke — STOP, R9)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_league_audit.py -v`
Expected: 3 PASS

- [ ] **Step 5: Run the live audit**

Run: `uv run python scripts/audit_league_history.py`
Expected: per-season lines, then the audit report. **Whatever the divergence outcome, paste the full report back to the user** — this decides whether the imported history is usable (R9) and defines the 2QB era boundary (R4). If the API response shape breaks `parse_settings` with KeyError, print `json.dumps(settings)[:2000]` and report — do not add defensive defaults.

- [ ] **Step 6: Commit**

```bash
git add scripts/audit_league_history.py tests/test_league_audit.py tests/conftest.py
git commit -m "feat: league renew-chain audit — identity check vs legacy import, 2QB era detection"
```

---

### Task 8: Placeholder-player cleanup

**Files:**
- Create: `scripts/fix_placeholder_players.py`

**Interfaces:**
- Consumes: `get_session`, `get_league`; `public.players`, `public.draft_picks`, `public.leagues`.
- Produces: `players.player_name/position/nfl_team` backfilled from Yahoo; a residual report. No schema changes.

**Context (verified):** `import_all_lmu.py:255-258` created every player as `Player {player_key}` with position/team `'TBD'`. Player details must be fetched via a league of the season the player appeared in (old player ids resolve in their own game context). `yahoo_fantasy_api`'s `league.player_details(ids)` accepts a list of numeric player ids (≤25 per call).

- [ ] **Step 1: Implement**

`scripts/fix_placeholder_players.py`:
```python
#!/usr/bin/env python3
"""Backfill real names/positions/teams for placeholder players created by the legacy import."""
import time
from ffi.db import connect
from ffi.yahoo_client import get_session, get_league

BATCH = 25


def numeric_id(player_key: str) -> str:
    # '461.p.12345' -> '12345'; bare '12345' stays as is
    return player_key.split(".p.")[-1]


def main():
    conn = connect()
    session = get_session()
    with conn.cursor() as cur:
        # Group placeholder players by the league they were drafted in (correct game context)
        cur.execute("""
            SELECT DISTINCT l.league_id, p.player_id, p.yahoo_player_id
            FROM players p
            JOIN draft_picks dp ON dp.player_id = p.player_id
            JOIN leagues l ON l.league_id = dp.league_id
            WHERE p.player_name LIKE 'Player %' OR p.position = 'TBD'
            ORDER BY l.league_id
        """)
        rows = cur.fetchall()
    print(f"{len(rows)} placeholder player-league rows to resolve")

    by_league: dict[str, list[tuple[int, str]]] = {}
    for league_id, player_id, ykey in rows:
        by_league.setdefault(league_id, []).append((player_id, ykey))

    fixed, failed = 0, []
    for league_key, players in by_league.items():
        lg = get_league(session, league_key)
        for i in range(0, len(players), BATCH):
            chunk = players[i:i + BATCH]
            ids = [int(numeric_id(k)) for _, k in chunk]
            try:
                details = lg.player_details(ids)
            except Exception as exc:  # fail loud per-chunk, keep going, report at end
                failed.append((league_key, ids, str(exc)))
                time.sleep(2)
                continue
            by_id = {str(d["player_id"]): d for d in details}
            with conn.cursor() as cur:
                for player_id, ykey in chunk:
                    d = by_id.get(numeric_id(ykey))
                    if d is None:
                        failed.append((league_key, ykey, "not in response"))
                        continue
                    cur.execute(
                        "UPDATE players SET player_name=%s, position=%s, nfl_team=%s WHERE player_id=%s",
                        (d["name"]["full"], d.get("primary_position", "TBD"),
                         d.get("editorial_team_abbr", "TBD"), player_id),
                    )
                    fixed += 1
            conn.commit()
            time.sleep(2)  # throttle (R15)

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM players WHERE player_name LIKE 'Player %' OR position='TBD'")
        remaining = cur.fetchone()[0]
    print(f"Fixed: {fixed}. Remaining placeholders: {remaining}. Failures: {len(failed)}")
    for f in failed[:20]:
        print("  FAIL:", f)
    if remaining:
        print("Residual placeholders need manual attention — report count to the user.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Dry-run scale check before the live run**

```bash
psql fantasy_football -c "SELECT count(*) FROM players WHERE player_name LIKE 'Player %' OR position='TBD';"
```
Expected: up to ~3,800. At 25/call + 2s sleep that's ≈ 160 calls / ~6 min per 4,000 — fine. If the count is 0, the legacy `update_player_names.py` already fixed it; skip the live run and just commit.

- [ ] **Step 3: Live run**

Run: `uv run python scripts/fix_placeholder_players.py`
Expected: `Fixed: <thousands>. Remaining placeholders: <small>. Failures: <small>`. A residual is normal (players deleted from Yahoo's DB); report the number. On error 999: stop (script already exits on unhandled), wait 15 min, rerun — it's idempotent.

- [ ] **Step 4: Verify data quality improved**

```bash
psql fantasy_football -c "SELECT position, count(*) FROM players GROUP BY position ORDER BY count(*) DESC LIMIT 8;"
```
Expected: real positions (WR/RB/QB/TE/K/DEF) dominate; TBD is a small tail.

- [ ] **Step 5: Commit**

```bash
git add scripts/fix_placeholder_players.py
git commit -m "feat: backfill placeholder player names/positions from Yahoo per-season leagues"
```

---

### Task 9: 2025 season import — draft results + weekly player stats

**Files:**
- Create: `scripts/import_yahoo_season.py`

**Interfaces:**
- Consumes: `get_session`, `get_league`; `raw.yahoo_player_week`, `raw.yahoo_standings`, `raw.yahoo_matchups`, `raw.yahoo_transactions` (Task 2); `public.draft_picks` / `players` / `leagues` / `teams` (legacy schema).
- Produces: draft picks in `public.draft_picks`; weekly per-player stat lines in `raw.yahoo_player_week`; season outcomes (final standings, weekly team scoreboards, full transaction log) in the three `raw.yahoo_*` outcome tables — the season time-series that connects "what left the draft" to "what finished the year". CLI: `uv run python scripts/import_yahoo_season.py --league-key 461.l.326814 --draft --outcomes --weeks 1-17`.

**Context:** `lg.draft_results()` returns a list of dicts with `pick`, `round`, `team_key`, `player_id` (skip entries without `player_id` — mid/failed drafts). `lg.player_stats(ids, 'week', week=N)` returns per-player dicts of stat name → value including `'total_points'`. Which league key to run for 2025 depends on **Task 7's audit outcome** (NAJEE `461.l.326814` vs LMU `461.l.863132`) — run for the league(s) the user confirms. Player id set per season = drafted players of that league (good-enough coverage for draft-outcome analysis; waiver pickups come in Phase 2 if needed via transactions).

- [ ] **Step 1: Implement**

`scripts/import_yahoo_season.py`:
```python
#!/usr/bin/env python3
"""Import a season's draft results into public.draft_picks and weekly player stats
into raw.yahoo_player_week. Idempotent; throttled (R15)."""
import argparse
import json
import time
from ffi.db import connect
from ffi.yahoo_client import get_session, get_league

BATCH = 25


def import_draft(conn, lg, league_key: str):
    picks = [p for p in lg.draft_results() if "player_id" in p]
    if not picks:
        raise SystemExit(f"No draft results for {league_key} — draft not held yet?")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM draft_picks WHERE league_id=%s", (league_key,))
        if cur.fetchone()[0] > 0:
            print(f"draft_picks already present for {league_key}; skipping (idempotent)")
            return
        s = lg.settings()
        num_teams = s["num_teams"]
        # draft_picks.league_id has an FK to leagues — upsert the league row first
        cur.execute(
            """INSERT INTO leagues (league_id, league_name, season_year, num_teams)
               VALUES (%s,%s,%s,%s) ON CONFLICT (league_id) DO NOTHING""",
            (league_key, s["name"], int(s["season"]), int(num_teams)),
        )
        for p in picks:
            player_key = f"{league_key.split('.l.')[0]}.p.{p['player_id']}"
            cur.execute(
                """INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
                   VALUES (%s, %s, 'TBD', 'TBD') ON CONFLICT (yahoo_player_id) DO NOTHING""",
                (player_key, f"Player {player_key}"),
            )
            cur.execute("SELECT player_id FROM players WHERE yahoo_player_id=%s", (player_key,))
            pid = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO draft_picks (league_id, player_id, round_number, pick_number, overall_pick)
                   VALUES (%s, %s, %s, %s, %s)""",
                (league_key, pid, int(p["round"]),
                 (int(p["pick"]) - 1) % int(num_teams) + 1, int(p["pick"])),
            )
    conn.commit()
    print(f"Imported {len(picks)} draft picks for {league_key} "
          f"(then run fix_placeholder_players.py for any new placeholders)")


def import_weeks(conn, lg, league_key: str, season: int, weeks: list[int]):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT p.yahoo_player_id FROM players p
            JOIN draft_picks dp ON dp.player_id = p.player_id
            WHERE dp.league_id = %s
        """, (league_key,))
        ids = [int(r[0].split(".p.")[-1]) for r in cur.fetchall()]
    print(f"{len(ids)} players, weeks {weeks}")
    for week in weeks:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM raw.yahoo_player_week WHERE league_key=%s AND week=%s",
                (league_key, week),
            )
            if cur.fetchone()[0] > 0:
                print(f"  week {week}: already imported, skipping")
                continue
        for i in range(0, len(ids), BATCH):
            stats = lg.player_stats(ids[i:i + BATCH], "week", week=week)
            with conn.cursor() as cur:
                for s in stats:
                    cur.execute(
                        """INSERT INTO raw.yahoo_player_week
                           (league_key, season, week, yahoo_player_id, total_points, stats)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (league_key, week, yahoo_player_id) DO NOTHING""",
                        (league_key, season, week, str(s["player_id"]),
                         s.get("total_points"), json.dumps(s, default=str)),
                    )
            conn.commit()
            time.sleep(2)  # throttle (R15); on 999 the request raises and we exit loud
        print(f"  week {week}: done")


def import_outcomes(conn, lg, league_key: str, season: int):
    """Final standings + weekly team scoreboards + full transaction log (the season time-series)."""
    # Standings: one call. Parse rank/name minimally; keep the full entry as payload.
    standings = lg.standings()
    with conn.cursor() as cur:
        for i, entry in enumerate(standings):
            cur.execute(
                """INSERT INTO raw.yahoo_standings (league_key, team_key, season, team_name, final_rank, payload)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (league_key, team_key) DO UPDATE SET payload=EXCLUDED.payload, fetched_at=now()""",
                (league_key, entry.get("team_key", f"{league_key}.t.{i+1}"), season,
                 entry.get("name"), int(entry["rank"]) if entry.get("rank") else None,
                 json.dumps(entry, default=str)),
            )
    conn.commit()
    print(f"  standings: {len(standings)} teams")
    time.sleep(2)

    # Weekly scoreboards: one call per week; store raw, parse in Phase 2.
    end_week = int(lg.end_week())
    for week in range(1, end_week + 1):
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM raw.yahoo_matchups WHERE league_key=%s AND week=%s",
                        (league_key, week))
            if cur.fetchone():
                continue
        payload = lg.matchups(week=week)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.yahoo_matchups (league_key, season, week, payload) VALUES (%s,%s,%s,%s)",
                (league_key, season, week, json.dumps(payload, default=str)),
            )
        conn.commit()
        time.sleep(2)
    print(f"  matchups: weeks 1-{end_week}")

    # Transactions: full log (adds/drops/trades). One-ish call; count=999 requests everything.
    txns = lg.transactions("add,drop,trade", 999)
    with conn.cursor() as cur:
        for t in txns:
            cur.execute(
                """INSERT INTO raw.yahoo_transactions (league_key, transaction_key, season, type, ts, payload)
                   VALUES (%s,%s,%s,%s, to_timestamp(%s), %s)
                   ON CONFLICT (league_key, transaction_key) DO NOTHING""",
                (league_key, t["transaction_key"], season, t.get("type"),
                 int(t["timestamp"]) if t.get("timestamp") else None,
                 json.dumps(t, default=str)),
            )
    conn.commit()
    print(f"  transactions: {len(txns)}")
    time.sleep(2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league-key", required=True)
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--outcomes", action="store_true",
                    help="standings + weekly scoreboards + transactions")
    ap.add_argument("--weeks", default=None, help="e.g. 1-17 (per-player stats)")
    args = ap.parse_args()

    conn = connect()
    session = get_session()
    lg = get_league(session, args.league_key)
    season = int(lg.settings()["season"])

    if args.draft:
        import_draft(conn, lg, args.league_key)
    if args.outcomes:
        import_outcomes(conn, lg, args.league_key, season)
    if args.weeks:
        lo, hi = args.weeks.split("-") if "-" in args.weeks else (args.weeks, args.weeks)
        import_weeks(conn, lg, args.league_key, season, list(range(int(lo), int(hi) + 1)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Import the FULL NAJEE chain's drafts + 2025 weekly stats**

The target league's 16-season history was never imported (Task 7 finding), so the draft import runs for the whole chain; weekly stats run for 2025 only in this phase (older seasons deferred — quota discipline, R15):

```bash
for KEY in 461.l.326814 449.l.399828 423.l.740979 414.l.736361 406.l.84455 \
           399.l.112777 390.l.112947 380.l.208647 371.l.22301 359.l.123809 \
           348.l.74399 331.l.34421 314.l.66686 273.l.11224 257.l.31534 242.l.8015; do
  uv run python scripts/import_yahoo_season.py --league-key "$KEY" --draft --outcomes || break
done
uv run python scripts/import_yahoo_season.py --league-key 461.l.326814 --weeks 1-17
uv run python scripts/fix_placeholder_players.py   # resolve placeholders created by these imports
```
Expected: ~16 × (12 teams × ~16-20 rounds) ≈ 3,000–3,800 NAJEE draft picks in `draft_picks`; per season, a standings line (12 teams), a matchups line, and a transactions count; 17 "week N: done" lines for 2025; then a cleanup pass for new placeholder players. Call budget: drafts ~2/season + outcomes ~20/season (standings 1, scoreboards ~17, transactions 1-2) + 2025 player-weeks ~110 ≈ **~460 calls at 2s spacing ≈ 25–30 min total** — run overnight or in one sitting; every piece is resumable/idempotent. This is the full season time-series: draft → every transaction → weekly scores → final standings, for all 16 seasons. (Weekly *lineups* are deliberately NOT pulled — ~3,300 extra calls; roster evolution is reconstructable from draft + transactions, and lineup-level analysis can sample champions later if Phase 2 wants it.)

- [ ] **Step 3: Sanity-check draft→outcome joinability (the phase's payoff)**

```bash
psql fantasy_football -c "
SELECT dp.round_number, count(*) picks, round(avg(ypw.total_points::numeric),1) avg_season_pts
FROM draft_picks dp
JOIN players p ON p.player_id = dp.player_id
JOIN raw.yahoo_player_week ypw ON ypw.yahoo_player_id = split_part(p.yahoo_player_id, '.p.', 2)
     AND ypw.league_key = dp.league_id
WHERE dp.league_id = '<2025_KEY_FROM_AUDIT>'
GROUP BY dp.round_number ORDER BY dp.round_number LIMIT 5;"
```
Expected: rows with nonzero `avg_season_pts` declining by round — proof that draft position now connects to actual outcomes for the first time in this project.

- [ ] **Step 4: Commit**

```bash
git add scripts/import_yahoo_season.py
git commit -m "feat: season importer — draft results + weekly player stats (throttled, resumable)"
```

---

### Task 10: Player ID crosswalk

**Files:**
- Create: `src/ffi/ingest/crosswalk.py`
- Create: `scripts/build_crosswalk.py`
- Test: `tests/test_crosswalk.py`

**Interfaces:**
- Consumes: `nflreadpy.load_ff_playerids()` (dynastyprocess crosswalk: columns include `name, position, team, gsis_id, sleeper_id, yahoo_id, fantasypros_id`); `public.players` (Yahoo ids); `public.player_id_xwalk` (Task 2).
- Produces: populated `public.player_id_xwalk`; `match_report(conn) -> dict` returning `{"total_fantasy_players": int, "matched": int, "unmatched": list[tuple[name, position, yahoo_id]]}`. CLI: `uv run python scripts/build_crosswalk.py`.

- [ ] **Step 1: Write failing test for the matching logic**

`tests/test_crosswalk.py`:
```python
from ffi.ingest.crosswalk import load_xwalk_rows, match_report


def _seed(db):
    with db.cursor() as cur:
        cur.execute("""INSERT INTO public.player_id_xwalk (name, position, gsis_id, sleeper_id, yahoo_id, fantasypros_id)
                       VALUES ('Justin Jefferson','WR','00-0036322','6794','32692','19236')""")
        cur.execute("""INSERT INTO players (yahoo_player_id, player_name, position, nfl_team)
                       VALUES ('449.p.32692','Justin Jefferson','WR','MIN'),
                              ('449.p.99999','Mystery Man','RB','FA')""")
    db.commit()


def test_match_report_flags_unmatched(db):
    _seed(db)
    report = match_report(db)
    assert report["total_fantasy_players"] == 2
    assert report["matched"] == 1
    assert report["unmatched"] == [("Mystery Man", "RB", "99999")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_crosswalk.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/ffi/ingest/crosswalk.py`:
```python
import psycopg2.extras
from ffi.ingest.base import IngestError

FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
XWALK_COLS = ["name", "position", "team", "gsis_id", "sleeper_id", "yahoo_id", "fantasypros_id"]


def load_xwalk_rows(conn) -> int:
    import nflreadpy
    df = nflreadpy.load_ff_playerids()
    missing = set(XWALK_COLS) - set(df.columns)
    if missing:
        raise IngestError(f"ff_playerids missing columns {sorted(missing)}; actual: {sorted(df.columns)[:40]}")
    rows = df.select(XWALK_COLS).rows()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.player_id_xwalk WHERE manual_override = FALSE")
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO public.player_id_xwalk ({', '.join(XWALK_COLS)}) VALUES %s",
            rows, page_size=5000,
        )
    conn.commit()
    return len(rows)


def match_report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.player_name, p.position, split_part(p.yahoo_player_id, '.p.', 2) AS yid,
                   x.xwalk_id
            FROM players p
            LEFT JOIN public.player_id_xwalk x
                   ON x.yahoo_id = split_part(p.yahoo_player_id, '.p.', 2)
            WHERE p.position IN %s
        """, (FANTASY_POSITIONS,))
        rows = cur.fetchall()
    unmatched = [(n, pos, yid) for (n, pos, yid, xid) in rows if xid is None]
    return {
        "total_fantasy_players": len(rows),
        "matched": len(rows) - len(unmatched),
        "unmatched": unmatched,
    }
```

`scripts/build_crosswalk.py`:
```python
#!/usr/bin/env python3
"""Load dynastyprocess/nflverse ff_playerids into public.player_id_xwalk and report match coverage."""
from ffi.db import connect
from ffi.ingest.crosswalk import load_xwalk_rows, match_report

conn = connect()
n = load_xwalk_rows(conn)
print(f"Loaded {n} crosswalk rows")
report = match_report(conn)
pct = 100 * report["matched"] / max(report["total_fantasy_players"], 1)
print(f"Yahoo match coverage: {report['matched']}/{report['total_fantasy_players']} ({pct:.1f}%)")
print(f"Unmatched fantasy-relevant players: {len(report['unmatched'])}")
for name, pos, yid in report["unmatched"][:40]:
    print(f"  UNMATCHED: {name} ({pos}) yahoo_id={yid}")
if pct < 90:
    raise SystemExit("Coverage <90% — investigate before Phase 2 (risk R6). Historical/retired "
                     "players may legitimately be absent; current-season misses are the concern.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_crosswalk.py -v`
Expected: PASS

- [ ] **Step 5: Live build + report**

Run: `uv run python scripts/build_crosswalk.py`
Expected: thousands of rows loaded; coverage % printed. **Paste the unmatched list into the session** — 2026 rookies and old retired players will dominate it; rookies get manual-override rows (`manual_override=TRUE`) in Phase 2 when FP/Sleeper rookie ids land (R6).

- [ ] **Step 6: Commit**

```bash
git add src/ffi/ingest/crosswalk.py scripts/build_crosswalk.py tests/test_crosswalk.py
git commit -m "feat: player ID crosswalk from ff_playerids with fail-loud coverage report"
```

---

### Task 11: FantasyPros API key application + runbook (user-in-the-loop)

**Files:**
- Create: `docs/runbooks/fantasypros-api.md`

**Interfaces:**
- Produces: documented application steps + call-budget policy; `.env` gains `FANTASYPROS_API_KEY=` once approved (user action). The FP client itself is Phase 2 (blocked on key approval — R12).

- [ ] **Step 1: Write the runbook**

`docs/runbooks/fantasypros-api.md`:
```markdown
# FantasyPros Public API — key application & usage policy

## Apply (user action, do this on day 1 — approval is discretionary and takes unknown time, risk R12)
1. Log into your FantasyPros account in a browser.
2. Go to https://secure.fantasypros.com/api-keys/request/
3. Describe intended use as: personal, non-commercial fantasy football research for
   your own league; daily cached sync of projections/rankings/ADP; ~20 calls/day.
4. When the key arrives, add to `.env`: `FANTASYPROS_API_KEY=<key>` (never commit).

## Hard limits (ToS, verified 2026-07-08)
- 1 call/second, 100 calls/day. Personal, non-commercial. No redistribution.
- Historical player statistics are explicitly NOT licensed — never store them from FP
  (we use nflverse for historicals anyway).

## Call budget (ADR Domain 6)
- One daily sync ≤ 30 calls: projections (QB/RB/WR/TE/K/DST × draft), consensus-rankings
  (superflex + positional), ADP. Everything else reads the local cache in `raw`.
- Ad-hoc queries NEVER hit the API directly.

## Fallback if key is denied/delayed (R12)
- `ffpros`-style authenticated page parsing / `?export=xls` with session cookie.
- Sleeper remains the projection backbone either way; FP is the consensus overlay.
```

- [ ] **Step 2: Tell the user to submit the application (blocking item, their login)**

Message the user: the form at `secure.fantasypros.com/api-keys/request/` needs their logged-in account; the runbook has the suggested wording.

- [ ] **Step 3: Commit**

```bash
git add docs/runbooks/fantasypros-api.md
git commit -m "docs: FantasyPros API key application runbook and call-budget policy"
```

---

### Task 12: Phase 1 verification sweep + data-quality report

**Files:**
- Create: `scripts/phase1_report.py`

**Interfaces:**
- Consumes: everything above.
- Produces: a single console report proving Phase 1's exit criteria; this doubles as the seed of the morning briefing's health header (ADR Domain 5).

- [ ] **Step 1: Implement**

`scripts/phase1_report.py`:
```python
#!/usr/bin/env python3
"""Phase 1 exit-criteria report. Every section must print OK (or an explained SKIP) before Phase 2."""
from ffi.db import connect

CHECKS = [
    ("legacy LMU draft history intact",
     "SELECT count(*) >= 3700 FROM draft_picks"),
    ("NAJEE chain drafts imported (>=3000 picks across audited seasons)",
     """SELECT count(*) >= 3000 FROM draft_picks dp
        JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id"""),
    ("NAJEE season outcomes imported (standings for >=14 seasons)",
     "SELECT count(DISTINCT league_key) >= 14 FROM raw.yahoo_standings"),
    ("NAJEE transaction log imported (>=14 seasons)",
     "SELECT count(DISTINCT league_key) >= 14 FROM raw.yahoo_transactions"),
    ("placeholder players cleaned (<5% remain)",
     """SELECT (count(*) FILTER (WHERE player_name LIKE 'Player %'))::float
        / greatest(count(*),1) < 0.05 FROM players"""),
    ("league settings audit populated",
     "SELECT count(*) >= 10 FROM raw.yahoo_league_settings"),
    ("2QB era boundary known",
     "SELECT count(*) >= 1 FROM raw.yahoo_league_settings WHERE qb_slots >= 2"),
    ("sleeper snapshot present",
     "SELECT count(*) >= 1 FROM raw.sleeper_projections"),
    ("nflverse actuals loaded w/ first downs",
     "SELECT sum(rushing_first_downs) > 0 FROM raw.nflverse_player_week"),
    ("2025 weekly stats imported",
     "SELECT count(DISTINCT week) >= 17 FROM raw.yahoo_player_week WHERE season = 2025"),
    ("crosswalk loaded",
     "SELECT count(*) > 5000 FROM public.player_id_xwalk"),
    ("no failed ingest runs in last 24h",
     """SELECT count(*) = 0 FROM raw.ingest_runs
        WHERE status='failed' AND started_at > now() - interval '24 hours'"""),
]

conn = connect()
failures = 0
for label, sql in CHECKS:
    with conn.cursor() as cur:
        cur.execute(sql)
        ok = bool(cur.fetchone()[0])
    print(f"{'OK  ' if ok else 'FAIL'} {label}")
    failures += (not ok)
raise SystemExit(failures)
```

- [ ] **Step 2: Run it**

Run: `uv run python scripts/phase1_report.py`
Expected: all `OK` (exit 0). Any `FAIL` is worked before Phase 2 planning starts — this is the week-1 exit gate feeding the R3 week-3 checkpoint.

- [ ] **Step 3: Run the full test suite one last time**

Run: `uv run pytest -v`
Expected: all tests pass.

- [ ] **Step 4: Commit + backup**

```bash
git add scripts/phase1_report.py
git commit -m "feat: phase 1 exit-criteria report"
./scripts/backup_db.sh
```

---

## Self-review notes

- **Spec coverage (Phase-1 scope):** Postgres revival + backup (Task 2), data-quality audit incl. placeholder cleanup (Tasks 7, 8, 12), renew-chain/settings/2QB audit (Task 7), 2025 season + weekly stats (Task 9), Sleeper (Task 4), nflverse (Task 5), crosswalk (Task 10), FP key application (Task 11), fail-loud ingestion + run records (Task 3), gitignore browser profiles (Task 1). Full Yahoo *stat-modifier* mapping and golden-test fixtures are **Phase 2** (scoring engine) — `raw.yahoo_league_settings.settings_payload` already captures each season's stat modifiers wholesale, so nothing is lost.
- **Known live-API risk:** exact response shapes for `lg.settings()`, `player_details()`, `player_stats()`, and Sleeper season-level projections may differ in detail; every parser fails loud with the raw payload rather than adapting silently, and steps say what to report back.
- **Historical weekly stats for pre-2025 seasons** are deliberately deferred (Task 9 imports the full NAJEE chain's *drafts* — cheap, 1–2 calls/season — but the 16-season *weekly-stats* crawl is a Phase 2/3 decision once era boundaries are known; R15).
- **League-identity resolution (2026-07-09 probe):** the NAJEE chain (16 seasons, 2010–2025, 12 teams, renamed annually) is the target league's true history and drives Tasks 7/9; the legacy LMU import (14-team league) remains in `public` untouched, demoted to secondary reference. Task 12's "17y draft history intact" check now reads as LMU baseline + NAJEE chain imported (≥3,000 NAJEE picks expected).
