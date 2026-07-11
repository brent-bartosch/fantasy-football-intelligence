import json
import os
import pathlib
import time

import requests
from dotenv import load_dotenv

load_dotenv()

OAUTH_FILE = pathlib.Path("config/yahoo_oauth.json")
LEGACY_TOKEN = pathlib.Path("config/yahoo_token.json")


class YahooAuthError(Exception):
    pass


def _build_oauth_file() -> None:
    consumer_key = os.getenv("YAHOO_CLIENT_ID")
    consumer_secret = os.getenv("YAHOO_CLIENT_SECRET")
    if not consumer_key or not consumer_secret:
        raise YahooAuthError("YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET missing from .env")
    if not LEGACY_TOKEN.exists():
        raise YahooAuthError(
            f"{LEGACY_TOKEN} not found. Run: python scripts/yahoo_manual_auth.py, then retry."
        )
    legacy = json.loads(LEGACY_TOKEN.read_text())
    if "refresh_token" not in legacy:
        raise YahooAuthError(
            f"{LEGACY_TOKEN} has keys {sorted(legacy.keys())} — expected 'refresh_token'. "
            "Re-authorize with scripts/yahoo_manual_auth.py."
        )
    payload = json.dumps(
        {
            "consumer_key": consumer_key,
            "consumer_secret": consumer_secret,
            "access_token": legacy.get("access_token", ""),
            "refresh_token": legacy["refresh_token"],
            "token_type": legacy.get("token_type", "bearer"),
            "token_time": 0.0,  # force immediate refresh on first use
        }
    )
    # Create with 0600 from the start (no world-readable window), then
    # atomically move into place.
    tmp = OAUTH_FILE.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(payload)
    os.replace(tmp, OAUTH_FILE)


def get_session():
    from yahoo_oauth import OAuth2

    if not OAUTH_FILE.exists():
        _build_oauth_file()
    try:
        sc = OAuth2(None, None, from_file=str(OAUTH_FILE))
    except json.JSONDecodeError as exc:
        raise YahooAuthError(
            f"{OAUTH_FILE} is corrupted (invalid JSON: {exc}). Delete it and "
            f"re-run — it is rebuilt from .env + {LEGACY_TOKEN}."
        ) from exc
    if not sc.token_is_valid():
        try:
            sc.refresh_access_token()
        except requests.RequestException as exc:
            raise YahooAuthError(
                f"Network failure refreshing the Yahoo token: {exc}. "
                "Check connectivity and retry; the token file was not changed."
            ) from exc
    if not sc.token_is_valid():
        raise YahooAuthError(
            "Yahoo token refresh failed — refresh token likely revoked. "
            "Run: python scripts/yahoo_manual_auth.py, delete config/yahoo_oauth.json, retry."
        )
    return sc


def ensure_fresh_token(sc, margin_s: int = 900) -> bool:
    """Proactively refresh `sc`'s access token if it has less than margin_s
    seconds of life left, instead of waiting for get_session's reactive
    token_is_valid() check (ADR Domain 4). Call every assistant loop tick.
    Returns True if a refresh was performed.

    Any refresh failure -- the call raising, or the token still invalid
    afterward -- raises YahooAuthError. Draft-day token death must be loud
    so the operator flips to MANUAL and keeps drafting from the board."""
    remaining = sc.token_time + 3600 - time.time()
    if remaining >= margin_s:
        return False
    try:
        sc.refresh_access_token()
    except Exception as exc:
        raise YahooAuthError(
            f"Proactive Yahoo token refresh failed: {exc}. Flip to MANUAL "
            "and keep drafting from the board."
        ) from exc
    if not sc.token_is_valid():
        raise YahooAuthError(
            "Yahoo token refresh completed but the token is still invalid "
            "-- refresh token likely revoked. Flip to MANUAL and keep "
            "drafting from the board."
        )
    return True


def get_league(session, league_key: str):
    import yahoo_fantasy_api as yfa

    return yfa.league.League(session, league_key)


YAHOO_MIN_INTERVAL_S = 2.0
_last_call_monotonic = 0.0


class YahooRateLimitError(Exception):
    """Yahoo error 999 = IP lockout for ~10-15 minutes. Never retry; stop ALL
    Yahoo API work and re-run after cooldown (risk R15/R2, ADR Domain 1)."""


def yahoo_call(fn, *args, **kwargs):
    """Every Yahoo API call goes through here: enforces >=2s spacing between
    calls (R15) and converts error-999 responses into YahooRateLimitError.
    No retries by design (ADR Domain 1: a retry against 999 only extends the
    lockout)."""
    global _last_call_monotonic
    wait = YAHOO_MIN_INTERVAL_S - (time.monotonic() - _last_call_monotonic)
    if wait > 0:
        time.sleep(wait)
    try:
        return fn(*args, **kwargs)
    except requests.exceptions.RequestException as exc:
        raise YahooAuthError(f"network failure calling yahoo: {exc}") from exc
    except Exception as exc:
        # yahoo_fantasy_api surfaces 999 as RuntimeError text containing the
        # status code. A substring false-positive would only rename one loud
        # error to a scarier loud error — acceptable.
        if "999" in str(exc):
            raise YahooRateLimitError(
                "Yahoo returned error 999 (rate-limit lockout, ~10-15 min). "
                f"Stop all Yahoo API work now; re-run after cooldown. Original: {exc}"
            ) from exc
        raise
    finally:
        _last_call_monotonic = time.monotonic()
