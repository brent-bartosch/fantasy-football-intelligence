"""Daily archive of Sleeper's trending add/drop lists (ADR Precondition P2).

Sleeper exposes only a rolling `lookback_hours` window and has no history
endpoint (R8). This module is the sole reason a same-season market-signal
validation will be possible from ~week 6 onward, so it stores the payload
untouched, dated, and immutable — no normalization, no dedupe, no upsert.
Normalization into `public.market_trends` happens later, from these rows.
"""
import datetime
import json
import time
from zoneinfo import ZoneInfo

import requests
import structlog

from ffi.ingest.base import BaseIngester, IngestError
from ffi.ingest.gates import (
    check_fieldset,
    check_nonzero_coverage,
    check_rank_correlation,
    union_keys,
)

log = structlog.get_logger()

BASE_URL = "https://api.sleeper.app/v1/players/nfl/trending"
TREND_TYPES = ("add", "drop")
LEAGUE_TZ = ZoneInfo("America/Los_Angeles")

# The server clamps trending to 100 rows per direction no matter what `limit`
# asks for — probed 2026-08-31, limit=200 and limit=500 both returned exactly
# 100. So a healthy day is 100 rows, not `limit`, and the floor below is 50%
# of a healthy day. Thinner than half a full list is an outage or a semantic
# change, and must not be archived as if it were a normal day.
SERVER_ROW_CAP = 100
MIN_ROWS_PER_DIRECTION = 50

# One missed day is unrecoverable (R8), so a transient network blip or a
# Sleeper 5xx gets a bounded second and third try before the job fails.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (2, 4)  # slept after attempt 1 and attempt 2


class SleeperTrendingIngester(BaseIngester):
    source = "sleeper_trending"

    # Observe-and-log, not hard-fail: this archive is unrecoverable (R8), so
    # refusing to store a suspect day destroys more than it protects. The run
    # is recorded 'sanity_warned' and ffi.health.state() then refuses to
    # render it OK, so the operator sees it in the briefing the next morning.
    sanity_mode = "warn"

    # The `add` list is what the urgency overlay will read, so it is the list
    # the gate watches. Deliberately equal to MIN_ROWS_PER_DIRECTION rather
    # than to a healthy day: the server caps each direction at 100 rows
    # (SERVER_ROW_CAP, probed 2026-08-31), so a floor of 100 positive counts
    # would fire whenever a single archived row carried count<=0, and on every
    # 50-99 row day that validate() legitimately admits. This floor adds what
    # validate() cannot see — rows present but their counts collapsed to zero.
    MIN_POSITIVE_COUNTS = MIN_ROWS_PER_DIRECTION

    def __init__(self, lookback_hours: int = 24, limit: int = SERVER_ROW_CAP):
        self.lookback_hours = lookback_hours
        self.limit = limit

    def _fetch_direction(self, trend_type: str) -> list:
        """GET one direction, retrying transport errors and 5xx only.

        A 4xx is deterministic — a bad request, a moved route, a rate-limit
        policy — so retrying it just burns the window; it fails immediately.
        Exhausting the attempts raises IngestError naming the direction and
        the last error. Nothing here is swallowed.
        """
        last_error = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                resp = requests.get(
                    f"{BASE_URL}/{trend_type}",
                    params={"lookback_hours": self.lookback_hours, "limit": self.limit},
                    timeout=30,
                )
            except requests.RequestException as exc:
                last_error = exc
            else:
                if 400 <= resp.status_code < 500:
                    raise IngestError(
                        f"sleeper_trending: '{trend_type}' returned HTTP "
                        f"{resp.status_code} — client error, not retried: "
                        f"{resp.text[:200]}"
                    )
                if resp.status_code >= 500:
                    last_error = requests.HTTPError(
                        f"HTTP {resp.status_code}", response=resp
                    )
                else:
                    return resp.json()
            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_BACKOFF_SECONDS[attempt - 1]
                log.warning(
                    "sleeper_trending.retry",
                    trend_type=trend_type,
                    attempt=attempt,
                    sleep_s=delay,
                    error=str(last_error),
                )
                time.sleep(delay)
        raise IngestError(
            f"sleeper_trending: '{trend_type}' failed after {RETRY_ATTEMPTS} "
            f"attempts — last error {type(last_error).__name__}: {last_error}"
        ) from last_error

    def fetch(self) -> dict:
        return {t: self._fetch_direction(t) for t in TREND_TYPES}

    def validate(self, payload) -> int:
        if not isinstance(payload, dict):
            raise IngestError(
                f"sleeper_trending: expected a dict keyed by trend type, got "
                f"{type(payload).__name__}: {str(payload)[:200]}"
            )
        total = 0
        for trend_type in TREND_TYPES:
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

    def _prior_add_rows(self, conn) -> list | None:
        """The `add` payload from the most recent EARLIER archive day.

        Strictly earlier, not `<=`: a same-day retry must not correlate
        against the snapshot it is retrying.
        """
        today = (
            datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ).date()
        )
        with conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM raw.sleeper_trending "
                "WHERE trend_type='add' AND archive_date < %s "
                "ORDER BY archive_date DESC, snapshot_id DESC LIMIT 1",
                (today,),
            )
            row = cur.fetchone()
        return None if row is None else row[0]

    def sanity_check(self, conn, payload) -> None:
        adds = payload["add"]
        check_nonzero_coverage(
            adds,
            feed=self.source,
            value_key="count",
            min_players=self.MIN_POSITIVE_COUNTS,
        )
        prior_rows = self._prior_add_rows(conn)
        if prior_rows is None:
            return  # day one: nothing to compare against
        # Union across all rows, not row[0]: a key that appears on (or
        # vanishes from) record 40 alone is invisible to a single-record
        # comparison, and nothing guarantees Sleeper's rows stay uniform.
        check_fieldset(union_keys(prior_rows), union_keys(adds), feed=self.source)
        # Uncoerced on purpose — check_rank_correlation floats behind its own
        # guard, so a non-numeric count fails as a named gate error.
        prior = {rec["player_id"]: rec["count"] for rec in prior_rows}
        curr = {rec["player_id"]: rec["count"] for rec in adds}
        check_rank_correlation(prior, curr, feed=self.source)

    def store(self, conn, run_id: int, payload) -> None:
        archive_date = (
            datetime.datetime.now(datetime.timezone.utc).astimezone(LEAGUE_TZ).date()
        )
        with conn.cursor() as cur:
            for trend_type in TREND_TYPES:
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
