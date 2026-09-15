#!/usr/bin/env python3
"""Ingest manual captures into the canonical league-state tables.

Reads every capture file in the captures directory (manual.CAPTURES_DIR —
this module deliberately does not name the path; manual.py is the only
module that knows where captures live, per ARCHITECTURE §3a). A file whose
header block declares `team:` is a ROSTER capture (headers + `---` + a raw
Yahoo roster-page paste); anything else is a TRANSACTION capture (the
pipe-delimited format in ffi.league_state.manual).

Player names in a roster paste are resolved through the crosswalk
(public.player_id_xwalk) to the best id the row has — yahoo_id, else
sleeper_id, else gsis_id (2026 rookies have never been in a Yahoo league
we have scraped, so their rows carry only sleeper/gsis ids; every id maps
1:1 back to a crosswalk row). Kickers are stored there as PK; III/Jr
suffixes are stripped on a second pass; a final normalization pass makes
'DJ Moore' match 'D.J. Moore' and 'Kenneth Walker' match 'Kenneth Walker
III'. Defenses resolve via public.team_def_map. ANY unresolved name fails
the whole run loudly — a roster with a hole in it is worse than no roster.

Usage: uv run python scripts/ingest_captures.py [--dir DIR] [--file FILE]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

from ffi import db
from ffi.ids import normalize_team_abbr
from ffi.league_state import adapter
from ffi.league_state.manual import (
    CAPTURES_DIR,
    parse_capture,
    parse_roster_capture,
)

# The Yahoo roster page says K; the crosswalk stores kickers as PK.
_XWALK_POSITION = {"K": "PK"}
# "James Cook III" -> "James Cook" when the exact name misses.
_NAME_SUFFIXES = ("III", "II", "Jr.", "Jr", "Sr.", "Sr")


def _normalize_name(name: str) -> str:
    """Lowercase, punctuation-free, suffix-free, single-spaced — so
    'D.J. Moore' == 'DJ Moore' and 'Kenneth Walker III' == 'Kenneth Walker'."""
    s = name.lower().replace(".", "").replace("'", "").replace("\u2019", "")
    s = re.sub(r"\s+", " ", s).strip()
    for suffix in _NAME_SUFFIXES:
        if s.endswith(" " + suffix.lower()):
            s = s[: -(len(suffix) + 1)]
    return s


def make_resolver(conn):
    """Build (name, position, nfl_team) -> player id, or None.

    ID preference: yahoo_id, else sleeper_id, else gsis_id — every id maps
    1:1 back to a crosswalk row, so a roster row is always joinable to
    identity even when the Yahoo id is not yet known.
    """

    def _ids(name: str, position: str):
        with conn.cursor() as cur:
            cur.execute(
                "SELECT yahoo_id, sleeper_id, gsis_id FROM public.player_id_xwalk "
                "WHERE lower(name) = lower(%s) AND position = %s",
                (name, position),
            )
            return cur.fetchone()

    def _pick(ids):
        return ids[0] or ids[1] or ids[2] or None

    def _lookup(name: str, position: str):
        row = _ids(name, position)
        if row:
            return _pick(row)
        for suffix in _NAME_SUFFIXES:
            if name.endswith(" " + suffix):
                row = _ids(name[: -(len(suffix) + 1)], position)
                if row:
                    return _pick(row)
        return None

    def _normalized_lookup(name: str, position: str):
        target = _normalize_name(name)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, yahoo_id, sleeper_id, gsis_id "
                "FROM public.player_id_xwalk WHERE position = %s",
                (position,),
            )
            for row in cur.fetchall():
                if _normalize_name(row[0]) == target:
                    return _pick(row[1:])
        return None

    def resolve(name: str, position: str, nfl_team: str):
        if position == "DEF":
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT yahoo_def_id FROM public.team_def_map "
                    "WHERE team_abbr = %s",
                    (normalize_team_abbr(nfl_team),),
                )
                row = cur.fetchone()
            return row[0] if row else None
        xw_pos = _XWALK_POSITION.get(position, position)
        hit = _lookup(name, xw_pos)
        if hit:
            return hit
        return _normalized_lookup(name, xw_pos)

    return resolve


def _is_roster(text: str) -> bool:
    head = text.split("---", 1)[0]
    return any(
        line.strip().lower().startswith("team:") for line in head.splitlines()
    )


def ingest_file(path: pathlib.Path, conn, resolve) -> str:
    text = path.read_text()
    if _is_roster(text):
        rows = parse_roster_capture(path, resolve)
        adapter.record(rows, "manual", "date", conn=conn)
        return f"roster: {len(rows)} players"
    tx_rows = parse_capture(path)
    by_precision: dict[str, list] = {}
    for row in tx_rows:
        by_precision.setdefault(row.ts_precision, []).append(row)
    for precision, batch in by_precision.items():
        adapter.record(batch, "manual", precision, conn=conn)
    return f"transactions: {len(tx_rows)} (precisions {sorted(by_precision)})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=pathlib.Path,
        default=CAPTURES_DIR,
        help="captures directory (default: manual.CAPTURES_DIR)",
    )
    parser.add_argument("--file", type=pathlib.Path, help="ingest one file only")
    args = parser.parse_args(argv)

    files = (
        [args.file]
        if args.file
        else sorted(p for p in args.dir.glob("*.txt") if p.is_file())
    )
    if not files:
        print(f"no capture files found in {args.dir}")
        return 1
    conn = db.connect()
    try:
        resolve = make_resolver(conn)
        for path in files:
            summary = ingest_file(path, conn, resolve)
            print(f"OK {path.name}: {summary}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
