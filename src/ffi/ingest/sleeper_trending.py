"""Daily archive of Sleeper's trending add/drop lists (ADR Precondition P2).

Sleeper exposes only a rolling `lookback_hours` window and has no history
endpoint (R8). This module is the sole reason a same-season market-signal
validation will be possible from ~week 6 onward, so it stores the payload
untouched, dated, and immutable — no normalization, no dedupe, no upsert.
Normalization into `public.market_trends` happens later, from these rows.
"""
import datetime
import json
from zoneinfo import ZoneInfo

import requests
import structlog

from ffi.ingest.base import BaseIngester, IngestError

log = structlog.get_logger()

BASE_URL = "https://api.sleeper.app/v1/players/nfl/trending"
TRA_TYPES = ("add", "drop")
LEAGUE_TZ = ZoneInfo("America/Los_Angeles")

# The live endpoint returns `limit` rows when healthy. A payload this thin is
# either an outage or a semantic change; either way it must not be archived
# as if it were a normal day.
MIN_ROWS_PER_DIRECTION = 50


class SleeperTrendingIngester(BaseIngester):
    source = "sleeper_trending"

    def __init__(self, lookback_hours: int = 24, limit: int = 200):
        self.lookback_hours = lookback_hours
        self.limit = limit

    def fetch(self) -> dict:
        out = {}
        for trend_type in TRA_TYPES:
            resp = requests.get(
                f"{BASE_URL}/{trend_type}",
                params={"lookback_hours": self.lookback_hours, "limit": self.limit},
                timeout=30,
            )
            resp.raise_for_status()
            out[trend_type] = resp.json()
        return out

    def validate(self, payload) -> int:
        if not isinstance(payload, dict):
            raise IngestError(
                f"sleeper_trending: expected a dict keyed by trend type, got "
                f"{type(payload).__name__}: {str(payload)[:200]}"
            )
        total = 0
        for trend_type in TRA_TYPES:
            if trend_type not in payload:
                raise IngestError(
                    f"sleeper_trending: payload missing '{trend_type}' — "
                    f"present keys: {sorted(payload)}"
                )
            rows = payload[trend_type]
            if not isinstance(rows, list):
                raise IngestError(
                    f"sleeper_trending: '{trend_type}' is {type(rows).__name__}, expected list"
                )
            if len(rows) < MIN_ROWS_PER_DIRECTION:
                raise IngestError(
                    f"sleeper_trending: '{trend_type}' returned only {len(rows)} rows "
                    f"(floor {MIN_ROWS_PER_DIRECTION}) — refusing to archive a "
                    f"degraded day as if it were normal (R8: the archive is unrecoverable)"
                )
            for rec in rows:
                if (
                    not isinstance(rec, dict)
                    or "player_id" not in rec
                    or "count" not in rec
                ):
                    raise IngestError(
                        f"sleeper_trending: '{trend_type}' record missing "
                        f"player_id/count — schema drift? record: {json.dumps(rec)[:200]}"
                    )
            total += len(rows)
        return total

    def store(self, conn, run_id: int, payload) -> None:
        archive_date = (
            datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ).date()
        )
        with conn.cursor() as cur:
            for trend_type in TRA_TYPES:
                cur.execute(
                    "INSERT INTO raw.sleeper_trending "
                    "(run_id, archive_date, trend_type, lookback_hours, payload) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        run_id,
                        archive_date,
                        trend_type,
                        self.lookback_hours,
                        json.dumps(payload[trend_type]),
                    ),
                )
        log.info(
            "sleeper_trending.archived",
            archive_date=archive_date.isoformat(),
            add_rows=len(payload["add"]),
            drop_rows=len(payload["drop"]),
        )
