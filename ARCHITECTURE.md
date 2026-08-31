# Architecture Contract — fantasy_football

**Tier:** 2 (inherited from the risk register via the ADR — not re-classified here)
**Date:** 2026-08-31
**ADR:** [docs/superpowers/risks/2026-08-30-in-season-ops-adr.md](docs/superpowers/risks/2026-08-30-in-season-ops-adr.md)
**Design doc:** [docs/superpowers/specs/2026-08-30-in-season-ops-design.md](docs/superpowers/specs/2026-08-30-in-season-ops-design.md)
**Risk register:** [docs/superpowers/risks/2026-08-30-in-season-ops-risks.md](docs/superpowers/risks/2026-08-30-in-season-ops-risks.md)

Read this before writing code. The ADR is the *intent* layer; this file is the *contract*; the three
scripts in `scripts/check_*` are the *enforcement* layer. Where this file and the ADR disagree, the
ADR wins and this file is wrong — fix it here rather than working around it.

Scope: the whole repo. Existing (draft-era) modules and the new in-season modules are both covered,
because the in-season system is built by extension, not replacement (ADR approach A).

---

## 1. Module list

Column 3 is the hard line ceiling for **each file** in that path. Directory rows (trailing `/`) apply
to every `.py` file beneath them; file rows override directory rows. This table is the source of truth
for `scripts/check_file_size.sh` — keep the third column purely numeric.

### 1a. Existing modules (draft-era, in maintenance)

| Module | Responsibility | Max lines |
|---|---|---|
| `src/ffi/db.py` | The ONLY module that opens Postgres connections | 200 |
| `src/ffi/ids.py` | Player/team/league key parsing, normalization, crosswalk SQL helpers | 300 |
| `src/ffi/yahoo_client.py` | The ONLY module that makes Yahoo HTTP calls; OAuth session + proactive refresh (ADR D4) | 300 |
| `src/ffi/ingest/` | Vendor feed adapters (nflverse, Sleeper, FantasyPros) + crosswalk write path | 400 |
| `src/ffi/scoring/` | League scoring rules, statline scoring, per-source scoring adapters | 400 |
| `src/ffi/valuation/` | Starts-weighted valuation v2: baselines, starts curve, tiers | 300 |
| `src/ffi/history/` | 16-season league-history mining and matchup parsing (base rates for ADR D7) | 500 |
| `src/ffi/sim/` | Season/draft simulation, opponent models, calibration, backtests | 600 |
| `src/ffi/sim/backtest.py` | Backtest harness — grandfathered, do not grow; split before adding features | 800 |
| `src/ffi/draft/` | Live-draft console engine: session, state, poller, recommender, modes | 600 |
| `src/ffi/breakout.py` | Curated breakout-notes layer joined onto the board | 300 |
| `src/ffi/signals_apply.py` | FP news → signals queue application | 250 |
| `scripts/` | One-shot jobs, launchd entry points, reports, research (default budget) | 600 |
| `scripts/draft_console.py` | Live draft TUI — grandfathered, do not grow | 800 |
| `scripts/tournament_v2.py` | Strategy tournament runner — grandfathered, do not grow | 800 |
| `scripts/run_sim_farm.py` | Sim-farm driver — grandfathered, do not grow | 700 |
| `scripts/morning_briefing.py` | THE dashboard (ADR D5); health, artifact freshness, capture recency, archive continuity | 400 |
| `tests/` | pytest suite + golden fixtures | 600 |

### 1b. New in-season modules (ADR 2026-08-30)

| Module | Responsibility | Max lines |
|---|---|---|
| `src/ffi/health.py` | `SourceState` three-state model (OK / KNOWN-LAGGING / BROKEN) + `render_or_refuse`; reads `config/source_clock.yaml`. Leaf: imports nothing internal | 150 |
| `src/ffi/flags.py` | Single reader of `config/modules.yaml` (per-module enable flags, ADR D8 rollback unit). Leaf | 100 |
| `src/ffi/ingest/gates.py` | Semantic sanity gates at every ingest boundary: field-set diff, distribution assertions, wk/wk rank correlation ≥0.85. Pure functions; imports no feed it checks | 250 |
| `src/ffi/usage/` | `usage_weekly` build + trend rules + cold-start variants (package) | 400 |
| `src/ffi/usage/build.py` | Derive `public.usage_weekly` from nflverse; `games_complete` / `teams_observed` partial-publish guard (R5) | 300 |
| `src/ffi/usage/trends.py` | Role-change rules → ASCENDING / FALLING / WATCH with evidence lines and rule IDs | 400 |
| `src/ffi/usage/coldstart.py` | Weeks 1–3 rule variants (1–2 wk windows, camp/preseason priors), COLD-START labeling (R7) | 200 |
| `src/ffi/usage/market.py` | `market_trends` normalization over `raw.sleeper_trending` + urgency score (fails closed) | 200 |
| `src/ffi/waiver/` | Waiver advisory engine (package) | 400 |
| `src/ffi/waiver/cutline.py` | Position-aware roster cutline; FA value vs worst droppable | 300 |
| `src/ffi/waiver/priority.py` | Rolling-priority pricing as a finite-horizon expiring option with a stopping rule (R18); standing QB reservation | 350 |
| `src/ffi/waiver/clear_time.py` | Drop→clear state machine over the weekly calendar, tz-aware, DST-safe (R4, R29) | 350 |
| `src/ffi/waiver/ledger.py` | Event-sourced move-budget ledger for all 12 teams + reconciliation + safety margin (R21) | 300 |
| `src/ffi/waiver/cards.py` | Contingency cards ("if X inactive → add Y"); prep only, window 3 automation de-scoped | 200 |
| `src/ffi/league_state/` | League-state adapter (package): one output schema, two backends | 400 |
| `src/ffi/league_state/adapter.py` | Canonical read/write API for `league_transactions` / `league_rosters` with `source` + `ts_precision` | 300 |
| `src/ffi/league_state/manual.py` | PRIMARY backend: manual capture / screenshot-text parsing. The ONLY reader of `data/captures/` | 350 |
| `src/ffi/league_state/api.py` | Yahoo backfill backend (optimization path, never on a critical path); calls `ffi.yahoo_client` only | 250 |
| `src/ffi/league_state/reconcile.py` | Explicit API-vs-manual reconciliation pass; logs every diff, never silently overwrites | 200 |
| `src/ffi/league_state/clock.py` | Single reader of `config/league_clock.yaml`; deadline/window derivation for every job time | 200 |
| `src/ffi/trade_angles.py` | Window 6: opponent needs, buy-low / sell-high, QB-repair angle. Recommend-only | 400 |
| `src/ffi/reports/` | Renderers for the two decision artifacts (package). Composites go through `render_or_refuse` | 400 |
| `src/ffi/reports/health_section.py` | Briefing health-section renderers: artifact freshness, archive continuity, expected-season assertions. Extracted from `scripts/morning_briefing.py` at its 400-line ceiling | 200 |
| `src/ffi/reports/claims.py` | Monday-evening claims brief → `reports/claims-YYYY-WW.md` | 350 |
| `src/ffi/reports/trends.py` | Tuesday trends & targets report → `reports/trends-YYYY-WW.md` | 400 |
| `scripts/notify.py` | The ONLY module that sends a push. ntfy topic from `NTFY_TOPIC_URL`; 3/week cap enforced in code against `push_log`; suppressed pushes recorded | 200 |
| `scripts/probe_yahoo_access.py` | Daily authenticated Yahoo probe → `raw.ingest_runs` under source `yahoo_probe`; no retry on 403; Sept 10 decision date | 100 |
| `scripts/run_claims_brief.py` | launchd entry point for `com.ffi.claims` (Monday evening) | 150 |
| `scripts/run_trends_report.py` | launchd entry point for `com.ffi.trends` (data-readiness trigger + hard fallback fire time) | 150 |
| `scripts/ingest_sleeper_trending.py` | P2: daily `raw.sleeper_trending` archive (started before anything else). Renamed from `archive_sleeper_trending.py` during writing-plans to match the repo's `scripts/ingest_*.py` convention | 100 |

### 1c. Data contracts (config and artifacts — not code, no line budget)

| Path | Contract |
|---|---|
| `config/source_clock.yaml` | Per source: expected interval, expected lag window, deadline, owner. Committed, `as_of`-stamped. Read ONLY by `src/ffi/health.py` |
| `config/league_clock.yaml` | Observed waiver processing hour, drop→clear state machine, roster locks, trade/playoff deadlines, tz-aware, `as_of`-stamped. Read at runtime ONLY by `src/ffi/league_state/clock.py`; `scripts/validate_league_clock.py` reads it for validation only and derives nothing. Initial contents come from the P3 observed-mechanics tests; UNSET / `<field>_verified: false` markers are enforced by that validator |
| `config/modules.yaml` | Per-module enable flags (`usage_trends`, `waiver_advisor`, `trade_angles`, `push`). Read ONLY by `src/ffi/flags.py`. Disabled sections render `OFF (disabled <date>)`, never vanish |
| `league_rules.md` | Gets an `as_of` header; its transaction/waiver/playoff fields become machine-readable config that modules read directly (R16). No module re-transcribes prose |
| `reports/` | Output artifacts only (`claims-*.md`, `trends-*.md`, `briefing-*.md`). No Python lives here; renderers live in `src/ffi/reports/` |
| `data/captures/` | Gitignored manual captures. Read ONLY by `src/ffi/league_state/manual.py` |
| `.env` / `.env.example` | Secrets by field name only in `.env.example`; `NTFY_TOPIC_URL` is a capability URL and is never logged or rendered into an artifact |

---

## 2. Dependency direction

Directed edges = "may import". Anything not listed is forbidden by default. Verified acyclic by
topological sort (order below is a valid topological order; every edge points strictly leftward).

**Layer 0 — leaves (import nothing internal):**

- `src/ffi/db.py` → (nothing internal; `psycopg2` + `dotenv`)
- `src/ffi/ids.py` → (nothing internal)
- `src/ffi/yahoo_client.py` → (nothing internal; `yahoo_oauth` + `dotenv`)
- `src/ffi/health.py` → (nothing internal; `config/source_clock.yaml`)
- `src/ffi/flags.py` → (nothing internal; `config/modules.yaml`)
- `src/ffi/ingest/gates.py` → (nothing internal)

**Layer 1 — data and domain:**

- `src/ffi/ingest/` → `db`, `ids`, `ingest/gates`
- `src/ffi/scoring/` → `db`, `ids`
- `src/ffi/history/` → `db`, `ids`
- `src/ffi/breakout.py` → `db`, `ids`
- `src/ffi/signals_apply.py` → `db`, `ids`

**Layer 2 — valuation and simulation:**

- `src/ffi/valuation/` → `db`, `ids`, `scoring`
- `src/ffi/sim/` → `db`, `ids`, `scoring`, `valuation`, `history`
- `src/ffi/draft/` → `db`, `ids`, `scoring`, `valuation`, `sim`

**Layer 3 — in-season stages (strictly sequential, no back-coupling):**

- `src/ffi/usage/` → `db`, `ids`, `health`, `flags`, `ingest/gates`
- `src/ffi/league_state/` → `db`, `ids`, `health`, `flags`, `yahoo_client`, `ingest/gates`
- `src/ffi/waiver/` → `db`, `ids`, `health`, `flags`, `usage`, `league_state`, `valuation`, `scoring`, `history`
- `src/ffi/trade_angles.py` → `db`, `ids`, `health`, `flags`, `usage`, `league_state`, `waiver`, `valuation`

**Layer 4 — rendering:**

- `src/ffi/reports/` → `health`, `flags`, `usage`, `waiver`, `trade_angles`, `league_state`, `valuation`, `db`, `ids`

**Layer 5 — jobs (top of the graph; nothing imports `scripts/`):**

- `scripts/morning_briefing.py` → `health`, `flags`, `db`, `ids`, `league_state`, `reports/health_section`, `ingest/fantasypros`, `signals_apply` (the last two predate this branch; recorded here so the edge list is not fiction)
- `scripts/run_claims_brief.py`, `scripts/run_trends_report.py` → `reports/` and anything below it
- `scripts/notify.py` → `db`, `health` (and nothing else internal — it must stay importable from any job)
- `scripts/probe_yahoo_access.py` → `yahoo_client`, `db`
- `scripts/ingest_sleeper_trending.py` → `ingest/`, `db`
- other `scripts/*` → any `src/ffi` module

**Topological order (acyclic proof — 20 nodes, 80 directed edges, verified by topological sort):**
`db` → `ids` → `yahoo_client` → `health` → `flags` → `ingest/gates` → `ingest` → `scoring` →
`valuation` → `history` → `sim` → `draft` → `breakout` → `signals_apply` → `usage` →
`league_state` → `waiver` → `trade_angles` → `reports` → `scripts/*`.
Every edge above points from a later node to an earlier one. No cycle exists.

---

## 3. Forbidden imports

Machine-parsed by `scripts/check_import_boundaries.py`. Format is load-bearing — one bullet per rule,
exactly:  ``- `from_glob` → `to_glob` — rationale``  . `*` is a wildcard.

- `src/ffi/*` → `scripts/*` — the library must never depend on job runners; scripts are the top of the graph and are not importable units.
- `src/ffi/health.py` → `src/ffi/*` — health reports on every module's state; importing them makes the gate self-referential and creates a cycle (ADR D1/D5).
- `src/ffi/flags.py` → `src/ffi/*` — the rollback switch must be readable by a module that is itself never disabled.
- `src/ffi/ingest/gates.py` → `src/ffi/ingest/*` — a sanity gate must not import the feed adapters it validates (ADR D1).
- `src/ffi/ingest/gates.py` → `src/ffi/usage/*` — same rule in the downstream direction; the gate is a pure function of data.
- `src/ffi/ingest/gates.py` → `src/ffi/league_state/*` — same rule.
- `src/ffi/usage/*` → `src/ffi/waiver/*` — usage and waiver are sequential stages; usage feeds waiver and never reads back (ADR D2 data flow).
- `src/ffi/usage/*` → `src/ffi/trade_angles.py` — same sequential-stage rule.
- `src/ffi/usage/*` → `src/ffi/league_state/*` — the trend engine must remain fully functional with zero league-state data (ADR D6 manual-first acceptance criterion).
- `src/ffi/usage/*` → `src/ffi/reports/*` — engines never import renderers.
- `src/ffi/league_state/*` → `src/ffi/waiver/*` — the adapter layer must not import its consumers.
- `src/ffi/league_state/*` → `src/ffi/usage/*` — unrelated stages; no coupling.
- `src/ffi/league_state/*` → `src/ffi/trade_angles.py` — adapter must not import its consumers.
- `src/ffi/league_state/*` → `src/ffi/reports/*` — engines never import renderers.
- `src/ffi/waiver/*` → `src/ffi/trade_angles.py` — trade_angles reads the budget ledger; the coupling is one-way by design.
- `src/ffi/waiver/*` → `src/ffi/reports/*` — engines never import renderers.
- `src/ffi/trade_angles.py` → `src/ffi/reports/*` — engines never import renderers.
- `src/ffi/reports/*` → `src/ffi/sim/*` — reports render precomputed state; a report must never kick off a simulation inside a deadline-bounded job.
- `src/ffi/reports/*` → `src/ffi/draft/*` — draft-day code is out of the in-season path.
- `src/ffi/usage/*` → `src/ffi/draft/*` — same.
- `src/ffi/waiver/*` → `src/ffi/draft/*` — same.

### 3a. Non-import rules (same contract, different enforcement)

These are hard-coded in `scripts/check_import_boundaries.py` rather than parsed from the list above,
because they are call-site or text rules, not import-graph rules.

- **Yahoo transport:** only `src/ffi/yahoo_client.py` may import `yahoo_oauth`, `requests`,
  `requests_oauthlib`, `httpx`, `aiohttp`, or `urllib.request` for Yahoo. Allowlist for HTTP
  libraries generally:
  `src/ffi/yahoo_client.py`, `src/ffi/ingest/*`, `scripts/notify.py`, `scripts/probe_yahoo_access.py`.
  Rationale: ADR D4 consolidates five auth scripts to one supported entry point; ADR D6 caps vendor
  transport at one seam per vendor.
- **Postgres connections:** only `src/ffi/db.py` may call `psycopg2.connect` / `psycopg.connect`.
  Everything else uses `ffi.db.connect()`. Rationale: ADR D2 single source of truth; ADR D8 advisory
  locking and repeatable-read snapshot pinning are only enforceable at one connection seam.
- **Push:** only `scripts/notify.py` may send a push. The 3-per-week cap is enforced in code against
  the `push_log` table, not by convention; every suppressed push is recorded (ADR D8).
- **Captures:** no module except `src/ffi/league_state/manual.py` may read `data/captures/` or parse
  screenshot text. Rationale: ADR D1/D6 — precision metadata (`ts_precision`) is attached at exactly
  one place, so "success at the wrong precision" (R17) is detectable.
- **Bare `except:`** is forbidden anywhere. Fail-loud house rule (user CLAUDE.md, ADR D1). Every
  `try`/`except`/fallback goes through the `fail-loud-error-handling` skill before it is written.
- **Composite rendering:** any composite of a gated input (urgency score, ASC/FALL flags, priority
  pricing, clear-time alerts) must render through `ffi.health.render_or_refuse`. Direct rendering of a
  composite whose input is BROKEN is forbidden. *Review-enforced, not machine-checked (see TBDs).*

### 3b. Known debt (grandfathered violations)

Pre-existing violations, allowlisted by exact path in `scripts/check_import_boundaries.py`. New files
get no such grace. Do not extend these lists.

- **Bare `except:`** — `scripts/import_league_326814.py`, `scripts/yahoo_auth_simple.py` (×2),
  `scripts/yahoo_manual_auth.py`. Three of the four are in auth scripts that ADR D4 archives.
- **Direct `psycopg2.connect`** — `scripts/import_all_lmu.py`, `scripts/import_league_326814.py`,
  `scripts/import_yahoo_data.py`, `scripts/ingest_expert_rankings.py`, `scripts/rss_ingester.py`,
  `scripts/scoring_adjuster.py`, `scripts/update_player_names.py`. Legacy importers that predate
  `ffi.db`; none is on the in-season critical path.
- **HTTP libraries outside the allowlist** — `scripts/import_all_lmu.py`,
  `scripts/import_league_326814.py`, `scripts/import_yahoo_data.py`,
  `scripts/ingest_expert_rankings.py`, `scripts/list_my_leagues.py`, `scripts/setup_yahoo_auth.py`,
  `scripts/source_backtest_archives.py`, `scripts/yahoo_auth.py`, `scripts/yahoo_auth_simple.py`,
  `scripts/yahoo_manual_auth.py`. ADR D4 moves `yahoo_auth.py`, `yahoo_auth_simple.py`,
  `yahoo_manual_auth.py`, `setup_yahoo_auth.py`, `oauth_https_server.py` to `scripts/archive/`; the
  allowlist shrinks by five entries in that commit.
- **Oversized files** — `src/ffi/sim/backtest.py` (787), `scripts/draft_console.py` (770),
  `scripts/tournament_v2.py` (704), `scripts/run_sim_farm.py` (619). Budgeted at the next 100 above
  current size and annotated "do not grow" in the module list.

---

## 4. Interface contracts

Canonical entry points for cross-cutting concerns. Any module needing this functionality goes through
the named interface; direct access is a forbidden import (§3). Signatures for modules that do not yet
exist are provisional in *name and shape only* — the semantics are fixed by the ADR and may not drift.

| Concern | Canonical entry point | Owner |
|---|---|---|
| Postgres access | `connect(dbname: str \| None = None) -> psycopg2.connection` | `src/ffi/db.py` |
| Yahoo transport | `get_session()`, `ensure_fresh_token(sc, margin_s=900) -> bool`, `yahoo_call(fn, *args, **kwargs)` | `src/ffi/yahoo_client.py` |
| Player identity | `player_key(game_code, player_id) -> str`, `normalize_team_abbr(abbr) -> str` | `src/ffi/ids.py` |
| Source health state | `state(source: str, status: str, age_h: float, clock: SourceClock \| None = None) -> SourceState` (OK / KNOWN_LAGGING / BROKEN); `load_clock(path) -> SourceClock`; `is_alarming(state) -> bool` | `src/ffi/health.py` |
| Fail-closed rendering | `render_or_refuse(inputs: Mapping[str, SourceState], render: Callable[[], str]) -> str` — emits `NO SIGNAL — <source> <state> since <ts>` instead of computing on a BROKEN input | `src/ffi/health.py` |
| Module enable flags | `enabled(module: str) -> bool`, `disabled_since(module: str) -> date \| None` | `src/ffi/flags.py` |
| Ingest sanity gate | `check(feed: str, snapshot, prior) -> GateResult`; failure raises `GateFailure` and the run is recorded `status='sanity_failed'` | `src/ffi/ingest/gates.py` |
| League clock | `deadline(event: str, week: int) -> datetime` (tz-aware), `window(event, week) -> tuple[datetime, datetime]`, `fallback_fire_time(job, week) -> datetime` | `src/ffi/league_state/clock.py` |
| League state read/write | `load_transactions(week) -> list[Transaction]`, `load_rosters(as_of) -> list[RosterRow]`, `record(rows, source, ts_precision) -> None` | `src/ffi/league_state/adapter.py` |
| Capture ingestion | `parse_capture(path) -> list[Transaction]` — the only screenshot/text parser | `src/ffi/league_state/manual.py` |
| Usage table build | `build_usage_weekly(week) -> UsageFrame` (carries `games_complete`, `teams_observed`) | `src/ffi/usage/build.py` |
| Trend classification | `classify(usage: UsageFrame, week: int) -> TrendResult` (ASCENDING / FALLING / WATCH + evidence lines + rule IDs) | `src/ffi/usage/trends.py` |
| Market overlay / urgency | `urgency(signal, market) -> float \| Refusal` — fails closed when either input's gate failed | `src/ffi/usage/market.py` |
| Roster cutline | `cutline(roster, position) -> CutlineRow` | `src/ffi/waiver/cutline.py` |
| Priority pricing | `price(claim, priority_pos: int, weeks_remaining: int) -> Decision` (claim / wait / skip) — finite-horizon expiring-option stopping rule | `src/ffi/waiver/priority.py` |
| Clear-time | `next_clear(drop_ts: datetime, clock) -> ClearEvent` — tz-aware state machine; refuses on unset award mechanism | `src/ffi/waiver/clear_time.py` |
| Move-budget ledger | `moves_remaining(team_id, as_of) -> int` (conservative margin applied), `reconcile(observed_totals) -> LedgerDiff` | `src/ffi/waiver/ledger.py` |
| Trade angles | `angles(week, limit: int = 3) -> list[Angle]` — recommend-only, never sends | `src/ffi/trade_angles.py` |
| Report rendering | `render_claims(week) -> str`, `render_trends(week) -> str` — both wrap composites in `render_or_refuse` | `src/ffi/reports/` |
| Push delivery | `push(event_class: Literal['claim_deadline','inactive_contingency','qb_watchlist'], title: str, body: str) -> PushResult` — 3/week cap checked against `push_log`; over-cap downgrades to the next report and logs | `scripts/notify.py` |

---

## 5. File-size budget

- Default max: **600 lines** for `.py`. Per-module overrides live in column 3 of §1.
- Four files are **grandfathered** above the default (see §3b). Their budget is the next 100 above
  current size, annotated "do not grow". A grandfathered file that needs a feature gets split first.
- When a file exceeds its budget: **split before adding features.** Do not add to the blob, do not
  raise the budget in this file to make CI pass. Raising a budget requires a rationale in the module
  list and is capped at 1000.
- New modules get the budget in §1b, not the 600 default. `src/ffi/health.py` at 150 is deliberate:
  it is a leaf that every other module imports, and it stays readable in one screen.
- Enforced by: `scripts/check_file_size.sh` (reads §1 for overrides). Run in CI / pre-commit.
- Scan scope is `src/` and `scripts/`, excluding `__pycache__/`, `.venv/`, `archive/`. The `tests/`
  budget in §1a is documented but not machine-enforced; widen the scan if test files start bloating.
- Only §1 is parsed for budgets. Tables elsewhere in this file also contain backticked paths — do not
  move budget numbers out of §1 or add a numeric third column to another table's path rows.

---

## 6. Tech stack constraints (ADR Domain 6)

- Python dependencies are pinned via `uv` + `uv.lock`. **Frozen from Week 1 through Nov 22** except
  for security fixes. No dependency upgrades inside the season window.
- Five external dependencies with fixed stances: **nflverse** (primary, archival, column-presence
  asserted at ingest — rules degrade by removal, never by silent null), **Sleeper** (free,
  unauthenticated, undocumented; gated per feed; archived daily since there is no history endpoint),
  **Yahoo** (demoted to an *optimization* — every module's acceptance criterion must be met with zero
  Yahoo access), **FantasyPros** (30/day cap: weekday 20, Sunday reserve 10, enforced in
  `fp_calls_today` accounting), **manual capture** (a first-class dependency whose vendor is the
  operator; monitored via capture recency, precision-typed via `ts_precision`).
- No new runtime services. Postgres stays localhost-only; no network listeners; no cloud runner
  (held as an escalation only if a Tuesday actually slips).
- No web scraping of Yahoo. Out of scope by design-doc decision and it stays out of scope.

## 7. Conventions (ADR Domains 1, 5, 7)

- **Fail loud.** No bare `except:`, no silent fallback, no default-on-missing. Derived composites fail
  closed: refuse to render rather than compute on a failed input. Every `try`/`except`/fallback/retry
  goes through the `fail-loud-error-handling` skill first (user CLAUDE.md).
- **Retries** only for transient HTTP (3 attempts, exponential backoff base 2s, jitter). **Never retry
  a Yahoo 403** — it is an authorization state, not a transient error.
- **Job chains** are `;`-separated with a trap, never `&&`: the briefing must render even when an
  upstream step fails (R23).
- **Logging** is structlog JSON lines under `logs/`, 90-day retention. Trend-engine output logs
  evidence lines and rule IDs so a false ASCENDING is traceable to the rule that fired it.
- **Observability is briefing-first.** Every new health signal lands in `scripts/morning_briefing.py`,
  the one artifact the operator opens daily. Exit non-zero on red so launchd surfaces it.
- **Testing:** `pytest`, existing `tests/fixtures` golden-fixture pattern. Ship gates, not coverage:
  a precision gate (≤8 ASCENDING/wk at ≥40% precision on the 2025 replay), a lead-time gate (median
  usage→production lead ≥1 week), an ROS-mode gate (>5pp strict-win degradation at a week-8 truncated
  horizon is a red flag), a base-rate gate (priority hurdle computed from the 16-season history, never
  asserted), and the QB-repair acceptance criterion (week-1 dry run surfaces ≥1 actionable QB item).
  Thresholds are tuned on 2024 and gated on 2025 — never tuned on the gate season.
- **Timezones:** tz-aware datetimes throughout; a DST-crossing fixture (Nov 1) is mandatory for any
  clear-time math.
- **No-deploy window:** changes land Wednesday–Saturday. Sunday–Tuesday is frozen.

---

## 8. Enforcement

| Script | Checks | Status on 2026-08-31 |
|---|---|---|
| `scripts/check_file_size.sh` | §1 budgets + §5 default 600 over `src/` and `scripts/` | PASS — 123 files |
| `scripts/check_import_boundaries.py` | §3 parsed rules (21) + 4 hard-coded rules (R-HTTP, R-PGCONN, R-EXCEPT, R-CAPTURE) | PASS — 123 files, 20 grandfathered paths |
| `scripts/check_no_secrets.sh` | `.env.example` field-names-only, no committed ntfy topic URL, `.gitignore` covers `.env` / `config/yahoo_token.json` / `data/captures/` | PASS |

Run all three before every commit and in CI:

```bash
bash scripts/check_file_size.sh && python3 scripts/check_import_boundaries.py && bash scripts/check_no_secrets.sh
```

`python3 scripts/check_import_boundaries.py --list-debt` prints the grandfather allowlist. The list is
allowed to shrink and never to grow; new files get no grace.

The §3 bullet format is machine-parsed. When adding a forbidden import, keep the exact shape
``- `from_glob` → `to_glob` — rationale`` or the rule is silently ignored — the checker fails loudly
if it parses zero rules, but not if it parses one fewer than you wrote.

---

## TBDs

1. `src/ffi/flags.py` is not named in the ADR. It is introduced here as the single reader of
   `config/modules.yaml`, which ADR D8 *does* mandate. Confirm the module name during writing-plans,
   or fold it into `health.py` if a second leaf module is judged unnecessary.
2. The `render_or_refuse` obligation (§3a) is review-enforced. A machine check would require knowing
   which values are composites; that mapping does not exist until `usage/` and `waiver/` are written.
   Revisit once the composite list is concrete.
3. Per-feed numeric sanity thresholds beyond the ≥0.85 rank correlation are unset (ADR D1 TBD). Set
   empirically from the first four weeks of the P2 archive; until then gates run observe-and-log for
   trending and hard-fail for projections.
4. The operational definition of a "sustained role change" for precision labeling is unset (ADR D7
   TBD). Define once against the 2025 replay and freeze before any threshold tuning.
5. `src/ffi/reports/` as the renderer package location is an inference: `reports/` at the repo root is
   an artifact directory and must stay code-free. Confirm during writing-plans.
6. §4's health signature was provisional ("provisional in name and shape only").
   Resolved during writing-plans to `state(source, status, age_h, clock)`:
   `raw.ingest_runs` is queried with `extract(epoch FROM now() - started_at)/3600`
   and never returns a `last_success_at` timestamp, so a `(datetime, datetime)`
   signature would force every caller to reconstruct one. Semantics
   (three states, age-aware, config-driven) are unchanged.
