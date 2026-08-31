#!/usr/bin/env bash
# scripts/check_file_size.sh — fails if any Python source file exceeds its max lines.
#
# Budgets come from the module-list tables in ARCHITECTURE.md (section 1). A row is an
# override when column 1 is a backticked path and column 3 is a number:
#
#   | `src/ffi/sim/backtest.py` | Backtest harness — do not grow | 800 |
#   | `src/ffi/valuation/`      | Starts-weighted valuation v2   | 300 |
#
# Paths ending in "/" are directory prefixes (apply to every .py beneath them).
# Paths ending in ".py" are exact-file overrides and win over directory prefixes.
# Anything with no matching override gets DEFAULT_MAX.
#
# Scans src/ and scripts/ only. Skips __pycache__, .venv, archive/.
# Contract: ARCHITECTURE.md §1 (module list) and §5 (file-size budget).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCH="$ROOT/ARCHITECTURE.md"
DEFAULT_MAX=600

OVERRIDES="$(mktemp)"
trap 'rm -f "$OVERRIDES"' EXIT

if [ -f "$ARCH" ]; then
  # Only section 1 (the module list) carries budgets. Other sections have tables with
  # backticked paths too, and reading them would invent nonsense budgets.
  awk '/^## 1\. Module list/{s=1; next} /^## 2\./{s=0} s' "$ARCH" \
    | grep -E '^\| *`[^`]+` *\|' \
    | awk -F'|' '{
        p = $2; m = $4;
        gsub(/`/, "", p); gsub(/^[ \t]+|[ \t]+$/, "", p);
        if (match(m, /[0-9]+/)) {
          n = substr(m, RSTART, RLENGTH);
          if (p != "" && n != "") print p "\t" n;
        }
      }' > "$OVERRIDES"
else
  echo "FAIL: ARCHITECTURE.md not found at $ARCH — no budgets to enforce." >&2
  exit 1
fi

max_for() {
  # $1 = repo-relative path. Longest matching prefix wins; exact match beats any prefix.
  local rel="$1" max="$DEFAULT_MAX" best=0 p m
  while IFS=$'\t' read -r p m; do
    [ -n "$p" ] || continue
    case "$p" in
      */)
        if [ "${rel#"$p"}" != "$rel" ] && [ "${#p}" -gt "$best" ]; then
          max="$m"; best="${#p}"
        fi
        ;;
      *)
        if [ "$rel" = "$p" ]; then
          max="$m"; best=9999
        fi
        ;;
    esac
  done < "$OVERRIDES"
  printf '%s' "$max"
}

VIOLATIONS=0
CHECKED=0

# Process substitution (not a pipe) so VIOLATIONS survives the loop.
while IFS= read -r f; do
  rel="${f#"$ROOT"/}"
  lines=$(wc -l < "$f" | tr -d ' ')
  max=$(max_for "$rel")
  CHECKED=$((CHECKED + 1))
  if [ "$lines" -gt "$max" ]; then
    echo "FAIL: $rel has $lines lines (max $max). Split before adding features. See ARCHITECTURE.md §5."
    VIOLATIONS=$((VIOLATIONS + 1))
  fi
done < <(
  find "$ROOT/src" "$ROOT/scripts" -type f -name '*.py' \
    -not -path '*/__pycache__/*' \
    -not -path '*/.venv/*' \
    -not -path '*/archive/*' \
    2>/dev/null | sort
)

if [ "$VIOLATIONS" -ne 0 ]; then
  echo "FAIL: $VIOLATIONS file(s) over budget out of $CHECKED checked."
  exit 1
fi

echo "OK: all $CHECKED Python files within their ARCHITECTURE.md size budget (default $DEFAULT_MAX)."
