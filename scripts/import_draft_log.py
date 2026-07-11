#!/usr/bin/env python3
"""Post-draft importer: replays a draft's JSONL event log (`ffi.draft.state`)
and archives every event into `draft.events` (migration 006) for after-action
analysis. The JSONL file remains the draft-day source of truth; this table
is a durable copy taken once the draft is over.

Refuses (SystemExit) if the draft_id already has rows, unless `--replace` is
given, in which case existing rows are deleted and reinserted in the same
transaction. Corruption policy matches `DraftLog.replay`: a torn final line
is tolerated (banner printed); any other corruption raises and aborts.
"""
import argparse
import json
import pathlib

from ffi.db import connect
from ffi.draft.state import DraftLog


def import_log(conn, log_path: pathlib.Path, draft_id: str, replace: bool) -> int:
    _, events, torn_tail = DraftLog.replay(log_path)
    if torn_tail:
        print(f"WARNING: torn final line dropped from {log_path} (crash mid-write)")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM draft.events WHERE draft_id = %s", (draft_id,)
        )
        existing = cur.fetchone()[0]
        if existing > 0 and not replace:
            raise SystemExit(
                f"draft.events already has {existing} row(s) for draft_id={draft_id!r}; "
                "pass --replace to overwrite"
            )
        if existing > 0:
            cur.execute("DELETE FROM draft.events WHERE draft_id = %s", (draft_id,))

        for event in events:
            cur.execute(
                "INSERT INTO draft.events (draft_id, seq, ts, kind, payload)"
                " VALUES (%s, %s, %s, %s, %s)",
                (draft_id, event.seq, event.ts, event.kind, json.dumps(event.payload)),
            )
    conn.commit()
    return len(events)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--log", required=True, type=pathlib.Path, help="path to the draft JSONL log"
    )
    ap.add_argument(
        "--draft-id", required=True, help="e.g. 2026-real, 2026-rehearsal-2"
    )
    ap.add_argument(
        "--replace", action="store_true", help="delete existing rows for draft_id first"
    )
    args = ap.parse_args()

    conn = connect()
    count = import_log(conn, args.log, args.draft_id, args.replace)
    print(f"imported {count} row(s) into draft.events for draft_id={args.draft_id!r}")


if __name__ == "__main__":
    main()
