# Backtest archive sourcing attempt (R11) — 2026-07-10

Goal: per-season preseason ADP/ECR (superflex preferred) and preseason season-projections for 2023/2024/2025, for Task 11's backtest drafts. Script: `scripts/source_backtest_archives.py` (idempotent upserts into `raw.backtest_sources` on `(source, season, kind)`). Success bars: adp/ecr ≥150 named players with positions; projections ≥250 players with stat lines or season points **and** QB/RB/WR/TE all covered (250 rows of QB+TE only would not be a usable draft pool — coverage requirement added during execution, stricter than the brief's raw count).

## Outcome matrix (source × season × kind)

| source | season | kind | status | rows | detail |
|---|---|---|---|---|---|
| dynastyprocess | 2023 | ecr | **PASS** | 500 | `db_fpecr.parquet`, page `ppr-superflex-cheatsheets`, snapshot 2023-08-18 (2d from Aug 20). QB 60 / RB 157 / WR 195 / TE 88 |
| dynastyprocess | 2024 | ecr | **PASS** | 510 | same, snapshot 2024-08-23 (3d). QB 48 / RB 167 / WR 201 / TE 94 |
| dynastyprocess | 2025 | ecr | **PASS** | 489 | same, snapshot 2025-08-22 (2d). QB 53 / RB 152 / WR 193 / TE 91 |
| ffverse orgs | all | projections | **MISS** | — | probed `ffverse` + `dynastyprocess` GitHub orgs and `FantasyFootballAnalytics/ffanalytics`; no archived preseason stat-line CSVs anywhere (details below) |
| wayback_fp | 2023 | projections | **PASS** | 693 | all 5 positional `?week=draft` pages snapped 2023-08-11. QB 86 / RB 171 / WR 255 / TE 143 / K 38 |
| wayback_fp | 2024 | projections | **PARTIAL** | 92 | only `qb.php?week=draft` (2024-08-04) exists in the Aug 1–Sep 20 window. QB 92, **no RB/WR/TE/K** |
| wayback_fp | 2025 | projections | **PASS** | 675 | `qb` (08-25) + `te` (08-22) + `flex?week=draft&scoring=PPR` (08-23, covers RB/WR/TE). QB 101 / RB 183 / WR 231 / TE 160, **no K** |
| wayback_fp | all | adp | **MISS** | — | `fantasypros.com/nfl/adp/superflex-overall.php` has **zero** Wayback captures ever (CDX confirmed); `/nfl/adp/*` captures are all individual player pages. ECR stands in for ADP, as the brief allows |

## Chosen primary per season

| season | adp/ecr primary | projections primary |
|---|---|---|
| 2023 | dynastyprocess ecr 2023-08-18 (superflex, 500) | wayback_fp 2023-08-11 (5 positions, 693) |
| 2024 | dynastyprocess ecr 2024-08-23 (superflex, 510) | wayback_fp QB only (92) → **degraded path for RB/WR/TE/K** |
| 2025 | dynastyprocess ecr 2025-08-22 (superflex, 489) | wayback_fp 2025-08-22/25 (QB/RB/WR/TE, 675) → degraded path for K only |

## Explicit degrade decisions (Task 11)

- **2024 projections**: only QB is real. Task 11 MUST use its degraded synthetic-projection path for 2024 RB/WR/TE/K. The 92 real QB rows are stored and should still be preferred over synthetic QB numbers.
- **2025 K projections**: no kicker page snapshot in the window (weekly-view snapshots exist but were rejected — see "weekly-view trap"). Synthetic/replacement-level K is fine; K is near-noise in draft value anyway.
- **2023**: no degrade needed.

## Sample rows (as stored in `raw.backtest_sources.payload`)

ecr (dynastyprocess 2023): `{"ecr": 2.99, "fp_id": "19236", "name": "Justin Jefferson", "position": "WR", "team": "MIN"}` — ranks are FP superflex/2QB PPR ECR, so QBs price like a 2QB room (2024 #1 overall = Josh Allen).

projections (wayback_fp 2023 QB): `{"name": "Patrick Mahomes II", "position": "QB", "team": "KC", "fp_id": "16413", "fpts": 383.9, "stats": {"PASSING_ATT": 609.6, "PASSING_CMP": 405.4, "PASSING_YDS": 4833.2, "PASSING_TDS": 37.7, "PASSING_INTS": 11.2, "RUSHING_ATT": 64.9, "RUSHING_YDS": 356.8, "RUSHING_TDS": 3.2, "MISC_FL": 1.9, ...}, "snapshot_date": "2023-08-11", "snapshot_url": "https://web.archive.org/web/20230811031000/..."}`

projections (wayback_fp 2025 flex RB): `{"name": "Kaleb Johnson", "position": "RB", "fpts": 165.0, "stats": {RUSHING_*, RECEIVING_*, MISC_*}, "snapshot_date": "2025-08-23"}`

## Gotchas Task 11 must respect

1. **FPTS scoring is inconsistent across pages — recompute from stat lines.** Positional pages (`qb/rb/wr/te.php?week=draft`) show FP default scoring (standard, non-PPR; QB = 4-pt pass TD — verified against Mahomes 2023 stat line). The 2025 flex snapshot is `scoring=PPR` (Chase 353.0). Do NOT mix stored `fpts` across sources; league-score the `stats` dict instead. K rows (2023 only) have FG/FGA/XPT — enough for K league-scoring.
2. **ECR is rank-only** (no points): ecr float ("2.99"), plus `fp_id`. Payload has no team-bye or ADP-style round info.
3. **fp_id join key**: every stored row (both kinds) carries FantasyPros numeric `player_id` (`fp_id`) — joins to `player_id_xwalk.fantasypros_id` and across kinds without name matching. Name matching is the fallback; `data/backtest_name_overrides.json` (created, `{}`) is the manual override hook.
4. **Snapshot dates differ within 2025 projections** (Aug 22/23/25 across pages) — fine for preseason purposes, recorded per-row in `snapshot_date`.
5. **2025 flex included 2 fringe IDP rows (DT1/LB1) and 2 gadget "QB" rows** (Tommy Mellott, Feleipe Franks). IDP rows are skipped (bounded at 2% before the parse fails loud); the 2 QBs are stored (harmless tail).

## The weekly-view trap (why some "found" snapshots were rejected)

FantasyPros serves the *same table layout* for weekly and season projections; the page without `?week=draft` (or an in-season default view) shows weekly numbers. Four candidate snapshots parsed cleanly but were rejected by a magnitude guard (top-5 FPTS < 60 ⇒ weekly): 2024 `k.php` 08-03 (top 8.3), 2024 `te.php?scoring=STD` 08-07 (top 8.8), 2024 `wr.php?scoring=STD` 09-07 (top 13.8), 2025 `rb.php` 09-17 (top 16.8). Without the guard these would have been stored as garbage season projections. The guard is permanent in `parse_projection_table`.

## Full attempt log

**1. dynastyprocess/data (GitHub)** — `api.github.com/repos/dynastyprocess/data/contents/files`: `db_fpecr.parquet` (38 MB, 1.78 M rows, weekly FP ECR scrapes 2019-12→today, 94 fp_pages incl. `ppr-superflex-cheatsheets.php` = redraft superflex). Aug snapshots exist for all 3 seasons → the 3 ecr PASSes. `files/archives/fantasypros/` positional cheatsheet CSVs stop at 2020-04 (useless for 2023+). No `*adp*` asset, no projected stat lines anywhere in the repo.

**2. ffverse orgs** — `ffverse` org: ffscrapr/ffpros/ffopportunity/ffsimulator etc. — live-scrape R packages, no data archives of projections. `FantasyFootballAnalytics/ffanalytics`: scraper package, `data/` holds only source configs (`projection_sources.rda`), no historical output. `dynastyprocess` org: sfb-draft data repos, no projections. **Documented miss.**

**3. Wayback FantasyPros** — CDX (`web.archive.org/cdx/search/cdx`) per page, window Aug 1–Sep 20, closest to Aug 20:
- 2023: qb/rb/wr/te/k `?week=draft` all captured 2023-08-11 → 693 rows. PASS.
- 2024: qb 08-04 only. rb/wr/te/k: zero captures in window (full-year CDX confirms nearest are May/Nov/Dec). `flex.php`: only a March STD capture — pre-NFL-draft, no rookie class, rejected as a preseason proxy. Directory-wide prefix scan of `/nfl/projections/` for Jul 20–Sep 20 2024 found only the two weekly-view pages noted above. PARTIAL (QB only).
- 2025: qb 08-25, te 08-22 `?week=draft`; rb/wr/k missing in window, but `flex.php?week=draft&scoring=PPR` captured 08-23 → RB/WR/TE recovered (569-row table, POS column). K: nothing valid. PASS minus K.
- ADP pages: `adp/superflex-overall.php` never archived (CDX empty over all time). MISS; ECR substitutes.

Politeness: ≤1 req/2.5 s, plain UA, ~30 URLs total; archive.org still rate-limited via connection-refusal bursts — script retries with 20-80 s backoff (`_get_with_retries`) and re-runs are idempotent (`--seasons` flag re-sources a subset).
