import json

import requests
import structlog

from ffi.ingest.base import BaseIngester, IngestError

log = structlog.get_logger()

POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
BASE_URL = "https://api.sleeper.app/projections/nfl"


class SleeperProjectionsIngester(BaseIngester):
    """Ingests Sleeper season/week projections.

    Native Sleeper first-down projections (pass_fd/rush_fd/rec_fd) were
    rejected as a scoring input on 2026-07-09: verified ~2x inflated against
    nflverse 2019-2025 ground truth (see ffi.scoring.fd_impute and
    docs/research/2026-07-09-fd-imputation-divergence.md). ALL projection
    scoring now uses FD imputed from nflverse-fitted rates
    (ffi.scoring.fd_impute.impute_fd); native *_fd fields are not consumed
    anywhere downstream. Their presence is therefore only monitored (a
    structlog warning on low coverage), never a hard validation gate — see
    _VOLUME_BY_POSITION below for the fields that actually are load-bearing.
    """

    source = "sleeper_projections"

    # The project's core edge depends on per-position volume being
    # projected: these feed both FD imputation (fit_fd_rates/impute_fd take
    # carries/receptions/completions) and the scoring weights themselves
    # (e.g. pass_completions is directly weighted in config/scoring/v1.json).
    # Fail loud per-position rather than on the payload-wide union: a
    # position whose volume field silently vanishes should trip even if
    # other positions' fields are still present (carry-forward from Phase
    # 1's union check).
    #
    # QB uses pass_cmp (completions), not pass_att: pass_att is explicitly
    # unscored/ignored in ffi.scoring.sleeper_adapter ("not individually
    # scored (cmp/inc are)") and is not consumed by FD imputation either —
    # it is not load-bearing, so it would be the wrong field to guard.
    _VOLUME_BY_POSITION = {"QB": "pass_cmp", "RB": "rush_att", "WR": "rec", "TE": "rec"}

    # Diagnostic only (unconsumed by scoring — see class docstring): coverage
    # is logged, never blocks ingestion.
    _FD_BY_POSITION = {"QB": "pass_fd", "RB": "rush_fd", "WR": "rec_fd", "TE": "rec_fd"}

    # Live-verified 2026-07-09: at every position, most player_id records
    # tagged with a scored position carry ONLY ADP/metadata keys (adp_*,
    # pos_adp_*, pts_*, gp, cmp_pct) and no per-play stat projection at all —
    # inactive/deep-bench players Sleeper lists but doesn't project (e.g. QB:
    # 279/355 such records; RB 536/674; WR 1152/1364; TE 513/640). These are
    # not schema drift and never score any points; counting them in the
    # per-position denominator makes the <50% guard trip on every position on
    # every normal payload (verified: it would newly break RB/WR/TE, which
    # passed under the pre-amendment FD-only check). The denominator is
    # therefore restricted to records with at least one non-metadata stat key
    # ("meaningfully projected" players) — the guard still fires if a
    # load-bearing volume key vanishes among players Sleeper is actually
    # projecting.
    _METADATA_PREFIXES = ("adp_", "pos_adp_", "pts_")
    _METADATA_EXACT = {"gp", "cmp_pct"}

    @classmethod
    def _is_meaningfully_projected(cls, stats: dict) -> bool:
        for key in stats:
            if key in cls._METADATA_EXACT:
                continue
            if any(key.startswith(p) for p in cls._METADATA_PREFIXES):
                continue
            return True
        return False

    # R5 finding: the ratio guard above (with_volume/total) shares its
    # denominator with its numerator — both count "meaningfully projected"
    # records. If a volume key vanishes from the payload entirely, the
    # denominator shrinks right along with the numerator, so the ratio can
    # still read 100% and pass; at total==0 the ratio check's `if total`
    # short-circuits and skips validation altogether. The guard goes silent
    # exactly when the data is most degraded. This floor is independent of
    # the ratio: it bounds the meaningfully-projected population itself
    # against an absolute size, so a population collapse trips even when the
    # ratio can't see it. Live-verified 2026-07-09 season-level
    # meaningfully-projected totals: QB 279, RB 536 (WR/TE higher still) —
    # floors are set to ~1/4 of observed so legitimate off-season thinning
    # never trips this, but a population collapse does. Class attribute (not
    # a constant) so tests with small fixtures can override it; the live
    # default below is what production ingestion actually runs with.
    MIN_PROJECTED = {"QB": 60, "RB": 120, "WR": 150, "TE": 60}

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
        # [with_volume, with_fd, total] per position.
        counts = {pos: [0, 0, 0] for pos in self._VOLUME_BY_POSITION}
        for rec in payload:
            if "stats" not in rec or "player_id" not in rec:
                raise IngestError(
                    f"sleeper: record missing 'stats'/'player_id' — schema drift? record: {json.dumps(rec)[:300]}"
                )
            pos = (rec.get("player") or {}).get("position")
            if pos in counts and self._is_meaningfully_projected(rec["stats"]):
                counts[pos][2] += 1
                if self._VOLUME_BY_POSITION[pos] in rec["stats"]:
                    counts[pos][0] += 1
                if self._FD_BY_POSITION[pos] in rec["stats"]:
                    counts[pos][1] += 1
        totals = {pos: c[2] for pos, c in counts.items()}
        for pos, (with_volume, with_fd, total) in counts.items():
            if total and with_volume / total < 0.5:
                raise IngestError(
                    f"sleeper: {self._VOLUME_BY_POSITION[pos]} present in only "
                    f"{with_volume}/{total} {pos} records — partial volume drift breaks "
                    f"FD imputation and scoring (design 4.2/R5, post-R16 amendment). "
                    f"Current per-position meaningfully-projected totals: {totals}."
                )
            if total and with_fd / total < 0.5:
                log.warning(
                    "sleeper.fd_coverage", position=pos, with_fd=with_fd, total=total
                )
        # See MIN_PROJECTED docstring above the class attribute: this catches
        # exactly the case the ratio guard above can't — a collapsed
        # meaningfully-projected population where the ratio still reads 100%
        # (or total==0, which the ratio guard skips outright).
        for pos, (with_volume, with_fd, total) in counts.items():
            floor = self.MIN_PROJECTED.get(pos, 0)
            if total < floor:
                raise IngestError(
                    f"sleeper: {pos} meaningfully-projected population collapsed — "
                    f"{total} records (floor {floor}) — upstream drift (R5). "
                    f"Current per-position meaningfully-projected totals: {totals}."
                )
        return len(payload)

    def store(self, conn, run_id: int, payload) -> None:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.sleeper_projections (run_id, season, week, payload) VALUES (%s,%s,%s,%s)",
                (run_id, self.season, self.week, json.dumps(payload)),
            )
