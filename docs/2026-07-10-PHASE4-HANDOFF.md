# Phase 4 Handoff — Draft Assistant, Opponent Calibration & Rehearsal Ladder
**Written 2026-07-10, same day Phase 3 merged to `main` (3c53d2a; post-merge commits through d6715bc). Audience: the session (or agent) that starts Phase 4.**

## 0. Read these first, in order

1. `docs/2026-07-08-PROJECT-RECORD.md` — §13a–13d are mandatory (league identity; Phases 1–3 outcomes). §13d = Phase 3.
2. `docs/superpowers/specs/2026-07-08-draft-intelligence-design.md` — §4.6 (draft-day assistant CLI — the core deliverable), §4.5 (ladder Levels 1–3 are THIS phase), §4.7 (briefing signal lane — FP news wiring lands here), §5 (build order wk 4–5).
3. `docs/superpowers/risks/2026-07-08-draft-intelligence-risks.md` — **R2 (draft-day meltdown) and R3 (scope/rehearsal runway) are Phase 4's own risks**; R7 residual (see §2 item 1 below).
4. `docs/superpowers/risks/2026-07-08-draft-intelligence-adr.md` — Domain 1 (LIVE→POLL-DEGRADED→MANUAL→PAPER state machine, poll thresholds: 1 failure → POLL-DEGRADED, 999 → immediate MANUAL), Domain 7 (rehearsal drills as acceptance tests with written pass criteria), Domain 8 (git tag per passed rehearsal; `draft-day` tag; freeze).
5. `docs/research/2026-07-10-strategy-conclusions.md` — the policies the assistant implements (2-QB core, DEF/K late 14–18, no tier-break bonus) and their caveats. **Read the caveats — they define Phase 4 work.**
6. `.superpowers/sdd/phase3-progress.md` — Phase 3 execution ledger (per-task outcomes + ~20 accepted Minors, some of which are Phase 4 fodder).

## 1. Process contract (same discipline as Phases 1–3)

- Phase 4 starts with **superpowers:writing-plans** (new plan under `docs/superpowers/plans/`), then subagent-driven execution with per-task review gates. No code before the plan.
- Work on a branch; merge via finishing-a-development-branch. Fail-loud binds all new code (invoke fail-loud-error-handling for try/except).
- **ADR D7 gate is LIVE and mandatory**: any strategy/valuation change must pass `uv run python scripts/run_backtests.py --gate` (reference: composite 0.5297, band 0.0196, active row in `sim.backtest_reference`). Gate runs persist nothing; only `--reference` writes.
- Extend `scripts/phase1_report.py` (now 26 checks; never replace).
- Deterministic spine: no model calls in the sim/board/assistant pick path. The async agent lane (design §4.6) is advisory-only and never blocks the board.
- **Near-zero Yahoo API budget until the rehearsal tasks** — then it's deliberate, drilled usage via `ffi.yahoo_client.yahoo_call` (2s throttle; 999 = stop everything).

## 2. Phase 4 scope, in priority order (calibration BEFORE assistant conclusions are trusted)

1. **Opponent QB-timing calibration** (R7 residual — first, it gates everything downstream): sim opponents currently take QB1 at mean round ~2.84 vs the room's historical **1.83** (`qb_timing_by_slot`). Measured cause: the pick model's within-position ADP softmax leans on national 2QB ADP; the room is more QB-early than that market. QB runs are STRUCTURAL, not contagion (16-season permutation test: 3+ QB streaks 46 obs vs 44.9±4.5 null, p≈0.45 — do NOT build a contagion mechanism). Fix shape: tune the priors-vs-ADP blend (or add a per-position prior weight) until simulated QB1/QB2/QB3 round distributions match `qb_timing_by_slot` per slot; make the sim-farm assumption audit (currently WARN on a biased 198-draft sample) a proper uniform-sample regression check. This is a strategy-adjacent change → D7 gate + re-run the farm + re-verify the QB-timing conclusion (the "wait on QB" finding is currently OPTIMISTIC — flagged in the conclusions doc).
2. **Tier-target QB strategy knob** (user request, Boris Chen cohorts confirmed): extend `StrategyParams` with tier-based QB targeting (e.g. QB1 from tier ≤2, QB2 from tier ≤2–3) as an alternative to round-based plans; add to the farm grid; compare against round plans. Context: 2026 tiers (hoard_12) = tier 1 is Josh Allen alone (ADP 1.2); tier 2 is 11 deep with ADP 3.5→44 (Lawrence 32, Nix 33, Dak 44).
3. **Strategy polish**: default caps → K:1, DEF:1 (demo draft took a second kicker at R19 — rational under raw VORP, silly in practice); consider a bench-value discount for late-round onesie positions. D7 gate applies.
4. **VONA / availability layer**: P(player X survives to our next pick) from the calibrated opponent model — powers "last tier-2 QB" decisions. Design §4.3/§4.6.
5. **Draft assistant CLI** (design §4.6): precomputed board + roster-need logic + live VONA; Yahoo `draftresults` polling 5–10s with diff detection (unmade picks arrive without `player_key`); proactive OAuth refresh; mode state machine per ADR Domain 1; per-pick state persistence + resume; single-keystroke manual entry mode.
6. **Rehearsal ladder Levels 1–3** (each gates the next; written pass criteria per ADR D7):
   - Level 1: FP Draft Wizard browser mocks (5–10/day, human-paced; keep automation off the API-key account — R13).
   - Level 2: **private Yahoo test league** (user explicitly wants this — his own idea): create league, schedule draft with 11 autodraft bots; drill poll lag measurement, mid-draft token refresh, forced-999 → manual switchover <30s, crash → resume. Yahoo has NO draft-submission API and mocks are not API-visible — the private league is the only live-plumbing rehearsal venue.
   - Level 3: user-in-loop mocks with the assistant advising; log every human override.
7. **FP news → briefing signal lane** (design §4.7): live-verified 2026-07-10 — `GET /news` works on our key; returns items {title, desc, **impact**, player_id, team_id, categories, link}; public tier caps items (asked 5, got 3; `public_api_limited: true`). Full articles are NOT in the API (items link to pages). Budget: ~8/30 calls used daily. Signals apply only via typed, capped adjustments (±10%/day, ±20% cumulative, human-confirmed).
8. **Small debt batch** (fold where convenient, from Phase 3 ledger): `load_backtest_pool` ORDER BY; `data_vintage` per-position degraded fractions (bool_or over-flags); farm `git_sha` records parent commit on run-before-commit; PG_BIN eval alignment at next restore drill.

## 3. Timeline (user-confirmed 2026-07-10)

- **Draft: last weekend of August (Aug 29–30, 2026).** Assumed until Yahoo renewal confirms.
- **Feature freeze ≈ Aug 22** (ADR D8: final week = reverts only; last passed rehearsal tagged; `draft-day` tag is the only code that runs on draft day).
- ~6 weeks of runway from this handoff → Phase 4 has weeks, not days, of slack vs the original mid-Aug assumption. R3 discipline still applies: the assistant and rehearsals are the critical path; everything else is expendable.

## 4. Carry-forward facts (verified this phase — do NOT re-derive, trust these over older docs)

- **The draft is 19 rounds / 228 picks** (11 starters + 8 bench; IR not drafted). Design docs saying 20 are wrong.
- **Sleeper ADP field is `adp_2qb`** (999 = undrafted sentinel → None); ~263 skill players carry real values; **K/DEF have NO ADP in any format**. `adp_dd_ppr` does not exist in payloads.
- **Sleeper DST `pts_allow_*`/`yds_allow_*` keys are constant-1.0 placeholders** — DEF tier points priced via flat fitted uplift (9.66/wk); DEF cross-team signal = 4 counting stats only (Spearman 0.572 vs 2025). DEF differentiation is the valuation chain's softest link.
- **Season projections use weekly gamma bonus EV** (`ffi/scoring/projection_bonus.py`, `components->>'bonus_model'='weekly_gamma_v1'`); this LOWERED mean bonus components vs one-shot (one-shot overpaid marginal players). Weekly-actuals golden path untouched.
- Crosswalk kickers are `'PK'`; valuation normalizes to `'K'`. DEF xwalk rows exist (sleeper_id = team abbr; Rams = 'LAR' not 'LA').
- **Sim interfaces**: `build_pool(conn, scenario)`, `build_slot_priors(conn)` (annotation-aware; slot-12 floor = **2021**, user-confirmed; manager nicknames UNHIDDEN in payloads 2021+ and identical across 2021–25 → no human turnover in the recency window), `make_strategy_fn(StrategyParams)` (knobs: scenario, qb_by_round, **qb_not_before** — the knob that actually delays QBs; deadlines never bind because top-25 VORP is all QBs under hoard_12), `run_draft(pool, priors, fn, seed, our_franchise_slot=12, our_position=None)`, `evaluate_league(rosters, cv, seed, n_seasons, points_lookup=None)`. `snake_position(228) == (19, 12)`.
- **Farm caveats (binding on any evidence use)**: absolute all-play levels inflated (farm 0.64–0.73 vs backtest 0.53) — cite cross-cell deltas only; `top3_rate` saturated ~0.994 — never cite. Nightly `com.ffi.simfarm` (02:30) writes `reports/sim-farm-<date>.md` with data-vintage header; farm refuses valuation/ADP snapshot mismatch and >36h staleness.
- **Backtest limits**: DEF zeroed; K borrowed from 2026 pool; 2024 non-QB + 2025 K synthetic (degraded-flagged); Wayback superflex ADP never archived.
- **Team-change residual study (2026-07-10, d6715bc): situational context is PRICED by preseason ECR** — all four positions' changer-vs-stayer CIs include zero. Coach/team mean-adjustments are DEAD unless new evidence beats that study. Open situational angles (post-draft candidates, not Phase 4 critical path): scheme-rate FD imputation; variance-widening on situation change.
- **QB cohort source = Boris Chen (user-confirmed).** His GMM method already runs on our league-adjusted values; he publishes no 2QB tiers.
- 2026 draft kits are live (FP cheat sheet + superflex tiers, ESPN, etc.) — useful as market-comparison color; the board is the arbiter.

## 5. Pending user inputs

1. **2026 league renewal** — R8 trigger still armed: when `renewed` ≠ '' on the 2025 league, run `scripts/audit_league_history.py` from the new key and diff settings BEFORE trusting the board (annual rule tweaks happen).
2. Draft date confirmation when the league schedules it (assumed Aug 29–30).
3. Slot-turnover annotation for pre-2021 seasons — nice-to-have only now (2021+ verified stable via unhidden nicknames); recency weighting discounts pre-2021 anyway.

## 6. Environment facts & gotchas

- Stack: uv only; Postgres 15 (brew); test DB self-bootstraps (conftest runs schema + ALL migrations sorted; teardown now truncates `manager_slot_annotations` and `leagues` CASCADE too). **314 tests green**; health gate `uv run python scripts/phase1_report.py` = 26/26.
- Two launchd jobs: `com.ffi.morning` 07:00 (backup → ingest → FP → score → valuation → briefing) and `com.ffi.simfarm` 02:30 (farm → report). Don't double-run FP sync (check `fp_calls_today`, budget 30).
- `main` is pushed to origin (github.com/brent-bartosch/fantasy-football-intelligence) through d6715bc. v1-era docs archived under `docs/archive/v1-2025/`.
- python-dotenv's `load_dotenv()` breaks under `python << EOF` stdin (frame inspection) — run scripts from files.
- `.superpowers/sdd/` is gitignored scratch: task briefs/reports/review packages live there; Phase 4 should use its own ledger file (`phase4-progress.md`).
- The demo-draft script pattern lives at the scratchpad from this session; nothing draft-day-critical is uncommitted.
