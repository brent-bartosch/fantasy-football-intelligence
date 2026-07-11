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


class _FakeSC:
    def __init__(self, token_time, valid_after_refresh=True, refresh_raises=None):
        self.token_time = token_time
        self._valid_after_refresh = valid_after_refresh
        self._refresh_raises = refresh_raises
        self.refresh_calls = 0

    def token_is_valid(self):
        return self._valid_after_refresh

    def refresh_access_token(self):
        self.refresh_calls += 1
        if self._refresh_raises is not None:
            raise self._refresh_raises


def test_ensure_fresh_token_refreshes_when_near_expiry(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(yc.time, "time", lambda: clock["t"])
    # token_time + 3600 - now = 1000 + 3600 - 4200 = 400s left, < margin 900
    clock["t"] = 4200.0
    sc = _FakeSC(token_time=1000.0)

    refreshed = yc.ensure_fresh_token(sc, margin_s=900)

    assert refreshed is True
    assert sc.refresh_calls == 1


def test_ensure_fresh_token_skips_when_fresh(monkeypatch):
    clock = {"t": 1000.0}
    monkeypatch.setattr(yc.time, "time", lambda: clock["t"])
    # token_time + 3600 - now = 1000 + 3600 - 1000 = 3600s left, >= margin 900
    sc = _FakeSC(token_time=1000.0)

    refreshed = yc.ensure_fresh_token(sc, margin_s=900)

    assert refreshed is False
    assert sc.refresh_calls == 0


def test_ensure_fresh_token_raises_on_refresh_failure(monkeypatch):
    """KeyError is the real failure mode for a revoked refresh token:
    yahoo_oauth's oauth2_access_parser does json.loads(body)['access_token'],
    and Yahoo's error body for a revoked token lacks that key."""
    clock = {"t": 4200.0}
    monkeypatch.setattr(yc.time, "time", lambda: clock["t"])
    sc = _FakeSC(token_time=1000.0, refresh_raises=KeyError("access_token"))

    with pytest.raises(yc.YahooAuthError):
        yc.ensure_fresh_token(sc, margin_s=900)

    assert sc.refresh_calls == 1


def test_ensure_fresh_token_propagates_unrelated_bugs_raw(monkeypatch):
    """An AttributeError from bad wiring (not a real refresh failure) must
    NOT be laundered into YahooAuthError -- that would read to the
    ModeMachine as "Yahoo is down" instead of crashing loud as a bug."""
    clock = {"t": 4200.0}
    monkeypatch.setattr(yc.time, "time", lambda: clock["t"])
    sc = _FakeSC(token_time=1000.0, refresh_raises=AttributeError("boom"))

    with pytest.raises(AttributeError):
        yc.ensure_fresh_token(sc, margin_s=900)


def test_ensure_fresh_token_raises_if_still_invalid_after_refresh(monkeypatch):
    clock = {"t": 4200.0}
    monkeypatch.setattr(yc.time, "time", lambda: clock["t"])
    sc = _FakeSC(token_time=1000.0, valid_after_refresh=False)

    with pytest.raises(yc.YahooAuthError):
        yc.ensure_fresh_token(sc, margin_s=900)
