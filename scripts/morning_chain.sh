#!/usr/bin/env bash
# scripts/morning_chain.sh — the com.ffi.morning job body.
#
# R23 / ADR Domain 1+8: the previous plist chained seven commands with `&&`,
# so ANY failing step silenced scripts/morning_briefing.py — the dashboard
# whose entire job is to report that failure. An FpBudgetExceededError on a
# Sunday would take out the only artifact that could have told the operator
# the budget was exhausted.
#
# Therefore: no `&&`, no `set -e`. Steps are newline-separated (equivalent to
# `;`), each runs regardless of its predecessor, and the briefing runs last.
# The EXIT trap covers abnormal termination (SIGTERM from launchd, a step
# that kills the shell); the `briefing_ran` guard keeps the normal path
# single-render.
#
# ADR Domain 5 (exit-nonzero-on-red): "all steps always run" is NOT "all
# failures are forgiven". `failed` accumulates step rcs so launchd still sees
# a nonzero exit and surfaces the job as failed; the briefing's own rc takes
# precedence because a dead dashboard is the more urgent signal.
set +e
cd "$(dirname "${BASH_SOURCE[0]}")/.." || {
  echo "FATAL: cannot cd to repo root from ${BASH_SOURCE[0]}" >&2
  exit 78
}

# nflverse has no 2026 data until Week 1 publishes (~2026-09-16) and
# nflreadpy.get_current_season() does not roll to 2026 until 2026-09-10, so a
# `2026` request 404s / raises today. FLIP THIS TO "2025-2026" ON 2026-09-16.
# You do not have to remember: config/source_clock.yaml carries
# `expected_season` for both nflverse feeds and scripts/morning_briefing.py
# goes RED the moment the loaded season falls behind it.
FFI_NFLVERSE_SEASONS="${FFI_NFLVERSE_SEASONS:-2025}"

failed=0
briefing_ran=0
briefing_rc=1
run_briefing() {
  [ "$briefing_ran" -eq 1 ] && return 0
  briefing_ran=1
  uv run python scripts/morning_briefing.py
  briefing_rc=$?
  return "$briefing_rc"
}
trap run_briefing EXIT

step() {
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) :: $*"
  "$@"
  local rc=$?
  [ "$rc" -ne 0 ] && failed=1
  echo "=== rc=$rc :: $*"
  # Always 0: a nonzero return here under a future `set -e` would abort the
  # chain, which is the exact bug R23 was about. The failure is recorded in
  # `failed` and surfaces at exit instead.
  return 0
}

step bash scripts/backup_db.sh
step uv run python scripts/ingest_sleeper.py --season 2026
step uv run python scripts/ingest_nflverse.py --seasons "$FFI_NFLVERSE_SEASONS"
step uv run python scripts/ingest_fantasypros.py --daily
step uv run python scripts/score_sleeper_projections.py
step uv run python scripts/build_valuation.py
step uv run python scripts/ingest_fp_news.py --daily

run_briefing
# Briefing rc wins: if the dashboard itself is broken, that is the failure the
# operator must see first. Otherwise report whether any upstream step failed.
[ "$briefing_rc" -ne 0 ] && exit "$briefing_rc"
exit "$failed"
