#!/usr/bin/env bash
# scripts/pre-commit.sh — the versioned pre-commit body.
#
# .git/hooks/ is not tracked by git, so hook logic living there cannot be
# reviewed, cannot be diffed, and silently differs between clones. The hook
# is a two-line shim that sources this file. Contract: ARCHITECTURE.md §8.
#
# Five checks, in ascending cost order:
#   1. secret-path denylist   (staged paths; catches `git add -f` of a token)
#   2. file-size budgets      (ARCHITECTURE.md §1 column 3)
#   3. import boundaries      (ARCHITECTURE.md §3 + 4 hard-coded rules)
#   4. secret hygiene         (.env.example field names, no ntfy topic)
#   5. league-clock safety    (no UNSET/UNVERIFIED field is consumed)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# --- 1. secret-path denylist -------------------------------------------------
DENYLIST=(
  'config/yahoo_(oauth|token)\.json'
  'config/.*(oauth|token).*'
  'config/.*\.(key|crt|pem)$'
  '\.env(\.|$)'
  '\.(key|crt|pem)$'
  'browser-profiles/'
  '\.playwright/'
  'playwright/\.auth/'
  'data/draft-logs/'
  'data/captures/'
  'backups/'
)
staged="$(git diff --cached --name-only --diff-filter=ACM)"
if [ -n "$staged" ]; then
  bad=""
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    for re in "${DENYLIST[@]}"; do
      if printf '%s\n' "$f" | grep -Eq "$re"; then
        # .env.example is field-names-only and IS tracked; check_no_secrets.sh
        # enforces that. Exempt it from the '\.env(\.|$)' pattern.
        [ "$f" = ".env.example" ] && break
        bad="${bad}${f}\n"
        break
      fi
    done
  done <<< "$staged"
  if [ -n "$bad" ]; then
    printf 'BLOCKED COMMIT: staged files match the secret denylist:\n'
    printf '%b' "$bad"
    printf '\nUnstage them (git reset HEAD -- <file>) and delete/rotate if already pushed.\n'
    exit 1
  fi
fi

# --- 2-5. the architecture and config guards --------------------------------
bash scripts/check_file_size.sh
python3 scripts/check_import_boundaries.py
bash scripts/check_no_secrets.sh
python3 scripts/validate_league_clock.py --quiet

echo "pre-commit: all guards passed."
exit 0
