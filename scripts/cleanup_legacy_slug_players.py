#!/usr/bin/env python3
"""Remove legacy slug-format player rows (nfl.p.<name>) — duplicates of
numeric-id rows from the old import. FK-safe: referenced rows are remapped to
their numeric twin when the match is unambiguous, otherwise reported and kept."""
import argparse

from ffi.db import connect
from ffi.ids import yahoo_numeric_id_filter_sql

FK_TABLES = [
    ("draft_picks", "player_id"),
    ("player_stats", "player_id"),
    ("trade_details", "player_id"),
    ("transactions", "player_id"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply", action="store_true", help="actually delete/remap (default: dry run)"
    )
    args = ap.parse_args()
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT player_id, yahoo_player_id, player_name, position FROM players
                WHERE NOT ({yahoo_numeric_id_filter_sql('yahoo_player_id')})"""
        )
        slug_rows = cur.fetchall()
    print(f"{len(slug_rows)} legacy slug rows found")

    deleted = remapped = kept = 0
    for pid, ykey, name, pos in slug_rows:
        refs = {}
        with conn.cursor() as cur:
            for table, col in FK_TABLES:
                cur.execute(f"SELECT count(*) FROM {table} WHERE {col}=%s", (pid,))
                n = cur.fetchone()[0]
                if n:
                    refs[table] = n
            if not refs:
                if args.apply:
                    cur.execute("DELETE FROM players WHERE player_id=%s", (pid,))
                deleted += 1
                conn.commit()
                continue
            # referenced: find the unambiguous numeric twin by (name, position)
            cur.execute(
                f"""SELECT player_id FROM players
                    WHERE player_name=%s AND position=%s AND player_id<>%s
                      AND {yahoo_numeric_id_filter_sql('yahoo_player_id')}""",
                (name, pos, pid),
            )
            twins = cur.fetchall()
            if len(twins) != 1:
                print(
                    f"  KEEP (ambiguous twin x{len(twins)}): {name} ({pos}) {ykey} refs={refs}"
                )
                kept += 1
                conn.commit()
                continue
            twin_id = twins[0][0]
            if args.apply:
                for table, col in FK_TABLES:
                    cur.execute(
                        f"UPDATE {table} SET {col}=%s WHERE {col}=%s", (twin_id, pid)
                    )
                cur.execute("DELETE FROM players WHERE player_id=%s", (pid,))
            remapped += 1
        conn.commit()
    print(
        f"deleted={deleted} remapped={remapped} kept={kept} "
        f"({'APPLIED' if args.apply else 'DRY RUN — rerun with --apply'})"
    )
    if kept:
        print("Kept rows need manual resolution — report to the user.")


if __name__ == "__main__":
    main()
