# Phase 2 Handoff — Scoring Engine, Valuation & Historical Mining
**Written 2026-07-09, immediately after Phase 1 merged to `main` (f10de19). Audience: the session (or agent) that starts Phase 2.**

## 0. Read these first, in order

1. `docs/2026-07-08-PROJECT-RECORD.md` — full project narrative; **§13a/13b are mandatory** (league identity resolution; Phase 1 outcomes and established facts).
2. `docs/superpowers/specs/2026-07-08-draft-intelligence-design.md` — §3 (architecture bright line), §4.2–4.4 (scoring/valuation/opponent models = Phase 2's scope), §5 (build order).
3. `docs/superpowers/risks/2026-07-08-draft-intelligence-risks.md` — R1, **R16** (methodology risk — Phase 2 owns its mitigations), R7, R3 (week-3 checkpoint).
4. `docs/superpowers/risks/2026-07-08-draft-intelligence-adr.md` — Domains 1 (fail-loud), 2 (schema/provenance), 7 (testing: golden tests, property tests, purity test).
5. `docs/research/2026-07-09-in-season-management-research.md` — the hypotheses historical mining must test; draft-board implications section at the bottom.
6. `.superpowers/sdd/progress.md` — Phase 1 execution ledger (per-task commits, deferred Minor findings).

## 1. Process contract

- Phase 2 starts with **superpowers:writing-plans** (a new plan doc under `docs/superpowers/plans/`), then subagent-driven execution with per-task review gates — same discipline as Phase 1. Do not start coding before the plan exists.
- **Week-3 checkpoint (R3 red flag):** scoring engine golden-tested by end of week 3 or escalate to the user.
- Work on a branch, merge via finishing-a-development-branch. Fail-loud rules bind all new code (invoke fail-loud-error-handling when writing try/except).
- **pg_restore drill is due this phase** (ADR Domain 8, moved to week 3 by review): restore the newest backup into a scratch DB once, document the procedure.

## 2. Mandated first commit (final-review condition)

Consolidation BEFORE any new Yahoo-touching code:
- `ffi.ids`: one home for Yahoo key parsing (currently scattered: `numeric_id()` in fix_placeholder_players.py, inline `.split(".p.")`/`.split('.l.')` in import_yahoo_season.py, SQL `split_part` in crosswalk.py ×4).
- Throttle wrapper in `ffi.yahoo_client` (replaces 6+ hand-rolled `time.sleep(2)` sites).
- Fold the nflverse 4× duplicated column lists into one source→DB mapping structure while in there (deferred T5 Minor).

## 3. Phase 2 scope (design §5, weeks 2–3)

1. **Scoring engine** (`scoring` schema): versioned config v1 from `league_rules.md`; pure function of (stat_line, config_version) — property test enforces purity (ADR D7). **Golden tests: exact match vs Yahoo's official 2025 points — the data is already local** (`raw.yahoo_player_week.total_points` for 228 players × 17 weeks; stat lines in the `stats` JSONB). Pick ~40 edge-case fixtures (bonus thresholds, multi-bonus stacks, negative games, DEF tiers, return yards).
2. **First-down imputation** (R16): regressions on `raw.nflverse_player_week` (FD per carry / per reception by profile); validate vs Sleeper native `*_fd` — divergence >15% = investigate, never silently prefer either source. Divergence report is a deliverable.
3. **Threshold-bonus distribution pricing** (R16): per-player yardage distributions from nflverse week-level variance; calibration report (predicted vs actual bonus hit rates on 2023–25).
4. **Valuation layer** (`valuation` schema): VORP with **computed** 2QB baseline (12 teams × 2QB + flex; sensitivity analysis over QB-hoarding assumptions — R16), GMM tiers (fftiers method) on FP superflex ECR + our adjusted values, uncertainty bands. **Explicit deliverable — DEF streaming-baseline check** (research doc, flagged for Phase 2): does an elite DEF's projected points clear the replacement-level *streaming* DEF baseline under this league's enhanced DEF scoring (TFL, 3-and-outs, 4th-down stops, points/yards-allowed tiers)? Same check for K distance-tier scoring. Answer decides draft-early-vs-stream for K/DEF on the board — easy to build the whole engine without ever answering it; don't.
5. **Historical mining report** (the user-facing deliverable): draft position → outcome across 16 NAJEE seasons; manager tendencies **by slot** (+ user's turnover annotation, see §5); test the research hypotheses — champions' draft-vs-waiver value split, transaction timing vs the weeks-10–14 cluster, actual trade frequency/QB premiums, all-play vs record divergence.
6. **Morning briefing v1** (design §4.7): cron/launchd ingestion + generated report with the health header (`scripts/phase1_report.py` checks are the seed); agent digestion with capped adjustments can be v1.5 — the plumbing and health reporting come first.

## 4. Carry-forward fixes (from Phase 1 reviews — fold into plan tasks where they fit)

- DEF team-abbr mapping (crosswalk excludes DEF by design; scoring engine needs defenses).
- 2025-rookie crosswalk manual overrides (`manual_override=TRUE` rows; rookies have null yahoo_id in ff_playerids — R6).
- 85 legacy `nfl.p.<slug>` player-row cleanup (duplicates; check FK references before deleting).
- Team-abbr case normalization (Yahoo `Buf`/`Was` vs uppercase) wherever team joins are written.
- Crosswalk `match_report` dup-yahoo_id join guard (matters once manual overrides coexist with auto rows).
- Per-position FD validation in scoring-engine input checks (Sleeper union-check tolerates partial drift).
- Manager slot-turnover annotation table (see §5); migrate the "user inherited slot ~2022" fact from the audit script's print into data.
- `ffi.yahoo_client`: wrap raw `JSONDecodeError` (corrupted legacy token file) and network exceptions from `refresh_access_token()` in actionable `YahooAuthError`s (T6 deferred Minor — matters for the 2026 renewal re-audit and any re-imports).
- `import_outcomes` skip guards for standings/transactions (currently always re-fetches; matchups has one — T9 deferred Minor; saves API budget on the 2026 renewal re-run).
- Sleeper `week=None` (season-level projections) path: **untested and Phase 2 depends on it** (scoring engine works on season stat lines) — test it live + add the missing unit coverage before relying on it.

## 5. Pending user inputs (ask early, none block plan-writing)

1. **Slot-turnover annotation:** which of the 12 team slots changed humans, when (user knows; API can't — GUIDs redacted). User = slot manager_id 12, joined ~2022 (profile "Sports", Yahoo nickname "Brent").
2. **QB cohort reference material** the user promised ("what people have done to organize those groupings") — feeds tiers (§3.4).
3. **2026 draft date** (assumed ~mid-August).
4. **2026 league renewal:** neither league renewed as of 2026-07-09 (`renewed=''`). When the 2026 NAJEE league exists, re-run `scripts/audit_league_history.py` from its key and diff settings (R8 gate; scoring config versioning handles changes).

## 6. Environment facts & gotchas (hard-won, don't re-derive)

- **Stack:** uv only (`uv run pytest`, `uv sync`); Postgres 15 via brew (`brew services start postgresql@15`); pg_dump pinned via `PG_BIN` in `scripts/backup_db.sh` (PATH has v14!). Test DB `fantasy_football_test` self-bootstraps via conftest.
- **Yahoo:** `ffi.yahoo_client.get_session()/get_league()` — token refresh works; `config/yahoo_oauth.json` auto-built (0600). `lg.settings()` does NOT include roster_positions — use `lg.positions()`. Error 999 = 10–15 min lockout: never bulk-crawl near live work. All import scripts are idempotent/resumable (`import_weeks` requires COMPLETE weeks to skip).
- **FantasyPros:** key in `.env` (`FANTASYPROS_API_KEY`), **verified live 2026-07-09**. Hard limits 1/sec, 100/day; budget ≤30/day, cache to `raw`; never store FP historical stats (ToS). Runbook: `docs/runbooks/fantasypros-api.md`.
- **Sleeper:** 2026 projections already live (validated with `pass_fd/rush_fd/rec_fd`). Snapshot via `scripts/ingest_sleeper.py --season 2026 --week N` (week omitted = season-level, untested path).
- **nflreadpy real column names:** `passing_interceptions` (not `interceptions`); fumbles lost split per-type (`rushing_/receiving_/sack_fumbles_lost`) — `raw.nflverse_player_week.fumbles_lost` is the derived sum.
- **Data inventory:** `draft_picks` holds BOTH leagues' picks (join `raw.yahoo_league_settings` to select NAJEE chain); `raw.yahoo_player_week/standings/matchups/transactions` are NAJEE-season time-series (matchups payloads unparsed — Phase 2 parses); **63 residual placeholder players** (live count 2026-07-09; none drafted in the NAJEE chain — verify anytime with `SELECT count(*) FROM players WHERE player_name LIKE 'Player %' OR position='TBD'`); crosswalk 94.2% with DEF/slug exclusions by design.
- **Idempotency nuance:** drafts and player-weeks SKIP when complete; `import_outcomes` standings/transactions are idempotent (upserts) but ALWAYS re-fetch on re-run — see §4 skip-guard item before burning API budget on re-runs.
- **Health gate:** `uv run python scripts/phase1_report.py` must stay 12/12 OK — extend it with Phase 2 checks rather than replacing it.
