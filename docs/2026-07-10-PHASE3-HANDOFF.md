# Phase 3 Handoff — Monte Carlo Simulator, Opponent Models & Backtests
**Written 2026-07-10, immediately after Phase 2 merged to `main` (afd960a). Audience: the session (or agent) that starts Phase 3.**

## 0. Read these first, in order

1. `docs/2026-07-08-PROJECT-RECORD.md` — **§13a/13b/13c are mandatory** (league identity; Phase 1 data; Phase 2 outcomes, R16 catches, and strategic verdicts).
2. `docs/superpowers/specs/2026-07-08-draft-intelligence-design.md` — §4.4 (opponent & manager models), §4.5 (simulator + testing ladder Levels 0/0.5 — Levels 1–3 are Phase 4), §5 (build order wk 3–4), §7.5 (backtest-archive sourcing candidates).
3. `docs/superpowers/risks/2026-07-08-draft-intelligence-risks.md` — **R7** (sim-to-reality transfer — Phase 3 owns its mitigations), **R11** (backtest inputs unobtainable — sourcing attempt due this phase), R3 (critical path), R4 residual.
4. `docs/superpowers/risks/2026-07-08-draft-intelligence-adr.md` — Domain 7 (backtests as regression suite; property tests), Domain 5 (sim-farm nightly adversarial report carries its own data-vintage line), Domain 8 (sim-log volume policy: results → `sim` schema tables, logs errors-only).
5. `docs/research/2026-07-09-historical-mining-report.md` — the opponent-model priors live here (franchise-slot tendencies, QB timing by slot, transaction/trade patterns).
6. `docs/research/2026-07-09-baseline-sensitivity.md` + `2026-07-09-def-k-streaming-baseline.md` — the two strategy questions FROM RESEARCH the simulator must adjudicate (QB policy; DEF/K draft-early verdicts). §3.3's knob grid adds two simulator-native knobs on top (tier-break rules, positional caps) — nothing is missing.
7. `.superpowers/sdd/phase2-progress.md` — Phase 2 execution ledger (per-task outcomes, deferred Minors, final-review triage).

## 1. Process contract

- Phase 3 starts with **superpowers:writing-plans** (new plan under `docs/superpowers/plans/`), then subagent-driven execution with per-task review gates — same discipline as Phases 1–2. No code before the plan.
- Work on a branch; merge via finishing-a-development-branch. Fail-loud rules bind all new code (invoke fail-loud-error-handling for try/except).
- **The simulator's core loop is deterministic-spine code** (design §3 bright line): strategy logic and pick models are plain Python, seeded/reproducible; no model calls anywhere in the sim path.
- Extend `scripts/phase1_report.py` with Phase 3 checks (now 20 checks; never replace).
- ADR D7 gate: **any strategy/valuation change must not degrade the 2023–25 backtest composite beyond noise bands** — the backtest harness is Phase 3's own regression suite; build it early so later tasks run under it.

## 2. Mandated early items (final-review conditions + structural)

Fold into the plan's first tasks:
- **Sleeper DST projection tier semantics** (T7 carry-forward, Important): DEF records price ~0 in `scoring.projection_points` — the simulator/board cannot value DEF from Sleeper until the DST stat keys (`pts_allow_0`, `yds_allow_0_100`, …) are live-verified and mapped. Needs its own verification task; a wrong guess silently corrupts DST scores.
- **Config-v2 preconditions** (only if any scoring-config change happens): canonical-JSON compare in `ensure_config_in_db` + ascending-order validator on `RangeTier` lists. Do NOT bump the config otherwise.
- Small debt batch (fold where convenient): RequestException→YahooAuthError unit test; `make_golden_fixtures.py` ORDER BY tiebreaker (before any fixture regeneration); gmm `<4-values`/unsorted-pool guard tests; PG_BIN eval alignment in the restore runbook at next drill.

## 3. Phase 3 scope (design §5, weeks 3–4)

1. **Opponent & manager models** (`sim` schema): per-slot tendency priors from the mining functions (`ffi/history/mining.py`: `position_round_tendencies`, `qb_timing_by_slot` — reuse, don't rewrite); softmax/ADP-noise pick models blended with slot priors. **Slot ≠ human** — weight recent seasons; the user's turnover annotation (pending input #1) upgrades priors when it arrives.
2. **Monte Carlo draft simulator**: full 12-team × 20-round snake in milliseconds; our seat driven by strategy-parameterized board logic (valuation tables + tiers from Phase 2); opponents from the pick models. Seeded, reproducible, property-tested (every sim produces legal rosters: 2QB/2RB/3WR/1TE/1FLEX/1K/1DEF + 8 bench).
3. **Sim farm (ladder Level 0)**: thousands of drafts nightly via launchd; strategy knobs gridded — **QB timing is the headline knob** (the hoarding-sensitivity finding means the simulator, not VORP, decides QB policy); also tier-break rules, positional caps, DEF/K draft-early-vs-late (test the Phase 2 verdicts in context). Output: win-rate deltas with confidence intervals via roster season-scoring. Nightly report is **adversarial** (worst drafts, failure clusters, assumption audits) and carries a data-vintage line.
4. **Backtests (ladder Level 0.5, R11)**: draft 2023–2025 with that year's preseason projections/ADP; score rosters with actual results (`scoring.player_week_points`, source='nflverse') under league scoring. **Archive sourcing attempt is due**: candidates = dynastyprocess ADP archive, ffanalytics archives, Wayback FP pages (design §7.5); degrade to cross-source holdout validation if all fail — document the outcome either way.
5. **Strategy conclusions report** (user-facing deliverable): QB-timing policy with evidence, DEF/K policy confirmed-or-revised, tier-break rules, and the sim-vs-backtest agreement check (R7's earliest signal).

## 4. Carry-forward facts Phase 3 builds on (do NOT re-derive)

- **Valuation is live**: `valuation.player_value` (scenarios qb_hoard_0/12/24) + `valuation.replacement_baseline`; GMM tiers per position; rebuild via `scripts/build_valuation.py` (idempotent per snapshot). The hoarding scenarios are INPUTS to the simulator, not answers.
- **Imputed FD is the universal projection FD source** (Sleeper native FD ~2x inflated — never map it back). `ffi/scoring/fd_impute.py`; wired in `scripts/score_sleeper_projections.py`.
- **Bonus pricing** (`ffi/scoring/bonus_pricing.py`, gamma, calibrated) exists but is NOT yet wired into valuation points — a candidate Phase 3 refinement: season-total scoring understates weekly threshold-bonus EV; decide deliberately whether sim roster-scoring uses it. Known gap to fix FIRST if wired in: `weekly_threshold_prob` short-circuits on mean≤0 before validating cv (Phase 2 Task 9 accepted Minor) — harmless while callers pre-filter via SQL `HAVING avg > 0`, load-bearing the moment the sim calls it with arbitrary inputs.
- **Historical league-scoring points**: `scoring.player_week_points` — nflverse 2019–2025 (129,657) for backtests/roster-scoring; yahoo_engine 2025 (4,658, exact) as ground truth. Known gaps: pick-sixes absent from nflverse (−4, rare); one pinned Yahoo payload-gap (Aubrey wk15, diff exactly 1.93).
- **History tables**: `teams` (192, slot+team_key), `draft_picks` fully team-attributed, `public.matchup_results` (2,994; playoff weeks legitimately shrink — 33 accepted shortfall weeks), `manager_slot_annotations` (only slot 12 seeded).
- **Mining findings for opponent models**: franchise-slot skill spread 3.24 avg-finish ranks (vs ~0.3 for true draft position); QB1 goes round ~1.83 league-wide with slot-level variance in QB2/QB3 timing; trades rare (~/season count in report); transaction timing curves in the report.

## 5. Pending user inputs (ask early; none block plan-writing)

1. **Manager slot-turnover annotation** — elevated: slot-skill is the strongest signal found; unannotated slots dilute opponent priors.
2. **QB cohort reference material** — elevated: QB policy is the simulator's headline question.
3. **2026 draft date** (assumed ~mid-August).
4. **2026 league renewal** — R8 trigger armed: when `renewed` ≠ '', run `scripts/audit_league_history.py` from the new key and diff settings before strategy conclusions.

## 6. Environment facts & gotchas (hard-won, don't re-derive)

- **Stack:** uv only; Postgres 15 via brew; test DB self-bootstraps (conftest runs ALL migrations/*.sql sorted). 158 tests green; health gate `uv run python scripts/phase1_report.py` = 20/20.
- **Morning launchd chain runs daily 07:00** (`com.ffi.morning`): backup → sleeper ingest → FP daily sync (~7 calls) → score → valuation → briefing (exit-nonzero on red = correct). Reports to `reports/` (gitignored). Don't double-run the FP sync manually on the same day without checking `fp_calls_today` (budget 30).
- **FP public key tier caps every response at 10 players** (limit param ignored — probed). FP ADP is therefore useless for opponent models; **Sleeper snapshots already carry full-coverage ADP fields** (`adp_dd_ppr`, `pos_adp_dd_ppr` in every record) — the natural free ADP source for pick models; validate before trusting (same skepticism that caught the FD inflation).
- **Yahoo:** all calls via `ffi.yahoo_client.yahoo_call` (2s throttle); 999 = YahooRateLimitError = stop everything. Phase 3 needs near-zero Yahoo API work — keep it that way.
- **Sleeper ingest guard**: volume keys (pass_cmp/rush_att/rec) are the hard gate with per-position population floors {QB60/RB120/WR150/TE60}; FD presence is monitored-only. Off-season payloads have ~75–85% ADP-only placeholder records per position — expected, filtered by "meaningfully-projected" logic.
- **`players.yahoo_player_id` is one row per game-code** — always join via `public.v_player_yahoo_ids`; crosswalk 97.6% with manual-override precedence + loud quarantine for upstream dup-ids.
- **`manager_slot_annotations` is excluded from the test-teardown sweep** (Phase 2 Task 3 accepted Minor — the migration seeds it idempotently). Phase 3 touches this table (pending input #1 lands here): the first test that WRITES to it must add it to conftest's teardown or tests will leak state.
- **Sim outputs go to the `sim` schema** (exists since migration 001, empty); per ADR D8: results in tables, logs errors-only (sim farms generate volume).
- **Backups nightly via the launchd chain** (gzipped plain SQL, `PG_BIN` v15 pin — PATH has v14); restore drill proven 2.17s (`docs/runbooks/pg-restore-drill.md`).
- **Week-6 feature freeze stands** (~1 week before the mid-August draft): Phase 3 has weeks 3–4; Phase 4 (assistant + rehearsal ladder 1–3) needs weeks 4–5 — do not let sim-farm tuning eat the assistant's runway (R3).
