# Architecture Decision Record — in-season-ops

**Date:** 2026-08-30
**Project:** fantasy_football
**Design doc:** [../specs/2026-08-30-in-season-ops-design.md](../specs/2026-08-30-in-season-ops-design.md)
**Risk register:** [2026-08-30-in-season-ops-risks.md](2026-08-30-in-season-ops-risks.md)
**Tier:** 2 (inherited from risk register)
**Domains covered:** 1, 2, 3, 4, 5, 6, 7, 8
**Domains skipped:** none

## Preconditions (Step 0)

Nothing in Domains 1–8 gets built until the four items below are done. They are not "phase 0 of the plan" — they are entry conditions for the plan, because each one invalidates design assumptions the rest of this ADR depends on. Directives 1, 7, and 8 from the risk register are **accepted as written**; the only modification is sequencing (P2 must start today; P4 runs in parallel rather than blocking).

**P1 — Fix R1 before any new code (directive 8, accepted).** `scripts/ingest_nflverse.py` is in no launchd plist and no crontab, is 1209h (50 days) stale, and `morning_briefing.py` renders it `[OK]` because the `raw.ingest_runs` loop tests `status == 'success'` and never tests `age_h` (which it already computes and prints). The design doc's "step 1 works today, no Yahoo needed" premise is false until this is fixed. Deliverable: nflverse added to a scheduled job, plus the age-aware health state from Domain 5, plus a regression test that a stale-but-successful run renders non-OK. One commit, own PR-equivalent, no new-module code merges ahead of it.

**P2 — Start the `market_trends` archive today (directive 7, accepted).** Sleeper's trending endpoint is a rolling window with no historical access; every day we don't archive is a day of validation data that can never be recovered (R8, L9×I6=54). This is a ~30-line script and a `raw` table, and it is the only decision in this ADR that expires. It ships before the observed-mechanics tests, before the health work, before anything.

**P3 — Observed-mechanics tests (~2h, $0) (directive 1, accepted).** The register's four tests against the 2025 Yahoo transaction log and the live settings page: actual waiver processing hour (R2), settings-page diff vs `league_rules.md` with an `as_of` stamp (R16), drop→clear interval fit with FCFS-vs-priority determination (R4), and the daily authenticated Yahoo probe with a hard decision date of **Sept 10** (R14). Their outputs are not notes — they are the initial contents of `config/league_clock.yaml` (Domain 2). Until P3 lands, the clear-time state machine's award-mechanism field is unset and window-2 alerting is disabled (fail-closed, Domain 1).

**P4 — Sim-farm ceiling test as a scope gate, not a blocker (directive 1, accepted with modification).** The 3–5h perfect-foresight-vs-do-nothing waiver run (R24) gates build steps 3–4 only. Modification: it runs on the sim farm in parallel with build steps 1–2 rather than serially ahead of them, because steps 1–2 are justified by the trend engine's standalone value and blocking them on a 5-hour job burns calendar we don't have (R11). If the ceiling is under ~8 playoff-probability points, steps 3–4 are cut and this ADR's Domains 2/6/8 shrink accordingly.

---

## Cross-cutting decisions

**QB-repair as Goal 0 (directive 6, accepted with modification).** The design doc treats the QB room (Willis/Shough/Sanders in a 2QB league) as a watchlist parenthetical inside `waiver_advisor.py`. Promoted: QB repair is a first-class objective that every module carries an obligation toward. Modification — it does not become a fifth module (YAGNI); it becomes (a) a standing reservation policy in priority pricing, (b) the first trade angle built, pulled forward out of build step 4 into step 2 as a heuristic over manually-captured rosters, and (c) an acceptance criterion in Domain 7. R19's point is structural and worth restating: the system's edge is information asymmetry, and the QB market in a 2QB league is the one market with near-zero asymmetry — it is decided by priority position and willingness to spend, not by knowing something. So the QB fix is an *allocation* decision the system must make explicit, not a signal it can discover.

**Directive adjudication index.**

| # | Directive | Verdict | Adjudicated in |
|---|---|---|---|
| 1 | Step 0 + sim-farm ceiling gate | Accept (P4 parallelized) | Preconditions |
| 2 | Manual-first, API-as-optimization | Accept | Domain 6 (posture), Domain 2 (source of truth) |
| 3 | League-clock spec + data-readiness triggers | Accept with modification (hard fallback fire time) | Domain 2 |
| 4 | Three-state health + fail-closed urgency | Accept | Domain 1 |
| 5 | Push channel or de-scope windows 2+3 | Split: fund push for windows 1/2/QB; **de-scope window 3 automation** | Domain 2 (policy), Domain 8 (implementation) |
| 6 | QB-repair objective | Accept with modification (objective + gate, not a module) | Cross-cutting, Domain 7 |
| 7 | `market_trends` archive now | Accept | Preconditions (P2) |
| 8 | Fix R1 first | Accept | Preconditions (P1), Domain 5 |

Where a directive conflicts with the design doc, the directive wins and the conflict is named in the relevant Decision.

---

### Domain 1: Error Handling & Failure Design

#### Decision
Three data states, not two: **OK / KNOWN-LAGGING / BROKEN** (directive 4, accepted — this supersedes the design doc's binary DEGRADED banner). Every source declares an expected-cadence contract in a committed registry; state is derived from `(status, age, expected lag window)`, not from status alone. Banners fire only for BROKEN and for KNOWN-LAGGING that has crossed its own declared deadline; routine, structural lag (nflverse publishing a day after MNF, every single week) renders as a quiet timestamp line. Derived composites **fail closed**: the urgency score, ASCENDING/FALLING flags, priority pricing, and clear-time alerts refuse to render when an input has failed its gate, emitting `NO SIGNAL — <source> <state> since <ts>` rather than computing with a zeroed or defaulted input. Semantic sanity gates sit at every ingest boundary, extending the existing 2QB gate pattern in `src/ffi/sim/pool.py` to feeds that currently only get a structlog warning. The manual league-state path carries precision metadata and the advisor refuses precision it doesn't have.

#### Rationale
Addresses risks R1, R5 (partial), R6, R10, R12, R17, R23. The register's cross-cutting root cause (a) is decisive: this system already guards data *absence* well and degraded *presence* not at all — R1 is a live proof, and the Aug-2026 Sleeper `adp_2qb` semantic drift is a second. The rejected alternative is the design doc's own text ("report renders with an explicit DEGRADED banner naming the stale source"): with two of three sources structurally lagging most weeks, a binary banner is on every report, and R12 (L8×I6=48) says fail-loud degrades to fail-decorative within two weeks. The second rejected alternative is fail-open urgency — an empty Sleeper trending pull yielding `market_signal = 0` makes every player look maximally undiscovered, which is the worst possible failure for a system whose output spends a capped, non-replenishing resource. A loud absence costs one quiet week; wrong-with-confidence costs moves and priority position. Per user CLAUDE.md, every try/except and fallback written here goes through the fail-loud-error-handling skill.

#### Implementation
- `src/ffi/health.py`: `SourceState` enum + `state(source, status, last_success_at, now)`; cadence registry in `config/source_clock.yaml` (per source: expected interval, expected lag window, deadline, owner). Consumed by `morning_briefing.py` (Domain 5) and every report renderer.
- `src/ffi/ingest/gates.py`: field-set diff vs prior snapshot, distribution assertions, week-over-week rank correlation ≥ 0.85. Gate failure raises, the run is recorded as `status='sanity_failed'`, and downstream treats the source BROKEN. Applied to every Sleeper feed (projections, ROS, trending) — the `adp_2qb` incident is the template.
- Fail-closed rendering: one helper (`render_or_refuse`) used by the urgency column, ASC/FALL lists, priority pricing, and clear-time alerts. No component computes a composite from a BROKEN input, ever.
- Partial-publish guard (R5): `usage_weekly` writes a `games_complete` / `teams_observed` count; share metrics (snap, target, carry, route) refuse to compute on an incomplete team-week rather than dividing by a short denominator and firing false ASCENDING.
- Screenshot path (R17): `source` and `ts_precision` columns declared at schema design time, not retrofitted; the advisor refuses to emit minute-resolution alerts on `ts_precision='day'` rows; impossible-value checks (>5 adds/team/week, negative move counts) hard-fail; capture gaps >72h **disable** windows 2 and 5 rather than degrading them.
- Retries: existing 3-attempt exponential backoff (base 2s, jitter) for transient Sleeper/nflverse HTTP. **No retry against a Yahoo 403** — it is an authorization state, not a transient error; one probe per day, status logged (Domain 4).
- FP budget (R23): the `&&` job chain is the bug — an `FpBudgetExceededError` currently kills `morning_briefing.py`, silencing the dashboard that would have reported the exhaustion. Chains become `;`-separated with a trap so the briefing runs unconditionally and reports upstream failures; plus a Sunday sub-budget reservation (weekday cap 20 of 30, 10 reserved).
- TBD — design doc does not specify: per-feed numeric sanity thresholds beyond the ≥0.85 rank correlation. Set empirically from the first four weeks of the P2 archive; until then gates run in observe-and-log mode for trending only, hard-fail for projections.

#### Risk if Skipped
R1 is not hypothetical, it is live at L10×I8=80: a 50-day-frozen `usage_weekly` produces a perfectly plausible "quiet week" report, and the operator acts on it. Without the three-state model, R12 (L8×I6=48) makes every report look identical whether or not something is actually broken — by week 3 the banner conveys no information and a real outage hides inside it. Without fail-closed urgency, R6 (L7×I8=56) converts a silent Sleeper shape change into confident recommendations that spend a 5-move week and a rolling-priority slot that takes weeks to regain. Recovery from any of these is not a rollback; it is a wasted week of the 14 that exist.

---

### Domain 2: Data Flow & State Management

#### Decision
Postgres remains the single source of truth and the existing layering holds: immutable dated snapshots in `raw` (including the P2 `raw.sleeper_trending` archive), normalized derived tables in `public` keyed by crosswalk ID so all cross-source joins resolve in one schema, everything derived recomputable from raw + config. Three changes to the design doc, all directive-driven:

**(1) League-state polarity reverses (directive 2).** The manual capture path is the source of truth for `league_transactions` / `league_rosters`; the Yahoo API is a backfill and cross-check, not the primary. The design doc says "API is the primary league-state source" — the directive wins, and the reasoning is the draft-day lesson already in this repo's memory: a 403 on a freshly-granted key with no appeal path is not a dependency you build a season on. Both paths write through the same adapter schema with `source` + `ts_precision` provenance; where both exist for one event, API rows supersede manual rows only through an explicit reconciliation pass that logs the diff — never a silent overwrite.

**(2) Wall-clock scheduling is replaced by a league-clock spec plus data-readiness triggers (directive 3, accepted with one modification).** `config/league_clock.yaml` — committed, `as_of`-stamped, tz-aware — holds the observed waiver processing hour, the drop→clear state machine over the weekly calendar, roster lock times, and the trade/playoff deadlines; every job time is derived from a deadline in that file rather than hardcoded. Jobs fire on ingest completion within a deadline-bounded window. **Modification:** each trigger also carries a hard fallback fire time. A pure data-readiness trigger means a feed that never publishes produces a report that never renders, which is fail-silent — the exact failure class this ADR exists to eliminate. At the fallback time the job runs anyway and renders KNOWN-LAGGING or BROKEN sections. Cadence becomes dual: a **Monday-evening claims brief** (the decision artifact, before the observed processing hour) and a **Tuesday trends report** (the analysis artifact). The design doc's single Tuesday 6:30am report is superseded — R2 (L8×I9=72) says a report that renders after ~3am ET processing is a post-hoc newsletter and kills windows 1 and 4 every week of the season.

**(3) Outbound delivery is part of the data flow (directive 5, split verdict).** The report is not the only channel. A minimal push channel (Domain 8) carries three event classes; the Tuesday report additionally syncs to a location readable off-laptop. Honest adjudication of windows 2 and 3: **window 2 gets the push channel** (clear-times are predictable timestamps once P3 fits the model, so a push is genuinely actionable). **Window 3 is de-scoped from automation** — R20 is right that no component in this design produces inactives, the 7am briefing fires ~90 minutes before they publish, and FP is budget-capped. Window 3 becomes prepped-card-plus-manual-check: the contingency cards ship in the Tuesday report, the Sunday trigger is the operator's own eyes, and the push channel carries the card only when the operator manually flags a scratch. Calling that "automated" would be the design doc lying to itself.

Cold start (R7): trend rules declare their minimum window; weeks 1–3 run 1–2 week variants with camp/preseason priors and are labeled COLD-START in the output. An empty ASC/FALL section in week 1 is not acceptable — that is the highest-value waiver week of the season.

#### Rationale
Addresses risks R2, R4, R5, R7, R8, R9, R16, R21, R22. The unifying insight is the register's root cause (c): the output cadence was set by data convenience, not by the league's clock, so the arbitrage windows and the artifacts were never going to line up. Deriving job times from written deadlines makes that mismatch a config error someone can see rather than a structural defeat. Reversing the Yahoo polarity costs us nothing we have (the API has never worked) and buys a system that is complete at kickoff. The rejected alternative on scheduling — keep fixed times, accept the lag — was rejected because R2's impact (9) applies every week for 14 weeks, which is not a risk, it is a guaranteed loss.

#### Implementation
- New tables: `public.usage_weekly` (per player-week snap/target/carry/route shares, RZ touches, `games_complete`, `weeks_observed`), `public.league_transactions` and `public.league_rosters` (with `source`, `ts_precision`, `captured_at`), `public.market_trends` (normalized daily view over `raw.sleeper_trending`).
- `config/league_clock.yaml` + `config/source_clock.yaml`, both committed and `as_of`-stamped; `league_rules.md` gets an `as_of` header and its transaction/waiver/playoff fields become machine-readable config the modules read directly (R16 — prose a human re-transcribes is how a renewal-time settings change poisons the ledger, the clear math, and the priority model simultaneously).
- Clear-time model (R4): a state machine over the weekly calendar, not `drop + 1 day` arithmetic. Fixtures generated from the P3 observed pairs, not from the settings page. Until P3 fits the award mechanism, the FCFS-vs-priority field is unset and window-2 alerting stays disabled (Domain 1 fail-closed).
- Snapshot pinning (R22): each report captures a `computed_at` / `config_version` at start and reads within one repeatable-read transaction, so `build_valuation.py`'s DELETE+INSERT in the 7am chain cannot produce a half-old, half-new report. The Tuesday/Monday jobs additionally serialize against `com.ffi.morning` with a Postgres advisory lock (`pg_try_advisory_lock`) — cleaner than a lockfile because it dies with the connection.
- Budget ledger (R21): event-sourced from transactions, with a periodic reconciliation against the move counts Yahoo's UI displays, and a conservative 1–2 move safety margin on every "opponent is capped" call. Counting semantics (failed claims? IR moves? drop-and-re-add? reset boundary?) come from the P3 empirics, not from inference.
- Backtest baseline swap (R8): Sleeper trending has no historical endpoint, so the 2025 lead-time baseline becomes league add-timestamps plus FP news timestamps; the P2 archive makes a same-season validation possible from ~week 6 onward.
- Market-proxy audit (R9): once any transaction data exists, a weekly hit-rate query of in-league adds vs trending rank, computed separately for QB. Decision gate at week 6 — if rank correlation < 0.4, the market overlay is demoted to a tiebreaker and the urgency score stops being a headline number.
- Backup: existing nightly `pg_dump` via `scripts/backup_db.sh`, plus the offsite line from Domain 6.

#### Risk if Skipped
R2 at L8×I9=72 is the single largest addressable risk in the register and it is a scheduling decision, not an engineering problem: a Tuesday-morning report against Tuesday-overnight processing means the flagship deliverable names players who were rostered while the operator slept — every week, for the entire season, with the build cost already sunk. R5 (L8×I7=56) means the report structurally cannot contain Monday-night data, the hottest signal of the week. R7 (L8×I7=56) silences the engine for weeks 1–3, when league-winners are disproportionately claimed. R16 (L5×I9=45) is a 15-minute check whose omission corrupts the ledger, the clear math, and the priority model at once — and a FAAB switch at renewal would make windows 4 and 5 total losses that the system would keep confidently reporting on.

---

### Domain 3: Secrets & Configuration

#### Decision
No change to the established pattern, because nothing in this system changes the threat model. Secrets live in a gitignored `.env` loaded via `python-dotenv` at process start (the `src/ffi/db.py` and `src/ffi/yahoo_client.py` convention), with the Yahoo OAuth token pair in `config/yahoo_token.json` at mode 600. This system introduces exactly one new secret: the ntfy topic URL, which is a capability URL — anyone holding it can push to the operator's phone — and is therefore stored as `NTFY_TOPIC_URL` in `.env` with a placeholder in `.env.example`. New *configuration* introduced here (`config/league_clock.yaml`, `config/source_clock.yaml`, `config/modules.yaml`, the `as_of`-stamped league rules) is non-secret, versioned, and committed. Screenshot captures of league pages are not secrets but do contain other managers' names and roster state; they land in a gitignored `data/captures/` directory and are never committed.

#### Rationale
Addresses no register risk directly (no secrets risk scored in the top 29) — which is itself the finding: the existing pattern is sound and the proportionate decision is to extend it by two lines rather than introduce a vault this project has no use for (YAGNI). The threat model remains "accidental git commit / laptop theft," single operator, single machine. The one genuinely new consideration is the capability-URL semantics of ntfy topics, handled by classification rather than infrastructure.

#### Implementation
- Add `NTFY_TOPIC_URL` to `.env` and a placeholder line to `.env.example`; never logged, never rendered into a report artifact.
- Add `data/captures/` to `.gitignore` alongside the existing `.env` / `*.token` / `config/yahoo_token.json` entries.
- Token file written atomically (temp + rename) on refresh; permissions 600 asserted at write time.
- Existing pre-commit secret grep hook stays; extend its pattern list with `ntfy.sh/`.
- Rotation remains manual and documented: ntfy topic is regenerated by changing one env var; Yahoo secret/token rotation follows the existing runbook note.

#### Risk if Skipped
A leaked ntfy topic lets a stranger push fake waiver alerts to the operator's phone during a live window — low severity, funny, and genuinely capable of costing a claim. A committed `.env` exposes the Yahoo client secret and refresh token, which carry read and write scope on the user's league account; remediation is roughly an hour of revocation and re-auth, but with the API already 403-gated behind a manual approval process, a revoked-for-abuse Yahoo app may simply never be re-approved — a permanent loss of the Domain 6 optimization path.

---

### Domain 4: Authentication & Access Control

#### Decision
Outbound-only, single operator, no auth surface of the system's own — unchanged from the draft-intelligence ADR. What changes is the Yahoo posture: since Yahoo is demoted to an optimization (Domain 6), its auth lifecycle is managed for *reliability of an optional path*, not for availability of a critical one. Concretely: the five accumulated auth scripts (`yahoo_auth.py`, `yahoo_auth_simple.py`, `yahoo_manual_auth.py`, `setup_yahoo_auth.py`, `oauth_https_server.py`) are consolidated to one supported entry point — `src/ffi/yahoo_client.py` — with the rest archived, so that a token problem in week 8 is a bug fix and not archaeology across a fossil record. Token refresh becomes proactive inside the daily job rather than lazy-on-401. A daily authenticated probe of `league/326814/transactions` logs its status, with a **hard decision date of Sept 10**: if the probe is still 403 at kickoff, the API branch of the adapter is formally shelved and marked as such in the briefing, not left as perpetual "coming soon." Sleeper and nflverse are unauthenticated and add no auth surface; Postgres stays localhost-only.

#### Rationale
Addresses risk R14. The register's framing is exact: Yahoo approval has no SLA, no status page, and no appeal path (memory: 403 even on a fresh grant), and the design doc silently reversed the documented "assume no approval" posture. Auth work here is therefore capped at what makes the optimization path *reliable if it ever arrives* and *visibly dead if it doesn't* — the failure mode being avoided is neither a security breach nor an outage, but a season spent believing approval is imminent. Consolidating the auth scripts is the cheap half of that: five half-working paths is a guarantee that the one used in October is not the one debugged in July. Internal RBAC or a login remains pure ceremony for one human and one machine.

#### Implementation
- Single client module: `src/ffi/yahoo_client.py` (existing `load_dotenv` + `YAHOO_CLIENT_ID` / `YAHOO_CLIENT_SECRET` pattern retained); other auth scripts moved to `scripts/archive/` in the same commit that names the supported path.
- Proactive refresh: access token TTL 3600s → refresh at ~45 min of age inside any long job, and unconditionally at the start of the daily chain and before any league-state capture attempt.
- Probe: `scripts/probe_yahoo_access.py`, run daily, writing a run record to `raw.ingest_runs` under source `yahoo_probe` so the briefing renders approval state as a first-class health line (Domain 5). Status codes logged verbatim; no retries on 403 (Domain 1).
- Minimum scope requested (read); token written atomically at 600 (Domain 3).
- No new network listeners; Postgres unchanged.

#### Risk if Skipped
R14 at L6×I8=48. Without proactive refresh, the first live capture after approval fails on an expired token during a waiver window and looks identical to a continued 403, which sends the operator debugging the wrong problem. Without the probe and the Sept 10 decision date, the manual path stays under-invested all season because the API is perpetually "about to work" — which is precisely how the design doc came to list a 403-gated endpoint as its primary source.

---

### Domain 5: Logging & Observability

#### Decision
Briefing-first observability, extended rather than replaced: `scripts/morning_briefing.py` remains THE dashboard, and every new signal this system produces about its own health lands in that one artifact the operator already reads daily. Four additions: (1) the health loop becomes age-aware and three-state — the R1 fix, which is the highest-value line of code in this entire ADR; (2) **artifact-freshness assertions** — the briefing goes RED if the Monday claims brief or Tuesday trends report is absent or stale against its league-clock deadline, so a missed job is distinguishable from an unread one; (3) a **capture-recency line** for the manual league-state path, since a human-operated feed needs monitoring exactly like a vendor one; (4) an **archive-continuity line** for `market_trends` (days archived, gaps detected), because a silent gap there is unrecoverable. Component logs stay structlog JSON lines under `logs/`, existing pattern, 90-day retention.

#### Rationale
Addresses risks R1, R12, R13. The register's headline finding is an observability failure, not a data failure: the ingest was broken for 50 days *and the dashboard said OK*, because the health check tested `status` and never `age_h` — while computing and printing `age_h` on the very same line. That is the whole argument for putting the three-state model in the briefing rather than in a separate monitoring stack: the alerting channel with a guaranteed audience is the one the operator already opens. A Grafana instance would be unread ceremony. The rejected alternative — alert on every non-OK state — is R12's banner fatigue in a different costume; expected lag renders as a quiet timestamp, and only unexpected states get a mark.

#### Implementation
- Replace the current health loop body in `morning_briefing.py`:
  ```python
  mark = "OK" if status == "success" else "RED"
  ```
  with a call to `ffi.health.state(source, status, age_h)` returning OK / KNOWN-LAGGING / BROKEN against `config/source_clock.yaml`, and append to `red_flags` only on BROKEN or on KNOWN-LAGGING past its declared deadline. The existing `STALE_HOURS = 36` global becomes a per-source contract (nflverse's expected lag is not Sleeper's).
- Artifact freshness: glob `reports/claims-*.md` and `reports/trends-*.md`, compare newest mtime against the league-clock deadline for the current week, RED on absent/stale. Mirrors the existing `pg_dump` freshness block, which is already the right pattern.
- Capture recency: hours since the newest `league_rosters.captured_at`; >72h RED and, per Domain 1, windows 2 and 5 disable themselves.
- Archive continuity: count of distinct archive days in `raw.sleeper_trending` vs days elapsed since P2; any gap listed explicitly.
- FP budget line extended with the Sunday reservation state (`used_today/30, sunday_reserve_remaining`).
- Trend-engine output logged with evidence lines and rule IDs, so a false ASCENDING in week 6 is traceable to the rule and threshold that fired it (feeds the Domain 7 precision work).
- Exit-nonzero-on-red behavior retained so launchd surfaces failures.

#### Risk if Skipped
This is the one domain where the counterfactual is already measured: R1 at L10×I8=80 has been running in production for 50 days behind a green checkmark. Skipping the age check means the in-season version of the same bug — a frozen `usage_weekly` producing plausible "quiet week" reports — burns weeks, not hours, because there is no crash and no empty section to notice. R13 (L8×I6=48) is the same failure in the time dimension: without artifact-freshness assertions, a laptop asleep at the trigger time produces no report, and no report is indistinguishable from a report the operator hasn't opened yet.

---

### Domain 6: Dependency Management

#### Decision
Five dependencies with explicit stances, and one deliberate demotion. **nflverse** — primary and effectively archival (static GitHub releases), but 2026 route/participation column granularity is unverified against 2025, so a preseason week gets pulled now and any trend rule whose input columns are missing is *dropped with an announcement*, never silently nulled. **Sleeper** — free, unauthenticated, undocumented; supplies ROS projections and trending; guarded by Domain 1 sanity gates and archived daily since there is no history endpoint. **Yahoo — demoted from dependency to optimization (directive 2, accepted).** The system must be fully functional with zero Yahoo access; every module's acceptance criterion is met using manual capture alone. **FantasyPros** — budget-capped at 30/day with a Sunday sub-budget reservation. **Manual screenshot capture — promoted to a first-class dependency** whose vendor is the operator; its SLA is a human with a laptop, and it gets monitored (capture recency, Domain 5) and precision-typed (`ts_precision`, Domain 1) like any other feed. Python dependencies stay pinned via `uv` + `uv.lock`, frozen from Week 1 through Nov 22 except for security fixes.

#### Rationale
Addresses risks R14, R6, R8, R27, R28. The polarity reversal is the core decision and it follows the prior ADR's own rule — the more irreplaceable the source, the more conservative the usage pattern — applied honestly to a source that is currently *unavailable*, not merely fragile. The design doc's "API is the primary league-state source; screenshots cover the gap" inverts the observed reality (403 on a fresh grant, no SLA, no appeal, approval expected around kickoff at best), and the directive wins. Treating the manual path as a monitored dependency rather than a fallback is what makes this more than a relabeling: R17 (L6×I7=42) is precisely the risk that one successful draft-day parse doesn't generalize to a 14-week chore, and its failure mode is "success at the wrong precision" — rows that pass schema checks while lacking the minute timestamps window 2 depends on. Rejected alternative: web scraping Yahoo, which is out of scope by design-doc decision and remains so.

#### Implementation
- Per-dependency stance table added to the runbook: what breaks, what the fallback is, how long the switch takes.
- nflverse (R27): pull a 2026 preseason week immediately; a column-presence assertion at ingest emits the list of trend rules disabled by missing inputs into the briefing. Rules degrade by removal, not by silent null-handling.
- Sleeper (R6, R8): sanity gates per feed; daily `raw.sleeper_trending` archive (P2); schema-hash comparison against the prior snapshot, drift = hard fail.
- Yahoo (R14): probe + Sept 10 decision date (Domain 4); the adapter's API backend is written behind the same output schema as the manual backend but is never on the critical path for any acceptance criterion. Manual capture is scheduled for **Monday night**, deliberately off the critical path of the Monday-evening claims brief's data needs.
- FP (R23): weekday cap 20, Sunday reserve 10; the reservation is enforced in `fp_calls_today` accounting, not by convention.
- `uv.lock` pinned; no dependency upgrades inside the season window.
- R28 (L2×I9=18, one line): add an offsite rsync/cloud-sync target to `scripts/backup_db.sh` after the `pg_dump`. Backups currently live on the same laptop as the database; laptop loss takes both, RPO 24h. This is the cheapest risk retirement in the register and there is no reason it isn't already done.

#### Risk if Skipped
Leaving Yahoo as primary (R14, L6×I8=48) means windows 1, 2, 4, and 5 are dark for as long as approval doesn't arrive — which, at kickoff, is the observed state, so this is not a tail risk but the base case. R6 (L7×I8=56) is a rerun of an incident this repo has already survived once. R27 (L4×I7=28) silently removes two of five trend rules' inputs, and without the presence check the engine keeps reporting on rules it can no longer evaluate. R28 is a 24-hour RPO on the entire season's data plus every backup of it, retired by one line.

---

### Domain 7: Testing & Validation

#### Decision
Ship gates, not coverage percentages. Three classes of validation, each blocking something specific. **(a) Golden fixtures** (existing `tests/fixtures` pattern): transaction screenshot/HTML text → parsed rows including a name-collision case, clear-time math tz-aware with a mandatory DST-crossing fixture, cutline computation and priority pricing against fixture rosters. **(b) Statistical acceptance gates written as failing tests, not report lines** — a precision gate (full-2025 replay must emit ≤8 ASCENDING per week at ≥40% precision against labeled sustained role changes, with position-stratified thresholds and negative fixtures), a lead-time gate (median usage→production lead ≥1 week, or buy-low logic is demoted to descriptive color and does not drive recommendations), an ROS-mode gate (valuation-v2's ROS mode is specced explicitly, then the strict-win backtest is re-run at a week-8 truncated horizon; >5pp degradation is a red flag, and ROS output passes a sanity gate before the advisor consumes it), and a base-rate gate (the priority-pricing hurdle is computed from the operator's own 16-season history via `mine_history.py` / `audit_league_history.py` — the "2–3 league-winners per season" figure in the design doc is invented and must not survive into code). **(c) The sim-farm ceiling test** (P4) as the scope gate on build steps 3–4. Plus one product gate from the cross-cutting section: **the QB-repair acceptance criterion** — a week-1 dry run must surface at least one actionable QB item (claim, stash, or trade angle) or the waiver advisor is not done.

#### Rationale
Addresses risks R10, R15, R18, R24, R25, R29. Three of these are the same failure in different clothes — a number that is dimensionally plausible and numerically wrong, shipped because nothing was ever measured against ground truth. R10's round-number thresholds tuned on a single positive fixture would produce 40+ flags a week against a 5-move budget; R15's ROS mode doesn't exist yet and full-season calibration does not transfer to truncated horizons; R18's base rate was asserted in a design doc. Making these *tests* rather than report sections is the entire point: a failing test blocks a merge, and a report line gets rationalized. The rejected alternative is the design doc's "backtest hook" as an afterthought — R25 (L4×I8=32) says the system's core thesis (usage leads production by an actionable margin) is unmeasured, and if the median lead is under a week the trend engine sees role changes simultaneously with the market and the whole edge is imaginary. That measurement is a gate, not a follow-up. Coverage stays asymmetric by blast radius, per the prior ADR: exhaustive on parsing and clear-time math, moderate on ranking logic, thin on one-shot scripts.

#### Implementation
- `pytest`, existing conventions and fixture directory.
- Replay harness over the 2025 season producing per-week ASC/FALL lists; labels for "sustained 3+ week role change" derived from league add-timestamps and FP news timestamps (Sleeper trending has no history — R8), plus negative weeks where nothing happened.
- **Train/test split to avoid tuning on the gate:** thresholds tuned on 2024, gated on 2025. The labeling protocol is defined and frozen *before* any threshold tuning.
- ROS mode: specced as an explicit mode of the valuation engine (starts curve and replacement baselines re-derived for the truncated horizon, not reused), with a sanity gate on its output distribution before `waiver_advisor.py` reads it.
- Priority pricing modeled as a finite-horizon expiring option with a stopping rule, not a fixed hurdle rate (R18) — priority that reaches week 17 unused is worth zero, and a hurdle-rate model systematically hoards it there.
- DST fixture (R29): a clear-time computation spanning Nov 1, tz-aware datetimes throughout.
- Ceiling test (P4): perfect-foresight vs do-nothing waiver policy on the sim farm; <8 playoff-probability points cuts steps 3–4.
- TBD — design doc does not specify: the precise operational definition of a "sustained role change" for precision labeling. Defined once against the 2025 replay and frozen before tuning; not left to be settled after the first disappointing gate run.

#### Risk if Skipped
R10 at L7×I7=49 is the most likely way this system dies quietly: a flood of unfiltered flags that the operator stops reading by week 4, after which the build was for nothing regardless of its correctness. R15 at L6×I8=48 spends the season's entire capped move budget on a compressed, miscalibrated ROS number that no one can tell is wrong by inspection. R18 at L6×I7=42 hoards rolling priority into worthless expiry at week 17 while league-winners went elsewhere. R24 at L5×I7=35 is the meta-risk: 60–80 hours built on an asserted 40/60 split that a 3–5 hour sim run can test first.

---

### Domain 8: Deployment & Rollback

#### Decision
Deployment stays local: code runs from the git repo on the operator's Mac, scheduled work under launchd (wake-safe), reports written to `reports/`. Three changes from the draft-intelligence posture. **(1) Rollback unit is the module feature flag.** `config/modules.yaml` carries an enabled flag per module (`usage_trends`, `waiver_advisor`, `trade_angles`, `push`); a misbehaving module is switched off and its report section renders `OFF (disabled <date>)` rather than vanishing — a disabled module must be as visible as a broken one. Git-tag rollback remains for code, config-version rollback for valuation. **(2) The build order is dated against the season calendar, not sequenced by technical dependency** (R11): step 1 is timeboxed with a cut-scope trigger at 50% overrun; `trade_angles.py` must land before the **Nov 22** trade deadline or it is not built at all; the QB-repair angle is pulled forward into step 2 per the cross-cutting decision. A module delivered after its window has closed is not late, it is waste. **(3) A weekly no-deploy window:** changes land Wednesday through Saturday; Sunday through Tuesday is frozen, mirroring the draft-week freeze that worked. **Push channel (directive 5, funded):** a minimal ntfy channel, one topic, `scripts/notify.py`, ~4–6h, with a **hard cap of 3 pushes per week enforced in code** — over-cap events downgrade to the next report and are logged. Three event classes only: claim deadline, operator-flagged inactive contingency, QB-watchlist hit. Window 3's automation is de-scoped (Domain 2); the push channel exists for windows 1, 2, and the QB objective.

#### Rationale
Addresses risks R11, R13, R22. The register's root cause (c) — a 24-hour pull-based digest against minute-wide windows — is a *delivery* defect, and R3 (L7×I9=63) is the highest-scoring risk not otherwise assigned a domain. The honest options were fund a push channel or formally de-scope the latency-bound windows; funding the minimal version is right because the alternative wastes the analysis those windows exist to act on, and the 3/week cap is what keeps it from becoming the notification spam that gets muted in week 2 — a muted channel is worse than no channel, because it looks like coverage. The design doc's "push notifications deferred until proven necessary (YAGNI)" is superseded: the register proved necessity, and YAGNI does not mean declining to build the one component the value chain terminates in. Rejected alternative for scheduling: a cloud runner (Railway precedent) — real reliability gains, but it relocates the Postgres source of truth and adds a deploy surface mid-season; `pmset` plus artifact-freshness assertions is the proportionate control, with the cloud runner held as the escalation if a Tuesday actually slips.

#### Implementation
- New plists: `com.ffi.claims` (Monday evening, league-clock-derived) and `com.ffi.trends` (Tuesday, data-readiness triggered with a hard fallback fire time). Existing `com.ffi.morning` (7:00) and `com.ffi.simfarm` unchanged in schedule.
- `pmset repeat wakeorpoweron` covering all scheduled trigger times (R13); artifact-freshness assertion in the briefing as the detector when it fails anyway (Domain 5).
- Job serialization (R22): the Tuesday job takes a Postgres advisory lock that `com.ffi.morning` also takes; whichever runs second waits rather than read-skewing across `build_valuation.py`'s DELETE+INSERT. Moving the Tuesday job to 5:30 is the fallback if lock waits prove awkward.
- Job chains switch from `&&` to `;` with a trap so the briefing renders regardless of upstream failure (R23, Domain 1) — the current `com.ffi.morning` plist chains seven commands with `&&`, meaning any single failure silences the dashboard that would report it.
- `scripts/notify.py`: ntfy topic from `NTFY_TOPIC_URL` (Domain 3), a `push_log` table enforcing the weekly cap, and every suppressed push recorded so the cap's cost is visible.
- Rollback procedure: flip flag in `config/modules.yaml` → next scheduled run renders the section OFF (seconds); or `git checkout <last green tag>` (minutes); or valuation config version pointer (minutes). Post-change smoke test: regenerate the previous week's report from cache and diff against the archived copy, with any change explained as code, config, or data.
- Tuesday report syncs to a location readable off-laptop, so the analysis artifact is reachable when the operator isn't at the machine.

#### Risk if Skipped
R11 at L7×I7=49 is the failure the calendar makes inevitable: four modules, a part-time single builder, and a Yahoo-gated step 3 sequenced by technical dependency lands `trade_angles.py` after the Nov 22 deadline — 60–80 hours spent on a capability that expires before it ships. R13 at L8×I6=48 means a sleeping laptop silently skips the week's decision artifact, and without freshness assertions the operator finds out by not receiving something. R22 at L6×I6=36 produces an internally inconsistent report with no error raised — the exact "degraded-quality presence" class this whole ADR is organized around. And without the push channel, R3 stands at L7×I9=63: correct recommendations, generated on time, that never convert into a single transaction.
