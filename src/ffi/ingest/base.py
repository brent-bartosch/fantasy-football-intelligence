import hashlib

import structlog

from ffi.ingest.gates import SanityGateError

log = structlog.get_logger()


class IngestError(Exception):
    """Raised when a source's payload fails validation. Never swallowed."""


def schema_hash(record: dict) -> str:
    return hashlib.sha256("|".join(sorted(record.keys())).encode()).hexdigest()


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

    def sanity_check(self, conn, payload) -> None:
        """Raise SanityGateError if this payload fails its semantic gates.
        Only called when `sanity_mode != 'off'`; a subclass that sets a mode
        without implementing this raises rather than silently passing."""
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
            if self.sanity_mode != "off":
                try:
                    self.sanity_check(conn, payload)
                except SanityGateError as gate_exc:
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
            )
            log.info(
                "ingest.success",
                source=self.source,
                run_id=run_id,
                rows=row_count,
                status=final_status,
            )
            return run_id
        except SanityGateError as exc:
            conn.rollback()
            self._finish(conn, run_id, "sanity_failed", error=str(exc))
            log.error(
                "ingest.sanity_failed",
                source=self.source,
                run_id=run_id,
                error=str(exc),
            )
            raise  # fail loud — callers/cron must see nonzero exit
        except Exception as exc:
            conn.rollback()
            self._finish(conn, run_id, "failed", error=str(exc))
            log.error(
                "ingest.failed", source=self.source, run_id=run_id, error=str(exc)
            )
            raise  # fail loud — callers/cron must see nonzero exit
