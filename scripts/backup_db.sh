#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
pg_dump fantasy_football | gzip > "backups/fantasy_football_$(date +%Y%m%d_%H%M%S).sql.gz"
# keep newest 14
ls -t backups/fantasy_football_*.sql.gz | tail -n +15 | xargs -r rm
echo "Backup complete: $(ls -t backups/fantasy_football_*.sql.gz | head -1)"
