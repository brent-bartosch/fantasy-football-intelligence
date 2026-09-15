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

Roster captures use the same header convention plus `as_of` and `team`, a
mandatory `---` separator, and then the RAW paste from the Yahoo roster
page (a slot line, the player name, and a `Team - Pos` line per player —
see `parse_roster_text`):

    league_id: 326814
    season: 2026
    as_of: 2026-09-14
    team: 12
    ---
    <raw Yahoo roster-page paste>

`kind` is one of add/drop/add_drop/trade/commish. A tz-aware ISO datetime
carries `ts_precision='exact'`; a bare date carries `'date'` (ts is that day's
midnight in the league timezone). A naive datetime is rejected — the operator
must include the offset (ADR §7: tz-aware throughout).
"""
from __future__ import annotations

import datetime
import hashlib
import pathlib
import re
from zoneinfo import ZoneInfo

from ffi.league_state import KINDS, RosterRow, Transaction

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


# --- Roster captures ---------------------------------------------------------

# Slot tokens exactly as they appear on a copied Yahoo roster page. A line
# matching one of these EXACTLY (the whole line, nothing else) starts a player;
# the `Mia - QB` lines never match because they contain ' - '.
ROSTER_SLOTS = frozenset(
    {"QB", "RB", "WR", "TE", "K", "DEF", "BN", "IR", "W/R/T", "W/R", "W/T",
     "Q/W/R/T", "Q/W/R"}
)

# `Mia - QB` / `Cin - K` / `Phi - DEF` — NFL team abbr, then the real position.
_TEAM_POS_RE = re.compile(r"^([A-Za-z]{2,3}) - ([A-Z/]+)$")

_ROSTER_HEADERS = ("league_id", "season", "as_of", "team")


def _roster_slot_type(slot: str) -> str:
    if slot == "BN":
        return "bench"
    if slot == "IR":
        return "ir"
    return "starter"


def parse_roster_text(text: str, resolve) -> list[RosterRow]:
    """Parse a roster capture: headers, `---`, then the raw Yahoo UI paste.

    `resolve(name, position, nfl_team) -> player_id | None` is injected by the
    caller (scripts/ingest_captures.py) so this module stays pure and the
    crosswalk/defense lookups live at the call site. A None resolution for
    ANY player fails the whole file — a roster with a name-shaped hole in it
    is worse than no roster (fail-loud, all-or-nothing).
    """
    if "---" not in text:
        raise CaptureParseError(
            "roster capture needs a `---` line between the headers "
            "(league_id, season, as_of, team) and the pasted roster"
        )
    meta_block, body = text.split("---", 1)
    meta = _parse_meta(meta_block)
    missing = [k for k in _ROSTER_HEADERS if k not in meta]
    if missing:
        raise CaptureParseError(
            f"roster capture headers missing {missing} — need all of "
            f"{list(_ROSTER_HEADERS)}"
        )
    league_id = int(meta["league_id"])
    season = int(meta["season"])
    as_of = datetime.date.fromisoformat(meta["as_of"])
    team_id = int(meta["team"])

    rows: list[RosterRow] = []
    unresolved: list[str] = []
    slot: str | None = None
    name: str | None = None
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        if s in ROSTER_SLOTS:
            slot, name = s, None
            continue
        if slot is None:
            continue  # section headers / column labels between players
        if name is None:
            name = s  # the line right after the slot token is the player name
            continue
        m = _TEAM_POS_RE.match(s)
        if m is None:
            continue  # the name+notes junk line, game result, stat columns
        position = m.group(2)
        player_id = resolve(name, position, m.group(1))
        if player_id is None:
            unresolved.append(f"{name} ({position}, {m.group(1)})")
        else:
            rows.append(
                RosterRow(
                    league_id=league_id,
                    season=season,
                    as_of=as_of,
                    team_id=team_id,
                    player_id=player_id,
                    slot_type=_roster_slot_type(slot),
                    position=position,
                    source="manual",
                    ts_precision="date",
                )
            )
        slot = name = None
    if unresolved:
        raise CaptureParseError(
            f"unresolved player(s): {', '.join(unresolved)} — add them to the "
            f"crosswalk (public.player_id_xwalk) or fix the name and re-run"
        )
    if not rows:
        raise CaptureParseError(
            "no players parsed — expected a Yahoo roster paste below the `---` "
            "(slot line, player name, `Team - Pos` line per player)"
        )
    return rows


def parse_roster_capture(path: str | pathlib.Path, resolve) -> list[RosterRow]:
    """Parse one roster capture file (see parse_roster_text for the format)."""
    return parse_roster_text(pathlib.Path(path).read_text(), resolve)
