#!/usr/bin/env python3
"""Load dynastyprocess/nflverse ff_playerids into public.player_id_xwalk and report match coverage."""
from ffi.db import connect
from ffi.ingest.crosswalk import load_xwalk_rows, match_report

conn = connect()
n = load_xwalk_rows(conn)
print(f"Loaded {n} crosswalk rows")
report = match_report(conn)
pct = 100 * report["matched"] / max(report["total_fantasy_players"], 1)
print(
    f"Yahoo match coverage: {report['matched']}/{report['total_fantasy_players']} ({pct:.1f}%)"
)
print(
    f"DEF excluded from coverage (team-abbr mapping, Phase 2); "
    f"{report['def_rows']} DEF rows present."
)
print(
    f"{report['legacy_slug_rows']} legacy slug-format player rows excluded "
    "(duplicates of numeric-ID rows; cleanup deferred to Phase 2)"
)
print(f"Unmatched fantasy-relevant players: {len(report['unmatched'])}")
for name, pos, yid in report["unmatched"][:40]:
    print(f"  UNMATCHED: {name} ({pos}) yahoo_id={yid}")
if pct < 90:
    raise SystemExit(
        "Coverage <90% — investigate before Phase 2 (risk R6). Historical/retired "
        "players may legitimately be absent; current-season misses are the concern."
    )
