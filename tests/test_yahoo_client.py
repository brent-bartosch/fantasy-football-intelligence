import json

import pytest
import requests

import ffi.yahoo_client as yc


def test_yahoo_call_enforces_spacing(monkeypatch):
    sleeps = []
    clock = {"t": 100.0}
    monkeypatch.setattr(yc.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(yc.time, "sleep", lambda s: sleeps.append(s))
    yc._last_call_monotonic = 0.0

    yc.yahoo_call(lambda: "a")  # first call: 100 - 0 > 2s, no sleep
    assert sleeps == []
    clock["t"] = 100.5  # 0.5s after last call
    yc.yahoo_call(lambda: "b")
    assert len(sleeps) == 1 and sleeps[0] == pytest.approx(1.5)


def test_yahoo_call_converts_999_to_rate_limit_error():
    def boom():
        raise RuntimeError("HTTP 999 Request denied")

    with pytest.raises(yc.YahooRateLimitError) as ei:
        yc.yahoo_call(boom)
    assert "lockout" in str(ei.value)


def test_yahoo_call_passes_through_other_errors():
    def boom():
        raise ValueError("something else")

    with pytest.raises(ValueError):
        yc.yahoo_call(boom)


def test_yahoo_call_returns_result():
    assert yc.yahoo_call(lambda x: x * 2, 21) == 42


def test_yahoo_call_converts_request_exception_to_yahoo_auth_error():
    """Phase 2 Task 1 Minor: network-level RequestException must surface as
    the domain error, not leak requests internals."""

    def boom():
        raise requests.exceptions.ConnectionError("dns down")

    with pytest.raises(yc.YahooAuthError):
        yc.yahoo_call(boom)


def test_get_session_corrupted_oauth_file(monkeypatch, tmp_path):
    bad = tmp_path / "yahoo_oauth.json"
    bad.write_text("{not json")
    monkeypatch.setattr(yc, "OAUTH_FILE", bad)
    with pytest.raises(yc.YahooAuthError) as ei:
        yc.get_session()
    assert "corrupted" in str(ei.value)
