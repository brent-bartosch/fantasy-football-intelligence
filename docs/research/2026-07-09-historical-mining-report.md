# NAJEE league historical mining — 2026-07-09

**Coverage:** drafts/standings/transactions/matchups 2010-2025 (16 seasons); league-scoring player-weeks 2019-2025 only (champions split limited to those 7). **Slot caveat:** results key on team slots; humans changed within slots (see manager_slot_annotations — currently only slot 12/Brent/~2022 is annotated).

**Week-bucket method:** transaction timestamps are bucketed into approximate NFL weeks anchored on each season's real week-1 start date (the `week_start` field on the week=1 raw.yahoo_matchups payload — present and sane for all 16 seasons; spot-checked 2010/2012/2014/2019/2021/2023/2025, all land in early September). An earlier draft anchored on the earliest transaction timestamp (draft day) was rejected after verification: draft day sits ~2-3 weeks before week 1 (confirmed: 2010 draft-day txn 2010-08-24 vs. week-1 start 2010-09-09, a 16-day/2.3-week gap), which would have shifted every bucket 2-3 weeks late.

## Annotations on file
- slot 12: Brent (2022-present)

## 1. Franchise slot -> outcome (persistent manager-seat quality; 16 seasons)
`teams.slot` is the stable Yahoo franchise/team seat, not the draft position — snake-draft order varies every season (see section 1b for that). A franchise slot can change hands between managers over the league's history (see `manager_slot_annotations`, e.g. slot 12 = Brent from ~2022-present); this table measures how strong a *seat* has been across whoever has held it, not any draft-order advantage.
| slot | seasons | avg finish | titles | avg PF |
|---|---|---|---|---|
| 1 | 16 | 8.12 | 0 | 2563 |
| 2 | 16 | 6.69 | 1 | 2565 |
| 3 | 16 | 6.00 | 2 | 2548 |
| 4 | 16 | 5.44 | 2 | 2568 |
| 5 | 16 | 6.88 | 1 | 2547 |
| 6 | 16 | 6.25 | 1 | 2562 |
| 7 | 16 | 6.50 | 3 | 2521 |
| 8 | 16 | 4.88 | 1 | 2647 |
| 9 | 16 | 5.19 | 2 | 2655 |
| 10 | 16 | 6.81 | 2 | 2532 |
| 11 | 16 | 7.94 | 1 | 2461 |
| 12 | 16 | 7.31 | 0 | 2542 |

## 1b. Draft position -> outcome (snake order, 16 seasons)
TRUE draft position: each team-season's round-1 `pick_number` (`draft_picks WHERE round_number = 1`) — the actual snake-draft slot a team drafted from that year, independent of its stable franchise seat. Validated: 192 team-seasons, exactly one round-1 pick each, live permutation check passed (see script run log above).
| position | seasons | avg finish | titles | avg PF |
|---|---|---|---|---|
| 1 | 16 | 5.12 | 2 | 2627 |
| 2 | 16 | 7.25 | 0 | 2504 |
| 3 | 16 | 6.00 | 1 | 2560 |
| 4 | 16 | 6.12 | 3 | 2635 |
| 5 | 16 | 7.75 | 1 | 2462 |
| 6 | 16 | 5.88 | 2 | 2647 |
| 7 | 16 | 6.94 | 2 | 2519 |
| 8 | 16 | 8.19 | 0 | 2436 |
| 9 | 16 | 5.88 | 2 | 2625 |
| 10 | 16 | 6.19 | 0 | 2585 |
| 11 | 16 | 5.81 | 0 | 2602 |
| 12 | 16 | 6.88 | 3 | 2513 |

**Cross-check (franchise slot vs true draft position):** positions 1-6 average a 6.35 finish vs 6.65 for positions 7-12 — earlier draft positions finish better on average. This is the honest draft-order signal; the much larger spread seen in section 1 (franchise slot 8 at 4.88 vs slot 1 at 8.12) is manager-seat quality accumulated over 16 years, not a draft-position effect — those two tables should not be conflated.

## 2. QB draft timing by slot (2QB fingerprint)
| slot | QB1 round | QB2 round | QB3 round | seasons |
|---|---|---|---|---|
| 1 | 1.9 | 4.2 | 9.4 | 16 |
| 2 | 1.6 | 4.5 | 12.9 | 16 |
| 3 | 1.9 | 4.8 | 13.0 | 16 |
| 4 | 1.8 | 4.4 | 10.2 | 16 |
| 5 | 2.2 | 4.6 | 10.4 | 16 |
| 6 | 1.6 | 4.3 | 10.0 | 16 |
| 7 | 1.6 | 4.8 | 9.5 | 16 |
| 8 | 1.4 | 4.3 | 9.6 | 16 |
| 9 | 1.8 | 4.4 | 9.8 | 16 |
| 10 | 1.8 | 4.2 | 11.8 | 16 |
| 11 | 2.6 | 4.5 | 10.2 | 16 |
| 12 | 1.9 | 4.3 | 12.4 | 16 |

## 3. Position-by-round tendencies (share of picks, per slot)
- **slot 1**: R1-3: RB 40%, QB 38%, WR 23%; R4-8: WR 40%, RB 32%, QB 22%, TE 5%; R9+: WR 31%, RB 30%, QB 19%, TE 12%, DEF 8%, K 1%
- **slot 2**: R1-3: QB 38%, RB 31%, WR 31%; R4-8: WR 35%, RB 34%, QB 18%, TE 14%; R9+: WR 34%, RB 25%, QB 17%, DEF 9%, K 8%, TE 7%
- **slot 3**: R1-3: QB 38%, WR 31%, RB 31%; R4-8: WR 39%, RB 34%, QB 18%, TE 9%, DEF 1%; R9+: WR 35%, RB 25%, QB 15%, TE 10%, DEF 8%, K 7%
- **slot 4**: R1-3: QB 40%, RB 35%, WR 25%; R4-8: WR 36%, RB 29%, QB 20%, TE 15%; R9+: WR 34%, QB 20%, RB 19%, TE 9%, K 9%, DEF 9%
- **slot 5**: R1-3: QB 40%, RB 35%, WR 23%, TE 2%; R4-8: WR 42%, RB 30%, QB 20%, TE 8%; R9+: WR 32%, RB 25%, QB 16%, TE 9%, DEF 9%, K 8%
- **slot 6**: R1-3: QB 40%, RB 29%, WR 27%, TE 4%; R4-8: WR 36%, RB 36%, QB 20%, TE 8%; R9+: WR 31%, RB 29%, QB 15%, DEF 9%, K 9%, TE 8%
- **slot 7**: R1-3: QB 40%, RB 35%, WR 23%, TE 2%; R4-8: WR 38%, RB 29%, QB 28%, TE 5%, DEF 1%; R9+: WR 42%, RB 20%, TE 12%, DEF 10%, QB 9%, K 7%
- **slot 8**: R1-3: QB 50%, RB 33%, WR 12%, TE 4%; R4-8: WR 45%, RB 29%, QB 14%, TE 12%; R9+: WR 33%, RB 26%, QB 21%, DEF 9%, TE 6%, K 5%
- **slot 9**: R1-3: QB 44%, RB 35%, WR 19%, TE 2%; R4-8: WR 48%, RB 21%, QB 18%, TE 14%; R9+: WR 30%, RB 29%, QB 15%, K 9%, DEF 9%, TE 8%
- **slot 10**: R1-3: QB 42%, RB 38%, WR 19%, TE 2%; R4-8: WR 49%, RB 24%, QB 16%, TE 11%; R9+: WR 32%, RB 30%, QB 15%, DEF 8%, K 8%, TE 7%
- **slot 11**: R1-3: QB 38%, RB 33%, WR 19%, TE 10%; R4-8: WR 41%, RB 31%, QB 24%, TE 4%; R9+: WR 30%, RB 28%, QB 17%, TE 9%, DEF 9%, K 7%
- **slot 12**: R1-3: QB 42%, RB 29%, WR 27%, TE 2%; R4-8: WR 46%, RB 25%, QB 19%, TE 10%; R9+: RB 36%, WR 26%, QB 12%, TE 9%, DEF 9%, K 7%

## 4. All-play vs record (luck audit; hypothesis 6.2)
Biggest schedule-luck beneficiaries and victims (|luck| = actual% - all-play%):
| season | slot | team | record | all-play% | luck |
|---|---|---|---|---|---|
| 2020 | 8 | N95 | 10-3 | 0.462 | +0.308 |
| 2018 | 1 | BEAST MODE | 1-12 | 0.373 | -0.296 |
| 2019 | 7 | Kegs by Solis | 6-7 | 0.734 | -0.273 |
| 2022 | 5 | Dumpster Fire | 10-4 | 0.468 | +0.247 |
| 2017 | 8 | SteamN Willy Beamin! | 5-8 | 0.629 | -0.245 |
| 2024 | 6 | HE HATE ME | 9-5 | 0.403 | +0.240 |
| 2014 | 8 | Romophobic | 8-5 | 0.406 | +0.210 |
| 2014 | 2 | Kegs by Solis | 8-5 | 0.420 | +0.196 |
| 2010 | 6 | HE HATE ME | 11-2 | 0.657 | +0.189 |
| 2016 | 5 | Mr. EXCITEMENT!!!! | 10-3 | 0.580 | +0.189 |
| 2018 | 3 | DRAFT KING 🇬🇷 | 6-7 | 0.273 | +0.189 |
| 2017 | 4 | Rastafarian-Jenkins | 9-4 | 0.503 | +0.189 |
| 2016 | 7 | Kegs by Solis | 3-10 | 0.413 | -0.182 |
| 2022 | 4 | BUSTOS | 6-8 | 0.610 | -0.182 |
| 2025 | 2 | Mr. E! | 6-8 | 0.610 | -0.182 |

## 5. Transaction timing (hypothesis 6.3: weeks 10-14 cluster?)
| approx week | transactions (all seasons) |
|---|---|
| 1 | 697 |
| 2 | 370 |
| 3 | 366 |
| 4 | 411 |
| 5 | 411 |
| 6 | 429 |
| 7 | 447 |
| 8 | 510 |
| 9 | 487 |
| 10 | 519 |
| 11 | 383 |
| 12 | 368 |
| 13 | 392 |
| 14 | 313 |
| 15 | 244 |
| 16 | 167 |
| 17 | 36 |

## 6. Trades (hypothesis 5.4)
- total trades 2010-2025: **199** (12.4/season); QB involved in 144 (72%)
- per season: 2010: 10, 2011: 5, 2012: 9, 2013: 10, 2014: 8, 2015: 13, 2016: 13, 2017: 14, 2018: 16, 2019: 25, 2020: 17, 2021: 12, 2022: 16, 2023: 16, 2024: 11, 2025: 4

## 7. Champions: draft vs waiver value split (2019-2025; hypothesis 1.3)
Attribution: player's weekly league-scoring points credited to the roster holding him that week (bench/start unknown — lineups not imported). Points require an nflverse gsis_id crosswalk match — team defenses (DEF) do not join and are undercounted here.
| season | champion | drafted pts | added pts | traded-in pts |
|---|---|---|---|---|
| 2019 | Kegs by Solis | 2969 | 1293 | 757 |
| 2020 | Marina del Rob | 3352 | 1433 | 555 |
| 2021 | Kegs by Solis | 3271 | 1292 | 594 |
| 2022 | Marina del Rob | 3213 | 1034 | 585 |
| 2023 | HE HATE ME | 3264 | 1051 | 587 |
| 2024 | Ricky Cent | 3798 | 1167 | 159 |
| 2025 | Kegs by Solis | 3720 | 859 | 0 |
