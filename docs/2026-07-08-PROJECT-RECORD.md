# Fantasy Football Intelligence — Complete Project Record
**Date:** 2026-07-08
**Purpose:** Single consolidated record of everything discussed, researched, and decided in the project-revival session. The formal pipeline artifacts (design doc, risk register, ADR) are linked in §2; this document is the superset narrative so nothing lives only in a chat log.

---

## 1. Where this started

Dormant 2025 draft-prep codebase (last touched Aug 18, 2025, five days before that year's draft): Python + PostgreSQL, ~5,000 lines. Reviewed and assessed:

**Keep:** Postgres schema + 17 years of imported league draft history (3,782 picks, 244 team-seasons, 2009–2025), Yahoo OAuth plumbing, `league_rules.md` as scoring ground truth.
**Discard:** flat-multiplier scoring adjuster (+15% QB etc. — can't express the actual rules), keyword-counting "sentiment analysis," the RAG-in-name-only system (no embeddings, just SQL keyword matching), mostly-broken RSS feed list.
**Never built in v1:** weekly performance data (draft position was never connected to actual outcomes), injury tracking, any UI beyond CLI.

## 2. Formal artifacts produced this session

| Artifact | Path | Status |
|---|---|---|
| Design doc | `docs/superpowers/specs/2026-07-08-draft-intelligence-design.md` | Committed |
| Risk register (Tier 2; techniques C, A, F, H) | `docs/superpowers/risks/2026-07-08-draft-intelligence-risks.md` | Written — **awaiting your `accept`** |
| ADR (8 domains) | `docs/superpowers/risks/2026-07-08-draft-intelligence-adr.md` | Written — **awaiting your `accept`** |
| This record | `docs/2026-07-08-PROJECT-RECORD.md` | — |

Pipeline per your CLAUDE.md: brainstorm → design doc → risk-analysis → ADR → **writing-plans (next, after you accept)** → implementation.

## 3. Your goals and philosophy (as stated)

1. **Two pieces matter: the draft, then in-season changes.** The draft determines ~65–75% of the season → it gets the focus.
2. **In-season, trades are deprioritized** (people get skittish; hard to execute) but **earmarked** — be aware, maybe do better later.
3. **In-season priority #2: next-man-up detection** — replacement players after injuries, lead backs underperforming with backups poised to explode; across all offensive positions.
4. **Mine the historical data**: how people draft → how seasons went. Themes you expect (anecdotal, want hard data): people draft QBs early; smart systems treat players in **tiers/cohorts** (~6–7 QB cohorts). *You'll feed reference material on cohort groupings later.*
5. **Stand on shoulders**: research who has done agentic/ML fantasy drafting + in-season management well, rather than inventing from scratch. Also **curate experts** who live in fantasy football and consistently find winners/diamonds in the rough.
6. **Education wanted**: when to set lineups, clever start/sit timing, pickup timing before game day to optimize weekly lineups.
7. **Draft-day vision**: an assistant analyzing picks in real time, understanding your roster holes and what's available, making recommendations.
8. **FantasyPros frustration**: used it, feels inadequate, but willing to pay and pull from its API for data/scenarios.
9. **Custom rules complaint**: Yahoo/other sources' recommendations ignore the league's micro-changes; the optimal lineup here differs from generic leagues.
10. **Attitude**: "sophisticated but viable"; happy to provision databases/ML tooling; CLI output is fine; ~6 weeks to draft; wants to work on this **daily**; not in a rush — be thorough.
11. **Later addition — automation idea**: run auto-drafts at scale (your suggestion: via FP/Yahoo APIs), collect data, troubleshoot, build layered confidence, then human-in-the-loop trial drafts. (Modified by research findings — see §7.)
12. **Agentic architecture question**: could we strip agents of deterministic work, add strict input guardrails + output audits/review/confirmation and trust an agent workflow — or keep draft-day deterministic? (Resolved — see §8.)

## 4. League facts (decisions + documents)

- **Target league:** the NAJEE 'LEFT EYE' HARRIS league (2025 ID: 326814) — the league renames itself annually; Yahoo issues a new league ID each season, linked via the API `renew`/`renewed` chain.
- **Same core managers as the 17-year history** except ~1–2 rotations. You joined ~4 years ago; your profile is **"Sports"**. Most managers keep names; 1–2 change annually — anchor on Yahoo manager GUIDs, not display names.
- **12 teams, H2H, no keepers (redraft), snake, 90-second pick clock.** 2025 draft was Aug 23; 2026 expected ~6 weeks from this session (mid-August).
- **Roster:** 2 QB / 3 WR / 2 RB / 1 TE / 1 FLEX (W/R/T) / 1 K / 1 DEF, 8 bench, 1 IR (20 total).
- **Scoring (full detail in `league_rules.md`):** 6-pt passing TDs; ±0.5 completions/incompletions; full PPR; **+1 per rushing/receiving first down**; +0.33/rush attempt; yardage bonuses 100/150/200 (rush & rec) and 300/400/500 (pass); pick-six −4 extra; return yards; enhanced DEF (TFL, 3-and-outs, 4th-down stops, points/yards-allowed tiers); K distance tiers with miss penalties.
- **Transactions:** 65/season, 5/week max, rolling waivers, 1-day waiver period. Playoffs: 6 teams, weeks 15–17.
- **Discrepancy to verify in data audit:** history shows 14–16 teams; current league is 12. Also verify when (if ever historically) the league became 2QB — era segmentation matters (risk R4).

## 5. Decision log (clarifying Q&A)

| Question | Your answer |
|---|---|
| Target league / does history transfer? | NAJEE league; same core managers → tendencies transfer |
| Draft-day pick tracking | Auto-sync from Yahoo API, manual entry as fallback |
| Budget | Have FantasyPros sub; ~$50–100/mo total OK (research showed most needs are free) |
| Daily rhythm | **Automated morning briefing** (cron ingestion + generated report) + CLI tools for deep dives |
| Risk-analysis tier | Delegated to me; you guessed Tier 3 → rubric says **Tier 2** (no autonomous consequential actions by design) |

## 6. Research findings (four parallel deep-dives, verified against live endpoints July 8, 2026)

### 6a. FantasyPros programmatic access
- **Official free public API v2 exists** (newish): `api.fantasypros.com/public/v2/json`, key via `secure.fantasypros.com/api-keys/request/` (free, discretionary approval, not tier-gated). **Limits: 1 call/sec, 100 calls/day, personal use.** Apply day 1.
- Serves: stat-level projections (attempts, completions, yards, TDs, receptions, yardage-bonus buckets; **no first downs, no targets**), ECR with min/max/std, ADP across formats (incl. superflex-relevant types), news, injuries, actual points.
- **Draft Wizard has NO API** — UI only. Practitioners scrape or build their own simulators seeded with FP ADP.
- CSV/XLS export endpoints work with a logged-in session cookie (login-gated, not tier-gated) — automatable fallback.
- Community fallbacks: R `ffpros` scraper (stable for years), `ecrData` JSON blob in rankings pages.

### 6b. Yahoo live-draft feasibility
- **`/league/{key}/draftresults` returns picks DURING a live draft — confirmed** (maintainer docs of `yahoo_fantasy_api` + GitHub issue #27 where mid-draft data crashed a parser: unmade picks arrive without `player_key`). Lag unmeasured anywhere — measure in rehearsal.
- OAuth: access tokens live 1 hour (refresh mid-draft required; wrappers handle it). Rate limits undocumented; **error 999 = lockout ~10–15 min** (fatal mid-draft → poll conservatively at 5–10s, drilled manual fallback).
- Commercial tools (FP, Draft Sharks, RotoWire) all use **browser extensions scraping the draft room**, not the API. Yahoo **mock** drafts are not API-visible → rehearse in a private test league.
- Historical data: weekly player stats per league per season via API; transactions (adds/drops/trades) available; **league IDs change yearly, linked by `renew`/`renewed` fields** — walk the chain to stitch 17 years.
- Yahoo is tightening API access (new application portal) but self-serve personal apps still work across the ecosystem as of mid-2026.

### 6c. ML/agentic landscape (stand-on-shoulders findings)
- **No dominant open-source draft optimizer exists** (best: jjti/ff, 69 stars — projection aggregation → custom scoring → VOR vs dynamic baseline). The gap: no maintained Python library for multi-source aggregation + custom scoring + VBD — we assemble it from proven pieces.
- **The consensus lesson (one production system + every serious hobbyist):** *LLMs must not do the math.* Amazon's production NFL Fantasy AI paper: deterministic tools compute; the LLM gets analyst-style reasoning guidance and explains. Every project that let the model do valuation at decision time failed (hallucinated availability, 10–15s latency, untestable).
- **Methodology stack to implement:** VBD family — VORP (replacement baseline), VOLS, **VONA** (value vs. next-available, needs live availability forecasting); **2QB implication: QB replacement drops from ~QB14 to ~QB20–24**, which is exactly why generic rankings fail this league. **Boris Chen GMM tier clustering** (borischen.co still active; `fftiers` code open source; publishes no 2QB tiers — we re-run his method on superflex ECR). **Monte Carlo pick-availability simulation** (opponents as ADP-noise draws; powers VONA and "will he make it back to me"). Bootstrap season simulation (ffsimulator approach) for evaluating roster construction.
- **Reusable code:** `derekrbreese/fantasy-football-mcp-public` (Yahoo MCP: OAuth + live draft tools solved — fork/reference), `joewlos` MC draft simulator (logistic-regression pick model), R `ffanalytics` (design reference for aggregation + custom scoring), `nflreadpy` (nfl_data_py is deprecated/archived — use nflreadpy).
- **Commercial benchmark:** Draft Sharks "War Room" is the best-in-class custom-scoring drafter (syncs exact league rules, regenerates projections, live 3D value). FP Draft Wizard can't express custom yardage bonuses. Subvertadown = the model to imitate later for DEF/K streaming.

### 6d. Data sources + expert curation
- **Sleeper API is the projection backbone** (free, no auth, undocumented; verified live): per-player projected **first downs (pass/rush/rec)**, completions AND incompletions, rush attempts, targets, punt-return yards, yardage-bucket receptions, plus **pre-computed bonus flags** in historical actuals that map ~1:1 to league rules. Projections are Rotowire-sourced. Risk: undocumented → snapshot daily, validate schema, fail loud.
- **nflverse** weekly player stats (verified download): completions, attempts, rushing/receiving/passing first downs, return yards, snap counts — full historical ground truth for golden tests, backtests, and FD-imputation regressions. Free.
- **ESPN hidden API**: projects completions/incompletions and **threshold-game counts** (100–199, 200+ yd games) — useful because *a mean projection cannot price a nonlinear bonus*; threshold bonuses need distributions.
- **Dead/limited:** numberFire shut down (Feb 2025, folded into FanDuel Research, shallow); FantasyPros/CBS carry no first downs; **4for4 is the only paid source with projected first downs** (optional fallback).
- **Player ID crosswalk:** dynastyprocess / nflverse `ff_playerids` for Sleeper↔FP↔Yahoo↔nflverse mapping (rookies = weakest coverage, highest stakes — manual override table + fail-loud unmatched report).
- **Accuracy-verified expert shortlist** (FantasyPros accuracy contests, 2022–25): Sean Koerner (4× winner), Jeff Ratcliffe (#1 multi-year draft accuracy), Justin Boone (2025 in-season winner — your start/sit education source), Pat Thorman, Draft Sharks trio (English/Smola/Smith), Joe Bond, Tyler Orginski, Dalton Del Don. **Breakout/next-man-up specialists:** JJ Zachariason (late-round values; working podcast RSS), Ben Gretch (Stealing Signals usage analysis; Substack RSS verified), Dwain McFarland (Utilization Report; email-to-RSS bridge needed), Fantasy Points injury desk (Dr. Edwin Porras; podcast RSS verified). **Working aggregation feeds verified:** RotoWire NFL news RSS, FantasyPros NFL news RSS; Rotoworld = scrape.

## 7. Automation ladder (your auto-draft idea, corrected and adopted)

Corrections from research: **neither platform allows drafting via API** (Yahoo API is read-only for drafts; FP has no draft API), and Yahoo mocks aren't API-visible. The volume therefore comes from our own simulator; platforms are for realism and plumbing.

- **Level 0 — Local sim farm** (continuous): Monte Carlo engine, full 12-team × 20-round snake in milliseconds → tens of thousands of drafts nightly; opponents = ADP-noise bots blended with manager-tendency priors from the 17-year history. Strategy knobs gridded (QB timing, tier-break rules, positional caps). Output: win-rate deltas with confidence intervals.
- **Level 0.5 — Backtests** (the non-circular validator): draft 2023–2025 with that year's preseason projections/ADP; score rosters with **actual** results (nflverse) under league scoring. Prevents grading our drafts with the same projections we drafted from.
- **Level 1 — FP Draft Wizard browser mocks**: I drive their simulator UI via browser automation; batches of 5–10/day (independent bot opponents + UI-flow sanity; not 24/7 — anti-bot/ToS risk; keep automation off the API-key account).
- **Level 2 — Yahoo dress rehearsals**: private test league; drill the actual plumbing — OAuth refresh mid-draft, poll lag measurement, forced-999 → manual switchover, crash → resume.
- **Level 3 — You in the loop**: mock drafts with you at the wheel, assistant advising; every human override logged (each is a tool bug or an intuition leak — both valuable).
- **Level 4 — The real draft.**
- Each level gates the next. Nightly sim report is **adversarial**: worst drafts, failure clusters, assumption audits — not a congratulation mirror.

## 8. Architecture (the agentic-vs-deterministic resolution)

Your proposal — strip agents of deterministic work, strict input guardrails, output audits/reviews/confirmation — is adopted as the pattern for everything **upstream** of decisions. The one bright line added:

> **The number that ranks players at pick time is computed by code, never generated by a model.**

Reasoning: guardrails solve *trust*; draft day's binding constraint is *time* (an audit gate that catches a bad output at pick 4.07 has no time to re-run) and *rehearsability* (code can be regression-tested against 50,000 simulated drafts; a model's every utterance cannot).

Reconciliation that gets both: in a 12-team snake you have ~15 minutes between your picks. The deterministic board updates instantly on every pick (always-ready answer); an **agent runs asynchronously between picks** — re-running availability sims, checking breaking news against your next-pick window, annotating top options with reasoning. Advisory only; if unfinished when you're on the clock, the board answer stands alone.

Agent write-path guardrails (morning briefing): agent digests news/expert content → schema-validated structured signals `{player, type, direction, magnitude, source, confidence, evidence_url}` → applied only through **typed, capped adjustments** (±10%/player/day, ±20% cumulative from signals) → **you confirm before adjustments go live** → full provenance log → weekly bias audit.

Deterministic spine (plain Python + SQL, all testable): scoring engine (exact rules, versioned config; threshold bonuses priced on **distributions**, not means; first-down imputation regressions for FD-less sources), valuation (VORP w/ computed 2QB baselines, VONA, GMM tiers), opponent models, Monte Carlo simulator, draft assistant logic.

## 9. My honest assessment (as given when you asked)

**Opportunity — real, and specific:** the edge is not predicting football better; it's that the league's scoring creates systematic mispricing we can compute exactly, against 11 opponents using tools that literally cannot express the rules (~45+ pts/season of invisible first-down value for a volume receiver; completion scoring punishing gunslingers; the 2QB baseline reordering the top 50), plus 17 years of these specific humans' draft behavior.

**Worries, ranked:** (1) projection error dwarfs valuation precision — we shift odds, we don't guarantee outcomes; aggregation + uncertainty bands matter more than decimals. (2) Draft day is a single point of failure on a 90-second clock — rehearsals non-negotiable. (3) Fragile foundations (undocumented Sleeper, scrapers, feed rot) — fail loud, never silently stale. (4) 3,782 picks is small data — descriptive tendencies yes, fitted ML no. (5) Six weeks × infinite feature surface — the board and rehearsed assistant must be *finished*; everything else is expendable.

## 10. Risk register (Tier 2) — top risks

Full table: `docs/superpowers/risks/2026-07-08-draft-intelligence-risks.md`. Highest-scored:

| ID | Risk | L×I |
|---|---|---|
| R2 | Draft-day live-sync meltdown (999 lockout / OAuth expiry / wifi) on the clock | 48 |
| R3 | Scope overrun → assistant unrehearsed (critical path has ~zero slack) | 48 |
| R4 | Historical era drift (14–16 team, likely 1QB history) → simulator's QB-timing conclusions systematically wrong | 42 |
| R1 | Scoring-engine encoding bug silently poisons the entire board (golden tests) | 40 |
| R16 | Scoring methodology error — distribution model, FD imputation, or 2QB baseline algorithmically wrong despite correct encoding (backtests + calibration + divergence checks) | 40 |
| R6 | Player-ID crosswalk errors (worst: 2026 rookies) | 36 |
| R5 | Sleeper endpoint drift/shutoff (silent drift worst case) | 32 |
| R7 | Sim-to-reality transfer failure (bots ≠ these 11 humans) | 30 |

Draft-day SPOFs (fault tree): local machine, home network, single OAuth token, in-memory state — all cheaply hardened (persistence+resume, hotspot, checklist, printed board) in week 4, not draft week.

## 11. ADR — one-line domain summaries

Full doc: `docs/superpowers/risks/2026-07-08-draft-intelligence-adr.md`. (1) Fail-loud, named degraded modes LIVE→POLL-DEGRADED→MANUAL→PAPER, per-pick state persistence. (2) Postgres single source of truth; immutable raw snapshots → recomputable layers; versioned scoring configs; provenance everywhere. (3) Secrets stay in gitignored `.env`/token files (verified clean), pre-commit guard. (4) Outbound-only auth; proactive Yahoo refresh; localhost Postgres. (5) Structured JSON logs; **the morning briefing is the dashboard**. (6) Five data dependencies ranked by replaceability with per-dependency fallbacks + call budgets. (7) Golden tests vs Yahoo official 2025 scores (exact match) anchor correctness; backtests as regression suite; rehearsal drills with written pass criteria. (8) launchd scheduling; git tag per passed rehearsal; `draft-day` tag is the only code that runs on draft day; week-6 feature freeze.

## 12. Build order (~6 weeks)

1. **Wk 1 — Research→infrastructure:** Postgres revival + data audit (renew chain, manager GUIDs, per-season settings incl. when 2QB began); import 2025 season + weekly stats for all seasons (throttled); FP API key application (day 1); Sleeper + nflverse ingestion; ID crosswalk.
2. **Wk 2–3 — Scoring + history:** scoring engine with golden tests; FD imputation; threshold-bonus distributions; valuation layer; historical mining report (your anecdotal themes vs. hard data); briefing v1.
3. **Wk 3–4 — Simulation:** MC simulator, sim farm, backtests, strategy tuning, tiers.
4. **Wk 4–5 — Draft assistant:** CLI, Yahoo polling + manual fallback, async agent lane; ladder Levels 1–3.
5. **Wk 6 — Freeze, rehearse, draft.**

## 13. Open questions / pending inputs

**Pending from you:**
- The QB cohort/tier reference material you mentioned you'd feed in ("what people have done to organize those groupings").
- Actual 2026 draft date when known (assumed ~mid-August).
- `accept` (or edits) on the risk register + ADR to unlock the implementation plan.

**Pending verification (owned by the build, tracked in design §7):**
- Yahoo live-poll lag (measure in Level 2 rehearsal); Sleeper endpoint stability; FP key approval timing; 2026 league settings re-verification at renewal; sourcing 2023–25 preseason projection/ADP archives for backtests; when the league adopted 2QB (era segmentation).

## 13a. Addendum (2026-07-09): league identity RESOLVED by live probe

Pre-execution API probe (Yahoo token refreshed successfully; Sleeper alive with first-down projections) settled the identity question:

- **The target NAJEE league and the imported "LMU Still Undefeated" league are two different, simultaneously active leagues.** LMU 2025 (`461.l.863132`) is 14 teams; NAJEE 2025 (`461.l.326814`) is 12 teams.
- **The NAJEE league renames itself every season and has its own 16-year renew chain (2010–2025, always 12 teams):** NAJEE 'LEFT EYE' HARRIS ← SPEED RASHEE ← DARKNESS RETREAT ← RIDLEY'S LOCKS ← DESHAUN'S MASSEUSE ← KRAFT SERVICES (×2) ← ZAY JONES' HOTEL ROOM ← ZEKE'S PARADE ← Peyton's Grundle Gravy ← GENO'S JAW ← Rice's Elevator Rides ← HERNANDEZ KILLED A GUY ← SARAH JONES HIGH SCHOOL ← ROCK OUT WITH YOUR LOCK OUT ← BEN RAPETHLISBERGER (2010). (The old IMPORT_SUMMARY misread these annual names as separate leagues.)
- **Consequence:** the 3,782 imported picks describe the LMU league, not the target. The correct history — the NAJEE chain — was never imported. Phase 1 Task 9 now imports all 16 NAJEE drafts (cheap: 1–2 API calls/season); LMU data is retained but demoted to secondary reference. Neither 2026 league exists yet (`renewed=''`), so 2026 settings verification (R8) waits for renewal.
- **Scope addition (user, 2026-07-09): full season time-series per NAJEE season.** Task 9 gained an `--outcomes` importer: final standings, weekly team scoreboards, and the complete transaction log (adds/drops/trades) for all 16 seasons (~20 calls/season). This connects "what left the draft" to "what finished the year" — champions' draft-vs-waiver value split, transaction volume vs finish, actual trade frequency/timing in this league. Weekly *lineups* deliberately deferred (~3,300 calls; rosters reconstructable from draft + transactions). A parallel deep-research task on evidence-based in-season management best practices (draft-vs-waiver studies, waiver/streaming strategy, trade norms, accuracy-verified analysts' weekly processes) is running; findings feed the in-season module design.

## 13b. Addendum (2026-07-09): Phase 1 executed and merged

Phase 1 (Foundation & Data Revival) executed via subagent-driven development — 12 tasks, each implemented + review-gated, final whole-branch review "Ready to merge," fast-forward merged to `main` at `f10de19`. 24/24 tests; 12/12 exit criteria.

**Data now in Postgres:** all 16 NAJEE seasons' drafts (3,720 picks) + standings + weekly scoreboards (261 weeks) + full transaction log (6,550); 2025 weekly player stats (3,876 rows); Sleeper projection snapshots (2025 + 2026 — 2026 already live); nflverse actuals 2019–2025 (129,809 player-weeks incl. first downs); ID crosswalk (94.2% coverage of fantasy-relevant players).

**Facts established:** the NAJEE league has been 12-team/2QB for its entire 16-season life (no era boundary — R4 largely dissolved; all draft history format-comparable). Yahoo redacts manager GUIDs; identity keys on stable team slots (12/12 slots present all 16 seasons); human turnover within slots is invisible to the API and needs the user's annotation (Phase 2). nflreadpy column names corrected vs plan (passing_interceptions; per-type fumbles).

**Phase 2 carry-forward (from reviews):** FIRST commit = `ffi.ids` + Yahoo throttle-wrapper consolidation; DEF team-abbr mapping; 2025-rookie crosswalk manual overrides (null yahoo_id in ff_playerids — R6 as predicted); 85 legacy slug-row cleanup; team-abbr case normalization (Buf/BUF); manager slot-turnover annotation table; crosswalk dup-yahoo_id join guard; per-position FD validation in scoring-engine input checks.

**Pending user inputs:** FP API key approval (applied 2026-07-09); manager slot-turnover annotation; QB cohort reference material; 2026 draft date; 2026 league renewal (neither league renewed yet — R8 gate waits).

## 14. Current status

Design doc committed. Risk register + ADR reviewed and **accepted with fixes applied** (2026-07-09): R16 methodology-error risk added; R4 impact raised to I6; Domain 1 poll thresholds tightened for the 90-second clock (1 failure → POLL-DEGRADED, 999 → immediate MANUAL); Domain 2 migration path decided (named Postgres schemas; `public` = core layer, crosswalk in `public`); Domain 6 committed to `uv`. Reviewer's nice-to-fixes (agent-latency risk, browser-profile gitignore, sim-log volume policy, scoring purity test, restore test at week 3, ADR preamble) deferred to the implementation plan. Verified codebase-context gaps for the plan to front-load: legacy import created placeholder players (`Player {key}`, TBD pos/team); legacy stat map covers only 11 basic stat IDs (none of the exotic scoring); legacy assistant/adjuster hardcode 14 teams — effectively all v1 analytical code is dead. Next: `superpowers:writing-plans` → implementation plan → week-1 infrastructure revival.

## 13c. Addendum (2026-07-10): Phase 2 executed and merged

Phase 2 (Scoring Engine, Valuation & Historical Mining) executed subagent-driven — 16 tasks, per-task review gates, final whole-branch review "ready to merge with fixes" (applied), fast-forward merged to `main` at `e8975eb`. 158/158 tests; health gate extended to 20/20.

**Golden gate (R1):** engine reproduces Yahoo's official 2025 points EXACTLY — 4,658/4,658 player-weeks (incl. backfilled full DEF/K pools), 39 committed fixtures, one evidence-pinned payload-gap exception (Aubrey wk15 fake-FG rush: Yahoo's position-scoped K payload omits cross-category stats; diff = exactly 1.93, nflverse-verified; pin fails loud on drift). Cumulative bonus stacking verified empirically before any code. Week-3 checkpoint (R3) satisfied at Task 5.

**R16 catches (the methodology risk paid for itself):**
- **Sleeper native FD projections are ~2x inflated** vs nflverse ground truth (impossible fd>volume in 53–96% of pairs). Design amendment: imputed FD (nflverse-fitted, EB-shrunk) is THE FD source for all projection scoring; native FD rejected with evidence, downgraded to monitored-only at ingest (volume keys + population floors are the hard guard).
- **Gamma bonus pricing calibrated:** Brier 0.0212 vs 0.0259 mean-pricing on 48k out-of-sample obs.
- **QB-hoarding sensitivity is LOAD-BEARING:** QB baseline 476 pts @QB24 vs 73 @QB36 (pool cliffs past real starters); top-24 board overlap only 10/24 between no-hoard and hoard scenarios (hoard_12 vs 24 robust at 24/24). Phase 3 simulator must adjudicate the real QB policy; QB-cohort input elevated.

**Strategic verdicts (docs/research/):** DEF **DRAFT EARLY** (+6.96 pts/wk over realistic streamer under enhanced DEF scoring ≈ +97.5/season); K **DRAFT EARLY** (+4.07 ≈ +57/season) — inverts generic streaming wisdom (1 season, hindsight-upper-bound caveats stated). Mining (16 seasons): true draft position worth only ~0.3 avg-finish ranks, but FRANCHISE SLOT spread is 3.24 ranks (persistent manager skill — opponent-model gold); QB1 goes round 1.83 league-wide; champions ~3.3k drafted + ~1.1k added pts (2019–25).

**Constraints discovered:** FP public key tier hard-caps responses at 10 players/position → FP = elite overlay only (~9/30 daily calls used); ADR D6 page-export fallback is the path if full ECR ever needed. Sleeper DST projection tier semantics deferred (DEF prices ~0 from Sleeper) — carry-forward.

**Infrastructure live:** morning launchd chain (backup → sleeper ingest → FP sync → score → valuation → briefing), health-first briefing exits nonzero on red; pg_restore drill executed (2.17s, 6/6 counts). Data: draft_picks fully team-attributed (3,720), matchup_results 2,994 team-weeks, crosswalk 97.6% (101 rookie overrides).

**Phase 3 carry-forward:** Sleeper DST tier-semantics task; RequestException→YahooAuthError test; jsonb-canonical compare + RangeTier order validator before any config v2; fixture ORDER BY tiebreaker at regeneration; gmm guard-branch tests; PG_BIN eval alignment next drill. Pending user inputs unchanged: slot-turnover annotation (now higher-value given the franchise-slot finding), QB cohort material (elevated by baseline sensitivity), 2026 draft date, league renewal (R8 re-audit trigger armed).
