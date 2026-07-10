import pytest

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


def test_latest_fp_payload_reads_cache(db):
    _seed_snapshot(
        db, "consensus-rankings", {"position": "OP", "week": 0}, {"players": [1]}
    )
    got = latest_fp_payload(db, "consensus-rankings", {"position": "OP"})
    assert got == {"players": [1]}
    assert latest_fp_payload(db, "consensus-rankings", {"position": "XX"}) is None
