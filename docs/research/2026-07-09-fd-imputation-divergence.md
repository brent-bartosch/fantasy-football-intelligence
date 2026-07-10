# FD imputation vs Sleeper native — 2026-07-09

compared: 508 (player, kind) pairs with native FD >= 10

median divergence = 48.9%; over-15% pairs = 491 (96.7%)

Method: see ffi/scoring/fd_impute.py docstring (pooled position rates 2019-2025 + empirical-Bayes player rates, k=50/30/100).

## Pairs over the 15% investigation threshold

| player | pos | kind | native | imputed | div% |
|---|---|---|---|---|---|
| TreVeyon Henderson | RB | rec | 26 | 9.1 | 65% |
| LeQuint Allen | RB | rec | 13 | 4.4 | 65% |
| Rhamondre Stevenson | RB | rec | 21 | 7.8 | 64% |
| Omarion Hampton | RB | rec | 36 | 13.3 | 64% |
| J.K. Dobbins | RB | rec | 19 | 7.0 | 63% |
| Ashton Jeanty | RB | rec | 38 | 14.2 | 63% |
| Dylan Sampson | RB | rec | 18 | 6.6 | 62% |
| Tyquan Thornton | WR | rec | 31 | 11.6 | 62% |
| Chris Brooks | RB | rec | 11 | 4.2 | 62% |
| Derrick Henry | RB | rec | 12 | 4.7 | 61% |
| James Cook | RB | rec | 27 | 10.5 | 61% |
| Jonathan Taylor | RB | rec | 28 | 10.9 | 61% |
| Rashid Shaheed | WR | rec | 63 | 24.8 | 61% |
| Javonte Williams | RB | rec | 24 | 9.4 | 61% |
| Breece Hall | RB | rec | 32 | 12.9 | 60% |
| Saquon Barkley | RB | rec | 26 | 10.7 | 60% |
| Calvin Austin | WR | rec | 20 | 8.2 | 59% |
| Marvin Mims | WR | rec | 29 | 12.0 | 59% |
| Jayden Reed | WR | rec | 73 | 30.0 | 59% |
| D'Andre Swift | RB | rec | 28 | 11.4 | 59% |
| DeMario Douglas | WR | rec | 25 | 10.4 | 59% |
| Tyjae Spears | RB | rec | 25 | 10.3 | 59% |
| Jameson Williams | WR | rec | 100 | 41.6 | 58% |
| Malik Nabers | WR | rec | 98 | 40.8 | 58% |
| Josh Jacobs | RB | rec | 26 | 10.8 | 58% |
| Drew Sample | TE | rec | 13 | 5.3 | 58% |
| Emeka Egbuka | WR | rec | 101 | 42.1 | 58% |
| Jordan Mason | RB | rec | 14 | 5.9 | 58% |
| Kyle Williams | WR | rec | 15 | 6.3 | 58% |
| Troy Franklin | WR | rec | 36 | 15.2 | 58% |
| Xavier Hutchinson | WR | rec | 18 | 7.7 | 58% |
| Quentin Johnston | WR | rec | 75 | 31.9 | 57% |
| Christian Watson | WR | rec | 94 | 40.1 | 57% |
| Rachaad White | RB | rec | 24 | 10.3 | 57% |
| Zay Flowers | WR | rec | 110 | 47.3 | 57% |
| Quinshon Judkins | RB | rec | 23 | 9.8 | 57% |
| Tory Horton | WR | rec | 42 | 18.2 | 57% |
| Malik Washington | WR | rec | 40 | 17.1 | 57% |
| Isaiah Williams | WR | rec | 14 | 5.8 | 57% |
| Kenny Gainwell | RB | rec | 36 | 15.4 | 57% |
| Greg Dortch | WR | rec | 10 | 4.4 | 56% |
| Darnell Mooney | WR | rec | 27 | 12.0 | 56% |
| Tommy DeVito | QB | pass | 20 | 8.9 | 56% |
| Cade Stover | TE | rec | 13 | 5.7 | 56% |
| Jordan Addison | WR | rec | 78 | 34.4 | 56% |
| Jaxon Smith-Njigba | WR | rec | 134 | 59.1 | 56% |
| Brian Thomas | WR | rec | 90 | 39.4 | 56% |
| Travis Hunter | WR | rec | 42 | 18.3 | 56% |
| Wan'Dale Robinson | WR | rec | 68 | 30.2 | 56% |
| Ashton Jeanty | RB | rush | 115 | 51.2 | 56% |
| Tre Tucker | WR | rec | 51 | 22.6 | 55% |
| Marquez Valdes-Scantling | WR | rec | 12 | 5.5 | 55% |
| Nico Collins | WR | rec | 120 | 53.6 | 55% |
| DeVonta Smith | WR | rec | 104 | 46.2 | 55% |
| Dameon Pierce | RB | rush | 11 | 4.7 | 55% |
| Barion Brown | WR | rec | 14 | 6.1 | 55% |
| Chris Brazzell | WR | rec | 38 | 17.0 | 55% |
| Brenen Thompson | WR | rec | 19 | 8.5 | 55% |
| Bhayshul Tuten | RB | rec | 23 | 10.5 | 55% |
| Woody Marks | RB | rec | 14 | 6.1 | 55% |

## Resolution (2026-07-09, controller-decided design amendment concluding Task 8)

**Decision: Sleeper's native FD (`rush_fd`/`rec_fd`) is REJECTED as a scoring input.
Imputed FD from `ffi.scoring.fd_impute` (rates fitted on nflverse 2019-2025 actuals) is
the FD source for ALL projection scoring, effective immediately.**

Evidence, consolidated from the divergence run above plus a direct comparison against
nflverse 2025 ground truth:

- nflverse 2025 actuals show RB rush-FD/carry in the 0.18-0.27 range and RB rec-FD/rec in
  the 0.27-0.49 range for real players. Sleeper's 2026 season projections carry
  0.41-0.50 and 0.76-0.87 for the same players — roughly 2x inflated.
- 53% of (player, rec) pairs and 96% of (player, pass) pairs have `native_fd > native_volume`
  — a mathematical impossibility (a first down credit cannot exceed the receptions/completions
  that could produce it). This is not noise; it is broad-based, systemic miscalibration.
- Under this league's +1/FD scoring, the inflation is worth roughly 50-70 phantom points per
  season for high-volume RB/WR players (see the Bijan Robinson spot-check below).
- The imputation method itself independently reproduces well-known real-world NFL first-down
  conversion rates and is internally consistent by construction (rate = observed FD / observed
  volume over real plays, so it can never exceed 1.0). It was not the source of the divergence.

This resolves the "Concerns" raised at the end of Task 8: the median->15% `SystemExit` in
`scripts/fd_divergence_report.py` remains in the code (see the comment added there
2026-07-09) — it correctly documents the finding and should keep firing if re-run — but the
finding has been adjudicated: the failure indicates a Sleeper-side native-FD data-quality
problem, not a defect in the imputation method, so the guard does not block using imputed FD
as the scoring source.

### Implementation

- `src/ffi/scoring/sleeper_adapter.py`: `rush_fd`/`rec_fd` moved out of `_SLEEPER_MAP` into
  `_IGNORED_EXACT` — the adapter's `StatLine` never carries native FD. The keys are still
  classified as "known" (ingest-shape check only), never scored.
- `scripts/score_sleeper_projections.py`: for QB/RB/WR/TE records, `impute_fd` is called on
  each record's own projected rush/rec/pass volume (using a sleeper_id -> gsis_id crosswalk
  for player-level shrunk rates where available) and the result is injected into the
  `StatLine` via `model_copy(update=...)` before scoring. `pass_first_downs` is intentionally
  left unset (not a scored stat in this league's config). `components.fd_source = "imputed"`
  is recorded on every scored record that went through imputation, for auditability.

### Re-scored snapshot 3 (2026 season-level, re-run 2026-07-09 post-amendment)

QBs still dominate the top 10 (Josh Allen #1 at 673.10, down slightly from 675.80 — QBs get
imputed pass volume same as before via `pass_completions`/`pass_yards`/etc.; only their small
rush-FD component moves). RB/WR totals dropped materially, as expected from de-inflating FD:

- **Bijan Robinson (RB) spot-check**: native rush_fd=137.2, rec_fd=53.7 (190.9 total FD
  points at +1/FD). Imputed: rush_first_downs=72.57, rec_first_downs=24.74 (97.31 total).
  Old score 632.20 -> new score 538.61, a drop of 93.59 points — matches
  `old_points - (native_fd_sum - imputed_fd_sum)` exactly (632.20 - 93.593 = 538.607),
  confirming the imputation swap is the only change affecting his score.

New top-10 by points (snapshot 3, config v1):

| player_ref | pos | points |
|---|---|---|
| 4984 (Josh Allen) | QB | 673.10 |
| 11564 (Drake Maye) | QB | 626.30 |
| 11566 (Jayden Daniels) | QB | 615.40 |
| 4881 (Lamar Jackson) | QB | 611.31 |
| 6904 (Jalen Hurts) | QB | 610.05 |
| 6797 (Justin Herbert) | QB | 603.96 |
| 12508 (Jaxson Dart) | QB | 601.71 |
| 6770 | QB | 598.70 |
| 11563 (Bo Nix) | QB | 598.56 |
| 3294 (Dak Prescott) | QB | 597.95 |

QBs sweep the top 10 both before and after — the amendment does not change the shape of the
projection rankings' top tier, only the magnitude of RB/WR/TE scores (which fall, as intended,
now that phantom FD points are removed).
