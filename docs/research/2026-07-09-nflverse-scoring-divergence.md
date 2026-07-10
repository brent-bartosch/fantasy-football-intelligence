# nflverse-vs-Yahoo scoring divergence — 2026-07-09

Joined 2025 player-weeks: 2664

median |diff| = 0.000   p95 |diff| = 4.000

Known structural gaps (nflverse_adapter.KNOWN_GAPS):
- `pick_sixes`: not in nflverse player stats; league -4 each; rare (~1 QB-week in ~60)
- `offensive_fumble_return_tds`: not in nflverse; league +6; very rare
- `return_tds`: approximated by special_teams_tds (includes all ST TDs)

## 30 largest divergences

| player | week | nflverse | yahoo | diff |
|---|---|---|---|---|
| Brandon Aubrey | 14 | 0.000 | 23.00 | -23.000 |
| Brandon Aubrey | 2 | 0.000 | 22.00 | -22.000 |
| Chase McLaughlin | 3 | 0.000 | 21.00 | -21.000 |
| Cameron Dicker | 14 | 0.000 | 20.00 | -20.000 |
| Cairo Santos | 4 | 0.000 | 19.00 | -19.000 |
| Ka'imi Fairbairn | 5 | 0.000 | 18.00 | -18.000 |
| Ka'imi Fairbairn | 9 | 0.000 | 18.00 | -18.000 |
| Ka'imi Fairbairn | 15 | 0.000 | 18.00 | -18.000 |
| Chase McLaughlin | 8 | 0.000 | 17.00 | -17.000 |
| Cameron Dicker | 6 | 0.000 | 17.00 | -17.000 |
| Brandon Aubrey | 7 | 0.000 | 17.00 | -17.000 |
| Brandon Aubrey | 17 | 0.000 | 17.00 | -17.000 |
| Cairo Santos | 11 | 0.000 | 16.00 | -16.000 |
| Ka'imi Fairbairn | 16 | 0.000 | 16.00 | -16.000 |
| Harrison Butker | 12 | 0.000 | 16.00 | -16.000 |
| Chase McLaughlin | 4 | 0.000 | 16.00 | -16.000 |
| Jake Bates | 14 | 0.000 | 16.00 | -16.000 |
| Cameron Dicker | 8 | 0.000 | 15.00 | -15.000 |
| Cam Little | 1 | 0.000 | 15.00 | -15.000 |
| Cam Little | 9 | 0.000 | 15.00 | -15.000 |
| Brandon Aubrey | 15 | 1.930 | 16.00 | -14.070 |
| Cairo Santos | 16 | 0.000 | 14.00 | -14.000 |
| Ka'imi Fairbairn | 1 | 0.000 | 14.00 | -14.000 |
| Ka'imi Fairbairn | 8 | 0.000 | 14.00 | -14.000 |
| Cameron Dicker | 10 | 0.000 | 14.00 | -14.000 |
| Cam Little | 17 | 0.000 | 14.00 | -14.000 |
| Harrison Butker | 4 | 0.000 | 13.00 | -13.000 |
| Jake Bates | 4 | 0.000 | 13.00 | -13.000 |
| Cam Little | 10 | 0.000 | 13.00 | -13.000 |
| Cam Little | 12 | 0.000 | 13.00 | -13.000 |

## Investigation: p95 breach (18.0 > 3.0 gate)

Median |diff| = 0.000 passes cleanly. p95 = 4.000 (script-level, across all
positions) breaches the 3.0 gate. Per-position breakdown makes the cause
unambiguous:

| position | n   | median &#124;diff&#124; | p95 &#124;diff&#124; |
|----------|-----|-----------------|--------------|
| K        | 124 | 9.0             | 18.0         |
| QB       | 504 | 0.0             | 1.0          |
| RB       | 853 | 0.0             | 1.0          |
| TE       | 264 | 0.0             | 0.0          |
| WR       | 919 | 0.0             | 0.0          |

**Root cause: kicking is out of scope for Task 6 (and was never in scope for
Task 1).** `COLUMN_MAP`/`DERIVED_SUMS` in `ffi.ingest.nflverse` — both before
and after this task — carry only passing/rushing/receiving/return/fumble/2pt
columns. `raw.nflverse_player_week` has no `fg_made`, `fg_att`, `pat_made`,
etc., so `stat_line_from_nflverse` always leaves `StatLine`'s kicking fields
(`fg_0_19` … `pat_missed`) at their default `None`, and every kicker scores
`0.000` under `source='nflverse'`. This is **not** a KNOWN_GAPS case (source
lacks the stat) — nflreadpy's `load_player_stats` *does* carry full
distance-bucketed FG/PAT data per kicker per week (verified directly:
`fg_made`, `fg_att`, `fg_made_0_19`…`fg_made_60_`, `pat_made`, `pat_att`,
etc. all present and populated for every K-position row in 2025). It is
simply unmapped — kicking widening was never requested by Task 6's brief
(scope: "total fumbles at -1, 2-pt conversions, and return TDs" only).

Excluding K, the join is excellent: median 0.0, p95 <= 1.0 across
QB/RB/TE/WR (2,540 of 2,664 joined rows, 95.3%). This is well inside the
audit's expected-divergence envelope (KNOWN_GAPS + FD-definition noise).

**Brandon Aubrey wk15 note (pre-flagged by task context):** row 36 above
(nflverse=1.930, yahoo=16.00, diff=-14.070) is the one previously-known
engine-side exception — Yahoo's own K payload for that game missed
Aubrey's fake-FG rushing TD (`components->>'payload_gap'='1.93'` in the
yahoo_engine row), which nflverse's rushing stats *do* capture. The 1.930
nflverse score is exactly that gap. This row is dominated by the same
general kicking gap (nflverse has no FG/PAT scoring at all) — the fake-FG
rush nuance only explains why nflverse isn't a flat 0.000 here, not the
bulk of the -14.070 diff.

**Conclusion: structural, fully explained, does not block Task 6.** No
code change is warranted within this task's scope. Recommendation for a
policy call: either (a) file a follow-up task to widen
`raw.nflverse_player_week`/`COLUMN_MAP` with kicking columns (they exist
and are populated in nflreadpy) so nflverse-scored history includes
kickers, or (b) exclude position='K' from any nflverse-sourced backtest/
mining work until that follow-up lands. Reported as DONE_WITH_CONCERNS.
