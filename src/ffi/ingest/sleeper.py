import json

import requests

from ffi.ingest.base import BaseIngester, IngestError

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
BASE_URL = "https://api.sleeper.app/projections/nfl"
# The project's core edge depends on first downs being projected. Fail loud if they vanish.
FIRST_DOWN_FIELDS = {"pass_fd", "rush_fd", "rec_fd"}


class SleeperProjectionsIngester(BaseIngester):
    source = "sleeper_projections"

    def __init__(self, season: int, week: int | None):
        self.season = season
        self.week = week

    def fetch(self):
        url = f"{BASE_URL}/{self.season}"
        if self.week is not None:
            url = f"{url}/{self.week}"
        params = [("season_type", "regular")] + [("position[]", p) for p in POSITIONS]
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def validate(self, payload) -> int:
        if not isinstance(payload, list) or not payload:
            raise IngestError(
                f"sleeper: empty or non-list payload: {str(payload)[:200]}"
            )
        seen_fd = set()
        for rec in payload:
            if "stats" not in rec or "player_id" not in rec:
                raise IngestError(
                    f"sleeper: record missing 'stats'/'player_id' — schema drift? record: {json.dumps(rec)[:300]}"
                )
            seen_fd |= FIRST_DOWN_FIELDS & set(rec["stats"].keys())
        if seen_fd != FIRST_DOWN_FIELDS:
            raise IngestError(
                f"sleeper: first-down fields missing from entire payload: {FIRST_DOWN_FIELDS - seen_fd}. "
                "This breaks FD pricing (design 4.2). Inspect payload before proceeding."
            )
        return len(payload)

    def store(self, conn, run_id: int, payload) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.sleeper_projections (run_id, season, week, payload) VALUES (%s,%s,%s,%s)",
                (run_id, self.season, self.week, json.dumps(payload)),
            )
