# Architecture Decision Record — draft-intelligence

**Date:** 2026-07-08
**Project:** fantasy_football
**Design doc:** [2026-07-08-draft-intelligence-design.md](../specs/2026-07-08-draft-intelligence-design.md)
**Risk register:** [2026-07-08-draft-intelligence-risks.md](2026-07-08-draft-intelligence-risks.md)
**Tier:** 2 (inherited from risk register)
**Tier Justification:** Real authenticated data (Yahoo league, FantasyPros subscription) and paid API budget, but single operator and no autonomous consequential actions.
**Project Goal:** A draft-intelligence system that re-scores stat-level projections under the league's exotic rules, prices 2QB scarcity, and delivers a rehearsed real-time draft assistant plus a daily briefing, ~6 weeks before the 2026 draft.
**Key Constraints:** Solo developer working daily sessions; ~6-week deadline (immovable — draft day); ≤$100/mo; local Mac + Postgres; several load-bearing external APIs are undocumented or rate-capped.
**Domains covered:** 1, 2, 3, 4, 5, 6, 7, 8
**Domains skipped:** 9, 10, 11 (above tier — see stubs)

---

## Domain 1: Error Handling & Failure Design

### Decision
Fail-loud everywhere, with explicitly named degraded modes instead of silent fallbacks. Ingestion failures, schema drift, and stale snapshots refuse to propagate into derived outputs (board, briefing, sims) without a visible alert and a data-vintage stamp; there is no "best effort with old data" path that doesn't announce itself. Draft-day operation is a state machine of declared modes — LIVE → POLL-DEGRADED → MANUAL → PAPER — with automatic downshift on failure and the operator always told which mode they're in. In-flight draft state is persisted to disk on every pick and resumable after a crash.

### Rationale
Addresses risks R2, R5, R14, R15 from [the risk register](2026-07-08-draft-intelligence-risks.md): the two failure classes that can actually hurt are silent data corruption (a board built from stale/drifted inputs looks identical to a good one) and draft-day meltdown under a 90-second clock. The rejected alternative — graceful silent fallback to cached data — is exactly the failure mode the user's global fail-loud preference forbids: in this system a confidently wrong number is worse than a loud absence. Per-user CLAUDE.md, all try/except and fallback code is written under the fail-loud-error-handling skill.

### Implementation
- Retry with exponential backoff on transient HTTP errors: 3 attempts, base 2s, jitter; Yahoo error 999 is special-cased as "rate-limit lockout" → immediate downshift to MANUAL mode during drafts, long cool-off (15 min) otherwise. No automatic retry storms against Yahoo.
- Every ingestion job writes a run record (source, started/finished, row counts, schema hash, status) to Postgres; a failed or missing run marks that source **stale**, which (a) stamps all downstream outputs with data vintage and (b) tops the next briefing with a red alert.
- Schema validation at ingestion boundaries (pydantic models per source); unknown/missing fields = hard failure, not coercion.
- Draft assistant: watchdog on poll loop with asymmetric thresholds tuned for a 90-second clock. **One poll failure → POLL-DEGRADED immediately** (audible/visual banner, retries continue, board serves from last known state); **two consecutive failures → escalate POLL-DEGRADED → MANUAL**; **one 999 → immediate MANUAL, no retry** (999 means a 10–15 min lockout — a second attempt only burns the pick window). All mode transitions logged. State file (JSON, append-only event log of picks) written per pick; startup replays it.
- Exceptions never swallowed: top-level handlers log full context and re-raise or halt the pipeline; cron jobs exit nonzero so failures are observable.

### Risk if Skipped
A silent Sleeper schema drift (R5, L4×I8) poisons the board for days before anyone notices — discovered mid-draft as recommendations that contradict obvious reality. A draft-day poll failure without a drilled manual mode (R2, L6×I8) costs multiple panicked picks; at ~15 min of lockout across rounds 3–5, that plausibly forfeits the season's main goal.

---

## Domain 2: Data Flow & State Management

### Decision
Postgres is the single source of truth for everything durable: raw ingested snapshots (immutable, dated), normalized projections, the ID crosswalk, league history, scoring config versions, signals and adjustments, sim results, and draft event logs. Data flows one direction: raw snapshot → normalized → scored → valued → outputs; derived layers are always recomputable from raw + config, never hand-edited. Scoring rules live in versioned config (not code); board outputs carry (scoring-config version, data vintage) provenance. Conflicts between projection sources are not "resolved" at ingestion — sources are stored separately and combined explicitly in the aggregation layer with recorded weights.

### Rationale
Addresses risks R1, R4, R5, R6, R8, R9, R10: recomputability is the antidote to slow-burn corruption — if the crosswalk or a scoring rule is found wrong in week 4, we re-derive the world from immutable raw snapshots rather than untangling mutations. Storing sources separately preserves the ability to audit "which input moved this player." The alternative (mutate a single canonical projections table in place) was rejected because it destroys exactly the provenance the agentic layer's audit requirements depend on.

### Implementation
- Migration decision: new areas are **named Postgres schemas** (`CREATE SCHEMA raw, scoring, valuation, signals, sim, draft`); **existing flat tables remain in `public`, which IS the core layer** — new normalized tables (stat projections keyed by crosswalk ID) and the ID crosswalk itself are created in `public` alongside `players`/`leagues`/`draft_picks`, so all cross-source joins resolve within one schema. Area contents: `raw` (JSONB snapshots + schema hash per source per day), `scoring` (versioned rule configs; league_rules.md imported as config v1), `valuation`, `signals` (agent outputs + applied adjustments with caps/provenance), `sim` (draft logs, results), `draft` (live event log).
- ID crosswalk built on dynastyprocess/nflverse `ff_playerids`, extended with a manual-override table; every cross-source join goes through it; unmatched fantasy-relevant players fail loudly (R6).
- Per-season league-settings audit table populated during the Yahoo renew-chain import (team count, roster slots, scoring), so era segmentation (R4) is a query, not a guess.
- Stale-data policy: outputs display vintage; briefing refuses to apply adjustments derived from signals older than 48h; draft board refuses to build from any source >36h stale without an explicit `--override-stale` flag.
- Backup: nightly `pg_dump` to a local backups directory (+ external copy before draft day); raw snapshots make even a restore-from-scratch recoverable (R14).

### Risk if Skipped
Without one source of truth and recomputable layers, a single crosswalk error (R6, L6×I6) or scoring misencoding (R1, L4×I10) becomes permanent silent corruption — the board is unfixable without knowing which of five sources contributed what, and debugging during draft week is hours-to-days we don't have.

---

## Domain 3: Secrets & Configuration

### Decision
All secrets stay in gitignored local files, loaded via environment at process start: `.env` for API keys (FantasyPros key, Anthropic key, Yahoo client ID/secret), `config/yahoo_token.json` for the OAuth token pair (already the established pattern). Configuration that is not secret (scoring rules, source weights, poll intervals, caps) lives in versioned, committed config files — separated from secrets by classification, not location convention alone. Single environment (local Mac); no dev/staging split.

### Rationale
The current `.gitignore` already covers `.env`, `*.token`, `config/yahoo_token.json`, keys/certs, and verification confirms only `.env.example` is tracked — the existing pattern is sound, so we keep it rather than adding a vault the project doesn't need (YAGNI; threat model is "accidental git commit / laptop theft," not multi-user secret sharing). Addresses risks R12/R13 indirectly (FP key and session cookie are revocable assets worth protecting) and the browser-automation session cookies used for FP mocks, which are secrets and treated as such.

### Implementation
- `.env` + `python-dotenv` (existing pattern); `.env.example` documents required keys without values.
- FP session cookie for export automation stored in `.env`, never logged; FP mock-automation browser profile kept separate from the API-key account (R13 mitigation).
- Yahoo token file permissions 600; token refresh rewrites it atomically (write-temp-rename).
- Rotation: manual, documented in the runbook — Yahoo app secret and FP key each have a "how to rotate" note; anything suspected leaked gets revoked at the provider first.
- Pre-commit guard: `detect-secrets`-style hook (or a simple grep hook) blocking accidental key commits.

### Risk if Skipped
A committed `.env` pushed to a remote exposes the Yahoo client secret + refresh token (full read access to all league history and the live draft) and the Anthropic/FP keys — remediation is an hour of rotation but the FP key is discretionary-approval (R12): a revoked-for-abuse key may simply not be re-granted, killing the consensus overlay permanently.

---

## Domain 4: Authentication & Access Control

### Decision
Single-operator system with no user-facing auth surface of its own; the auth problem is entirely outbound — holding and refreshing credentials to third parties (Yahoo OAuth 2.0 authorization-code flow with refresh tokens; FP `x-api-key` header; FP browser session for exports/mocks; Anthropic API key). Everything runs as the user's local account; Postgres listens on localhost only with default local trust; no network exposure is added by this project.

### Rationale
There is one human and one machine; adding internal RBAC or a login would be pure ceremony (YAGNI). The real risks are outbound-credential lifecycle ones — mid-draft token expiry (R2) and provider tightening (Yahoo's new access-application process noted in research). The rejected alternative — running components as services with their own credentials — adds operational surface with zero threat-model benefit for a laptop-local system.

### Implementation
- Yahoo: existing registered app; authorization-code flow via the repo's oauth helper; access token TTL 3600s → proactive refresh at ~45 min and always immediately before draft start; refresh handled by the same wrapper pattern spilchen/yfpy use.
- FP public API: `x-api-key` header, key from `.env`.
- Draft-day preflight (part of the smoke-test runbook): verify token refresh works, verify draftresults endpoint answers for the league, verify FP/Sleeper caches are warm — before the draft room opens.
- Postgres: localhost-only, existing local auth; no remote listeners.

### Risk if Skipped
An expired Yahoo token 40 minutes into the draft silently stops pick sync (R2) — the board recommends against a stale player pool, which is worse than no board. Unmanaged refresh is the single most likely draft-day failure and costs nothing to prevent.

---

## Domain 5: Logging & Observability

### Decision
Structured JSON logs for all pipelines and the draft assistant, written to per-component local files plus the run-record table in Postgres for anything that affects data freshness. Observability is briefing-first: the morning briefing IS the dashboard — its header is feed health, data vintages, last-run statuses, and any adjustment/audit anomalies, so the one artifact the user reads daily carries the alerts (fail-loud made visible). Draft-day gets its own high-verbosity event log (every poll, pick diff, mode transition, recommendation shown, and latency measurement).

### Rationale
Addresses risks R5, R10, R14, R15 and the register's cross-cutting note that silent staleness is deadlier than crashes. A separate monitoring stack (Grafana etc.) would be unread ceremony for a solo operator; putting health at the top of the artifact the user already reads every morning is the only alerting channel with a guaranteed audience. The draft-day event log doubles as the tuning dataset for poll cadence (R2's "measure lag in rehearsal") and the post-mortem record.

### Implementation
- Python `structlog` (JSON lines), one file per component per day under `logs/` (already gitignored); INFO default, DEBUG for draft-day assistant during rehearsals.
- Run-records table (Domain 1) is the machine-readable health source; briefing generator queries it and renders the health header; any ERROR-level event in the last 24h is surfaced verbatim.
- Signal/adjustment audit log (agent lane): every signal with source URL, confidence, resulting capped adjustment, and confirm/deny status — queryable for the weekly bias audit (R10).
- Sim farm nightly adversarial report includes its own data-vintage line so a stale-input sim run can't masquerade as fresh strategy evidence (R7).
- Retention: everything local, pruned at 90 days except draft-day logs and audit logs (kept for the season).

### Risk if Skipped
When the board looks wrong in week 5, no logs means no way to distinguish scoring bug vs. crosswalk miss vs. stale source — mean time to diagnose goes from minutes (query run records) to a day of manual replay, during the exact week (R3) that has zero slack.

---

## Domain 6: Dependency Management

### Decision
Accept five external data dependencies with explicit per-dependency fallback stances, ranked by replaceability: Yahoo (irreplaceable — sole source of league/draft state; protect via caching, throttling, and scheduling bulk work away from live windows), Sleeper (primary projections, undocumented — snapshot daily, validate schema, fallbacks: FP API + ESPN hidden API, paid 4for4 if FD imputation proves noisy), FantasyPros API (overlay — fallback: authenticated page/export parsing à la ffpros), nflverse (static GitHub releases, effectively archival — vendored local copies), expert feeds (advisory only — rot is tolerated, health-tracked). Python dependencies are pinned via lockfile; the R ecosystem is used as design reference only (no runtime R dependency).

### Rationale
Addresses risks R5, R12, R13, R15 and the register's cross-cutting Yahoo notes directly. The dependency posture follows one rule: the more irreplaceable the source, the more conservative our usage pattern. Rejected alternative: depending on FP as the projection backbone (original 2025 design) — research showed Sleeper is strictly more granular and free, and FP's 100-call/day cap makes it a poor primary. Rejected: runtime dependency on R/ffanalytics — porting the aggregation *ideas* to Python beats shelling out to an unmaintained-for-us toolchain.

### Implementation
- Python: `uv` with `pyproject.toml` + `uv.lock` — a decision, not an option (the repo currently has no lockfile or requirements file at all; README's bare `pip install` instructions are superseded). Pinned versions, no auto-upgrades inside the 6-week window; freeze all versions at week 6.
- Yahoo call budget: bulk historical import throttled (~1 req/2s, resumable, run overnight); zero bulk jobs within 24h of any rehearsal or the draft; live poll cadence 5–10s tuned by rehearsal lag measurements.
- FP call budget: daily sync ≤30 calls (positions × formats), cached in `raw`; ad-hoc queries read cache, never the API.
- Sleeper: daily snapshot + schema-hash comparison; drift = fail loud (Domain 1) and auto-open a "switch to fallback source?" decision in the briefing.
- Per-dependency runbook entry: what breaks if it dies, which fallback, how long the switch takes.

### Risk if Skipped
Unmanaged, the two rate-capped providers get exhausted by accident (a stray backfill mid-rehearsal → 999 lockout, R15/R2) and a Sleeper shape change silently zeroes first-down points (R5) — the board loses its core edge (FD pricing) while looking perfectly healthy.

---

## Domain 7: Testing & Validation

### Decision
Correctness is anchored by golden tests against external ground truth, not self-consistency: the scoring engine must exactly reproduce Yahoo's official computed fantasy points for a curated set of 2025 player-weeks before anything downstream is built on it. Strategy claims are validated by backtests against historical actuals (non-circular by construction); infrastructure is validated by rehearsal drills treated as acceptance tests with pass/fail criteria; agent outputs are validated by schema enforcement plus sampled human review at the briefing confirm gate. Coverage target is asymmetric by blast radius: exhaustive on the scoring engine and crosswalk, moderate on valuation logic, thin on one-shot import scripts.

### Rationale
Addresses risks R1 (golden tests are the only reliable detector for rule misencoding), R16 (methodology error — golden tests cannot catch a wrong algorithm, so backtest comparison, FD cross-source divergence checks, baseline sensitivity analysis, and bonus-calibration reports validate the three novel algorithms separately), R7 (backtests break sim circularity), R2 (drills convert "should work" into "measured working"), R10 (schema + sampling is the proportionate control for capped agent adjustments), R6 (crosswalk validation report). The asymmetric coverage rejects blanket coverage targets: a bug in `import_league_326814.py` wastes an evening; a bug in the scoring engine wastes the draft.

### Implementation
- `pytest`; golden fixtures: ~40 hand-picked 2025 player-weeks (bonus-threshold edge cases, multi-bonus stacks, negative-point games, DEF tiers, return yards) with Yahoo's official points as expected values — exact match required; second golden source: Sleeper's historical bonus flags.
- Property tests (hypothesis): valuation monotonicity (more projected points at same position ⇒ ≥ VORP), replacement-baseline sensitivity bounds, adjustment-cap invariants.
- Backtest suite as regression harness: 2023–2025 seasons; any strategy/valuation change must not degrade backtest composite beyond noise bands (tracked in the nightly report).
- Rehearsal drills with written pass criteria (poll lag < 15s p95; token refresh mid-session without pick loss; forced-999 → manual switchover < 30s; crash → resume with full state).
- Signal validation: pydantic schemas on all agent outputs; 100% human confirm on adjustments (by design); weekly audit query comparing adjustment direction vs. subsequent ADP/news movement (R10).

### Risk if Skipped
Without golden tests the most likely catastrophic bug (R1, I10) ships invisibly — every sim, tier, and recommendation inherits it, and it surfaces as "why does the board love this guy?" with 30 seconds on the clock. Without drill criteria, draft-day readiness is a feeling, not a fact.

---

## Domain 8: Deployment & Rollback

### Decision
"Deployment" is local: code runs from the git repo on the user's Mac; scheduled work runs under launchd (wake-safe, unlike cron); the draft assistant runs foreground in a terminal. Rollback is git-based for code, version-pointer-based for scoring configs (the board can be rebuilt under any prior config version), and pg-dump-based for data. A hard feature freeze at week 6 converts draft week into a no-deploy zone: only reverts allowed; the last rehearsed commit is tagged and is the only thing that runs on draft day.

### Rationale
Addresses risks R3 (freeze is the scope-control backstop), R8 (config versioning makes a late league-rules change a config edit + board rebuild, not a code change during freeze), R14 (launchd + dumps). Rejected: containerization/cloud deployment — zero benefit for a single-machine, single-user system and it adds failure modes to the exact day we need fewest. The one cloud-ish exception considered (running ingestion on a VPS for reliability) is deferred: launchd + fail-loud briefing alerts is enough for a 6-week horizon.

### Implementation
- launchd plists for: nightly ingestion + briefing build (morning), sim farm (overnight), pg_dump (nightly). Jobs are idempotent and resumable; a missed run self-reports via the run-record table (Domain 5).
- Git tags: `rehearsal-N` after each passed drill; `draft-day` tag = last passed full rehearsal; draft-day runbook step 1 is `git checkout draft-day`.
- Scoring config rollback: configs immutable + numbered; board build takes `--scoring-config vN`; a bad config change is rolled back by pointing back to vN-1 and rebuilding (minutes).
- Database: nightly `pg_dump` (14-day retention) + one pre-draft external-drive copy; restore procedure documented and tested once in week 5.
- Post-change smoke test (scripted): rebuild board from cache → golden-test subset → render top-50 diff vs. previous board with changes explained (config? data? code?).

### Risk if Skipped
A "quick improvement" pushed draft-day morning with no rollback point is the classic self-inflicted meltdown: an untagged working state can't be recovered under time pressure, turning a 5-minute `git checkout` into drafting off a paper printout (the PAPER mode floor exists, but reaching it by own-goal is unforgivable).

---

### Domain 9: Audit Trail & Action Logging — SKIPPED
**Risk if Skipped:** Tier 3 domain; the system takes no autonomous consequential actions — the capped, human-confirmed adjustment log in Domains 2/5 already covers the only agent-influenced writes.

### Domain 10: Kill Switches & Blast Radius — SKIPPED
**Risk if Skipped:** Tier 3 domain; maximum blast radius of any single automated operation is a wrong number on a locally-rendered board that a human must act on, and mode-downshift (Domain 1) already provides the stop mechanism.

### Domain 11: Compliance & Data Privacy — SKIPPED
**Risk if Skipped:** Tier 3 domain; the only personal data is the user's own league history handled under Yahoo's API terms (single account, no redistribution, attribution respected), with no third-party PII processing or external publication.

---

## Architecture Review Summary

**Domains Addressed:** 8 / 11
**Domains Skipped (below tier):** none
**Domains Skipped (above tier, risk accepted):** 9, 10, 11

**Top 3 Architectural Risks Accepted:**
1. Yahoo remains a single irreplaceable provider for live draft state — no true redundancy exists; mitigation is drilled degradation (manual mode), not failover.
2. Sleeper's undocumented endpoints as projection backbone — accepted for superior granularity (first downs) with snapshot+validation+fallback, but a July shutoff would force a mid-build source migration.
3. Single-machine operation (laptop + home network on draft day) — accepted with hotspot/power/persistence hardening rather than cloud redundancy.

**Risks Carried Forward from Pillar 1 (not fully addressed by architecture):**
- R3 (scope overrun) — architecture can't fix a calendar; controlled by phase gates and the week-6 freeze, tracked at the week-3 checkpoint.
- R7 (sim-to-reality transfer) — partially mitigated by backtests and tendency-blending; residual epistemic risk is inherent.
- R11 (backtest input sourcing) — unresolved until the week-3 sourcing attempt; fallback documented.
- R4 residual: if settings audit reveals most history is 1QB-era, tendency models get demoted to descriptive color and the simulator leans harder on ADP-noise + recent seasons.

**Reviewer:** Brent Bartosch (pending)
**Date:** 2026-07-08
