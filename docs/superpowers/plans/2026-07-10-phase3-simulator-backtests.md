# Phase 3 — Monte Carlo Simulator, Opponent Models & Backtests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A seeded, property-tested Monte Carlo draft simulator (12-team × 19-round snake) whose opponents draft like this league's franchise slots, a nightly sim farm that grids strategy knobs (QB timing is the headline), a 2023–25 backtest harness that becomes the permanent regression suite (ADR D7), and a user-facing strategy-conclusions report.

**Architecture:** Deterministic spine only — every module in the sim path is plain seeded Python/numpy, no model calls (design §3 bright line). Data flows: valuation tables + Sleeper `adp_2qb` + 16-season slot priors → pick models → draft engine → season evaluator (gamma weekly draws / actual weekly points for backtests) → results in `sim` schema tables (ADR D8: results in tables, logs errors-only).

**Tech Stack:** Python 3.11 via `uv`, numpy (new direct dep), scipy (gamma), psycopg2, Postgres 15 (`sim` schema exists since migration 001), pytest + hypothesis, launchd.

## Global Constraints

- **No model calls anywhere in the sim path** (design §3). Strategy logic and pick models are plain Python, seeded and reproducible.
- **Fail-loud everywhere** (user CLAUDE.md + ADR Domain 1): invoke `fail-loud-error-handling` for any try/except; no silent fallbacks; every degraded mode announces itself.
- **`uv` only** — run everything as `uv run python …`; tests as `uv run pytest`.
- **Extend `scripts/phase1_report.py`, never replace** — currently 20 checks; Phase 3 adds checks (Task 13).
- **ADR D7 gate:** any strategy/valuation change must not degrade the 2023–25 backtest composite beyond noise bands once the harness exists (Task 11).
- **ADR D8:** sim results → `sim` schema tables; logs errors-only (farms generate volume). Nightly report carries its own data-vintage line (ADR D5).
- **Near-zero Yahoo API work this phase.** All Yahoo calls via `ffi.yahoo_client.yahoo_call` if ever needed; 999 = stop everything.
- **NO scoring-config bump this phase.** Config v1 is untouched (bonus-EV rework is scorer methodology, not config; DEF mapping is an adapter). The config-v2 preconditions (canonical-JSON compare, RangeTier ascending validator) therefore stay on the debt list — do NOT implement them speculatively (YAGNI), but they are MANDATORY before any future v2.
- **The draft is 19 rounds, 228 picks** — verified from `draft_picks` (2017–2025 all 19 rounds; 11 starters + 8 bench; IR is not drafted). The handoff's "20-round" line is wrong; design docs saying 20 are superseded by the data.
- **Roster legality:** 2 QB / 2 RB / 3 WR / 1 TE / 1 FLEX (W/R/T) / 1 K / 1 DEF + 8 bench = 19 picks.
- **Fantasy regular season = weeks 1–14** (playoffs 15–17); win-rate metrics use 14 weeks.
- **`manager_slot_annotations` is excluded from conftest teardown today.** The FIRST test that WRITES it must add it to the teardown sweep (Task 5 does this).
- Reference `file:line` facts in this plan were verified 2026-07-10 against `main` @ 45edf76.

## Facts pinned during planning (verified live 2026-07-10 — do NOT re-derive, do trust these over the handoff where they conflict)

1. **Sleeper ADP field is `adp_2qb`** (not the handoff's `adp_dd_ppr`, which appears in 0/3292 records). `adp_2qb` is present on 100% of records with **999 = undrafted sentinel**; real values: QB 51, RB 76, WR 101, TE 35 (263 skill players — covers 228 picks). Top of board sane for 2QB (Josh Allen 1.2, B.Robinson 2.4, Maye 3.5). **K and DEF have NO real ADP in any format** (all 999) — opponents must draft K/DEF from priors + projection order.
2. **Kickers are position `PK` in `player_id_xwalk`** (282 rows; there is no `'K'`). `build_valuation.py:41` filters `IN ('QB','RB','WR','TE','K')` → **K has ZERO rows in `valuation.player_value`** despite the script's "K included for completeness" comment. Silent, load-bearing (K verdict = DRAFT EARLY).
3. **`valuation.player_value` / `replacement_baseline` contain stacked duplicates** (3 identical baseline rows per scenario/position; player_value QB 747 = 249 × 3 snapshots). Cause: the idempotent DELETE keys on `(params->>'snapshot_id')` but the morning chain advances the snapshot daily, so prior-snapshot rows persist forever. Any consumer not filtering latest snapshot reads triple rows.
4. **DEF projections currently score into nothing:** DEF `player_ref` is the team abbr (e.g. `'ARI'`), which joins neither `player_id_xwalk` (no DEF rows) nor prices above ~0 (DST stat keys unmapped — the T7 carry-forward). Sleeper DEF season keys observed: `int, sack, fum_rec, blk_kick, pass_int_td, pts_allow_0, yds_allow_0_100, gp, pts_std, pts_ppr, pts_half_ppr, adp_*` (enumerate the full union in Task 3 — more bucket keys likely exist on other teams).
5. **Snapshots:** `raw.sleeper_projections` ids 1 (2025 wk5), 2 (2026 wk1), 3–6 (2026 season-level). Latest season snapshot = `max(snapshot_id) WHERE week IS NULL`.
6. Mining functions to reuse (never rewrite): `ffi/history/mining.py` — `position_round_tendencies(conn)` returns `[{slot, band('R1-3'|'R4-8'|'R9+'), position, picks}]`; `qb_timing_by_slot(conn)` returns `[{slot, qb1_round, qb2_round, qb3_round, seasons}]`. Priors additionally need per-round rows — new SQL in Task 5 follows the same join pattern (`draft_picks dp JOIN teams t ON t.team_id=dp.team_id JOIN players p ON p.player_id=dp.player_id JOIN raw.yahoo_league_settings s ON s.league_key=dp.league_id`).
7. Actual weekly league-scored points for backtests: `scoring.player_week_points` (`source='nflverse'`, `player_ref`=gsis_id, 2019–2025, config_version=1, includes K). **DEF exists only under `source='yahoo_engine'` (2025)** — backtests neutralize DEF (Task 11, disclosed).
8. Baselines/valuation code: `compute_replacement_ranks(scenario)->{pos:rank}` and `compute_baselines(points_by_pos, ranks)->{pos:pts}` in `ffi/valuation/baseline.py` (STARTERS includes K:1, DEF:1; flex share 0.5/0.4/0.1); `gmm_tiers(values, max_k=9)->list[int]` in `ffi/valuation/tiers.py` (raises on <4 values).
9. Weekly-variance infrastructure: `ffi/scoring/bonus_pricing.py` — `weekly_threshold_prob(mean_weekly, cv, threshold)`, `bonus_ev_per_week(mean_weekly, cv, tiers)`, `estimate_weekly_cv(conn, seasons, min_weeks=8)` returning `{"players": {gsis: {stat: cv}}, "positions": {pos: {stat: cv}}}` with stat keys `rush_yards|rec_yards|pass_yards`. Known gap: `mean<=0` short-circuits BEFORE cv validation (Task 2 fixes).
10. Morning launchd chain (`launchd/com.ffi.morning.plist`) runs 07:00: backup → sleeper ingest → FP daily → score → valuation → briefing. The sim farm gets its OWN plist (02:30) — never extend the morning chain with hours of sim work.
11. Season-horizon engine scoring awards yardage bonuses ONCE on season totals (`score_components` bonuses loop) — understates weekly threshold-bonus EV systematically for volume players. Task 2 replaces it for season projections with calibrated gamma weekly EV.

---

### Task 1: Debt batch (three small carried-forward items)

**Files:**
- Modify: `tests/test_yahoo_client.py`
- Modify: `tests/test_valuation.py`
- Modify: `scripts/make_golden_fixtures.py`

**Interfaces:**
- Consumes: `ffi.yahoo_client` (existing `yahoo_call`, `YahooAuthError`), `ffi.valuation.tiers.gmm_tiers`, `ffi.valuation.baseline.compute_baselines`
- Produces: nothing new — tests + a deterministic fixture query only.

- [ ] **Step 1: Read the three touch-points**

Read `src/ffi/yahoo_client.py` (find where `requests.exceptions.RequestException` is translated to `YahooAuthError` — Phase 2 Task 1 Minor), `tests/test_valuation.py`, and `scripts/make_golden_fixtures.py` (find its fixture-selection `ORDER BY`).

- [ ] **Step 2: Write the failing tests**

In `tests/test_yahoo_client.py` add (adapt the module's existing mocking style — it already stubs the OAuth session):

```python
def test_request_exception_becomes_yahoo_auth_error(monkeypatch):
    """Phase 2 Task 1 Minor: network-level RequestException must surface as
    the domain error, not leak requests internals."""
    import requests
    from ffi import yahoo_client

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("dns down")

    # patch at the same seam existing tests use for the session call
    monkeypatch.setattr(yahoo_client, "_session_get", boom, raising=False)
    with pytest.raises(yahoo_client.YahooAuthError):
        yahoo_client.yahoo_call("league/461.l.326814/settings")
```

(If the seam name differs, patch whatever `yahoo_call` actually invokes — read the module first; the assertion is the contract: RequestException in → YahooAuthError out.)

In `tests/test_valuation.py` add:

```python
def test_gmm_tiers_rejects_fewer_than_four_values():
    with pytest.raises(ValueError, match="need >=4 values"):
        gmm_tiers([300.0, 200.0, 100.0])


def test_compute_baselines_rejects_unsorted_pool():
    with pytest.raises(ValueError, match="sorted descending"):
        compute_baselines({"QB": [100.0, 300.0, 200.0]}, {"QB": 2})
```

- [ ] **Step 3: Run them** — `uv run pytest tests/test_yahoo_client.py tests/test_valuation.py -v`. The two guard-branch tests should PASS immediately (the guards exist, they were just untested — that's fine, commit them as regression pins). The RequestException test may PASS or FAIL depending on whether the translation exists; if it FAILS because the translation is genuinely missing, add it in `yahoo_client.py` at the call site (`except requests.exceptions.RequestException as e: raise YahooAuthError(f"network failure calling yahoo: {e}") from e`).

- [ ] **Step 4: Fix the fixture-regeneration tiebreaker**

In `scripts/make_golden_fixtures.py`, find the fixture-selection query and append a full tiebreaker so regeneration is deterministic, e.g. `ORDER BY <existing keys>, yahoo_player_id, week` — every selected column set must be totally ordered. Do NOT regenerate fixtures in this task (regeneration only happens when fixtures change for a real reason).

- [ ] **Step 5: Full suite green** — `uv run pytest` → 158 + 3 new passing.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "test: debt batch — RequestException->YahooAuthError pin, gmm/baseline guard tests, golden-fixture ORDER BY tiebreaker"`

---

### Task 2: Weekly bonus-EV wiring + valuation hygiene (PK→K, duplicate purge)

**Decision (made deliberately, per handoff §4):** season-horizon projection scoring switches its `bonuses` component from one-shot-on-season-totals to calibrated gamma weekly EV (`bonus_ev_per_week × 17`). Rationale: the league pays threshold bonuses weekly; season-total awarding gives a 1,200-yard back ONE 100-yd bonus instead of ~17 weekly chances — a systematic understatement that biases against exactly the volume players this league's rules favor, and the sim/backtests would inherit it. The gamma pricer is already calibrated (Brier 0.0212 vs 0.0259, 48k out-of-sample obs). Weekly-actuals scoring (golden gate) is UNTOUCHED.

**Files:**
- Modify: `src/ffi/scoring/bonus_pricing.py` (validation-order fix)
- Create: `src/ffi/scoring/projection_bonus.py`
- Modify: `scripts/score_sleeper_projections.py`
- Modify: `scripts/build_valuation.py` (PK→K, duplicate purge)
- Create: `tests/test_projection_bonus.py`
- Create: `docs/research/2026-07-XX-bonus-ev-valuation-diff.md` (generated evidence, date = execution date)

**Interfaces:**
- Consumes: `bonus_ev_per_week(mean_weekly: float, cv: float, tiers: list[BonusTier]) -> float`; `estimate_weekly_cv(conn, seasons: list[int]) -> dict`; `StatLine`; `ScoringConfig.offense.yardage_bonuses: dict[str, list[BonusTier]]` (keys are StatLine field names `rush_yards|rec_yards|pass_yards` — same keys as `estimate_weekly_cv` output).
- Produces: `season_bonus_ev(line: StatLine, cfg: ScoringConfig, cv: dict, position: str, gsis_id: str | None) -> float` in `ffi/scoring/projection_bonus.py` with module constant `PROJ_WEEKS = 17.0`. Later tasks (4, 9) rely on `scoring.projection_points` rows carrying `components->>'bonus_model' = 'weekly_gamma_v1'` for season horizon, and on `valuation.player_value` having position `'K'` rows and NO stacked duplicates.

- [ ] **Step 1: Fix the guard-order gap in `bonus_pricing.py`** (load-bearing the moment the sim calls with arbitrary inputs — handoff §4):

```python
def weekly_threshold_prob(mean_weekly: float, cv: float, threshold: float) -> float:
    if cv <= 0:
        raise ValueError(f"cv must be positive, got {cv}")
    if mean_weekly <= 0:
        return 0.0
    ...

def bonus_ev_per_week(mean_weekly: float, cv: float, tiers: list[BonusTier]) -> float:
    if cv <= 0:
        raise ValueError(f"cv must be positive, got {cv}")
    if mean_weekly <= 0:
        return 0.0
    ...
```

- [ ] **Step 2: Write failing tests** in `tests/test_projection_bonus.py`:

```python
import pytest
from ffi.scoring.bonus_pricing import bonus_ev_per_week, weekly_threshold_prob
from ffi.scoring.config import BonusTier, load_config_v1
from ffi.scoring.projection_bonus import PROJ_WEEKS, season_bonus_ev
from ffi.scoring.statline import StatLine

CV = {"players": {"g1": {"rush_yards": 0.5}}, "positions": {"RB": {"rush_yards": 0.6, "rec_yards": 0.9}, "WR": {"rec_yards": 0.7}}}


def test_cv_validated_even_when_mean_nonpositive():
    with pytest.raises(ValueError, match="cv must be positive"):
        weekly_threshold_prob(0.0, -1.0, 100.0)
    with pytest.raises(ValueError, match="cv must be positive"):
        bonus_ev_per_week(-5.0, 0.0, [BonusTier(threshold=100, points=1)])


def test_season_bonus_ev_beats_one_shot_for_volume_back():
    cfg = load_config_v1()
    line = StatLine(rush_yards=1200.0)
    ev = season_bonus_ev(line, cfg, CV, "RB", "g1")
    # one-shot awards 100+150+200 crossings once; weekly EV must exceed it
    one_shot = sum(t.points for tiers in [cfg.offense.yardage_bonuses["rush_yards"]] for t in tiers if 1200 >= t.threshold)
    assert ev > one_shot


def test_season_bonus_ev_uses_player_cv_over_position_cv():
    cfg = load_config_v1()
    line = StatLine(rush_yards=1200.0)
    assert season_bonus_ev(line, cfg, CV, "RB", "g1") != season_bonus_ev(line, cfg, CV, "RB", None)


def test_season_bonus_ev_fails_loud_on_missing_cv():
    cfg = load_config_v1()
    with pytest.raises(ValueError, match="no weekly CV"):
        season_bonus_ev(StatLine(pass_yards=4000.0), cfg, CV, "RB", None)


def test_zero_yardage_prices_zero():
    cfg = load_config_v1()
    assert season_bonus_ev(StatLine(), cfg, CV, "RB", "g1") == 0.0
```

Run: `uv run pytest tests/test_projection_bonus.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `src/ffi/scoring/projection_bonus.py`**

```python
"""Weekly threshold-bonus EV for SEASON-horizon projections (R16 refinement,
Phase 3 Task 2). The engine run on a season stat line awards each yardage
bonus at most once, but the league pays them EVERY WEEK. Season scoring
replaces that component with sum-over-weeks gamma-priced EV (calibrated:
Brier 0.0212 vs 0.0259 mean-pricing, Phase 2 Task 9)."""
from ffi.scoring.bonus_pricing import bonus_ev_per_week
from ffi.scoring.config import ScoringConfig
from ffi.scoring.statline import StatLine

PROJ_WEEKS = 17.0  # NFL regular-season games projected (bye already excluded)


def season_bonus_ev(
    line: StatLine, cfg: ScoringConfig, cv: dict, position: str, gsis_id: str | None
) -> float:
    total = 0.0
    for field, tiers in cfg.offense.yardage_bonuses.items():
        season_yards = getattr(line, field)
        if season_yards is None or season_yards <= 0:
            continue
        player_cv = cv["players"].get(gsis_id, {}).get(field) if gsis_id else None
        stat_cv = player_cv or cv["positions"].get(position, {}).get(field)
        if stat_cv is None:
            raise ValueError(
                f"no weekly CV for {position}/{field} — is nflverse history loaded?"
            )
        total += bonus_ev_per_week(season_yards / PROJ_WEEKS, stat_cv, tiers) * PROJ_WEEKS
    return total
```

- [ ] **Step 4: Tests pass** — `uv run pytest tests/test_projection_bonus.py -v`

- [ ] **Step 5: Wire into `scripts/score_sleeper_projections.py`**

After `comps = score_components(line, cfg)` (line ~88), for season horizon + FD-imputed positions only:

```python
from ffi.scoring.bonus_pricing import estimate_weekly_cv
from ffi.scoring.projection_bonus import season_bonus_ev
...
cv = estimate_weekly_cv(conn, seasons=_FD_FIT_SEASONS)   # once, near fd_rates fit
...
    comps = score_components(line, cfg)
    if week is None and pos in _FD_IMPUTED_POSITIONS:
        comps["bonuses"] = Decimal(repr(round(season_bonus_ev(line, cfg, cv, pos, gsis_id), 4)))
        bonus_model = "weekly_gamma_v1"
    else:
        bonus_model = None
    points = sum(comps.values())
    comps_out = {k: str(v) for k, v in comps.items()}
    if bonus_model:
        comps_out["bonus_model"] = bonus_model
```

(`gsis_id` is already computed in the FD-imputation block — hoist it so both uses share it. `Decimal`/`repr` matches the engine's exact-arithmetic convention.)

- [ ] **Step 6: Fix `build_valuation.py`** — three changes:

1. PK→K: the pool query's position filter and mapping become

```sql
AND x.position IN ('QB','RB','WR','TE','K','PK')
```

with Python-side `pos = 'K' if pos == 'PK' else pos` before `by_pos.setdefault(...)`.

2. Duplicate purge — the DELETEs drop the snapshot predicate so each rebuild fully replaces the (config, scenario) slice (valuation is the CURRENT view; history is recomputable from raw):

```python
cur.execute(
    "DELETE FROM valuation.replacement_baseline WHERE config_version=%s AND scenario=%s",
    (cfg.version, scen_name),
)
cur.execute(
    "DELETE FROM valuation.player_value WHERE config_version=%s AND scenario=%s",
    (cfg.version, scen_name),
)
```

3. Add a post-build assertion (fail-loud):

```python
with conn.cursor() as cur:
    cur.execute(
        """SELECT count(*) FROM (SELECT xwalk_id, scenario FROM valuation.player_value
           WHERE config_version=%s GROUP BY 1,2 HAVING count(*) > 1) d""",
        (cfg.version,),
    )
    dups = cur.fetchone()[0]
    if dups:
        raise SystemExit(f"valuation.player_value has {dups} duplicated (player, scenario) rows after rebuild")
    cur.execute("SELECT count(*) FROM valuation.player_value WHERE config_version=%s AND position='K' AND scenario='qb_hoard_12'", (cfg.version,))
    if cur.fetchone()[0] < 20:
        raise SystemExit("K missing from valuation — PK mapping regressed")
```

- [ ] **Step 7: Rescore + rebuild live, capture the before/after evidence**

```bash
psql fantasy_football -c "SELECT x.name, v.position, round(v.vorp,1) FROM valuation.player_value v JOIN public.player_id_xwalk x USING (xwalk_id) WHERE v.scenario='qb_hoard_12' ORDER BY v.vorp DESC LIMIT 25" > /tmp/top25_before.txt
uv run python scripts/score_sleeper_projections.py
uv run python scripts/build_valuation.py
psql fantasy_football -c "<same query>" > /tmp/top25_after.txt
```

Write `docs/research/<exec-date>-bonus-ev-valuation-diff.md`: both top-25 tables, count of rank moves ≥3, per-position mean bonus-component delta (SQL over `components->>'bonuses'`), and one paragraph interpreting direction (expect volume RB/WR up vs QB — pass bonuses at 300/400/500 are weekly-rare). If the top-25 is UNCHANGED, that is itself a finding — report it, don't force a narrative.

- [ ] **Step 8: Full suite + health** — `uv run pytest` green; `uv run python scripts/phase1_report.py` still 20/20 (the valuation check is `count >= 100`, purge keeps thousands).

- [ ] **Step 9: Commit** — `git add -A && git commit -m "feat: weekly gamma bonus EV for season projections; fix K/PK valuation gap + stacked-duplicate purge"`

---

### Task 3: Sleeper DST tier semantics (T7 carry-forward) + DEF projection scoring + DEF valuation

**Files:**
- Create: `src/ffi/scoring/def_projection.py`
- Create: `scripts/verify_dst_semantics.py` (one-shot verification protocol, kept for re-runs)
- Create: `tests/test_def_projection.py`
- Modify: `scripts/score_sleeper_projections.py` (DEF branch)
- Modify: `scripts/build_valuation.py` (include DEF)
- Create: `docs/research/2026-07-XX-dst-semantics.md` (verification evidence)
- Modify: `migrations/005_sim.sql` — NO: DEF xwalk rows are data, not DDL; they're inserted by `scripts/build_crosswalk.py`-style idempotent block inside `scripts/verify_dst_semantics.py` Step 4 below.

**Interfaces:**
- Consumes: `public.team_def_map(yahoo_def_id, team_abbr, team_name)` (32 rows); latest season snapshot payload; `scoring.player_week_points` `source='yahoo_engine'` 2025 DEF rows (`player_ref` = numeric yahoo def id) with `components` JSONB; `ScoringConfig.defense` (weights + `points_allowed_tiers`/`yards_allowed_tiers` as `RangeTier(max, points)` lists); `ffi.scoring.yahoo_adapter` (DEF stat-line construction for the uplift fit).
- Produces:
  - `def_projection_points(stats: dict, cfg: ScoringConfig, uplift_per_week: float, games: float = 17.0) -> tuple[float, dict]` in `ffi/scoring/def_projection.py` — returns (season points, components dict).
  - `fit_def_uplift(conn, cfg) -> float` (league-mean weekly points from categories Sleeper does not project, fitted on 2025 `yahoo_engine` DEF actuals).
  - 32 DEF rows in `public.player_id_xwalk` (`position='DEF'`, `sleeper_id=team_abbr`, `yahoo_id=yahoo_def_id`, `manual_override=true`).
  - DEF rows in `scoring.projection_points` (season horizon) and `valuation.player_value`/`replacement_baseline` (DEF replacement rank 12).

- [ ] **Step 1: Enumerate the full DST key union (live verification, R16 discipline)**

```bash
psql fantasy_football -tA -c "
WITH latest AS (SELECT payload::jsonb p FROM raw.sleeper_projections WHERE week IS NULL ORDER BY snapshot_id DESC LIMIT 1),
recs AS (SELECT jsonb_array_elements(p) rec FROM latest)
SELECT jsonb_object_keys(rec->'stats') k, count(*) FROM recs
WHERE rec->'player'->>'position'='DEF' GROUP BY 1 ORDER BY 1;"
```

Record every key + coverage in the research doc. Expected families: counting stats (`sack`, `int`, `fum_rec`, `blk_kick`, `pass_int_td`, maybe `safe`, `def_td`, `ff`), bucket counts (`pts_allow_*`, `yds_allow_*`), metadata (`gp`, `pts_*`, `adp_*`).

- [ ] **Step 2: Verify bucket semantics = expected GAME COUNTS**

Protocol (in `scripts/verify_dst_semantics.py`): reconstruct Sleeper's own `pts_std` for each DEF from its stat keys under Sleeper's documented standard DEF scoring (sack 1, int 2, fum_rec 2, blk_kick 2, def TD 6, safety 2, pts_allow buckets 10/7/4/1/0/−1/−4). If `abs(reconstructed − pts_std) / pts_std < 0.05` for ≥ 28/32 teams, semantics CONFIRMED (buckets are projected games-in-bucket counts; counting stats are season totals). If reconstruction fails, STOP — print the residuals, write the doc with status BLOCKED, and do not guess (a wrong guess silently corrupts DST scores — handoff §2). The script prints a table and exits nonzero on failure.

- [ ] **Step 3: Fit the enhanced-stat uplift**

Sleeper does not project the league's enhanced DEF categories (TFL, 3-and-outs, 4th-down stops, return yards, XPR…). Fit `fit_def_uplift(conn, cfg)`: for 2025 DEF player-weeks, build each week's StatLine via the existing yahoo adapter from `raw.yahoo_player_week`, zero out the Sleeper-covered fields (`sacks, def_interceptions, fumble_recoveries, blocked_kicks, defensive_tds, safeties, points_allowed, yards_allowed`), score the residual line with `score_components`, and return the league-mean weekly residual (one float). This is the disclosed constant added per projected week.

- [ ] **Step 4: Failing tests, then implement `def_projection.py`**

```python
def test_def_projection_maps_buckets_to_league_tiers():
    cfg = load_config_v1()
    stats = {"sack": 40.0, "int": 12.0, "fum_rec": 8.0, "blk_kick": 1.0,
             "pts_allow_0": 1.0, "pts_allow_1_6": 2.0, "pts_allow_7_13": 5.0,
             "pts_allow_14_20": 5.0, "pts_allow_21_27": 3.0, "pts_allow_28_34": 1.0,
             "yds_allow_300_349": 6.0, "gp": 17.0, "adp_2qb": 999.0}
    pts, comps = def_projection_points(stats, cfg, uplift_per_week=3.0)
    assert pts > 0
    assert comps["uplift"] == pytest.approx(3.0 * 17)
    # tier component = sum(count * league tier points for the bucket's range)
    assert "pts_allow_tiers" in comps and "counting" in comps


def test_def_projection_fails_loud_on_unknown_stat_key():
    cfg = load_config_v1()
    with pytest.raises(ValueError, match="unmapped DEF stat key"):
        def_projection_points({"brand_new_key": 1.0}, cfg, uplift_per_week=0.0)
```

Implementation contract: an explicit key map (counting stat → league weight field; bucket key → representative value fed to the engine's `_tier_points` against `cfg.defense.points_allowed_tiers`/`yards_allowed_tiers`; metadata keys → ignored list). ANY key outside the map raises `ValueError("unmapped DEF stat key: …")` — drift-proof by construction. Season points = Σ counting×weight + Σ bucket_count×tier_points + uplift×17.

- [ ] **Step 5: Insert DEF crosswalk rows (idempotent)**

```sql
INSERT INTO public.player_id_xwalk (name, position, team, sleeper_id, yahoo_id, manual_override)
SELECT m.team_name || ' DEF', 'DEF', m.team_abbr, m.team_abbr, m.yahoo_def_id, true
FROM public.team_def_map m
WHERE NOT EXISTS (SELECT 1 FROM public.player_id_xwalk x WHERE x.position='DEF' AND x.sleeper_id = m.team_abbr);
```

- [ ] **Step 6: Wire DEF into `score_sleeper_projections.py`** — a DEF branch beside the skill-position path: `points, comps = def_projection_points(stats, cfg, uplift)` (uplift fitted once per run), rows keyed `player_ref = rec["player_id"]` (the team abbr). And into `build_valuation.py`: position filter gains `'DEF'` (joins now that xwalk has DEF rows); remove the "DEF absent in v1" rank filter so DEF baseline (rank 12) computes; keep the loud print if DEF pool < 25.

- [ ] **Step 7: Sanity vs 2025 ground truth** — final section of `verify_dst_semantics.py`: Spearman rank-correlation between 2026 projected DEF season points and 2025 actual DEF season league points (yahoo_engine). Print it; require > 0.3 (weak is fine — year-to-year DEF is noisy; NEGATIVE or ~0 means a mapping bug). Write `docs/research/<date>-dst-semantics.md` with key census, reconstruction table, uplift value, correlation.

- [ ] **Step 8: Rescore + rebuild + suite + commit** — `uv run python scripts/score_sleeper_projections.py && uv run python scripts/build_valuation.py && uv run pytest` → commit `"feat: DST tier semantics verified; DEF projection scoring + DEF valuation live"`.

---

### Task 4: Migration 005 (sim tables) + draftable-pool builder

**Files:**
- Create: `migrations/005_sim.sql`
- Create: `src/ffi/sim/__init__.py` (empty)
- Create: `src/ffi/sim/pool.py`
- Create: `tests/test_sim_pool.py`

**Interfaces:**
- Consumes: `valuation.player_value` (deduped, K+DEF included), `raw.sleeper_projections` latest season payload (`stats.adp_2qb`, 999 sentinel), `public.player_id_xwalk`.
- Produces:

```python
@dataclass(frozen=True)
class PoolPlayer:
    ref: str            # sleeper_id (skill/K) or team abbr (DEF) — unique in pool
    name: str
    position: str       # QB RB WR TE K DEF
    proj_points: float
    vorp: float
    tier: int
    adp: float | None   # adp_2qb when < 999, else None
    gsis_id: str | None # for backtest/actuals joins

def build_pool(conn, scenario: str) -> list[PoolPlayer]   # sorted: real ADP asc, then vorp desc
```

and DDL:

```sql
-- migrations/005_sim.sql — Phase 3: simulator results (ADR D8: results in tables, logs errors-only)
CREATE TABLE IF NOT EXISTS sim.batches (
    batch_id    SERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('farm','backtest')),
    git_sha     TEXT,
    config_version INTEGER NOT NULL,
    scenario    TEXT NOT NULL,
    season      INTEGER,                -- backtest year; NULL for farm (2026 pool)
    strategy    JSONB NOT NULL,         -- StrategyParams dump
    opponent_params JSONB NOT NULL,     -- tau, half_life, damp table, priors floor seasons
    n_drafts    INTEGER NOT NULL,
    seasons_per_draft INTEGER NOT NULL,
    base_seed   BIGINT NOT NULL,
    data_vintage JSONB NOT NULL,        -- {snapshot_id, snapshot_fetched_at, valuation_computed_at, priors_latest_season, degraded: bool}
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS sim.batch_results (
    batch_id    INTEGER NOT NULL REFERENCES sim.batches(batch_id) ON DELETE CASCADE,
    metric      TEXT NOT NULL,          -- 'all_play_pct','all_play_se','top3_rate','qb1_round_mean',...
    value       NUMERIC NOT NULL,
    PRIMARY KEY (batch_id, metric)
);
CREATE TABLE IF NOT EXISTS sim.sample_drafts (
    batch_id    INTEGER NOT NULL REFERENCES sim.batches(batch_id) ON DELETE CASCADE,
    draft_seed  BIGINT NOT NULL,
    reason      TEXT NOT NULL CHECK (reason IN ('worst','best','random')),
    our_position INTEGER NOT NULL,
    all_play_pct NUMERIC NOT NULL,
    picks       JSONB NOT NULL,         -- [{overall, slot, pos, ref, name}] x228
    our_roster  JSONB NOT NULL,
    PRIMARY KEY (batch_id, draft_seed, reason)
);
CREATE TABLE IF NOT EXISTS raw.backtest_sources (
    source      TEXT NOT NULL,          -- 'dynastyprocess','wayback_fp',...
    season      INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('adp','projections','ecr')),
    url         TEXT NOT NULL,
    payload     JSONB NOT NULL,
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, season, kind)
);
CREATE TABLE IF NOT EXISTS sim.backtest_pool (
    season      INTEGER NOT NULL,
    ref         TEXT NOT NULL,          -- gsis_id (actuals join key)
    name        TEXT NOT NULL,
    position    TEXT NOT NULL,
    proj_points NUMERIC NOT NULL,
    vorp        NUMERIC NOT NULL,
    tier        INTEGER NOT NULL,
    adp         NUMERIC,
    degraded    BOOLEAN NOT NULL DEFAULT false,   -- synthetic projections (R11 fallback)
    provenance  JSONB NOT NULL,
    PRIMARY KEY (season, ref)
);
CREATE TABLE IF NOT EXISTS sim.backtest_reference (
    ref_id      SERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    git_sha     TEXT,
    description TEXT NOT NULL,
    composite   NUMERIC NOT NULL,       -- mean our-seat all-play% over reference cells
    band        NUMERIC NOT NULL,       -- 2*SE noise band (ADR D7 gate)
    detail      JSONB NOT NULL,         -- per-cell values
    is_active   BOOLEAN NOT NULL DEFAULT true
);
```

- [ ] **Step 1: Add numpy as a direct dependency** (`uv add numpy` — it is already transitively present via scipy/sklearn, but Tasks 6–9 import it directly), then write the migration exactly as above; run `psql fantasy_football -f migrations/005_sim.sql` (conftest picks it up automatically — sorted glob).

- [ ] **Step 2: Failing tests** in `tests/test_sim_pool.py` (uses the `db` fixture; seed minimal xwalk + valuation + snapshot fixture rows in-test):

```python
def test_pool_maps_sentinel_adp_to_none(db): ...
    # insert 1 xwalk row + 1 player_value row + tiny snapshot payload with adp_2qb=999
    # assert pool[0].adp is None
def test_pool_orders_real_adp_before_none(db): ...
def test_pool_fails_loud_when_positions_missing(db):
    with pytest.raises(ValueError, match="pool missing positions"):
        build_pool(conn, "qb_hoard_12")   # seeded with QBs only
def test_pool_rejects_duplicate_refs(db): ...
```

- [ ] **Step 3: Implement `pool.py`.** Query: `valuation.player_value` (given scenario, `config_version = load_config_v1().version`) joined to xwalk for `sleeper_id/name/position/gsis_id` (map `PK→K` if it leaks through; DEF joins the same way now); ADP from the latest season snapshot payload in one pass (`jsonb_array_elements`, `(stats->>'adp_2qb')::float`, `< 999` else NULL) keyed by `player_id`. Validation gates (fail loud, `ValueError`): all six positions present; ≥ 200 players with real ADP; ≥ 8 QBs in the top 30 by ADP (2QB sanity); ≥ 25 DEF and ≥ 25 K rows; no duplicate refs. Sorted real-ADP-asc then vorp-desc.

- [ ] **Step 4: Tests pass; live smoke** — `uv run python -c "from ffi.db import connect; from ffi.sim.pool import build_pool; p = build_pool(connect(), 'qb_hoard_12'); print(len(p), p[0])"` → expect ~1,900+ players, Josh Allen-ish first.

- [ ] **Step 5: Commit** — `"feat: sim schema (005) + draftable pool builder with ADP sentinel handling"`

---

### Task 5: Opponent priors from 16-season history (annotation-aware)

**Files:**
- Create: `src/ffi/sim/priors.py`
- Create: `tests/test_priors.py`
- Modify: `tests/conftest.py` (add `manager_slot_annotations` to teardown — MANDATORY, this task's tests write it)

**Interfaces:**
- Consumes: `draft_picks`/`teams`/`players`/`raw.yahoo_league_settings` join (fact #6), `public.manager_slot_annotations(league_slot, human_label, from_season, to_season, note)`.
- Produces:

```python
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
HALF_LIFE = 4.0     # seasons; weight = 0.5 ** ((latest - season) / HALF_LIFE)
SHRINK_M = 8.0      # pseudo-picks of band-share blended into round-level share

@dataclass(frozen=True)
class SlotPriors:
    latest_season: int
    pos_share: dict          # (slot:int, round:int) -> {pos: float}  (sums to 1)
    params: dict             # provenance: half_life, shrink_m, floors, n_picks_used

def build_slot_priors(conn) -> SlotPriors
def _pos_share_from_rows(rows, floors, latest_season) -> dict   # pure, unit-tested
```

- [ ] **Step 1: conftest teardown FIRST** (handoff §6 gotcha): in `tests/conftest.py`, extend the public truncate:

```python
cur.execute(
    "TRUNCATE public.player_id_xwalk, public.matchup_results, "
    "public.manager_slot_annotations RESTART IDENTITY CASCADE"
)
```

Note: the migration re-seeds slot 12 idempotently on next fixture setup, so truncation is safe.

- [ ] **Step 2: Failing tests** (pure function — feed synthetic rows `(slot, season, round, position)`):

```python
def test_recency_weighting_prefers_recent_seasons():
    # slot 1 drafted QB round 1 in 2010-2017, WR round 1 in 2022-2025
    rows = [(1, s, 1, "QB") for s in range(2010, 2018)] + [(1, s, 1, "WR") for s in range(2022, 2026)]
    share = _pos_share_from_rows(rows, floors={}, latest_season=2025)
    assert share[(1, 1)]["WR"] > share[(1, 1)]["QB"]

def test_annotation_floor_excludes_prior_human_seasons():
    rows = [(12, s, 1, "RB") for s in range(2010, 2022)] + [(12, s, 1, "QB") for s in range(2022, 2026)]
    share = _pos_share_from_rows(rows, floors={12: 2022}, latest_season=2025)
    assert share[(12, 1)].get("RB", 0.0) < 0.05   # only band-shrinkage mass remains

def test_shares_sum_to_one_and_cover_all_rounds():
    ...  # every (slot, round 1..19) key sums to ~1.0

def test_unknown_position_fails_loud():
    with pytest.raises(ValueError, match="unexpected position"):
        _pos_share_from_rows([(1, 2025, 1, "W/R")], floors={}, latest_season=2025)
```

- [ ] **Step 3: Implement.** SQL reader:

```sql
SELECT t.slot, s.season, dp.round_number, p.position
FROM draft_picks dp
JOIN teams t ON t.team_id = dp.team_id
JOIN players p ON p.player_id = dp.player_id
JOIN raw.yahoo_league_settings s ON s.league_key = dp.league_id
WHERE dp.round_number BETWEEN 1 AND 19
```

Floors: `SELECT league_slot, max(from_season) FROM public.manager_slot_annotations GROUP BY league_slot` — but ONLY treat a floor as a human-change cut when there are ≥ 2 annotation rows for the slot or `from_season > 2010` (the seeded slot-12 row means: Brent from 2022 — seasons before 2022 for slot 12 belong to the previous human and are excluded). Pure builder: drop rows below floor; weight `w = 0.5 ** ((latest − season)/HALF_LIFE)`; band shares per (slot, band) then round-level shrinkage `share(pos | slot, round) = (Σw·[pos at round] + M·band_share) / (Σw at round + M)`; positions outside `POSITIONS` raise. When the user's turnover annotation lands (pending input #1), rebuilding priors picks it up with zero code change — note this in the module docstring.

- [ ] **Step 4: Statistical pin against mining ground truth** (integration test, live DB guard-skipped in CI-less envs is NOT allowed — the suite runs against the self-bootstrapped test DB, so seed from a fixture dump of real draft_picks? No: keep it a LIVE-DB script check instead): add to `tests/test_priors.py` a pure-data test that league-wide weighted QB share in rounds 1–2 exceeds RB and WR shares when fed the real distribution shape (synthetic rows mimicking §3 of the mining report), and defer the true live-number check to the sim-farm assumption audit (Task 12 report compares sim QB1 round vs the historical 1.83).

- [ ] **Step 5: Suite green; commit** — `"feat: recency-weighted, annotation-aware opponent slot priors (+ manager_slot_annotations teardown)"`

---

### Task 6: Opponent pick model (two-stage: position from priors, player by ADP softmax)

**Files:**
- Create: `src/ffi/sim/opponent.py`
- Create: `tests/test_opponent.py`

**Interfaces:**
- Consumes: `SlotPriors.pos_share`, `PoolPlayer`, `numpy.random.Generator`.
- Produces:

```python
TAU = 1.8                     # softmax temperature over within-position candidate index
CAND_WINDOW = 12              # only the top-N available at a position are pickable
ROSTER_DAMP = {               # multiplier applied for the HIGHEST crossed count threshold
    "QB": {3: 0.15, 4: 0.0}, "TE": {2: 0.3, 4: 0.0},
    "K": {1: 0.02, 2: 0.0}, "DEF": {1: 0.02, 2: 0.0},
}
STARTERS = {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}   # + 1 FLEX (RB/WR/TE)

def required_picks(counts: dict[str, int]) -> int          # starter slots still unfillable
def feasible(counts, pos, picks_left_after: int) -> bool
def opponent_pick(avail_by_pos: dict[str, list[PoolPlayer]], priors: SlotPriors,
                  slot: int, round_: int, counts: dict[str, int],
                  picks_left_after: int, rng) -> PoolPlayer
```

- [ ] **Step 1: Failing tests**

```python
def test_required_picks_counts_flex():
    assert required_picks({}) == 11                      # 2+2+3+1+1+1 starters + flex
    assert required_picks({"QB":2,"RB":2,"WR":3,"TE":1,"K":1,"DEF":1}) == 1   # flex open
    assert required_picks({"QB":2,"RB":3,"WR":3,"TE":1,"K":1,"DEF":1}) == 0   # flex covered by RB3

def test_feasibility_forces_starters_at_the_death():
    counts = {"QB":2,"RB":2,"WR":3,"TE":1}               # 8 picks, no K/DEF, flex open
    assert not feasible(counts, "WR", picks_left_after=2)  # K+DEF+flex need 3
    assert feasible(counts, "K", picks_left_after=2)

def test_pick_is_deterministic_given_seed():
    rng1, rng2 = np.random.default_rng(7), np.random.default_rng(7)
    assert opponent_pick(AVAIL, PRIORS, 3, 2, {}, 17, rng1).ref == \
           opponent_pick(AVAIL, PRIORS, 3, 2, {}, 17, rng2).ref

def test_roster_damp_suppresses_fourth_qb():
    # 2000 seeded draws with 3 QBs already: QB picked < 5% despite high prior
def test_softmax_prefers_top_of_position_board():
    # top ADP candidate picked most often over 2000 draws
def test_never_returns_infeasible_position():
    # exhaustive over seeds at a forced endgame state
```

- [ ] **Step 2: Implement.**

```python
def required_picks(counts):
    need = sum(max(0, req - counts.get(p, 0)) for p, req in STARTERS.items())
    flex_surplus = (max(0, counts.get("RB", 0) - 2) + max(0, counts.get("WR", 0) - 3)
                    + max(0, counts.get("TE", 0) - 1))
    return need + (0 if flex_surplus >= 1 else 1)

def feasible(counts, pos, picks_left_after):
    c2 = dict(counts); c2[pos] = c2.get(pos, 0) + 1
    return required_picks(c2) <= picks_left_after

def opponent_pick(avail_by_pos, priors, slot, round_, counts, picks_left_after, rng):
    share = priors.pos_share[(slot, round_)]
    weights = {}
    for pos in POSITIONS:
        cands = avail_by_pos.get(pos) or []
        if not cands or not feasible(counts, pos, picks_left_after):
            continue
        w = share.get(pos, 0.0)
        damp = ROSTER_DAMP.get(pos, {})
        crossed = [t for t in damp if counts.get(pos, 0) >= t]
        if crossed:
            w *= damp[max(crossed)]
        weights[pos] = w
    if not weights:
        raise ValueError(f"no feasible position for slot {slot} round {round_} counts {counts}")
    total = sum(weights.values())
    if total <= 0:      # all dampened to zero → uniform over feasible
        weights = {p: 1.0 for p in weights}
        total = float(len(weights))
    positions = sorted(weights)          # sorted for determinism
    pos = rng.choice(positions, p=[weights[p] / total for p in positions])
    cands = avail_by_pos[pos][:CAND_WINDOW]
    logits = np.exp(-np.arange(len(cands)) / TAU)
    return cands[rng.choice(len(cands), p=logits / logits.sum())]
```

`avail_by_pos` lists are maintained sorted (real-ADP asc, None-ADP after by proj desc) by the draft engine — document that contract in the docstring.

- [ ] **Step 3: Tests pass; commit** — `"feat: opponent pick model — priors-driven position choice + ADP softmax, feasibility-masked"`

---

### Task 7: Draft engine (snake, state, legality) — property-tested

**Files:**
- Create: `src/ffi/sim/draft.py`
- Create: `tests/test_draft_engine.py`

**Interfaces:**
- Consumes: `opponent_pick`, `feasible`, `required_picks`, `SlotPriors`, `PoolPlayer`; a strategy callable for OUR seat (Task 8 provides the real one).
- Produces:

```python
TEAMS, ROUNDS = 12, 19

@dataclass
class DraftResult:
    rosters: dict[int, list[PoolPlayer]]     # draft position (1-12) -> 19 players
    picks: list[dict]                        # [{overall, position_slot, franchise_slot, pos, ref, name}]
    our_position: int
    slot_of_position: dict[int, int]         # draft position -> franchise slot

PickFn = Callable[[dict[str, list[PoolPlayer]], int, dict[str, int], int], PoolPlayer]
# (avail_by_pos, round, counts, picks_left_after) -> player   — OUR seat only

def snake_position(overall: int) -> tuple[int, int]           # -> (round 1-19, position 1-12)
def run_draft(pool: list[PoolPlayer], priors: SlotPriors, our_pick_fn: PickFn,
              seed: int, our_franchise_slot: int = 12,
              our_position: int | None = None) -> DraftResult
```

Semantics: franchise slots 1–12 are randomly permuted onto draft positions each draft (`rng.permutation`) — the 2026 draft order is unknown, so conclusions marginalize over it; `our_position` pins ours when given (grid slicing). Our franchise slot's priors are irrelevant (we use the strategy fn); opponents use the priors of the franchise slot occupying each position.

- [ ] **Step 1: Failing unit tests**

```python
def test_snake_order():
    assert snake_position(1) == (1, 1)
    assert snake_position(12) == (1, 12)
    assert snake_position(13) == (2, 12)
    assert snake_position(24) == (2, 1)
    assert snake_position(228) == (19, 1)

def test_draft_is_deterministic_by_seed(): ...      # same seed -> identical pick list
def test_our_position_pinning(): ...                # our_position=5 -> we pick 5th, 20th, ...
def test_no_player_drafted_twice(): ...
```

- [ ] **Step 2: Property test (hypothesis)** — the handoff's mandated property:

```python
@given(seed=st.integers(0, 10_000))
@settings(max_examples=50, deadline=None)
def test_every_roster_is_legal(seed, toy_pool, toy_priors):
    res = run_draft(toy_pool, toy_priors, greedy_vorp_fn, seed=seed)
    for pos_slot, roster in res.rosters.items():
        counts = Counter(p.position for p in roster)
        assert len(roster) == 19
        assert counts["QB"] >= 2 and counts["RB"] >= 2 and counts["WR"] >= 3
        assert counts["TE"] >= 1 and counts["K"] >= 1 and counts["DEF"] >= 1
        assert (max(0, counts["RB"]-2) + max(0, counts["WR"]-3) + max(0, counts["TE"]-1)) >= 1  # flex
```

`toy_pool`: a generated pool of ~350 synthetic players with enough of every position; `toy_priors`: uniform-ish shares. `greedy_vorp_fn`: picks max-vorp feasible player — a stand-in until Task 8.

- [ ] **Step 3: Implement `run_draft`.** Single `np.random.default_rng(seed)`; permute slots; maintain `avail_by_pos` (sorted lists per position; removal by ref via per-position dict index — O(1) with an ordered list + set of taken refs, filtering lazily on read of the top `CAND_WINDOW`); loop overall 1..228; for our position call `our_pick_fn`, else `opponent_pick`; ENFORCE legality on our fn's output too (`raise ValueError` if our strategy returns an infeasible pick — a strategy bug must never produce an illegal roster silently).

- [ ] **Step 4: Perf smoke** (design: "full draft in milliseconds"): `python -c` timing 100 seeded drafts on the toy pool — require < 100ms/draft (fail the step, not silently accept, if slower; optimize the availability structure before proceeding).

- [ ] **Step 5: Suite; commit** — `"feat: seeded snake draft engine, roster legality property-tested"`

---

### Task 8: Our-seat strategy logic (the knobs the farm grids)

**Files:**
- Create: `src/ffi/sim/strategy.py`
- Create: `tests/test_strategy.py`

**Interfaces:**
- Consumes: `PoolPlayer` (vorp, tier, adp), `feasible`/`required_picks`.
- Produces:

```python
@dataclass(frozen=True)
class StrategyParams:
    scenario: str = "qb_hoard_12"        # which valuation scenario builds the pool
    qb_by_round: tuple = (2, 5, 9)       # QB #n on roster by END of round qb_by_round[n-1]; len = QBs planned
    defk_round: int = 14                 # DEF forced at this round if unheld; K at defk_round+1
    caps: tuple = (("QB", 4), ("RB", 9), ("WR", 9), ("TE", 3), ("K", 2), ("DEF", 2))
    tier_break_bonus: float = 0.0        # score bump when player is the LAST of his tier available at his position

def make_strategy_fn(params: StrategyParams) -> PickFn
```

Decision order inside the returned fn (documented in docstring, tested):
1. **Feasibility force**: if `required_picks(counts) == picks_left_after`, restrict candidates to unmet starter positions.
2. **QB deadline force**: if `counts["QB"] < n` and `round >= qb_by_round[n-1]` for the smallest unmet n → best QB (by vorp).
3. **DEF/K force**: `round >= defk_round` and no DEF → best DEF; `round >= defk_round + 1` and no K → best K.
4. **Otherwise**: over feasible, under-cap candidates (top `CAND_WINDOW` per position), excluding QB beyond `len(qb_by_round)` and DEF/K before `defk_round`, score = `vorp + tier_break_bonus * is_last_in_tier`; argmax, ties → lower ADP (None ADP last), then name (total order → determinism).

- [ ] **Step 1: Failing tests**

```python
def test_qb_deadline_forces_qb(): ...          # round 2, 0 QBs, plan (2,5,9) -> returns a QB
def test_defk_window_respected(): ...          # never K/DEF before defk_round; forced at it
def test_caps_respected(): ...                 # 4 QBs held -> never a 5th
def test_tier_break_prefers_last_in_tier():    # two cands equal vorp, one closes a tier
def test_deterministic_tiebreak(): ...         # equal vorp+tier -> lower adp wins
def test_returns_feasible_at_endgame(): ...
```

- [ ] **Step 2: Implement; tests pass.**

- [ ] **Step 3: Integration:** swap Task 7's `greedy_vorp_fn` for `make_strategy_fn(StrategyParams())` in one end-to-end test asserting our roster has exactly 3 QBs (plan (2,5,9) → 3 QBs by round 9, cap 4 allows a late 4th only if vorp-argmax chooses it — assert ≥ 3).

- [ ] **Step 4: Commit** — `"feat: strategy-parameterized board logic (QB timing, DEF/K window, caps, tier-break)"`

---

### Task 9: Season evaluator (gamma weekly draws → optimal lineups → all-play)

**Files:**
- Create: `src/ffi/sim/season.py`
- Create: `tests/test_season_eval.py`

**Interfaces:**
- Consumes: `DraftResult.rosters`; `scoring.player_week_points` + `raw.nflverse_player_week` (CV fit); `public.team_def_map` + yahoo_engine 2025 (DEF CV).
- Produces:

```python
REG_WEEKS = 14
BYE_WINDOW = (5, 14)

def fit_weekly_points_cv(conn) -> dict[str, float]
    # {'QB': cv, 'RB':…, 'WR':…, 'TE':…, 'K':…, 'DEF':…} of weekly LEAGUE-SCORED points
    # QB/RB/WR/TE/K: scoring.player_week_points source='nflverse' joined to
    #   raw.nflverse_player_week on (gsis, season, week) for position; active weeks only
    #   (any of carries/receptions/completions/attempts > 0; K: points <> 0);
    #   pooled per-position sd/mean over 2019-2025. Fail loud if any position absent.
    # DEF: yahoo_engine 2025 weekly DEF points via team_def_map (mean of per-team sd/mean).

def evaluate_league(rosters: dict[int, list[PoolPlayer]], cv_by_pos: dict[str, float],
                    seed: int, n_seasons: int = 20,
                    points_lookup: dict[tuple[str, int], float] | None = None,
                    ) -> dict[int, float]     # draft position -> mean all-play pct
```

`points_lookup` mode (backtests, Task 11): when given, weekly points are `points_lookup.get((gsis_id, week), 0.0)` — deterministic, no draws, `n_seasons` ignored. Otherwise Monte Carlo mode: per player `mean_w = proj_points / 17`, weekly draw `Gamma(k=1/cv², θ=mean_w·cv²)`, one bye week per player per season uniform in `BYE_WINDOW` (draw = 0), vectorized numpy `(S, W, players)`.

Lineup (per team-week): sort roster's drawn points within position; take top 2 QB + 2 RB + 3 WR + 1 TE + 1 K + 1 DEF + best remaining RB/WR/TE as FLEX. All-play: each week each team beats `#(teams with lower total)` of 11; pct = wins / (11 × weeks × seasons).

- [ ] **Step 1: Failing tests**

```python
def test_lineup_is_optimal_on_toy_roster():
    # hand-built roster + fixed points: assert exact team total incl. FLEX = best leftover
def test_points_lookup_mode_is_deterministic_and_exact():
    # 12 tiny rosters, lookup with known values -> hand-computed all-play pcts
def test_missing_player_week_scores_zero_in_lookup_mode(): ...
def test_mc_mode_deterministic_by_seed(): ...
def test_strictly_dominant_roster_wins_more():
    # roster A = every player 2x the mean of roster B's -> all_play(A) > all_play(B)  (property-ish, seeded)
def test_bye_week_zeroes_exactly_one_week_in_window(): ...
def test_fit_weekly_points_cv_fails_loud_on_missing_position():
    # empty test DB -> ValueError naming the position
```

- [ ] **Step 2: Implement** with numpy throughout; the only loop is over teams for lineup assembly per (S, W) — use per-position index arrays + `np.sort` on gathered slices. Target: `evaluate_league` for 12×19 rosters, S=20, W=14 in < 50ms.

- [ ] **Step 3: Live CV smoke** — print `fit_weekly_points_cv(connect())`; expect CVs roughly 0.4–0.9 (weekly fantasy points are noisy); values outside (0.1, 2.0) for any position = investigate before proceeding, do not clamp silently.

- [ ] **Step 4: Suite; commit** — `"feat: vectorized season evaluator — gamma weekly draws, optimal lineups, all-play"`

---

### Task 10: Backtest archive sourcing attempt (R11 — due this phase, outcome documented either way)

**Files:**
- Create: `scripts/source_backtest_archives.py`
- Create: `docs/research/2026-07-XX-backtest-archive-sourcing.md`
- Create: `data/backtest_name_overrides.json` (starts `{}`)

**Interfaces:**
- Consumes: the open web (requests; Jina Reader fallback per user CLAUDE.md for JS-shelled pages), `raw.backtest_sources` (Task 4 DDL).
- Produces: `raw.backtest_sources` rows per (source, season, kind) that succeeded; the research doc records every attempt, URL, and outcome. **This task is allowed to end in documented failure** — that is an acceptable outcome per the risk register; Task 11 has the degrade path.

- [ ] **Step 1: Attempt, in order, for seasons 2023/2024/2025** (script downloads + stores; doc narrates):
1. **dynastyprocess ADP/ECR archives** — probe `https://github.com/dynastyprocess/data` (raw files under `/files/`, e.g. `db_fpecr.parquet` weekly ECR archive incl. superflex variants; also any `*adp*` assets). A late-August snapshot of each year = preseason. Kind `ecr` or `adp`.
2. **ffanalytics / ffverse archives** — probe the ffverse GitHub orgs for archived preseason projections CSVs.
3. **Wayback FantasyPros** — `http://archive.org/wayback/available?url=<fp-page>&timestamp=<YYYY>0820` for `fantasypros.com/nfl/adp/superflex-overall.php` (adp) and `fantasypros.com/nfl/projections/{qb,rb,wr,te,k}.php?week=draft` (projections); fetch the snapshot HTML, parse the data table.
4. Anything found gets stored raw + a parsed row-count printed. 1 req/sec politeness; no auth'd scraping.

- [ ] **Step 2: Success criteria per season** — `adp|ecr`: ≥ 150 named players with positions. `projections`: ≥ 250 players with season fantasy-relevant stat lines OR season point totals. Record per-season status matrix in the doc.

- [ ] **Step 3: Write the doc** with the matrix, chosen primary source per season, sample rows, and the explicit degrade decision for any season lacking projections (see Task 11). Commit — `"research: backtest archive sourcing attempt (R11) — outcome documented"`.

---

### Task 11: Backtest harness (Level 0.5) + ADR D7 regression gate

**Files:**
- Create: `src/ffi/sim/backtest.py`
- Create: `scripts/build_backtest_pools.py`
- Create: `scripts/run_backtests.py`
- Create: `tests/test_backtest.py`

**Interfaces:**
- Consumes: `raw.backtest_sources`, `public.player_id_xwalk` (name+position match), `scoring.player_week_points` (source='nflverse'), `compute_replacement_ranks`/`compute_baselines`, `gmm_tiers`, `run_draft`, `make_strategy_fn`, `evaluate_league(points_lookup=…)`, `build_slot_priors`, `sim.backtest_pool`, `sim.batches/batch_results`, `sim.backtest_reference`.
- Produces: `sim.backtest_pool` rows per season; `run_backtests.py --reference` (writes an active `sim.backtest_reference` row) and `run_backtests.py --gate` (**exits nonzero if composite < reference.composite − reference.band**) — this becomes the ADR D7 regression suite every later strategy/valuation change runs under.

Fixed decisions (document in module docstring):
- **Pool per season:** ref = gsis_id. Names from archives matched `lower(name)+position` against xwalk (+ `data/backtest_name_overrides.json` for stubborn ones). Match gate: ≥ 85% of the top 150 ADP names must resolve, else `SystemExit` listing the misses (R6 discipline).
- **Projections:** primary = archived stat lines scored under config v1 (same engine, weekly bonus EV via Task 2 with position CVs). **Degraded fallback** (season lacks projections): synthetic `proj_points` = the 2026 points-at-ADP-rank curve applied to that season's ADP order (monotone, position-wise), `degraded=true` on every such pool row and in `batches.data_vintage` — conclusions must carry the flag.
- **DEF neutralized:** all DEF score the 2025 realistic-streamer constant 13.59/wk in every backtest season (nflverse has no DEF; yahoo_engine only 2025). `defk_round` is therefore FIXED at 18 for all seats in backtests; DEF/K strategy is adjudicated by the sim farm + the Phase 2 streaming baselines, NOT by backtests. Loud in the report.
- **Opponents:** slot priors as of that season (`build_slot_priors` already recency-weights; acceptable known-simplification: it uses all 16 seasons — document, don't rebuild per-year priors).
- **Evaluator:** `points_lookup` from `scoring.player_week_points WHERE source='nflverse' AND season=%s AND week BETWEEN 1 AND 14`, keyed (gsis_id, week); missing = 0.0 (injury/inactive is real signal).
- **Reference cells** (composite): 4 strategies × 3 seasons × 100 seeded drafts:
  `REF_STRATEGIES = [StrategyParams(qb_by_round=(1,4,9)), StrategyParams(qb_by_round=(2,5,9)), StrategyParams(qb_by_round=(3,6,10)), StrategyParams(qb_by_round=(2,4,6))]` (all `defk_round=18`). Composite = mean of our-seat all-play% across cells; band = 2 × SE across cells' means.

- [ ] **Step 1: Failing tests** — pool-builder name matching (override file honored; <85% match exits loud); degraded synthetic curve is monotone in ADP; `run_backtests` composite math on a stubbed 2-cell result; gate exit codes (pass, fail, no-active-reference → loud error telling you to run `--reference`).

- [ ] **Step 2: Implement `build_backtest_pools.py`** (per season: parse stored payloads → match → score/synthesize → vorp via that pool's baselines (hoard_12 scenario shape) → gmm tiers → upsert `sim.backtest_pool`; prints per-season counts + degraded flags).

- [ ] **Step 3: Implement `run_backtests.py`** — for each cell: N=100 drafts (seeds `base_seed+i`), our franchise slot 12, evaluate with the season's lookup, write `sim.batches (kind='backtest')` + `batch_results` (`all_play_pct`, `all_play_se`, `qb1_round_mean`); `--reference` stores the composite (deactivating prior refs); `--gate` recomputes and compares.

- [ ] **Step 4: Run it live** for real: `uv run python scripts/build_backtest_pools.py && uv run python scripts/run_backtests.py --reference`. Sanity: composite should sit near 0.5 (we're one of 12 with a decent board — meaningfully above 0.5 is plausible, far below means a bug). Investigate < 0.45 before accepting.

- [ ] **Step 5: Suite; commit** — `"feat: 2023-25 backtest harness + ADR D7 regression gate (reference composite stored)"`

---

### Task 12: Sim farm nightly (Level 0) + adversarial report + launchd

**Files:**
- Create: `scripts/run_sim_farm.py`
- Create: `scripts/sim_report.py`
- Create: `launchd/com.ffi.simfarm.plist`
- Create: `tests/test_sim_farm.py` (grid construction + report SQL on seeded fixtures; no full-farm run in tests)

**Interfaces:**
- Consumes: everything above; `sim.batches/batch_results/sample_drafts`.
- Produces: nightly `reports/sim-farm-YYYY-MM-DD.md`; `com.ffi.simfarm` launchd job (02:30, wake-safe, separate from the morning chain).

Grid (explicit constant in `run_sim_farm.py` — QB timing is the headline knob):

```python
QB_PLANS = [(1, 4, 9), (2, 5, 9), (3, 6, 10), (2, 4, 6), (5, 8, 12), (3, 7)]   # incl. a 2-QB-only plan
DEFK_ROUNDS = [8, 11, 14, 18]      # tests the Phase 2 DRAFT-EARLY verdicts in context
TIER_BREAK = [0.0, 8.0]
SCENARIOS_MAIN = ["qb_hoard_12"]
SCENARIOS_QB_SUBGRID = ["qb_hoard_0", "qb_hoard_12", "qb_hoard_24"]   # crossed with QB_PLANS at defk=14, tb=0
N_DRAFTS_PER_CELL = 200
SEASONS_PER_DRAFT = 20
# cells: 6*4*2 = 48 main + 6*3 = 18 QB-scenario subgrid = 66 → 13,200 drafts/night
```

- [ ] **Step 1: `run_sim_farm.py`** — args `--base-seed` (default: required; launchd passes `$(date +%Y%m%d)`), `--cells N` (dev cap). Per cell: batch row (git SHA via `git rev-parse HEAD`, full data_vintage: snapshot_id + fetched_at, valuation max(computed_at), priors latest_season); run drafts (seed = base_seed × 100003 + cell_idx × 1009 + draft_idx); metrics → `batch_results` (`all_play_pct` mean, `all_play_se`, `top3_rate` = share of drafts finishing top-3 by PF among the 12, `qb1_round_mean`, `def_round_mean`); store 3 `sample_drafts` (worst/best/random by our all-play). **Staleness refusal (ADR D2):** exit nonzero before simulating if the snapshot is > 36h old. Errors-only logging; single summary print at end.

- [ ] **Step 2: `sim_report.py`** — reads tonight's batches (`started_at::date = today or --date`) and writes the ADVERSARIAL report:
  - header: **data-vintage line** (snapshot id/age, valuation timestamp, priors latest season, git SHA, degraded flags) — ADR D5 mandate;
  - QB-policy table: all-play% ± CI by qb_plan × scenario (the headline answer forming);
  - DEF/K table: all-play% by defk_round (verdict-in-context vs the Phase 2 streaming baseline);
  - tier-break and caps deltas;
  - **worst-drafts section**: the stored worst `sample_drafts` with pick-by-pick narrative of what went wrong (e.g. QB run before our qb_by_round deadline);
  - **assumption audit**: sim league-wide QB1-round mean vs historical 1.83 (±0.5 tolerance → loud WARN line, not silent), sim position-share by round band vs priors table;
  - exit nonzero if any batch tonight ran degraded/stale.

- [ ] **Step 3: launchd plist** — mirror `com.ffi.morning.plist` structure: label `com.ffi.simfarm`, `StartCalendarInterval` 02:30, program `bash -lc 'cd <repo> && uv run python scripts/run_sim_farm.py --base-seed $(date +%Y%m%d) && uv run python scripts/sim_report.py'`, logs to `logs/launchd-simfarm.{log,err}`. Bootstrap + `launchctl kickstart` once to verify end-to-end, then confirm `reports/sim-farm-<today>.md` exists and reads sane.

- [ ] **Step 4: One full manual farm run** (this is the Level-0 milestone): inspect the report with skepticism — if a conclusion looks too clean (e.g. every knob "significant"), suspect the evaluator's variance model before believing it.

- [ ] **Step 5: Suite; commit** — `"feat: nightly sim farm (66-cell strategy grid) + adversarial report + launchd job"`

---

### Task 13: Health-gate extension (phase1_report → 26 checks)

**Files:**
- Modify: `scripts/phase1_report.py` (append; NEVER replace)

Append after the Phase 2 block:

```python
    # --- Phase 3 checks ---
    ("K valuation present (PK->K fix holds)",
     "SELECT count(*) >= 20 FROM valuation.player_value WHERE scenario='qb_hoard_12' AND position='K'"),
    ("DEF valuation present (DST semantics task holds)",
     "SELECT count(*) >= 25 FROM valuation.player_value WHERE scenario='qb_hoard_12' AND position='DEF'"),
    ("valuation has no stacked duplicates",
     """SELECT count(*) = 0 FROM (SELECT xwalk_id, scenario FROM valuation.player_value
        GROUP BY 1,2 HAVING count(*) > 1) d"""),
    ("season projections carry weekly bonus model",
     """SELECT count(*) > 500 FROM scoring.projection_points
        WHERE horizon='season' AND components->>'bonus_model'='weekly_gamma_v1'
        AND snapshot_id=(SELECT max(snapshot_id) FROM raw.sleeper_projections WHERE week IS NULL)"""),
    ("sim farm has produced results",
     "SELECT count(*) >= 1 FROM sim.batches WHERE kind='farm' AND finished_at IS NOT NULL"),
    ("backtest reference composite active (ADR D7 gate armed)",
     "SELECT count(*) = 1 FROM sim.backtest_reference WHERE is_active"),
```

- [ ] **Step 1: Append checks; run `uv run python scripts/phase1_report.py`** → 26/26 OK (the farm/backtest checks pass because Tasks 11–12 ran live).
- [ ] **Step 2: `uv run pytest` green (the briefing subprocess test path still passes); commit** — `"feat: health gate extended to 26 checks (Phase 3)"`

---

### Task 14: Strategy conclusions report (user-facing deliverable) + R7 agreement check

**Files:**
- Create: `scripts/strategy_conclusions.py` (regenerable — reads `sim.*`, writes the doc)
- Create: `docs/research/2026-07-XX-strategy-conclusions.md`

**Interfaces:**
- Consumes: `sim.batch_results` (farm + backtest), `sim.backtest_reference`, the Phase 2 verdict docs, `docs/research/2026-07-09-baseline-sensitivity.md`.
- Produces: the Phase 3 exit deliverable (handoff §3.5).

Required sections (script assembles evidence tables; you write the judgments):
1. **QB-timing policy** — the headline: best qb_plan × scenario with CIs from the farm; cross-checked against backtest qb1-round slice; explicit statement of which hoarding scenario the evidence supports (resolves the Phase 2 finding that scenarios reorder the top-24 10/24).
2. **DEF/K policy confirmed-or-revised** — farm defk_round deltas vs the Phase 2 DRAFT-EARLY verdicts (+6.96 / +4.07 pts/wk); state clearly that backtests neutralize DEF and why.
3. **Tier-break rule** — keep or drop `tier_break_bonus` with the delta.
4. **Sim-vs-backtest agreement (R7's earliest signal)** — Spearman correlation of strategy-cell ordering (common cells: QB_PLANS at defk=18) between farm all-play% and backtest all-play%. Positive & material (>0.4) = transfer credible; near-zero/negative = R7 fired, say so and rank backtest evidence above sim evidence in the conclusions.
5. **Caveats** — degraded backtest seasons, DEF neutralization, slot-vs-human annotation coverage (only slot 12 today), pending QB-cohort input.

- [ ] **Step 1: Implement script; generate; hand-edit judgments; commit** — `"docs: Phase 3 strategy conclusions — QB policy, DEF/K verdicts in context, R7 agreement check"`

---

## Self-Review (performed at plan-writing time)

1. **Spec coverage vs handoff:** §2 mandated items → Tasks 1–3 (DST = Task 3; config-v2 preconditions correctly NOT implemented — no config change; debt = Task 1; PG_BIN runbook alignment deferred to next drill per handoff wording). §3.1 → Tasks 5–6; §3.2 → Tasks 4, 7, 8; §3.3 → Tasks 9, 12; §3.4 → Tasks 10, 11; §3.5 → Task 14. Process contract → this plan + subagent execution + Task 13 health gate + ADR D7 via Task 11. Bonus-pricing decision (handoff §4) → Task 2, decided YES with rationale.
2. **Placeholders:** none knowingly left; every code step carries real code or an exact contract; Task 1 Step 2 explicitly instructs adapting the patch seam after reading the module (a read-first instruction, not a TBD).
3. **Type consistency:** `PoolPlayer` defined once (Task 4), consumed by 6/7/8/9/11; `required_picks/feasible` defined in Task 6, reused by 7/8; `SlotPriors.pos_share` keyed `(slot, round)` everywhere; evaluator's two modes share one signature; 19 rounds / 228 picks / 14 weeks used consistently.
4. **Known deviations from handoff, deliberate:** 20→19 rounds (data); `adp_dd_ppr`→`adp_2qb` (live probe); backtests neutralize DEF (data availability, disclosed).
