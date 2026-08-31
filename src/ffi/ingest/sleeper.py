import datetime
import json

import requests
import structlog

from ffi.ingest.base import Baseline, BaseIngester, IngestError, baseline_label
from ffi.ingest.gates import (
    check_fieldset,
    check_nonzero_coverage,
    check_rank_correlation,
    union_keys,
)

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

    # End-state is hard-fail: unlike the trending archive, a bad projections
    # snapshot is fully recoverable (tomorrow's pull replaces it) while a bad
    # one poisons valuation, the board and every downstream recommendation, so
    # refusing to store is strictly cheaper than storing wrong (ADR D1).
    # SOAK: warn until 2026-09-08 — fit correlation floor from a week of
    # observed rho in ingest_runs.sanity_rho (migration 011; written on
    # passing runs too, which is the half of the distribution a floor is
    # actually fitted to), then flip to "fail" (ADR Domain 1 end-state). The
    # 0.85 floor and the new union field-set check have never seen two real
    # consecutive days; hard-failing on an unfitted threshold would block the
    # morning chain on a guess. Flip = this one literal (or the sanity_mode=
    # constructor override, exposed by scripts/ingest_sleeper.py
    # --sanity-mode for a one-off run).
    sanity_mode = "warn"

    # `pts_ppr` and not `adp_2qb`: adp_2qb is the field with KNOWN cohort
    # instability (data/adp-pin.json exists precisely because it flaps), so
    # correlating on it would fire on days the projections themselves are
    # fine. pts_ppr is the projection the gate actually cares about.
    # Floor basis, live-probed 2026-08-31 on the season-level 2026 payload:
    # 3303 records, 628 with a positive pts_ppr. 400 is ~2/3 of observed, so
    # normal roster churn cannot trip it but a population collapse does.
    RANK_KEY = "pts_ppr"
    MIN_RANKED = 400

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

    def __init__(
        self, season: int, week: int | None, *, sanity_mode: str | None = None
    ):
        self.season = season
        self.week = week
        # Explicit per-run override of the class default, so flipping the soak
        # (or forcing hard-fail for one manual run) needs no code edit. `None`
        # leaves the class attribute alone; anything else is validated by
        # BaseIngester.run(), which rejects an unknown mode outright.
        if sanity_mode is not None:
            self.sanity_mode = sanity_mode

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

    def _prior_payload(self, conn) -> Baseline | None:
        """The most recent earlier SUCCESSFUL snapshot for this (season, week).

        `week IS NOT DISTINCT FROM %s`, not a hard-coded `week IS NULL`: the
        season-level payload (week NULL) and a week-N payload are different
        populations — season totals versus one week's projection — so
        correlating a week-5 pull against the season snapshot compares
        magnitudes that share no scale and would trip the rank gate on every
        weekly run while never actually gating week-5 drift. `IS NOT DISTINCT
        FROM` rather than `=` because `week = NULL` matches nothing, which is
        how a season-level run would silently lose its own baseline.

        The join to raw.ingest_runs is what keeps the gate honest in warn
        mode: a warned run STILL STORES its payload, so yesterday's suspect
        snapshot would otherwise become today's baseline and the gate would
        quietly re-normalize onto the drift it just flagged — one bad day
        would be enough to blind the check permanently. Only 'success' runs
        are eligible; the join also subsumes the old `run_id IS NOT NULL`.
        """
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.payload, p.run_id, p.fetched_at "
                "FROM raw.sleeper_projections p "
                "JOIN raw.ingest_runs r ON r.run_id = p.run_id "
                "WHERE p.week IS NOT DISTINCT FROM %s "
                "AND p.season IS NOT DISTINCT FROM %s "
                "AND r.status = 'success' "
                "ORDER BY p.snapshot_id DESC LIMIT 1",
                (self.week, self.season),
            )
            row = cur.fetchone()
        if row is None:
            return None
        payload, run_id, fetched_at = row
        return Baseline(rows=payload, run_id=run_id, at=fetched_at.date())

    @staticmethod
    def _stats(payload) -> list[dict]:
        return [rec.get("stats", {}) for rec in payload]

    def _ranked(self, payload) -> dict:
        # Values are passed through uncoerced on purpose: check_rank_correlation
        # does the float() behind its own guard, so a non-numeric pts_ppr
        # surfaces as a SanityGateError naming the key instead of a bare
        # ValueError raised out of this comprehension.
        return {
            rec["player_id"]: rec["stats"][self.RANK_KEY]
            for rec in payload
            if self.RANK_KEY in rec.get("stats", {})
        }

    def sanity_check(self, conn, payload) -> float | None:
        check_nonzero_coverage(
            self._stats(payload),
            feed=self.source,
            value_key=self.RANK_KEY,
            min_players=self.MIN_RANKED,
        )
        baseline = self._prior_payload(conn)
        if baseline is None:
            return None  # first snapshot of this scope: nothing to compare to
        # Every gate message below names the baseline run/date (and flags it
        # when stale) — with success-preferring selection the comparison is no
        # longer necessarily against yesterday, so "drift vs prior snapshot"
        # on its own would be unactionable.
        feed = baseline_label(self.source, baseline, datetime.date.today())
        # Union of stats keys across every record, not record[0]'s: the
        # adp_2qb disappearance is precisely a key that goes missing from part
        # of the payload, and Sleeper's records are ragged (deep-bench entries
        # carry ADP metadata only), so record[0] is not representative of
        # anything. The union is the only stable field-set this feed has.
        check_fieldset(
            union_keys(self._stats(baseline.rows)),
            union_keys(self._stats(payload)),
            feed=feed,
        )
        return check_rank_correlation(
            self._ranked(baseline.rows), self._ranked(payload), feed=feed
        )
