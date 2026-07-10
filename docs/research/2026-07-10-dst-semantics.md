# DST tier semantics verification + DEF projection scoring (Task 3)

Live verification protocol run: `scripts/verify_dst_semantics.py`. Reusable/re-runnable.

## Step 1: full DEF stat-key union (live, snapshot_id=6, season=2026, week=NULL, 32 DEF records)

```
adp_2qb              32/32   adp_dynasty          32/32   adp_dynasty_2qb      32/32
adp_dynasty_half_ppr 32/32   adp_dynasty_ppr      32/32   adp_dynasty_std      32/32
adp_half_ppr         32/32   adp_idp              32/32   adp_ppr              32/32
adp_rookie           32/32   adp_std              32/32
blk_kick             32/32   fum_rec              32/32   int                  32/32   sack 32/32
gp                   32/32   pts_allow_0          32/32   yds_allow_0_100      32/32
pts_half_ppr         32/32   pts_ppr              32/32   pts_std              32/32
def_fum_td            1/32   pass_int_td           4/32   def_kr_td            4/32   pr_td 5/32
```

25 distinct keys total. No richer bucket family exists (no `pts_allow_1_6`,
`pts_allow_7_13`, ... `pts_allow_35+`, and no `yds_allow_100_199` etc.) —
Sleeper's season-level snapshot emits exactly ONE flat bucket name per
category (`pts_allow_0`, `yds_allow_0_100`), never a full per-tier
distribution.

## Step 2: reconstruction protocol (the binding gate)

Reconstructed `pts_std` = `sack*1 + int*2 + fum_rec*2 + blk_kick*2` (Sleeper's
documented standard DEF weights) vs. Sleeper's own `pts_std` field.

**Result: 32/32 teams matched EXACTLY (0% residual)** — e.g. ARI:
34 sack + 8 int×2 + 7 fum_rec×2 + 1 blk_kick×2 = 66 = pts_std=66.00. Every
one of the 32 teams reconstructs to the cent. Full per-team table is
reproduced by `scripts/verify_dst_semantics.py`'s Step 2 output (all rows
`0.00%` / PASS). This clears the ≥28/32 @ 5% gate at the strongest possible
margin — **CONFIRMED, not BLOCKED.**

Consequence of the exact match: `pts_allow_0`, `yds_allow_0_100`, `gp`, and
the four rare TD-adjacent keys (`pass_int_td`, `def_fum_td`, `pr_td`,
`def_kr_td`) contribute **zero** to Sleeper's own scoring — confirmed by
three independent pieces of evidence, not just the reconstruction:

1. `pts_allow_0` and `yds_allow_0_100` are **exactly 1.0 for all 32 teams**
   (zero variance — the league's best and worst projected defenses report
   the identical value). A real games-in-bucket count would vary with
   defensive quality; this doesn't.
2. Excluding them from the reconstruction formula is the ONLY way to hit
   32/32 exact — including them (e.g. crediting `pts_allow_0=1.0` as "1
   shutout game" worth Sleeper's own +10) would overshoot `pts_std` for
   every team.
3. `yds_allow_0_100`'s own range (0–100) doesn't even align with this
   league's `yards_allowed_tiers` boundaries (`max: 99` then `max: 199` —
   100 straddles the boundary), reinforcing that it's Sleeper's own
   internal placeholder, not data meant to be fed into a specific tier
   table at all.

`gp` is similarly always `1.0` for DEF records (not a real season
games-played count — offense positions carry real `gp≈17` values).

**This differs from the brief's original hypothesis**, which assumed
Sleeper might supply genuine per-tier game counts (illustrated by the Step 4
unit-test fixture using `pts_allow_1_6`, `pts_allow_7_13`, etc.). Live data
shows that richer schema never appears. Per the brief's own built-in escape
hatch ("if reconstruction only works when excluding [buckets], that's fine
and expected; document it") — this is exactly that outcome, just wider than
anticipated (both pts_allow AND yds_allow buckets are non-contributing, not
just yds_allow).

## Deviation from the brief: `fit_def_uplift`'s zero-out list

The brief's Step 3 said to zero `{sacks, def_interceptions,
fumble_recoveries, blocked_kicks, defensive_tds, safeties, points_allowed,
yards_allowed}` before scoring the 2025 Yahoo-actual residual used to fit
the uplift. That list assumed Sleeper's season projection would supply
usable `defensive_tds`/`safeties`/`points_allowed`/`yards_allowed` signal.
Step 1/2 above prove it does not: no `def_td`/`safety`-equivalent key
exists in the union at all, and the points/yards-allowed buckets are
verified-junk placeholders.

**Adjustment made (implemented in `fit_def_uplift`):** zero out only
`{sacks, def_interceptions, fumble_recoveries, blocked_kicks}` — the 4
fields Sleeper actually, verifiably projects. Everything else the league
scores for DEF (`defensive_tds`, `safeties`, `points_allowed`/
`yards_allowed` tiers, `fourth_down_stops`, `tackles_for_loss`,
`three_and_outs`, `extra_point_returns`) folds into the single fitted
constant, because Sleeper's season snapshot gives literally no usable
signal for any of them. Note this constant is a flat league-average added
identically to every team — it does not add per-team relative signal (and
so does not change VORP/valuation ranking, only the realism of absolute
point totals); the real per-team differentiation left in the projection
comes entirely from `sack`/`int`/`fum_rec`/`blk_kick` counts.

Fitted on 2025 `yahoo_engine` actuals (`raw.yahoo_player_week`,
`position_type='DT'`, 544 team-weeks = 32 teams × 17 weeks, via the
existing `ffi.scoring.yahoo_adapter.stat_line_from_yahoo`):

**`uplift_per_week = 9.660`** points/week (≈164.2 pts over a 17-game season).

Sanity check: average weekly `yahoo_engine` DEF total = 14.19 pts; average
weekly counting-stat contribution (sack/int/fum_rec/blk_kick at league
weights) = 4.53 pts; residual = 9.66 pts — matches exactly, and
`4.53×17 + 9.66×17 ≈ 241.3` matches the real average full-season DEF total
(241.3) computed independently from `scoring.player_week_points`.

## Step 4/5: `def_projection_points` implementation

Explicit key map (`src/ffi/scoring/def_projection.py`):
- **Counting** (real, league-weighted): `sack→sacks(1)`, `int→def_interceptions(2)`,
  `fum_rec→fumble_recoveries(2)`, `blk_kick→blocked_kicks(2)`.
- **Ignored** (verified-junk/metadata): `gp`, `pts_allow_0`, `yds_allow_0_100`,
  `pass_int_td`, `def_fum_td`, `pr_td`, `def_kr_td`, `pts_ppr`, `pts_std`,
  `pts_half_ppr`, and the `adp_*`/`pos_adp_*` prefix family.
- **Bucket-tier mechanism** (generic, regex-driven against
  `cfg.defense.points_allowed_tiers`/`yards_allowed_tiers`): wired in for
  robustness/future-proofing (e.g. `pts_allow_1_6`, `yds_allow_300_349`) —
  never fires against the current live snapshot (only the two verified-junk
  single-bucket names ever appear there), but satisfies the brief's
  prescribed unit test and would activate automatically if Sleeper ever
  starts emitting genuine per-tier game counts.
- **Fail loud**: any other key → `ValueError("unmapped DEF stat key: ...")`.

Season points = `Σ counting×weight + Σ bucket_count×tier_points + uplift×games`.

TDD evidence (`tests/test_def_projection.py`, 8 tests):
- RED: `ModuleNotFoundError: No module named 'ffi.scoring.def_projection'`
  (collection error before implementation existed).
- GREEN: all 8 pass, including the brief's two prescribed tests
  (`test_def_projection_maps_buckets_to_league_tiers`,
  `test_def_projection_fails_loud_on_unknown_stat_key`), plus tests proving
  the verified-junk keys score exactly zero and a DB-backed
  `fit_def_uplift` correctness check (synthetic 2-week fixture, hand-computed
  expected residual of `(23.0 + 3.0)/2 = 13.0`, asserted via `pytest.approx`).

## Step 3 (xwalk) — a second bug found and fixed along the way

`public.player_id_xwalk` DEF rows were inserted per the brief's SQL
(32 rows from `team_def_map`). A post-insert LEFT JOIN check against the
freshly-scored Sleeper projections found **31/32 joined** — one team
(`team_abbr='LA'`, the Rams) never matched a Sleeper record.

Root cause: `team_def_map.team_abbr` is populated from **Yahoo's own** team
abbreviation (`scripts/build_def_map.py`), which is `"LA"` for the Rams;
Sleeper's `raw.sleeper_projections` player_id for the same team is
`"LAR"`. Verified this is the *only* divergence among the 32 teams.
Fix: a scoped `_YAHOO_TO_SLEEPER_ABBR = {"LA": "LAR"}` override applied only
to the `sleeper_id` column of the xwalk insert (not to `team_def_map`
itself, which other call sites — `backfill_def_k_weeks.py`,
`phase1_report.py` — rely on carrying Yahoo's own convention). After the
fix: **32/32 DEF crosswalk rows join correctly.**

## Step 7: 2025 sanity correlation

Spearman rank correlation between 2026 projected DEF season points
(`def_projection_points`, freshly scored) and 2025 actual DEF season league
points (`scoring.player_week_points`, `source='yahoo_engine'`, summed per
team):

**ρ = 0.572 (p = 0.001), n = 32 teams** — comfortably clears the required
`> 0.3` gate, and the small p-value indicates the correlation is not noise.
Weak-to-moderate year-over-year DEF correlation is expected and normal;
this result is consistent with the counting-stat-only signal genuinely
carrying real (if partial) predictive value, with no sign of a
mapping bug (not negative, not ~0).

## Final numbers

- DEF season projections: range **226.2 – 270.2 points** (32 teams), computed
  as `counting (66–106, driven by sack/int/fum_rec/blk_kick) + uplift (164.2183 flat,
  9.6601 pts/week × 17 games — rounded to 4dp in fit_def_uplift, matching the
  repo convention used by projection_bonus.season_bonus_ev for
  statistically-fitted quantities feeding the Decimal-exact engine)`.
- Top 5 by projected VORP (`qb_hoard_12` scenario, replacement rank 12,
  replacement points 251.2): Rams (+19.0), Texans (+17.0), Seahawks (+16.0),
  Eagles (+11.0), Broncos (+9.0).
- `valuation.player_value`: 32/32 DEF rows per scenario (all 3 QB-hoarding
  scenarios); no `<25` pool warning fires.
- Full test suite: **174 passed** (166 pre-existing + 8 new in
  `tests/test_def_projection.py`).
- `scripts/phase1_report.py`: **20/20 OK**.

## Files touched

- `src/ffi/scoring/def_projection.py` (new) — `def_projection_points`, `fit_def_uplift`.
- `tests/test_def_projection.py` (new) — 8 tests, TDD RED→GREEN.
- `scripts/verify_dst_semantics.py` (new) — re-runnable verification protocol
  (Steps 1/2/3/4 above), exits nonzero on reconstruction-gate or
  correlation-gate failure.
- `scripts/score_sleeper_projections.py` — DEF branch beside the skill-position
  path (no FD imputation, no yardage-bonus EV for DEF); uplift fitted once per run.
- `scripts/build_valuation.py` — `'DEF'` added to the position filter; the
  old "DEF absent in v1" rank exclusion removed; loud `<25`-pool warning kept.
