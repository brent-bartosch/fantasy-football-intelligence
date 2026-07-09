import pytest
from ffi.ingest.base import BaseIngester, IngestError, schema_hash


class GoodIngester(BaseIngester):
    source = "test_good"

    def fetch(self):
        return [{"a": 1, "b": 2}]

    def validate(self, payload):
        return len(payload)

    def store(self, conn, run_id, payload):
        pass


class BadIngester(GoodIngester):
    source = "test_bad"

    def validate(self, payload):
        raise IngestError("missing field 'b'")


def _run_row(db, run_id):
    with db.cursor() as cur:
        cur.execute(
            "SELECT source, status, row_count, error FROM raw.ingest_runs WHERE run_id=%s",
            (run_id,),
        )
        return cur.fetchone()


def test_successful_run_records_success(db):
    run_id = GoodIngester().run(db)
    source, status, row_count, error = _run_row(db, run_id)
    assert (source, status, row_count, error) == ("test_good", "success", 1, None)


def test_failed_validation_records_failure_and_reraises(db):
    with pytest.raises(IngestError, match="missing field 'b'"):
        BadIngester().run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE source='test_bad' ORDER BY run_id DESC LIMIT 1"
        )
        status, error = cur.fetchone()
    assert status == "failed"
    assert "missing field 'b'" in error


def test_schema_hash_depends_on_keys_not_values():
    assert schema_hash({"a": 1, "b": 2}) == schema_hash({"b": 99, "a": 0})
    assert schema_hash({"a": 1}) != schema_hash({"a": 1, "c": 3})
