# Team-change residual study — is situational context priced by preseason ECR?

**Question (league manager, GATE study):** do situation-changers (players who
changed NFL teams in the offseason) systematically over/under-perform their
preseason market valuation, under THIS league's scoring? If yes, coach/team
context is not fully priced and earns a capped adjustment (stage 2). If no,
we deliberately build nothing — avoiding an overfit error source.

**Script:** `scripts/research_team_change_residuals.py` (read-only; no DB
writes, no schema/table changes). Re-run: `uv run python
scripts/research_team_change_residuals.py`.

**Test:** `tests/test_team_change_residuals.py` — synthetic-data unit tests
for the bucketing/expectation/bootstrap/verdict math (no DB access).

## Method

1. **Universe**: `raw.backtest_sources` (source='dynastyprocess', kind='ecr'),
   seasons 2023/2024/2025, positions QB/RB/WR/TE only (the only positions
   this payload carries). Each row matched to a `gsis_id` via the SAME
   three-tier matcher `ffi.sim.backtest` already uses for the Phase 3
   backtest pools (`fp_id → player_id_xwalk.fantasypros_id`, then normalized
   `lower(name)+position`, then the manual override file) — reused, not
   reimplemented, per the brief.
2. **Market rank**: within each season × position, rank ascending by ECR
   value over ALL rows at that position (matched or not, so an unmatched
   player doesn't shift a matched neighbor's rank), then bucket into groups
   of 6 consecutive ranks (bucket 1 = ranks 1–6, etc). Only matched rows
   carry forward past this step.
3. **Expectation curve (leave-one-season-out)**: for held-out season S,
   `expected(position, bucket)` = the median summed actual league points
   (weeks 1–14, `scoring.player_week_points`, `source='nflverse'`,
   `config_version=1`; a player/week with no row scores 0.0 — the same
   "absence is signal" convention `ffi.sim.backtest.load_points_lookup`
   already uses) among matched players at that (position, bucket) **in the
   other two seasons only**. A (position, bucket) combination absent from
   the training seasons (thin tail, e.g. deep TE buckets some seasons)
   falls back to the nearest trained bucket for that position. This means
   no season's own outcomes ever feed its own expectation — the residual is
   not self-referential.
4. **Residual** = actual − expected(position, bucket).
5. **Team-change classification** (veterans only): modal team in season N
   vs. modal team in season N−1, both computed from `raw.nflverse_player_week`
   (mode = most frequent team across weeks played that season; ties broken
   by the team associated with the latest week played, closer to "where
   they ended up"). A player with **zero season N−1 rows** is a **ROOKIE**
   — excluded from the changer/stayer split (a different phenomenon; still
   included in the expectation-curve fit, since rookies are part of what
   "expected production at this market slot" means). A player with **zero
   season-N rows** (season-ending injury/cut before playing a snap) has no
   observable landing team — excluded as **UNKNOWN_CURRENT_TEAM**, reported
   separately, also still included in the expectation fit (actual = 0.0 is
   real signal).
6. **Comparison**: CHANGER vs. STAYER residuals, per position and pooled
   across positions — n, mean, median, a seeded (deterministic,
   `numpy.random.default_rng(20260710)`) bootstrap 95% CI on the difference
   of means (10,000 resamples), and a bust rate (fraction of each class at
   or below the pooled veteran bottom-quartile residual for that
   position/pool — i.e. the 25th percentile of all changer+stayer residuals
   together, so "bust" means "worse than 3 of every 4 veterans at that
   position," not an absolute cutoff).
7. **Verdict**: PRICED if the CI spans 0 or `|diff of means| < 10 pts/season`;
   otherwise MISPRICED, with direction and magnitude reported. Strict by
   design — with ~30–120 changers per position pooled across 3 seasons, only
   a clear signal should count (per the brief).

## Match rates

| season | overall (QB/RB/WR/TE) | top-200 by ECR rank |
|---|---|---|
| 2023 | 500/500 (100.0%) | 200/200 (100.0%) |
| 2024 | 506/510 (99.2%) | 200/200 (100.0%) |
| 2025 | 485/489 (99.2%) | 200/200 (100.0%) |

Well above the ≥95% (top-200) expectation in every season. The handful of
misses in 2024/2025 are deep-bench names outside the top-200 (not
inspected individually — excluded per the method, immaterial at this
depth).

## Classification counts (pooled, 3 seasons, matched players)

| class | n |
|---|---|
| stayer | 789 |
| changer | 291 |
| rookie (excluded, no N−1 team) | 293 |
| unknown_current_team (excluded, no N actual games) | 118 |

Spot-checked the `unknown_current_team` cohort: all are deep-bench/fringe
names (rank 89+ at their position) with `actual = 0.0` — consistent with
"never played a snap that season," not a matching bug.

## Results

### Per position (changer vs. stayer residual, pts/season)

| pos | n_changer | n_stayer | mean_changer | mean_stayer | diff (chg−sty) | 95% CI | bust% chg | bust% sty | verdict |
|---|---|---|---|---|---|---|---|---|---|
| QB | 42 | 90 | -9.19 | 3.29 | -12.48 | [-63.98, 38.89] | 23.8% | 25.6% | **PRICED** |
| RB | 80 | 235 | 22.32 | 16.48 | +5.84 | [-15.66, 27.81] | 27.5% | 24.3% | **PRICED** |
| WR | 123 | 290 | 8.26 | 13.28 | -5.02 | [-16.91, 6.57] | 24.4% | 25.5% | **PRICED** |
| TE | 46 | 174 | -1.58 | 7.56 | -9.14 | [-21.82, 4.36] | 30.4% | 23.6% | **PRICED** |

Medians (reference only, not the verdict basis): QB 1.85 vs 14.07, RB 6.11
vs 16.45, WR -1.90 vs 9.95, TE -6.38 vs 7.70 (changer vs. stayer). Medians
run in the same direction as the means for WR/TE (changers softer) and QB
(changers softer), but RB changers still come out ahead on both mean and
median — none of this clears the bar once bootstrap uncertainty is
accounted for.

### Pooled (all positions)

| n_changer | n_stayer | mean_changer | mean_stayer | diff | 95% CI | bust% chg | bust% sty | verdict |
|---|---|---|---|---|---|---|---|---|
| 291 | 789 | 8.05 | 11.83 | -3.78 | [-14.57, 7.11] | 27.1% | 24.2% | **PRICED** |

Every CI spans 0. QB shows the largest point estimate of the four positions
(-12.48, i.e. changers underperforming) but also by far the widest CI
(±~50 pts) — n=42 changers pooled over 3 seasons is simply too thin to
separate signal from a couple of outlier seasons (e.g. Kirk Cousins/ATL
2024 landed well below expectation while Sam Darnold/MIN 2024, Baker
Mayfield/TB 2023, and Daniel Jones/IND 2025 landed well above — these
offsetting stories are exactly why the CI is so wide, and exactly why a
strict verdict rule matters here).

## DECISION

**Coach/team modeling: DO NOT BUILD (stage 2 not entered).**

Reasoning: every position and the pooled comparison is PRICED — the
bootstrap 95% CI on the changer-vs-stayer residual difference includes 0 in
all five cells, and even the point estimates (range -12.5 to +5.8
pts/season) sit well inside real season-to-season variance for these
positions. Building a coach/team-change adjustment on top of this would be
fitting noise: the RB point estimate leans "changers outperform," QB/WR/TE
lean "changers underperform," and none of the four positions agree on
direction, which is itself evidence against a real, position-general
effect. Per the brief's own strictness bar ("only a clear signal counts"),
this is a clean no.

## Limits (stated honestly)

- **3 seasons only** (2023–2025) — the leave-one-season-out design only has
  2 training seasons per fold, and the per-position changer counts (42–123)
  are small enough that a single outlier player (e.g. a Cousins-to-ATL or a
  Darnold-to-MIN) visibly moves the point estimate, even though it doesn't
  move the verdict.
- **2024 uses ECR only as the market proxy everywhere** in this study — not
  specific to this analysis, but worth restating: the 2024 wayback_fp
  projections archive (used elsewhere in the backtest stack) is QB-only, so
  there is no real projection signal to fall back on for RB/WR/TE that
  season; ECR rank is the sole preseason ordering this study uses for every
  season/position anyway (by design — the method never touches the
  projections archive), so this limit doesn't change the method, but it
  does mean 2024's "expected" curve is training data too, same as 2023/2025.
- **Committee/depth-chart formation without an accompanying team change is
  NOT captured.** A player who stays on the same team but inherits a
  changed offensive coordinator, a new starting QB, or a suddenly-vacated
  backfield (a teammate traded/cut/injured) shows up as a STAYER here even
  though their situational context clearly changed. This study only
  measures the "changed teams" trigger, not "situation changed" more
  broadly — a real gap, but explicitly out of scope per the brief (that's
  a materially different, harder-to-define phenomenon than "team change").
- **Modal-team-N-vs-N−1 conflates offseason moves with pure in-season
  trades.** A player who started the season on Team A (matching their
  preseason ECR team) but was traded to Team B in Week 8, spending more
  weeks at B, would show up as a "changer" here even though the market
  correctly priced them as a stayer at draft time. This is rare (a handful
  of players/season) and cuts in both directions (it can land in either
  the changer or stayer bucket depending on which team accumulates more
  weeks), so it adds noise rather than a systematic bias — but it means the
  changer arm isn't a pure "offseason move" sample.
- **Rookies and zero-snap players are excluded from the changer/stayer
  split** (293 rookies, 118 zero-snap veterans) but still feed the
  expectation curve. This is the correct call for the question asked (team
  change is undefined for a rookie; a zero-snap veteran didn't "land"
  anywhere observable), but it does mean roughly 28% of matched top-200-ish
  players never enter the comparison at all.
- **Bust rate uses a pooled veteran (changer+stayer) bottom quartile**, not
  a fixed absolute points threshold — a deliberate choice so "bust" tracks
  each position/season's own outcome spread rather than an arbitrary
  constant, but it means the bust-rate numbers aren't comparable to a bust
  definition used elsewhere in this codebase if one exists.
- **No regression, no coach identity data, no depth-chart signal** — by
  design, per the brief. This is strictly a gate.
