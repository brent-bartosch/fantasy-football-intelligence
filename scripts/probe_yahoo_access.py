#!/usr/bin/env python3
"""Daily authenticated Yahoo probe -> `raw.ingest_runs` under source
`yahoo_probe` (ADR Domain 4/6). No retry on a 403 — an authorization state,
not a transient error (the Yahoo transport already refuses to retry it).

The probe records ONE thing the briefing can read every morning: is the Yahoo
token grant still alive? A 403 on league DATA (not the token) needs a follow-up
league call the operator runs manually; the daily job only proves the grant.
Decision date 2026-09-10: if the probe is still failing by then, the Yahoo
backfill path stays de-scoped.
"""
from __future__ import annotations

import sys

from ffi import db
from ffi import yahoo_client

SOURCE = "yahoo_probe"


def _finish(conn, run_id: int, status: str, error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE raw.ingest_runs SET finished_at=now(), status=%s, error=%s "
            "WHERE run_id=%s",
            (status, error, run_id),
        )
    conn.commit()


def main() -> int:
    conn = db.connect()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.ingest_runs (source) VALUES (%s) RETURNING run_id",
            (SOURCE,),
        )
        run_id = cur.fetchone()[0]
    conn.commit()
    try:
        # get_session() refreshes the token if stale: a live grant is the first
        # and cheapest signal that Yahoo access is (still) possible.
        yahoo_client.get_session()
        _finish(conn, run_id, "success")
        print("OK: yahoo token grant valid")
        return 0
    except Exception as exc:  # noqa: BLE001 — recorded, then re-raised as nonzero
        _finish(conn, run_id, "failed", error=str(exc))
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
