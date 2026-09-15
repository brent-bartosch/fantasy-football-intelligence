# In-Season Ops — Phase 2 (Plan 2) Implementation Plan

**Date:** 2026-09-03
**Status:** DONE — executed by the next agent
**Predecessor:** `docs/superpowers/plans/2026-08-31-in-season-ops-phase1.md` (DONE)
**Authority:** `docs/superpowers/risks/2026-08-30-in-season-ops-adr.md` (ADR in-season-ops), `ARCHITECTURE.md` §1b/§2/§4

## 1. Goal

Complete the in-season management stack: league-state capture (rosters/transactions),
the waiver advisory engine, trade angles, and the two decision reports — parameterized
for **two leagues** (NAJEE 12-team/2-QB and LMU 14-team/1-QB) via `ffi.league_profile`.

Phase 1 (foundations) is done: `health.py`, `joblock.py`, `ingest/gates.py`,
`usage/{build,trends,coldstart}.py`, `reports/health_section.py`,
`ingest/sleeper_trending.py`, `config/{source_clock,league_clock}.yaml`.
This session also added `src/ffi/flags.py` + `config/modules.yaml` (the module rollback
switch — see §3).

## 2. Current state (do NOT re-derive)

- **Tests:** 667 passed, 1 skipped.
- **CI guards (run all three before committing):** `scripts/check_file_size.sh`,
  `scripts/check_import_boundaries.py`, `scripts/check_no_secrets.sh` — all green.
- **League profiles** live in `src/ffi/league_profile.py` (`NAJEE`, `LMU`, `get_profile`).
  Every in-season module below is **multi-league aware** and reads the profile, never a
  hardcoded team count / QB count.
- **Yahoo API is DEAD** (403). The manual-capture path (`league_state/manual.py`) is the
  PRIMARY roster/transaction source; `league_state/api.py` is a backfill-only optimization.

## 3. Build order (dependency order = topological order)

Build strictly in this order; each module lands with tests before the next starts.

1. `src/ffi/league_state/clock.py` — runtime reader of `config/league_clock.yaml`.
2. `src/ffi/league_state/adapter.py` — canonical read/write API for
   `league_transactions` / `league_rosters` (carries `source` + `ts_precision`).
3. `src/ffi/league_state/manual.py` — manual capture / screenshot-text parsing; the ONLY
   reader of `data/captures/`.
4. `src/ffi/league_state/reconcile.py` — API-vs-manual reconciliation (logs diffs, never
   silently overwrites).
5. `src/ffi/league_state/api.py` — Yahoo backfill (calls `ffi.yahoo_client` only).
6. `src/ffi/usage/market.py` — `market_trends` normalization over `raw.sleeper_trending`
   + urgency score (fails closed).
7. `src/ffi/waiver/cutline.py` — position-aware roster cutline.
8. `src/ffi/waiver/priority.py` — rolling-priority pricing (finite-horizon expiring-option
   stopping rule).
9. `src/ffi/waiver/clear_time.py` — drop→clear state machine (tz-aware, DST-safe).
10. `src/ffi/waiver/ledger.py` — event-sourced move-budget ledger (all teams, + safety margin).
11. `src/ffi/waiver/cards.py` — contingency cards ("if X inactive → add Y"); prep only.
12. `src/ffi/trade_angles.py` — opponent needs, buy-low/sell-high, QB-repair. Recommend-only.
13. `src/ffi/health.py::render_or_refuse` — build WITH the first composite renderer (do not
    build before step 13; it is the fail-closed wrapper the report renderers use).
14. `src/ffi/reports/claims.py` — Monday claims brief → `reports/claims-YYYY-WW.md`.
15. `src/ffi/reports/trends.py` — Tuesday trends/targets → `reports/trends-YYYY-WW.md`.
16. `scripts/notify.py` — ntfy push, hard 3/week cap against `push_log`.
17. `scripts/probe_yahoo_access.py` — daily authenticated Yahoo probe → `raw.ingest_runs`
    (no retry on 403; Sept 10 decision date).
18. `scripts/run_claims_brief.py` + `scripts/run_trends_report.py` — launchd entry points.

### Calendar-dated build order (ADR Domain 8)

Deliver in this order or cut scope (a module after its window is waste, not late):

| Step | Module | Hard window |
|---|---|---|
| 1 | league_state (1–5) + usage/market (6) | before first waiver run (~Sep 9) |
| 2 | waiver (7–11) + QB-repair angle in trade_angles | before Week 1 |
| 3 | trade_angles (12) full | before **Nov 22** trade deadline |
| 4 | reports (14–15) + notify (16) | before first claim deadline |

## 4. Module contracts (condensed from ARCHITECTURE.md §4)

Signatures are fixed by the ADR. Do not drift the semantics.

| Module | Canonical entry points | Max lines |
|---|---|---|
| `league_state/clock.py` | `deadline(event, week) -> tz-aware datetime`; `window(event, week) -> (start, end)`; `fallback_fire_time(job, week) -> datetime` | 200 |
| `league_state/adapter.py` | `load_transactions(week) -> list[Transaction]`; `load_rosters(as_of) -> list[RosterRow]`; `record(rows, source, ts_precision) -> None` | 300 |
| `league_state/manual.py` | `parse_capture(path) -> list[Transaction]` (only parser) | 350 |
| `league_state/reconcile.py` | API-vs-manual diff pass | 200 |
| `league_state/api.py` | Yahoo backfill (off critical path) | 250 |
| `usage/market.py` | `urgency(signal, market) -> float \| Refusal` (fails closed) | 200 |
| `waiver/cutline.py` | `cutline(roster, position) -> CutlineRow` | 300 |
| `waiver/priority.py` | `price(claim, priority_pos, weeks_remaining) -> Decision` (claim/wait/skip) | 350 |
| `waiver/clear_time.py` | `next_clear(drop_ts, clock) -> ClearEvent` | 350 |
| `waiver/ledger.py` | `moves_remaining(team_id, as_of) -> int`; `reconcile(observed_totals) -> LedgerDiff` | 300 |
| `waiver/cards.py` | contingency cards (prep only) | 200 |
| `trade_angles.py` | `angles(week, limit=3) -> list[Angle]` | 400 |
| `reports/claims.py` | `render_claims(week) -> str` | 350 |
| `reports/trends.py` | `render_trends(week) -> str` | 400 |
| `health.py::render_or_refuse` | `render_or_refuse(inputs: Mapping[str, SourceState], render: Callable[[], str]) -> str` | (within 150) |
| `scripts/notify.py` | `push(event_class, title, body) -> PushResult` | 200 |

## 5. Dependency direction (enforce via check_import_boundaries)

- `league_state/*` → `db`, `ids`, `health`, `flags`, `yahoo_client`, `ingest/gates` (NOT `waiver`, `usage`, `trade_angles`, `reports`).
- `waiver/*` → `db`, `ids`, `health`, `flags`, `usage`, `league_state`, `valuation`, `scoring`, `history`.
- `trade_angles.py` → `db`, `ids`, `health`, `flags`, `usage`, `league_state`, `waiver`, `valuation`.
- `reports/*` → `health`, `flags`, `usage`, `waiver`, `trade_angles`, `league_state`, `valuation`, `db`, `ids` (NOT `sim`, NOT `draft`).
- `scripts/notify.py` → `db`, `health` only (must stay importable from any job).

## 6. Multi-league design (read this before writing any module)

Every module takes/derives league shape from `ffi.league_profile.get_profile()`. Key diffs
to carry through (already in `LeagueProfile`):

| Concern | NAJEE | LMU |
|---|---|---|
| teams | 12 | 14 |
| QB starters | 2 | 1 |
| roster size | 20 (8 BN, 1 IR) | 18 (6 BN, 2 IR) |
| weekly acquisitions | 5 (65 season) | **6 (unlimited season)** |
| trade deadline | Nov 22 | Nov 28 |
| league id | 326814 | 878667 |

Concretely:
- `waiver/cutline.py` and `waiver/ledger.py` must use `profile.teams`, `profile.starters`,
  `profile.bench`, `profile.ir`, and the weekly-acquisition cap (5 vs 6) — NOT hardcoded.
- `waiver/priority.py` (rolling-waiver pricing) is league-shape-sensitive: 14-team 1-QB has
  different waiver scarcity and QB replacement than 12-team 2-QB.
- `league_state` `source`/`ts_precision` must record WHICH league a capture belongs to.

## 7. Data contracts

- `config/league_clock.yaml` — read at runtime ONLY by `league_state/clock.py`
  (`scripts/validate_league_clock.py` reads it for validation only). UNSET /
  `<field>_verified: false` markers are enforced by the validator — fail the build if a
  module consumes an UNSET field.
- `config/modules.yaml` — read ONLY by `ffi/flags.py` (already built).
- `data/captures/` — gitignored; read ONLY by `league_state/manual.py`.
- `reports/` — output only (`claims-*.md`, `trends-*.md`, `briefing-*.md`).
- `.env.example` — add `NTFY_TOPIC_URL` placeholder (field name only, no value).

## 8. Validation gates (per module and final)

1. After each module: `uv run pytest -q` must stay 667+ green.
2. After each module: run the three CI guards (file size, import boundaries, no secrets).
3. `check_import_boundaries.py` is the machine check for §5 — a forbidden import fails the build.
4. `check_file_size.sh` enforces the per-file line ceilings in §4 — do not exceed them.
5. Fail-loud house rules: no bare `except:`, no silent fallbacks; Postgres only via `ffi.db`,
   Yahoo HTTP only via `ffi.yahoo_client`, pushes only via `scripts/notify.py`.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Building `render_or_refuse` before a composite exists (YAGNI) | Build it in step 13, with `reports/claims.py` |
| Consuming an UNSET `league_clock.yaml` field | Validator fails the build (P3 gate) |
| 14-team / 1-QB assumptions leak in as NAJEE hardcodes | Every module reads `LeagueProfile`; grep for `teams=12` / `"QB": 2` in new code |
| Yahoo API dead | `manual.py` is the primary path; `api.py` is backfill-only, never critical-path |
| `trade_angles.py` misses its window | Land the QB-repair angle in step 2; full module by Nov 22 or cut it |

## 10. Done markers to check off

- [x] `flags.py` + `modules.yaml` (this session — DONE, 6 tests)
- [x] `league_state/clock.py`
- [x] `league_state/adapter.py`
- [x] `league_state/manual.py`
- [x] `league_state/reconcile.py`
- [x] `league_state/api.py`
- [x] `usage/market.py`
- [x] `waiver/cutline.py`
- [x] `waiver/priority.py`
- [x] `waiver/clear_time.py`
- [x] `waiver/ledger.py`
- [x] `waiver/cards.py`
- [x] `trade_angles.py`
- [x] `health.render_or_refuse`
- [x] `reports/claims.py`
- [x] `reports/trends.py`
- [x] `scripts/notify.py`
- [x] `scripts/probe_yahoo_access.py`
- [x] `scripts/run_claims_brief.py`
- [x] `scripts/run_trends_report.py`

## P3 completion note (2026-09-14)

`waiver_processing_hour` was fitted from the NAJEE transaction log
(league_key 461.l.326814, 2024 + 2025 seasons): the waiver batch runs at
**01:00 PT the morning after the transcribed claim day** — 84 adds at Wed
01:00 in 2025, 45 in 2024, batch completing by ~02:00 (the 06:00–07:00
Wednesday clusters are post-waiver FA grabs, not the batch). Wired into
`league_state/clock.py`: `deadline("waivers")` is now Wednesday 01:00 PT.
Still UNSET: `clear_award_mechanism`, `weekend_drop_clear_behavior`,
`move_count_reset_boundary` (plus the unverified trade/playoff fields).
