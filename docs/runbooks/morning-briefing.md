# Morning briefing runbook

## What it is

`scripts/morning_briefing.py` is THE dashboard (ADR Domain 5): health header
first, then data freshness and board-input summaries. It is fail-loud — the
script **exits nonzero when any health item is red**. That is correct
behavior, not a bug: a red-flag exit means the job did not fail silently, it
is telling you something is wrong.

The launchd job `com.ffi.morning` runs the full morning pipeline each day at
07:00 local time:

```
ingest_sleeper.py --season 2026
  && ingest_fantasypros.py --daily
  && score_sleeper_projections.py
  && build_valuation.py
  && morning_briefing.py
```

Each step is chained with `&&`, so if any upstream step fails, the briefing
never runs and the launchd job's own exit code is nonzero — chain failures
surface loudly instead of the briefing silently reporting on stale/partial
data.

## Install

```bash
cp launchd/com.ffi.morning.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ffi.morning.plist
```

## Verify

```bash
launchctl list | grep ffi
```

Shows the job is loaded (PID `-` and last exit status `0` when idle/healthy).

To force an immediate run without waiting for 07:00 (useful right after
install, or to confirm the job still works after a code change):

```bash
launchctl kickstart -k gui/$(id -u)/com.ffi.morning
```

**Before forcing a run, check `raw.fp_snapshots` for today's call count** —
the `ingest_fantasypros.py --daily` step costs ~7 FantasyPros API calls
against the 30/day budget (ADR Domain 6). Do not force a run if
`fp_calls_today + 7` would exceed the budget; wait for the scheduled run or
the next day instead.

## Remove

```bash
launchctl bootout gui/$(id -u)/com.ffi.morning
```

## Where output lands

- Briefing: `reports/briefing-YYYY-MM-DD.md` (gitignored — regenerated daily,
  not committed).
- launchd stdout/stderr: `logs/launchd-morning.log` /
  `logs/launchd-morning.err` (relative to the plist's `WorkingDirectory`,
  the repo root). `logs/` must exist before the first run — launchd does not
  create parent directories for the log paths.

## Reading a red-flag exit

A nonzero exit from `morning_briefing.py` (or from the launchd job as a
whole, via the `&&` chain) means: **read the briefing file, the job did not
fail silently.** The script prints `RED FLAGS:` followed by the specific
reasons (stale sleeper snapshot, a failed ingest run, the structural health
gate failing, missing backups, etc.) both to stdout (captured in
`logs/launchd-morning.log`) and reflected in the `## Health` section of the
written briefing file itself. Fix the underlying cause, re-run the relevant
step manually, then re-run `uv run python scripts/morning_briefing.py` to
confirm green before trusting the board-input numbers.
