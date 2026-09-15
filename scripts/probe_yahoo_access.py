#!/usr/bin/env python3
"""Daily authenticated Yahoo probe -> `raw.ingest_runs` under source
`yahoo_probe` (ADR Domain 4/6). No retry on a 403 — an authorization state,
not a transient error (the Yahoo transport already refuses to retry it).

The probe records TWO things the morning can read: (1) is the token grant
still alive, and (2) is the Fantasy Sports product gate still open? The
second is the one that mattered: the grant worked for weeks while every
league call 403'd (approval pending 2026-08-24 → 2026-09-14). One
league-agnostic call (the user's games list) proves both in a single run.
"""
from __future__ import annotations

import sys

import requests

from ffi import db
from ffi import yahoo_client

SOURCE = "yahoo_probe"
GAMES_URL = (
    "https://fantasysports.yahooapis.com/fantasy/v2/users;use_login=1/games?format=json"
)


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
        sc = yahoo_client.get_session()
        resp = requests.get(
            GAMES_URL,
            headers={"Authorization": "Bearer " + sc.access_token},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"fantasy API returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        _finish(conn, run_id, "success")
        print("OK: token grant + fantasy API access")
        return 0
    except Exception as exc:  # noqa: BLE001 — recorded, then re-raised as nonzero
        _finish(conn, run_id, "failed", error=str(exc))
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
