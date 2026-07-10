import json

import requests

from ffi.ingest.base import BaseIngester, IngestError

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
BASE_URL = "https://api.sleeper.app/projections/nfl"


class SleeperProjectionsIngester(BaseIngester):
    source = "sleeper_projections"

    # The project's core edge depends on first downs being projected. Fail
    # loud per-position rather than on the payload-wide union: a position
    # whose FD field silently vanishes should trip even if other positions'
    # FD fields are still present (carry-forward from Phase 1's union check).
    _FD_BY_POSITION = {"QB": "pass_fd", "RB": "rush_fd", "WR": "rec_fd", "TE": "rec_fd"}

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
        counts = {pos: [0, 0] for pos in self._FD_BY_POSITION}  # [with_fd, total]
        for rec in payload:
            if "stats" not in rec or "player_id" not in rec:
                raise IngestError(
                    f"sleeper: record missing 'stats'/'player_id' — schema drift? record: {json.dumps(rec)[:300]}"
                )
            pos = (rec.get("player") or {}).get("position")
            if pos in counts:
                counts[pos][1] += 1
                if self._FD_BY_POSITION[pos] in rec["stats"]:
                    counts[pos][0] += 1
        for pos, (with_fd, total) in counts.items():
            if total and with_fd / total < 0.5:
                raise IngestError(
                    f"sleeper: {self._FD_BY_POSITION[pos]} present in only {with_fd}/{total} "
                    f"{pos} records — partial FD drift breaks FD pricing (design 4.2/R5)."
                )
        return len(payload)

    def store(self, conn, run_id: int, payload) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.sleeper_projections (run_id, season, week, payload) VALUES (%s,%s,%s,%s)",
                (run_id, self.season, self.week, json.dumps(payload)),
            )
