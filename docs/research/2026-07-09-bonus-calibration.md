# Threshold-bonus calibration — 2026-07-09

obs=48018 (player-week × tier), eval [2023, 2024, 2025], CV fit [2019, 2020, 2021, 2022]

Caveat: season-mean-as-projection is in-sample for the mean; this validates the
distribution SHAPE around a known mean, not projection accuracy.

**Brier (gamma model) = 0.0212  vs  Brier (mean-pricing) = 0.0259**

| predicted-P bin | n | mean predicted | actual freq |
|---|---|---|---|
| 0.0-0.1 | 42470 | 0.013 | 0.006 |
| 0.1-0.2 | 3485 | 0.145 | 0.115 |
| 0.2-0.3 | 1297 | 0.241 | 0.219 |
| 0.3-0.4 | 594 | 0.338 | 0.354 |
| 0.4-0.5 | 133 | 0.439 | 0.444 |
| 0.5-0.6 | 39 | 0.549 | 0.615 |
