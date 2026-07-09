import hashlib
import structlog

log = structlog.get_logger()


class IngestError(Exception):
    """Raised when a source's payload fails validation. Never swallowed."""


def schema_hash(record: dict) -> str:
    return hashlib.sha256("|".join(sorted(record.keys())).encode()).hexdigest()


class BaseIngester:
    source: str = None  # subclasses must set

    def fetch(self):
        raise NotImplementedError

    def validate(self, payload) -> int:
        raise NotImplementedError

    def store(self, conn, run_id: int, payload) -> None:
        raise NotImplementedError

    def _first_record(self, payload) -> dict | None:
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        if isinstance(payload, dict):
            return payload
        return None

    def run(self, conn) -> int:
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
            self.store(conn, run_id, payload)
            first = self._first_record(payload)
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE raw.ingest_runs SET finished_at=now(), status='success', "
                    "row_count=%s, schema_hash=%s WHERE run_id=%s",
                    (row_count, schema_hash(first) if first else None, run_id),
                )
            conn.commit()
            log.info(
                "ingest.success", source=self.source, run_id=run_id, rows=row_count
            )
            return run_id
        except Exception as exc:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE raw.ingest_runs SET finished_at=now(), status='failed', error=%s WHERE run_id=%s",
                    (str(exc), run_id),
                )
            conn.commit()
            log.error(
                "ingest.failed", source=self.source, run_id=run_id, error=str(exc)
            )
            raise  # fail loud — callers/cron must see nonzero exit
