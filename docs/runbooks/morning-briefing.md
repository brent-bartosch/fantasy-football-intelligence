# Morning briefing runbook

## What it is

`scripts/morning_briefing.py` is THE dashboard (ADR Domain 5): health header
first, then data freshness and board-input summaries. It is fail-loud — the
script **exits nonzero when any health item is red**. That is correct
behavior, not a bug: a red-flag exit means the job did not fail silently, it
is telling you something is wrong.

The launchd job `com.ffi.morning` runs the full morning pipeline each day at
07:00 local time:

The chain body is `scripts/morning_chain.sh` (backup → sleeper → nflverse →
fantasypros → scoring → valuation → fp_news → briefing).

Steps are **not** chained with `&&` (R23): each step runs regardless of its
predecessor and the briefing runs last, under an EXIT trap, so a failing
ingest can no longer silence the dashboard whose job is to report that
failure. Step failures still accumulate into the job's exit code; the
briefing's own exit code wins, because a dead dashboard is the more urgent
signal.

The chain now starts with `backup_db.sh`, so a fresh `pg_dump` is taken every
morning before ingest runs — the briefing's 2-day backup-freshness check is
therefore self-satisfying as long as the daily job keeps running.

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

## Operator setup — NOT YET APPLIED as of 2026-08-31

Two things live outside the repo. Neither is code, so nothing in CI can tell
you they are missing; this section is the only record that they are owed.

**1. Wake the laptop for the 07:00 run (R13).** launchd will run a missed job
when the machine wakes, but a lid-closed laptop at 07:00 means the briefing
lands whenever you happen to open it — which is exactly the "artifact absent
or hours late" condition the freshness assertions red on. Schedule a wake a
few minutes before the chain starts:

```bash
sudo pmset repeat wakeorpoweron MTWRFSU 02:25:00
```

Verify:

```bash
pmset -g sched
```

`repeat` (not `schedule`) so the entry survives; the whole-week `MTWRFSU`
because the chain runs daily. Adjust the time if the chain's start moves.

**2. Offsite backup target (R28).** `FFI_BACKUP_REMOTE` is unset, so
`scripts/backup_db.sh` **skips the offsite copy** and prints a loud
`WARN: FFI_BACKUP_REMOTE unset — backups are LAPTOP-ONLY (R28, RPO 24h)`.
That WARN is the intended behavior of an unconfigured install, not a failure —
the script still exits 0, because a missing offsite target must never cost the
local dump. Set it in `.env` to enable the sync:

```bash
FFI_BACKUP_REMOTE="/Volumes/Backup/fantasy_football/"            # external disk
FFI_BACKUP_REMOTE="brent@nas.local:/volume1/backups/fantasy_football/"  # or NAS
```

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

## Reading the health marks

The health header is three-state (`src/ffi/health.py`), driven by the
per-source contracts in `config/source_clock.yaml` — there is no global
staleness constant any more:

| Mark | Meaning | Banner? |
| --- | --- | --- |
| `[OK]` | within `expected_interval_h + lag_window_h` and status success | no |
| `[LAG]` | past that but within `deadline_h`, or a `sanity_warned` run — structural lag (e.g. nflverse publishing a day after MNF) | no |
| `[RED]` | past `deadline_h`, or a non-success run status, or an unregistered source | yes, red-flag exit |
| `[PENDING]` | an artifact assertion whose renderer has not shipped yet (`active_from` in the future) | no |

Two consequences worth knowing before you debug:

- A source with no entry in `config/source_clock.yaml` renders `[RED]
  unregistered source` and reds the run. Add the contract; do not special-case
  the source in the briefing.
- `[RED] nflverse_snap_counts: table ... does not exist` is expected until the
  snap-counts feed lands. It is a real red flag, not a bug in the briefing.

## Reading a red-flag exit

A nonzero exit from `morning_briefing.py` — or from the launchd job as a
whole, since the R23 trapped-`;` chain lets the briefing's own exit code win
over any earlier step's — means: **read the briefing file, the job did not
fail silently.** The script prints `RED FLAGS:` followed by the specific
reasons (stale sleeper snapshot, a failed ingest run, the structural health
gate failing, missing backups, etc.) both to stdout (captured in
`logs/launchd-morning.log`) and reflected in the `## Health` section of the
written briefing file itself. Fix the underlying cause, re-run the relevant
step manually, then re-run `uv run python scripts/morning_briefing.py` to
confirm green before trusting the board-input numbers.
