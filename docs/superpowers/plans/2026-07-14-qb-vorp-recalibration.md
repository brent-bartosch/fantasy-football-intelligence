# QB VORP Recalibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lower the QB replacement baseline (rank 36 → the empirically-chosen ~24) so projected QB value tracks actual points, without leaving the roster QB-thin.

**Architecture:** A guarded rank search (in-memory VORP+tier recompute over one backtest pool, graded on actual points under the realistic tuned strategy, with QB-depth and injury-robustness disqualifiers) picks the rank; deployment is a pointer switch at 6 sites + a valuation/backtest-pool rebuild + a D7 reference re-baseline. `qb_hoard_12` is never mutated, so rollback is a pointer revert.

**Tech Stack:** Python 3.14, `uv`, Postgres (`ffi.db`), existing `ffi.sim` / `ffi.valuation` / `ffi.sim.backtest` modules, `pytest`.

## Global Constraints

- Run everything via `PYTHONPATH=src uv run python ...` and `PYTHONPATH=src uv run pytest ...`.
- **Never mutate `qb_hoard_12`** — switch the default *pointer* to the winning scenario (ADR D8 rollback depends on `qb_hoard_12` staying intact). Reference: [design §3](../specs/2026-07-14-qb-vorp-recalibration-design.md), [ADR Domain 8](../risks/2026-07-14-qb-vorp-recalibration-adr.md).
- **Fail loud** on any invariant that could yield a silently-wrong valuation (repo convention; ADR Domain 1). No silent fallback.
- The **guarded search is the arbiter** of the rank; the D7 gate is the discipline/re-baseline step, NOT the goodness test ([design §4](../specs/2026-07-14-qb-vorp-recalibration-design.md)).
- QB replacement rank = `24 + qb_extra_rostered` (only QB is padded; verified in `ffi/valuation/baseline.py`).
- The 6 default-pin sites (design §3): `ffi/sim/strategy.py` `StrategyParams.scenario`; `ffi/sim/backtest.py` `VORP_SCENARIO`; `scripts/run_sim_farm.py` `SCENARIOS_MAIN`; `scripts/build_backtest_pools.py` `build_pool(conn, "qb_hoard_12")`; `scripts/draft_assistant.py` snapshot scenario; `scripts/build_valuation.py` lines ~172/182.

---

## File map

- Modify `scripts/qb_vorp_sweep.py` — upgrade the throwaway sweep into the guarded search (Task 1).
- Modify `scripts/build_valuation.py` — add intermediate scenario(s) only if the winner is intermediate (Task 4).
- Modify `ffi/sim/strategy.py`, `ffi/sim/backtest.py`, `scripts/run_sim_farm.py`, `scripts/build_backtest_pools.py`, `scripts/draft_assistant.py` — pointer switch (Task 3).
- Modify `tests/test_valuation.py` (+ any default-scenario assertion tests) — Task 5.
- Append to `docs/superpowers/specs/2026-07-14-qb-vorp-recalibration-design.md` — decision addendum (Task 5).

---

### Task 1: Upgrade the sweep into the guarded search

**Files:**
- Modify: `scripts/qb_vorp_sweep.py`

**Interfaces:**
- Consumes: `build_slot_priors(conn)`, `load_backtest_pool(conn, season)`, `load_points_lookup(conn, season)`, `run_draft(pool, priors, pick_fn, seed=, our_franchise_slot=12)`, `evaluate_league(rosters, cv_by_pos={}, seed=, points_lookup=)`, `make_strategy_fn(StrategyParams(...))`, `gmm_tiers(values) -> list[int]` (from `ffi.valuation.tiers`), `PoolPlayer` (frozen dataclass; use `dataclasses.replace`).
- Produces: a `--selftest` mode and a search that prints, per rank, actual all-play % ± 2SE, avg QB count, % with a real QB3, mean injury-robustness, QB tier count, top-3 finish rate, and a per-season breakdown.

- [ ] **Step 1: Write the failing self-test for the two guardrail helpers**

Add near the top of `scripts/qb_vorp_sweep.py` (after imports) a `--selftest` path in `main()` that exercises `retier_qbs` and `injury_robustness` on a synthetic pool. First add the test body that will fail because the helpers don't exist yet:

```python
def _selftest():
    from dataclasses import replace as _replace
    # 5 synthetic QBs with descending vorp -> gmm should yield >=2 tiers
    qbs = [PoolPlayer(ref=f"q{i}", name=f"QB{i}", position="QB",
                      proj_points=400 - 30 * i, vorp=300 - 40 * i, tier=1,
                      adp=float(i + 1), gsis_id=f"q{i}") for i in range(5)]
    retiered = retier_qbs(qbs)
    assert len({p.tier for p in retiered}) >= 2, "gmm should split 5 spread QBs into >=2 tiers"
    # injury_robustness: a roster with a real QB3 loses less than one without
    print("selftest OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=src uv run python scripts/qb_vorp_sweep.py --selftest`
Expected: `NameError: name 'retier_qbs' is not defined`.

- [ ] **Step 3: Implement the recompute + guardrail helpers**

Add these helpers to `scripts/qb_vorp_sweep.py` (replaces the throwaway `repriced_pool`; keep `qb_vorp_at_rank`):

```python
from ffi.valuation.tiers import gmm_tiers

def retier_qbs(qbs):
    """Reassign QB tiers via gmm on their (already-recomputed) vorps -- faithful
    to how build_valuation materializes tiers, done in-memory so no per-rank
    scenario needs materializing for the search."""
    tiers = gmm_tiers([p.vorp for p in qbs])
    return [replace(p, tier=t) for p, t in zip(qbs, tiers)]

def repriced_pool(pool, rank):
    """Pool with QB vorp recomputed at `rank` AND QB tiers regmm'd. Non-QB
    untouched (only QB replacement rank changes across scenarios)."""
    new_vorp = qb_vorp_at_rank(pool, rank)
    qbs = retier_qbs([replace(p, vorp=new_vorp[p.ref]) for p in pool if p.position == "QB"])
    return qbs + [p for p in pool if p.position != "QB"]

def injury_robustness(rosters, our_pos, points_lookup, seed):
    """Our actual-points all-play% AFTER losing our best (highest-vorp) QB --
    a roster with a real QB3 barely drops; a thin one craters (one QB slot
    scores 0). Directly measures the QB3-protection guardrail."""
    roster = list(rosters[our_pos])
    qbs = [p for p in roster if p.position == "QB"]
    qb1 = max(qbs, key=lambda p: p.vorp)
    injured = dict(rosters)
    injured[our_pos] = [p for p in roster if p.ref != qb1.ref]
    return evaluate_league(injured, cv_by_pos={}, seed=seed, points_lookup=points_lookup)[our_pos]
```

- [ ] **Step 4: Run the self-test to verify it passes**

Run: `PYTHONPATH=src uv run python scripts/qb_vorp_sweep.py --selftest`
Expected: `selftest OK`.

- [ ] **Step 5: Wire the guarded search (realistic strategy + guardrails + logging + tier assert)**

Replace the search body so it: uses the tuned strategy `StrategyParams(qb_by_round=(2, 5, 9), qb_tier_targets=(1, 2, 99))`; searches ranks `[24, 27, 30, 33, 36]`; for each (rank, season) builds `repriced_pool`, **asserts ≥2 distinct QB tiers survive** (else prints a loud `TIER-COLLAPSE` warning and marks `qb_tier_targets` inert for that rank — risk R4); runs 50 seeds; records per draft the actual all-play %, QB count, has-QB3 flag, `injury_robustness`, and top-3 finish flag (rank ≤3 of 12); prints a per-rank + per-season table and a pooled summary.

```python
N_SEEDS = 50
RANKS = [24, 27, 30, 33, 36]
STRAT = StrategyParams(qb_by_round=(2, 5, 9), qb_tier_targets=(1, 2, 99))
# ... per (rank, season): pool = repriced_pool(load_backtest_pool(conn, season), rank)
#     assert len({p.tier for p in pool if p.position=='QB'}) >= 2, f"TIER-COLLAPSE rank {rank} season {season}"
#     pick_fn = make_strategy_fn(STRAT); grade each of N_SEEDS drafts on actual points;
#     also compute injury_robustness and top-3 flag per draft.
```

- [ ] **Step 6: Run the search to confirm it executes cleanly (smoke, not the decision)**

Run: `PYTHONPATH=src uv run python scripts/qb_vorp_sweep.py 2>&1 | grep -vE "DEBUG|INFO|WARNING"`
Expected: a per-rank table with all five ranks, both guardrail columns populated, no `TIER-COLLAPSE` assertion crash, and a pooled summary. (Interpretation happens in Task 2.)

- [ ] **Step 7: Commit**

```bash
git add scripts/qb_vorp_sweep.py
git commit -m "feat(valuation): guarded QB-rank search (tuned strategy + depth/injury guardrails + in-memory retier)"
```

---

### Task 2: Run the search and decide the winning rank (decision gate)

**Files:** none modified — this task runs the Task-1 harness and records a decision.

- [ ] **Step 1: Run the 50-seed search**

Run: `PYTHONPATH=src uv run python scripts/qb_vorp_sweep.py 2>&1 | tee /tmp/qb_search.txt | grep -vE "DEBUG|INFO|WARNING"`

- [ ] **Step 2: Apply the hard success criterion**

Winner = the rank with the highest pooled actual all-play % **among ranks whose QB-count AND injury-robustness are no worse than rank 36's** (design §2). Record the chosen rank and its scenario name (`24 → qb_hoard_0`; `27/30/33 → new qb_hoard_3/6/9`; `36 → qb_hoard_12`, i.e. no change).

- [ ] **Step 3: Guard against the two null outcomes (risks R10, R8)**

- If the cross-rank spread is < 2pp (deadlines dominate — R10): **stop**, do not ship; note that the baseline effect is masked by the QB deadline and reassess whether the deadline knob should relax. Escalate to the user.
- If every rank below 36 fails a guardrail (overshoot — R8): **stop**, keep rank 36, escalate.

- [ ] **Step 4: Confirm the winner at 100 seeds**

Temporarily set `N_SEEDS = 100`, re-run, and confirm the winner's edge holds with the tighter CI. Revert `N_SEEDS` to 50.
Run: `PYTHONPATH=src uv run python scripts/qb_vorp_sweep.py`
Expected: winner unchanged; CI ~±1.2%.

- [ ] **Step 5: Record the decision**

Write the chosen rank/scenario and the pooled table into the commit message; the rest of the plan uses `<WINNER_SCENARIO>` to mean this name.

```bash
git commit --allow-empty -m "chore(valuation): QB-rank search decision — WINNER=<WINNER_SCENARIO> (rank <R>), <X>% vs rank-36 <Y>%, guardrails pass"
```

---

### Task 3: Switch the default pointer at all 6 sites

**Files:**
- Modify: `ffi/sim/strategy.py` (StrategyParams.scenario default)
- Modify: `ffi/sim/backtest.py` (`VORP_SCENARIO`)
- Modify: `scripts/run_sim_farm.py` (`SCENARIOS_MAIN`)
- Modify: `scripts/build_backtest_pools.py` (`build_pool(conn, ...)`)
- Modify: `scripts/draft_assistant.py` (snapshot scenario)
- Modify: `scripts/build_valuation.py` (lines ~172/182 validation/print)
- Test: `tests/test_pointer_default.py` (new)

> Skip this entire task if `<WINNER_SCENARIO>` == `qb_hoard_12` (search found no improvement).

- [ ] **Step 1: Write the failing pointer assertion test**

```python
# tests/test_pointer_default.py
from ffi.sim.strategy import StrategyParams
from ffi.sim.backtest import VORP_SCENARIO

WINNER = "<WINNER_SCENARIO>"  # e.g. "qb_hoard_0"
WINNER_EXTRA = 0  # e.g. 0 for qb_hoard_0

def test_default_scenario_is_winner():
    assert StrategyParams().scenario == WINNER

def test_vorp_scenario_matches_winner():
    assert VORP_SCENARIO["qb_extra_rostered"] == WINNER_EXTRA
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src uv run pytest tests/test_pointer_default.py -v`
Expected: FAIL (`assert 'qb_hoard_12' == 'qb_hoard_0'`).

- [ ] **Step 3: Switch the pointer at all 6 sites**

Change `StrategyParams.scenario` default to `<WINNER_SCENARIO>`; set `VORP_SCENARIO = {"teams": 12, "qb_extra_rostered": <WINNER_EXTRA>}`; `SCENARIOS_MAIN = ["<WINNER_SCENARIO>"]`; `build_backtest_pools.py`'s `build_pool(conn, "<WINNER_SCENARIO>")`; the draft-assistant snapshot scenario; and `build_valuation.py`'s two validation/print references.

- [ ] **Step 4: Run the pointer test + fail-loud grep assertion**

Run: `PYTHONPATH=src uv run pytest tests/test_pointer_default.py -v`
Expected: PASS.
Run: `grep -rn "qb_hoard_12" src/ scripts/ | grep -v test`
Expected: only *intended* survivors (the `SCENARIOS` dict definition keeping `qb_hoard_12` for rollback, and comments) — no live default pin. Eyeball the list; any unexpected pin is risk R1 surfacing.

- [ ] **Step 5: Commit**

```bash
git add ffi/sim/strategy.py ffi/sim/backtest.py scripts/run_sim_farm.py scripts/build_backtest_pools.py scripts/draft_assistant.py scripts/build_valuation.py tests/test_pointer_default.py
git commit -m "feat(valuation): switch default scenario pointer to <WINNER_SCENARIO> (6 sites); qb_hoard_12 retained for rollback"
```

---

### Task 4: Materialize (if needed), rebuild pools, re-baseline the D7 reference

**Files:**
- Modify (only if `<WINNER_SCENARIO>` is intermediate): `scripts/build_valuation.py` (`SCENARIOS`)

> Skip this task if `<WINNER_SCENARIO>` == `qb_hoard_12`.

- [ ] **Step 1: Materialize the winner's current-season valuation (intermediate ranks only)**

If `<WINNER_SCENARIO>` is `qb_hoard_3/6/9`, add it to `SCENARIOS` in `build_valuation.py`; `qb_hoard_0`/`_24` are already materialized. Then:
Run: `PYTHONPATH=src uv run python scripts/build_valuation.py`
Expected: prints row counts; `<WINNER_SCENARIO>` now present.
Verify vintage consistency (risk R6):
Run: `PYTHONPATH=src uv run python -c "from ffi.db import connect; c=connect().cursor(); c.execute(\"SELECT scenario, count(DISTINCT config_version) FROM valuation.player_value GROUP BY scenario\"); print(c.fetchall())"`
Expected: one `config_version` per scenario, all equal.

- [ ] **Step 2: Rebuild the backtest pools under the new VORP_SCENARIO**

Run: `PYTHONPATH=src uv run python scripts/build_backtest_pools.py`
Expected: per-season rebuild prints; `sim.backtest_pool` now reflects `<WINNER_EXTRA>` QB baseline.

- [ ] **Step 3: Run the D7 gate (informational — expected to move)**

Run: `PYTHONPATH=src uv run python scripts/run_backtests.py --gate 2>&1 | tail -20`
Expected: reports the new composite vs the stale rank-36 reference. Movement here is EXPECTED (intended valuation change), not a failure of the change (design §4).

- [ ] **Step 4: Re-baseline the D7 reference**

Run: `PYTHONPATH=src uv run python scripts/run_backtests.py --reference`
Expected: writes a new `sim.backtest_reference` row as the composite for `<WINNER_SCENARIO>`. Without this, future gates compare to the stale reference (risk R9).

- [ ] **Step 5: Verify the live assistant snapshot advances (risk R2)**

Run: `PYTHONPATH=src uv run python scripts/draft_assistant.py --dry-run 2>&1 | grep -iE "snapshot|scenario|vorp" | head`
Expected: boots against `<WINNER_SCENARIO>` with a snapshot id at/after the rebuild; a live QB's VORP is the lowered value (e.g. no longer ~527 for Dak). (If `--dry-run` is unsupported, load the assistant's `DraftSession` in a REPL and print the scenario + a QB's vorp.)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(valuation): materialize + rebuild pools under <WINNER_SCENARIO>, re-baseline D7 reference"
```

---

### Task 5: Update tests, spot-check, finalize

**Files:**
- Modify: `tests/test_valuation.py` (+ any test asserting the default scenario)
- Modify: `docs/superpowers/specs/2026-07-14-qb-vorp-recalibration-design.md` (decision addendum)

- [ ] **Step 1: Update the affected value/default assertion tests**

`qb_hoard_12`'s value tests (e.g. `r12["QB"] == 36`) **stay** (it's unmutated). Update any test that asserts the *default* scenario is `qb_hoard_12`, and add a value assertion for `<WINNER_SCENARIO>` (its QB replacement rank == `24 + <WINNER_EXTRA>`):

```python
def test_winner_scenario_replacement_rank():
    from ffi.valuation.baseline import compute_replacement_ranks
    ranks = compute_replacement_ranks({"teams": 12, "qb_extra_rostered": WINNER_EXTRA})
    assert ranks["QB"] == 24 + WINNER_EXTRA
```

- [ ] **Step 2: Run the full suite**

Run: `PYTHONPATH=src uv run pytest -q`
Expected: all pass (the 468-test suite plus the new pointer/rank tests). Fix any default-assumption test that legitimately changed; do NOT weaken `qb_hoard_12`'s value tests.

- [ ] **Step 3: Diagnostic spot-check at the chosen rank**

Run: `PYTHONPATH=src uv run python scripts/draft_diagnostic.py --backtest 2024 2>&1 | sed -n '/4. OUR ROSTER/,/breakdown/p'`
Expected: QB1 no longer forced in round 1; the projected-VORP-vs-actual gap for QBs is smaller than the rank-36 baseline; roster still holds a viable QB3 (guardrail sanity).

- [ ] **Step 4: Write the decision addendum to the spec**

Append a `## Decision (implemented)` section to the design doc: chosen rank/scenario, the pooled search table (win% + guardrails), and the before/after D7 composite.

- [ ] **Step 5: Run the D7 health gate once more end-to-end**

Run: `PYTHONPATH=src uv run python scripts/run_backtests.py --gate 2>&1 | tail -5`
Expected: PASS against the *new* reference (confirms the pipeline is internally consistent post-change).

- [ ] **Step 6: Commit**

```bash
git add tests/ docs/superpowers/specs/2026-07-14-qb-vorp-recalibration-design.md
git commit -m "test+docs(valuation): update assertions for <WINNER_SCENARIO> default; spec decision addendum"
```

---

## Self-review

**Spec coverage:** guarded search (Task 1–2) ✓; realistic tuned strategy + `qb_tier_targets` ✓ (Task 1 Step 5); depth + injury-robustness guardrails ✓ (Task 1 helpers, Task 2 criterion); in-memory recompute avoiding pre-materialization ✓ (Task 1); pointer switch at 6 sites ✓ (Task 3); never-mutate-`qb_hoard_12` ✓ (Global Constraints, Task 3 grep); rebuild + re-baseline ✓ (Task 4); test updates ✓ (Task 5); rollback = pointer revert ✓ (implicit — Task 3 is a clean revert). Risk coverage: R1 (Task 3 grep), R2 (Task 4 Step 5), R3 (Task 2 100-seed), R4 (Task 1 tier assert), R6 (Task 4 Step 1 vintage check), R8/R10 (Task 2 Step 3 stop-gates), R9 (Task 4 Step 4), R13 (Task 1 self-test + rank assert). R5/R7 are surfaced (upside metric logged Task 1; projection-reliability is a documented non-goal fallback).

**Placeholder scan:** `<WINNER_SCENARIO>`/`<WINNER_EXTRA>`/`<R>` are intentional parameters resolved by the Task-2 decision, not placeholders — every task states how to resolve them. No TBDs.

**Type consistency:** `repriced_pool`, `retier_qbs`, `injury_robustness`, `qb_vorp_at_rank` names consistent across Tasks 1–2; `StrategyParams(qb_by_round=(2,5,9), qb_tier_targets=(1,2,99))` identical in Task 1 Step 5 and referenced in Task 2; scenario names (`qb_hoard_0/3/6/9/12`) consistent throughout.
