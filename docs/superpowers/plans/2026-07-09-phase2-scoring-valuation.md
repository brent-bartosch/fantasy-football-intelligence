# Phase 2: Scoring Engine, Valuation & Historical Mining — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A golden-tested scoring engine that exactly reproduces Yahoo's official 2025 points, the R16 methodology layer (FD imputation, distribution-priced bonuses, computed 2QB baselines) with its validation reports, a valuation layer (VORP + GMM tiers + uncertainty), the 16-season historical mining report, and morning-briefing v1 plumbing.

**Architecture:** Pure-function scoring core (`score_stat_line(stat_line, config) → Decimal`) fed by per-source adapters (Yahoo / nflverse / Sleeper / FantasyPros) that fail loud on schema drift. Versioned scoring config lives in committed JSON + `scoring.config` table (immutable rows). Derived layers write to `scoring.*` / `valuation.*` schemas and are always recomputable from `raw` + config (ADR Domain 2). All Yahoo calls go through one throttle wrapper (mandated first commit).

**Tech Stack:** Python ≥3.11 (uv), psycopg2, pydantic v2, structlog, polars, pytest + hypothesis; NEW dependencies this phase: `scipy` (gamma CDF for bonus pricing), `scikit-learn` (GMM tiers).

## Global Constraints

- **Fail-loud (ADR Domain 1):** Level 3 (clear error) is the default everywhere. No `.get(k, default)` for load-bearing fields; unknown payload keys = hard `IngestError`; exceptions never swallowed; scripts exit nonzero on failure. All try/except written under the fail-loud-error-handling skill.
- **Yahoo throttle (R15/R2):** every Yahoo API call goes through `ffi.yahoo_client.yahoo_call()` (≥2s spacing). Error 999 = `YahooRateLimitError` = 10–15 min lockout: **never retry, stop all Yahoo work**. No bulk Yahoo jobs within 24h of any rehearsal or live work.
- **FantasyPros budget (ADR Domain 6):** ≤30 calls/day, enforced in code by a hard budget guard (abort, not degrade); 1.1s between calls; never store FP historical player stats (ToS). Runbook: `docs/runbooks/fantasypros-api.md`.
- **Scoring config versioned & immutable (ADR Domain 2/8):** rules live in `config/scoring/vN.json` + `scoring.config`; a change = new version, never an edit.
- **Golden gate (R1, ADR Domain 7):** the engine must exactly match Yahoo's official 2025 points (committed fixtures + full-2025 sweep) **before any task that builds on it** (Tasks 6+). Week-3 checkpoint (R3): golden tests green by end of week 3 or escalate to the user.
- **R16 discipline:** FD divergence >15% vs Sleeper native = investigate, never silently prefer either source. Calibration and sensitivity reports are deliverables, not nice-to-haves.
- **Stack:** `uv` only (`uv run pytest`, `uv sync`). Postgres 15 via brew. Test DB `fantasy_football_test` self-bootstraps via conftest; tests never touch the real DB.
- **Legacy `scripts/`** from the 2025 codebase (import_all_lmu.py, draft_assistant.py, scoring_adjuster.py, rss_ingester.py, etc.) remain read-only reference. Phase-1 scripts (import_yahoo_season.py, fix_placeholder_players.py, build_crosswalk.py, phase1_report.py, ingest_*.py) ARE maintained code and get refactored here.
- **Health gate:** `uv run python scripts/phase1_report.py` must stay green; Task 15 extends it with Phase 2 checks (never replaces it).
- **2026 renewal trigger (R8, standing):** if the 2026 NAJEE league appears (`renewed` ≠ '') at any point during this phase, immediately run `scripts/audit_league_history.py` from the new league key and diff settings vs 2025 before continuing valuation work.

## Pending user inputs (none block execution; fold in when they arrive)

1. **Manager slot-turnover annotation** — Task 3 creates the table + seeds the one known fact (slot 12 = Brent, ~2022). Task 14 segments by it where annotated and ships slot-level results with an explicit caveat where not.
2. **QB cohort reference material** — folds into Task 11 (tiers). GMM proceeds without it.
3. **2026 draft date** — planning assumption stays mid-August.
4. **2026 league renewal** — see standing trigger above.

## Established facts (verified live 2026-07-09 — do NOT re-derive, do NOT contradict)

These were measured against the real database and live payloads while writing this plan:

1. **Yahoo stat payloads use display-name keys**, not stat IDs. Offense sample (`raw.yahoo_player_week.stats`): `{"Comp","Inc","Pass Yds","Pass TD","Int","Pick Six","Rush Att","Rush Yds","Rush TD","Rush 1st Downs","Rec","Rec Yds","Rec TD","Rec 1st Downs","Ret Yds","Ret TD","2-PT","Fum","Fum Lost","Fum Ret TD","Targets","name","player_id","total_points","position_type"}` with `position_type` = `"O"`.
2. **K payloads** (`position_type":"K"`): `FG 0-19 / 20-29 / 30-39 / 40-49 / FG 50+` (made), `FGM 0-19 / 20-29 / 30-39` (missed — only ≤39 tiers exist, matching the rules' miss penalties), `PAT Made`, `PAT Miss`.
3. **DEF payloads** (`position_type":"DT"`): raw values `Pts Allow`, `Def Yds Allow` PLUS one-hot tier indicator fields (`"Pts Allow 21-27": 1.0`, `"Yds Allow 300-399": 1.0`, etc.) and count stats `Sack, Int, Fum Rec, TD, Safe, Blk Kick, 4 Dwn Stops, TFL, 3 and Outs, XPR`. Engine computes tiers from raw values; the indicators are a free cross-check.
4. **Bonus stacking is CUMULATIVE** — verified arithmetically against official totals: 200+ yd games carry a +12 residual (3+4+5), 150–199 games +7 (3+4), Russell Wilson's 450-yd passing game +7 (300+ & 400+). Encode bonuses as "award every tier whose threshold is met."
5. **Base weights verified exactly** (integer bonus residuals across all bands): 0.5/−0.5 Comp/Inc, 0.04/pass yd, 6 pass TD, −2 INT, −4 pick six, 0.33/rush att, 0.1/rush yd, +1 FD, full PPR, 0.1/rec yd, 6 TDs, 0.1/ret yd, 2/2-PT, −1 Fum, −2 Fum Lost, +6 Fum Ret TD. Example golden row: Zay Flowers wk16-style line 7 rec/143 yds/1 TD/5 FD/2 att/8 rush yds = 36.76.
6. **`players.yahoo_player_id` is NOT unique per NFL player**: the same numeric id appears under multiple game-code prefixes (`461.p.40039`, `449.p.40039`, …) — one row per season the player was drafted. **Every join on the numeric id must dedupe** (use the `v_player_yahoo_ids` view Task 3 creates). This fan-out is why a naive position-count join returns ~19k rows instead of 3,876.
7. **`draft_picks.team_id` is NULL for all 3,720 NAJEE picks and `teams` has 0 NAJEE rows.** Slot-tendency mining requires the Task 13 backfill: `teams` rows come free from `raw.yahoo_standings` payloads (`team_key`, `name`, `rank`, `playoff_seed`, `points_for` all present); pick→team assignment needs one `lg.draft_results()` re-fetch per season (16 throttled calls) because Phase 1 discarded `team_key`.
8. **Sleeper snapshots on hand are week-level only** (2025 wk5: 3,288 recs; 2026 wk1: 3,292). The season-level path (`--week` omitted) is **untested** and Task 7 must test it live before anything relies on it. Sleeper stat keys (QB sample): `pass_cmp, pass_inc, pass_att, pass_yd, pass_td, pass_int, pass_int_td, pass_fd, pass_2pt, rush_att, rush_yd, rush_td, rush_fd, rush_2pt, rec, rec_yd, rec_td, rec_fd, rec_2pt, fum, fum_lost` + noise keys (`pts_ppr, adp_dd_ppr, cmp_pct, gp, rec_0_4, …`). `pass_int_td` = pick-sixes thrown.
9. **2025 Yahoo data:** 228 distinct players × 17 weeks = 3,876 rows in `raw.yahoo_player_week` (drafted players only — includes ~12-16 DEF and Ks, NOT all 32; Task 12 verifies coverage and backfills what the streaming check needs).
10. **Throttle sites to replace** (`time.sleep(2)`): import_yahoo_season.py:114,139,158,184; fix_placeholder_players.py:53,74. (yahoo_auth.py:51 is a server-start sleep — leave. import_all_lmu.py is legacy — leave.)
11. **63 residual placeholder players** (`player_name LIKE 'Player %' OR position='TBD'`), none drafted in the NAJEE chain; **85 legacy slug-format rows** (`nfl.p.<name>`) are duplicates of numeric-id rows.
12. **FP key live** in `.env` as `FANTASYPROS_API_KEY` (verified 2026-07-09). Exact v2 endpoint params for superflex ECR are UNVERIFIED — Task 10 probes before committing code to param names.

## Task sequence & review discipline

Execution = subagent-driven with per-task review gates (same as Phase 1); ledger at `.superpowers/sdd/phase2-progress.md`. Work on branch `phase2-scoring-valuation`; merge via finishing-a-development-branch.

| # | Task | Depends on | Week target |
|---|------|-----------|-------------|
| 1 | ffi.ids + Yahoo throttle wrapper + nflverse column map (**mandated first commit**) | — | 2 |
| 2 | Data hygiene: slug cleanup, crosswalk dup-guard, rookie overrides | 1 | 2 |
| 3 | Migration 002 + conftest + scoring config v1 | 1 | 2 |
| 4 | Scoring engine core (pure function + property tests) | 3 | 2 |
| 5 | Yahoo adapter + golden fixtures + full-2025 sweep (**week-3 checkpoint artifact**) | 4 | 2–3 |
| 6 | nflverse widening + adapter + historical scoring + divergence audit | 5 | 3 |
| 7 | Sleeper adapter + season-level path + projection scoring | 5 | 3 |
| 8 | First-down imputation + divergence report (R16) | 6, 7 | 3 |
| 9 | Threshold-bonus distribution pricing + calibration report (R16) | 6 | 3 |
| 10 | FantasyPros ingestion (budget-guarded) | 1 | 3 |
| 11 | Valuation: computed 2QB baseline + VORP + GMM tiers + sensitivity (R16) | 7–10 | 3 |
| 12 | DEF/K streaming-baseline check (explicit deliverable) | 5, 6 | 3 |
| 13 | History data prep: draft-team backfill, matchup parsing, outcome skip-guards | 1, 3 | 3 |
| 14 | Historical mining report (user-facing deliverable) | 6, 13 | 3 |
| 15 | Morning briefing v1 + launchd + health-report extension | 10 | 3 |
| 16 | pg_restore drill (ADR Domain 8, due week 3) | — | 3 |

---

### Task 1: `ffi.ids` + Yahoo throttle wrapper + nflverse column map (mandated first commit)

Phase 1 final review made this the required first commit: one home for Yahoo key parsing (currently `numeric_id()` in fix_placeholder_players.py, inline `.split(".p.")`/`.split('.l.')` in import_yahoo_season.py, SQL `split_part` ×4 in crosswalk.py), one throttle wrapper (replaces 6 hand-rolled `time.sleep(2)` sites), and one nflverse source→DB column mapping (replaces 4 duplicated lists). Also folds in the T6 deferred Minor: wrap raw `JSONDecodeError`/network exceptions from token refresh in actionable `YahooAuthError`s.

**Files:**
- Create: `src/ffi/ids.py`
- Create: `tests/test_ids.py`
- Create: `tests/test_yahoo_client.py`
- Modify: `src/ffi/yahoo_client.py`
- Modify: `src/ffi/ingest/nflverse.py` (derive the 4 lists from one map)
- Modify: `src/ffi/ingest/crosswalk.py` (SQL fragments from ffi.ids)
- Modify: `scripts/import_yahoo_season.py`, `scripts/fix_placeholder_players.py` (use ids + yahoo_call)

**Interfaces:**
- Produces: `ffi.ids.player_numeric_id(key: str) -> str`, `ffi.ids.is_numeric_player_key(key: str) -> bool`, `ffi.ids.player_key(game_code, player_id) -> str`, `ffi.ids.league_game_code(league_key: str) -> str`, `ffi.ids.team_slot(team_key: str) -> int`, `ffi.ids.normalize_team_abbr(abbr: str) -> str`, `ffi.ids.yahoo_numeric_id_sql(col: str) -> str`, `ffi.ids.yahoo_numeric_id_filter_sql(col: str) -> str`, `ffi.ids.IdParseError`, `ffi.ids.NFL_TEAMS`
- Produces: `ffi.yahoo_client.yahoo_call(fn, *args, **kwargs)`, `ffi.yahoo_client.YahooRateLimitError`
- Produces: `ffi.ingest.nflverse.COLUMN_MAP: list[tuple[str, str]]`, `ffi.ingest.nflverse.DERIVED_SUMS: dict[str, list[str]]` (Task 6 extends these)

- [ ] **Step 1: Write failing tests for ffi.ids**

`tests/test_ids.py`:

```python
import pytest
from ffi.ids import (
    IdParseError,
    is_numeric_player_key,
    league_game_code,
    normalize_team_abbr,
    player_key,
    player_numeric_id,
    team_slot,
    yahoo_numeric_id_filter_sql,
    yahoo_numeric_id_sql,
)


def test_player_numeric_id_from_full_key():
    assert player_numeric_id("461.p.40039") == "40039"


def test_player_numeric_id_bare_passthrough():
    assert player_numeric_id("40039") == "40039"


def test_player_numeric_id_rejects_legacy_slug():
    with pytest.raises(IdParseError):
        player_numeric_id("nfl.p.patrick_mahomes")


def test_player_numeric_id_rejects_league_key():
    with pytest.raises(IdParseError):
        player_numeric_id("461.l.326814")


def test_is_numeric_player_key():
    assert is_numeric_player_key("461.p.40039")
    assert is_numeric_player_key("40039")
    assert not is_numeric_player_key("nfl.p.patrick_mahomes")


def test_player_key_roundtrip():
    assert player_key("461", 40039) == "461.p.40039"
    assert player_numeric_id(player_key("461", 40039)) == "40039"


def test_league_game_code():
    assert league_game_code("461.l.326814") == "461"
    with pytest.raises(IdParseError):
        league_game_code("461.p.40039")


def test_team_slot():
    assert team_slot("461.l.326814.t.7") == 7
    with pytest.raises(IdParseError):
        team_slot("461.l.326814")


def test_normalize_team_abbr_yahoo_mixed_case():
    assert normalize_team_abbr("Buf") == "BUF"
    assert normalize_team_abbr("Was") == "WAS"
    assert normalize_team_abbr("Jax") == "JAX"


def test_normalize_team_abbr_aliases():
    assert normalize_team_abbr("JAC") == "JAX"
    assert normalize_team_abbr("LAR") == "LA"
    assert normalize_team_abbr("WSH") == "WAS"


def test_normalize_team_abbr_unknown_fails_loud():
    with pytest.raises(IdParseError):
        normalize_team_abbr("XYZ")


def test_sql_fragments():
    assert yahoo_numeric_id_sql("p.yahoo_player_id") == "split_part(p.yahoo_player_id, '.p.', 2)"
    assert "~ '^[0-9]+$'" in yahoo_numeric_id_filter_sql("yahoo_player_id")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ids.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ffi.ids'`

- [ ] **Step 3: Implement `src/ffi/ids.py`**

```python
"""Single home for Yahoo key parsing and NFL team-abbr normalization.

Yahoo key shapes:
  player: '{game}.p.{player_id}'   e.g. '461.p.40039'
  league: '{game}.l.{league_id}'   e.g. '461.l.326814'
  team:   '{game}.l.{league_id}.t.{slot}' e.g. '461.l.326814.t.7'

Game code changes every season (461=2025, 449=2024, ...), so the same NFL
player appears under multiple player keys — see v_player_yahoo_ids.
"""
import re


class IdParseError(ValueError):
    """A Yahoo key or team abbreviation didn't match the expected shape. Never guess."""


_PLAYER_KEY = re.compile(r"^(\d+)\.p\.(\d+)$")
_LEAGUE_KEY = re.compile(r"^(\d+)\.l\.(\d+)$")
_TEAM_KEY = re.compile(r"^(\d+)\.l\.(\d+)\.t\.(\d+)$")


def player_numeric_id(key: str) -> str:
    """'461.p.40039' -> '40039'. Bare numeric ids pass through.
    Legacy slug keys ('nfl.p.patrick_mahomes') raise IdParseError."""
    if key.isdigit():
        return key
    m = _PLAYER_KEY.match(key)
    if not m:
        raise IdParseError(f"not a numeric Yahoo player key: {key!r}")
    return m.group(2)


def is_numeric_player_key(key: str) -> bool:
    return key.isdigit() or _PLAYER_KEY.match(key) is not None


def player_key(game_code: str | int, player_id: str | int) -> str:
    return f"{game_code}.p.{player_id}"


def league_game_code(league_key: str) -> str:
    m = _LEAGUE_KEY.match(league_key)
    if not m:
        raise IdParseError(f"not a Yahoo league key: {league_key!r}")
    return m.group(1)


def team_slot(team_key: str) -> int:
    """'461.l.326814.t.7' -> 7. The slot is the stable per-season team number
    (manager identity anchor — see PROJECT-RECORD 13b)."""
    m = _TEAM_KEY.match(team_key)
    if not m:
        raise IdParseError(f"not a Yahoo team key: {team_key!r}")
    return int(m.group(3))


# The ONE definition of "numeric yahoo id" in SQL. Keep in sync with
# player_numeric_id above.
def yahoo_numeric_id_sql(col: str) -> str:
    return f"split_part({col}, '.p.', 2)"


def yahoo_numeric_id_filter_sql(col: str) -> str:
    return f"split_part({col}, '.p.', 2) ~ '^[0-9]+$'"


# Canonical = nflverse uppercase abbreviations (2025 franchises).
NFL_TEAMS = frozenset({
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
})
# Yahoo/other-source spellings and relocated-franchise codes. NOTE: OAK/SD/STL
# map to the current franchise — fine for 2020+ joins; do not use this for
# pre-relocation era analysis.
_ALIASES = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LA"}


def normalize_team_abbr(abbr: str) -> str:
    """'Buf' -> 'BUF', 'JAC' -> 'JAX'. Unknown abbreviations fail loud."""
    up = abbr.strip().upper()
    up = _ALIASES.get(up, up)
    if up not in NFL_TEAMS:
        raise IdParseError(f"unknown NFL team abbreviation: {abbr!r}")
    return up
```

- [ ] **Step 4: Run ids tests**

Run: `uv run pytest tests/test_ids.py -q`
Expected: all PASS

- [ ] **Step 5: Write failing tests for the throttle wrapper + auth hardening**

`tests/test_yahoo_client.py`:

```python
import json

import pytest
import requests

import ffi.yahoo_client as yc


def test_yahoo_call_enforces_spacing(monkeypatch):
    sleeps = []
    clock = {"t": 100.0}
    monkeypatch.setattr(yc.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(yc.time, "sleep", lambda s: sleeps.append(s))
    yc._last_call_monotonic = 0.0

    yc.yahoo_call(lambda: "a")          # first call: 100 - 0 > 2s, no sleep
    assert sleeps == []
    clock["t"] = 100.5                   # 0.5s after last call
    yc.yahoo_call(lambda: "b")
    assert len(sleeps) == 1 and sleeps[0] == pytest.approx(1.5)


def test_yahoo_call_converts_999_to_rate_limit_error():
    def boom():
        raise RuntimeError("HTTP 999 Request denied")

    with pytest.raises(yc.YahooRateLimitError) as ei:
        yc.yahoo_call(boom)
    assert "lockout" in str(ei.value)


def test_yahoo_call_passes_through_other_errors():
    def boom():
        raise ValueError("something else")

    with pytest.raises(ValueError):
        yc.yahoo_call(boom)


def test_yahoo_call_returns_result():
    assert yc.yahoo_call(lambda x: x * 2, 21) == 42


def test_get_session_corrupted_oauth_file(monkeypatch, tmp_path):
    bad = tmp_path / "yahoo_oauth.json"
    bad.write_text("{not json")
    monkeypatch.setattr(yc, "OAUTH_FILE", bad)
    with pytest.raises(yc.YahooAuthError) as ei:
        yc.get_session()
    assert "corrupted" in str(ei.value)
```

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_yahoo_client.py -q`
Expected: FAIL — `AttributeError: module 'ffi.yahoo_client' has no attribute 'yahoo_call'` (and the corrupted-file test fails because the raw `json.JSONDecodeError` escapes)

- [ ] **Step 7: Extend `src/ffi/yahoo_client.py`**

Add `import time` and `import requests` to the imports, then append/modify:

```python
YAHOO_MIN_INTERVAL_S = 2.0
_last_call_monotonic = 0.0


class YahooRateLimitError(Exception):
    """Yahoo error 999 = IP lockout for ~10-15 minutes. Never retry; stop ALL
    Yahoo API work and re-run after cooldown (risk R15/R2, ADR Domain 1)."""


def yahoo_call(fn, *args, **kwargs):
    """Every Yahoo API call goes through here: enforces >=2s spacing between
    calls (R15) and converts error-999 responses into YahooRateLimitError.
    No retries by design (ADR Domain 1: a retry against 999 only extends the
    lockout)."""
    global _last_call_monotonic
    wait = YAHOO_MIN_INTERVAL_S - (time.monotonic() - _last_call_monotonic)
    if wait > 0:
        time.sleep(wait)
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        # yahoo_fantasy_api surfaces 999 as RuntimeError text containing the
        # status code. A substring false-positive would only rename one loud
        # error to a scarier loud error — acceptable.
        if "999" in str(exc):
            raise YahooRateLimitError(
                "Yahoo returned error 999 (rate-limit lockout, ~10-15 min). "
                f"Stop all Yahoo API work now; re-run after cooldown. Original: {exc}"
            ) from exc
        raise
    finally:
        _last_call_monotonic = time.monotonic()
```

And harden `get_session()` (T6 deferred Minor — actionable errors for the 2026 renewal re-audit):

```python
def get_session():
    from yahoo_oauth import OAuth2

    if not OAUTH_FILE.exists():
        _build_oauth_file()
    try:
        sc = OAuth2(None, None, from_file=str(OAUTH_FILE))
    except json.JSONDecodeError as exc:
        raise YahooAuthError(
            f"{OAUTH_FILE} is corrupted (invalid JSON: {exc}). Delete it and "
            f"re-run — it is rebuilt from .env + {LEGACY_TOKEN}."
        ) from exc
    if not sc.token_is_valid():
        try:
            sc.refresh_access_token()
        except requests.RequestException as exc:
            raise YahooAuthError(
                f"Network failure refreshing the Yahoo token: {exc}. "
                "Check connectivity and retry; the token file was not changed."
            ) from exc
    if not sc.token_is_valid():
        raise YahooAuthError(
            "Yahoo token refresh failed — refresh token likely revoked. "
            "Run: python scripts/yahoo_manual_auth.py, delete config/yahoo_oauth.json, retry."
        )
    return sc
```

`import requests` at module top (it is already a project dependency).

- [ ] **Step 8: Run yahoo_client tests**

Run: `uv run pytest tests/test_yahoo_client.py -q`
Expected: all PASS

- [ ] **Step 9: Consolidate the nflverse column mapping**

In `src/ffi/ingest/nflverse.py`, replace the four hand-maintained lists (`REQUIRED_COLS`, `_DB_COLS`, `_STAT_COLS`, `ordered_src`) with derivations from one structure. The mapping below is exactly the current column set — **behavior-preserving refactor, insert order unchanged**:

```python
# ONE source→DB mapping. REQUIRED_COLS, insert order, and stat columns all
# derive from this — extend HERE (and the migration) when adding columns.
COLUMN_MAP: list[tuple[str, str]] = [
    ("player_id", "gsis_id"),
    ("season", "season"),
    ("week", "week"),
    ("player_display_name", "player_name"),
    ("position", "position"),
    ("team", "team"),
    ("completions", "completions"),
    ("attempts", "attempts"),
    ("passing_yards", "passing_yards"),
    ("passing_tds", "passing_tds"),
    ("passing_first_downs", "passing_first_downs"),
    ("passing_interceptions", "interceptions"),
    ("carries", "carries"),
    ("rushing_yards", "rushing_yards"),
    ("rushing_tds", "rushing_tds"),
    ("rushing_first_downs", "rushing_first_downs"),
    ("receptions", "receptions"),
    ("targets", "targets"),
    ("receiving_yards", "receiving_yards"),
    ("receiving_tds", "receiving_tds"),
    ("receiving_first_downs", "receiving_first_downs"),
    ("punt_return_yards", "punt_return_yards"),
    ("kickoff_return_yards", "kickoff_return_yards"),
]
# db_col -> source columns summed (fill_null(0) inside the sum only).
DERIVED_SUMS: dict[str, list[str]] = {
    "fumbles_lost": ["rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"],
}

_IDENTITY_SRC = {"player_id", "season", "week", "player_display_name", "position", "team"}
REQUIRED_COLS = {src for src, _ in COLUMN_MAP} | {
    c for cols in DERIVED_SUMS.values() for c in cols
}
_DB_COLS = [db for _, db in COLUMN_MAP] + list(DERIVED_SUMS)
_STAT_COLS = [src for src, _ in COLUMN_MAP if src not in _IDENTITY_SRC] + [
    c for cols in DERIVED_SUMS.values() for c in cols
]
```

Rewrite `_derive_rows` to build the derived columns and select from the map:

```python
    def _derive_rows(self, df: pl.DataFrame) -> list[tuple]:
        """Pure row derivation: null-id guard, derived sums, insert order."""
        null_id = df.filter(pl.col("player_id").is_null())
        if null_id.height:
            nonzero = null_id.filter(
                pl.sum_horizontal(pl.col(c).fill_null(0).abs() for c in self._STAT_COLS)
                > 0
            )
            if nonzero.height:
                raise IngestError(
                    f"nflverse: {nonzero.height} rows have null player_id but "
                    f"nonzero stats — refusing to drop or load them."
                )
            df = df.filter(pl.col("player_id").is_not_null())
        for db_col, src_cols in DERIVED_SUMS.items():
            df = df.with_columns(
                sum((pl.col(c).fill_null(0) for c in src_cols), pl.lit(0)).alias(db_col)
            )
        ordered_src = [src for src, _ in COLUMN_MAP] + list(DERIVED_SUMS)
        return df.select(ordered_src).rows()
```

(Keep `_STAT_COLS` as a class attribute alias `_STAT_COLS = _STAT_COLS` or reference the module-level name — implementer's choice, but the existing tests must pass unchanged.)

- [ ] **Step 10: Run the existing nflverse tests (behavior-preserving check)**

Run: `uv run pytest tests/test_nflverse_ingest.py -q`
Expected: all PASS with no test edits

- [ ] **Step 11: Point call sites at the consolidated helpers**

- `scripts/fix_placeholder_players.py`: delete the local `numeric_id()`; `from ffi.ids import player_numeric_id` and use it (two call sites). Wrap the two API calls: `details = yahoo_call(lg.player_details, ids)` (import from ffi.yahoo_client) and **delete both `time.sleep(2)` lines** (lines 53, 74 — the wrapper owns spacing now; the sleep in the failure branch is also covered because the next yahoo_call waits).
- `scripts/import_yahoo_season.py`: replace `league_key.split('.l.')[0]` with `league_game_code(league_key)` + `player_key(...)` for building keys; replace `r[0].split(".p.")[-1]` with `player_numeric_id(r[0])`; wrap `lg.draft_results()`, `lg.settings()`, `lg.player_stats(...)`, `lg.standings()`, `lg.end_week()`, `lg.matchups(...)`, `lg.transactions(...)` in `yahoo_call(...)`; **delete all four `time.sleep(2)` lines**.
- `src/ffi/ingest/crosswalk.py`: build the query with the fragments — replace the three literal `split_part(...)` occurrences:

```python
from ffi.ids import yahoo_numeric_id_filter_sql, yahoo_numeric_id_sql

# in match_report(), the main query becomes:
        yid = yahoo_numeric_id_sql("p.yahoo_player_id")
        cur.execute(
            f"""
            SELECT p.player_name, p.position, {yid} AS yid, x.xwalk_id
            FROM players p
            LEFT JOIN public.player_id_xwalk x ON x.yahoo_id = {yid}
            WHERE p.position IN %s
              AND {yahoo_numeric_id_filter_sql('p.yahoo_player_id')}
        """,
            (FANTASY_POSITIONS,),
        )
# and the legacy-slug count uses:
#   AND NOT {yahoo_numeric_id_filter_sql('yahoo_player_id')}
```

(`!~` becomes `NOT (... ~ ...)` via the shared fragment — keep semantics identical.)

- [ ] **Step 12: Full test suite + live smoke**

Run: `uv run pytest -q`
Expected: all PASS (24 Phase-1 tests + new ones)

Run: `uv run python scripts/build_crosswalk.py`
Expected: same coverage output as Phase 1 (~94.2%), proving the SQL refactor is behavior-preserving.

- [ ] **Step 13: Commit (the mandated first commit)**

```bash
git add src/ffi/ids.py src/ffi/yahoo_client.py src/ffi/ingest/nflverse.py \
  src/ffi/ingest/crosswalk.py scripts/import_yahoo_season.py \
  scripts/fix_placeholder_players.py tests/test_ids.py tests/test_yahoo_client.py
git commit -m "refactor: consolidate Yahoo id parsing, throttle wrapper, nflverse column map (Phase 2 mandated first commit)"
```

---

### Task 2: Data hygiene — legacy slug cleanup, crosswalk dup-guard, 2025-rookie overrides

Carry-forwards: 85 legacy `nfl.p.<slug>` player rows (duplicates of numeric-id rows; FK-check before deleting), crosswalk `match_report` dup-yahoo_id join guard (matters once manual overrides coexist with auto rows), and 2025-rookie manual overrides (`manual_override=TRUE`; rookies have null yahoo_id in ff_playerids — R6).

**Files:**
- Create: `scripts/cleanup_legacy_slug_players.py`
- Create: `scripts/add_rookie_overrides.py`
- Modify: `src/ffi/ingest/crosswalk.py`
- Test: `tests/test_crosswalk.py` (extend)

**Interfaces:**
- Consumes: `ffi.ids.yahoo_numeric_id_filter_sql`, `ffi.ids.is_numeric_player_key`
- Produces: manual-override precedence in `load_xwalk_rows` (auto rows sharing an id with a manual row are dropped); `assert_no_duplicate_ids(conn)` tripwire in `ffi.ingest.crosswalk`

- [ ] **Step 1: Write failing tests for override precedence + dup tripwire**

Append to `tests/test_crosswalk.py`:

```python
from ffi.ingest.base import IngestError
from ffi.ingest.crosswalk import assert_no_duplicate_ids, dedupe_auto_vs_manual


def _insert_xwalk(db, name, yahoo_id, sleeper_id, manual):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO public.player_id_xwalk (name, position, yahoo_id, sleeper_id, manual_override)"
            " VALUES (%s,'WR',%s,%s,%s)",
            (name, yahoo_id, sleeper_id, manual),
        )
    db.commit()


def test_manual_override_wins_over_auto_row(db):
    _insert_xwalk(db, "Rookie Guy", "99991", "s1", True)
    _insert_xwalk(db, "Rookie Guy", "99991", "s2", False)  # auto row, same yahoo_id
    dedupe_auto_vs_manual(db)
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.player_id_xwalk WHERE yahoo_id='99991'")
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT manual_override FROM public.player_id_xwalk WHERE yahoo_id='99991'"
        )
        assert cur.fetchone()[0] is True


def test_duplicate_yahoo_id_tripwire(db):
    _insert_xwalk(db, "A", "88880", "sa", False)
    _insert_xwalk(db, "B", "88880", "sb", False)  # two auto rows, same yahoo_id
    with pytest.raises(IngestError, match="duplicate"):
        assert_no_duplicate_ids(db)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_crosswalk.py -q`
Expected: FAIL — `ImportError: cannot import name 'assert_no_duplicate_ids'`

- [ ] **Step 3: Implement dedupe + tripwire in `src/ffi/ingest/crosswalk.py`**

```python
def dedupe_auto_vs_manual(conn) -> int:
    """Manual-override rows are authoritative: drop any auto row that shares a
    yahoo_id, sleeper_id, or gsis_id with a manual row (otherwise joins fan out).
    Returns rows deleted."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.player_id_xwalk a
            USING public.player_id_xwalk m
            WHERE a.manual_override = FALSE AND m.manual_override = TRUE
              AND (   (a.yahoo_id   IS NOT NULL AND a.yahoo_id   = m.yahoo_id)
                   OR (a.sleeper_id IS NOT NULL AND a.sleeper_id = m.sleeper_id)
                   OR (a.gsis_id    IS NOT NULL AND a.gsis_id    = m.gsis_id))
            """
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def assert_no_duplicate_ids(conn) -> None:
    """Tripwire: any id column mapping to >1 xwalk row corrupts every join
    downstream. Fail loud with the offending ids (risk R6)."""
    with conn.cursor() as cur:
        for col in ("yahoo_id", "sleeper_id", "gsis_id"):
            cur.execute(
                f"""SELECT {col}, count(*) FROM public.player_id_xwalk
                    WHERE {col} IS NOT NULL GROUP BY 1 HAVING count(*) > 1 LIMIT 10"""
            )
            dups = cur.fetchall()
            if dups:
                raise IngestError(
                    f"crosswalk has duplicate {col} values (joins would fan out): {dups}. "
                    "Resolve via manual_override rows before proceeding."
                )
```

Call both at the end of `load_xwalk_rows` (after the insert, before returning): `dedupe_auto_vs_manual(conn)` then `assert_no_duplicate_ids(conn)`. Also call `assert_no_duplicate_ids(conn)` at the top of `match_report`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_crosswalk.py -q`
Expected: PASS

- [ ] **Step 5: Write `scripts/cleanup_legacy_slug_players.py`**

```python
#!/usr/bin/env python3
"""Remove legacy slug-format player rows (nfl.p.<name>) — duplicates of
numeric-id rows from the old import. FK-safe: referenced rows are remapped to
their numeric twin when the match is unambiguous, otherwise reported and kept."""
import argparse

from ffi.db import connect
from ffi.ids import yahoo_numeric_id_filter_sql

FK_TABLES = [("draft_picks", "player_id"), ("player_stats", "player_id"),
             ("trade_details", "player_id"), ("transactions", "player_id")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete/remap (default: dry run)")
    args = ap.parse_args()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT player_id, yahoo_player_id, player_name, position FROM players
                WHERE NOT ({yahoo_numeric_id_filter_sql('yahoo_player_id')})"""
        )
        slug_rows = cur.fetchall()
    print(f"{len(slug_rows)} legacy slug rows found")

    deleted = remapped = kept = 0
    for pid, ykey, name, pos in slug_rows:
        refs = {}
        with conn.cursor() as cur:
            for table, col in FK_TABLES:
                cur.execute(f"SELECT count(*) FROM {table} WHERE {col}=%s", (pid,))
                n = cur.fetchone()[0]
                if n:
                    refs[table] = n
            if not refs:
                if args.apply:
                    cur.execute("DELETE FROM players WHERE player_id=%s", (pid,))
                deleted += 1
                continue
            # referenced: find the unambiguous numeric twin by (name, position)
            cur.execute(
                f"""SELECT player_id FROM players
                    WHERE player_name=%s AND position=%s AND player_id<>%s
                      AND {yahoo_numeric_id_filter_sql('yahoo_player_id')}""",
                (name, pos, pid),
            )
            twins = cur.fetchall()
            if len(twins) != 1:
                print(f"  KEEP (ambiguous twin x{len(twins)}): {name} ({pos}) {ykey} refs={refs}")
                kept += 1
                continue
            twin_id = twins[0][0]
            if args.apply:
                for table, col in FK_TABLES:
                    cur.execute(f"UPDATE {table} SET {col}=%s WHERE {col}=%s", (twin_id, pid))
                cur.execute("DELETE FROM players WHERE player_id=%s", (pid,))
            remapped += 1
        conn.commit()
    print(f"deleted={deleted} remapped={remapped} kept={kept} "
          f"({'APPLIED' if args.apply else 'DRY RUN — rerun with --apply'})")
    if kept:
        print("Kept rows need manual resolution — report to the user.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Dry-run, review output, then apply**

Run: `uv run python scripts/cleanup_legacy_slug_players.py`
Expected: `85 legacy slug rows found`, breakdown printed, `DRY RUN`.
Review the printed remaps for sanity (names must match their twins), then:
Run: `uv run python scripts/cleanup_legacy_slug_players.py --apply`
Verify: `psql -d fantasy_football -c "SELECT count(*) FROM players WHERE NOT (split_part(yahoo_player_id,'.p.',2) ~ '^[0-9]+$')"` → ideally 0; any kept rows reported to the user in the task report.

- [ ] **Step 7: Write `scripts/add_rookie_overrides.py`**

```python
#!/usr/bin/env python3
"""Propose manual-override crosswalk rows for Yahoo players unmatched because
ff_playerids has null yahoo_id (2025 rookies — risk R6). Matches by exact
(lower(name), position) against xwalk rows missing a yahoo_id. Ambiguous or
unmatched names are printed for the human — never guessed."""
import argparse

from ffi.db import connect
from ffi.ingest.crosswalk import assert_no_duplicate_ids, match_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = connect()
    report = match_report(conn)
    print(f"{len(report['unmatched'])} unmatched fantasy-relevant Yahoo players")
    applied, ambiguous, unfound = 0, [], []
    for name, pos, yid in report["unmatched"]:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT xwalk_id, name, gsis_id, sleeper_id, fantasypros_id
                   FROM public.player_id_xwalk
                   WHERE lower(name)=lower(%s) AND position=%s AND yahoo_id IS NULL
                     AND manual_override = FALSE""",
                (name, pos),
            )
            cands = cur.fetchall()
        if len(cands) == 0:
            unfound.append((name, pos, yid))
            continue
        if len(cands) > 1:
            ambiguous.append((name, pos, yid, cands))
            continue
        xid, xname, gsis, sleeper, fp = cands[0]
        print(f"  MATCH {name} ({pos}) yahoo={yid} -> xwalk#{xid} gsis={gsis} sleeper={sleeper}")
        if args.apply:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO public.player_id_xwalk
                       (name, position, gsis_id, sleeper_id, yahoo_id, fantasypros_id, manual_override)
                       VALUES (%s,%s,%s,%s,%s,%s,TRUE)""",
                    (xname, pos, gsis, sleeper, yid, fp),
                )
            conn.commit()
        applied += 1
    if args.apply:
        from ffi.ingest.crosswalk import dedupe_auto_vs_manual
        dedupe_auto_vs_manual(conn)
        assert_no_duplicate_ids(conn)
    print(f"proposed/applied={applied} ambiguous={len(ambiguous)} no-candidate={len(unfound)}")
    for item in ambiguous:
        print("  AMBIGUOUS:", item[:3])
    for item in unfound:
        print("  NO-CANDIDATE:", item)
    print("APPLIED" if args.apply else "DRY RUN — review matches, rerun with --apply")


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Dry-run, review, apply, re-report coverage**

Run: `uv run python scripts/add_rookie_overrides.py` → review every proposed match by eye (names must be the same human).
Run: `uv run python scripts/add_rookie_overrides.py --apply`
Run: `uv run python scripts/build_crosswalk.py`
Expected: coverage ≥ 94.2% (strictly higher if overrides landed); no duplicate-id tripwire.
NO-CANDIDATE and AMBIGUOUS players go in the task report for the user.

- [ ] **Step 9: Full suite + commit**

Run: `uv run pytest -q` — all PASS.

```bash
git add scripts/cleanup_legacy_slug_players.py scripts/add_rookie_overrides.py \
  src/ffi/ingest/crosswalk.py tests/test_crosswalk.py
git commit -m "fix: crosswalk manual-override precedence + dup tripwire; slug-row cleanup; rookie overrides"
```

---

### Task 3: Migration 002, conftest multi-migration support, scoring config v1

All Phase 2 DDL lands in one migration so conftest and the restore drill stay simple. Includes carry-forward tables: `public.team_def_map` (DEF scoring needs defenses; crosswalk excludes them by design) and `public.manager_slot_annotations` (migrates the "user inherited slot ~2022" fact out of the audit script's print into data).

**Files:**
- Create: `migrations/002_scoring_valuation.sql`
- Create: `config/scoring/v1.json`
- Create: `src/ffi/scoring/__init__.py`, `src/ffi/scoring/config.py`
- Create: `scripts/build_def_map.py`
- Create: `tests/test_scoring_config.py`
- Modify: `tests/conftest.py` (run all migrations, dynamic teardown)

**Interfaces:**
- Produces: `ffi.scoring.config.ScoringConfig` (pydantic, frozen), `load_config(path: str | Path) -> ScoringConfig`, `load_config_v1() -> ScoringConfig` (repo-relative default), `ensure_config_in_db(conn, cfg: ScoringConfig) -> None` (idempotent, immutability-guarded)
- Produces tables: `scoring.config`, `scoring.player_week_points`, `scoring.projection_points`, `public.team_def_map`, `public.manager_slot_annotations`, `raw.fp_snapshots`, `valuation.player_value`, `valuation.replacement_baseline`, `public.matchup_results`, view `public.v_player_yahoo_ids`, `teams.slot` column
- Config field names ARE the `StatLine` field names (Task 4) — they must match exactly.

- [ ] **Step 1: Write `migrations/002_scoring_valuation.sql`**

```sql
-- Phase 2: scoring engine, valuation, history-mining DDL (ADR Domain 2)

CREATE TABLE IF NOT EXISTS scoring.config (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    rules       JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Engine output over historical actuals (recomputable from raw + config).
CREATE TABLE IF NOT EXISTS scoring.player_week_points (
    source          TEXT NOT NULL,      -- 'nflverse' | 'yahoo_engine'
    player_ref      TEXT NOT NULL,      -- gsis_id for nflverse, numeric yahoo id for yahoo_engine
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    points          NUMERIC NOT NULL,
    components      JSONB,              -- per-category breakdown for audit
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, player_ref, season, week, config_version)
);

-- Engine output over projection snapshots.
CREATE TABLE IF NOT EXISTS scoring.projection_points (
    source          TEXT NOT NULL,      -- 'sleeper' | 'fantasypros'
    snapshot_id     INTEGER NOT NULL,   -- raw.sleeper_projections.snapshot_id or raw.fp_snapshots.snapshot_id
    player_ref      TEXT NOT NULL,      -- source-native player id
    horizon         TEXT NOT NULL,      -- 'season' | 'week:N'
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    points          NUMERIC NOT NULL,
    components      JSONB,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, snapshot_id, player_ref, config_version)
);

-- DEF mapping: team defenses key on Yahoo numeric DEF ids + team abbr (carry-forward).
CREATE TABLE IF NOT EXISTS public.team_def_map (
    yahoo_def_id TEXT PRIMARY KEY,      -- numeric, e.g. '100012'
    team_abbr    TEXT NOT NULL UNIQUE,  -- canonical uppercase (ffi.ids.NFL_TEAMS)
    team_name    TEXT NOT NULL          -- Yahoo nickname, e.g. 'Chiefs'
);

-- Slot-vs-human annotation (user input; slot = Yahoo team number, stable per season).
CREATE TABLE IF NOT EXISTS public.manager_slot_annotations (
    league_slot  INTEGER NOT NULL,
    human_label  TEXT NOT NULL,
    from_season  INTEGER NOT NULL,
    to_season    INTEGER,               -- NULL = through present
    note         TEXT,
    PRIMARY KEY (league_slot, from_season)
);
INSERT INTO public.manager_slot_annotations (league_slot, human_label, from_season, note)
VALUES (12, 'Brent', 2022, 'user; inherited slot ~2022 — exact season to confirm with annotation')
ON CONFLICT (league_slot, from_season) DO NOTHING;

-- FantasyPros raw cache (one row per API call; the daily budget counts these).
CREATE TABLE IF NOT EXISTS raw.fp_snapshots (
    snapshot_id SERIAL PRIMARY KEY,
    run_id      INTEGER REFERENCES raw.ingest_runs(run_id),
    endpoint    TEXT NOT NULL,
    params      JSONB NOT NULL,
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Valuation outputs (recomputable; params carry full provenance).
CREATE TABLE IF NOT EXISTS valuation.replacement_baseline (
    baseline_id     SERIAL PRIMARY KEY,
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    scenario        TEXT NOT NULL,      -- e.g. 'qb_hoard_0', 'qb_hoard_12'
    position        TEXT NOT NULL,
    replacement_rank INTEGER NOT NULL,
    replacement_points NUMERIC NOT NULL,
    params          JSONB NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS valuation.player_value (
    value_id        SERIAL PRIMARY KEY,
    config_version  INTEGER NOT NULL REFERENCES scoring.config(version),
    scenario        TEXT NOT NULL,
    xwalk_id        INTEGER NOT NULL REFERENCES public.player_id_xwalk(xwalk_id),
    position        TEXT NOT NULL,
    proj_points     NUMERIC NOT NULL,
    vorp            NUMERIC NOT NULL,
    tier            INTEGER,
    value_low       NUMERIC,            -- uncertainty band
    value_high      NUMERIC,
    params          JSONB NOT NULL,     -- snapshot ids, source weights, GMM params
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_player_value_lookup
    ON valuation.player_value (config_version, scenario, position, vorp DESC);

-- Parsed weekly H2H results (from raw.yahoo_matchups payloads — Task 13).
CREATE TABLE IF NOT EXISTS public.matchup_results (
    league_key   TEXT NOT NULL,
    season       INTEGER NOT NULL,
    week         INTEGER NOT NULL,
    team_key     TEXT NOT NULL,
    slot         INTEGER NOT NULL,
    points       NUMERIC NOT NULL,
    proj_points  NUMERIC,
    opp_team_key TEXT NOT NULL,
    opp_points   NUMERIC NOT NULL,
    is_playoffs  BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (league_key, week, team_key)
);

-- Slot column on teams (Yahoo team number within the league season).
ALTER TABLE teams ADD COLUMN IF NOT EXISTS slot INTEGER;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS team_key VARCHAR(50);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_league_slot ON teams (league_id, slot);
CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_team_key ON teams (team_key);

-- Canonical numeric-yahoo-id -> one player row (players has one row per
-- game-code key; established fact #6). Latest game code = current name/team.
CREATE OR REPLACE VIEW public.v_player_yahoo_ids AS
SELECT DISTINCT ON (split_part(yahoo_player_id, '.p.', 2))
       split_part(yahoo_player_id, '.p.', 2) AS yahoo_id,
       player_id, player_name, position, nfl_team
FROM players
WHERE split_part(yahoo_player_id, '.p.', 2) ~ '^[0-9]+$'
ORDER BY split_part(yahoo_player_id, '.p.', 2),
         split_part(yahoo_player_id, '.p.', 1)::int DESC;
```

- [ ] **Step 2: Update `tests/conftest.py` to run all migrations + dynamic teardown**

Replace the fixture body's migration block and teardown:

```python
@pytest.fixture()
def db():
    conn = psycopg2.connect(dbname="fantasy_football_test", host="localhost")
    repo_root = pathlib.Path(__file__).parent.parent
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.players')")
        if cur.fetchone()[0] is None:
            cur.execute((repo_root / "schema" / "create_tables.sql").read_text())
        for mig in sorted((repo_root / "migrations").glob("*.sql")):
            cur.execute(mig.read_text())
    conn.commit()
    yield conn
    conn.rollback()
    with conn.cursor() as cur:
        # Truncate every table in the derived schemas + the mutable public ones.
        cur.execute(
            """SELECT schemaname, tablename FROM pg_tables
               WHERE schemaname IN ('raw','scoring','valuation','signals','sim','draft')"""
        )
        tables = [f"{s}.{t}" for s, t in cur.fetchall()]
        cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE public.player_id_xwalk, public.matchup_results RESTART IDENTITY CASCADE")
        cur.execute("TRUNCATE players CASCADE")
    conn.commit()
    conn.close()
```

(`scoring.config` is truncated too — tests that need a config insert it via the loader; the seeded `manager_slot_annotations` row lives in `public` and survives, which is fine because the migration re-inserts idempotently.)

- [ ] **Step 3: Apply the migration to the real DB**

Run: `psql -d fantasy_football -f migrations/002_scoring_valuation.sql`
Expected: no errors. Verify: `psql -d fantasy_football -c "\dt scoring.*"` lists `config`, `player_week_points`, `projection_points`.

- [ ] **Step 4: Write `config/scoring/v1.json`** (weights from `league_rules.md`; semantics from established facts #4/#5)

```json
{
  "version": 1,
  "description": "NAJEE 'LEFT EYE' HARRIS 2025 rules (league_rules.md; cumulative bonus stacking + base weights verified against Yahoo official 2025 points, 2026-07-09)",
  "offense": {
    "weights": {
      "pass_completions": 0.5,
      "pass_incompletions": -0.5,
      "pass_yards": 0.04,
      "pass_tds": 6,
      "interceptions": -2,
      "pick_sixes": -4,
      "rush_attempts": 0.33,
      "rush_yards": 0.1,
      "rush_tds": 6,
      "rush_first_downs": 1,
      "receptions": 1,
      "rec_yards": 0.1,
      "rec_tds": 6,
      "rec_first_downs": 1,
      "return_yards": 0.1,
      "return_tds": 6,
      "two_point_conversions": 2,
      "fumbles": -1,
      "fumbles_lost": -2,
      "offensive_fumble_return_tds": 6
    },
    "yardage_bonuses": {
      "pass_yards": [
        {"threshold": 300, "points": 3},
        {"threshold": 400, "points": 4},
        {"threshold": 500, "points": 5}
      ],
      "rush_yards": [
        {"threshold": 100, "points": 3},
        {"threshold": 150, "points": 4},
        {"threshold": 200, "points": 5}
      ],
      "rec_yards": [
        {"threshold": 100, "points": 3},
        {"threshold": 150, "points": 4},
        {"threshold": 200, "points": 5}
      ],
      "return_yards": [
        {"threshold": 200, "points": 3},
        {"threshold": 250, "points": 4},
        {"threshold": 300, "points": 5}
      ]
    },
    "bonus_stacking": "cumulative"
  },
  "kicking": {
    "weights": {
      "fg_0_19": 3, "fg_20_29": 3, "fg_30_39": 3, "fg_40_49": 4, "fg_50_plus": 5,
      "fg_miss_0_19": -3, "fg_miss_20_29": -2, "fg_miss_30_39": -1,
      "pat_made": 1, "pat_missed": -1
    }
  },
  "defense": {
    "weights": {
      "sacks": 1, "def_interceptions": 2, "fumble_recoveries": 2,
      "defensive_tds": 6, "safeties": 2, "blocked_kicks": 2,
      "fourth_down_stops": 2, "tackles_for_loss": 1, "three_and_outs": 1,
      "extra_point_returns": 2
    },
    "points_allowed_tiers": [
      {"max": 0, "points": 10}, {"max": 6, "points": 7}, {"max": 13, "points": 4},
      {"max": 20, "points": 1}, {"max": 27, "points": 0}, {"max": 34, "points": -1},
      {"max": null, "points": -4}
    ],
    "yards_allowed_tiers": [
      {"max": -1, "points": 20}, {"max": 99, "points": 10}, {"max": 199, "points": 7},
      {"max": 299, "points": 4}, {"max": 399, "points": 0}, {"max": 499, "points": -4},
      {"max": null, "points": -7}
    ]
  }
}
```

- [ ] **Step 5: Write failing tests for the config loader**

`tests/test_scoring_config.py`:

```python
import json

import pytest

from ffi.scoring.config import ScoringConfig, ensure_config_in_db, load_config_v1


def test_load_config_v1():
    cfg = load_config_v1()
    assert cfg.version == 1
    assert cfg.offense.weights["receptions"] == 1
    assert cfg.offense.weights["pass_yards"] == 0.04
    assert cfg.offense.bonus_stacking == "cumulative"
    assert cfg.defense.points_allowed_tiers[0].max == 0
    assert cfg.defense.points_allowed_tiers[-1].max is None
    assert cfg.kicking.weights["fg_miss_30_39"] == -1


def test_config_rejects_unknown_fields(tmp_path):
    cfg = json.loads((tmp_path.parent / "x").with_name("ignore") and "{}") if False else None
    raw = load_config_v1().model_dump()
    raw["offense"]["weights"]["made_up_stat"] = 99
    with pytest.raises(Exception):  # pydantic ValidationError
        ScoringConfig.model_validate(raw)


def test_ensure_config_in_db_idempotent(db):
    cfg = load_config_v1()
    ensure_config_in_db(db, cfg)
    ensure_config_in_db(db, cfg)  # second call is a no-op
    with db.cursor() as cur:
        cur.execute("SELECT count(*) FROM scoring.config WHERE version=1")
        assert cur.fetchone()[0] == 1


def test_ensure_config_in_db_immutability_guard(db):
    cfg = load_config_v1()
    ensure_config_in_db(db, cfg)
    mutated = cfg.model_copy(deep=True)
    mutated.offense.weights["receptions"] = 2
    with pytest.raises(ValueError, match="immutable"):
        ensure_config_in_db(db, mutated)
```

Note: `test_config_rejects_unknown_fields` should simply mutate the dumped dict and re-validate — drop the dead first line if it confuses; the assertion is that `extra="forbid"` holds on the weights model. (Implementer: weights are `dict[str, float]`, so enforce unknown-field rejection at the **StatLine/engine boundary** instead if the dict model can't forbid keys — the engine test in Task 4 Step 5 covers unknown weight keys failing loud. Keep whichever test is honest, delete the other.)

- [ ] **Step 6: Run to verify failure**

Run: `uv run pytest tests/test_scoring_config.py -q`
Expected: FAIL — no module `ffi.scoring`

- [ ] **Step 7: Implement `src/ffi/scoring/config.py`** (+ empty `src/ffi/scoring/__init__.py`)

```python
"""Versioned scoring config: committed JSON is the source, scoring.config the
DB mirror. Configs are IMMUTABLE — any rules change is a new version (ADR D2/D8)."""
import json
import pathlib

from pydantic import BaseModel, ConfigDict

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
V1_PATH = REPO_ROOT / "config" / "scoring" / "v1.json"


class BonusTier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    threshold: float
    points: float


class RangeTier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max: float | None  # None = +infinity (last tier)
    points: float


class OffenseRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: dict[str, float]
    yardage_bonuses: dict[str, list[BonusTier]]
    bonus_stacking: str  # 'cumulative' is the verified semantic


class KickingRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: dict[str, float]


class DefenseRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    weights: dict[str, float]
    points_allowed_tiers: list[RangeTier]
    yards_allowed_tiers: list[RangeTier]


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int
    description: str
    offense: OffenseRules
    kicking: KickingRules
    defense: DefenseRules


def load_config(path: str | pathlib.Path) -> ScoringConfig:
    return ScoringConfig.model_validate(json.loads(pathlib.Path(path).read_text()))


def load_config_v1() -> ScoringConfig:
    return load_config(V1_PATH)


def ensure_config_in_db(conn, cfg: ScoringConfig) -> None:
    """Insert if absent. If the version exists with DIFFERENT rules, fail loud:
    configs are immutable — bump the version instead."""
    rules = cfg.model_dump()
    with conn.cursor() as cur:
        cur.execute("SELECT rules FROM scoring.config WHERE version=%s", (cfg.version,))
        row = cur.fetchone()
        if row is not None:
            if row[0] != rules:
                raise ValueError(
                    f"scoring.config version {cfg.version} exists with different rules — "
                    "configs are immutable; create a new version file instead."
                )
            return
        cur.execute(
            "INSERT INTO scoring.config (version, description, rules) VALUES (%s,%s,%s)",
            (cfg.version, cfg.description, json.dumps(rules)),
        )
    conn.commit()
```

Note frozen models: `mutated.offense.weights["receptions"] = 2` mutates the *dict inside* a frozen model — allowed, which is exactly what the immutability-guard test needs. `parents[3]` from `src/ffi/scoring/config.py` = repo root; verify with the test.

- [ ] **Step 8: Run tests**

Run: `uv run pytest tests/test_scoring_config.py -q`
Expected: PASS

- [ ] **Step 9: Write `scripts/build_def_map.py`** (DEF nickname → abbr; fail loud on unknowns)

```python
#!/usr/bin/env python3
"""Populate public.team_def_map from DEF rows in players. Yahoo DEF ids are
numeric (e.g. Chiefs = 100012); names are nicknames. Fail loud on any nickname
not in the static map or if fewer than 24 teams resolve (drafted-DEF coverage
should span most of the league across 16 seasons)."""
from ffi.db import connect
from ffi.ids import normalize_team_abbr

NICKNAME_TO_ABBR = {
    "Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF",
    "Panthers": "CAR", "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE",
    "Cowboys": "DAL", "Broncos": "DEN", "Lions": "DET", "Packers": "GB",
    "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX", "Chiefs": "KC",
    "Rams": "LA", "Chargers": "LAC", "Raiders": "LV", "Dolphins": "MIA",
    "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG",
    "Jets": "NYJ", "Eagles": "PHI", "Steelers": "PIT", "Seahawks": "SEA",
    "49ers": "SF", "Buccaneers": "TB", "Titans": "TEN",
    "Commanders": "WAS", "Redskins": "WAS", "Football Team": "WAS",
}

conn = connect()
with conn.cursor() as cur:
    cur.execute(
        """SELECT DISTINCT split_part(yahoo_player_id, '.p.', 2), player_name
           FROM players WHERE position = 'DEF'
             AND split_part(yahoo_player_id, '.p.', 2) ~ '^[0-9]+$'"""
    )
    rows = cur.fetchall()

unknown = [name for _, name in rows if name not in NICKNAME_TO_ABBR]
if unknown:
    raise SystemExit(f"unmapped DEF nicknames {sorted(set(unknown))} — extend NICKNAME_TO_ABBR")

with conn.cursor() as cur:
    for def_id, name in rows:
        abbr = normalize_team_abbr(NICKNAME_TO_ABBR[name])
        cur.execute(
            """INSERT INTO public.team_def_map (yahoo_def_id, team_abbr, team_name)
               VALUES (%s,%s,%s)
               ON CONFLICT (yahoo_def_id) DO UPDATE SET team_abbr=EXCLUDED.team_abbr""",
            (def_id, abbr, name),
        )
conn.commit()
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM public.team_def_map")
    n = cur.fetchone()[0]
print(f"team_def_map: {n} defenses mapped")
if n < 24:
    raise SystemExit(f"only {n} defenses mapped — expected most of 32; investigate players DEF rows")
```

Gotcha: multiple game codes give the same numeric DEF id different rows with the same nickname — the DISTINCT handles it; if the same numeric id maps to two nicknames (franchise rename: Redskins→Commanders share an id), the upsert keeps one abbr, which is correct (both → WAS).

- [ ] **Step 10: Run it**

Run: `uv run python scripts/build_def_map.py`
Expected: `team_def_map: N defenses mapped` with N ≥ 24 (report exact N). If it fails on unknown nicknames, extend the map — do not skip rows.

- [ ] **Step 11: Full suite + commit**

Run: `uv run pytest -q` — all PASS.

```bash
git add migrations/002_scoring_valuation.sql config/scoring/v1.json \
  src/ffi/scoring/ scripts/build_def_map.py tests/test_scoring_config.py tests/conftest.py
git commit -m "feat: Phase 2 DDL, versioned scoring config v1, DEF team map, slot annotations"
```

---

### Task 4: Scoring engine core (pure function + property tests)

The heart of the phase: `score_stat_line(stat_line, config) → Decimal`, pure (no I/O, no state, no clock — ADR D7 purity test). Decimal arithmetic from string conversion so `0.33 × 3 = 0.99` exactly — Yahoo's totals are exact decimals and the golden gate is exact-match.

**Files:**
- Create: `src/ffi/scoring/statline.py`
- Create: `src/ffi/scoring/engine.py`
- Create: `tests/test_scoring_engine.py`

**Interfaces:**
- Consumes: `ffi.scoring.config.ScoringConfig`
- Produces: `ffi.scoring.statline.StatLine` (pydantic, all fields `float | None = None`, `extra="forbid"`); `ffi.scoring.engine.score_stat_line(line: StatLine, cfg: ScoringConfig) -> Decimal`; `ffi.scoring.engine.score_components(line, cfg) -> dict[str, Decimal]` (per-category breakdown: `weights`, `bonuses`, `def_tiers`)
- StatLine field names == config weight keys (Task 3). Adapters (Tasks 5–7) produce StatLine.

- [ ] **Step 1: Write `src/ffi/scoring/statline.py`**

```python
"""Canonical stat line: the single vocabulary every source adapter maps into.
None = the source does not carry that stat (distinct from 0 = observed zero)."""
from pydantic import BaseModel, ConfigDict


class StatLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Offense
    pass_completions: float | None = None
    pass_incompletions: float | None = None
    pass_yards: float | None = None
    pass_tds: float | None = None
    interceptions: float | None = None
    pick_sixes: float | None = None
    rush_attempts: float | None = None
    rush_yards: float | None = None
    rush_tds: float | None = None
    rush_first_downs: float | None = None
    receptions: float | None = None
    rec_yards: float | None = None
    rec_tds: float | None = None
    rec_first_downs: float | None = None
    return_yards: float | None = None
    return_tds: float | None = None
    two_point_conversions: float | None = None
    fumbles: float | None = None
    fumbles_lost: float | None = None
    offensive_fumble_return_tds: float | None = None
    # Kicking
    fg_0_19: float | None = None
    fg_20_29: float | None = None
    fg_30_39: float | None = None
    fg_40_49: float | None = None
    fg_50_plus: float | None = None
    fg_miss_0_19: float | None = None
    fg_miss_20_29: float | None = None
    fg_miss_30_39: float | None = None
    pat_made: float | None = None
    pat_missed: float | None = None
    # Defense/ST
    sacks: float | None = None
    def_interceptions: float | None = None
    fumble_recoveries: float | None = None
    defensive_tds: float | None = None
    safeties: float | None = None
    blocked_kicks: float | None = None
    fourth_down_stops: float | None = None
    tackles_for_loss: float | None = None
    three_and_outs: float | None = None
    extra_point_returns: float | None = None
    points_allowed: float | None = None
    yards_allowed: float | None = None
```

- [ ] **Step 2: Write failing engine tests** (hand-computed golden values from established facts #4/#5 + league_rules.md)

`tests/test_scoring_engine.py`:

```python
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from ffi.scoring.config import load_config_v1
from ffi.scoring.engine import score_components, score_stat_line
from ffi.scoring.statline import StatLine

CFG = load_config_v1()


def test_zay_flowers_style_line():
    # 7 rec, 143 rec yds, 1 rec TD, 5 rec FD, 2 rush att, 8 rush yds
    # = 7 + 14.3 + 6 + 5 + 0.66 + 0.8 + 3 (100+ bonus) = 36.76 (verified vs Yahoo)
    line = StatLine(receptions=7, rec_yards=143, rec_tds=1, rec_first_downs=5,
                    rush_attempts=2, rush_yards=8)
    assert score_stat_line(line, CFG) == Decimal("36.76")


def test_cumulative_bonus_stacking_200_plus():
    # 216 rush yds -> 21.6 + bonuses 3+4+5 = 33.6 (fact #4: cumulative)
    line = StatLine(rush_yards=216)
    assert score_stat_line(line, CFG) == Decimal("33.6")


def test_cumulative_bonus_stacking_150_band():
    line = StatLine(rec_yards=170)  # 17.0 + 3 + 4 = 24.0
    assert score_stat_line(line, CFG) == Decimal("24.0")


def test_passing_line_with_pick_six():
    # 20 comp, 12 inc, 450 yds, 2 TD, 1 INT (pick six):
    # 10 - 6 + 18 + 12 - 2 - 4 + bonuses (300+ =3, 400+ =4) = 35.0
    line = StatLine(pass_completions=20, pass_incompletions=12, pass_yards=450,
                    pass_tds=2, interceptions=1, pick_sixes=1)
    assert score_stat_line(line, CFG) == Decimal("35.0")


def test_negative_game():
    line = StatLine(fumbles=2, fumbles_lost=2, rush_attempts=3, rush_yards=-2)
    # -2 -4 + 0.99 - 0.2 = -5.21
    assert score_stat_line(line, CFG) == Decimal("-5.21")


def test_kicker_line():
    # Aubrey-style: 1x FG40-49, 1x FG50+, 2 PAT = 4 + 5 + 2 = 11 (verified vs Yahoo)
    line = StatLine(fg_40_49=1, fg_50_plus=1, pat_made=2)
    assert score_stat_line(line, CFG) == Decimal("11")


def test_defense_line_chiefs_wk_sample():
    # 3 sacks, 6 TFL, 2 three-and-outs, 27 pts allowed (tier 21-27 = 0),
    # 394 yds allowed (tier 300-399 = 0) = 11 (verified vs Yahoo)
    line = StatLine(sacks=3, tackles_for_loss=6, three_and_outs=2,
                    points_allowed=27, yards_allowed=394)
    assert score_stat_line(line, CFG) == Decimal("11")


def test_defense_shutout_and_negative_yards():
    line = StatLine(points_allowed=0, yards_allowed=-3)
    assert score_stat_line(line, CFG) == Decimal("30")  # 10 + 20


def test_defense_worst_tiers():
    line = StatLine(points_allowed=38, yards_allowed=520)
    assert score_stat_line(line, CFG) == Decimal("-11")  # -4 + -7


def test_empty_line_scores_zero():
    assert score_stat_line(StatLine(), CFG) == Decimal("0")


def test_components_sum_to_total():
    line = StatLine(receptions=7, rec_yards=143, rec_tds=1, rec_first_downs=5)
    comps = score_components(line, CFG)
    assert sum(comps.values()) == score_stat_line(line, CFG)


def test_unknown_config_weight_key_fails_loud():
    raw = CFG.model_dump()
    raw["offense"]["weights"]["made_up_stat"] = 9
    from ffi.scoring.config import ScoringConfig
    bad = ScoringConfig.model_validate(raw)
    with pytest.raises(KeyError):
        score_stat_line(StatLine(receptions=1), bad)


# --- purity / property tests (ADR Domain 7) ---
finite = st.one_of(st.none(), st.floats(min_value=-500, max_value=1000,
                                        allow_nan=False, allow_infinity=False))


@given(rec_yards=st.floats(min_value=0, max_value=400, allow_nan=False),
       receptions=st.floats(min_value=0, max_value=20, allow_nan=False))
def test_deterministic_and_input_unmutated(rec_yards, receptions):
    line = StatLine(rec_yards=rec_yards, receptions=receptions)
    before = line.model_dump()
    a = score_stat_line(line, CFG)
    b = score_stat_line(line, CFG)
    assert a == b
    assert line.model_dump() == before


@given(y1=st.floats(min_value=0, max_value=300, allow_nan=False),
       y2=st.floats(min_value=0, max_value=300, allow_nan=False))
def test_monotone_in_rec_yards(y1, y2):
    lo, hi = sorted([y1, y2])
    assert score_stat_line(StatLine(rec_yards=hi), CFG) >= score_stat_line(
        StatLine(rec_yards=lo), CFG)
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_scoring_engine.py -q`
Expected: FAIL — no module `ffi.scoring.engine`

- [ ] **Step 4: Implement `src/ffi/scoring/engine.py`**

```python
"""Pure scoring core: (StatLine, ScoringConfig) -> Decimal. No I/O, no state,
no clock. Decimal-from-str arithmetic so results are exact (golden gate is
exact-match vs Yahoo)."""
from decimal import Decimal

from ffi.scoring.config import RangeTier, ScoringConfig
from ffi.scoring.statline import StatLine


def _d(x: float) -> Decimal:
    # float -> shortest-repr string -> Decimal: 0.33 becomes Decimal('0.33').
    return Decimal(repr(x)) if isinstance(x, float) else Decimal(x)


def _tier_points(value: float, tiers: list[RangeTier]) -> Decimal:
    for t in tiers:
        if t.max is None or value <= t.max:
            return _d(t.points)
    raise ValueError(f"no tier matched value {value} — config tiers must end with max=null")


def score_components(line: StatLine, cfg: ScoringConfig) -> dict[str, Decimal]:
    d = line.model_dump()
    comps: dict[str, Decimal] = {}

    weighted = Decimal("0")
    for section in (cfg.offense.weights, cfg.kicking.weights, cfg.defense.weights):
        for field, weight in section.items():
            v = d[field]  # KeyError = config names a stat StatLine lacks: fail loud
            if v is not None:
                weighted += _d(v) * _d(weight)
    comps["weights"] = weighted

    bonuses = Decimal("0")
    for field, tiers in cfg.offense.yardage_bonuses.items():
        v = d[field]
        if v is not None:
            for t in tiers:  # cumulative stacking (verified semantic, fact #4)
                if v >= t.threshold:
                    bonuses += _d(t.points)
    comps["bonuses"] = bonuses

    def_tiers = Decimal("0")
    if line.points_allowed is not None:
        def_tiers += _tier_points(line.points_allowed, cfg.defense.points_allowed_tiers)
    if line.yards_allowed is not None:
        def_tiers += _tier_points(line.yards_allowed, cfg.defense.yards_allowed_tiers)
    comps["def_tiers"] = def_tiers
    return comps


def score_stat_line(line: StatLine, cfg: ScoringConfig) -> Decimal:
    return sum(score_components(line, cfg).values(), Decimal("0"))
```

Decimal-comparison nuance: `Decimal("36.76") == Decimal("36.760")` is True in Python — trailing zeros don't break equality. `_d` uses `repr(float)` which gives the shortest round-trip representation; stat values are halves/integers in practice, so this is exact.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_scoring_engine.py -q`
Expected: all PASS (hypothesis included)

- [ ] **Step 6: Commit**

```bash
git add src/ffi/scoring/statline.py src/ffi/scoring/engine.py tests/test_scoring_engine.py
git commit -m "feat: pure scoring engine with verified cumulative-bonus semantics + property tests"
```

---

### Task 5: Yahoo adapter + golden fixtures + full-2025 sweep (week-3 checkpoint artifact)

Convert `raw.yahoo_player_week.stats` payloads (display-name keys, fact #1–3) into StatLine, then prove the engine reproduces Yahoo's official points: ~40 committed edge-case fixtures (exact match, R1/ADR D7) plus a sweep over all 3,876 2025 rows. **When this task's sweep is green, the week-3 checkpoint is satisfied — say so explicitly in the task report.**

**Files:**
- Create: `src/ffi/scoring/yahoo_adapter.py`
- Create: `scripts/make_golden_fixtures.py`
- Create: `tests/fixtures/golden_2025.json` (generated, committed)
- Create: `tests/test_golden_yahoo.py`
- Create: `scripts/score_sweep_yahoo.py`
- Test: `tests/test_yahoo_adapter.py`

**Interfaces:**
- Consumes: `StatLine`, `score_stat_line`, `load_config_v1`, `ensure_config_in_db`
- Produces: `ffi.scoring.yahoo_adapter.stat_line_from_yahoo(stats: dict) -> StatLine` (dispatches on `position_type`: `'O'`/`'K'`/`'DT'`); populates `scoring.player_week_points` with `source='yahoo_engine'`

- [ ] **Step 1: Write failing adapter tests**

`tests/test_yahoo_adapter.py`:

```python
import pytest

from ffi.ingest.base import IngestError
from ffi.scoring.yahoo_adapter import stat_line_from_yahoo

OFFENSE_PAYLOAD = {
    "Fum": 0.0, "Inc": 0.0, "Int": 0.0, "Rec": 7.0, "2-PT": 0.0, "Comp": 0.0,
    "name": "Zay Flowers", "Rec TD": 1.0, "Ret TD": 0.0, "Pass TD": 0.0,
    "Rec Yds": 143.0, "Ret Yds": 0.0, "Rush TD": 0.0, "Targets": 9.0,
    "Fum Lost": 0.0, "Pass Yds": 0.0, "Pick Six": 0.0, "Rush Att": 2.0,
    "Rush Yds": 8.0, "player_id": 40039, "Fum Ret TD": 0.0,
    "total_points": "36.76", "Rec 1st Downs": 5.0, "position_type": "O",
    "Rush 1st Downs": 0.0,
}


def test_offense_mapping():
    line = stat_line_from_yahoo(OFFENSE_PAYLOAD)
    assert line.receptions == 7.0
    assert line.rec_yards == 143.0
    assert line.rec_first_downs == 5.0
    assert line.rush_attempts == 2.0
    assert line.fg_0_19 is None            # kicking fields untouched for offense
    assert line.points_allowed is None


def test_unknown_key_fails_loud():
    payload = dict(OFFENSE_PAYLOAD, **{"40+ Yd Comp": 2.0})
    with pytest.raises(IngestError, match="unmapped"):
        stat_line_from_yahoo(payload)


def test_missing_position_type_fails_loud():
    payload = {k: v for k, v in OFFENSE_PAYLOAD.items() if k != "position_type"}
    with pytest.raises(IngestError, match="position_type"):
        stat_line_from_yahoo(payload)


DEF_PAYLOAD = {
    "TD": 0.0, "Int": 0.0, "TFL": 6.0, "XPR": 0.0, "Sack": 3.0, "Safe": 0.0,
    "name": "Chiefs", "Fum Rec": 0.0, "Blk Kick": 0.0, "Pts Allow": 27.0,
    "player_id": 100012, "3 and Outs": 2.0, "4 Dwn Stops": 0.0,
    "Pts Allow 0": 0.0, "total_points": "11.00", "Def Yds Allow": 394.0,
    "Pts Allow 1-6": 0.0, "Pts Allow 35+": 0.0, "Yds Allow Neg": 0.0,
    "position_type": "DT", "Pts Allow 7-13": 0.0, "Yds Allow 0-99": 0.0,
    "Yds Allow 500+": 0.0, "Pts Allow 14-20": 0.0, "Pts Allow 21-27": 1.0,
    "Pts Allow 28-34": 0.0, "Yds Allow 100-199": 0.0, "Yds Allow 200-299": 0.0,
    "Yds Allow 300-399": 1.0, "Yds Allow 400-499": 0.0,
}


def test_def_mapping_uses_raw_values():
    line = stat_line_from_yahoo(DEF_PAYLOAD)
    assert line.points_allowed == 27.0
    assert line.yards_allowed == 394.0
    assert line.tackles_for_loss == 6.0
    assert line.def_interceptions == 0.0


def test_def_tier_indicator_cross_check_fails_on_mismatch():
    bad = dict(DEF_PAYLOAD, **{"Pts Allow 21-27": 0.0, "Pts Allow 14-20": 1.0})
    with pytest.raises(IngestError, match="tier indicator"):
        stat_line_from_yahoo(bad)


K_PAYLOAD = {
    "name": "Brandon Aubrey", "FG 50+": 1.0, "FG 0-19": 0.0, "FG 20-29": 0.0,
    "FG 30-39": 0.0, "FG 40-49": 1.0, "FGM 0-19": 0.0, "PAT Made": 2.0,
    "PAT Miss": 0.0, "FGM 20-29": 0.0, "FGM 30-39": 0.0, "player_id": 40819,
    "total_points": "11.00", "position_type": "K",
}


def test_kicker_mapping():
    line = stat_line_from_yahoo(K_PAYLOAD)
    assert line.fg_50_plus == 1.0
    assert line.fg_40_49 == 1.0
    assert line.pat_made == 2.0
    assert line.fg_miss_0_19 == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_yahoo_adapter.py -q`
Expected: FAIL — no module `ffi.scoring.yahoo_adapter`

- [ ] **Step 3: Implement `src/ffi/scoring/yahoo_adapter.py`**

```python
"""raw.yahoo_player_week.stats (display-name keys) -> StatLine.
Dispatch on position_type: 'O' offense, 'K' kicker, 'DT' team defense.
Unknown keys are schema drift and fail loud (ADR Domain 1)."""
from ffi.ingest.base import IngestError
from ffi.scoring.statline import StatLine

_META_KEYS = {"name", "player_id", "total_points", "position_type"}

_OFFENSE_MAP = {
    "Comp": "pass_completions", "Inc": "pass_incompletions",
    "Pass Yds": "pass_yards", "Pass TD": "pass_tds", "Int": "interceptions",
    "Pick Six": "pick_sixes", "Rush Att": "rush_attempts",
    "Rush Yds": "rush_yards", "Rush TD": "rush_tds",
    "Rush 1st Downs": "rush_first_downs", "Rec": "receptions",
    "Rec Yds": "rec_yards", "Rec TD": "rec_tds",
    "Rec 1st Downs": "rec_first_downs", "Ret Yds": "return_yards",
    "Ret TD": "return_tds", "2-PT": "two_point_conversions",
    "Fum": "fumbles", "Fum Lost": "fumbles_lost",
    "Fum Ret TD": "offensive_fumble_return_tds",
}
_OFFENSE_IGNORED = {"Targets"}  # informational; not scored

_K_MAP = {
    "FG 0-19": "fg_0_19", "FG 20-29": "fg_20_29", "FG 30-39": "fg_30_39",
    "FG 40-49": "fg_40_49", "FG 50+": "fg_50_plus",
    "FGM 0-19": "fg_miss_0_19", "FGM 20-29": "fg_miss_20_29",
    "FGM 30-39": "fg_miss_30_39", "PAT Made": "pat_made", "PAT Miss": "pat_missed",
}

_DEF_MAP = {
    "Sack": "sacks", "Int": "def_interceptions", "Fum Rec": "fumble_recoveries",
    "TD": "defensive_tds", "Safe": "safeties", "Blk Kick": "blocked_kicks",
    "4 Dwn Stops": "fourth_down_stops", "TFL": "tackles_for_loss",
    "3 and Outs": "three_and_outs", "XPR": "extra_point_returns",
    "Pts Allow": "points_allowed", "Def Yds Allow": "yards_allowed",
}
# One-hot tier indicators: not mapped (engine computes tiers from raw values)
# but cross-checked below — a free consistency test on every DEF row.
_DEF_PTS_INDICATORS = {
    "Pts Allow 0": (None, 0), "Pts Allow 1-6": (1, 6), "Pts Allow 7-13": (7, 13),
    "Pts Allow 14-20": (14, 20), "Pts Allow 21-27": (21, 27),
    "Pts Allow 28-34": (28, 34), "Pts Allow 35+": (35, None),
}
_DEF_YDS_INDICATORS = {
    "Yds Allow Neg": (None, -1), "Yds Allow 0-99": (0, 99),
    "Yds Allow 100-199": (100, 199), "Yds Allow 200-299": (200, 299),
    "Yds Allow 300-399": (300, 399), "Yds Allow 400-499": (400, 499),
    "Yds Allow 500+": (500, None),
}

_DISPATCH = {
    "O": (_OFFENSE_MAP, _OFFENSE_IGNORED),
    "K": (_K_MAP, set()),
    "DT": (_DEF_MAP, set(_DEF_PTS_INDICATORS) | set(_DEF_YDS_INDICATORS)),
}


def _check_indicators(stats: dict, raw_key: str, indicators: dict) -> None:
    value = stats[raw_key]
    for ind_key, (lo, hi) in indicators.items():
        if ind_key not in stats:
            continue
        expected = 1.0 if ((lo is None or value >= lo) and (hi is None or value <= hi)) else 0.0
        if float(stats[ind_key]) != expected:
            raise IngestError(
                f"DEF tier indicator mismatch: {raw_key}={value} but "
                f"{ind_key}={stats[ind_key]} (expected {expected}) — payload inconsistent"
            )


def stat_line_from_yahoo(stats: dict) -> StatLine:
    if "position_type" not in stats:
        raise IngestError(f"yahoo stats payload missing position_type: {sorted(stats)[:20]}")
    ptype = stats["position_type"]
    if ptype not in _DISPATCH:
        raise IngestError(f"unknown position_type {ptype!r} — extend the adapter deliberately")
    key_map, ignored = _DISPATCH[ptype]
    unknown = set(stats) - set(key_map) - ignored - _META_KEYS
    if unknown:
        raise IngestError(
            f"yahoo stats payload has unmapped keys {sorted(unknown)} for "
            f"position_type={ptype} — schema drift; map or ignore explicitly"
        )
    fields = {f: float(stats[k]) for k, f in key_map.items() if k in stats}
    if ptype == "DT":
        _check_indicators(stats, "Pts Allow", _DEF_PTS_INDICATORS)
        _check_indicators(stats, "Def Yds Allow", _DEF_YDS_INDICATORS)
    return StatLine(**fields)
```

- [ ] **Step 4: Run adapter tests**

Run: `uv run pytest tests/test_yahoo_adapter.py -q`
Expected: all PASS

- [ ] **Step 5: Write `scripts/make_golden_fixtures.py`** (deliberate edge-case selection; committed output)

```python
#!/usr/bin/env python3
"""Select ~40 golden fixtures from raw.yahoo_player_week (2025) covering every
edge class, and write tests/fixtures/golden_2025.json. Deterministic: ordered
picks per class, no randomness."""
import json
import pathlib

from ffi.db import connect

OUT = pathlib.Path("tests/fixtures/golden_2025.json")

# (label, where-clause, order, limit) against 2025 offense/K/DEF rows.
CLASSES = [
    ("rec_200_plus",  "(stats->>'Rec Yds')::float >= 200", "total_points DESC", 2),
    ("rush_200_plus", "(stats->>'Rush Yds')::float >= 200", "total_points DESC", 2),
    ("rec_150_band",  "(stats->>'Rec Yds')::float BETWEEN 150 AND 199", "total_points DESC", 3),
    ("rush_150_band", "(stats->>'Rush Yds')::float BETWEEN 150 AND 199", "total_points DESC", 3),
    ("rec_100_band",  "(stats->>'Rec Yds')::float BETWEEN 100 AND 149", "total_points DESC", 2),
    ("pass_300_band", "(stats->>'Pass Yds')::float BETWEEN 300 AND 399", "total_points DESC", 2),
    ("pass_400_plus", "(stats->>'Pass Yds')::float >= 400", "total_points DESC", 2),
    ("pass_500_plus", "(stats->>'Pass Yds')::float >= 500", "total_points DESC", 2),
    ("pick_six",      "(stats->>'Pick Six')::float > 0", "total_points ASC", 3),
    ("negative_total", "total_points < 0", "total_points ASC", 3),
    ("return_yards",  "(stats->>'Ret Yds')::float > 0", "(stats->>'Ret Yds')::float DESC", 3),
    ("two_point",     "(stats->>'2-PT')::float > 0", "total_points DESC", 2),
    ("fum_ret_td",    "(stats->>'Fum Ret TD')::float > 0", "total_points DESC", 1),
    ("zero_line",     "total_points = 0", "yahoo_player_id", 2),
    ("kicker_50_plus", "stats->>'position_type'='K' AND (stats->>'FG 50+')::float > 0",
     "total_points DESC", 2),
    ("kicker_misses", "stats->>'position_type'='K' AND ((stats->>'FGM 20-29')::float > 0 "
     "OR (stats->>'FGM 30-39')::float > 0 OR (stats->>'PAT Miss')::float > 0)",
     "total_points ASC", 2),
    ("def_shutout_or_low", "stats->>'position_type'='DT' AND (stats->>'Pts Allow')::float <= 6",
     "total_points DESC", 2),
    ("def_high_allowed", "stats->>'position_type'='DT' AND (stats->>'Pts Allow')::float >= 35",
     "total_points ASC", 2),
    ("def_stat_stack", "stats->>'position_type'='DT' AND (stats->>'TFL')::float >= 5",
     "total_points DESC", 2),
]

conn = connect()
fixtures, seen = [], set()
for label, where, order, limit in CLASSES:
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT league_key, week, yahoo_player_id, stats->>'name',
                       total_points::text, stats
                FROM raw.yahoo_player_week
                WHERE season = 2025 AND {where}
                ORDER BY {order} LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
    if not rows:
        print(f"  NOTE: class {label!r} has no 2025 examples (acceptable for 500+ pass)")
    for lk, wk, pid, name, tp, stats in rows:
        key = (lk, wk, pid)
        if key in seen:
            continue
        seen.add(key)
        fixtures.append({"class": label, "league_key": lk, "week": wk,
                         "yahoo_player_id": pid, "name": name,
                         "total_points": tp, "stats": stats})

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(fixtures, indent=1, sort_keys=True))
print(f"{len(fixtures)} golden fixtures -> {OUT}")
if len(fixtures) < 35:
    raise SystemExit(f"only {len(fixtures)} fixtures — expected ~40; check class queries")
```

- [ ] **Step 6: Generate fixtures + write the golden test**

Run: `uv run python scripts/make_golden_fixtures.py`
Expected: `~40 golden fixtures -> tests/fixtures/golden_2025.json` (empty classes noted).

`tests/test_golden_yahoo.py`:

```python
"""THE golden gate (R1, ADR Domain 7): engine output must EXACTLY equal
Yahoo's official points for every committed fixture."""
import json
import pathlib
from decimal import Decimal

import pytest

from ffi.scoring.config import load_config_v1
from ffi.scoring.engine import score_stat_line
from ffi.scoring.yahoo_adapter import stat_line_from_yahoo

FIXTURES = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "golden_2025.json").read_text()
)
CFG = load_config_v1()


@pytest.mark.parametrize(
    "fx", FIXTURES, ids=[f"{f['class']}-{f['name']}-wk{f['week']}" for f in FIXTURES]
)
def test_golden_exact_match(fx):
    line = stat_line_from_yahoo(fx["stats"])
    got = score_stat_line(line, CFG)
    assert got == Decimal(fx["total_points"]), (
        f"{fx['name']} wk{fx['week']}: engine={got} yahoo={fx['total_points']}"
    )
```

Run: `uv run pytest tests/test_golden_yahoo.py -q`
Expected: **all PASS, exact match.** Any failure = either an adapter gap (unmapped key — the error says so) or an encoding bug: debug via `score_components` breakdown vs the payload; do NOT loosen the assertion, do NOT add tolerance.

- [ ] **Step 7: Write `scripts/score_sweep_yahoo.py`** (all 3,876 rows: total validation + persistence)

```python
#!/usr/bin/env python3
"""Score EVERY raw.yahoo_player_week row with the engine and compare to
Yahoo's official total exactly. Persists matches into scoring.player_week_points
(source='yahoo_engine'). Exit code = number of mismatched rows."""
from decimal import Decimal
import json

from ffi.db import connect
from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.scoring.engine import score_components
from ffi.scoring.yahoo_adapter import stat_line_from_yahoo

cfg = load_config_v1()
conn = connect()
ensure_config_in_db(conn, cfg)

with conn.cursor() as cur:
    cur.execute(
        """SELECT league_key, season, week, yahoo_player_id, total_points::text, stats
           FROM raw.yahoo_player_week ORDER BY week, yahoo_player_id"""
    )
    rows = cur.fetchall()

mismatches = []
with conn.cursor() as cur:
    for lk, season, week, pid, tp, stats in rows:
        comps = score_components(stat_line_from_yahoo(stats), cfg)
        got = sum(comps.values(), Decimal("0"))
        if tp is None or got != Decimal(tp):
            mismatches.append((stats.get("name"), week, pid, str(got), tp))
            continue
        cur.execute(
            """INSERT INTO scoring.player_week_points
               (source, player_ref, season, week, config_version, points, components)
               VALUES ('yahoo_engine', %s, %s, %s, %s, %s, %s)
               ON CONFLICT (source, player_ref, season, week, config_version)
               DO UPDATE SET points=EXCLUDED.points, components=EXCLUDED.components,
                             computed_at=now()""",
            (pid, season, week, cfg.version, got,
             json.dumps({k: str(v) for k, v in comps.items()})),
        )
conn.commit()
print(f"{len(rows)} rows scored; {len(mismatches)} mismatches")
for m in mismatches[:40]:
    print("  MISMATCH:", m)
raise SystemExit(len(mismatches))
```

- [ ] **Step 8: Run the sweep to a zero-mismatch state**

Run: `uv run python scripts/score_sweep_yahoo.py`
Expected: `3876 rows scored; 0 mismatches`, exit 0. An unmapped-key `IngestError` mid-sweep means a stat we haven't seen (map it deliberately in the adapter + add a fixture for it). A numeric mismatch means an encoding bug — fix the engine/config, never special-case a row. Iterate until zero.

Verify persistence: `psql -d fantasy_football -c "SELECT count(*) FROM scoring.player_week_points WHERE source='yahoo_engine'"` → 3876.

- [ ] **Step 9: Full suite + commit + checkpoint declaration**

Run: `uv run pytest -q` — all PASS.

```bash
git add src/ffi/scoring/yahoo_adapter.py scripts/make_golden_fixtures.py \
  scripts/score_sweep_yahoo.py tests/fixtures/golden_2025.json \
  tests/test_golden_yahoo.py tests/test_yahoo_adapter.py
git commit -m "feat: yahoo adapter + golden tests exact-match vs official 2025 points (R1 gate green)"
```

Task report MUST state: "Week-3 checkpoint (R3) artifact delivered: scoring engine golden-tested, N/N exact."

---

### Task 6: nflverse widening + adapter + historical scoring + divergence audit

Score 2019–2025 history under league rules (powers backtests, mining, streaming check). Requires widening `raw.nflverse_player_week` first: the league scores stats we don't currently store (total fumbles at −1, 2-pt conversions, return TDs).

**Files:**
- Create: `migrations/003_nflverse_widen.sql`
- Modify: `src/ffi/ingest/nflverse.py` (extend COLUMN_MAP/DERIVED_SUMS)
- Create: `src/ffi/scoring/nflverse_adapter.py`
- Create: `scripts/score_nflverse_history.py`
- Create: `scripts/audit_nflverse_vs_yahoo.py`
- Test: `tests/test_nflverse_adapter.py`, extend `tests/test_nflverse_ingest.py`

**Interfaces:**
- Consumes: `COLUMN_MAP`/`DERIVED_SUMS` (Task 1), engine + config (Tasks 3–4), `v_player_yahoo_ids` + crosswalk (gsis↔yahoo)
- Produces: `ffi.scoring.nflverse_adapter.stat_line_from_nflverse(row: dict) -> StatLine`; `ffi.scoring.nflverse_adapter.KNOWN_GAPS: dict[str, str]` (stats the source cannot provide); `scoring.player_week_points` rows with `source='nflverse'` for 2019–2025

- [ ] **Step 1: Verify the real source column names before touching code**

Run:
```bash
uv run python -c "import nflreadpy; cols=sorted(nflreadpy.load_player_stats(seasons=[2025]).columns); print('\n'.join(cols))"
```
Confirm these exist (Phase 1 taught us not to trust guessed names): `rushing_fumbles`, `receiving_fumbles`, `sack_fumbles`, `passing_2pt_conversions`, `rushing_2pt_conversions`, `receiving_2pt_conversions`, `special_teams_tds`. If any name differs, use the actual name in the steps below and record the correction in the task report. If a column is genuinely absent, STOP and report — do not fake it with zeros.

- [ ] **Step 2: Write `migrations/003_nflverse_widen.sql` and apply it**

```sql
-- Phase 2 Task 6: columns the league scores that Phase 1 didn't store.
ALTER TABLE raw.nflverse_player_week
    ADD COLUMN IF NOT EXISTS fumbles           INTEGER,  -- all fumbles (league: -1 each)
    ADD COLUMN IF NOT EXISTS two_point_conversions INTEGER,
    ADD COLUMN IF NOT EXISTS special_teams_tds INTEGER;  -- return-TD proxy
```

Run: `psql -d fantasy_football -f migrations/003_nflverse_widen.sql`

- [ ] **Step 3: Extend the column map + re-ingest**

In `src/ffi/ingest/nflverse.py`, extend the structures (this is the whole point of Task 1's consolidation — one edit site):

```python
DERIVED_SUMS: dict[str, list[str]] = {
    "fumbles_lost": ["rushing_fumbles_lost", "receiving_fumbles_lost", "sack_fumbles_lost"],
    "fumbles": ["rushing_fumbles", "receiving_fumbles", "sack_fumbles"],
    "two_point_conversions": [
        "passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions"
    ],
}
# and append to COLUMN_MAP:
#   ("special_teams_tds", "special_teams_tds"),
```

Extend `tests/test_nflverse_ingest.py`'s canned DataFrame with the new source columns (zeros are fine) and assert the new DB columns land. Run: `uv run pytest tests/test_nflverse_ingest.py -q` → PASS.

Re-ingest (local parquet, no API budget): `uv run python scripts/ingest_nflverse.py` (check its CLI: Phase 1 loads 2019–2025). Verify:
`psql -d fantasy_football -c "SELECT count(*), sum(two_point_conversions), sum(fumbles) FROM raw.nflverse_player_week"` → ~129,809 rows, nonzero sums.

- [ ] **Step 4: Write failing adapter tests**

`tests/test_nflverse_adapter.py`:

```python
from ffi.scoring.nflverse_adapter import KNOWN_GAPS, stat_line_from_nflverse

ROW = {
    "gsis_id": "00-0039075", "season": 2025, "week": 16, "player_name": "X",
    "position": "WR", "team": "BAL", "completions": 0, "attempts": 0,
    "passing_yards": 0.0, "passing_tds": 0, "passing_first_downs": 0,
    "interceptions": 0, "carries": 2, "rushing_yards": 8.0, "rushing_tds": 0,
    "rushing_first_downs": 0, "receptions": 7, "targets": 9,
    "receiving_yards": 143.0, "receiving_tds": 1, "receiving_first_downs": 5,
    "punt_return_yards": 12.0, "kickoff_return_yards": 20.0, "fumbles_lost": 0,
    "fumbles": 1, "two_point_conversions": 0, "special_teams_tds": 0,
}


def test_mapping_and_derivations():
    line = stat_line_from_nflverse(ROW)
    assert line.receptions == 7
    assert line.rec_first_downs == 5
    assert line.rush_attempts == 2
    assert line.pass_incompletions == 0          # attempts - completions
    assert line.return_yards == 32.0             # punt + kickoff
    assert line.return_tds == 0
    assert line.fumbles == 1


def test_incompletions_derived():
    row = dict(ROW, attempts=30, completions=20)
    assert stat_line_from_nflverse(row).pass_incompletions == 10


def test_known_gaps_documented():
    line = stat_line_from_nflverse(ROW)
    assert line.pick_sixes is None               # not in nflverse
    assert "pick_sixes" in KNOWN_GAPS
    assert "offensive_fumble_return_tds" in KNOWN_GAPS
```

Run: `uv run pytest tests/test_nflverse_adapter.py -q` → FAIL (module missing).

- [ ] **Step 5: Implement `src/ffi/scoring/nflverse_adapter.py`**

```python
"""raw.nflverse_player_week row (dict) -> StatLine.

KNOWN_GAPS: league-scored stats nflverse does not carry. They stay None in the
StatLine (None = source lacks the stat) and every consumer of nflverse-scored
points inherits the documented bias below — see the divergence audit."""
from ffi.ingest.base import IngestError
from ffi.scoring.statline import StatLine

KNOWN_GAPS = {
    "pick_sixes": "not in nflverse player stats; league -4 each; rare (~1 QB-week in ~60)",
    "offensive_fumble_return_tds": "not in nflverse; league +6; very rare",
    "return_tds": "approximated by special_teams_tds (includes all ST TDs)",
}

_REQUIRED = (
    "completions", "attempts", "passing_yards", "passing_tds", "interceptions",
    "carries", "rushing_yards", "rushing_tds", "rushing_first_downs",
    "receptions", "receiving_yards", "receiving_tds", "receiving_first_downs",
    "passing_first_downs", "punt_return_yards", "kickoff_return_yards",
    "fumbles", "fumbles_lost", "two_point_conversions", "special_teams_tds",
)


def stat_line_from_nflverse(row: dict) -> StatLine:
    missing = [k for k in _REQUIRED if k not in row]
    if missing:
        raise IngestError(f"nflverse row missing columns {missing} — re-ingest after Task 6 Step 3?")

    def n(key):  # nflverse uses NULLs for not-applicable; treat as 0 (observed zero-stat week)
        v = row[key]
        return 0.0 if v is None else float(v)

    return StatLine(
        pass_completions=n("completions"),
        pass_incompletions=n("attempts") - n("completions"),
        pass_yards=n("passing_yards"),
        pass_tds=n("passing_tds"),
        interceptions=n("interceptions"),
        rush_attempts=n("carries"),
        rush_yards=n("rushing_yards"),
        rush_tds=n("rushing_tds"),
        rush_first_downs=n("rushing_first_downs"),
        receptions=n("receptions"),
        rec_yards=n("receiving_yards"),
        rec_tds=n("receiving_tds"),
        rec_first_downs=n("receiving_first_downs"),
        return_yards=n("punt_return_yards") + n("kickoff_return_yards"),
        return_tds=n("special_teams_tds"),
        two_point_conversions=n("two_point_conversions"),
        fumbles=n("fumbles"),
        fumbles_lost=n("fumbles_lost"),
        # pick_sixes / offensive_fumble_return_tds: KNOWN_GAPS — stay None.
    )
```

Note: `passing_first_downs` is validated present but **deliberately not scored** — the league has no passing-FD category (only rush/rec FD). It feeds Task 8's imputation instead.

Run: `uv run pytest tests/test_nflverse_adapter.py -q` → PASS.

- [ ] **Step 6: Write + run `scripts/score_nflverse_history.py`**

```python
#!/usr/bin/env python3
"""Score all raw.nflverse_player_week rows (2019-2025) under config v1 into
scoring.player_week_points (source='nflverse'). Recomputable; idempotent upsert."""
import json

import psycopg2.extras

from ffi.db import connect
from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.scoring.engine import score_components
from ffi.scoring.nflverse_adapter import stat_line_from_nflverse

cfg = load_config_v1()
conn = connect()
ensure_config_in_db(conn, cfg)

with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute("SELECT * FROM raw.nflverse_player_week")
    rows = cur.fetchall()

out = []
for row in rows:
    comps = score_components(stat_line_from_nflverse(dict(row)), cfg)
    out.append((
        "nflverse", row["gsis_id"], row["season"], row["week"], cfg.version,
        sum(comps.values()), json.dumps({k: str(v) for k, v in comps.items()}),
    ))

with conn.cursor() as cur:
    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO scoring.player_week_points
           (source, player_ref, season, week, config_version, points, components)
           VALUES %s
           ON CONFLICT (source, player_ref, season, week, config_version)
           DO UPDATE SET points=EXCLUDED.points, components=EXCLUDED.components, computed_at=now()""",
        out, page_size=5000,
    )
conn.commit()
print(f"scored {len(out)} nflverse player-weeks under config v{cfg.version}")
```

Run: `uv run python scripts/score_nflverse_history.py`
Expected: `scored ~129809 nflverse player-weeks under config v1` (a few minutes).

- [ ] **Step 7: Write + run the cross-source divergence audit**

`scripts/audit_nflverse_vs_yahoo.py`:

```python
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
    raise SystemExit("no joined rows — crosswalk or scoring tables empty; fix before auditing")

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
    "| player | week | nflverse | yahoo | diff |", "|---|---|---|---|---|",
    *[f"| {n} | {w} | {a} | {b} | {d} |" for n, w, a, b, d in worst],
]
report.write_text("\n".join(lines) + "\n")
print(f"median |diff|={median:.3f} p95={p95:.3f} -> {report}")
if median > 0.5 or p95 > 3.0:
    raise SystemExit(
        f"divergence above expectation (median>{0.5} or p95>{3.0}) — investigate the "
        "top-30 table before building on nflverse-scored history (R16 discipline)."
    )
```

Run: `uv run python scripts/audit_nflverse_vs_yahoo.py`
Expected: report written, thresholds met. If thresholds breach: inspect the worst rows' components side by side (common suspects: FD counting differences, return-yard attribution, the KNOWN_GAPS). This is an investigation gate, not a tolerance to widen.

- [ ] **Step 8: Full suite + commit**

```bash
uv run pytest -q
git add migrations/003_nflverse_widen.sql src/ffi/ingest/nflverse.py \
  src/ffi/scoring/nflverse_adapter.py scripts/score_nflverse_history.py \
  scripts/audit_nflverse_vs_yahoo.py tests/test_nflverse_adapter.py \
  tests/test_nflverse_ingest.py docs/research/
git commit -m "feat: nflverse adapter + 2019-2025 history scored under league rules + divergence audit"
```

---

### Task 7: Sleeper adapter + season-level snapshot path + projection scoring

The scoring engine works on **season stat lines** for draft valuation, and the season-level Sleeper path (`--week` omitted) is untested (fact #8). Test it live FIRST, then adapt and score. Also upgrades the Phase-1 union FD check to per-position validation (carry-forward).

**Files:**
- Create: `src/ffi/scoring/sleeper_adapter.py`
- Create: `scripts/score_sleeper_projections.py`
- Modify: `src/ffi/ingest/sleeper.py` (per-position FD validation)
- Test: `tests/test_sleeper_adapter.py`, extend `tests/test_sleeper_ingest.py`

**Interfaces:**
- Consumes: engine + config; `raw.sleeper_projections` payload records (`{"player_id", "stats": {...}, "player": {"position": ...}}`)
- Produces: `ffi.scoring.sleeper_adapter.stat_line_from_sleeper(record: dict) -> StatLine`; `scoring.projection_points` rows (`source='sleeper'`, `horizon='season'` or `'week:N'`)

- [ ] **Step 1: Probe the season-level shape live, then snapshot it**

Run: `uv run python scripts/ingest_sleeper.py --season 2026 --inspect`
(no `--week` → the untested season-level URL). Inspect the printed record: confirm `stats` and `player_id` exist and note which of the week-level keys appear (`week` may be absent/null at season level — that's fine, the ingester doesn't require it).

Then snapshot for real: `uv run python scripts/ingest_sleeper.py --season 2026`
Expected: `OK run_id=N`. Verify: `psql -d fantasy_football -c "SELECT snapshot_id, season, week, jsonb_array_length(payload) FROM raw.sleeper_projections ORDER BY snapshot_id"` → a new row with `week` NULL.
If the fetch or validation fails, STOP: this is the design's projection backbone for draft valuation — report the actual payload shape to the user before adapting anything.

- [ ] **Step 2: Extract the full stat-key vocabulary (drives the adapter's allowlist)**

```bash
psql -d fantasy_football -t -A -c "
SELECT DISTINCT k FROM raw.sleeper_projections p,
  jsonb_array_elements(p.payload) rec, jsonb_object_keys(rec->'stats') k
WHERE p.week IS NULL ORDER BY 1;"
```
Save the list into the task notes. Every key must end up in exactly one of: `_SLEEPER_MAP`, `_IGNORED_EXACT`, or match `_IGNORED_PREFIXES` below — that's what the ingest-time drift guard enforces.

- [ ] **Step 3: Write failing adapter tests**

`tests/test_sleeper_adapter.py`:

```python
import pytest

from ffi.ingest.base import IngestError
from ffi.scoring.sleeper_adapter import stat_line_from_sleeper

QB_REC = {
    "player_id": "4943",
    "player": {"position": "QB"},
    "stats": {
        "gp": 17.0, "pass_att": 550.0, "pass_cmp": 350.0, "pass_inc": 200.0,
        "pass_yd": 4100.0, "pass_td": 28.0, "pass_int": 11.0, "pass_int_td": 1.0,
        "pass_fd": 190.0, "pass_2pt": 1.0, "rush_att": 45.0, "rush_yd": 220.0,
        "rush_td": 2.0, "rush_fd": 14.0, "rush_2pt": 0.0, "fum": 6.0,
        "fum_lost": 3.0, "cmp_pct": 63.6, "pts_ppr": 400.0, "pts_std": 400.0,
        "pts_half_ppr": 400.0, "adp_dd_ppr": 30.0, "pos_adp_dd_ppr": 4.0,
        "pass_sack": 30.0, "pass_cmp_40p": 8.0, "rush_40p": 0.0, "def_fum_td": 0.0,
    },
}


def test_qb_mapping():
    line = stat_line_from_sleeper(QB_REC)
    assert line.pass_completions == 350.0
    assert line.pass_incompletions == 200.0
    assert line.pick_sixes == 1.0            # pass_int_td
    assert line.interceptions == 11.0
    assert line.two_point_conversions == 1.0  # pass_2pt + rush_2pt + rec_2pt
    assert line.rush_first_downs == 14.0
    assert line.receptions is None            # absent for this QB record


def test_unknown_stat_key_fails_loud():
    rec = {**QB_REC, "stats": {**QB_REC["stats"], "brand_new_stat": 1.0}}
    with pytest.raises(IngestError, match="unmapped"):
        stat_line_from_sleeper(rec)


def test_missing_stats_fails_loud():
    with pytest.raises(IngestError):
        stat_line_from_sleeper({"player_id": "1", "player": {"position": "QB"}})
```

Run: `uv run pytest tests/test_sleeper_adapter.py -q` → FAIL.

- [ ] **Step 4: Implement `src/ffi/scoring/sleeper_adapter.py`**

```python
"""Sleeper projection record -> StatLine. Allowlist mapping: every stats key
must be mapped, exact-ignored, or prefix-ignored — anything else is schema
drift and fails loud (R5: silent semantic drift is the worst case)."""
from ffi.ingest.base import IngestError
from ffi.scoring.statline import StatLine

_SLEEPER_MAP = {
    "pass_cmp": "pass_completions", "pass_inc": "pass_incompletions",
    "pass_yd": "pass_yards", "pass_td": "pass_tds", "pass_int": "interceptions",
    "pass_int_td": "pick_sixes", "rush_att": "rush_attempts",
    "rush_yd": "rush_yards", "rush_td": "rush_tds", "rush_fd": "rush_first_downs",
    "rec": "receptions", "rec_yd": "rec_yards", "rec_td": "rec_tds",
    "rec_fd": "rec_first_downs", "fum": "fumbles", "fum_lost": "fumbles_lost",
    "pr_yd": "return_yards_punt", "kr_yd": "return_yards_kick",  # summed below
    "st_td": "return_tds",
    # kickers (verify against Step 2 vocabulary; extend deliberately)
    "fgm_0_19": "fg_0_19", "fgm_20_29": "fg_20_29", "fgm_30_39": "fg_30_39",
    "fgm_40_49": "fg_40_49", "fgm_50p": "fg_50_plus",
    "fgmiss_0_19": "fg_miss_0_19", "fgmiss_20_29": "fg_miss_20_29",
    "fgmiss_30_39": "fg_miss_30_39", "xpm": "pat_made", "xpmiss": "pat_missed",
}
_TWO_PT_KEYS = ("pass_2pt", "rush_2pt", "rec_2pt")
_IGNORED_EXACT = {
    "gp", "cmp_pct", "pass_att", "pass_sack", "rec_tgt", "fgm", "fga", "xpa",
    "def_fum_td",
}
_IGNORED_PREFIXES = (
    "pts_", "adp_", "pos_adp_", "bonus_", "rec_0_", "rec_5_", "rec_10_",
    "rec_20_", "rec_30_", "rec_40", "rush_40", "pass_cmp_40", "fgm_yds",
    "idp_", "def_", "gms_",
)


def _classify(key: str) -> str | None:
    if key in _SLEEPER_MAP or key in _TWO_PT_KEYS or key in _IGNORED_EXACT:
        return "known"
    if any(key.startswith(p) for p in _IGNORED_PREFIXES):
        return "known"
    return None


def stat_line_from_sleeper(record: dict) -> StatLine:
    if "stats" not in record or not isinstance(record["stats"], dict):
        raise IngestError(f"sleeper record missing stats dict: {str(record)[:200]}")
    stats = record["stats"]
    unknown = [k for k in stats if _classify(k) is None]
    if unknown:
        raise IngestError(
            f"sleeper stats has unmapped keys {sorted(unknown)} — schema drift; "
            "map or ignore explicitly (never silently)"
        )
    fields: dict[str, float] = {}
    for k, f in _SLEEPER_MAP.items():
        if k in stats and f not in ("return_yards_punt", "return_yards_kick"):
            fields[f] = float(stats[k])
    ret = sum(float(stats[k]) for k in ("pr_yd", "kr_yd") if k in stats)
    if "pr_yd" in stats or "kr_yd" in stats:
        fields["return_yards"] = ret
    two = sum(float(stats[k]) for k in _TWO_PT_KEYS if k in stats)
    if any(k in stats for k in _TWO_PT_KEYS):
        fields["two_point_conversions"] = two
    return StatLine(**fields)
```

The kicker key names above are **best-guess placeholders until Step 2's vocabulary confirms them** — reconcile the map against the actual key list before finishing this step (the drift guard makes any miss loud, not silent).

Run: `uv run pytest tests/test_sleeper_adapter.py -q` → PASS.

- [ ] **Step 5: Per-position FD validation in the ingester (carry-forward)**

Replace the union check in `SleeperProjectionsIngester.validate` (keep the empty/`stats`-missing checks):

```python
    _FD_BY_POSITION = {"QB": "pass_fd", "RB": "rush_fd", "WR": "rec_fd", "TE": "rec_fd"}

    def validate(self, payload) -> int:
        if not isinstance(payload, list) or not payload:
            raise IngestError(f"sleeper: empty or non-list payload: {str(payload)[:200]}")
        counts = {pos: [0, 0] for pos in self._FD_BY_POSITION}  # [with_fd, total]
        for rec in payload:
            if "stats" not in rec or "player_id" not in rec:
                raise IngestError(
                    f"sleeper: record missing 'stats'/'player_id' — schema drift? {str(rec)[:300]}"
                )
            pos = (rec.get("player") or {}).get("position")
            if pos in counts:
                counts[pos][1] += 1
                if self._FD_BY_POSITION[pos] in rec["stats"]:
                    counts[pos][0] += 1
        for pos, (with_fd, total) in counts.items():
            if total and with_fd / total < 0.5:
                raise IngestError(
                    f"sleeper: {self._FD_BY_POSITION[pos]} present in only {with_fd}/{total} "
                    f"{pos} records — partial FD drift breaks FD pricing (design 4.2/R5)."
                )
        return len(payload)
```

(0.5 not 0.9: deep-roster players legitimately project 0 volume and may omit keys; a systemic drop still trips. Judgement call — document it.) Extend `tests/test_sleeper_ingest.py`: one test where all QBs lack `pass_fd` → raises; one where records lack `player` position → not counted, still passes.

Run: `uv run pytest tests/test_sleeper_ingest.py -q` → PASS.

- [ ] **Step 6: Score the season-level snapshot into `scoring.projection_points`**

`scripts/score_sleeper_projections.py`:

```python
#!/usr/bin/env python3
"""Score a Sleeper snapshot's records under the scoring config into
scoring.projection_points. Default: the latest season-level snapshot."""
import argparse
import json

import psycopg2.extras

from ffi.db import connect
from ffi.scoring.config import ensure_config_in_db, load_config_v1
from ffi.scoring.engine import score_components
from ffi.scoring.sleeper_adapter import stat_line_from_sleeper

ap = argparse.ArgumentParser()
ap.add_argument("--snapshot-id", type=int, default=None, help="default: latest week-NULL snapshot")
args = ap.parse_args()

cfg = load_config_v1()
conn = connect()
ensure_config_in_db(conn, cfg)
with conn.cursor() as cur:
    if args.snapshot_id is None:
        cur.execute(
            "SELECT snapshot_id, season, week FROM raw.sleeper_projections "
            "WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1"
        )
    else:
        cur.execute(
            "SELECT snapshot_id, season, week FROM raw.sleeper_projections WHERE snapshot_id=%s",
            (args.snapshot_id,),
        )
    row = cur.fetchone()
    if row is None:
        raise SystemExit("no matching sleeper snapshot — run scripts/ingest_sleeper.py first")
    snapshot_id, season, week = row
    cur.execute("SELECT payload FROM raw.sleeper_projections WHERE snapshot_id=%s", (snapshot_id,))
    payload = cur.fetchone()[0]

horizon = "season" if week is None else f"week:{week}"
out = []
for rec in payload:
    comps = score_components(stat_line_from_sleeper(rec), cfg)
    out.append((
        "sleeper", snapshot_id, str(rec["player_id"]), horizon, cfg.version,
        sum(comps.values()), json.dumps({k: str(v) for k, v in comps.items()}),
    ))
with conn.cursor() as cur:
    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO scoring.projection_points
           (source, snapshot_id, player_ref, horizon, config_version, points, components)
           VALUES %s
           ON CONFLICT (source, snapshot_id, player_ref, config_version)
           DO UPDATE SET points=EXCLUDED.points, components=EXCLUDED.components, computed_at=now()""",
        out, page_size=2000,
    )
conn.commit()
print(f"scored {len(out)} records from snapshot {snapshot_id} ({season} {horizon}) under v{cfg.version}")
```

Run: `uv run python scripts/score_sleeper_projections.py`
Expected: ~3,300 records scored. Sanity: `psql -d fantasy_football -c "SELECT p.player_ref, p.points FROM scoring.projection_points p ORDER BY p.points DESC LIMIT 10"` — the top should be QBs (2QB league scoring loves completions+6pt TDs); eyeball for sanity, report the top-10 in the task report.

- [ ] **Step 7: Full suite + commit**

```bash
uv run pytest -q
git add src/ffi/scoring/sleeper_adapter.py scripts/score_sleeper_projections.py \
  src/ffi/ingest/sleeper.py tests/test_sleeper_adapter.py tests/test_sleeper_ingest.py
git commit -m "feat: sleeper adapter, season-level path tested live, projections scored under league rules"
```

---

### Task 8: First-down imputation + divergence report (R16)

FP (and most sources) project no first downs; FD is worth 4–6 pts/game for volume players — the league's biggest invisible value. Impute FD from nflverse history, validate against Sleeper's native FD projections. **Divergence >15% = investigate, never silently prefer either source** — the report is a deliverable.

**Files:**
- Create: `src/ffi/scoring/fd_impute.py`
- Create: `scripts/fd_divergence_report.py`
- Test: `tests/test_fd_impute.py`

**Interfaces:**
- Consumes: `raw.nflverse_player_week` (2019–2025), latest season-level Sleeper snapshot + `stat_line_from_sleeper`
- Produces: `FdRates` (fitted rates object), `fit_fd_rates(conn, seasons: list[int]) -> FdRates`, `impute_fd(rates: FdRates, position: str, gsis_id: str | None, carries: float, receptions: float, completions: float) -> dict` with keys `rush_first_downs`, `rec_first_downs`, `pass_first_downs`; divergence report `docs/research/<date>-fd-imputation-divergence.md`

- [ ] **Step 1: Write failing tests**

`tests/test_fd_impute.py`:

```python
import pytest

from ffi.scoring.fd_impute import FdRates, impute_fd

RATES = FdRates(
    position_rates={
        "RB": {"rush_fd_per_carry": 0.21, "rec_fd_per_rec": 0.42},
        "WR": {"rush_fd_per_carry": 0.30, "rec_fd_per_rec": 0.58},
        "QB": {"rush_fd_per_carry": 0.32, "rec_fd_per_rec": 0.50, "pass_fd_per_cmp": 0.51},
    },
    player_rates={"00-0033280": {"rush_fd_per_carry": 0.25}},
    prior_strength={"rush": 50.0, "rec": 30.0, "pass": 100.0},
)


def test_position_rate_applied():
    out = impute_fd(RATES, "WR", None, carries=0, receptions=100, completions=0)
    assert out["rec_first_downs"] == pytest.approx(58.0)


def test_player_rate_shrinkage():
    # player rate exists -> blended result strictly between player and position rate
    out = impute_fd(RATES, "RB", "00-0033280", carries=200, receptions=0, completions=0)
    assert 0.21 * 200 < out["rush_first_downs"] < 0.25 * 200 or \
           0.25 * 200 < out["rush_first_downs"] < 0.21 * 200 or True
    # (direction depends on stored blending; the precise contract is tested below)


def test_unknown_position_fails_loud():
    with pytest.raises(KeyError):
        impute_fd(RATES, "LS", None, carries=1, receptions=0, completions=0)
```

(The shrinkage contract: `player_rates` stores the player's ALREADY-SHRUNK rate — computed at fit time as `(fd + k·pos_rate)/(vol + k)` — so `impute_fd` just prefers `player_rates` when present. Simplify the middle test to assert `out["rush_first_downs"] == pytest.approx(0.25 * 200)` once implemented.)

- [ ] **Step 2: Implement `src/ffi/scoring/fd_impute.py`**

```python
"""First-down imputation (R16): rates fitted on nflverse 2019-2025, applied to
projected volume for FD-less sources (FantasyPros).

Method (documented for the divergence report):
- position rate = sum(FD) / sum(volume) per position, all seasons pooled
- player rate  = empirical-Bayes shrunk: (player_fd + k*pos_rate) / (player_vol + k)
  with prior strength k = 50 carries / 30 receptions / 100 completions
- imputation uses the player rate when the player has history, else position rate.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class FdRates:
    position_rates: dict          # pos -> {"rush_fd_per_carry": r, "rec_fd_per_rec": r, ...}
    player_rates: dict            # gsis_id -> subset of the same keys (already shrunk)
    prior_strength: dict = field(default_factory=lambda: {"rush": 50.0, "rec": 30.0, "pass": 100.0})


_KINDS = [  # (rate key, fd column, volume column, prior key)
    ("rush_fd_per_carry", "rushing_first_downs", "carries", "rush"),
    ("rec_fd_per_rec", "receiving_first_downs", "receptions", "rec"),
    ("pass_fd_per_cmp", "passing_first_downs", "completions", "pass"),
]


def fit_fd_rates(conn, seasons: list[int]) -> FdRates:
    position_rates: dict = {}
    player_rates: dict = {}
    with conn.cursor() as cur:
        cur.execute(
            """SELECT position,
                      sum(rushing_first_downs)::float, sum(carries)::float,
                      sum(receiving_first_downs)::float, sum(receptions)::float,
                      sum(passing_first_downs)::float, sum(completions)::float
               FROM raw.nflverse_player_week
               WHERE season = ANY(%s) AND position IN ('QB','RB','WR','TE')
               GROUP BY position""",
            (seasons,),
        )
        for pos, rfd, car, cfd, rec, pfd, cmp_ in cur.fetchall():
            rates = {}
            if car and car > 0:
                rates["rush_fd_per_carry"] = rfd / car
            if rec and rec > 0:
                rates["rec_fd_per_rec"] = cfd / rec
            if cmp_ and cmp_ > 0:
                rates["pass_fd_per_cmp"] = pfd / cmp_
            position_rates[pos] = rates
        prior = FdRates([], []).prior_strength if False else {"rush": 50.0, "rec": 30.0, "pass": 100.0}
        cur.execute(
            """SELECT gsis_id, max(position),
                      sum(rushing_first_downs)::float, sum(carries)::float,
                      sum(receiving_first_downs)::float, sum(receptions)::float,
                      sum(passing_first_downs)::float, sum(completions)::float
               FROM raw.nflverse_player_week
               WHERE season = ANY(%s) AND position IN ('QB','RB','WR','TE')
               GROUP BY gsis_id""",
            (seasons,),
        )
        for gsis, pos, rfd, car, cfd, rec, pfd, cmp_ in cur.fetchall():
            pos_r = position_rates.get(pos)
            if not pos_r:
                continue
            triples = [("rush_fd_per_carry", rfd, car, "rush"),
                       ("rec_fd_per_rec", cfd, rec, "rec"),
                       ("pass_fd_per_cmp", pfd, cmp_, "pass")]
            shrunk = {}
            for key, fd, vol, pk in triples:
                if key in pos_r and vol and vol > 0:
                    k = prior[pk]
                    shrunk[key] = (fd + k * pos_r[key]) / (vol + k)
            if shrunk:
                player_rates[gsis] = shrunk
    return FdRates(position_rates=position_rates, player_rates=player_rates)


def impute_fd(rates: FdRates, position: str, gsis_id: str | None,
              carries: float, receptions: float, completions: float) -> dict:
    pos_r = rates.position_rates[position]  # KeyError = unknown position: fail loud
    ply_r = rates.player_rates.get(gsis_id, {}) if gsis_id else {}

    def rate(key):
        if key in ply_r:
            return ply_r[key]
        return pos_r[key]  # KeyError if the position genuinely lacks the rate: loud

    out = {"rush_first_downs": 0.0, "rec_first_downs": 0.0, "pass_first_downs": 0.0}
    if carries:
        out["rush_first_downs"] = carries * rate("rush_fd_per_carry")
    if receptions:
        out["rec_first_downs"] = receptions * rate("rec_fd_per_rec")
    if completions:
        out["pass_first_downs"] = completions * rate("pass_fd_per_cmp")
    return out
```

(Clean up the leftover `prior = ... if False else ...` line to just the dict — shown here to flag it must equal `FdRates.prior_strength`'s default.) Run: `uv run pytest tests/test_fd_impute.py -q` → PASS (simplify the shrinkage test per Step 1's note).

- [ ] **Step 3: Write + run the divergence report (the R16 deliverable)**

`scripts/fd_divergence_report.py`:

```python
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
    cur.execute("SELECT sleeper_id, gsis_id FROM public.player_id_xwalk WHERE sleeper_id IS NOT NULL")
    sleeper_to_gsis = dict(cur.fetchall())

rows = []
for rec in payload:
    pos = (rec.get("player") or {}).get("position")
    if pos not in ("QB", "RB", "WR", "TE"):
        continue
    s = rec["stats"]
    native = {"rush": s.get("rush_fd"), "rec": s.get("rec_fd"), "pass": s.get("pass_fd")}
    imputed = impute_fd(
        rates, pos, sleeper_to_gsis.get(str(rec["player_id"])),
        carries=float(s.get("rush_att", 0)), receptions=float(s.get("rec", 0)),
        completions=float(s.get("pass_cmp", 0)),
    )
    for kind, nat_key, imp_key in (("rush", "rush", "rush_first_downs"),
                                   ("rec", "rec", "rec_first_downs"),
                                   ("pass", "pass", "pass_first_downs")):
        nat = native[nat_key]
        imp = imputed[imp_key]
        if nat is None or float(nat) < 10:   # only meaningful volume
            continue
        rows.append({
            "name": f"{(rec['player'] or {}).get('first_name','?')} {(rec['player'] or {}).get('last_name','?')}",
            "pos": pos, "kind": kind, "native": float(nat), "imputed": imp,
            "pct": abs(imp - float(nat)) / float(nat),
        })

if not rows:
    raise SystemExit("no comparable FD rows — snapshot empty or volume filter too strict")
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
    "| player | pos | kind | native | imputed | div% |", "|---|---|---|---|---|---|",
    *[f"| {r['name']} | {r['pos']} | {r['kind']} | {r['native']:.0f} | {r['imputed']:.1f} | {r['pct']:.0%} |"
      for r in sorted(over15, key=lambda r: -r["pct"])[:60]],
]
report.write_text("\n".join(lines) + "\n")
print(f"median={median:.1%}, over-15%={len(over15)}/{len(rows)} -> {report}")
if median > 0.15:
    raise SystemExit(
        "MEDIAN divergence >15% — the imputation method itself is off (R16): "
        "investigate rate pooling/shrinkage before FP FD-imputation is trusted."
    )
```

Run: `uv run python scripts/fd_divergence_report.py`
Expected: report written; median well under 15%. Individual >15% players are EXPECTED (role changes the history can't see — that's the point of the report); a >15% MEDIAN is a method bug and blocks Task 11's use of FP imputed FD. Summarize the over-15% list in the task report — these are also useful draft intel (players whose role the market re-rates).

- [ ] **Step 4: Full suite + commit**

```bash
uv run pytest -q
git add src/ffi/scoring/fd_impute.py scripts/fd_divergence_report.py \
  tests/test_fd_impute.py docs/research/
git commit -m "feat: FD imputation with empirical-Bayes shrinkage + R16 divergence report"
```

---

### Task 9: Threshold-bonus distribution pricing + calibration report (R16)

A mean projection cannot price a 100/150/200 bonus (design 4.2): a 90-yd/wk mean back hits 100+ in ~40% of weeks, a "mean-pricing" model says 0%. Price bonuses as `EV = Σ P(week yards ≥ threshold) × points` using a gamma distribution around the projected weekly mean, with per-player CV from history. Calibration on 2023–25 actuals is the R16 deliverable.

**Files:**
- Create: `src/ffi/scoring/bonus_pricing.py`
- Create: `scripts/bonus_calibration_report.py`
- Modify: `pyproject.toml` (add `scipy>=1.12`)
- Test: `tests/test_bonus_pricing.py`

**Interfaces:**
- Consumes: `raw.nflverse_player_week`, `ScoringConfig.offense.yardage_bonuses`
- Produces: `estimate_weekly_cv(conn, seasons, min_weeks=8) -> dict` (`{"players": {gsis: {stat: cv}}, "positions": {pos: {stat: cv}}}`); `bonus_ev_per_week(mean_weekly: float, cv: float, tiers: list[BonusTier]) -> float`; `weekly_threshold_prob(mean_weekly: float, cv: float, threshold: float) -> float`

- [ ] **Step 1: Add scipy**

```bash
uv add "scipy>=1.12"
```

- [ ] **Step 2: Write failing tests**

`tests/test_bonus_pricing.py`:

```python
import pytest

from ffi.scoring.bonus_pricing import bonus_ev_per_week, weekly_threshold_prob
from ffi.scoring.config import load_config_v1

TIERS = load_config_v1().offense.yardage_bonuses["rec_yards"]


def test_probability_monotone_in_mean():
    cv = 0.6
    p_low = weekly_threshold_prob(50, cv, 100)
    p_mid = weekly_threshold_prob(90, cv, 100)
    p_high = weekly_threshold_prob(130, cv, 100)
    assert p_low < p_mid < p_high


def test_mean_at_threshold_is_roughly_half():
    # gamma is right-skewed so P(X >= mean) is a bit under 0.5 — sanity band
    p = weekly_threshold_prob(100, 0.5, 100)
    assert 0.30 < p < 0.55


def test_zero_mean_prices_zero():
    assert bonus_ev_per_week(0, 0.6, TIERS) == 0.0


def test_ev_includes_all_tiers():
    # enormous mean -> hits all three tiers nearly every week -> EV ≈ 12
    assert bonus_ev_per_week(400, 0.3, TIERS) == pytest.approx(12.0, abs=0.2)


def test_invalid_cv_fails_loud():
    with pytest.raises(ValueError):
        weekly_threshold_prob(90, 0, 100)
```

Run: `uv run pytest tests/test_bonus_pricing.py -q` → FAIL.

- [ ] **Step 3: Implement `src/ffi/scoring/bonus_pricing.py`**

```python
"""Distribution-based threshold-bonus pricing (R16).

Weekly yardage Y ~ Gamma(shape k, scale theta) with k = 1/cv^2,
theta = mean * cv^2 (so E[Y]=mean, SD/mean=cv). Gamma: positive support,
right-skewed — matches weekly yardage shape far better than normal.
CV per player from 2019-2025 weekly history (active weeks only), position
fallback for thin histories. Calibrated on 2023-25 (see calibration report)."""
from scipy.stats import gamma as gamma_dist

from ffi.scoring.config import BonusTier


def weekly_threshold_prob(mean_weekly: float, cv: float, threshold: float) -> float:
    if mean_weekly <= 0:
        return 0.0
    if cv <= 0:
        raise ValueError(f"cv must be positive, got {cv}")
    k = 1.0 / (cv * cv)
    theta = mean_weekly * cv * cv
    return float(gamma_dist.sf(threshold, a=k, scale=theta))


def bonus_ev_per_week(mean_weekly: float, cv: float, tiers: list[BonusTier]) -> float:
    if mean_weekly <= 0:
        return 0.0
    return sum(weekly_threshold_prob(mean_weekly, cv, t.threshold) * t.points for t in tiers)


_STAT_COLS = {"rush_yards": "rushing_yards", "rec_yards": "receiving_yards",
              "pass_yards": "passing_yards"}


def estimate_weekly_cv(conn, seasons: list[int], min_weeks: int = 8) -> dict:
    """Per-player weekly-yardage CV (sd/mean over ACTIVE weeks: volume > 0),
    plus per-position pooled fallback. Returns
    {"players": {gsis: {stat: cv}}, "positions": {pos: {stat: cv}}}."""
    out = {"players": {}, "positions": {}}
    with conn.cursor() as cur:
        for stat, col in _STAT_COLS.items():
            cur.execute(
                f"""SELECT gsis_id, max(position), avg({col}), stddev_samp({col}), count(*)
                    FROM raw.nflverse_player_week
                    WHERE season = ANY(%s) AND {col} > 0
                    GROUP BY gsis_id HAVING count(*) >= %s AND avg({col}) > 0""",
                (seasons, min_weeks),
            )
            for gsis, pos, mean, sd, _n in cur.fetchall():
                if sd is None or mean is None or float(mean) <= 0:
                    continue
                out["players"].setdefault(gsis, {})[stat] = float(sd) / float(mean)
            cur.execute(
                f"""SELECT position, avg({col}), stddev_samp({col})
                    FROM raw.nflverse_player_week
                    WHERE season = ANY(%s) AND {col} > 0 AND position IN ('QB','RB','WR','TE')
                    GROUP BY position HAVING avg({col}) > 0""",
                (seasons,),
            )
            for pos, mean, sd in cur.fetchall():
                out["positions"].setdefault(pos, {})[stat] = float(sd) / float(mean)
    if not out["positions"]:
        raise ValueError("no position CVs computed — is raw.nflverse_player_week loaded?")
    return out
```

Run: `uv run pytest tests/test_bonus_pricing.py -q` → PASS.

- [ ] **Step 4: Write + run the calibration report (the R16 deliverable)**

`scripts/bonus_calibration_report.py`:

```python
#!/usr/bin/env python3
"""Calibration (R16): for 2023-2025, predict each player-season's weekly bonus
hit rates from (season mean weekly yards, CV fitted on OTHER seasons), then
compare predicted vs actual hit frequencies in predicted-probability bins.
Also scores mean-pricing (the naive competitor) for the same weeks via Brier.
In-sample simplification (season mean as the 'projection') is documented in
the report header — this validates the DISTRIBUTION SHAPE, not projection skill."""
import datetime
import pathlib
from collections import defaultdict

from ffi.db import connect
from ffi.scoring.bonus_pricing import estimate_weekly_cv, weekly_threshold_prob
from ffi.scoring.config import load_config_v1

EVAL_SEASONS = [2023, 2024, 2025]
FIT_SEASONS = [2019, 2020, 2021, 2022]
STATS = {"rush_yards": "rushing_yards", "rec_yards": "receiving_yards",
         "pass_yards": "passing_yards"}

conn = connect()
cfg = load_config_v1()
cvs = estimate_weekly_cv(conn, FIT_SEASONS)

bins = defaultdict(lambda: [0, 0.0, 0])   # bin -> [n, sum_pred, sum_actual]
brier_dist, brier_mean, n_obs = 0.0, 0.0, 0
for stat, col in STATS.items():
    tiers = cfg.offense.yardage_bonuses[stat]
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT gsis_id, max(position), season, avg({col}),
                       array_agg({col}) AS weeks
                FROM raw.nflverse_player_week
                WHERE season = ANY(%s) AND {col} > 0
                GROUP BY gsis_id, season HAVING count(*) >= 8""",
            (EVAL_SEASONS,),
        )
        for gsis, pos, season, mean, weeks in cur.fetchall():
            cv = cvs["players"].get(gsis, {}).get(stat) or cvs["positions"].get(pos, {}).get(stat)
            if cv is None:
                continue
            for t in tiers:
                pred = weekly_threshold_prob(float(mean), cv, t.threshold)
                naive = 1.0 if float(mean) >= t.threshold else 0.0
                for y in weeks:
                    actual = 1.0 if float(y) >= t.threshold else 0.0
                    b = min(int(pred * 10), 9)
                    bins[b][0] += 1
                    bins[b][1] += pred
                    bins[b][2] += actual
                    brier_dist += (pred - actual) ** 2
                    brier_mean += (naive - actual) ** 2
                    n_obs += 1

if n_obs == 0:
    raise SystemExit("no calibration observations — check data load")
today = datetime.date.today().isoformat()
report = pathlib.Path(f"docs/research/{today}-bonus-calibration.md")
rows = []
for b in sorted(bins):
    n, sp, sa = bins[b]
    rows.append(f"| {b/10:.1f}-{(b+1)/10:.1f} | {n} | {sp/n:.3f} | {sa/n:.3f} |")
report.write_text("\n".join([
    f"# Threshold-bonus calibration — {today}",
    f"\nobs={n_obs} (player-week × tier), eval {EVAL_SEASONS}, CV fit {FIT_SEASONS}",
    "\nCaveat: season-mean-as-projection is in-sample for the mean; this validates the",
    "distribution SHAPE around a known mean, not projection accuracy.",
    f"\n**Brier (gamma model) = {brier_dist/n_obs:.4f}  vs  Brier (mean-pricing) = {brier_mean/n_obs:.4f}**",
    "\n| predicted-P bin | n | mean predicted | actual freq |", "|---|---|---|---|",
    *rows,
]) + "\n")
print(f"Brier gamma={brier_dist/n_obs:.4f} vs mean-pricing={brier_mean/n_obs:.4f} -> {report}")
if brier_dist >= brier_mean:
    raise SystemExit(
        "distribution pricing did NOT beat mean-pricing — R16 red flag; "
        "do not wire bonus_ev into valuation until resolved."
    )
```

Run: `uv run python scripts/bonus_calibration_report.py`
Expected: gamma Brier < mean-pricing Brier (decisively), per-bin predicted ≈ actual (slope eyeball: bin means within ~±0.05–0.10). Failure = R16 red flag; investigate distribution family (lognormal alternative) before Task 11 uses `bonus_ev_per_week`.

- [ ] **Step 5: Full suite + commit**

```bash
uv run pytest -q
git add pyproject.toml uv.lock src/ffi/scoring/bonus_pricing.py \
  scripts/bonus_calibration_report.py tests/test_bonus_pricing.py docs/research/
git commit -m "feat: gamma-distribution bonus pricing + 2023-25 calibration report (beats mean-pricing)"
```

---

### Task 10: FantasyPros ingestion (budget-guarded)

Consensus overlay: superflex ECR (+std for uncertainty bands), positional ECR, ADP. Hard limits 1/sec + 100/day; **our budget ≤30/day, enforced in code** (ADR Domain 6). Fact #12: exact v2 params for superflex are unverified — probe before committing code to param names. Never store FP historical player stats (ToS).

**Files:**
- Create: `src/ffi/ingest/fantasypros.py`
- Create: `scripts/ingest_fantasypros.py`
- Test: `tests/test_fantasypros_ingest.py`

**Interfaces:**
- Consumes: `raw.fp_snapshots` (Task 3), `FANTASYPROS_API_KEY` from `.env`, `raw.ingest_runs` framework
- Produces: `FpClient.get(endpoint: str, params: dict) -> dict` (budget-guarded, cached row per call); `FpDailySync(conn).run()` writing one `raw.fp_snapshots` row per call; `fp_calls_today(conn) -> int`; `latest_fp_payload(conn, endpoint_like: str, params_subset: dict) -> dict | None` (cache reader — ad-hoc consumers NEVER hit the API)

- [ ] **Step 1: Write failing tests for the budget guard + cache reader**

`tests/test_fantasypros_ingest.py`:

```python
import pytest

from ffi.ingest.fantasypros import (
    FpBudgetExceededError,
    FpClient,
    fp_calls_today,
    latest_fp_payload,
)


def _seed_snapshot(db, endpoint, params, payload):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.fp_snapshots (endpoint, params, payload) VALUES (%s,%s,%s)",
            (endpoint, __import__("json").dumps(params), __import__("json").dumps(payload)),
        )
    db.commit()


def test_fp_calls_today_counts_snapshots(db):
    assert fp_calls_today(db) == 0
    _seed_snapshot(db, "consensus-rankings", {"position": "OP"}, {"players": []})
    assert fp_calls_today(db) == 1


def test_budget_guard_blocks_at_limit(db, monkeypatch):
    client = FpClient(db, api_key="test", daily_budget=2)
    monkeypatch.setattr(client, "_http_get", lambda url, params: {"players": []})
    client.get("consensus-rankings", {"position": "QB"})
    client.get("consensus-rankings", {"position": "RB"})
    with pytest.raises(FpBudgetExceededError):
        client.get("consensus-rankings", {"position": "WR"})
    assert fp_calls_today(db) == 2  # the blocked call wrote nothing


def test_latest_fp_payload_reads_cache(db):
    _seed_snapshot(db, "consensus-rankings", {"position": "OP", "week": 0}, {"players": [1]})
    got = latest_fp_payload(db, "consensus-rankings", {"position": "OP"})
    assert got == {"players": [1]}
    assert latest_fp_payload(db, "consensus-rankings", {"position": "XX"}) is None
```

Run: `uv run pytest tests/test_fantasypros_ingest.py -q` → FAIL.

- [ ] **Step 2: Implement `src/ffi/ingest/fantasypros.py`**

```python
"""FantasyPros public API v2 client. HARD BUDGET: every successful call writes
one raw.fp_snapshots row; the guard counts today's rows and ABORTS (never
degrades) when the budget would be exceeded (ADR Domain 6, runbook
docs/runbooks/fantasypros-api.md). ToS: never store historical player stats."""
import json
import os
import time

import requests

from ffi.ingest.base import IngestError

BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl"
DAILY_BUDGET = 30
CALL_SPACING_S = 1.1


class FpBudgetExceededError(Exception):
    """Daily FP call budget would be exceeded. Aborting is the design: the cap
    protects a discretionary-approval key (R12). Re-run tomorrow or read cache."""


def fp_calls_today(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.fp_snapshots WHERE fetched_at::date = now()::date")
        return cur.fetchone()[0]


def latest_fp_payload(conn, endpoint_like: str, params_subset: dict):
    """Cache reader: most recent snapshot whose endpoint matches and whose params
    contain params_subset. Ad-hoc consumers use THIS, never the API."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT payload FROM raw.fp_snapshots
               WHERE endpoint = %s AND params @> %s::jsonb
               ORDER BY fetched_at DESC LIMIT 1""",
            (endpoint_like, json.dumps(params_subset)),
        )
        row = cur.fetchone()
    return row[0] if row else None


class FpClient:
    def __init__(self, conn, api_key: str | None = None, daily_budget: int = DAILY_BUDGET):
        self.conn = conn
        self.api_key = api_key or os.getenv("FANTASYPROS_API_KEY")
        if not self.api_key:
            raise IngestError("FANTASYPROS_API_KEY missing from .env")
        self.daily_budget = daily_budget
        self._last_call = 0.0

    def _http_get(self, url: str, params: dict) -> dict:
        resp = requests.get(url, params=params, headers={"x-api-key": self.api_key}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def get(self, endpoint: str, params: dict, season: int | None = None) -> dict:
        used = fp_calls_today(self.conn)
        if used + 1 > self.daily_budget:
            raise FpBudgetExceededError(
                f"FP budget: {used}/{self.daily_budget} calls already used today — "
                "refusing this call. Read the cache (latest_fp_payload) or wait for tomorrow."
            )
        wait = CALL_SPACING_S - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        url = f"{BASE_URL}/{season}/{endpoint}" if season else f"{BASE_URL}/{endpoint}"
        try:
            payload = self._http_get(url, params)
        finally:
            self._last_call = time.monotonic()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.fp_snapshots (endpoint, params, payload) VALUES (%s,%s,%s)",
                (endpoint, json.dumps({**params, "season": season}), json.dumps(payload)),
            )
        self.conn.commit()
        return payload
```

Run: `uv run pytest tests/test_fantasypros_ingest.py -q` → PASS.

- [ ] **Step 3: Probe the live API for superflex params (budget: ≤4 calls)**

Write `scripts/ingest_fantasypros.py` with a probe mode first:

```python
#!/usr/bin/env python3
"""FantasyPros daily sync (<=30 calls, budget-enforced in FpClient).
--probe: exploratory single call, prints top-level keys + first player."""
import argparse
import json

from ffi.db import connect
from ffi.ingest.fantasypros import FpClient, fp_calls_today

SEASON = 2026
RANKING_POSITIONS = ["OP", "QB", "RB", "WR", "TE", "K", "DST"]  # OP = superflex (verify via --probe)


def daily_sync(conn):
    client = FpClient(conn)
    for pos in RANKING_POSITIONS:
        payload = client.get(
            "consensus-rankings", {"type": "draft", "scoring": "PPR", "position": pos, "week": 0},
            season=SEASON,
        )
        players = payload.get("players")
        if not players:
            raise SystemExit(
                f"consensus-rankings position={pos} returned no players — payload keys: "
                f"{sorted(payload)[:20]}. Fix params before burning more budget."
            )
        print(f"  rankings {pos}: {len(players)} players")
    print(f"done; calls today: {fp_calls_today(conn)}/30")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", nargs=2, metavar=("ENDPOINT", "PARAMS_JSON"),
                    help='e.g. --probe consensus-rankings \'{"position":"OP","type":"draft","scoring":"PPR","week":0}\'')
    ap.add_argument("--daily", action="store_true")
    args = ap.parse_args()
    conn = connect()
    if args.probe:
        client = FpClient(conn)
        payload = client.get(args.probe[0], json.loads(args.probe[1]), season=SEASON)
        print("top-level keys:", sorted(payload)[:20])
        players = payload.get("players") or []
        print("first player:", json.dumps(players[0], indent=1)[:800] if players else "NONE")
    elif args.daily:
        daily_sync(conn)
    else:
        ap.error("choose --probe or --daily")


if __name__ == "__main__":
    main()
```

Probe sequence (stop as soon as one works; each writes a cached snapshot):
1. `uv run python scripts/ingest_fantasypros.py --probe consensus-rankings '{"position":"OP","type":"draft","scoring":"PPR","week":0}'`
2. If OP fails/empty: try `{"position":"ALL","type":"draft","scoring":"SUPERFLEX","week":0}` then `{"position":"ALL","type":"superflex","scoring":"PPR","week":0}`.
3. Record in the task report: the working param set, the player-record field names for rank (`rank_ecr`), std (`rank_std`), min/max, and the FP player id field (feeds `player_id_xwalk.fantasypros_id`).

Adjust `RANKING_POSITIONS`/params in `daily_sync` to the verified reality. If NO superflex variant exists, fall back to positional ECR + document that superflex ECR comes from GMM over our own 2QB values only (note for Task 11) — do not fake it.

- [ ] **Step 4: Run the daily sync once**

Run: `uv run python scripts/ingest_fantasypros.py --daily`
Expected: 7 ranking snapshots cached, `calls today: ≤11/30` (probes included). ADP/projections endpoints are NOT part of v1 sync — add only when Task 11 proves it needs them (YAGNI; the budget is precious).

- [ ] **Step 5: Full suite + commit**

```bash
uv run pytest -q
git add src/ffi/ingest/fantasypros.py scripts/ingest_fantasypros.py tests/test_fantasypros_ingest.py
git commit -m "feat: budget-guarded FantasyPros client + daily rankings sync (superflex params verified)"
```

---

### Task 11: Valuation — computed 2QB baseline + VORP + GMM tiers + sensitivity (R16)

The layer that turns points into draft value. Replacement baselines are **computed from league shape, never assumed** (12 teams × 2QB, FLEX is W/R/T only — QBs are NOT flex-eligible here). QB-hoarding sensitivity is the R16 mitigation: research says managers roster 3–4 QBs in this format, so QB replacement is nearer QB30–36 than QB24.

**Files:**
- Create: `src/ffi/valuation/__init__.py`, `src/ffi/valuation/baseline.py`, `src/ffi/valuation/tiers.py`
- Create: `scripts/build_valuation.py`
- Create: `scripts/baseline_sensitivity_report.py`
- Modify: `pyproject.toml` (add `scikit-learn>=1.4`)
- Test: `tests/test_valuation.py`

**Interfaces:**
- Consumes: `scoring.projection_points` (sleeper season, Task 7), FP rankings cache (Task 10), crosswalk, `bonus_ev_per_week`/`estimate_weekly_cv` (Task 9), `impute_fd` (Task 8 — only if FP projections are wired later; v1 valuation points come from Sleeper stat lines which carry native FD)
- Produces: `compute_replacement_ranks(scenario: dict) -> dict[str, int]`; `compute_baselines(points_by_pos: dict[str, list[float]], scenario: dict) -> dict[str, float]`; `gmm_tiers(values: list[float], max_k: int = 9) -> list[int]`; populated `valuation.replacement_baseline` + `valuation.player_value`; sensitivity report

- [ ] **Step 1: Add scikit-learn**

```bash
uv add "scikit-learn>=1.4"
```

- [ ] **Step 2: Write failing tests**

`tests/test_valuation.py`:

```python
import pytest

from ffi.valuation.baseline import compute_baselines, compute_replacement_ranks
from ffi.valuation.tiers import gmm_tiers


def test_replacement_ranks_2qb_league_shape():
    ranks = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 0})
    # 12 teams x 2 QB starters, no hoarding = QB24; FLEX excludes QB here.
    assert ranks["QB"] == 24
    assert ranks["TE"] == 12 + pytest.approx(0, abs=12)  # TE gets a flex share; exact below
    assert ranks["K"] == 12 and ranks["DEF"] == 12


def test_qb_hoarding_scenario_moves_baseline():
    r0 = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 0})
    r12 = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": 12})
    assert r12["QB"] == r0["QB"] + 12
    assert r12["RB"] == r0["RB"]           # hoarding QBs doesn't change RB demand


def test_compute_baselines_picks_nth_best():
    pts = {"QB": sorted([30 - i for i in range(40)], reverse=True)}
    ranks = {"QB": 24}
    base = compute_baselines(pts, ranks)
    assert base["QB"] == pts["QB"][23]


def test_baseline_fails_loud_on_thin_pool():
    with pytest.raises(ValueError, match="fewer players"):
        compute_baselines({"QB": [20.0] * 10}, {"QB": 24})


def test_gmm_tiers_orders_and_covers():
    values = [400, 395, 390, 300, 295, 290, 200, 195, 190, 100, 95, 90]
    tiers = gmm_tiers(values, max_k=6)
    assert len(tiers) == len(values)
    assert tiers[0] == 1                     # best player is tier 1
    assert tiers == sorted(tiers)            # descending values -> nondecreasing tier
    assert tiers[-1] > tiers[0]              # more than one tier found
```

Run: `uv run pytest tests/test_valuation.py -q` → FAIL.

- [ ] **Step 3: Implement `src/ffi/valuation/baseline.py`**

```python
"""Replacement baselines COMPUTED from league shape (design 4.3, R16).

League shape (league_rules.md): 12 teams; starters QB2 / RB2 / WR3 / TE1 /
FLEX1 (W/R/T only — QB not flex-eligible) / K1 / DEF1.
FLEX allocation: split across RB/WR/TE in proportion to historical flex usage;
default 0.5/0.4/0.1 (parameterized in the scenario — sensitivity report varies it).
QB hoarding: 2QB leagues roster QBs beyond starters; scenario adds
qb_extra_rostered to QB demand (0 = pure starters, 12 = one bench QB per team,
24 = two)."""

STARTERS = {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}
FLEX_SLOTS = 1
DEFAULT_FLEX_SHARE = {"RB": 0.5, "WR": 0.4, "TE": 0.1}


def compute_replacement_ranks(scenario: dict) -> dict[str, int]:
    teams = scenario["teams"]
    flex_share = scenario.get("flex_share", DEFAULT_FLEX_SHARE)
    if abs(sum(flex_share.values()) - 1.0) > 1e-9:
        raise ValueError(f"flex_share must sum to 1: {flex_share}")
    ranks = {}
    for pos, n in STARTERS.items():
        demand = teams * n
        if pos in flex_share:
            demand += round(teams * FLEX_SLOTS * flex_share[pos])
        if pos == "QB":
            demand += scenario.get("qb_extra_rostered", 0)
        ranks[pos] = int(demand)
    return ranks


def compute_baselines(points_by_pos: dict[str, list[float]],
                      replacement_ranks: dict[str, int]) -> dict[str, float]:
    """points_by_pos values must be sorted descending. Replacement points =
    the Nth-best player's points (N = replacement rank)."""
    out = {}
    for pos, rank in replacement_ranks.items():
        pool = points_by_pos.get(pos)
        if pool is None:
            raise ValueError(f"no projection pool for position {pos}")
        if sorted(pool, reverse=True) != list(pool):
            raise ValueError(f"points for {pos} must be sorted descending")
        if len(pool) < rank:
            raise ValueError(
                f"{pos}: fewer players projected ({len(pool)}) than replacement rank {rank}"
            )
        out[pos] = pool[rank - 1]
    return out
```

(Fix the TE test in Step 2 once implemented: with the default flex share, `ranks["TE"] == 12 + round(12*0.1) == 13` — assert exactly 13, and `RB == 24 + 6 == 30`, `WR == 36 + 5 == 41`. Write the exact expected numbers into the test.)

- [ ] **Step 4: Implement `src/ffi/valuation/tiers.py`**

```python
"""GMM tier clustering (Boris Chen / fftiers method) over a 1-D value vector.
K chosen by BIC over 2..max_k. Deterministic: fixed random_state."""
import numpy as np
from sklearn.mixture import GaussianMixture


def gmm_tiers(values: list[float], max_k: int = 9) -> list[int]:
    if len(values) < 4:
        raise ValueError(f"need >=4 values to tier, got {len(values)}")
    x = np.asarray(values, dtype=float).reshape(-1, 1)
    best, best_bic = None, np.inf
    for k in range(2, min(max_k, len(values) // 2) + 1):
        gm = GaussianMixture(n_components=k, random_state=17, n_init=3).fit(x)
        bic = gm.bic(x)
        if bic < best_bic:
            best, best_bic = gm, bic
    labels = best.predict(x)
    # relabel clusters so tier 1 = highest mean value
    order = np.argsort(-best.means_.ravel())
    remap = {int(old): rank + 1 for rank, old in enumerate(order)}
    return [remap[int(l)] for l in labels]
```

Run: `uv run pytest tests/test_valuation.py -q` → PASS.

- [ ] **Step 5: Write `scripts/build_valuation.py`** (end-to-end into the tables)

```python
#!/usr/bin/env python3
"""Build valuation.player_value for the QB-hoarding scenario grid from the
latest season-level Sleeper projection points (native FD included), tiered by
GMM. Uncertainty band v1 = FP ECR rank_std where a superflex ECR row joins
(cache only), else NULL. Provenance in params."""
import json

from ffi.db import connect
from ffi.ingest.fantasypros import latest_fp_payload
from ffi.scoring.config import load_config_v1
from ffi.valuation.baseline import compute_baselines, compute_replacement_ranks
from ffi.valuation.tiers import gmm_tiers

SCENARIOS = {
    "qb_hoard_0": {"teams": 12, "qb_extra_rostered": 0},
    "qb_hoard_12": {"teams": 12, "qb_extra_rostered": 12},
    "qb_hoard_24": {"teams": 12, "qb_extra_rostered": 24},
}

conn = connect()
cfg = load_config_v1()

# latest sleeper season snapshot's scored points, joined to crosswalk + position
with conn.cursor() as cur:
    cur.execute(
        """
        SELECT x.xwalk_id, x.position, x.name, pp.points::float, pp.snapshot_id
        FROM scoring.projection_points pp
        JOIN public.player_id_xwalk x ON x.sleeper_id = pp.player_ref
        WHERE pp.source = 'sleeper' AND pp.horizon = 'season'
          AND pp.config_version = %s
          AND pp.snapshot_id = (SELECT max(snapshot_id) FROM raw.sleeper_projections WHERE week IS NULL)
          AND x.position IN ('QB','RB','WR','TE','K')
        ORDER BY pp.points DESC
        """,
        (cfg.version,),
    )
    rows = cur.fetchall()
if not rows:
    raise SystemExit("no scored season projections joined to crosswalk — run Tasks 5-7 first")

# NOTE: DEF valuation deliberately absent from v1 board values — Task 12 answers
# draft-vs-stream for DEF/K first; K included for completeness.
by_pos: dict[str, list] = {}
for xid, pos, name, pts, snap in rows:
    by_pos.setdefault(pos, []).append((xid, name, pts))

fp_std = {}
fp = latest_fp_payload(conn, "consensus-rankings", {"position": "OP"})
if fp:
    for p in fp.get("players", []):
        # field names verified in Task 10 Step 3 — adjust if the probe said different
        if "player_name" in p and "rank_std" in p:
            fp_std[p["player_name"].lower()] = float(p["rank_std"])
else:
    print("WARNING: no superflex FP cache — value bands will be NULL (visible, not silent)")

snapshot_id = rows[0][4]
with conn.cursor() as cur:
    for scen_name, scen in SCENARIOS.items():
        ranks = compute_replacement_ranks(scen)
        ranks = {p: r for p, r in ranks.items() if p in by_pos}  # DEF absent in v1
        baselines = compute_baselines(
            {p: [pts for _, _, pts in by_pos[p]] for p in ranks}, ranks
        )
        for pos, base in baselines.items():
            cur.execute(
                """INSERT INTO valuation.replacement_baseline
                   (config_version, scenario, position, replacement_rank, replacement_points, params)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (cfg.version, scen_name, pos, ranks[pos], base,
                 json.dumps({**scen, "snapshot_id": snapshot_id})),
            )
        for pos in ranks:
            players = by_pos[pos]
            tiers = gmm_tiers([pts for _, _, pts in players]) if len(players) >= 4 else [1] * len(players)
            for (xid, name, pts), tier in zip(players, tiers):
                std = fp_std.get(name.lower())
                cur.execute(
                    """INSERT INTO valuation.player_value
                       (config_version, scenario, xwalk_id, position, proj_points, vorp,
                        tier, value_low, value_high, params)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (cfg.version, scen_name, xid, pos, pts, pts - baselines[pos], tier,
                     (pts - baselines[pos]) - (std or 0) * 1.0 if std else None,
                     (pts - baselines[pos]) + (std or 0) * 1.0 if std else None,
                     json.dumps({"snapshot_id": snapshot_id, "ecr_std_scale": 1.0})),
                )
conn.commit()
with conn.cursor() as cur:
    cur.execute(
        """SELECT v.scenario, x.name, v.position, round(v.vorp,1)
           FROM valuation.player_value v JOIN public.player_id_xwalk x USING (xwalk_id)
           WHERE v.scenario='qb_hoard_12' ORDER BY v.vorp DESC LIMIT 25"""
    )
    print("top 25 (qb_hoard_12):")
    for r in cur.fetchall():
        print("  ", r)
```

Rebuild semantics: the script INSERTs a fresh run each time (provenance preserved); consumers read the latest `computed_at` per scenario. Add `DELETE FROM valuation.player_value WHERE config_version=%s AND scenario=%s AND (params->>'snapshot_id')::int = %s` before inserting to keep re-runs idempotent per snapshot — implementer adds this with the same pattern as replacement_baseline.

Run: `uv run python scripts/build_valuation.py`
Sanity (report these in the task report): top-25 by VORP should be QB-heavy (2QB league), elite WRs present (full PPR + FD), and the ordering must differ visibly from generic PPR ADP — that difference IS the edge; if it doesn't differ, something is miswired.

- [ ] **Step 6: Sensitivity report (R16 deliverable)**

`scripts/baseline_sensitivity_report.py`:

```python
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
pairs = [(a, b) for i, a in enumerate(scens) for b in scens[i + 1:]]
lines = [f"# 2QB baseline sensitivity — {datetime.date.today().isoformat()}",
         "\n## Baselines\n", "| scenario | pos | repl rank | repl pts |", "|---|---|---|---|",
         *[f"| {s} | {p} | {r} | {pts} |" for s, p, r, pts in baselines],
         "\n## Ordering stability\n"]
for a, b in pairs:
    top24_a = {n for n, r in ranks[a].items() if r <= 24}
    top24_b = {n for n, r in ranks[b].items() if r <= 24}
    overlap = len(top24_a & top24_b)
    churn = sorted(
        ((n, ranks[a][n], ranks[b][n]) for n in set(ranks[a]) & set(ranks[b])
         if ranks[a][n] <= 50 or ranks[b][n] <= 50),
        key=lambda t: -abs(t[1] - t[2]),
    )[:10]
    lines += [f"### {a} vs {b}: top-24 overlap {overlap}/24",
              "| player | rank A | rank B |", "|---|---|---|",
              *[f"| {n} | {ra} | {rb} |" for n, ra, rb in churn], ""]
out = pathlib.Path(f"docs/research/{datetime.date.today().isoformat()}-baseline-sensitivity.md")
out.write_text("\n".join(lines) + "\n")
print(f"-> {out}")
```

Run it; read the output. Interpretation guidance for the report: top-24 overlap ≥ 20/24 across scenarios = ranking robust to the hoarding assumption (good); below that, the assumption is load-bearing → surface to the user as a strategy question, and the QB cohort material (pending input #2) becomes more urgent.

- [ ] **Step 7: Full suite + commit**

```bash
uv run pytest -q
git add pyproject.toml uv.lock src/ffi/valuation/ scripts/build_valuation.py \
  scripts/baseline_sensitivity_report.py tests/test_valuation.py docs/research/
git commit -m "feat: valuation layer — computed 2QB baselines, VORP, GMM tiers, sensitivity report"
```

---

### Task 12: DEF/K streaming-baseline check (explicit deliverable)

Does an elite DEF's weekly output clear the replacement-level **streaming** DEF under THIS league's enhanced DEF scoring (TFL, 3-and-outs, 4th-down stops, points/yards tiers)? Same for K distance tiers. The answer decides draft-early-vs-stream on the board — the handoff explicitly warns this is easy to never answer; this task exists so it gets answered.

**Files:**
- Create: `scripts/backfill_def_k_weeks.py` (only if coverage check requires it)
- Create: `scripts/streaming_baseline_report.py`

**Interfaces:**
- Consumes: `scoring.player_week_points` (`source='yahoo_engine'`, 2025), `public.team_def_map`, `v_player_yahoo_ids`, `yahoo_call` + `lg.player_stats` for backfill
- Produces: `docs/research/<date>-def-k-streaming-baseline.md` with an explicit verdict per position

- [ ] **Step 1: Check DEF/K weekly coverage**

```bash
psql -d fantasy_football -t -A -c "
SELECT v.position, count(DISTINCT s.player_ref) AS players, count(*) AS rows
FROM scoring.player_week_points s
JOIN public.v_player_yahoo_ids v ON v.yahoo_id = s.player_ref
WHERE s.source='yahoo_engine' AND v.position IN ('DEF','K')
GROUP BY 1;"
```
A streaming baseline needs (close to) ALL 32 DEFs and ~32 Ks per week — the streamer picks from the un-rostered pool. If distinct DEF < 28, backfill.

- [ ] **Step 2: Backfill missing DEF/K weeks (if needed; ~40 throttled calls, overnight-safe)**

`scripts/backfill_def_k_weeks.py`:

```python
#!/usr/bin/env python3
"""Fetch 2025 weekly stats for DEF/K ids missing from raw.yahoo_player_week.
DEF ids come from team_def_map (all 32); K ids from Yahoo's 2025 player pool
via league free-agent listing. Throttled via yahoo_call; resumable (skips
already-present rows via ON CONFLICT)."""
import json

from ffi.db import connect
from ffi.yahoo_client import get_league, get_session, yahoo_call

LEAGUE_KEY = "461.l.326814"
SEASON = 2025
BATCH = 25

conn = connect()
lg = get_league(get_session(), LEAGUE_KEY)

with conn.cursor() as cur:
    cur.execute("SELECT yahoo_def_id FROM public.team_def_map ORDER BY 1")
    def_ids = [int(r[0]) for r in cur.fetchall()]
if len(def_ids) < 28:
    raise SystemExit(f"team_def_map has only {len(def_ids)} defenses — run scripts/build_def_map.py first")

# K pool: taken + free agents at K in the 2025 league
k_ids = sorted({int(p["player_id"]) for p in yahoo_call(lg.taken_players) + yahoo_call(lg.free_agents, "K")
                if p.get("position_type") == "K" or "K" in (p.get("eligible_positions") or [])})
print(f"{len(def_ids)} DEF ids, {len(k_ids)} K ids")

for week in range(1, 18):
    with conn.cursor() as cur:
        cur.execute(
            """SELECT count(*) FROM raw.yahoo_player_week w
               WHERE w.league_key=%s AND w.week=%s
                 AND w.yahoo_player_id = ANY(%s)""",
            (LEAGUE_KEY, week, [str(i) for i in def_ids + k_ids]),
        )
        have = cur.fetchone()[0]
    want = len(def_ids) + len(k_ids)
    if have >= want:
        print(f"  week {week}: complete ({have}/{want}), skipping")
        continue
    ids = def_ids + k_ids
    for i in range(0, len(ids), BATCH):
        stats = yahoo_call(lg.player_stats, ids[i:i + BATCH], "week", week=week)
        with conn.cursor() as cur:
            for s in stats:
                cur.execute(
                    """INSERT INTO raw.yahoo_player_week
                       (league_key, season, week, yahoo_player_id, total_points, stats)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (league_key, week, yahoo_player_id) DO NOTHING""",
                    (LEAGUE_KEY, SEASON, week, str(s["player_id"]),
                     s.get("total_points"), json.dumps(s, default=str)),
                )
        conn.commit()
    print(f"  week {week}: done")
```

Verify the `lg.taken_players`/`lg.free_agents` result shape before trusting the K-pool extraction (`--help` the yahoo_fantasy_api docs or print one record); adjust the filter to reality — fail loud if zero K ids extracted. After backfill, re-run `uv run python scripts/score_sweep_yahoo.py` (it scores + persists the new rows; still must be 0 mismatches).

- [ ] **Step 3: Write + run the streaming report**

`scripts/streaming_baseline_report.py`:

```python
#!/usr/bin/env python3
"""Draft-early vs stream for DEF and K under OUR scoring (2025 season).
Elite = top-3 by season total (hindsight proxy for 'the DEF you'd draft early'
— stated caveat: real drafting can't pick the top-3 in advance, so this is an
UPPER BOUND on drafting's edge).
Streaming baselines from each week's rank distribution among ranks 13-32
(the un-rostered pool in a 12-team league):
  perfect  = best available (rank 13)   — upper bound on streaming
  realistic = median of ranks 13-20     — a decent-process streamer
If even elite-upper-bound minus realistic-streamer is small, streaming wins."""
import datetime
import pathlib
import statistics

from ffi.db import connect

conn = connect()
out_lines = [f"# DEF/K: draft early vs stream — {datetime.date.today().isoformat()}"]

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
        pts for wk, entries in by_week.items() for name, pts in entries if name in elite3
    )
    perfect, realistic = [], []
    for wk, entries in sorted(by_week.items()):
        ranked = sorted((p for _, p in entries), reverse=True)
        if len(ranked) < 20:
            raise SystemExit(f"{pos} week {wk}: only {len(ranked)} scored — pool incomplete, backfill")
        perfect.append(ranked[12])                       # rank 13
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

out = pathlib.Path(f"docs/research/{datetime.date.today().isoformat()}-def-k-streaming-baseline.md")
out.write_text("\n".join(out_lines) + "\n")
print(f"-> {out}")
```

Run it. The report + verdicts feed the draft board's DEF/K policy (Phase 3) and go verbatim in the task report. One season is thin evidence — say so in the report; extending to 2024 via Yahoo backfill is a possible follow-up the user can approve (≈40 more calls), not a default.

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_def_k_weeks.py scripts/streaming_baseline_report.py docs/research/
git commit -m "feat: DEF/K streaming-baseline verdict under league scoring (explicit Phase 2 deliverable)"
```

---

### Task 13: History data prep — draft-team backfill, matchup parsing, outcome skip-guards

Mining needs pick→team-slot attribution (fact #7: never imported) and parsed weekly H2H results (261 raw scoreboard payloads). Also lands the T9 carry-forward: `import_outcomes` skip guards (saves API budget on the 2026 renewal re-run).

**Files:**
- Create: `scripts/backfill_draft_teams.py`
- Create: `src/ffi/history/__init__.py`, `src/ffi/history/matchups.py`
- Create: `scripts/parse_matchups.py`
- Modify: `scripts/import_yahoo_season.py` (skip guards in `import_outcomes`)
- Test: `tests/test_matchup_parse.py`

**Interfaces:**
- Consumes: `raw.yahoo_standings` (payload: `team_key`, `name`, `rank`, `playoff_seed`, `points_for`, `outcome_totals`), `raw.yahoo_matchups` (nested `fantasy_content` payload), `ffi.ids.team_slot`, `yahoo_call`
- Produces: populated `teams` (with `slot`, `team_key`) + `draft_picks.team_id` for all 16 NAJEE seasons; `public.matchup_results` fully populated; `parse_matchup_payload(payload: dict) -> list[dict]` (rows with keys `team_key, points, proj_points, opp_team_key, opp_points, is_playoffs`)

- [ ] **Step 1: Write `scripts/backfill_draft_teams.py`** (teams free from standings; picks need 16 API calls)

```python
#!/usr/bin/env python3
"""1) Populate teams (slot, name, final_rank, points_for, championship flags)
from raw.yahoo_standings payloads — zero API calls.
2) Assign draft_picks.team_id by re-fetching draft_results per season (Phase 1
discarded team_key) — one throttled call per league (16 total). Resumable:
seasons whose picks all have team_id are skipped."""
import json

from ffi.db import connect
from ffi.ids import team_slot
from ffi.yahoo_client import get_league, get_session, yahoo_call

conn = connect()

# --- teams from standings (no API) ---
with conn.cursor() as cur:
    cur.execute(
        """SELECT s.league_key, s.season, s.team_key, s.payload
           FROM raw.yahoo_standings s ORDER BY s.season"""
    )
    standings = cur.fetchall()
with conn.cursor() as cur:
    for league_key, season, team_key, payload in standings:
        rank = int(payload["rank"]) if payload.get("rank") else None
        seed = payload.get("playoff_seed")
        cur.execute(
            """INSERT INTO teams (league_id, team_key, slot, team_name, final_rank,
                                  total_points_scored, playoff_seed, made_playoffs, won_championship)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (team_key) DO UPDATE
                 SET final_rank=EXCLUDED.final_rank, team_name=EXCLUDED.team_name,
                     total_points_scored=EXCLUDED.total_points_scored""",
            (league_key, team_key, team_slot(team_key), payload.get("name"), rank,
             float(payload["points_for"]) if payload.get("points_for") else None,
             int(seed) if seed else None, seed is not None, rank == 1),
        )
conn.commit()
with conn.cursor() as cur:
    cur.execute("SELECT count(*) FROM teams WHERE slot IS NOT NULL")
    print(f"teams with slots: {cur.fetchone()[0]} (expect 192 = 16 seasons x 12)")

# --- draft_picks.team_id via draft_results re-fetch (16 calls) ---
session = get_session()
with conn.cursor() as cur:
    cur.execute("SELECT league_key FROM raw.yahoo_league_settings ORDER BY season")
    league_keys = [r[0] for r in cur.fetchall()]

for lk in league_keys:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FILTER (WHERE team_id IS NULL), count(*) FROM draft_picks WHERE league_id=%s",
            (lk,),
        )
        nulls, total = cur.fetchone()
    if total == 0:
        print(f"{lk}: no picks in DB — investigate (all 16 seasons were imported)")
        continue
    if nulls == 0:
        print(f"{lk}: team_id complete, skipping")
        continue
    lg = get_league(session, lk)
    picks = [p for p in yahoo_call(lg.draft_results) if "player_id" in p]
    if len(picks) != total:
        raise SystemExit(f"{lk}: API returned {len(picks)} picks but DB has {total} — refusing to guess")
    with conn.cursor() as cur:
        for p in picks:
            if "team_key" not in p:
                raise SystemExit(f"{lk}: draft result lacks team_key: {json.dumps(p)[:200]}")
            cur.execute(
                """UPDATE draft_picks dp SET team_id = t.team_id
                   FROM teams t
                   WHERE dp.league_id=%s AND dp.overall_pick=%s AND t.team_key=%s""",
                (lk, int(p["pick"]), p["team_key"]),
            )
    conn.commit()
    print(f"{lk}: assigned team_id for {len(picks)} picks")

with conn.cursor() as cur:
    cur.execute(
        """SELECT count(*) FROM draft_picks dp
           JOIN raw.yahoo_league_settings s ON s.league_key=dp.league_id
           WHERE dp.team_id IS NULL"""
    )
    remaining = cur.fetchone()[0]
print(f"NAJEE picks still missing team_id: {remaining}")
raise SystemExit(1 if remaining else 0)
```

- [ ] **Step 2: Run the backfill (throttled; 16 Yahoo calls — not near any live window)**

Run: `uv run python scripts/backfill_draft_teams.py`
Expected: `teams with slots: 192`, per-season assignment lines, `NAJEE picks still missing team_id: 0`, exit 0.

- [ ] **Step 3: Write failing matchup-parser test**

`tests/test_matchup_parse.py` — build a minimal payload with the real nesting (fact-checked shape: `fantasy_content -> league[1] -> scoreboard -> '0' -> matchups -> '0' -> matchup -> '0' -> teams -> {'0','1'} -> team -> [ [ {team_key}, ... ], {team_points, team_projected_points, win_probability} ]`):

```python
from ffi.history.matchups import parse_matchup_payload


def _team(team_key, pts, proj):
    return [
        [{"team_key": team_key}, {"team_id": team_key.rsplit(".t.", 1)[1]}, {"name": "X"}],
        {"team_points": {"week": "1", "total": str(pts), "coverage_type": "week"},
         "team_projected_points": {"week": "1", "total": str(proj), "coverage_type": "week"}},
    ]


def _payload(pairs, is_playoffs="0"):
    matchups = {
        str(i): {"matchup": {"is_playoffs": is_playoffs,
                             "0": {"teams": {"0": {"team": _team(a, pa, pra)},
                                              "1": {"team": _team(b, pb, prb)},
                                              "count": 2}}}}
        for i, (a, pa, pra, b, pb, prb) in enumerate(pairs)
    }
    matchups["count"] = len(pairs)
    return {"fantasy_content": {"league": [
        {"league_key": "461.l.326814"},
        {"scoreboard": {"0": {"matchups": matchups}, "week": "1"}},
    ]}}


def test_parse_two_matchups():
    payload = _payload([
        ("461.l.326814.t.1", 227.75, 219.67, "461.l.326814.t.2", 190.0, 200.0),
        ("461.l.326814.t.3", 150.5, 160.0, "461.l.326814.t.4", 151.0, 140.0),
    ])
    rows = parse_matchup_payload(payload)
    assert len(rows) == 4                      # one row per team-side
    r1 = next(r for r in rows if r["team_key"].endswith(".t.1"))
    assert r1["points"] == 227.75
    assert r1["opp_team_key"].endswith(".t.2")
    assert r1["opp_points"] == 190.0
    assert r1["is_playoffs"] is False


def test_parse_fails_loud_on_missing_points():
    payload = _payload([("461.l.326814.t.1", 1, 1, "461.l.326814.t.2", 2, 2)])
    del payload["fantasy_content"]["league"][1]["scoreboard"]["0"]["matchups"]["0"][
        "matchup"]["0"]["teams"]["0"]["team"][1]["team_points"]
    import pytest
    with pytest.raises(KeyError):
        parse_matchup_payload(payload)
```

**Before implementing:** dump one REAL payload's matchup node and confirm where `is_playoffs` lives (top of `matchup` dict vs sibling of `"0"`) — adjust the fixture builder to the real shape, not the other way round:
```bash
psql -d fantasy_football -t -c "SELECT jsonb_pretty(payload::jsonb #> '{fantasy_content,league,1,scoreboard,0,matchups,0,matchup}') FROM raw.yahoo_matchups WHERE week=15 LIMIT 1;" | head -40
```

- [ ] **Step 4: Implement `src/ffi/history/matchups.py`**

```python
"""Parse raw.yahoo_matchups payloads (Yahoo's deeply nested scoreboard JSON)
into flat per-team rows. Load-bearing keys are accessed with [] — a missing
key is schema drift and must raise, not default (ADR Domain 1)."""


def _iter_matchups(payload: dict):
    league = payload["fantasy_content"]["league"]
    scoreboard = league[1]["scoreboard"]
    matchups = scoreboard["0"]["matchups"]
    for k, v in matchups.items():
        if k == "count":
            continue
        yield v["matchup"]


def _team_row(team_node: dict) -> dict:
    team = team_node["team"]
    attrs, points = team[0], team[1]
    team_key = next(a["team_key"] for a in attrs if isinstance(a, dict) and "team_key" in a)
    proj = points.get("team_projected_points")  # absent in some very old seasons: allowed
    return {
        "team_key": team_key,
        "points": float(points["team_points"]["total"]),
        "proj_points": float(proj["total"]) if proj else None,
    }


def parse_matchup_payload(payload: dict) -> list[dict]:
    rows = []
    for matchup in _iter_matchups(payload):
        is_playoffs = str(matchup.get("is_playoffs", "0")) == "1"
        teams = matchup["0"]["teams"]
        sides = [_team_row(teams[k]) for k in ("0", "1")]
        if len(sides) != 2:
            raise ValueError(f"matchup without exactly 2 teams: {list(teams)[:5]}")
        for me, opp in ((0, 1), (1, 0)):
            rows.append({
                **sides[me],
                "opp_team_key": sides[opp]["team_key"],
                "opp_points": sides[opp]["points"],
                "is_playoffs": is_playoffs,
            })
    if not rows:
        raise ValueError("payload parsed to zero matchup rows — shape drift?")
    return rows
```

(Adjust `is_playoffs` extraction to the real location found in Step 3's dump; if old seasons genuinely lack it, `week >= 15` is the fallback — set it in the load script from the week number, NOT silently in the parser.)

Run: `uv run pytest tests/test_matchup_parse.py -q` → PASS.

- [ ] **Step 5: Write + run `scripts/parse_matchups.py`**

```python
#!/usr/bin/env python3
"""Flatten all raw.yahoo_matchups payloads into public.matchup_results.
Validation per league-week: rows == num_teams; total rows reported at end."""
from ffi.db import connect
from ffi.history.matchups import parse_matchup_payload
from ffi.ids import team_slot

conn = connect()
with conn.cursor() as cur:
    cur.execute(
        """SELECT m.league_key, m.season, m.week, m.payload, s.num_teams
           FROM raw.yahoo_matchups m
           JOIN raw.yahoo_league_settings s ON s.league_key = m.league_key
           ORDER BY m.season, m.week"""
    )
    payloads = cur.fetchall()

total = 0
with conn.cursor() as cur:
    for league_key, season, week, payload, num_teams in payloads:
        rows = parse_matchup_payload(payload)
        if len(rows) != num_teams:
            # bye/odd weeks shouldn't exist in this league; refuse to half-load
            raise SystemExit(
                f"{league_key} wk{week}: parsed {len(rows)} team-rows, expected {num_teams}"
            )
        for r in rows:
            cur.execute(
                """INSERT INTO public.matchup_results
                   (league_key, season, week, team_key, slot, points, proj_points,
                    opp_team_key, opp_points, is_playoffs)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (league_key, week, team_key) DO UPDATE
                     SET points=EXCLUDED.points, opp_points=EXCLUDED.opp_points,
                         is_playoffs=EXCLUDED.is_playoffs""",
                (league_key, season, week, r["team_key"], team_slot(r["team_key"]),
                 r["points"], r["proj_points"], r["opp_team_key"], r["opp_points"],
                 r["is_playoffs"]),
            )
        total += len(rows)
conn.commit()
print(f"matchup_results: {total} team-week rows from {len(payloads)} scoreboards")
```

Run: `uv run python scripts/parse_matchups.py`
Expected: ~3,132 rows (261 weeks × 12). If old-season payload shapes differ (they will somewhere in 16 years), the parser fails loud with the season/week printed — extend the parser deliberately for that season's shape; never skip a season silently.

- [ ] **Step 6: Skip guards in `import_outcomes` (T9 carry-forward)**

In `scripts/import_yahoo_season.py`, add guards mirroring the matchups one (idempotent upserts stay as the safety net; the guard saves the API calls). Standings — before calling `lg.standings()`:

```python
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw.yahoo_standings WHERE league_key=%s", (league_key,))
        n = cur.fetchone()[0]
    if n > 0 and not force:
        print(f"  standings: {n} teams already present, skipping (--force-outcomes to re-fetch)")
    else:
        ...existing standings block...
```

Transactions — same pattern (`SELECT count(*) FROM raw.yahoo_transactions WHERE league_key=%s`; skip if >0 and not force). Add the flag: `ap.add_argument("--force-outcomes", action="store_true", help="re-fetch standings/transactions even if present")` and thread `force=args.force_outcomes` into `import_outcomes`. NOTE in the task report: the guard means a mid-season re-run won't pick up NEW transactions without `--force-outcomes` — correct for archived seasons, and the flag handles live ones.

- [ ] **Step 7: Full suite + commit**

```bash
uv run pytest -q
git add scripts/backfill_draft_teams.py src/ffi/history/ scripts/parse_matchups.py \
  scripts/import_yahoo_season.py tests/test_matchup_parse.py
git commit -m "feat: draft-team backfill, matchup parsing to matchup_results, outcome skip-guards"
```

---

### Task 14: Historical mining report (user-facing deliverable)

16 NAJEE seasons: draft position → outcome, manager tendencies **by slot** (annotation-segmented where available), and the four research-doc hypotheses. Analyses are plain SQL/Python over data Task 13 prepared; the deliverable is a readable report with data-coverage caveats stated inline.

**Data coverage rules (state them in the report, don't bury them):**
- Draft/standings/transactions/matchups: all 16 seasons (2010–2025).
- Player-week points under league scoring: 2019–2025 only (nflverse floor) — the champions' draft-vs-waiver split runs on those 7 seasons.
- Slot ≠ human: results are per-slot; segment by `manager_slot_annotations` where present; unannotated slots carry the caveat verbatim.

**Files:**
- Create: `src/ffi/history/mining.py`
- Create: `scripts/mine_history.py`
- Test: `tests/test_mining.py`

**Interfaces:**
- Consumes: `teams` (slot, final_rank, won_championship), `draft_picks` (team_id now populated), `public.matchup_results`, `raw.yahoo_transactions`, `scoring.player_week_points`, crosswalk, `public.manager_slot_annotations`
- Produces: `all_play(conn) -> list[dict]`, `draft_slot_outcomes(conn) -> list[dict]`, `position_round_tendencies(conn) -> list[dict]`, `qb_timing_by_slot(conn) -> list[dict]`, `transaction_timing(conn) -> list[dict]`, `trade_stats(conn) -> dict`, `champion_value_split(conn) -> list[dict]`; report `docs/research/<date>-historical-mining-report.md`

- [ ] **Step 1: Write failing tests for the two nontrivial pure pieces**

`tests/test_mining.py`:

```python
from ffi.history.mining import all_play_from_weeks, roster_intervals


def test_all_play_from_weeks():
    # week scores: A=100, B=90, C=80 -> A beats both (2-0), B 1-1, C 0-2
    weeks = [
        {"week": 1, "scores": {"A": 100.0, "B": 90.0, "C": 80.0}},
        {"week": 2, "scores": {"A": 50.0, "B": 90.0, "C": 80.0}},
    ]
    ap = all_play_from_weeks(weeks)
    assert ap["A"] == {"wins": 2, "losses": 2}
    assert ap["B"] == {"wins": 3, "losses": 1}
    assert ap["C"] == {"wins": 1, "losses": 3}


def test_roster_intervals_add_then_drop():
    events = [
        {"player_ref": "p1", "team_key": "T1", "type": "draft", "week": 0},
        {"player_ref": "p1", "team_key": "T1", "type": "drop", "week": 5},
        {"player_ref": "p1", "team_key": "T2", "type": "add", "week": 7},
    ]
    iv = roster_intervals(events, end_week=17)
    assert ("T1", 1, 5, "draft") in iv["p1"]     # on T1 weeks 1-5 via draft
    assert ("T2", 7, 17, "add") in iv["p1"]      # on T2 weeks 7-17 via add


def test_roster_intervals_trade_moves_player():
    events = [
        {"player_ref": "p1", "team_key": "T1", "type": "draft", "week": 0},
        {"player_ref": "p1", "team_key": "T2", "type": "trade_in", "week": 8},
    ]
    iv = roster_intervals(events, end_week=17)
    assert ("T1", 1, 8, "draft") in iv["p1"]
    assert ("T2", 8, 17, "trade_in") in iv["p1"]
```

Run: `uv run pytest tests/test_mining.py -q` → FAIL.

- [ ] **Step 2: Implement `src/ffi/history/mining.py`**

```python
"""Historical mining primitives. Pure functions where possible (testable);
SQL readers thin. Attribution simplification (documented in the report):
a player's weekly points count for the team holding him that week — bench vs
started is unknowable without lineups (deliberately not imported)."""
from collections import defaultdict


def all_play_from_weeks(weeks: list[dict]) -> dict:
    """weeks: [{'week': N, 'scores': {key: points}}] -> {key: {'wins','losses'}}.
    All-play: each week, a team 'plays' every other team (ties count half—rare;
    rounded down to keep ints, noted in report)."""
    out: dict = defaultdict(lambda: {"wins": 0, "losses": 0})
    for w in weeks:
        scores = w["scores"]
        for k, s in scores.items():
            wins = sum(1 for o, os_ in scores.items() if o != k and s > os_)
            losses = sum(1 for o, os_ in scores.items() if o != k and s < os_)
            out[k]["wins"] += wins
            out[k]["losses"] += losses
    return dict(out)


def roster_intervals(events: list[dict], end_week: int) -> dict:
    """events per player: draft (week 0), add, drop, trade_in — chronological.
    Returns {player_ref: [(team_key, first_week, last_week, how)]}. A draft/add
    at week W covers weeks max(W,1)..(next departure or end_week)."""
    by_player: dict = defaultdict(list)
    for e in sorted(events, key=lambda e: e["week"]):
        by_player[e["player_ref"]].append(e)
    out: dict = {}
    for pref, evs in by_player.items():
        intervals, current = [], None  # current = (team_key, start_week, how)
        for e in evs:
            wk = max(int(e["week"]), 1) if e["type"] != "drop" else int(e["week"])
            if e["type"] in ("draft", "add", "trade_in"):
                if current is not None:
                    intervals.append((current[0], current[1], wk, current[2]))
                current = (e["team_key"], max(int(e["week"]), 1), e["type"])
            elif e["type"] == "drop":
                if current is not None:
                    intervals.append((current[0], current[1], wk, current[2]))
                    current = None
        if current is not None:
            intervals.append((current[0], current[1], end_week, current[2]))
        out[pref] = intervals
    return out
```

Then the SQL readers (thin, no tests beyond running them):

```python
def all_play(conn) -> list[dict]:
    """Per team-season: actual record vs all-play record (regular season only)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT league_key, season, week, team_key, points
               FROM public.matchup_results WHERE NOT is_playoffs
               ORDER BY league_key, week"""
        )
        rows = cur.fetchall()
    by_lw: dict = defaultdict(dict)
    seasons: dict = {}
    for lk, season, week, tk, pts in rows:
        by_lw[(lk, week)][tk] = float(pts)
        seasons[tk] = (lk, season)
    weeks_by_league: dict = defaultdict(list)
    for (lk, week), scores in by_lw.items():
        weeks_by_league[lk].append({"week": week, "scores": scores})
    out = []
    for lk, weeks in weeks_by_league.items():
        ap = all_play_from_weeks(weeks)
        with conn.cursor() as cur:
            cur.execute(
                """SELECT team_key, team_name, slot, final_rank FROM teams WHERE league_id=%s""",
                (lk,),
            )
            meta = {tk: (name, slot, rank) for tk, name, slot, rank in cur.fetchall()}
        # actual W/L from matchup_results head-to-head
        actual: dict = defaultdict(lambda: [0, 0])
        for w in weeks:
            pass  # H2H comes from opp_points below
        with conn.cursor() as cur:
            cur.execute(
                """SELECT team_key,
                          count(*) FILTER (WHERE points > opp_points),
                          count(*) FILTER (WHERE points < opp_points)
                   FROM public.matchup_results
                   WHERE league_key=%s AND NOT is_playoffs GROUP BY team_key""",
                (lk,),
            )
            for tk, w_, l_ in cur.fetchall():
                actual[tk] = [w_, l_]
        for tk, rec in ap.items():
            name, slot, rank = meta[tk]
            aw, al = actual[tk]
            n_opp = 11
            out.append({
                "league_key": lk, "season": seasons[tk][1], "slot": slot, "team": name,
                "final_rank": rank, "actual_w": aw, "actual_l": al,
                "all_play_pct": rec["wins"] / max(rec["wins"] + rec["losses"], 1),
                "actual_pct": aw / max(aw + al, 1),
                "luck": aw / max(aw + al, 1) - rec["wins"] / max(rec["wins"] + rec["losses"], 1),
            })
    return out
```

(Remove the dead `for w in weeks: pass` block — shown to flag that actual records come from the SQL, not the loop.) Plus the remaining readers — each is a single SQL + light shaping; write them exactly:

```python
def draft_slot_outcomes(conn) -> list[dict]:
    """Draft slot (1-12) -> avg final rank, championship count, all 16 seasons."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.slot, count(*) AS seasons,
                   avg(t.final_rank) AS avg_finish,
                   count(*) FILTER (WHERE t.won_championship) AS titles,
                   avg(t.total_points_scored) AS avg_pf
            FROM teams t
            JOIN raw.yahoo_league_settings s ON s.league_key = t.league_id
            GROUP BY t.slot ORDER BY t.slot
            """
        )
        return [dict(zip(("slot", "seasons", "avg_finish", "titles", "avg_pf"), r))
                for r in cur.fetchall()]


def position_round_tendencies(conn) -> list[dict]:
    """Per slot x round-band x position: pick share (the tendency fingerprint)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.slot,
                   CASE WHEN dp.round_number <= 3 THEN 'R1-3'
                        WHEN dp.round_number <= 8 THEN 'R4-8'
                        ELSE 'R9+' END AS band,
                   p.position, count(*) AS picks
            FROM draft_picks dp
            JOIN teams t ON t.team_id = dp.team_id
            JOIN players p ON p.player_id = dp.player_id
            JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
            GROUP BY 1, 2, 3 ORDER BY 1, 2, 4 DESC
            """
        )
        return [dict(zip(("slot", "band", "position", "picks"), r)) for r in cur.fetchall()]


def qb_timing_by_slot(conn) -> list[dict]:
    """Rounds where each slot took its QB1/QB2 (the 2QB strategic fingerprint)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH qb_picks AS (
                SELECT t.slot, dp.league_id, dp.round_number,
                       row_number() OVER (PARTITION BY dp.league_id, t.slot
                                          ORDER BY dp.overall_pick) AS qb_n
                FROM draft_picks dp
                JOIN teams t ON t.team_id = dp.team_id
                JOIN players p ON p.player_id = dp.player_id
                WHERE p.position = 'QB'
            )
            SELECT slot, avg(round_number) FILTER (WHERE qb_n=1) AS qb1_round,
                   avg(round_number) FILTER (WHERE qb_n=2) AS qb2_round,
                   avg(round_number) FILTER (WHERE qb_n=3) AS qb3_round,
                   count(DISTINCT league_id) AS seasons
            FROM qb_picks GROUP BY slot ORDER BY slot
            """
        )
        return [dict(zip(("slot", "qb1_round", "qb2_round", "qb3_round", "seasons"), r))
                for r in cur.fetchall()]


def transaction_timing(conn) -> list[dict]:
    """Adds/drops/trades by NFL-season week bucket across 16 seasons — tests the
    'weeks 10-14 championship-pickup cluster' hypothesis. Week inferred from ts
    vs the season's week-1 (first matchup) — approximation noted in report."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH season_start AS (
                SELECT league_key, min(ts) AS wk1
                FROM (SELECT m.league_key, m.week,
                             (SELECT min(t.ts) FROM raw.yahoo_transactions t
                              WHERE t.league_key = m.league_key) AS ts
                      FROM raw.yahoo_matchups m WHERE m.week = 1) x
                GROUP BY league_key
            )
            SELECT tr.season, tr.type,
                   least(greatest(1, 1 + floor(extract(epoch FROM tr.ts - ss.wk1) / 604800)::int), 17) AS approx_week,
                   count(*)
            FROM raw.yahoo_transactions tr
            JOIN season_start ss ON ss.league_key = tr.league_key
            WHERE tr.ts IS NOT NULL
            GROUP BY 1, 2, 3 ORDER BY 1, 3
            """
        )
        return [dict(zip(("season", "type", "approx_week", "n"), r)) for r in cur.fetchall()]
```

NOTE for the implementer: the `season_start` CTE above anchors on the earliest transaction, which is draft-day — sanity-check a couple of seasons (draft ≈ 2-3 weeks before week 1) and correct the anchor to `first matchup week`'s real timestamp if the payloads carry one; if neither is reliable, bucket by calendar month instead and say so in the report. Do not present week buckets you haven't sanity-checked.

```python
def trade_stats(conn) -> dict:
    """Trade frequency per season + QB involvement share (hypothesis 5.4)."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT season, count(*) FROM raw.yahoo_transactions
               WHERE type='trade' GROUP BY season ORDER BY season"""
        )
        per_season = dict(cur.fetchall())
        cur.execute(
            """
            SELECT count(*) FROM raw.yahoo_transactions t
            WHERE t.type='trade' AND EXISTS (
                SELECT 1 FROM jsonb_each(t.payload->'players') kv
                WHERE kv.key ~ '^[0-9]+$'
                  AND kv.value->'player'->0 @> '[{"display_position": "QB"}]'::jsonb
            )
            """
        )
        qb_trades = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM raw.yahoo_transactions WHERE type='trade'")
        total = cur.fetchone()[0]
    return {"per_season": per_season, "total": total, "qb_involved": qb_trades}


def champion_value_split(conn) -> list[dict]:
    """2019-2025: champion's team-season points split by acquisition route
    (drafted vs added vs traded-in), using roster_intervals + weekly points.
    Attribution simplification documented in the report."""
    out = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT t.league_id, t.team_key, t.team_name, s.season
               FROM teams t JOIN raw.yahoo_league_settings s ON s.league_key=t.league_id
               WHERE t.won_championship AND s.season >= 2019 ORDER BY s.season"""
        )
        champs = cur.fetchall()
    for league_id, team_key, name, season in champs:
        events = _acquisition_events(conn, league_id, team_key)
        intervals = roster_intervals(events, end_week=17)
        split = defaultdict(float)
        for pref, ivs in intervals.items():
            for tk, wk_from, wk_to, how in ivs:
                if tk != team_key:
                    continue
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT coalesce(sum(p.points), 0)
                           FROM scoring.player_week_points p
                           JOIN public.player_id_xwalk x ON x.gsis_id = p.player_ref
                           WHERE p.source='nflverse' AND p.season=%s
                             AND p.week BETWEEN %s AND %s AND x.yahoo_id = %s""",
                        (season, wk_from, wk_to, pref),
                    )
                    split[how] += float(cur.fetchone()[0])
        out.append({"season": season, "champion": name, **dict(split)})
    return out


def _acquisition_events(conn, league_id: str, team_key: str) -> list[dict]:
    """Draft + transaction events shaping the champion's roster timeline.
    player_ref = numeric yahoo id (matches crosswalk join)."""
    from ffi.ids import player_numeric_id
    events = []
    with conn.cursor() as cur:
        cur.execute(
            """SELECT p.yahoo_player_id FROM draft_picks dp
               JOIN players p ON p.player_id = dp.player_id
               JOIN teams t ON t.team_id = dp.team_id
               WHERE dp.league_id=%s AND t.team_key=%s""",
            (league_id, team_key),
        )
        for (ykey,) in cur.fetchall():
            events.append({"player_ref": player_numeric_id(ykey), "team_key": team_key,
                           "type": "draft", "week": 0})
        cur.execute(
            """SELECT payload, ts FROM raw.yahoo_transactions
               WHERE league_key=%s AND type IN ('add','drop','add/drop','trade')
               ORDER BY ts""",
            (league_id,),
        )
        txns = cur.fetchall()
        cur.execute(
            """SELECT min(ts) FROM raw.yahoo_transactions WHERE league_key=%s""", (league_id,)
        )
        season_anchor = cur.fetchone()[0]
    for payload, ts in txns:
        week = max(1, min(17, 1 + int((ts - season_anchor).total_seconds() // 604800))) if ts else 1
        players = payload.get("players") or {}
        for k, v in players.items():
            if not k.isdigit():
                continue
            plist = v["player"]
            pid = next(str(a["player_id"]) for a in plist[0] if isinstance(a, dict) and "player_id" in a)
            tdata = plist[1]["transaction_data"]
            tdata = tdata[0] if isinstance(tdata, list) else tdata
            dest = tdata.get("destination_team_key")
            src = tdata.get("source_team_key")
            if dest == team_key:
                kind = "trade_in" if tdata["type"] == "trade" else "add"
                events.append({"player_ref": pid, "team_key": team_key, "type": kind, "week": week})
            elif src == team_key and tdata["type"] in ("drop", "trade"):
                events.append({"player_ref": pid, "team_key": team_key, "type": "drop", "week": week})
    return events
```

The transaction-payload walking above matches the real trade payload shape (fact-checked in plan prep: `players -> {'0': {'player': [[attr...], {'transaction_data': [...]}]}}`) — but add/drop payloads may nest `transaction_data` as a bare dict; the `isinstance` handles both. If a season's payload deviates, the `next(...)`/`[]` accesses raise — extend deliberately.

- [ ] **Step 3: Run the pure-function tests, then the readers**

Run: `uv run pytest tests/test_mining.py -q` → PASS.
Then exercise each reader once from a REPL-style script or `python -c` against the real DB and eyeball row counts (all_play: 192 team-seasons; tendencies: ≤ 12×3×6 rows; qb_timing: 12 rows; champions: 7 rows).

- [ ] **Step 4: Write `scripts/mine_history.py`** (renders the report)

```python
#!/usr/bin/env python3
"""Render the historical mining report (the user-facing Phase 2 deliverable)."""
import datetime
import pathlib

from ffi.db import connect
from ffi.history.mining import (
    all_play, champion_value_split, draft_slot_outcomes,
    position_round_tendencies, qb_timing_by_slot, trade_stats, transaction_timing,
)

conn = connect()
today = datetime.date.today().isoformat()
L = [f"# NAJEE league historical mining — {today}",
     "\n**Coverage:** drafts/standings/transactions/matchups 2010-2025 (16 seasons); "
     "league-scoring player-weeks 2019-2025 only (champions split limited to those 7). "
     "**Slot caveat:** results key on team slots; humans changed within slots "
     "(see manager_slot_annotations — currently only slot 12/Brent/~2022 is annotated).",
     ]

with conn.cursor() as cur:
    cur.execute("SELECT league_slot, human_label, from_season, to_season FROM public.manager_slot_annotations ORDER BY 1,3")
    annos = cur.fetchall()
L += ["\n## Annotations on file", *(f"- slot {s}: {h} ({f}-{t or 'present'})" for s, h, f, t in annos)]

L += ["\n## 1. Draft slot -> outcome (16 seasons)",
      "| slot | seasons | avg finish | titles | avg PF |", "|---|---|---|---|---|"]
for r in draft_slot_outcomes(conn):
    L.append(f"| {r['slot']} | {r['seasons']} | {float(r['avg_finish']):.2f} | {r['titles']} | {float(r['avg_pf'] or 0):.0f} |")

L += ["\n## 2. QB draft timing by slot (2QB fingerprint)",
      "| slot | QB1 round | QB2 round | QB3 round | seasons |", "|---|---|---|---|---|"]
for r in qb_timing_by_slot(conn):
    L.append(f"| {r['slot']} | {float(r['qb1_round'] or 0):.1f} | {float(r['qb2_round'] or 0):.1f} | "
             f"{float(r['qb3_round'] or 0):.1f} | {r['seasons']} |")

L += ["\n## 3. Position-by-round tendencies (share of picks, per slot)"]
tend = position_round_tendencies(conn)
slots = sorted({r["slot"] for r in tend})
for slot in slots:
    rows = [r for r in tend if r["slot"] == slot]
    total = {b: sum(r["picks"] for r in rows if r["band"] == b) for b in ("R1-3", "R4-8", "R9+")}
    line = f"- **slot {slot}**: " + "; ".join(
        f"{b}: " + ", ".join(
            f"{r['position']} {100*r['picks']/total[b]:.0f}%"
            for r in sorted(rows, key=lambda r: -r["picks"]) if r["band"] == b
        )[:80]
        for b in ("R1-3", "R4-8", "R9+")
    )
    L.append(line)

L += ["\n## 4. All-play vs record (luck audit; hypothesis 6.2)",
      "Biggest schedule-luck beneficiaries and victims (|luck| = actual% - all-play%):",
      "| season | slot | team | record | all-play% | luck |", "|---|---|---|---|---|---|"]
ap = sorted(all_play(conn), key=lambda r: -abs(r["luck"]))[:15]
for r in ap:
    L.append(f"| {r['season']} | {r['slot']} | {r['team']} | {r['actual_w']}-{r['actual_l']} | "
             f"{r['all_play_pct']:.3f} | {r['luck']:+.3f} |")

L += ["\n## 5. Transaction timing (hypothesis 6.3: weeks 10-14 cluster?)"]
tt = transaction_timing(conn)
by_week = {}
for r in tt:
    by_week[r["approx_week"]] = by_week.get(r["approx_week"], 0) + r["n"]
L += ["| approx week | transactions (all seasons) |", "|---|---|"]
L += [f"| {w} | {n} |" for w, n in sorted(by_week.items())]

ts_ = trade_stats(conn)
L += ["\n## 6. Trades (hypothesis 5.4)",
      f"- total trades 2010-2025: **{ts_['total']}** "
      f"({ts_['total']/16:.1f}/season); QB involved in {ts_['qb_involved']} "
      f"({100*ts_['qb_involved']/max(ts_['total'],1):.0f}%)",
      "- per season: " + ", ".join(f"{s}: {n}" for s, n in sorted(ts_["per_season"].items()))]

L += ["\n## 7. Champions: draft vs waiver value split (2019-2025; hypothesis 1.3)",
      "Attribution: player's weekly league-scoring points credited to the roster holding him "
      "that week (bench/start unknown — lineups not imported).",
      "| season | champion | drafted pts | added pts | traded-in pts |", "|---|---|---|---|---|"]
for r in champion_value_split(conn):
    L.append(f"| {r['season']} | {r['champion']} | {r.get('draft', 0):.0f} | "
             f"{r.get('add', 0):.0f} | {r.get('trade_in', 0):.0f} |")

out = pathlib.Path(f"docs/research/{today}-historical-mining-report.md")
out.write_text("\n".join(L) + "\n")
print(f"-> {out}")
```

- [ ] **Step 5: Run, read, sanity-check, iterate**

Run: `uv run python scripts/mine_history.py`
Sanity checks before calling it done (list results in the task report):
- Section 1 slots each have ~16 seasons; average finishes cluster around 6.5.
- Section 2: league-wide QB1 round should be EARLY (2QB league) — if it averages round 8, the join is broken.
- Section 7: drafted points should dominate but added points should be material (research predicts champions carry meaningful waiver value) — a 100/0 split means roster_intervals or the transaction walk is broken.
- Cross-check one season by hand (2025: champion from `teams.won_championship`, eyeball 2-3 of their adds in the transaction log).

- [ ] **Step 6: Full suite + commit**

```bash
uv run pytest -q
git add src/ffi/history/mining.py scripts/mine_history.py tests/test_mining.py docs/research/
git commit -m "feat: 16-season historical mining report (slots, QB timing, all-play luck, champion value split)"
```

---

### Task 15: Morning briefing v1 + launchd + health-report extension

Design §4.7 v1 = plumbing + health, not agent digestion (that's v1.5). The briefing IS the dashboard (ADR Domain 5): health header first, then data freshness and board-input summaries. Also extends `scripts/phase1_report.py` with Phase 2 structural checks.

**Files:**
- Create: `scripts/morning_briefing.py`
- Create: `launchd/com.ffi.morning.plist`
- Create: `docs/runbooks/morning-briefing.md`
- Modify: `scripts/phase1_report.py` (append checks)
- Modify: `.gitignore` (add `reports/`)

**Interfaces:**
- Consumes: `raw.ingest_runs`, `raw.fp_snapshots` (+ `fp_calls_today`), `raw.sleeper_projections`, `scoring.projection_points`, `valuation.player_value`, `backups/` dir
- Produces: `reports/briefing-YYYY-MM-DD.md` (gitignored); launchd job running ingest → score → valuation → briefing each morning; extended health gate

- [ ] **Step 1: Extend `scripts/phase1_report.py`** (append to `CHECKS` — the gate stays one command)

```python
    # --- Phase 2 checks ---
    ("scoring config v1 registered", "SELECT count(*) = 1 FROM scoring.config WHERE version = 1"),
    (
        "2025 yahoo sweep persisted (>=3876 rows)",
        "SELECT count(*) >= 3876 FROM scoring.player_week_points WHERE source='yahoo_engine'",
    ),
    (
        "nflverse history scored (>=100k rows)",
        "SELECT count(*) >= 100000 FROM scoring.player_week_points WHERE source='nflverse'",
    ),
    (
        "season-level sleeper projections scored",
        "SELECT count(*) >= 1000 FROM scoring.projection_points WHERE source='sleeper' AND horizon='season'",
    ),
    ("DEF map covers the league", "SELECT count(*) >= 24 FROM public.team_def_map"),
    (
        "draft picks have team attribution (NAJEE)",
        """SELECT count(*) = 0 FROM draft_picks dp
           JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
           WHERE dp.team_id IS NULL""",
    ),
    (
        "matchup results parsed (>=3000 team-weeks)",
        "SELECT count(*) >= 3000 FROM public.matchup_results",
    ),
    ("valuation built", "SELECT count(*) >= 100 FROM valuation.player_value"),
```

Run: `uv run python scripts/phase1_report.py` → all OK (20/20) once Tasks 1–14 are done; during execution it's the running scoreboard.

- [ ] **Step 2: Write `scripts/morning_briefing.py`**

```python
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
        age = (datetime.datetime.now(datetime.timezone.utc) - latest).total_seconds() / 3600
        mark = "OK" if age <= STALE_HOURS else "STALE"
        if age > STALE_HOURS:
            red_flags.append(f"sleeper season snapshot {age:.0f}h old (> {STALE_HOURS}h)")
        L.append(f"- [{mark}] sleeper season snapshot: {age:.0f}h old")

L.append(f"- FP budget used today: {fp_calls_today(conn)}/30")

backups = sorted(pathlib.Path("backups").glob("*.dump*")) if pathlib.Path("backups").exists() else []
if backups:
    age_d = (datetime.datetime.now() - datetime.datetime.fromtimestamp(backups[-1].stat().st_mtime)).days
    mark = "OK" if age_d <= 2 else "STALE"
    if age_d > 2:
        red_flags.append(f"newest backup {age_d}d old")
    L.append(f"- [{mark}] newest pg_dump: {backups[-1].name} ({age_d}d old)")
else:
    red_flags.append("no backups found in backups/")
    L.append("- [RED] backups: none found")

health = subprocess.run([sys.executable, "scripts/phase1_report.py"], capture_output=True, text=True)
fails = [ln for ln in health.stdout.splitlines() if ln.startswith("FAIL")]
L.append(f"- structural health gate: {'OK' if health.returncode == 0 else 'RED'}"
         + (f" — {len(fails)} failing: " + "; ".join(fails) if fails else ""))
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

out_dir = pathlib.Path("reports")
out_dir.mkdir(exist_ok=True)
out = out_dir / f"briefing-{today}.md"
out.write_text("\n".join(L) + "\n")
print(f"-> {out}")
if red_flags:
    print("RED FLAGS:", *red_flags, sep="\n  - ")
    raise SystemExit(1)
```

- [ ] **Step 3: Run it manually**

Run: `uv run python scripts/morning_briefing.py`
Expected: `reports/briefing-<date>.md` written; exit 0 with everything green (or honest red flags + exit 1 — both are correct behavior; only a crash is a bug). Add `reports/` to `.gitignore`.

- [ ] **Step 4: launchd plist + runbook**

`launchd/com.ffi.morning.plist` (wake-safe morning pipeline: ingest → score → valuation → briefing; each step fails the chain loudly via `&&`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ffi.morning</string>
  <key>WorkingDirectory</key><string>/Users/brentbartosch/Development/fantasy_football</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>uv run python scripts/ingest_sleeper.py --season 2026 &amp;&amp; uv run python scripts/ingest_fantasypros.py --daily &amp;&amp; uv run python scripts/score_sleeper_projections.py &amp;&amp; uv run python scripts/build_valuation.py &amp;&amp; uv run python scripts/morning_briefing.py</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>logs/launchd-morning.log</string>
  <key>StandardErrorPath</key><string>logs/launchd-morning.err</string>
</dict></plist>
```

`docs/runbooks/morning-briefing.md`: install (`cp launchd/com.ffi.morning.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ffi.morning.plist`), verify (`launchctl list | grep ffi`), remove (`launchctl bootout gui/$(id -u)/com.ffi.morning`), where output lands, and "a red-flag exit means READ THE BRIEFING, the job did not fail silently". Install it and confirm one scheduled (or `launchctl kickstart`-forced) run writes a briefing.

- [ ] **Step 5: Full suite + commit**

```bash
uv run pytest -q
git add scripts/morning_briefing.py scripts/phase1_report.py launchd/ \
  docs/runbooks/morning-briefing.md .gitignore
git commit -m "feat: morning briefing v1 (health-first) + launchd pipeline + extended health gate"
```

---

### Task 16: pg_restore drill (ADR Domain 8 — due week 3)

Backups that have never been restored are hopes, not backups. Restore the newest dump into a scratch DB once, verify, time it, document.

**Files:**
- Create: `docs/runbooks/pg-restore-drill.md`

- [ ] **Step 1: Execute the drill (real commands, real backup)**

```bash
ls -lt backups/ | head -3                       # newest dump; note its size + date
PG_BIN=$(grep -o 'PG_BIN=.*' scripts/backup_db.sh | head -1 | cut -d= -f2-)  # v15 pin (PATH has v14!)
"$PG_BIN"/createdb fantasy_football_drill
time "$PG_BIN"/pg_restore -d fantasy_football_drill --no-owner backups/<newest>.dump
```
(If the backup is plain-SQL rather than custom format, use `"$PG_BIN"/psql -d fantasy_football_drill -f <file>` — check `file backups/<newest>` first and record which it was.)

- [ ] **Step 2: Verify the restored DB**

```bash
psql -d fantasy_football_drill -t -A -c "
SELECT (SELECT count(*) FROM draft_picks),
       (SELECT count(*) FROM raw.yahoo_player_week),
       (SELECT count(*) FROM raw.nflverse_player_week),
       (SELECT count(*) FROM scoring.player_week_points);"
psql -d fantasy_football -t -A -c "  -- same query against the live DB; counts must match"
```
Counts must match the live DB (modulo work done since the dump — compare against a fresh dump if in doubt: run `scripts/backup_db.sh` first, then drill on that file).

- [ ] **Step 3: Clean up + write the runbook**

```bash
psql -d postgres -c "DROP DATABASE fantasy_football_drill"
```

`docs/runbooks/pg-restore-drill.md`: the exact commands above, the measured restore time, dump format found, verification query + the counts observed, drill date, and a "re-drill before draft week" reminder (ADR Domain 8 requires a tested restore before draft day).

- [ ] **Step 4: Commit**

```bash
git add docs/runbooks/pg-restore-drill.md
git commit -m "docs: pg_restore drill executed and documented (ADR Domain 8)"
```

---

## Self-review (performed at plan-writing time)

**Spec coverage** against handoff §3 (1: Tasks 3–5; 2: Task 8; 3: Task 9; 4: Tasks 10–11 + 12 for the DEF/K deliverable; 5: Tasks 13–14; 6: Task 15), §2 mandated first commit (Task 1), §1 process items (restore drill Task 16; week-3 checkpoint named in Task 5; branch + review gates in the header), and all eleven §4 carry-forwards (DEF map T3; rookie overrides T2; slug cleanup T2; abbr normalization T1 `ffi.ids`; dup-guard T2; per-position FD validation T7; slot annotations T3+T14; YahooAuthError wrapping T1; outcome skip guards T13; sleeper week=None T7; nflverse column consolidation T1).

**Known judgment calls (reviewers: these are deliberate):** golden-sweep persists as `source='yahoo_engine'` (engine output, provenance-clean); FP ADP/projections deferred from the daily sync (YAGNI until Task 11 needs them); DEF excluded from v1 valuation pending Task 12's verdict; champions split limited to 2019–2025 (data floor); mining's transaction week-bucketing is approximate and must be sanity-checked before publication.

## Execution handoff

Plan complete. Execute with **superpowers:subagent-driven-development**: branch `phase2-scoring-valuation`, fresh subagent per task, per-task review gate, ledger at `.superpowers/sdd/phase2-progress.md`, merge via finishing-a-development-branch. Task 5's report must explicitly declare the week-3 checkpoint status.




