"""FantasyPros public API v2 client. HARD BUDGET: every successful call writes
one raw.fp_snapshots row; the guard counts today's rows and ABORTS (never
degrades) when the budget would be exceeded (ADR Domain 6, runbook
docs/runbooks/fantasypros-api.md). ToS: never store historical player stats."""
import json
import os
import time

import requests

from ffi.ingest.base import IngestError

BASE_URL = "https://api.fantasypros.com/public/v2/json/nfl"
DAILY_BUDGET = 30
CALL_SPACING_S = 1.1


class FpBudgetExceededError(Exception):
    """Daily FP call budget would be exceeded. Aborting is the design: the cap
    protects a discretionary-approval key (R12). Re-run tomorrow or read cache."""


def fp_calls_today(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM raw.fp_snapshots WHERE fetched_at::date = now()::date"
        )
        return cur.fetchone()[0]


def latest_fp_payload(conn, endpoint_like: str, params_subset: dict):
    """Cache reader: most recent snapshot whose endpoint matches and whose params
    contain params_subset. Ad-hoc consumers use THIS, never the API."""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT payload FROM raw.fp_snapshots
               WHERE endpoint = %s AND params @> %s::jsonb
               ORDER BY fetched_at DESC LIMIT 1""",
            (endpoint_like, json.dumps(params_subset)),
        )
        row = cur.fetchone()
    return row[0] if row else None


class FpClient:
    def __init__(
        self, conn, api_key: str | None = None, daily_budget: int = DAILY_BUDGET
    ):
        self.conn = conn
        self.api_key = api_key or os.getenv("FANTASYPROS_API_KEY")
        if not self.api_key:
            raise IngestError("FANTASYPROS_API_KEY missing from .env")
        self.daily_budget = daily_budget
        self._last_call = 0.0

    def _http_get(self, url: str, params: dict) -> dict:
        resp = requests.get(
            url, params=params, headers={"x-api-key": self.api_key}, timeout=30
        )
        resp.raise_for_status()
        return resp.json()

    def get(self, endpoint: str, params: dict, season: int | None = None) -> dict:
        used = fp_calls_today(self.conn)
        if used + 1 > self.daily_budget:
            raise FpBudgetExceededError(
                f"FP budget: {used}/{self.daily_budget} calls already used today — "
                "refusing this call. Read the cache (latest_fp_payload) or wait for tomorrow."
            )
        wait = CALL_SPACING_S - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        url = f"{BASE_URL}/{season}/{endpoint}" if season else f"{BASE_URL}/{endpoint}"
        try:
            payload = self._http_get(url, params)
        finally:
            self._last_call = time.monotonic()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.fp_snapshots (endpoint, params, payload) VALUES (%s,%s,%s)",
                (
                    endpoint,
                    json.dumps({**params, "season": season}),
                    json.dumps(payload),
                ),
            )
        self.conn.commit()
        return payload
