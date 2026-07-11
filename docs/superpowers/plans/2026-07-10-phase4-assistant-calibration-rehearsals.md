# Phase 4 — Draft Assistant, Opponent Calibration & Rehearsal Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calibrate the sim's opponent QB timing to the room's measured behavior, then build and rehearse the deterministic draft-day assistant (Yahoo polling + mode state machine + VONA), with the FP news → capped-signal lane and the rehearsal ladder Levels 1–3 gating draft day.

**Architecture:** Everything on the pick path stays deterministic (plain Python + Postgres — the board number is computed by code, never a model). The opponent model gains a calibrated, roster-state-conditioned prior scale (measured against `qb_timing_by_slot`); the assistant is a thin foreground CLI over injectable components (poller, mode machine, event log, recommender) so every draft-day behavior is drillable offline. Signals apply to the board only through typed, capped, human-confirmed adjustments in the `signals` schema.

**Tech Stack:** Python 3.12 via `uv`, Postgres 15 (schemas `raw/scoring/valuation/signals/sim/draft`), numpy, pytest, yahoo_fantasy_api via `ffi.yahoo_client.yahoo_call`, FantasyPros public v2 via `ffi.ingest.fantasypros.FpClient`.

## Global Constraints

- **Deterministic spine:** no model calls in the sim/board/assistant pick path. The async agent lane (Task 16) is advisory-only and must never block or gate the board.
- **Fail-loud everywhere** (user CLAUDE.md + ADR Domain 1): invoke `fail-loud-error-handling` for any try/except. Draft-day degraded modes are named and announced, never silent. ADR D1 thresholds verbatim: **one poll failure → POLL-DEGRADED (immediately); two consecutive failures → MANUAL; one error 999 → immediate MANUAL, no retry** (999 = 10–15 min lockout). All mode transitions logged. Per-pick state persisted; startup replays it.
- **ADR D7 gate is LIVE and mandatory:** any strategy/valuation change runs `uv run python scripts/run_backtests.py --gate` (active reference: composite 0.5297, band 0.0196). Gate runs persist nothing; only `--reference` writes. **The band is season-dominated** (2024 cells 0.55–0.58 vs 2023 0.48–0.51): on a gate failure, check per-season deltas before concluding degradation. Rebuilding the reference after a legitimate calibration correction requires a written justification (Task 4 defines the protocol).
- **Extend `scripts/phase1_report.py` (26 checks today), never replace.** Checks are `(label, sql)` tuples returning one boolean.
- **Yahoo budget near zero until Task 17's drills**: all Yahoo access via `ffi.yahoo_client.yahoo_call` (2s throttle, no retries; 999 raises `YahooRateLimitError`). No bulk Yahoo jobs within 24h of any rehearsal.
- **FP budget:** 30 calls/day (`FpClient` aborts via `FpBudgetExceededError`); the morning job uses ~7–9. The news lane (Task 14) must check `fp_calls_today` headroom before calling.
- **Farm evidence rules:** cite cross-cell deltas only (absolute all-play% is MC-inflated: farm 0.64–0.73 vs backtest ~0.53); never cite `top3_rate` (saturated ~0.994).
- Stack: `uv` only; tests `uv run pytest` (314 green at plan time); health gate `uv run python scripts/phase1_report.py` (26/26 OK, exit 0).
- Work on branch `phase4-assistant`; per-task commits; execution ledger at `.superpowers/sdd/phase4-progress.md` (gitignored scratch).
- Timeline (user-confirmed): draft assumed **Aug 29–30, 2026**; feature freeze ≈ **Aug 22** (ADR D8: final week reverts only; `rehearsal-N` tag per passed drill; `draft-day` tag is the only code that runs on draft day). R3 discipline: the assistant and rehearsals are the critical path; Tasks 16 is expendable.
- Reference file:line facts below verified 2026-07-10 against `main` @ `81b0dfb`.

## Facts pinned during planning (do NOT re-derive; trust these over the handoff where they conflict)

1. **The draft is 19 rounds / 228 picks** (`ffi/sim/draft.py:58-60`: `TEAMS=12, ROUNDS=19, TOTAL_PICKS=228`); `snake_position(228) == (19, 12)`. Design docs saying 20 rounds are wrong.
2. **Health gate has 26 checks** (verified by running it), not 28. Append to the module-level `CHECKS` list of `(label, sql)` tuples in `scripts/phase1_report.py:5-114`.
3. **Opponent model** (`src/ffi/sim/opponent.py`): Stage 1 chooses position from `priors.pos_share[(slot, round_)]` × feasibility mask × `ROSTER_DAMP` (`:43-48`) — **ADP never enters Stage 1**. Stage 2 is a softmax over within-position ADP rank: `logits = exp(-arange(n)/TAU)` over `avail_by_pos[pos][:CAND_WINDOW]` with `TAU=1.8`, `CAND_WINDOW=12` (`:35-36`, `:124-126`). `STARTERS = {"QB":2,"RB":2,"WR":3,"TE":1,"K":1,"DEF":1}` (+1 FLEX RB/WR/TE).
4. **`StrategyParams`** (`src/ffi/sim/strategy.py:86-93`, frozen): `scenario="qb_hoard_12"`, `qb_by_round=(2,5,9)`, `defk_round=14`, `caps=(("QB",4),("RB",9),("WR",9),("TE",3),("K",2),("DEF",2))`, `tier_break_bonus=0.0`, `qb_not_before=(1,1,1)`. Rule order: feasibility force → QB deadline force → DEF/K force → argmax `vorp + tier_break_bonus*is_last_in_tier` with ADP-then-name tiebreak (`_pick_best`, `:124-132`).
5. **`run_draft(pool, priors, our_pick_fn, seed, our_franchise_slot=12, our_position=None) -> DraftResult`** (`draft.py:146`); one `np.random.default_rng(seed)` drives the slot permutation and every opponent pick; `DraftResult.picks` dicts are `{overall, position_slot, franchise_slot, pos, ref, name}`.
6. **`qb_timing_by_slot(conn) -> list[dict]`** at `src/ffi/history/mining.py:231` returns per-slot `{slot, qb1_round, qb2_round, qb3_round, seasons}` (REUSE, never rewrite). League-wide historical QB1 mean = **1.83**; sim opponents currently ~**2.84**.
7. **Assumption audit** = `_assumption_audit` in `scripts/sim_report.py:284` with `HISTORICAL_QB1_ROUND=1.83` (`:35`), `QB1_TOLERANCE=0.5`; it reads `sim.sample_drafts` — 66 cells × 3 stored drafts (worst/best/random) = 198, an **outcome-biased, non-uniform sample**. Task 4 replaces this with a uniform-sample check.
8. **Farm** (`scripts/run_sim_farm.py`): `build_grid` (`:90`) = 48 main cells (6 `QB_PLANS` × `DEFK_ROUNDS=[8,11,14,18]` × `TIER_BREAK=[0.0,8.0]`, scenario qb_hoard_12) + 18 qb_subgrid (6 plans × 3 scenarios, defk 14); `N_DRAFTS_PER_CELL=200`; seed via `derive_seed(base_seed, cell_idx, draft_idx)` (`:143`); `persist_cell` (`:373`) writes `sim.batches` (has `opponent_params JSONB` and `git_sha` columns), `sim.batch_results` (metrics: all_play_pct, all_play_se, top3_rate, qb1_round_mean, def_round_mean), 3 `sim.sample_drafts`. Vintage refusal: `build_data_vintage` (`:175`), `STALE_HOURS=36`.
9. **Backtest/gate** (`src/ffi/sim/backtest.py` + `scripts/run_backtests.py`): CLI is exactly `--reference | --gate`; gate = SystemExit nonzero if in-memory composite < ref.composite − ref.band; composite = mean of 12 cell means, band = 2×SE (`composite_and_band:726`, `evaluate_gate:737`). `load_backtest_pool` (`:614`) currently lacks an ORDER BY (debt). Backtest differentiates **QB timing only** (DEF zeroed, K borrowed/symmetric).
10. **VONA / availability code does not exist anywhere** — greenfield (`ffi/sim/availability.py` is a new file).
11. **`ffi.yahoo_client`** (`src/ffi/yahoo_client.py`): `yahoo_call(fn, *args, **kwargs)` (`:96-120`) — 2.0s min spacing, str-contains-"999" → `YahooRateLimitError`, `requests.RequestException` → `YahooAuthError`, everything else re-raises; NO retries by design. `get_session()` (`:53-78`) refresh is **reactive** (`token_is_valid()` check); token file `config/yahoo_oauth.json`, atomic 0600 writes. Live draft picks come from `yahoo_call(lg.draft_results)`; pick dicts carry `player_id`, `pick`, `team_key`, `round`; **unmade picks lack `player_id`** — filter like `import_yahoo_season.py:14`.
12. **FP client** (`src/ffi/ingest/fantasypros.py`): `FpClient.get()` budget-guarded (`DAILY_BUDGET=30`, `fp_calls_today(conn)`, `FpBudgetExceededError` aborts, never degrades); every call snapshots to `raw.fp_snapshots`; cache reader `latest_fp_payload(conn, endpoint_like, params_subset)`. **`/news` is unwired** (greenfield). Live-verified 2026-07-10: `GET /news` returns items `{title, desc, impact, player_id, team_id, categories, link}`; public tier caps item count (`public_api_limited: true`). Crosswalk has `fp_id` (fp_id-primary matching proved in backtests).
13. **`signals` schema exists with ZERO tables** (`migrations/001_foundation.sql:5`); the capped-adjustment machinery (±10%/player/day, ±20% cumulative) does not exist. `draft` schema likewise empty. Migrations: `NNN_name.sql`, sorted-glob applied by `tests/conftest.py:19`; latest is `005_sim.sql`. Next: `006_draft.sql` (Task 9), `007_signals.sql` (Task 14).
14. **Board source:** `valuation.player_value` (config_version, scenario, xwalk_id, position, proj_points, vorp, tier, …) ordered by vorp DESC; there is no board table. `ffi.sim.pool.build_pool(conn, scenario)` already assembles the draftable pool (2,141 players; `PoolPlayer.ref` = sleeper_id, or team abbr for DEF; `adp` from `adp_2qb`, 999-sentinel → None; K/DEF have no ADP in any format).
15. **`scripts/draft_assistant.py` is dead v1** (371 lines: raw psycopg2, hardcoded 14 teams, reads nonexistent `adjusted_rankings`). Task 13 deletes it (git history preserves it) and creates the replacement at the same path.
16. Stale fixture keys `adp_dd_ppr`/`pos_adp_dd_ppr` at `tests/test_sleeper_adapter.py:31-32` (real Sleeper superflex key is `adp_2qb`; the adapter's `_METADATA_PREFIXES` filter makes them harmless-but-unrealistic).
17. **Boris Chen cohorts = `gmm_tiers(values, max_k=9)`** (`src/ffi/valuation/tiers.py:7-21`, BIC-selected GaussianMixture, tier 1 = highest mean; deterministic `random_state=17`). 2026 hoard_12 tiers: tier 1 = Josh Allen alone (ADP 1.2); tier 2 = 11 deep, ADP 3.5→44.
18. **QB runs are STRUCTURAL, not contagion** (`docs/research/2026-07-10-qb-run-contagion.md`: runs-of-≥3 observed 46 vs null 44.9±4.5, p≈0.45). Do NOT build a contagion mechanism. The gap to fix is LEVEL calibration (QB1 mean 2.84 vs 1.83).
19. `python-dotenv`'s `load_dotenv()` breaks under `python << EOF` stdin — run scripts from files, never heredoc Python that imports ffi.
20. launchd jobs: `com.ffi.morning` 07:00 (backup → sleeper → FP → score → valuation → briefing), `com.ffi.simfarm` 02:30 (`run_sim_farm.py --base-seed $(date +%Y%m%d) && sim_report.py`). Don't double-run FP sync.

## Task overview & dependency order

| # | Task | Depends on | Handoff item |
|---|---|---|---|
| 1 | Debt batch (ORDER BY, per-position degraded fractions, farm git_sha, stale fixture keys) | — | §2.8 |
| 2 | Opponent QB-timing measurement harness | — | §2.1 |
| 3 | `OpponentParams` mechanism (inert default, byte-identical regression) | 2 | §2.1 |
| 4 | Fit calibration, adopt params, uniform-sample audit, D7 protocol | 2, 3 | §2.1 |
| 5 | Re-verify QB-timing conclusions (user-facing research addendum) | 4 | §2.1 |
| 6 | Tier-target QB knob (`qb_tier_targets`) + farm subgrid | 4 | §2.2 |
| 7 | Strategy polish: caps K:1/DEF:1 (+ documented bench-discount decision) | 4 | §2.3 |
| 8 | VONA availability layer (`ffi/sim/availability.py`) | 3 (uses OpponentParams) | §2.4 |
| 9 | `ffi/draft` package: append-only event log + replay + `006_draft.sql` | — | §2.5 |
| 10 | Mode state machine (ADR D1 thresholds) | 9 | §2.5 |
| 11 | Yahoo live poller + proactive token refresh + pick resolution | 9, 10 | §2.5 |
| 12 | Recommendation engine (board + roster-need + VONA) | 8 | §2.5 |
| 13 | Draft assistant CLI (replaces dead v1; resume; manual mode; paper floor) | 9–12 | §2.5 |
| 14 | FP news ingest + `007_signals.sql` signal tables | — | §2.7 |
| 15 | Capped adjustments + confirm gate + briefing wiring + health checks | 14 | §2.7 |
| 16 | Async agent lane (advisory; EXPENDABLE per R3) | 13 | §2.5 |
| 17 | Rehearsal ladder: runbooks, drill harness, pass criteria, tag protocol | 13 | §2.6 |

Execution notes: create branch `phase4-assistant` from `main` before Task 1; create `.superpowers/sdd/phase4-progress.md` with one line per task as they complete. Tasks 2–5 must complete **before** any Task 5+ conclusion is presented to the user as trustworthy (calibration gates downstream evidence). Tasks 9–12 are parallelizable with 2–8 if staffing allows; Task 17 is last and gates the freeze.

---

### Task 1: Debt batch (four small fixes from the Phase 3 ledger)

**Files:**
- Modify: `src/ffi/sim/backtest.py` (`load_backtest_pool` ~line 614; `season_data_vintage` ~line 594)
- Modify: `scripts/run_sim_farm.py` (git_sha capture, ~line 373 `persist_cell` / wherever `git_sha` is read)
- Modify: `tests/test_sleeper_adapter.py:31-32`
- Test: `tests/test_backtest_pool_order.py` (new), existing suites

**Interfaces:**
- Consumes: existing `load_backtest_pool(conn, season, scenario…)` and `season_data_vintage` signatures — do not change signatures, only behavior.
- Produces: deterministic pool ordering; `data_vintage` JSON gains `degraded_fraction_by_pos: {pos: float}`; farm `git_sha` reflects the actual HEAD at run time with a `-dirty` suffix when the tree is dirty.

- [ ] **Step 1: Read the three touch points.** Read `src/ffi/sim/backtest.py` around `load_backtest_pool` (:614) and `season_data_vintage` (:594), and `scripts/run_sim_farm.py` around its git_sha capture. Confirm current behavior matches Facts 8–9.

- [ ] **Step 2: Failing test — pool ordering.** Create `tests/test_backtest_pool_order.py`:

```python
"""load_backtest_pool must return a deterministic order (Phase 3 Minor: missing ORDER BY)."""
from ffi.sim import backtest


def test_load_backtest_pool_has_order_by():
    # The query text itself must carry an ORDER BY — cheaper and stricter than
    # comparing two live loads (which could agree by accident of heap order).
    import inspect
    src = inspect.getsource(backtest.load_backtest_pool)
    assert "ORDER BY" in src, "load_backtest_pool query needs a deterministic ORDER BY"
```

Run: `uv run pytest tests/test_backtest_pool_order.py -v` → FAIL.

- [ ] **Step 3: Add `ORDER BY` to `load_backtest_pool`'s SQL** — order by the same convention `build_pool` uses downstream sort-wise: `ORDER BY (adp IS NULL), adp, proj_points DESC, ref` (adapt column names to the actual query; the tiebreak on `ref` guarantees total order). Run the new test + `uv run pytest tests/test_backtest*.py -q` → all pass.

- [ ] **Step 4: Per-position degraded fractions.** In `season_data_vintage`, replace the season-level `bool_or`-style degraded flag computation with BOTH: keep the existing boolean key (backward compatible — `sim_report.py` reads it) AND add `degraded_fraction_by_pos`: for each position, the fraction of that season's pool rows with `degraded=true`. Extend the existing vintage test (find it via `grep -rn "season_data_vintage" tests/`) with an assertion that a mixed pool yields e.g. `{"QB": 0.0, "RB": 1.0, ...}` fractions and the boolean still reflects any-degraded. Run the touched test file → pass.

- [ ] **Step 5: Farm git_sha.** In `scripts/run_sim_farm.py`, capture the sha at run time as `git rev-parse HEAD` via `subprocess.run(..., check=True, capture_output=True, text=True)` and append `"-dirty"` when `git status --porcelain` is non-empty. No fallback value: if git fails, let the CalledProcessError propagate (fail-loud — a farm row with an unattributable sha is worse than a crashed farm run). Add a unit test that monkeypatches `subprocess.run` to return a fixed sha + dirty status and asserts the recorded string is `"<sha>-dirty"`.

- [ ] **Step 6: Fixture keys.** In `tests/test_sleeper_adapter.py:31-32` replace `"adp_dd_ppr": 30.0` / `"pos_adp_dd_ppr": 4.0` with `"adp_2qb": 30.0` / `"pos_adp_2qb": 4.0` (realistic keys; still exercises the `_METADATA_PREFIXES` skip). Run: `uv run pytest tests/test_sleeper_adapter.py -q` → pass.

- [ ] **Step 7: Full suite + commit.** `uv run pytest -q` → 314+ pass. Then:

```bash
git add -A && git commit -m "chore: phase 3 debt batch — backtest pool ORDER BY, per-position degraded fractions, farm git_sha dirty-aware, realistic sleeper fixture keys"
```

---

### Task 2: Opponent QB-timing measurement harness

**Files:**
- Create: `src/ffi/sim/calibrate.py`
- Create: `scripts/calibrate_opponents.py`
- Test: `tests/test_calibrate.py`

**Interfaces:**
- Consumes: `build_pool(conn, scenario)`, `build_slot_priors(conn)`, `run_draft(...)`, `make_strategy_fn(StrategyParams())`, `qb_timing_by_slot(conn)` (`ffi/history/mining.py:231`).
- Produces (Tasks 4, and the Task 4 sim_report audit, depend on these exact names):

```python
@dataclass(frozen=True)
class QbTimingMeasurement:
    n_drafts: int
    league_means: tuple[float, float, float]      # QB1/QB2/QB3 mean round, opponents only
    per_slot: dict[int, dict[str, float]]         # slot -> {"qb1":…, "qb2":…, "qb3":…, "n":…}
    pos_share_by_band: dict[tuple[str, str], float]  # (band, pos) -> share, bands "R1-3","R4-8","R9+"

def measure_qb_timing(pool, priors, n_drafts: int, base_seed: int,
                      opponent_params=None) -> QbTimingMeasurement
def historical_qb_timing(conn) -> dict[int, dict[str, float]]   # slot -> {"qb1","qb2","qb3","seasons"}
def timing_gap_report(measured: QbTimingMeasurement,
                      historical: dict[int, dict[str, float]]) -> str  # markdown
```

The `opponent_params` argument is accepted-and-ignored-if-None in this task (threaded for real in Task 3 — keep the kwarg now so Task 4's fit loop doesn't need a signature change here).

- [ ] **Step 1: Failing tests.** Create `tests/test_calibrate.py` with a synthetic fixture that needs no DB: build a 60-player pool by constructing `PoolPlayer` instances directly (5 positions × 12 players; give QBs the top proj_points/vorp; ADPs 1..60), and a `SlotPriors` built directly (`SlotPriors(latest_season=2025, pos_share={(s, r): share for s in 1..12 for r in 1..19}, params={})`) where `share` puts QB weight 0.97 in round 1 (rest split across RB/WR/TE/K/DEF respecting availability) and a flat share elsewhere:

```python
def test_measure_qb_timing_qb_heavy_priors_yield_early_qb1():
    m = measure_qb_timing(pool, priors, n_drafts=30, base_seed=7)
    assert m.n_drafts == 30
    assert m.league_means[0] < 1.6          # nearly every opponent takes QB1 in R1
    assert set(m.per_slot) == set(range(1, 13)) - set()  # all 12 slots present
    assert m.league_means[0] <= m.league_means[1] <= m.league_means[2]

def test_measure_is_deterministic():
    a = measure_qb_timing(pool, priors, n_drafts=10, base_seed=3)
    b = measure_qb_timing(pool, priors, n_drafts=10, base_seed=3)
    assert a == b

def test_our_seat_excluded():
    # our seat (franchise slot 12) must not contribute to opponent stats:
    # per_slot[12] stats come only from drafts where slot 12 is NOT our seat — with
    # our_franchise_slot fixed at 12 in measure_qb_timing, slot 12 must be absent.
    m = measure_qb_timing(pool, priors, n_drafts=10, base_seed=3)
    assert 12 not in m.per_slot
```

Run: `uv run pytest tests/test_calibrate.py -v` → FAIL (module missing).

- [ ] **Step 2: Implement `src/ffi/sim/calibrate.py`.** `measure_qb_timing` runs `n_drafts` drafts with seeds `base_seed + i`, `our_pick_fn = make_strategy_fn(StrategyParams())`, `our_franchise_slot=12`, `our_position=None` (marginalize over draft order). For each draft, walk `result.picks`; for every pick with `pos == "QB"` and `franchise_slot != 12`, record that franchise slot's 1st/2nd/3rd QB round. Aggregate league means over all opponent seats × drafts (a seat with no 2nd/3rd QB contributes nothing to that mean — count denominators separately). `pos_share_by_band` uses the same `_band` convention as `ffi/sim/priors.py` (`R1-3`, `R4-8`, `R9+`), opponents only. `historical_qb_timing` wraps `qb_timing_by_slot(conn)` into the dict shape, raising `ValueError` if it returns no rows (fail-loud, no empty default). `timing_gap_report` renders: league-mean table (measured vs historical vs delta for QB1/2/3), per-slot table sorted by slot, top-10 pos-share deviations. Historical league means = seasons-weighted average over slots.

- [ ] **Step 3: Tests pass.** `uv run pytest tests/test_calibrate.py -v` → PASS.

- [ ] **Step 4: CLI.** Create `scripts/calibrate_opponents.py` with `--measure --drafts N --seed S` (defaults 200 / 20260710): connects via `ffi.db`, loads pool (scenario `qb_hoard_12`) + priors + historical, prints `timing_gap_report`, and exits 0. Run against the live DB:

```
uv run python scripts/calibrate_opponents.py --measure --drafts 200
```

Expected: report prints; league QB1 measured mean lands near the audit's ~2.8 (the uniform-sample number may differ from the biased 198-sample 2.84 — RECORD the measured value in the task report; it becomes the calibration baseline).

- [ ] **Step 5: Commit.**

```bash
git add src/ffi/sim/calibrate.py scripts/calibrate_opponents.py tests/test_calibrate.py
git commit -m "feat(sim): opponent QB-timing measurement harness (uniform-sample, per-slot, vs qb_timing_by_slot)"
```

### Task 3: `OpponentParams` — roster-state-conditioned prior scale (inert default)

**Files:**
- Modify: `src/ffi/sim/opponent.py`
- Modify: `src/ffi/sim/draft.py` (`run_draft` threads params)
- Modify: `src/ffi/sim/calibrate.py` (`measure_qb_timing` passes params through)
- Test: `tests/test_opponent_params.py`

**Interfaces:**
- Produces (Tasks 4, 8, 11–12 consume):

```python
# src/ffi/sim/opponent.py
@dataclass(frozen=True)
class OpponentParams:
    tau: float = 1.8
    cand_window: int = 12
    # per-position prior-share multiplier indexed by CURRENT count at that
    # position, e.g. (("QB", (3.0, 1.4, 1.0)),) => a slot holding 0 QBs has its
    # QB prior share ×3.0, holding 1 => ×1.4, holding >=2 => ×1.0 (last entry
    # extends). () = mechanism off = bit-identical legacy behavior.
    pos_need_scale: tuple[tuple[str, tuple[float, ...]], ...] = ()

DEFAULT_OPPONENT_PARAMS = OpponentParams()

def opponent_pick(avail_by_pos, priors, slot, round_, counts, picks_left_after,
                  rng, params: OpponentParams | None = None) -> PoolPlayer
# run_draft gains: opponent_params: OpponentParams | None = None (keyword-only position at end)
```

- Rationale pinned for the implementer: the 2.84-vs-1.83 gap is NOT a Stage-1 share-level error (audit shows R1–3 QB share sim 40.8% vs priors 41.5%) — it is a *conditional-persistence* gap: real managers without a QB pick one with much higher probability than the unconditional share, while the sim re-rolls ~0.4 independently each round, leaving a 0.6³≈22% tail of QB-less-through-R3 teams that drags the mean. `pos_need_scale` conditions the prior on the slot's current count — exactly the handoff's "per-position prior weight," and NOT a contagion mechanism (no dependence on other seats' picks; Fact 18).

- [ ] **Step 1: Failing tests.** `tests/test_opponent_params.py` (reuse the synthetic pool/priors fixture from `tests/test_calibrate.py` — factor it into a shared helper in `tests/simfixtures.py` if not already importable):

```python
def test_default_params_are_bit_identical_to_legacy():
    fn = make_strategy_fn(StrategyParams())
    r_legacy = run_draft(pool, priors, fn, seed=42)
    r_default = run_draft(pool, priors, fn, seed=42,
                          opponent_params=OpponentParams())
    r_empty_scale = run_draft(pool, priors, fn, seed=42,
                              opponent_params=OpponentParams(pos_need_scale=()))
    assert r_legacy.picks == r_default.picks == r_empty_scale.picks

def test_qb_need_scale_pulls_qb1_earlier():
    boosted = OpponentParams(pos_need_scale=(("QB", (4.0, 1.0, 1.0)),))
    m0 = measure_qb_timing(pool, priors, n_drafts=30, base_seed=9)
    m1 = measure_qb_timing(pool, priors, n_drafts=30, base_seed=9,
                           opponent_params=boosted)
    assert m1.league_means[0] < m0.league_means[0]

def test_scale_index_extends_past_tuple_end():
    # count >= len(scale) uses the LAST entry; (("QB",(2.0,))) scales every count.
    p = OpponentParams(pos_need_scale=(("QB", (2.0,)),))
    # direct unit check on the weight math — call opponent_pick with a rigged
    # rng (np.random.default_rng(0)) and counts {"QB": 5}; assert no crash and
    # a player is returned (index clamping, not IndexError).

def test_tau_and_cand_window_respected():
    # cand_window=1 makes stage 2 deterministic: always the head of the list.
```

Run → FAIL.

- [ ] **Step 2: Implement.** In `opponent_pick`, resolve `params = params or DEFAULT_OPPONENT_PARAMS`. Stage 1, after the ROSTER_DAMP block:

```python
scale_map = dict(params.pos_need_scale)
sc = scale_map.get(pos)
if sc:
    w *= sc[min(counts.get(pos, 0), len(sc) - 1)]
```

Stage 2 uses `params.cand_window` / `params.tau` in place of the module constants (constants stay as the dataclass defaults' source of truth — set the dataclass defaults to `TAU` and `CAND_WINDOW`). **Do not touch the rng call sequence**: the scale multiplies weights before the existing renormalize; with `()` the loop body is skipped and draws are bit-identical. `run_draft` accepts `opponent_params` and passes it to every `opponent_pick` call. `measure_qb_timing` passes its `opponent_params` through.

- [ ] **Step 3: Tests pass + full suite.** `uv run pytest tests/test_opponent_params.py tests/test_draft*.py tests/test_opponent*.py -q` then `uv run pytest -q` → all pass (bit-identical default means zero churn in existing expectations).

- [ ] **Step 4: D7 gate (inert check).** `uv run python scripts/run_backtests.py --gate` → exit 0, composite unchanged from 0.5297 (default params are bit-identical; any drift here means Step 2 broke the rng contract — stop and fix).

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(sim): OpponentParams with roster-state pos_need_scale (inert default, bit-identical regression pinned)"
```

---

### Task 4: Fit the calibration, adopt it, and make the audit a uniform-sample regression check

**Files:**
- Modify: `src/ffi/sim/calibrate.py` (fit function)
- Modify: `scripts/calibrate_opponents.py` (`--fit`)
- Modify: `src/ffi/sim/opponent.py` (adopt fitted defaults)
- Modify: `scripts/sim_report.py` (`_assumption_audit` → uniform sample; live historical target)
- Modify: `scripts/run_sim_farm.py` (record `opponent_params` in `sim.batches`)
- Test: `tests/test_calibrate.py` (extend), existing `tests/test_sim_report*.py` if present

**Interfaces:**
- Produces:

```python
def fit_qb_need_scale(pool, priors, historical: dict, n_drafts: int, base_seed: int,
                      grid: dict[str, tuple] | None = None) -> tuple[OpponentParams, list[dict]]
# returns (best_params, trials) where trials = [{"scale": (s0,s1,s2), "qb1":…, "qb2":…,
#   "qb3":…, "objective":…}, …] sorted by objective ascending
```

- Objective (pinned so the fit is reproducible and reviewable): with historical seasons-weighted league means `h1,h2,h3` and measured `m1,m2,m3`, `objective = 3*|m1-h1| + 2*|m2-h2| + 1*|m3-h3| + 0.5 * per_slot_qb1_MAE`. Default grid: `s0 ∈ (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)`, `s1 ∈ (0.75, 1.0, 1.5, 2.0)`, `s2 ∈ (0.5, 0.75, 1.0)` — 72 candidates × `n_drafts=200` each (~72 × 200 × 5.5ms ≈ 80s; acceptable one-off).
- Acceptance criteria (written pass/fail, ADR D7 style): league QB1 mean within **±0.25** of historical; QB2 and QB3 means within **±0.5**; per-slot QB1 MAE reported (no hard bar — priors carry slot identity; the knob is global); top-10 pos-share deviations table not materially worse than the Task 2 baseline (no new deviation > the baseline's max). If no grid point meets the QB1 bar, STOP and report — do not silently widen the grid (a mechanism mismatch is a finding, not a tuning problem).

- [ ] **Step 1: Failing test.** Extend `tests/test_calibrate.py`: on the synthetic fixture, rig priors so QB share is LOW (0.15/round flat) → un-scaled QB1 mean is late; assert `fit_qb_need_scale` with a tiny grid (`s0 ∈ (1.0, 6.0)`, s1/s2 fixed 1.0, `n_drafts=10`) picks `s0=6.0` when the synthetic "historical" target is `{slot: {"qb1": 1.0, "qb2": 3.0, "qb3": 9.0, "seasons": 16} …}`, and that `trials` is sorted by objective. Run → FAIL.

- [ ] **Step 2: Implement `fit_qb_need_scale`** (pure grid search calling `measure_qb_timing` with `OpponentParams(pos_need_scale=(("QB", (s0,s1,s2)),))`, fixed `base_seed` for every candidate — common random numbers so the comparison is paired). Test passes.

- [ ] **Step 3: Run the real fit.**

```
uv run python scripts/calibrate_opponents.py --fit --drafts 200 --seed 20260710
```

`--fit` prints the trials table, the best params, and the acceptance-criteria verdict lines (each `PASS`/`FAIL` with numbers), and writes the full evidence to `reports/opponent-calibration-2026-07-10.md` (gitignored dir — the durable copy lands in Task 5's research doc). Record the winning `(s0, s1, s2)`.

- [ ] **Step 4: Adopt.** Set the fitted tuple as the shipped default: in `opponent.py`, `pos_need_scale`'s default becomes `(("QB", (<s0>, <s1>, <s2>)),)` with a comment citing the fit report + this plan. Update the Task 3 bit-identical test: it must now pin `OpponentParams(pos_need_scale=())` ≡ legacy (rename to `test_empty_scale_is_bit_identical_to_legacy`), and add `test_default_is_calibrated` asserting the default's QB scale equals the fitted tuple (a change-detector so nobody edits it casually). Full suite green.

- [ ] **Step 5: Farm provenance.** In `run_sim_farm.py`, write `dataclasses.asdict(DEFAULT_OPPONENT_PARAMS)`-equivalent JSON into the `sim.batches.opponent_params` column (it exists; currently records only TAU-era constants or nothing — check and extend). Verify with a 1-cell smoke run if the script supports it, else assert via unit test on the INSERT parameters.

- [ ] **Step 6: Audit upgrade.** In `scripts/sim_report.py`: replace the `sim.sample_drafts`-based QB1 estimate with a direct `measure_qb_timing(pool, priors, n_drafts=100, base_seed=<date-derived>, opponent_params=None)` call (uniform, unbiased, ~1s), and replace the hardcoded `HISTORICAL_QB1_ROUND=1.83` with the live seasons-weighted mean from `historical_qb_timing(conn)` (fail-loud if empty; keep `QB1_TOLERANCE=0.5`). Post-calibration this check must PASS; make a failure **exit nonzero** (it is now a regression check on an adopted calibration, not a WARN on a known bias). Keep the pos-share deviation table, now computed from the same uniform sample. Update/extend the sim_report tests accordingly.

- [ ] **Step 7: D7 gate + protocol.** Run `uv run python scripts/run_backtests.py --gate`. The calibrated opponents change the backtest environment, so a failure here is EXPECTED-POSSIBLE and is not automatically a strategy regression. Protocol (execute in order, document every number in the task report):
  1. If gate exits 0 → done, note the new composite.
  2. If nonzero → recompute per-season cell means (the gate output / `run_all_cells` detail) and compare against the reference's `detail` JSONB per-season pattern. If the delta is a roughly uniform level shift across seasons (opponents got harder everywhere) and the *strategy ordering* across the 4 REF strategies is preserved → this is a legitimate measurement correction: run `uv run python scripts/run_backtests.py --reference` to rebuild, with the description field citing "opponent QB-timing calibration (Task 4, fit report 2026-07-10)". Only `--reference` writes; record old→new composite/band in the task report AND in Task 5's research doc.
  3. If instead one season moves and others don't, or REF-strategy ordering flips → STOP; that pattern is not explained by calibration and needs investigation before anything is rebuilt.

- [ ] **Step 8: Nightly farm re-run.** `uv run python scripts/run_sim_farm.py --base-seed 20260710 && uv run python scripts/sim_report.py` (231s + report). Confirm: vintage OK, audit section now PASSES (QB1 mean within 0.5 of historical), report renders. This refreshed farm data feeds Task 5.

- [ ] **Step 9: Commit.**

```bash
git add -A && git commit -m "feat(sim): adopt calibrated QB need-scale (QB1 2.84->~1.8); uniform-sample audit now a hard regression check; D7 reference protocol executed"
```

---

### Task 5: Re-verify the QB-timing conclusions (user-facing deliverable)

**Files:**
- Create: `docs/research/2026-07-XX-opponent-calibration.md` (dated at execution)
- Modify: `docs/research/2026-07-10-strategy-conclusions.md` (addendum section + regenerated evidence block)
- Modify: `scripts/strategy_conclusions.py` (only if the R7 re-run needs the calibrated params threaded — check first; `run_draft`'s default now carries them)

**Interfaces:**
- Consumes: Task 4's refreshed farm run + rebuilt-or-confirmed D7 reference; `scripts/strategy_conclusions.py`'s regenerate-in-place contract (Fact: the generated block carries its own vintage; prose above is vintage-locked and must be re-checked when the block changes).

- [ ] **Step 1: Regenerate evidence.** `uv run python scripts/strategy_conclusions.py` — refreshes the GENERATED EVIDENCE block (farm qb_subgrid + defk tables + the in-memory R7 sim-vs-backtest correlation) from the calibrated farm/backtest. NOTHING persists to the D7 reference (the R7 path is in-memory by construction — verify that note still prints).

- [ ] **Step 2: Research doc.** Write `docs/research/2026-07-XX-opponent-calibration.md`: the measured baseline (Task 2 uniform number vs the biased 2.84), the mechanism (conditional-persistence, not contagion — cite `2026-07-10-qb-run-contagion.md`), the fit table (top 5 trials), acceptance verdicts, before/after per-slot table, the D7 gate outcome incl. old→new reference if rebuilt (with the per-season delta table that justified it), and the reproduce line (`uv run python scripts/calibrate_opponents.py --fit --drafts 200 --seed 20260710`).

- [ ] **Step 3: Re-adjudicate the QB policy (HAND-WRITTEN — you, not the script).** Add an addendum section to `2026-07-10-strategy-conclusions.md` ("Addendum 2026-07-XX: post-calibration re-verification"). Questions it must answer against the new numbers: (a) does "don't front-load" survive (both methods still rank qb_plan 0 worst)? (b) does the farm still prefer aggressive delay, and does the backtest still refuse to corroborate — i.e. does the OPTIMISTIC flag on "wait on QB" clear, tighten, or reverse now that opponents take QBs a round earlier? (c) new R7 Spearman. Update the headline recommendation lines in place if the verdicts change (the doc's own rule: prose is vintage-locked to the evidence block). Do NOT touch the DEF/K or tier-break sections beyond noting the evidence block refresh.

- [ ] **Step 4: Present the addendum to the user for review** (this is draft-policy, their call to accept), then commit:

```bash
git add docs/research/ scripts/strategy_conclusions.py
git commit -m "research: post-calibration QB-timing re-verification — opponent QB1 ~1.8, conclusions re-adjudicated"
```

### Task 6: Tier-target QB knob (`qb_tier_targets`) + farm subgrid

**Files:**
- Modify: `src/ffi/sim/strategy.py`
- Modify: `scripts/run_sim_farm.py` (`build_grid`)
- Test: `tests/test_strategy_tier_targets.py`
- Modify: `scripts/strategy_conclusions.py` + `docs/research/2026-07-10-strategy-conclusions.md` (new evidence table + hand-written verdict)

**Interfaces:**
- Produces: `StrategyParams` gains one field (all existing fields unchanged):

```python
qb_tier_targets: tuple = ()   # () = disabled. qb_tier_targets[n] (n = counts["QB"]
# at call time) = max tier acceptable for voluntarily drafting QB #(n+1) in rule 4.
# Example (2, 3, 99): QB1 only from tier <=2, QB2 from tier <=3, QB3 any tier.
# Rule-4-only, like qb_not_before: deadline forces (rule 2) ignore it, so a
# mis-set target can never deadlock the draft — the deadline backstop still fires.
# Index past tuple end = unrestricted (consistent with qb_not_before's n-indexing).
```

Semantics pinned: this is a *which-QB* filter, not a *when* filter — it composes with `qb_not_before` (timing) and `qb_by_round` (backstop). Tier values come from `PoolPlayer.tier` (`gmm_tiers` on league-adjusted values — the user's Boris-Chen cohorts, Fact 17).

- [ ] **Step 1: Failing tests.** `tests/test_strategy_tier_targets.py` on the synthetic fixture (give QBs explicit tiers: 1 QB tier 1, 3 QBs tier 2, rest tier 3+):

```python
def test_tier_target_filters_rule4_qb_candidates():
    # qb_tier_targets=(2,...): with all tier<=2 QBs taken, rule 4 must not
    # voluntarily take a tier-3 QB even though QB vorp dominates.

def test_deadline_force_ignores_tier_target():
    # qb_by_round=(1,...) + qb_tier_targets=(1,) + tier-1 QB already gone:
    # round 1 deadline still forces the best available QB (any tier).

def test_empty_targets_is_noop():
    # StrategyParams() vs StrategyParams(qb_tier_targets=()) -> identical drafts, seed-for-seed.

def test_index_past_end_unrestricted():
    # counts["QB"]=2 with qb_tier_targets=(2,) -> QB3 candidates unrestricted.
```

Run → FAIL.

- [ ] **Step 2: Implement.** In rule 4's QB branch (after the existing `qb_not_before` gate, `strategy.py:202-207`), add:

```python
if pos == "QB" and qb_n < len(params.qb_tier_targets):
    max_tier = params.qb_tier_targets[qb_n]
    cands = [c for c in cands if c.tier <= max_tier]
    # NB: filter the candidate list BEFORE the [:CAND_WINDOW] slice is scored,
    # and compute _is_last_in_tier against the position's full available list
    # exactly as today (the filter affects candidacy, not tier-closure math).
```

(Adapt to the actual code shape: the current loop builds `cands = avail_by_pos.get(pos) or []` then scores `cands[:CAND_WINDOW]` — apply the tier filter between those two.) Tests pass; full suite green.

- [ ] **Step 3: D7 gate (inert default).** `uv run python scripts/run_backtests.py --gate` → exit 0, composite equals Task 4's reference (default `()` is a no-op; REF strategies don't set it).

- [ ] **Step 4: Farm subgrid.** In `build_grid`, add a third block `kind="qb_tier"`: scenario `qb_hoard_12`, `defk_round=14`, `tier_break_bonus=0.0`, `qb_not_before=(1,1,1)`, `qb_by_round=(2,5,9)` (the backstop), over `QB_TIER_PLANS = [(), (1,2,99), (2,2,99), (2,3,99), (1,3,99), (2,3,3)]` → 6 new cells (66→72 nightly; +~21s, fine). `()` is the control cell — same knobs as the qb_subgrid's plan-1-adjacent cell, giving a within-night baseline. Extend the grid unit test (find via `grep -rn "build_grid" tests/`) to assert 72 cells and the new block's params.

- [ ] **Step 5: Run the farm + report the comparison.** `uv run python scripts/run_sim_farm.py --base-seed <today> && uv run python scripts/sim_report.py`. Add a "QB tier-target policy" table to `scripts/strategy_conclusions.py`'s generated block (all-play% by tier plan, cross-cell deltas only) and write the hand-written verdict in the conclusions doc: does tier-targeting beat the round-plan control beyond its ±1.96se, and which target? (Expected mechanically: `(2,3,99)` ≈ "QB1 from Allen-or-tier-2, QB2 before the tier-3 cliff" — but the table decides, not the expectation.)

- [ ] **Step 6: Commit.**

```bash
git add -A && git commit -m "feat(sim): qb_tier_targets strategy knob (rule-4 tier filter, deadline-backstopped) + qb_tier farm subgrid + conclusions table"
```

---

### Task 7: Strategy polish — default caps K:1 / DEF:1 (+ documented bench-discount decision)

**Files:**
- Modify: `src/ffi/sim/strategy.py` (`StrategyParams.caps` default)
- Test: `tests/test_strategy.py` (existing) + one new case
- Modify: `docs/research/2026-07-10-strategy-conclusions.md` (one-paragraph note)

**Interfaces:**
- Produces: `caps` default becomes `(("QB", 4), ("RB", 9), ("WR", 9), ("TE", 3), ("K", 1), ("DEF", 1))`. Everything else unchanged.

- [ ] **Step 1: Failing test.** Add to the strategy suite:

```python
def test_default_caps_forbid_second_k_and_def():
    # Roster with K:1, DEF:1 late in the draft: rule 4 must never return a
    # second K/DEF under default caps even when their vorp tops the remainder
    # (the demo-draft R19 second-kicker bug class).
```

Run → FAIL (current default caps allow K:2/DEF:2).

- [ ] **Step 2: Change the default; tests pass.** Note: `run_draft` re-validates only availability + feasibility, and the opponent model's `ROSTER_DAMP` already effectively bans opponents' 2nd K/DEF (×0.02 then 0.0) — this change is our-seat-only, as intended.

- [ ] **Step 3: Farm A/B evidence.** One ad-hoc paired run (NOT persisted; write a short throwaway invocation inside the task, e.g. a `--caps-ab` flag on `scripts/calibrate_opponents.py` is overkill — instead run 200 drafts × both caps via a 20-line `scripts/_oneoff_caps_ab.py`, print the paired all-play delta ±1.96se, then DELETE the script before commit). Record the delta in the conclusions doc note. Expected: small positive or noise — the point of the cap is behavioral sanity, not win-rate.
- [ ] **Step 4: D7 gate.** `uv run python scripts/run_backtests.py --gate` → REF strategies pin their own caps? CHECK `REF_STRATEGIES` (`backtest.py:176`): if they inherit the default caps, the gate composite may shift within-band; apply the Task 4 Step 7 protocol if it trips (expected: K/DEF caps change nothing before `defk_round=18` forces — likely bit-identical; verify and record).
- [ ] **Step 5: Bench-discount decision (documented YAGNI).** Add to the conclusions doc note: with K/DEF capped at 1, the second-onesie failure mode is structurally impossible; a bench-value discount for late onesies would only affect TE3/QB4 hoarding, which `ROSTER_DAMP`-analogous caps already bound — NOT implemented, revisit only if Level-3 rehearsal overrides show late-round bench picks misfiring.
- [ ] **Step 6: Commit.**

```bash
git add -A && git commit -m "feat(sim): default caps K:1/DEF:1 (kills second-kicker class); bench-discount decision documented as YAGNI"
```

---

### Task 8: VONA availability layer

**Files:**
- Create: `src/ffi/sim/availability.py`
- Test: `tests/test_availability.py`

**Interfaces:**
- Consumes: `opponent_pick(..., params)` (Task 3), `PoolPlayer`, `SlotPriors`.
- Produces (Task 12 consumes these exact names):

```python
@dataclass(frozen=True)
class AvailabilityForecast:
    n_rollouts: int
    n_upcoming: int                       # opponent picks simulated before our next pick
    survival: dict[str, float]            # ref -> P(still available at our next pick)
    expected_best_vorp: dict[str, float]  # pos -> E[max vorp still available at our next pick]

def forecast_availability(
    avail_by_pos: dict[str, list[PoolPlayer]],
    priors: SlotPriors,
    upcoming: list[tuple[int, int, dict]],   # [(franchise_slot, round_, counts), ...] in pick order
    n_rollouts: int,
    seed: int,
    opponent_params=None,
) -> AvailabilityForecast

def vona(avail_by_pos: dict[str, list[PoolPlayer]],
         forecast: AvailabilityForecast) -> dict[str, float]
# vona[pos] = best-available-now vorp at pos - forecast.expected_best_vorp[pos]
# (>= 0 up to MC noise; how much value dies at this position if we wait one turn)
```

Design pinned: each rollout gets `np.random.default_rng(seed + k)`; within a rollout, walk `upcoming` in order, calling `opponent_pick` with that seat's own *copied* counts dict (counts evolve per rollout — copy the inner dicts, callers' state must never mutate), maintaining a rollout-local `taken` set filtered exactly like `draft._avail_view` (reuse it — import, don't reimplement). `survival` counts every player in the head `CAND_WINDOW*2` of each position list (survival of players deeper than any opponent could plausibly take is 1.0 by construction — set them to 1.0 without simulation to keep the dict small is WRONG for correctness of `expected_best_vorp`; instead compute `expected_best_vorp` from the rollout's live view directly, and populate `survival` only for the simulated-window players, documented in the docstring). Empty `upcoming` (back-to-back snake turn) → survival all 1.0, `expected_best_vorp` = current best, `vona` all 0.0 — this is a real case (position 12's R1→R2), test it.

- [ ] **Step 1: Failing tests.** `tests/test_availability.py` on the synthetic fixture:

```python
def test_certain_taken_player_has_low_survival():
    # 1 upcoming pick, priors forcing QB with prob ~1, cand_window=1 via
    # OpponentParams -> the head QB's survival ~0.0; everyone else ~1.0.

def test_back_to_back_turn_is_identity():
    f = forecast_availability(avail, priors, upcoming=[], n_rollouts=50, seed=1)
    assert f.n_upcoming == 0 and all(v == 1.0 for v in f.survival.values())
    assert vona(avail, f) == {pos: 0.0 for pos in avail}

def test_deterministic_by_seed():
    # same args, same seed -> equal forecasts; different seed -> allowed to differ.

def test_vona_nonnegative_up_to_noise():
    # E[max vorp later] <= max vorp now for every pos (subset property holds
    # exactly per rollout, so even the mean satisfies it exactly).

def test_caller_state_not_mutated():
    # counts dicts and avail lists passed in are unchanged after the call.

def test_perf_budget():
    # 22 upcoming picks, 200 rollouts on the 60-player fixture: < 2.0s wall.
```

Run → FAIL.

- [ ] **Step 2: Implement `availability.py`** per the pinned design (~90 lines). No DB access, no I/O — pure function of its arguments (this is on the assistant's between-picks path; determinism and testability are the contract).

- [ ] **Step 3: Tests pass; full suite green. Real-pool perf smoke:** the perf test bounds cost on the 60-player fixture, but `_avail_view` filtering and the `taken`-set ops scale with pool size (2,141 live) — run one manual smoke against the live DB (extend `scripts/calibrate_opponents.py` with `--vona-smoke`: load pool+priors, 200 rollouts × 22 synthetic upcoming picks, print wall time). Anything under ~10s is fine (the between-picks window is ~15 min); record the number in the task report.

- [ ] **Step 4: Commit.**

```bash
git add src/ffi/sim/availability.py scripts/calibrate_opponents.py tests/test_availability.py
git commit -m "feat(sim): VONA availability layer — MC survival + expected-best-vorp forecast over calibrated opponent model"
```

### Task 9: `ffi.draft` package — append-only event log with crash-safe replay + `006_draft.sql`

**Files:**
- Create: `src/ffi/draft/__init__.py` (empty), `src/ffi/draft/state.py`
- Create: `migrations/006_draft.sql`
- Create: `scripts/import_draft_log.py`
- Test: `tests/test_draft_state.py`

**Interfaces:**
- Produces (Tasks 11, 13, 16, 17 consume):

```python
# src/ffi/draft/state.py
class TornTailError(Exception): ...     # unparseable NON-final line = corruption

@dataclass(frozen=True)
class DraftEvent:
    seq: int          # 1-based, strictly increasing
    ts: str           # isoformat, stamped at append
    kind: str         # "pick" | "undo" | "mode" | "note" | "meta"
    payload: dict

class DraftLog:
    def __init__(self, path: Path): ...          # opens append-mode, creates parents
    def append(self, kind: str, payload: dict) -> DraftEvent   # json line + flush + fsync
    @classmethod
    def replay(cls, path: Path) -> tuple["DraftLog", list[DraftEvent], bool]
    # -> (log positioned to continue, events, torn_tail)
```

Event payloads pinned: `pick` = `{overall:int, round:int, franchise_slot:int, team_key:str|None, ref:str|None, yahoo_player_id:str|None, name:str, pos:str, source:"poll"|"manual"}`; `undo` = `{undoes_seq:int}` (undo is an EVENT, never a file rewrite — append-only means append-only); `mode` = `{from:str, to:str, reason:str}`; `meta` = `{league_key, our_franchise_slot, our_position, board_vintage, scoring_config}` (first event of every log).

Failure policy (ADR D1): a torn FINAL line (crash mid-write) is the one expected corruption — `replay` drops it and returns `torn_tail=True`; the caller (assistant) MUST surface it as a banner. Any unparseable NON-final line raises `TornTailError` — that's real corruption, refuse to run on it (fail-loud; a silently mis-replayed draft state is the worst outcome in this system).

- [ ] **Step 1: Failing tests.** `tests/test_draft_state.py`:

```python
def test_append_replay_roundtrip(tmp_path): ...        # 3 events -> replay == events, torn False
def test_seq_strictly_increasing_across_resume(tmp_path): ...  # replay then append -> seq continues
def test_torn_final_line_dropped_and_flagged(tmp_path):
    # write 2 good lines + b'{"seq": 3, "ts"' (no newline) -> replay: 2 events, torn_tail=True
def test_corrupt_middle_line_raises(tmp_path):
    # good, garbage, good -> TornTailError
def test_undo_is_an_append_not_a_rewrite(tmp_path): ...  # file line count only grows
```

Run → FAIL.

- [ ] **Step 2: Implement `state.py`** (~80 lines). `append` writes `json.dumps(...) + "\n"`, then `flush()` + `os.fsync(fileno)` — every pick durably on disk before the function returns (R2: in-memory draft state is a named SPOF). No try/except around the write path at all: an fsync failure must crash the assistant into MANUAL/PAPER visibly, not be absorbed.

- [ ] **Step 3: Tests pass.**

- [ ] **Step 4: `migrations/006_draft.sql`** (post-draft durable copy; the JSONL file is the draft-day source of truth, the table is for after-action analysis):

```sql
-- 006_draft.sql — draft-day event archive (imported from the JSONL log post-draft)
CREATE TABLE IF NOT EXISTS draft.events (
    draft_id    text NOT NULL,          -- e.g. '2026-real', '2026-rehearsal-2'
    seq         integer NOT NULL,
    ts          timestamptz NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('pick','undo','mode','note','meta')),
    payload     jsonb NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_id, seq)
);
```

`scripts/import_draft_log.py --log <path> --draft-id <id>`: replays via `DraftLog.replay` (raises on corruption — same policy), inserts all events in one transaction, refuses (SystemExit) if the draft_id already has rows unless `--replace` (which deletes-then-inserts, still one transaction). Prints the row count.

- [ ] **Step 5: Migration test.** Extend the conftest-driven suite trivially: add a test that inserts one row into `draft.events` and reads it back (proves the migration applied; conftest already truncates the `draft` schema). Full suite green.

- [ ] **Step 6: Commit.**

```bash
git add -A && git commit -m "feat(draft): append-only fsync'd event log with crash-safe replay; draft.events archive + importer"
```

---

### Task 10: Mode state machine (ADR Domain 1, verbatim thresholds)

**Files:**
- Create: `src/ffi/draft/modes.py`
- Test: `tests/test_draft_modes.py`

**Interfaces:**
- Produces (Tasks 11, 13 consume):

```python
class Mode(str, Enum):
    LIVE = "LIVE"; POLL_DEGRADED = "POLL-DEGRADED"; MANUAL = "MANUAL"; PAPER = "PAPER"

@dataclass
class ModeMachine:
    mode: Mode = Mode.LIVE
    consecutive_failures: int = 0
    def on_poll_success(self) -> tuple[Mode, str | None]
    def on_poll_failure(self) -> tuple[Mode, str | None]
    def on_rate_limit(self) -> tuple[Mode, str | None]
    def operator_set(self, target: Mode, reason: str) -> tuple[Mode, str]
# each returns (new_mode, transition_reason_or_None_if_no_change); the CALLER
# logs every non-None transition to the DraftLog as a "mode" event and renders
# the banner — the machine itself does no I/O (pure, exhaustively testable).
```

Transition table (this IS the spec; ADR D1):

| From | Event | To | Note |
|---|---|---|---|
| LIVE | poll failure | POLL-DEGRADED | **one** failure, immediately |
| POLL-DEGRADED | poll failure | MANUAL | = two consecutive failures |
| LIVE / POLL-DEGRADED | 999 | MANUAL | immediate, **no retry** — lockout is 10–15 min |
| POLL-DEGRADED | poll success | LIVE | auto-recover; resets counter |
| MANUAL | poll success/failure/999 | MANUAL | **sticky** — automatic transitions only downshift; leaving MANUAL is `operator_set` only (deliberate: mid-draft flapping between MANUAL and LIVE is worse than staying manual; ADR D1 specifies automatic *downshift*) |
| any | operator_set(X) | X | incl. PAPER (assistant unusable → printed board) and MANUAL→LIVE after the operator judges the lockout over |

- [ ] **Step 1: Failing tests.** `tests/test_draft_modes.py` — one test per row of the table above (10 tests), plus `test_counter_resets_on_success` and `test_rate_limit_from_live_skips_degraded`. Run → FAIL.
- [ ] **Step 2: Implement `modes.py`** (~50 lines, no I/O, no try/except — nothing here can fail). Tests pass.
- [ ] **Step 3: Commit.**

```bash
git add src/ffi/draft/modes.py tests/test_draft_modes.py
git commit -m "feat(draft): LIVE/POLL-DEGRADED/MANUAL/PAPER mode machine, ADR D1 thresholds pinned by test-per-transition"
```

---

### Task 11: Yahoo live poller — diff detection, pick resolution, proactive token refresh

**Files:**
- Create: `src/ffi/draft/poller.py`
- Modify: `src/ffi/yahoo_client.py` (add `ensure_fresh_token`)
- Test: `tests/test_draft_poller.py`, extend `tests/test_yahoo_client.py`

**Interfaces:**
- Consumes: `yahoo_call` semantics (Fact 11), `ModeMachine` (Task 10), `DraftLog` (Task 9).
- Produces (Task 13 consumes):

```python
# src/ffi/draft/poller.py
@dataclass(frozen=True)
class ResolvedPick:
    overall: int; round: int; team_key: str; franchise_slot: int
    yahoo_player_id: str; ref: str | None; name: str | None; pos: str | None
    # ref None => crosswalk miss: the assistant MUST queue it for manual
    # resolution and show a banner — never guess, never drop (fail-loud).

@dataclass(frozen=True)
class PollResult:
    new_picks: tuple[ResolvedPick, ...]
    latency_s: float                 # wall time of the fetch (rehearsal metric)
    total_made: int                  # picks with a player_id seen so far

class DraftPoller:
    def __init__(self, fetch_fn: Callable[[], list[dict]],
                 resolve: Callable[[str], tuple[str, str, str] | None],  # yahoo_player_id -> (ref, name, pos)
                 team_slots: dict[str, int],       # team_key -> franchise slot 1-12
                 log: DraftLog): ...
    def poll(self) -> PollResult     # raises YahooRateLimitError / YahooAuthError UPWARD
    # (mode decisions belong to the caller's ModeMachine, not the poller)

def load_team_slots(conn, league_key: str) -> dict[str, int]
def build_resolver(conn) -> Callable[[str], tuple[str, str, str] | None]

# src/ffi/yahoo_client.py
def ensure_fresh_token(sc, margin_s: int = 900) -> bool
# refresh NOW if the access token has < margin_s of life left; returns True if
# it refreshed. Called by the assistant every loop tick — this is the proactive
# refresh ADR Domain 4 requires (the library's own refresh is reactive).
```

Diff detection pinned: Yahoo returns ALL pick slots; **unmade picks lack `player_id`** (Fact 11). The poller keeps `self._seen: set[int]` of made pick numbers; `new_picks` = made picks whose `pick` number is not in `_seen`, sorted by `pick`. Out-of-order arrivals and re-sends are therefore idempotent. Every new pick is appended to the DraftLog (`source:"poll"`) BEFORE being returned — durable before visible.

- [ ] **Step 1: Failing tests — poller.** `tests/test_draft_poller.py` with fake `fetch_fn`s (lists of dicts; no network):

```python
def test_unmade_picks_ignored(): ...          # slots without player_id never surface
def test_diff_only_new_picks(): ...           # second poll with same payload -> ()
def test_out_of_order_and_resend_idempotent(): ...
def test_crosswalk_miss_yields_ref_none(): ...  # resolver returns None -> ResolvedPick(ref=None), still logged
def test_rate_limit_propagates():
    # fetch_fn raises YahooRateLimitError -> poll() re-raises (NO catch inside)
def test_picks_logged_before_returned(tmp_path): ...  # DraftLog contains the pick event
def test_unknown_team_key_raises():
    # a team_key absent from team_slots -> ValueError (a 13th team mid-draft is
    # corruption, not a case to survive)
```

Run → FAIL.

- [ ] **Step 2: Implement `poller.py`.** `poll()` contains NO try/except: `yahoo_call`'s typed exceptions (`YahooRateLimitError`, `YahooAuthError`) propagate to the assistant loop, which owns the ModeMachine (single place where failure policy lives — Task 13). `load_team_slots`: read the 2026 league's team list (`yahoo_call(lg.teams)` at startup — one budgeted call) and map `team_key -> franchise slot` by joining against the `teams` table convention used by `backfill_draft_teams.py:53-70` (READ THAT FILE FIRST and reuse its mapping logic; the franchise-slot convention was established in Phase 1 — do not invent a new one). Raise `ValueError` unless exactly 12 teams map. `build_resolver`: one upfront SQL over `player_id_xwalk` (yahoo_id → sleeper ref, name, position; include DEF team-abbr rows) into a dict; return `dict.get`-style closure. Tests pass.

- [ ] **Step 3: Failing tests — proactive refresh.** Extend `tests/test_yahoo_client.py` (mirror its existing monkeypatch style, Fact: it patches `yc.time`): fake `sc` object with `token_time`/`token_is_valid`/`refresh_access_token`; assert refresh fires when <900s left, not when fresh, and that a refresh failure RAISES `YahooAuthError` (no swallow — draft-day token death must be loud so the operator flips to MANUAL and keeps drafting from the board).

- [ ] **Step 4: Implement `ensure_fresh_token`** in `yahoo_client.py` (~15 lines; compute remaining life from `sc.token_time + 3600 - time.time()`). Tests pass; full suite green.

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(draft): Yahoo draftresults poller (diff-by-pick-number, crosswalk resolution, log-before-return) + proactive OAuth refresh"
```

### Task 12: Recommendation engine — board + roster-need + VONA, consistent with the sim strategy

**Files:**
- Create: `src/ffi/draft/recommend.py`
- Test: `tests/test_recommend.py`

**Interfaces:**
- Consumes: `make_strategy_fn`/`StrategyParams` internals (import `feasible`, `required_picks`, `CAND_WINDOW` from `ffi.sim.opponent`; `_score`-equivalent logic from `ffi.sim.strategy` — if the private helpers are needed, promote them to public names in `strategy.py` in this task rather than duplicating), `forecast_availability`/`vona` (Task 8), `build_pool` (Fact 14).
- Produces (Task 13, 16 consume):

```python
@dataclass(frozen=True)
class Recommendation:
    primary: PoolPlayer
    rule: str                      # "feasibility" | "qb_deadline" | "defk" | "value"
    top: tuple[tuple[float, PoolPlayer], ...]        # top 8 scored candidates, desc
    by_position: dict[str, tuple[PoolPlayer, ...]]   # top 3 per position
    vona: dict[str, float] | None                    # None when no forecast supplied
    notes: tuple[str, ...]         # e.g. "last tier-2 QB on the board", "K/DEF window opens R14"

def recommend(avail_by_pos, round_: int, counts: dict, picks_left_after: int,
              params: StrategyParams,
              forecast: AvailabilityForecast | None = None) -> Recommendation
```

Consistency contract (the load-bearing property): **`recommend(...).primary` must equal `make_strategy_fn(params)(avail_by_pos, round_, counts, picks_left_after)` for identical inputs, always.** The assistant's number one answer IS the rehearsed sim strategy — no second implementation drift. Refactor shape (pinned to reduce implementation variance): extract the rule cascade into a public pure function in `strategy.py` —

```python
def evaluate_rules(avail_by_pos, round_: int, counts: dict,
                   picks_left_after: int, params: StrategyParams) -> tuple[PoolPlayer, str]
# returns (pick, rule) with rule in {"feasibility","qb_deadline","defk","value"}
```

— then `make_strategy_fn(params)` becomes a closure returning `evaluate_rules(...)[0]` (public signature and ALL existing strategy tests untouched — that's the correctness gate), and `recommend` calls `evaluate_rules` directly for `primary` + `rule`, computing `top`/`by_position` with the same `_score`/tiebreak helpers. `notes`: "last tier-N POS" from `_is_last_in_tier`-equivalent over the available list; VONA lines when forecast given ("waiting one turn costs ~X vorp at QB").

- [ ] **Step 1: Failing tests.** `tests/test_recommend.py`:

```python
def test_primary_equals_strategy_fn_property():
    # hypothesis or 200-seed loop over random subsets of the synthetic pool +
    # random legal (round, counts) states: recommend().primary == strategy_fn(...)
def test_rule_attribution(): ...        # deadline state -> rule == "qb_deadline", etc.
def test_top_is_desc_and_tiebroken_like_pick_best(): ...
def test_vona_none_without_forecast(): ...
def test_last_in_tier_note_fires(): ...
```

Run → FAIL.

- [ ] **Step 2: Implement** (~120 lines incl. the minimal `strategy.py` refactor). Full suite green (existing strategy tests unchanged is part of the deliverable).
- [ ] **Step 3: Commit.**

```bash
git add -A && git commit -m "feat(draft): recommendation engine — primary pinned equal to sim strategy fn, rule attribution, VONA annotations"
```

---

### Task 13: Draft assistant CLI (replaces the dead v1)

**Files:**
- Delete: `scripts/draft_assistant.py` (dead v1 — git history preserves it; Fact 15)
- Create: `scripts/draft_assistant.py` (fresh), `src/ffi/draft/session.py`
- Test: `tests/test_draft_session.py`

**Interfaces:**
- Consumes: everything from Tasks 8–12.
- Produces: `DraftSession` — the assistant's headless core, so the terminal shell stays a dumb renderer and every draft-day behavior is testable/drillable without a terminal:

```python
# src/ffi/draft/session.py
@dataclass
class SessionConfig:
    league_key: str; our_franchise_slot: int; our_position: int
    scenario: str = "qb_hoard_12"
    poll_interval_s: float = 7.0          # ADR: 5-10s; tune from rehearsal lag data
    log_path: Path = ...                  # data/draft-logs/<date>-<league_key>.jsonl
    params: StrategyParams = StrategyParams()

class DraftSession:
    def __init__(self, cfg, pool, priors, poller: DraftPoller | None,
                 machine: ModeMachine, log: DraftLog, clock=time.monotonic): ...
    def tick(self) -> list[str]        # one loop iteration: maybe poll, apply picks; returns banner lines
    def manual_pick(self, query: str) -> ResolvedPick        # fuzzy match, applies + logs (source="manual")
    def undo_last(self) -> None                              # appends undo event, rebuilds state
    def board_lines(self, pos: str | None = None) -> list[str]
    def recommendation(self) -> Recommendation               # uses forecast when <= 30 upcoming picks
    def status_lines(self) -> list[str]   # mode banner, pick clock position, roster, vintage stamps
    @classmethod
    def resume(cls, cfg, pool, priors, poller, machine) -> "DraftSession"  # replay log
```

`tick()` owns the failure policy (the ONLY try/except on the poll path):

```python
if self.poller is not None and self.machine.mode in (Mode.LIVE, Mode.POLL_DEGRADED) \
        and self.clock() - self._last_poll >= self.cfg.poll_interval_s:
    try:
        result = self.poller.poll()
    except YahooRateLimitError:
        new, reason = self.machine.on_rate_limit()      # -> MANUAL, no retry (ADR D1)
        self._log_mode(new, reason)
    except YahooAuthError as e:
        new, reason = self.machine.on_poll_failure()
        self._log_mode(new, reason, detail=str(e))
    else:
        self._apply(result.new_picks)
        new, reason = self.machine.on_poll_success()
        self._log_mode(new, reason)
    self._last_poll = self.clock()
# FAIL-LOUD Level 2: in POLL-DEGRADED/MANUAL the board serves last-known draft
# state — degraded state is disclosed via the mode banner rendered on EVERY
# frame plus a logged "mode" event; recovery to LIVE is automatic only from
# POLL-DEGRADED (ADR Domain 1). Any exception other than the two typed Yahoo
# errors propagates and crashes: state is fsync'd per pick, resume is drilled.
```

Terminal shell (`scripts/draft_assistant.py`): a `select.select([sys.stdin], [], [], 0.5)` loop calling `session.tick()` between inputs (no threads, no curses — YAGNI; rehearsable plain terminal). Commands: `<enter>`=refresh+recommendation, `p <name>`=manual pick for the team currently on the clock, `u`=undo, `b [pos]`=board, `s`=status, `m live|manual|paper`=operator mode set, `q`=quit. Startup preflight (ADR D4): board vintage check (refuse >36h stale without `--override-stale`, ADR D2), `ensure_fresh_token`, one `draft_results` probe, `load_team_slots`, then ALWAYS write the paper board `reports/paper-board-<date>.md` (top 60 overall + top 15/position with tiers — the PAPER floor exists before the draft room opens). `--resume` flag → `DraftSession.resume`; torn tail → prominent banner. `--no-poll` flag → poller None, pure MANUAL (Level-1/-3 rehearsals and the paper-only floor).

- [ ] **Step 1: Failing tests.** `tests/test_draft_session.py` (fake poller = list-of-payload fetch_fn from Task 11's tests; fake clock):

```python
def test_tick_applies_new_picks_and_advances_board(): ...
def test_999_goes_manual_and_stays(): ...           # then operator_set back to LIVE resumes polling
def test_two_failures_go_manual(): ...
def test_manual_pick_fuzzy_match_and_ambiguity():
    # "p jos all" -> Josh Allen; ambiguous prefix -> raises AmbiguousPickError
    # listing candidates (never guess a pick)
def test_undo_rebuilds_state(): ...
def test_resume_reproduces_state(tmp_path): ...     # 5 events, crash, resume -> same avail/counts/mode
def test_our_turn_recommendation_uses_forecast(): ...
def test_tick_respects_poll_interval(): ...         # fake clock; no double polls
```

Run → FAIL.

- [ ] **Step 2: Implement `session.py`** (~250 lines; if it reads better, split the pure state-derivation half into `src/ffi/draft/replay.py` — Task 16 imports exactly that half, so the seam is natural — but one file is acceptable). State derivation: replayed/streamed picks maintain `taken`, per-seat counts, current overall pick, whose turn (via `snake_position`), upcoming `(slot, round, counts)` list for the forecast (slots from `team_slots` + `slot_of_position`-equivalent live mapping: draft position order comes from Yahoo's pick/team_key sequence itself — derive `position -> franchise_slot` from round-1 pick order, cross-check against cfg.our_position, raise on mismatch). Fuzzy match: case-insensitive substring-of-tokens over available names, unique hit required.

- [ ] **Step 3: Implement the shell script** (~120 lines, argparse: `--league-key`, `--our-slot`, `--position`, `--resume`, `--no-poll`, `--override-stale`, `--log-path`). No business logic in the shell.

- [ ] **Step 4: Tests + full suite green. Manual smoke:** `uv run python scripts/draft_assistant.py --no-poll --our-slot 12 --position 5` → preflight (minus Yahoo, skipped under --no-poll), paper board written, board renders, `p`/`u`/`r` commands work against the live 2026 pool, quit cleanly, `--resume` restores. (Zero Yahoo calls in this task — the live poll path is exercised in Task 17's drills.)

- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(draft): draft assistant CLI — DraftSession core, plain-terminal shell, resume, manual mode, paper floor; retires dead v1 assistant"
```

---

### Task 14: FP news ingest + `007_signals.sql` signal tables

**Files:**
- Create: `migrations/007_signals.sql`, `scripts/ingest_fp_news.py`
- Modify: `src/ffi/ingest/fantasypros.py` (nothing structural — reuse `FpClient.get`; add a thin `news()` wrapper only if param shape needs it)
- Test: `tests/test_fp_news.py`

**Interfaces:**
- Produces (Task 15 consumes):

```sql
-- 007_signals.sql
CREATE TABLE IF NOT EXISTS signals.signals (
    signal_id    bigserial PRIMARY KEY,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    source       text NOT NULL,                        -- 'fp_news'
    external_id  text NOT NULL,                        -- stable dedupe key (fp link)
    xwalk_id     integer,                              -- resolved via fp player_id; NULL = unmatched
    player_name  text,
    signal_type  text NOT NULL CHECK (signal_type IN ('injury','role_change','depth_chart','hype','news')),
    title        text NOT NULL,
    summary      text,
    impact       text,                                 -- FP's impact string, verbatim
    evidence_url text NOT NULL,
    payload      jsonb NOT NULL,                       -- full item, provenance
    status       text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','denied')),
    decided_at   timestamptz,
    UNIQUE (source, external_id)
);
CREATE TABLE IF NOT EXISTS signals.adjustments (
    adjustment_id bigserial PRIMARY KEY,
    signal_id     bigint NOT NULL REFERENCES signals.signals(signal_id),
    xwalk_id      integer NOT NULL,
    pct           real NOT NULL CHECK (pct >= -0.10 AND pct <= 0.10),  -- ±10%/adjustment (per-day cap: one adjustment per signal, signals dedupe daily)
    applied_at    timestamptz NOT NULL DEFAULT now(),
    note          text
);
```

`scripts/ingest_fp_news.py --daily`: checks headroom (`fp_calls_today` + this run's 1 call ≤ 28 — leave 2 spare under the 30 budget; abort loudly otherwise), `GET /news` via `FpClient.get` (auto-snapshots to `raw.fp_snapshots`), maps items → `signals.signals` rows: `signal_type` from `categories` (contains "Injury" → `injury`; "Depth Chart" → `depth_chart`; "Breakout"/"Sleeper"-ish → `hype`; else `news` — pin the exact mapping in code with the live category strings observed on first run), `xwalk_id` via fp `player_id` → crosswalk `fp_id` (unmatched → row kept with `xwalk_id NULL` + counted in the output — visible, not dropped), dedupe via `ON CONFLICT (source, external_id) DO NOTHING`. **Deliberate deviation from design §4.7, documented here: no LLM digestion** — FP items arrive already structured (`impact`, `player_id`, `categories`), so the deterministic mapping replaces the agent-digestion stage; direction/magnitude are set by the HUMAN at confirm time (Task 15), which is strictly more conservative than the design's capped-agent path. YAGNI on RSS/expert feeds until FP coverage proves insufficient.

- [ ] **Step 1: Failing tests.** `tests/test_fp_news.py`: fixture payload of 3 items (shapes from Fact 12) → mapper produces correct rows (types, xwalk resolution via a seeded crosswalk row, NULL for unknown player_id); dedupe on second run inserts 0; budget-exceeded aborts before any HTTP (monkeypatch `fp_calls_today` → 28). Run → FAIL.
- [ ] **Step 2: Implement migration + script.** Tests pass; full suite green (conftest picks up 007 automatically).
- [ ] **Step 3: One live run** (budget permitting): `uv run python scripts/ingest_fp_news.py --daily` → N items stored (public tier will cap N; that's fine and logged). Record the live `categories` strings seen; adjust the mapping if they differ from the fixture assumption, and pin them in the test fixture.
- [ ] **Step 4: Commit.**

```bash
git add -A && git commit -m "feat(signals): FP /news ingest -> typed signals tables (007), deterministic mapping, budget-guarded, dedupe by link"
```

### Task 15: Capped adjustments + human confirm gate + briefing wiring + health checks

**Files:**
- Create: `src/ffi/signals_apply.py` (module, not package — one responsibility), `scripts/confirm_signals.py`
- Modify: `scripts/morning_briefing.py`, `scripts/phase1_report.py`, `src/ffi/draft/recommend.py`-adjacent board loader (see Step 4), `launchd/com.ffi.morning.plist`
- Test: `tests/test_signals_apply.py`

**Interfaces:**
- Produces:

```python
# src/ffi/signals_apply.py
class AdjustmentCapError(Exception): ...

def apply_adjustment(conn, signal_id: int, pct: float, note: str = "") -> int
# validates: signal exists & status='confirmed'; |pct| <= 0.10; player's
# SUM(pct) over signals.adjustments incl. this one within [-0.20, +0.20]
# (the ±20% cumulative cap); at most one adjustment per (xwalk_id, day)
# (the per-day cap). Violation -> AdjustmentCapError (no partial writes).
# Returns adjustment_id.

def cumulative_pct(conn) -> dict[int, float]      # xwalk_id -> clamped-sum of applied pct

def adjusted_pool(conn, scenario: str) -> list[PoolPlayer]
# build_pool output with proj_points/vorp shifted: adjusted_proj = proj*(1+cum),
# adjusted_vorp = vorp + proj*cum (baseline unchanged — a <=20% nudge on a few
# players does not move replacement rank; documented in the docstring).
# Pool order is re-sorted by the same build_pool convention after shifting.
```

- `scripts/confirm_signals.py`: interactive; lists `pending` signals (id, title, impact, player, url); per signal: `c` confirm (then prompt direction ±/magnitude as pct, validated ≤0.10, writes status + `apply_adjustment`), `d` deny, `s` skip, `q` quit. Confirm-only-informational is allowed (confirm with pct 0 → status confirmed, no adjustment row). This is THE human gate: nothing moves a board number without a keystroke here (design §4.7).
- Briefing: new "Signals" section — pending count + titles (top 5), yesterday's applied adjustments with provenance, cumulative-cap utilization per player; plus `ingest_fp_news.py --daily` added to the morning launchd chain AFTER `ingest_fantasypros --daily` (same budget pool, ~1 extra call).
- Health checks appended to `CHECKS` (keeps SQL-boolean convention):

```python
("signals tables present", "SELECT to_regclass('signals.signals') IS NOT NULL AND to_regclass('signals.adjustments') IS NOT NULL"),
("no adjustment exceeds per-signal cap", "SELECT count(*) = 0 FROM signals.adjustments WHERE abs(pct) > 0.10"),
("no player exceeds cumulative cap", "SELECT count(*) = 0 FROM (SELECT xwalk_id, sum(pct) s FROM signals.adjustments GROUP BY 1) t WHERE abs(s) > 0.20"),
("draft events table present", "SELECT to_regclass('draft.events') IS NOT NULL"),
```

- [ ] **Step 1: Failing tests.** `tests/test_signals_apply.py`: cap violations (single >10%, cumulative >20% across two signals, two-same-day) each raise `AdjustmentCapError` and leave zero rows; happy path returns id; `adjusted_pool` shifts exactly the adjusted player and preserves ordering convention; unconfirmed signal refuses. Run → FAIL.
- [ ] **Step 2: Implement module + confirm CLI.** All validation inside one transaction (`SELECT ... FOR UPDATE` on the player's adjustment rows to make the caps race-free — single operator, but correctness is cheap here). Tests pass.
- [ ] **Step 3: Briefing + launchd + health checks.** Extend `morning_briefing.py` (health header unchanged, new section below board); edit the plist ProgramArguments chain; append the 4 checks. Run `uv run python scripts/phase1_report.py` → 30/30 OK. Reload the launchd job (`launchctl unload/load` the plist) and note it in the task report.
- [ ] **Step 4: Assistant integration.** In `scripts/draft_assistant.py` preflight, load the pool via `adjusted_pool` when any adjustment rows exist (banner: "board includes N signal adjustments, cum-cap max X%") else `build_pool`. The sim/farm/backtest NEVER use `adjusted_pool` (live-board-only by design — sims must stay reproducible from snapshots; note this in the module docstring). D7 gate not required: no strategy/valuation-table change (verify `--gate` anyway → composite unchanged, exit 0).
- [ ] **Step 5: Commit.**

```bash
git add -A && git commit -m "feat(signals): capped human-confirmed adjustments (±10%/signal, ±20% cum, per-day), confirm CLI, briefing section, health checks 26->30"
```

---

### Task 16: Async agent lane (advisory annotations — EXPENDABLE per R3)

**Files:**
- Create: `scripts/draft_agent_lane.py`
- Test: `tests/test_agent_lane.py` (context-builder only)

**Interfaces:**
- Consumes: the DraftLog JSONL (read-only follower) + `DraftSession`-shaped state rebuild (reuse `DraftLog.replay` + the Task 13 state-derivation helpers — import from `ffi.draft.session`, do not duplicate).
- Produces: `reports/draft-annotations-live.md`, overwritten atomically (write-temp-rename) after each of OUR picks; the assistant (already built) renders its content in `status_lines` ONLY when mtime < 300s, else prints "agent lane: stale/absent" — the lane can die mid-draft with zero effect on the board (bright line: advisory only, never blocks).

Design: a standalone foreground process (`uv run python scripts/draft_agent_lane.py --log <path>`) that tails the log; when a new `pick` event lands and it's between our picks, it rebuilds state, composes a compact context (our roster, next-pick window size, top-10 board with tiers/VONA numbers — all deterministic inputs), and shells out to `claude -p <prompt> --max-turns 1` via `subprocess.run(timeout=60)`. On ANY failure (timeout, nonzero exit, missing CLI): print the error to ITS OWN terminal and write nothing — the annotations file simply goes stale, which the assistant already discloses. No try/except in the assistant for this; the lane is a separate OS process by design.

- [ ] **Step 1: Failing test — context builder.** `build_annotation_context(events, pool, priors) -> str` is pure; test that it contains the roster, the on-clock window, and top-board lines for a 3-pick synthetic log. Run → FAIL.
- [ ] **Step 2: Implement** (~100 lines). The subprocess call sits behind `--dry-run` (prints the prompt instead of calling) so the test suite and rehearsals never invoke a model.
- [ ] **Step 3: Manual smoke with `--dry-run`** against a replayed rehearsal log. Commit:

```bash
git add -A && git commit -m "feat(draft): advisory agent lane — log follower, atomic annotation file, hard-isolated from the pick path (expendable)"
```

---

### Task 17: Rehearsal ladder — runbooks, drill harness, pass criteria, tag protocol

**Files:**
- Create: `docs/runbooks/draft-day.md`, `docs/runbooks/rehearsal-ladder.md`, `scripts/drill_draft.py`
- Modify: `scripts/import_draft_log.py` usage documented per drill
- Test: `tests/test_drill_transports.py`

**Interfaces:**
- Consumes: `DraftPoller(fetch_fn=...)` injection seam (Task 11), `DraftSession` (Task 13).
- Produces: `scripts/drill_draft.py` — drives the REAL assistant against fake transports:

```python
def scripted_fetch(conn, league_key: str, season: int, schedule: dict) -> Callable[[], list[dict]]
# replays a REAL historical draft (draft_picks for one NAJEE season) as a
# growing draftresults payload, releasing picks on a wall-clock schedule;
# `schedule` injects faults: {"latency_s": {pick: s}, "fail_at": [pick,...],
#  "rate_limit_at": pick|None, "auth_fail_at": pick|None}
```

CLI: `uv run python scripts/drill_draft.py --drill {lag|999|refresh|crash} --season 2024` — each drill runs the assistant loop headlessly (DraftSession + fake clock where possible, real wall clock for lag measurement), measures, and prints `PASS`/`FAIL` against the written criteria, appending a row to `docs/runbooks/rehearsal-log.md` (date, drill, result, metrics, git sha).

**Written pass criteria (ADR D7, verbatim — these gate the ladder):**
1. Poll lag p95 < 15s (measured pick-visible-to-applied, `PollResult.latency_s` + apply time).
2. Token refresh mid-session without pick loss (drill forces `token_time` near expiry; assistant refreshes proactively; zero missed picks vs script).
3. Forced-999 → MANUAL switchover < 30s (from injected 999 to operator completing a manual pick — the drill prompts the operator; this one is human-timed).
4. Crash → resume with full state (kill -9 mid-draft at a random pick; `--resume` must reproduce taken/counts/mode exactly — the drill diffs derived state).

**`docs/runbooks/rehearsal-ladder.md`** must contain: the ladder table (Level 1 FP Draft Wizard browser mocks, 5–10/day human-paced, automation kept OFF the API-key account — R13; Level 2 private Yahoo test league; Level 3 user-in-loop with every override logged as `note` events and reviewed after), per-level entry/exit criteria (each level gates the next; a level passes when all its drills pass twice consecutively), the Level-2 league setup steps for the user (create private league, 12 teams, schedule draft, fill 11 seats with autodraft bots — Yahoo has NO draft-submission API and mocks are not API-visible, so this league is the ONLY live-plumbing venue), the drill schedule relative to Aug 29–30, and the tag protocol (ADR D8): `git tag rehearsal-N` after each passed drill session, `git tag draft-day` = last passed FULL rehearsal, freeze ≈ Aug 22, draft-day runbook step 1 is `git checkout draft-day`.

**`docs/runbooks/draft-day.md`** must contain: T-1 checklist (pg backup + external copy, PG_BIN eval alignment check from the Phase 3 ledger folded here — verify `pg_restore` binary matches server major version during the pre-draft restore drill, laptop power, phone hotspot tested, printed paper board), T-0 preflight (git checkout draft-day; `uv run python scripts/draft_assistant.py --league-key <2026 key> ...`; verify preflight banner green), mode cheat-sheet (what each mode means, the exact keystrokes, when to flip to PAPER), the 999 rule (do NOT retry; you are in MANUAL for 10–15 min minimum), and post-draft steps (`import_draft_log.py`, archive the log).

- [ ] **Step 1: Failing tests.** `tests/test_drill_transports.py`: `scripted_fetch` releases picks per schedule (fake clock), injects one failure and one 999 at the configured picks, payload shape matches Fact 11 (unmade picks lack `player_id`). Run → FAIL.
- [ ] **Step 2: Implement `drill_draft.py`** (~200 lines): transports + the four drill runners + rehearsal-log appender. Tests pass.
- [ ] **Step 3: Run drills 1, 2, 4 locally** (drill 3 needs the operator — run it with the user at Level 1 time). All PASS → `git tag rehearsal-1`. Record metrics in `docs/runbooks/rehearsal-log.md` (this file is committed — drill history is draft-day evidence, ADR D7).
- [ ] **Step 4: Ladder docs.** Write both runbooks per the required content above. The Level-2 items that need the user (league creation, scheduling bot drafts) go in "Pending user inputs" wording — the runbook is the request.
- [ ] **Step 5: Full suite + health gate + commit.** `uv run pytest -q` (all green) and `uv run python scripts/phase1_report.py` (30/30):

```bash
git add -A && git commit -m "feat(rehearsal): drill harness (lag/999/refresh/crash vs written ADR D7 criteria), draft-day + ladder runbooks, rehearsal-1 tagged"
```

---

## Self-Review (performed at plan-writing time)

**1. Spec coverage (handoff §2 → tasks):** §2.1 calibration → Tasks 2–5 (measure → mechanism → fit/adopt/audit → re-verify; contagion explicitly excluded per Fact 18). §2.2 tier knob → Task 6. §2.3 polish → Task 7 (bench discount = documented YAGNI decision, per "consider"). §2.4 VONA → Task 8. §2.5 assistant → Tasks 9–13 + 16 (polling 5–10s ✓, diff detection ✓, proactive refresh ✓, ADR D1 state machine ✓, per-pick persistence/resume ✓, manual keystroke mode ✓, async lane ✓). §2.6 ladder → Task 17 (Levels 1–3 defined, pass criteria written, Level 2 user-gated). §2.7 news lane → Tasks 14–15 (caps ±10/±20 ✓, human confirm ✓; LLM digestion consciously replaced by FP's own structure — flagged as a deliberate deviation in Task 14). §2.8 debt → Task 1 (+ PG_BIN folded into Task 17's T-1 checklist). Handoff §1 process contract → Global Constraints. §5 pending inputs unchanged (league renewal R8 trigger stays armed — re-audit before trusting the board once `renewed` ≠ '').

**2. Placeholder audit:** no TBDs. Two deliberate read-first instructions remain (Task 11 Step 2: reuse `backfill_draft_teams.py`'s team→slot mapping; Task 14 Step 3: pin live `categories` strings on first run) — these are verify-against-reality steps, not gaps, and each says exactly what to read and what to do with it.

**3. Type consistency:** `OpponentParams` name/fields identical in Tasks 3, 4, 8, 11; `QbTimingMeasurement`/`measure_qb_timing`/`historical_qb_timing` identical in Tasks 2, 4; `AvailabilityForecast`/`forecast_availability`/`vona` identical in Tasks 8, 12; `DraftLog`/`DraftEvent`/`TornTailError` in Tasks 9, 11, 13, 16; `Mode`/`ModeMachine` methods in Tasks 10, 13; `ResolvedPick`/`PollResult`/`DraftPoller(fetch_fn, resolve, team_slots, log)` in Tasks 11, 13, 17; `Recommendation`/`recommend` in Tasks 12, 13; `apply_adjustment`/`adjusted_pool`/`AdjustmentCapError` in Task 15 only. Health-check count arithmetic: 26 → +4 (Task 15) = 30, cited consistently in Tasks 15 and 17.

**4. Known deviations from handoff/design, deliberate:** (a) no LLM digestion stage in the news lane (FP items pre-structured; human sets magnitude — stricter than design §4.7); (b) assistant UI is a plain select-loop terminal, not curses (rehearsability over polish); (c) `adjusted_pool` is live-board-only — sims/backtests never see signal adjustments (reproducibility); acknowledged trade-off: the live board can diverge from the farm-optimized board, but the ±10/±20 caps are smaller than typical GMM tier gaps, the confirm gate provides oversight, and the preflight banner discloses every active adjustment; (d) MANUAL is sticky against auto-upshift (ADR D1 specifies automatic downshift; upshift is operator judgment); (e) bench-value discount not implemented (Task 7 Step 5 records why).

