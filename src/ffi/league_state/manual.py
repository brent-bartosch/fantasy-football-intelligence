"""Manual capture / screenshot-text parsing — the PRIMARY league-state backend.

Yahoo's API is dead (403), so the operator pastes transaction/roster text into
files under `data/captures/` and this module turns it into canonical
`Transaction` rows. It is the ONLY reader of `data/captures/` (ARCHITECTURE
§3a: `ts_precision` is attached at exactly one place, so "success at the wrong
precision" is detectable).

File format (text, `#`-comments ignored):

    league_id: 326814        # required header (key: value)
    season: 2026             # required header
    week: 1                  # optional header (also derivable from a 'ts')
    ---                      # optional separator
    add|2026-09-09T08:15:00-07:00|3|40039|Some Player
    drop|2026-09-09|7|40040
    add_drop|2026-09-09T08:15:00-07:00|2|40123|40045

`kind` is one of add/drop/add_drop/trade/commish. A tz-aware ISO datetime
carries `ts_precision='exact'`; a bare date carries `'date'` (ts is that day's
midnight in the league timezone). A naive datetime is rejected — the operator
must include the offset (ADR §7: tz-aware throughout).
"""
from __future__ import annotations

import datetime
import hashlib
import pathlib
from zoneinfo import ZoneInfo

from ffi.league_state import KINDS, Transaction

CAPTURES_DIR = pathlib.Path("data/captures")
LEAGUE_TZ = ZoneInfo("America/Los_Angeles")


class CaptureParseError(ValueError):
    """A capture file did not match the expected shape. Never guess."""


def _parse_ts(token: str, line_no: int) -> tuple[datetime.datetime, str]:
    token = token.strip()
    # A date-only token has no time separator; a datetime has 'T' or a space.
    if "T" in token or " " in token:
        try:
            parsed = datetime.datetime.fromisoformat(token)
        except ValueError as exc:
            raise CaptureParseError(
                f"line {line_no}: bad timestamp {token!r} — expected tz-aware ISO "
                f"datetime or YYYY-MM-DD"
            ) from exc
        if parsed.tzinfo is None:
            raise CaptureParseError(
                f"line {line_no}: timestamp {token!r} is tz-naive — include the "
                f"UTC offset (ADR §7: tz-aware throughout)"
            )
        return parsed, "exact"
    try:
        d = datetime.date.fromisoformat(token)
    except ValueError as exc:
        raise CaptureParseError(
            f"line {line_no}: bad timestamp {token!r} — expected tz-aware ISO "
            f"datetime or YYYY-MM-DD"
        ) from exc
    return datetime.datetime(d.year, d.month, d.day, tzinfo=LEAGUE_TZ), "date"


def _stable_id(line: str) -> str:
    return hashlib.sha256(line.strip().encode()).hexdigest()[:24]


def _parse_meta(text: str) -> dict:
    meta: dict = {}
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            break
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    return meta


def parse_text(text: str) -> list[Transaction]:
    """Parse capture text into canonical transactions. Metadata must include
    `league_id` and `season`; `week` is optional (nullable on the row)."""
    meta = _parse_meta(text)
    if "league_id" not in meta or "season" not in meta:
        raise CaptureParseError(
            "capture must declare `league_id` and `season` headers before any "
            f"transaction line; found {sorted(meta)}"
        )
    league_id = int(meta["league_id"])
    season = int(meta["season"])
    week = int(meta["week"]) if "week" in meta else None

    out: list[Transaction] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line == "---":
            continue
        if "|" not in line:
            if ":" in line:
                continue  # header metadata line (key: value)
            raise CaptureParseError(f"line {i}: unrecognized line {line!r}")
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            raise CaptureParseError(
                f"line {i}: expected `kind|ts|team|player[|player2]`, got {parts!r}"
            )
        kind, ts_token, team_token, player_token = parts[:4]
        if kind not in KINDS:
            raise CaptureParseError(
                f"line {i}: kind {kind!r} not in {sorted(KINDS)}"
            )
        ts, precision = _parse_ts(ts_token, i)
        team_id = int(team_token)
        payload = {"player_id": player_token}
        if len(parts) >= 5:
            payload["player2_id"] = parts[4]
        out.append(
            Transaction(
                league_id=league_id,
                season=season,
                week=week,
                kind=kind,
                source_transaction_id=_stable_id(line),
                ts=ts,
                team_id=team_id,
                payload=payload,
                source="manual",
                ts_precision=precision,
            )
        )
    return out


def parse_capture(path: str | pathlib.Path) -> list[Transaction]:
    """Parse one capture file. The only sanctioned reader of `data/captures/`."""
    p = pathlib.Path(path)
    return parse_text(p.read_text())
