import pytest
from ffi.ingest.base import BaseIngester, IngestError, schema_hash
from ffi.ingest.gates import SanityGateError


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


class _FailGateIngester(BaseIngester):
    source = "test_gate_feed"
    sanity_mode = "fail"

    def fetch(self):
        return [{"a": 1}]

    def validate(self, payload):
        return 1

    def sanity_check(self, conn, payload):
        raise SanityGateError("test_gate_feed: deliberately failing gate")

    def store(self, conn, run_id, payload):
        raise AssertionError("store() must not be reached in hard-fail mode")


def test_hard_fail_mode_records_sanity_failed_and_never_stores(db):
    with pytest.raises(SanityGateError, match="deliberately failing gate"):
        _FailGateIngester().run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, error FROM raw.ingest_runs WHERE source='test_gate_feed'"
        )
        status, error = cur.fetchone()
    assert status == "sanity_failed"
    assert "deliberately failing gate" in error


class _RhoFailGateIngester(_FailGateIngester):
    source = "test_gate_rho"

    def sanity_check(self, conn, payload):
        raise SanityGateError("test_gate_rho: gate tripped at rho", rho=0.42)


def test_hard_fail_mode_still_persists_the_measured_rho(db):
    """Even the run that refused to store keeps its measurement.

    The rho a hard-fail rejected is the single most useful point in the
    distribution the floor gets fitted from — recording only the verdict
    would throw it away exactly when it matters (migration 011).
    """
    with pytest.raises(SanityGateError):
        _RhoFailGateIngester().run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT status, sanity_rho FROM raw.ingest_runs WHERE source='test_gate_rho'"
        )
        assert cur.fetchone() == ("sanity_failed", pytest.approx(0.42))


class _WarnRhoIngester(GoodIngester):
    source = "test_gate_warn_rho"
    sanity_mode = "warn"

    def sanity_check(self, conn, payload):
        return 0.97  # a passing gate returns its measurement


def test_passing_gate_rho_is_persisted_and_a_gateless_run_stays_null(db):
    warn_run = _WarnRhoIngester().run(db)
    off_run = GoodIngester().run(db)  # sanity_mode 'off': nothing measured
    with db.cursor() as cur:
        cur.execute(
            "SELECT run_id, status, sanity_rho FROM raw.ingest_runs "
            "WHERE run_id = ANY(%s) ORDER BY run_id",
            ([warn_run, off_run],),
        )
        rows = cur.fetchall()
    assert rows[0] == (warn_run, "success", pytest.approx(0.97))
    assert rows[1] == (off_run, "success", None)


class _TypoModeIngester(GoodIngester):
    source = "test_typo_mode"
    sanity_mode = "wan"  # typo for 'warn'

    def store(self, conn, run_id, payload):
        raise AssertionError("store() must not be reached with an unknown mode")


def test_unknown_sanity_mode_raises_before_anything_runs(db):
    """A typo'd mode must not degrade to 'warn' or 'off'.

    `!= 'off'` is the only test the run loop makes, so 'wan' would have
    silently run the gate in warn mode — un-gating a feed configured to
    hard-fail, invisibly. Refuse instead, and refuse before the run row
    exists: this is a misconfiguration, not a data failure.
    """
    with pytest.raises(ValueError, match="sanity_mode='wan'"):
        _TypoModeIngester().run(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM raw.ingest_runs WHERE source='test_typo_mode'"
        )
        assert cur.fetchone()[0] == 0
