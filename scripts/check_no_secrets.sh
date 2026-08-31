#!/usr/bin/env bash
# scripts/check_no_secrets.sh — secret hygiene guard.
#
# Two checks:
#   1. .env.example carries field NAMES only. Real values live in .env (gitignored).
#   2. No ntfy topic URL is committed anywhere. An ntfy topic is a capability URL —
#      anyone holding it can push to the operator's phone (ADR 2026-08-30, Domain 3).
#
# Contract: ARCHITECTURE.md §1c (data contracts) and §7 (conventions).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SELF="scripts/check_no_secrets.sh"
FAILED=0

# --- 1. .env.example must contain field names only ----------------------------------
if [ ! -f .env.example ]; then
  echo "SKIP: no .env.example found."
else
  # Lines like KEY=abc123... where the value is 20+ chars and not a placeholder.
  if grep -nE '=[A-Za-z0-9_\-]{20,}' .env.example \
      | grep -vE '=\s*$|=#|placeholder|your_|example|EXAMPLE|REPLACE|CHANGEME|XXXX|<.*>' \
      | grep -v '^[0-9]*:#'; then
    echo "FAIL: .env.example contains what looks like a real token value."
    echo "      Field names only. Real values go in .env (gitignored)."
    FAILED=1
  fi
fi

# --- 2. no committed ntfy topic URL --------------------------------------------------
# Matches https://ntfy.sh/<topic> with a topic long enough to be a real one.
# A bare 'ntfy.sh/' reference in prose does not match.
NTFY_HITS="$(
  git ls-files -z 2>/dev/null \
    | xargs -0 grep -nIE 'ntfy\.sh/[A-Za-z0-9_-]{8,}' 2>/dev/null \
    | grep -v "^${SELF}:" \
    | grep -vE 'placeholder|your_|example|EXAMPLE|REPLACE|CHANGEME|XXXX|<.*>' \
    || true
)"
if [ -n "$NTFY_HITS" ]; then
  echo "$NTFY_HITS"
  echo "FAIL: an ntfy topic URL is committed. The topic is a capability URL —"
  echo "      anyone holding it can push to the operator's phone."
  echo "      Move it to NTFY_TOPIC_URL in .env; leave a placeholder in .env.example."
  FAILED=1
fi

# --- 3. gitignore must still cover the secret paths ----------------------------------
for pat in '.env' 'config/yahoo_token.json' 'data/captures/'; do
  if ! grep -qF -- "$pat" .gitignore 2>/dev/null; then
    echo "FAIL: .gitignore is missing an entry for '$pat' (ARCHITECTURE.md §1c)."
    FAILED=1
  fi
done

if [ "$FAILED" -ne 0 ]; then
  exit 1
fi

echo "OK: .env.example has field names only; no ntfy topic committed; .gitignore covers secrets."
