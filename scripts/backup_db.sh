#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# .env carries FFI_BACKUP_REMOTE (see the offsite block below). `set -a`
# exports every assignment; the guard keeps the sourcing side-effect-free
# if .env is absent.
if [ -f .env ]; then set -a; . ./.env; set +a; fi
mkdir -p backups
PG_BIN="${PG_BIN:-/opt/homebrew/opt/postgresql@15/bin}"
"$PG_BIN/pg_dump" fantasy_football | gzip > "backups/fantasy_football_$(date +%Y%m%d_%H%M%S).sql.gz"
# keep newest 14
ls -t backups/fantasy_football_*.sql.gz | tail -n +15 | xargs -r rm
echo "Backup complete: $(ls -t backups/fantasy_football_*.sql.gz | head -1)"

# --- Offsite copy (R28, L2xI9=18 — the cheapest risk retirement in the register) ---
# Backups currently live on the same laptop as the database: laptop loss
# takes both, RPO 24h. One rsync line retires it.
#
# USER INPUT REQUIRED: set FFI_BACKUP_REMOTE in .env to a real destination
# before this does anything. Examples:
#     FFI_BACKUP_REMOTE="brent@nas.local:/volume1/backups/fantasy_football/"
#     FFI_BACKUP_REMOTE="/Volumes/Backup/fantasy_football/"        # external disk
#   For a cloud target, swap `rsync` for `rclone copy` and configure the
#   remote with `rclone config` first.
#
# Until it is set this block prints a loud reminder and exits 0 — it must
# never fail the morning chain, because a missing offsite target is not a
# reason to lose the local backup too.
if [ -z "${FFI_BACKUP_REMOTE:-}" ]; then
  echo "WARN: FFI_BACKUP_REMOTE unset — backups are LAPTOP-ONLY (R28, RPO 24h)."
  echo "      Set it in .env to enable the offsite copy."
else
  rsync -a --delete-after \
    --include='fantasy_football_*.sql.gz' --exclude='*' \
    backups/ "$FFI_BACKUP_REMOTE"
  echo "Offsite sync complete: $FFI_BACKUP_REMOTE"
fi
