import datetime
import hashlib
from typing import Any, NamedTuple

import structlog

from ffi.ingest.gates import SanityGateError

log = structlog.get_logger()


class IngestError(Exception):
    """Raised when a source's payload fails validation. Never swallowed."""


def schema_hash(record: dict) -> str:
    return hashlib.sha256("|".join(sorted(record.keys())).encode()).hexdigest()


class Baseline(NamedTuple):
    """The prior snapshot a gate compares today's payload against.

    Carries its provenance, not just its rows: a gate message that says only
    "drift vs prior snapshot" leaves the operator guessing WHICH snapshot the
    feed picked — and after the success-preferring change below, the baseline
    is no longer simply "yesterday". `at` is a date (archive day for trending,
    fetched day for projections) so staleness is comparable across feeds.
    """

    rows: Any
    run_id: int
    at: datetime.date


# A baseline older than a week means the last several days all warned or
# failed. The comparison is still worth making — a stale baseline is a real
# baseline — but it silently weakens every gate downstream of it (field-set
# drift that arrived Tuesday reads as today's drift), so it is named in the
# message rather than left implicit.
BASELINE_STALE_DAYS = 7


def baseline_label(source: str, baseline: Baseline, today: datetime.date) -> str:
    """`feed=` label naming the baseline, plus a staleness note when due.

    Threaded through the gates' existing `feed` parameter rather than as a new
    gate argument: gates.py must stay ignorant of which feed calls it (
    ARCHITECTURE §3), and every gate message already starts with `feed`.
    The staleness note NEVER fails a run on its own — it only annotates the
    message of a gate that fired for its own reasons.
    """
    label = f"{source} [baseline run {baseline.run_id} @ {baseline.at.isoformat()}]"
    age_days = (today - baseline.at).days
    if age_days > BASELINE_STALE_DAYS:
        label += (
            f" [STALE BASELINE: {age_days}d old (> {BASELINE_STALE_DAYS}d) — the most "
            f"recent successful snapshot is that far back, so this comparison spans "
            f"more than one day of drift]"
        )
    return label


SANITY_MODES = ("off", "warn", "fail")


class BaseIngester:
    source: str = None  # subclasses must set

    # 'off'  no gate is run
    # 'warn' observe-and-log: the payload IS stored, the run is recorded
    #        'sanity_warned', and health.state() never reports it OK
    # 'fail' hard-fail: nothing is stored, the run is recorded 'sanity_failed'
    # Anything outside SANITY_MODES is rejected by run() before the run row is
    # created — an unknown mode is a misconfiguration with no safe default.
    # Per-feed numeric thresholds are unset until four weeks of P2 archive
    # exist (ADR TBD 3), so both gated feeds currently run 'warn'; projections
    # flips to 'fail' after the 2026-09-08 soak (see ingest/sleeper.py).
    sanity_mode: str = "off"

    def fetch(self):
        raise NotImplementedError

    def validate(self, payload) -> int:
        raise NotImplementedError

    def store(self, conn, run_id: int, payload) -> None:
        raise NotImplementedError

    def sanity_check(self, conn, payload) -> float | None:
        """Raise SanityGateError if this payload fails its semantic gates.
        Only called when `sanity_mode != 'off'`; a subclass that sets a mode
        without implementing this raises rather than silently passing.

        Returns the measured week-over-week rank correlation when one was
        computed, else None (gate had no baseline, or an earlier gate raised).
        run() persists it as raw.ingest_runs.sanity_rho — a passing run is a
        data point the soak needs just as much as a failing one."""
        raise NotImplementedError(
            f"{type(self).__name__}.sanity_mode={self.sanity_mode!r} but "
            f"sanity_check() is not implemented"
        )

    def _first_record(self, payload) -> dict | None:
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict):
            return payload
        return None

    def _finish(self, conn, run_id: int, status: str, **cols) -> None:
        sets = ", ".join(f"{k}=%s" for k in cols)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE raw.ingest_runs SET finished_at=now(), status=%s"
                + (f", {sets}" if cols else "")
                + " WHERE run_id=%s",
                (status, *cols.values(), run_id),
            )
        conn.commit()

    def run(self, conn) -> int:
        # Validated first, before the run row even exists: a typo'd mode is a
        # misconfiguration, not a data failure, and there is no safe default
        # to degrade to. Falling back to 'warn' would silently un-gate a feed
        # that was meant to hard-fail; falling back to 'off' would un-gate it
        # entirely. Neither is discoverable from a log line, so refuse to run.
        if self.sanity_mode not in SANITY_MODES:
            raise ValueError(
                f"{type(self).__name__}.sanity_mode={self.sanity_mode!r} is not "
                f"one of {SANITY_MODES} — refusing to run rather than guess "
                f"which gate mode was meant (ADR D1: no silent degradation)"
            )
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.ingest_runs (source) VALUES (%s) RETURNING run_id",
                (self.source,),
            )
            run_id = cur.fetchone()[0]
        conn.commit()
        try:
            payload = self.fetch()
            row_count = self.validate(payload)
            final_status = "success"
            gate_error = None
            # The soak's actual measurement (ADR TBD 3, migration 011). It is
            # captured on BOTH paths: a gate that passes at rho=0.91 is the
            # observation the 0.85 floor has to be fitted against, and a gate
            # that trips carries its rho on the exception.
            rho = None
            if self.sanity_mode != "off":
                try:
                    rho = self.sanity_check(conn, payload)
                except SanityGateError as gate_exc:
                    rho = gate_exc.rho
                    if self.sanity_mode == "fail":
                        raise
                    # observe-and-log: store anyway, but never render OK. The
                    # message is persisted as well as logged — a warned run
                    # that only says "sanity_warned" gives the operator
                    # nothing to act on the next morning.
                    gate_error = str(gate_exc)
                    log.warning(
                        "ingest.sanity_warned",
                        source=self.source,
                        run_id=run_id,
                        error=gate_error,
                        sanity_rho=rho,
                    )
                    final_status = "sanity_warned"
            self.store(conn, run_id, payload)
            first = self._first_record(payload)
            self._finish(
                conn,
                run_id,
                final_status,
                row_count=row_count,
                schema_hash=schema_hash(first) if first else None,
                error=gate_error,
                sanity_rho=rho,
            )
            log.info(
                "ingest.success",
                source=self.source,
                run_id=run_id,
                rows=row_count,
                status=final_status,
                sanity_rho=rho,
            )
            return run_id
        except SanityGateError as exc:
            conn.rollback()
            self._finish(
                conn, run_id, "sanity_failed", error=str(exc), sanity_rho=exc.rho
            )
            log.error(
                "ingest.sanity_failed",
                source=self.source,
                run_id=run_id,
                error=str(exc),
                sanity_rho=exc.rho,
            )
            raise  # fail loud — callers/cron must see nonzero exit
        except Exception as exc:
            conn.rollback()
            self._finish(conn, run_id, "failed", error=str(exc))
            log.error(
                "ingest.failed", source=self.source, run_id=run_id, error=str(exc)
            )
            raise  # fail loud — callers/cron must see nonzero exit
