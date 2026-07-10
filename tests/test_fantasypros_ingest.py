import pytest
import requests

from ffi.ingest.fantasypros import (
    FpBudgetExceededError,
    FpClient,
    fp_calls_today,
    latest_fp_payload,
)


def _seed_snapshot(db, endpoint, params, payload):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.fp_snapshots (endpoint, params, payload) VALUES (%s,%s,%s)",
            (
                endpoint,
                __import__("json").dumps(params),
                __import__("json").dumps(payload),
            ),
        )
    db.commit()


def test_fp_calls_today_counts_snapshots(db):
    assert fp_calls_today(db) == 0
    _seed_snapshot(db, "consensus-rankings", {"position": "OP"}, {"players": []})
    assert fp_calls_today(db) == 1


def test_budget_guard_blocks_at_limit(db, monkeypatch):
    client = FpClient(db, api_key="test", daily_budget=2)
    monkeypatch.setattr(client, "_http_get", lambda url, params: {"players": []})
    client.get("consensus-rankings", {"position": "QB"})
    client.get("consensus-rankings", {"position": "RB"})
    with pytest.raises(FpBudgetExceededError):
        client.get("consensus-rankings", {"position": "WR"})
    assert fp_calls_today(db) == 2  # the blocked call wrote nothing


def test_http_error_propagates_no_partial_snapshot(db, monkeypatch):
    # Task 11 Step 0 (from Task 10 review): an HTTP failure must propagate
    # loudly and must NOT write a partial/blocked snapshot row.
    client = FpClient(db, api_key="test", daily_budget=30)

    def _raise(url, params):
        raise requests.HTTPError("boom")

    monkeypatch.setattr(client, "_http_get", _raise)
    before = fp_calls_today(db)
    with pytest.raises(requests.HTTPError):
        client.get("consensus-rankings", {"position": "QB"})
    assert fp_calls_today(db) == before  # no partial snapshot row written


def test_latest_fp_payload_reads_cache(db):
    _seed_snapshot(
        db, "consensus-rankings", {"position": "OP", "week": 0}, {"players": [1]}
    )
    got = latest_fp_payload(db, "consensus-rankings", {"position": "OP"})
    assert got == {"players": [1]}
    assert latest_fp_payload(db, "consensus-rankings", {"position": "XX"}) is None
