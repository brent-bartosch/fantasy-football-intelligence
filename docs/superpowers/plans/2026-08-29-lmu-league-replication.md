# LMU Still Undefeated — Second League Replication Plan

**Date:** 2026-08-29
**Status:** Draft (for execution)
**Draft deadline:** Thursday 2026-09-03, 17:30 PDT

## 1. Goal

Replicate the draft-day console + in-season management stack for the **LMU Still Undefeated**
league (Yahoo ID 878667) — a **14-team, 1-QB** league — without disturbing the deployed
**NAJEE** (12-team, 2-QB) league. The two leagues share the scoring engine, valuation,
simulation, and (eventually) in-season modules; they differ only in the *league shape* knobs.

## 2. Design decision: parameterize, do NOT fork

We will **not** copy the codebase into an `LMU still undefeated/` folder. Reasons:

- Both trees would connect to the same Postgres `fantasy_football` database, so a code fork
  does **not** isolate data — it creates two divergent code trees fighting over one DB.
- Data isolation already exists in the schema: `scoring.projection_points.config_version`,
  `valuation.player_value.(config_version, scenario)`, `scoring.config.version`. The NAJEE
  and LMU slices live side-by-side keyed by these columns.
- The NAJEE path is golden-tested (661 tests, exact Yahoo 2025 points). Forking would mean
  either duplicating that test surface or silently diverging from it.

**Isolation model (how NAJEE stays untouched):**

| Dimension | NAJEE | LMU |
|---|---|---|
| Scoring config | `config/scoring/v1.json` | `config/scoring/v2.json` |
| Projection points | `config_version = 1` | `config_version = 2` |
| Valuation scenario | `qb_hoard_12` | `lmu_1qb_14` |
| ADP field | `adp_2qb` | `adp_std` |
| Console output | `reports/draft-console.html` | `reports/draft-console-lmu.html` |

All shared code becomes **parameterized** (league profile), never duplicated. Main-league call
sites keep their current behavior via defaults.

## 3. Current state (already done, tests green)

- [x] `config/scoring/v2.json` — LMU rules (identical to v1 except `rush_first_downs`/`rec_first_downs` = 0).
- [x] `src/ffi/scoring/config.py` — `load_config_v2()`, `load_config_by_version()`, `CONFIG_PATHS` registry.
- [x] `src/ffi/valuation/baseline.py` — `compute_replacement_ranks(scenario, starters=None, flex_slots=None)`.
- [x] `src/ffi/valuation/starts.py` — `starts_replacement_ranks(table, teams=12)`.
- [x] `scripts/score_sleeper_projections.py` — `--config-version` flag.
- [x] Full suite: **661 passed, 1 skipped**.

## 4. Phase A — Parameterize the shared engine

Introduce a single **`LeagueProfile`** frozen dataclass (new file
`src/ffi/league_profile.py`, a Layer-0 leaf) as the single source of truth for league shape:

```python
@dataclass(frozen=True)
class LeagueProfile:
    name: str
    config_version: int        # scoring config version
    teams: int
    rounds: int
    starters: dict             # {"QB": 2, ...} vs {"QB": 1, ...}
    bench: int
    ir: int
    scenario: str              # valuation scenario name
    adp_field: str             # 'adp_2qb' vs 'adp_std'
    qb_sanity_min: int         # min QBs in ADP top-30 (8 for 2QB, ~3 for 1QB)

NAJEE = LeagueProfile(name="najee", config_version=1, teams=12, rounds=19,
    starters={"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1},
    bench=8, ir=1, scenario="qb_hoard_12", adp_field="adp_2qb", qb_sanity_min=8)

LMU = LeagueProfile(name="lmu", config_version=2, teams=14, rounds=18,
    starters={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1},
    bench=6, ir=2, scenario="lmu_1qb_14", adp_field="adp_std", qb_sanity_min=3)
```

### A1. `src/ffi/sim/opponent.py`
- Replace module `STARTERS` with profile-driven starters. Thread `starters` into
  `required_picks`, `feasible`, `opponent_pick` (default = NAJEE starters).
- Retune `ROSTER_DAMP` QB thresholds for 1-QB (LMU: no QB hoarding — a 2nd QB is a luxury,
  not a forced starter). Neutralize the 2-QB `pos_need_scale` QB prior for LMU.

### A2. `src/ffi/sim/draft.py`
- Make `TEAMS` / `ROUNDS` / `TOTAL_PICKS` profile-driven (params with defaults), threaded
  through `snake_position`, `_resolve_slot_of_position`, `run_draft`.

### A3. `src/ffi/sim/strategy.py`
- Add `LMU_STRATEGY` (`StrategyParams`) with `qb_by_round` relaxed to a *late* single-QB
  deadline, no early-QB force, `defk_round` unchanged (~14), caps `QB:2 / TE:2`.
- `evaluate_rules` / `rule4_candidates` already take `params`; verify no module-level
  `STARTERS` dependency remains in the rule cascade (it flows through `feasible`).

### A4. `src/ffi/sim/pool.py`
- `build_pool(conn, scenario, *, config_version=None, adp_field="adp_2qb", qb_sanity_min=8)`
  — read config version and ADP field from the profile; relax the 2-QB sanity gate for 1-QB.

### A5. `scripts/estimate_p_starts.py`
- Parameterize `STARTERS` (import from profile, not `sim.opponent`), add `--teams` / `--starter-qb`
  so an LMU `P_start` table can be generated under the 1-QB lineup.

## 5. Phase B — Build LMU data

1. **Score projections under v2:**
   `uv run python scripts/score_sleeper_projections.py --config-version 2`
   → `scoring.projection_points` rows with `config_version=2` (first-down weight = 0).

2. **Generate LMU P(starts) table:**
   `uv run python scripts/estimate_p_starts.py --starter-qb 1 --out data/p_starts_lmu.json`
   (1-QB lineup, byes+injuries). Verify `_meta.mode == "byes+injuries"`.

3. **Build LMU valuation:**
   `scripts/build_valuation.py` gains `--league lmu` (or `--profile lmu`): loads v2 config,
   `lmu_1qb_14` scenario `{teams: 14, qb_extra_rostered: 0}`, starters QB=1, and the LMU
   starts table (`teams=14`). Idempotent DELETE+INSERT keyed by `(config_version=2, scenario)`.

4. **Verify LMU pool:**
   `build_pool(conn, "lmu_1qb_14", config_version=2, adp_field="adp_std", qb_sanity_min=3)`.
   Spot-check the top-25 board: QBs must be **dramatically devalued** vs the 2-QB board
   (top VORP should be RB/WR-heavy, not QB-heavy) — this is the key correctness check.

## 6. Phase C — LMU draft console

- New script `scripts/draft_console_lmu.py` (imports the shared `draft_console` build helpers
  OR is a thin profile-parameterized copy) producing `reports/draft-console-lmu.html`.
- Changes vs the NAJEE console:
  - Roster shape `1QB/2RB/3WR/1TE/1FLEX/1K/1DEF/6BN/2IR` (18 rounds).
  - Slot dropdown 1–14.
  - `LMU_STRATEGY` (no early-QB force) and `lmu_1qb_14` pool + LMU starts table.
  - Golden-trace self-test regenerated under the LMU profile.
- **Opponent-model caveat:** LMU has no draft history, so the self-test trace uses a
  generic/flat 1-QB prior (drift-guard self-consistency is preserved; opponent realism is not,
  which only affects the baked golden trace, not the live recommendation engine).

## 7. Phase D — In-season ops (multi-league aware, greenfield)

This is the ADR 2026-08-30 scope already documented in `ARCHITECTURE.md` §1b. Build **once**,
parameterized by `LeagueProfile`, so both leagues use it. Modules (in dependency order):

- `src/ffi/health.py`, `flags.py`, `joblock.py`, `ingest/gates.py` — infrastructure.
- `src/ffi/league_state/` — manual-capture-first roster/transaction adapter (Yahoo API dead).
- `src/ffi/usage/` — `usage_weekly` build + `trends` + `coldstart` + `market`.
- `src/ffi/waiver/` — `cutline`, `priority` (rolling-waiver pricing), `clear_time`, `ledger`, `cards`.
- `src/ffi/trade_angles.py`, `src/ffi/reports/`, `scripts/notify.py`.

Key LMU differences to carry into these modules:
- **14 teams / 1-QB** → different roster cutline, waiver scarcity, and QB replacement logic.
- **6 weekly acquisitions, unlimited season** → move-budget ledger differs from NAJEE (5/65).
- **2 IR slots** vs NAJEE's 1.

## 8. Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Regressing the golden NAJEE path during parameterization | High | Backward-compatible defaults; full `pytest` + `phase1_report.py` after every phase |
| 1-QB valuation still over-prices QBs (stale 2-QB baseline) | High | Separate `lmu_1qb_14` scenario + `starts_replacement_ranks(teams=14)` + explicit top-25 sanity check |
| Wrong ADP field (2-QB vs standard) | Med | `adp_std` for LMU; the pin/sanity gate announced loudly |
| LMU opponent model absent (no history) | Med | Generic flat prior; scoped to the self-test trace only; documented |
| P(starts) stale for 1-QB | Med | Regenerate under 1-QB lineup; `_meta` mode enforced |
| `draft_console.py` is at its 800-line "do not grow" ceiling | Med | New `draft_console_lmu.py`, do not extend the NAJEE one |

## 9. Validation gates

1. After each phase: `uv run pytest` (must stay 661+ green).
2. After Phase B: `uv run python scripts/phase1_report.py` (health gate) + top-25 LMU board
   sanity (QB not dominant).
3. After Phase C: open `reports/draft-console-lmu.html`, confirm the self-test badge is
   **"✓ self-test N/N"** and the roster panel shows the 1-QB shape.
4. Before Sep 3: rebuild the LMU console fresh the morning of the draft (snapshot + news).

## 10. Task order

```
Phase A (profile + parameterize)  →  Phase B (score v2 → P(starts) → valuation → pool)
                                   →  Phase C (console)  →  Phase D (in-season, multi-league)
```

Phase D is independent of the Sep 3 draft deadline and can proceed after C.
