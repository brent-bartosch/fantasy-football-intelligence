#!/usr/bin/env python3
"""Propose manual-override crosswalk rows for Yahoo players unmatched because
ff_playerids has null yahoo_id (2025 rookies — risk R6). Matches by exact
(lower(name), position) against xwalk rows missing a yahoo_id. Ambiguous or
unmatched names are printed for the human — never guessed."""
import argparse

from ffi.db import connect
from ffi.ingest.crosswalk import assert_no_duplicate_ids, match_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    conn = connect()
    report = match_report(conn)
    print(f"{len(report['unmatched'])} unmatched fantasy-relevant Yahoo players")
    applied, ambiguous, unfound = 0, [], []
    for name, pos, yid in report["unmatched"]:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT xwalk_id, name, gsis_id, sleeper_id, fantasypros_id
                   FROM public.player_id_xwalk
                   WHERE lower(name)=lower(%s) AND position=%s AND yahoo_id IS NULL
                     AND manual_override = FALSE""",
                (name, pos),
            )
            cands = cur.fetchall()
        if len(cands) == 0:
            unfound.append((name, pos, yid))
            continue
        if len(cands) > 1:
            ambiguous.append((name, pos, yid, cands))
            continue
        xid, xname, gsis, sleeper, fp = cands[0]
        print(
            f"  MATCH {name} ({pos}) yahoo={yid} -> xwalk#{xid} gsis={gsis} sleeper={sleeper}"
        )
        if args.apply:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO public.player_id_xwalk
                       (name, position, gsis_id, sleeper_id, yahoo_id, fantasypros_id, manual_override)
                       VALUES (%s,%s,%s,%s,%s,%s,TRUE)""",
                    (xname, pos, gsis, sleeper, yid, fp),
                )
            conn.commit()
        applied += 1
    if args.apply:
        from ffi.ingest.crosswalk import dedupe_auto_vs_manual

        dedupe_auto_vs_manual(conn)
        assert_no_duplicate_ids(conn)
    print(
        f"proposed/applied={applied} ambiguous={len(ambiguous)} no-candidate={len(unfound)}"
    )
    for item in ambiguous:
        print("  AMBIGUOUS:", item[:3])
    for item in unfound:
        print("  NO-CANDIDATE:", item)
    print("APPLIED" if args.apply else "DRY RUN — review matches, rerun with --apply")


if __name__ == "__main__":
    main()
