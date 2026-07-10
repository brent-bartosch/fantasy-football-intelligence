# Fantasy Football Draft Intelligence

Draft-intelligence stack for the NAJEE 12-team, 2-QB Yahoo league (2026 draft ~mid-August): Postgres 15 + Python (uv), with projection ingest (Sleeper/FantasyPros/Yahoo), a golden-tested league-scoring engine, VORP valuation with GMM tiers, a Monte Carlo draft simulator with opponent models mined from 16 seasons of league history, 2023–25 backtests, and a nightly sim farm.

## Start here

- `docs/2026-07-08-PROJECT-RECORD.md` — the living project record (league identity, phase outcomes, hard-won gotchas). Read this first.
- `docs/2026-07-10-PHASE3-HANDOFF.md` — most recent phase handoff (Phase 3 complete as of 2026-07-10; Phase 4 = draft-day assistant + rehearsal ladder).
- `docs/research/2026-07-10-strategy-conclusions.md` — the Phase 3 strategy verdicts (QB timing, DEF/K policy).
- `league_rules.md` — scoring ground truth (root on purpose; referenced by code).
- Design / risks / ADR / plans live under `docs/superpowers/`.

## Daily operation

- Morning launchd chain (`com.ffi.morning`, 07:00): backup → Sleeper ingest → FantasyPros sync → score → valuation → briefing (reports to `reports/`, gitignored).
- Nightly sim farm (`com.ffi.simfarm`): strategy-grid drafts + adversarial report.

## Verify health

```bash
uv run pytest                              # full suite
uv run python scripts/phase1_report.py    # 26-check health gate
```

## Archive

`docs/archive/v1-2025/` holds the docs of the dormant 2025 v1 codebase (LMU-league era: RAG/RSS/scoring-adjuster — assessed and superseded; see PROJECT-RECORD §1). `schema/create_tables.sql` remains live (test-DB bootstrap); `migrations/` is the current schema source.
