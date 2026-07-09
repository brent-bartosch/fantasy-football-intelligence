import json
import os
import pathlib
import time
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
    OAUTH_FILE.write_text(
        json.dumps(
            {
                "consumer_key": consumer_key,
                "consumer_secret": consumer_secret,
                "access_token": legacy.get("access_token", ""),
                "refresh_token": legacy["refresh_token"],
                "token_type": legacy.get("token_type", "bearer"),
                "token_time": 0.0,  # force immediate refresh on first use
            }
        )
    )
    OAUTH_FILE.chmod(0o600)


def get_session():
    from yahoo_oauth import OAuth2

    if not OAUTH_FILE.exists():
        _build_oauth_file()
    sc = OAuth2(None, None, from_file=str(OAUTH_FILE))
    if not sc.token_is_valid():
        sc.refresh_access_token()
    if not sc.token_is_valid():
        raise YahooAuthError(
            "Yahoo token refresh failed — refresh token likely revoked. "
            "Run: python scripts/yahoo_manual_auth.py, delete config/yahoo_oauth.json, retry."
        )
    return sc


def get_league(session, league_key: str):
    import yahoo_fantasy_api as yfa

    return yfa.league.League(session, league_key)
