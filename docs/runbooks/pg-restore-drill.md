# pg_restore drill (ADR Domain 8)

Backups that have never been restored are hopes, not backups. This drill restores the
newest backup into a scratch DB, verifies row counts against live, times the restore,
then tears the scratch DB down. Re-run before every draft day (see reminder below).

## Drill executed: 2026-07-10

### Step 0: fresh backup first
Phase 2 wrote to `scoring`/`valuation`/`public.matchup_results` throughout the day, so the
newest pre-existing backup (`fantasy_football_20260709_132229.sql.gz`, 13:22 the prior day)
was stale. Took a fresh backup immediately before drilling so drill counts and live counts
can't drift:

```bash
bash scripts/backup_db.sh
# Backup complete: backups/fantasy_football_20260710_000315.sql.gz
```

### Step 1: identify dump format and PG_BIN
```bash
ls -lt backups/ | head -3
file backups/fantasy_football_20260710_000315.sql.gz
```
Result: `gzip compressed data ... original size modulo 2^32 66621220` — this is **gzipped
plain SQL**, not `pg_dump` custom format (matches how `scripts/backup_db.sh` produces it:
`pg_dump fantasy_football | gzip > ...`). Restore method is therefore `gunzip -c | psql`,
not `pg_restore`.

`PG_BIN` is pinned in `scripts/backup_db.sh` because PATH resolves to Postgres 14.18
(`/opt/homebrew/bin/psql`) while the server runs 15.14:
```bash
PG_BIN_EXPR=$(grep -o 'PG_BIN=.*' scripts/backup_db.sh | head -1 | cut -d= -f2-)
eval "PG_BIN=$PG_BIN_EXPR"   # -> /opt/homebrew/opt/postgresql@15/bin
```
(The brief's literal `cut -d= -f2-` capture is the shell-parameter-expansion string
`"${PG_BIN:-/opt/homebrew/opt/postgresql@15/bin}"` — it must be `eval`'d, not called
directly, to resolve to a real path since `PG_BIN` is unset in an interactive shell.)

### Step 2: create scratch DB and restore, timed
```bash
PG_BIN=/opt/homebrew/opt/postgresql@15/bin
"$PG_BIN"/createdb fantasy_football_drill
time (gunzip -c backups/fantasy_football_20260710_000315.sql.gz \
  | "$PG_BIN"/psql -d fantasy_football_drill -v ON_ERROR_STOP=1 > /tmp/pg_restore_drill.log 2>&1)
```
- Backup size: **5,838,959 bytes (5.6 MB)** gzipped
- Restore time: **2.172s total** (0.09s user / 0.12s system client-side; server-side work
  dominates the wall time)
- Log: 444 lines of `CREATE TABLE`/`CREATE INDEX`/`ALTER TABLE`/`COPY`, **0 errors** (`grep -ci error` = 0)

### Step 3: verify — drill DB vs live DB
```bash
Q="SELECT (SELECT count(*) FROM draft_picks),
       (SELECT count(*) FROM raw.yahoo_player_week),
       (SELECT count(*) FROM raw.nflverse_player_week),
       (SELECT count(*) FROM scoring.player_week_points),
       (SELECT count(*) FROM public.matchup_results),
       (SELECT count(*) FROM valuation.player_value);"
"$PG_BIN"/psql -d fantasy_football_drill -t -A -c "$Q"
"$PG_BIN"/psql -d fantasy_football       -t -A -c "$Q"
```

| table | drill count | live count | match |
|---|---|---|---|
| `draft_picks` | 7502 | 7502 | yes |
| `raw.yahoo_player_week` | 4658 | 4658 | yes |
| `raw.nflverse_player_week` | 129657 | 129657 | yes |
| `scoring.player_week_points` | 134315 | 134315 | yes |
| `public.matchup_results` | 2994 | 2994 | yes |
| `valuation.player_value` | 11982 | 11982 | yes |

All six counts matched exactly (backup taken immediately before the drill, so no
mid-day writes could cause drift). No mismatch to investigate.

### Step 4: clean up
```bash
"$PG_BIN"/psql -d postgres -c "DROP DATABASE fantasy_football_drill"
"$PG_BIN"/psql -d postgres -t -A -c \
  "SELECT datname FROM pg_database WHERE datname='fantasy_football_drill';"
# empty result confirms the drop
```
`fantasy_football_drill` confirmed dropped (query returned no rows).

## Summary
- Dump format: gzipped plain SQL (`pg_dump | gzip`), restore via `gunzip -c | psql`
- Backup size: 5.6 MB
- Restore time: ~2.2s wall clock
- Verification: 6/6 table counts matched between drill and live
- Scratch DB cleaned up and confirmed gone

## Re-drill before draft week
ADR Domain 8 requires a *tested* restore before draft day, not just a scheduled backup
job. Re-run this exact drill (fresh `scripts/backup_db.sh` → restore → verify → drop)
within the week before the live draft, and update this file's "Drill executed" date and
counts. If the restore time or row counts look wildly different from this run, stop and
investigate before trusting the backup during draft-day pressure.
