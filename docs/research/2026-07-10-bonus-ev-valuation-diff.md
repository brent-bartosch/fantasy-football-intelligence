# Bonus-EV valuation diff (Phase 3 Task 2)

Executed live against `fantasy_football` on 2026-07-10: `scripts/score_sleeper_projections.py`
then `scripts/build_valuation.py`, snapshot_id=6 (2026 season, week NULL, 3292 records) both
before and after — the rescore reused the same Sleeper snapshot, so all deltas below are
attributable purely to the three code changes (weekly-gamma bonus EV, PK→K mapping, duplicate
purge), not to new source data.

## 1. Duplicate purge

Before the fix, `valuation.player_value`/`replacement_baseline` DELETEs keyed on
`(params->>'snapshot_id')::int`, so every nightly rebuild that advanced the snapshot id left the
prior snapshot's rows in place — three full rebuilds had stacked three copies of every row.

| | qb_hoard_0 | qb_hoard_12 | qb_hoard_24 |
|---|---|---|---|
| rows before | 5991 | 5991 | 5991 |
| rows after | 2109 | 2109 | 2109 |
| `(xwalk_id, scenario)` pairs with count>1, before | 5991 (100%) | | |
| `(xwalk_id, scenario)` pairs with count>1, after | 0 | | |

The post-build assertion added in this task (`build_valuation.py`) now fails loud
(`SystemExit`) if this regresses.

A direct symptom of the bug: the brief's literal before-query (`ORDER BY vorp DESC LIMIT 25`)
returned only **9 distinct players** (each tripled) instead of 25 — any downstream consumer
asking for "top 25" was silently getting a third of that.

## 2. PK→K mapping

`public.player_id_xwalk` tags kickers `'PK'`; `build_valuation.py`'s pool query filtered
`x.position IN ('QB','RB','WR','TE','K')`, so no kicker ever joined — `K` had **zero** valuation
rows before this fix, silently (no error, just an empty position bucket that nothing checked).

| | before | after |
|---|---|---|
| K rows in `valuation.player_value` (qb_hoard_12) | 0 | 112 |
| K rows with positive VORP (above replacement) | 0 | 10 |
| K rank range (by VORP, within the 2109-row scenario) | — | 107–536 |
| K rows landing inside the draftable top-200 | — | 28 |

The post-build assertion (`count(position='K' scenario='qb_hoard_12') >= 20`) now fails loud if
this regresses; live count is 112.

## 3. Weekly-gamma bonus EV — per-position mean bonus-component delta

Reconstructed via replay: for every FD-imputed-position (QB/RB/WR/TE) record in the season-level
snapshot, computed the OLD one-shot bonus (`score_components(line, cfg)["bonuses"]`, unchanged
code) and the NEW weekly-gamma bonus (`season_bonus_ev`) against the *same* stat line, so the
comparison isolates the bonus-model change with no confound from re-fetched data.

**All players, this position (n = count of FD-imputed-pos records):**

| pos | n | mean old (one-shot) | mean new (weekly-gamma) | mean delta |
|---|---|---|---|---|
| QB | 355 | 2.166 | 0.592 | **-1.574** |
| RB | 674 | 2.122 | 0.480 | **-1.642** |
| WR | 1364 | 1.191 | 0.309 | **-0.881** |
| TE | 640 | 1.139 | 0.090 | **-1.049** |

**Top-25 by volume stat per position** (pass_yards for QB, rush_yards for RB, rec_yards for
WR/TE — a proxy; QB/RB totals also include any rush/rec bonus crossings, which is why mean old
exceeds the single-category cap of 12):

| pos | mean old | mean new | mean delta |
|---|---|---|---|
| QB | 21.680 | 7.297 | **-14.383** |
| RB | 23.280 | 10.470 | **-12.810** |
| WR | 12.000 | 11.179 | -0.821 |
| TE | 12.000 | 2.236 | **-9.764** |

## 4. Rank movement

The full-population rank-move count is dominated by noise: 1966 players are comparable between
old and new pools, 1863 (94.8%) move ≥3 ranks, mean |move| = 145 — but the biggest single moves
(+1282, -964, +880...) all belong to deep-bench/practice-squad names with near-zero VORP
clustered at replacement level, where a fractional point swing flips hundreds of rank positions.
This metric is not meaningful at full-pool scope.

Restricted to the draftable range (new rank ≤ 200), with K rows excluded from both sides to
isolate the bonus-EV effect from the K-pool-size effect (K's 28 top-200 entrants would otherwise
mechanically shift everyone below them):

- 200 players comparable; 83 (41.5%) move ≥3 ranks; mean |move| = 3.02
- Within the true top-25 (reconstructed old ranking vs. live new ranking): **same 25 players**
  (all QB, driven by the `qb_hoard_12` scenario's +12 QB demand), with modest intra-QB reordering
  (e.g. Joe Burrow #8→#4, Lamar Jackson #4→#7); top-50/top-100 show mean |move| of 1.50/1.62.

| top-N (new rank ≤ N) | n | moves ≥3 | mean \|move\| |
|---|---|---|---|
| 25 | 25 | 4 | 1.36 |
| 50 | 50 | 11 | 1.50 |
| 100 | 100 | 22 | 1.62 |
| 200 | 200 | 83 | 3.02 |

## 5. Top-25 (qb_hoard_12) — before vs. after, as actually queried live

Before (literal brief query, `LIMIT 25` — hits only 9 distinct players due to the duplicate bug,
each repeated 3x):

```
      name      | position | vorp
Josh Allen      | QB       | 600.1  (x3)
Drake Maye      | QB       | 553.3  (x3)
Jayden Daniels  | QB       | 542.4  (x3)
Lamar Jackson   | QB       | 538.3  (x3)
Jalen Hurts     | QB       | 537.1  (x3)
Justin Herbert  | QB       | 531.0  (x3)
Jaxson Dart     | QB       | 528.7  (x3)
Joe Burrow      | QB       | 525.7  (x3)
Bo Nix          | QB       | 525.6  (1 of 3 shown; LIMIT 25 cuts here)
```

After (same query, post-fix — 25 distinct players, no duplicates):

```
Josh Allen 588.5, Drake Maye 545.7, Jayden Daniels 530.0, Joe Burrow 528.2,
Dak Prescott 527.8, Jalen Hurts 526.0, Lamar Jackson 525.0, Justin Herbert 521.0,
Jaxson Dart 516.1, Bo Nix 514.8, Trevor Lawrence 506.9, Caleb Williams 497.9,
Brock Purdy 493.6, Jared Goff 476.2, Patrick Mahomes 474.1, Jordan Love 473.1,
Baker Mayfield 472.2, Matthew Stafford 464.2, Tyler Shough 450.7, Kyler Murray 448.5,
Malik Willis 437.1, Sam Darnold 435.3, Cam Ward 396.3, Daniel Jones 394.9, C.J. Stroud 389.5
```

Reconstructed true old top-25 (dedup'd, for a fair comparison — same 25 names, different order):
Josh Allen 600.1, Drake Maye 553.3, Jayden Daniels 542.4, Lamar Jackson 538.3, Jalen Hurts 537.1,
Justin Herbert 531.0, Jaxson Dart 528.7, Joe Burrow 525.7, Bo Nix 525.6, Dak Prescott 525.0,
Trevor Lawrence 519.0, Caleb Williams 509.4, Brock Purdy 503.1, Patrick Mahomes 486.7,
Baker Mayfield 483.5, Jordan Love 478.6, Jared Goff 471.5, Tyler Shough 464.6, Kyler Murray 463.4,
Matthew Stafford 461.7, Sam Darnold 439.1, Malik Willis 436.0, C.J. Stroud 403.2,
Daniel Jones 403.0, Cam Ward 402.9

**Composition unchanged** — same 25 QBs both before and after (expected: `qb_hoard_12` demand of
+12 extra rostered QBs dominates the replacement baseline so heavily that no non-QB clears it).
Absolute VORP values shift down by roughly 10-15 points per QB — consistent with §3's
top-25-by-volume QB row (mean bonus delta -14.4), not the whole-population QB mean (-1.574),
since these are specifically the highest-volume starters.

## 6. Interpretation

**The brief's stated expectation — that weekly-gamma pricing would systematically favor
high-volume players relative to the old one-shot season-total bonus — does NOT hold, in either
direction or magnitude, for this league's actual threshold structure.** Every position's mean
bonus delta is negative, including the top-25-by-volume cut (RB -12.8, QB -14.4, TE -9.8, WR
essentially flat at -0.8). Only a single extreme outlier RB (1406 projected rush yards, the
single highest in the pool) shows the predicted increase (old 15.0 → new 20.75), because the
one-shot model hadn't yet saturated at the max stacked bonus (24 points: full rush + full rec
tiers) for that specific player.

The mechanism: this league's thresholds (100/150/200 rush/rec yards, 300/400/500 pass yards,
cumulative stacking) are a **low bar at the season-aggregate level** — almost any starting-caliber
skill player's season total clears 200 yards trivially, so the old one-shot model handed out the
*full* stacked bonus (all three tiers) to nearly every starter, uncapped by how that production was
actually distributed across weeks. Reproducing that same threshold **weekly**, repeatedly across
17 games, is a much higher bar — even elite backs rarely post a single 200-yard rushing game more
than once or twice a season — so the calibrated gamma-priced weekly EV comes in below the
one-shot ceiling for the large majority of realistic season projections. The weekly-gamma model is
not "more generous to volume"; it is **more accurate**, and the correction runs in the direction of
removing bonus inflation that the season-total model was handing out for free, not adding bonus
credit the old model was missing. This is a materially different conclusion from the brief's
rationale, and is reported as found rather than forced to match the predicted direction.

Practically: total points drop modestly for essentially every FD-imputed-position player (mean
~1-1.6 pts/player at the config's default cumulative-stacking rules), VORP for the qb_hoard_12
top-25 shifts down by roughly 10-15 points per QB but reorders only mildly within the group, and
the K/PK and duplicate-purge fixes are the more consequential changes for the sim: K now has a
real, non-empty valuation pool (112 rows, 10 above replacement) instead of being silently absent,
and every downstream consumer of `valuation.player_value` now sees exactly one row per
(player, scenario) instead of three stacked copies.

## Verification

- `uv run pytest tests/test_projection_bonus.py -v` — 5/5 pass (TDD RED confirmed first: module
  import error before `projection_bonus.py` existed).
- `uv run pytest -q` — 166 passed (161 pre-existing + 5 new), golden-Yahoo gate untouched and
  still green.
- `uv run python scripts/phase1_report.py` — 20/20 OK, including `valuation built`.
- `build_valuation.py`'s new post-build assertions ran live and did not fire (no dup rows, K
  count 112 ≥ 20 threshold).
