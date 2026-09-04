"""Yahoo backfill backend (ARCHITECTURE §1b: optimization path, never on a
critical path).

Yahoo's API is DEAD (403) as of 2026-09-03; every in-season module's
acceptance criterion must be met with zero Yahoo access. This module exists so
that, if access is restored, the operator can backfill the canonical tables
with Yahoo's higher-precision (`ts_precision='exact'`) data — it is NEVER the
primary source, and a 403 is never retried (ADR Domain 1: an authorization
state, not a transient error).

HTTP goes through `ffi.yahoo_client` only (ARCHITECTURE §3a); team-key parsing
through `ffi.ids`.
"""
from __future__ import annotations

import datetime

from ffi import ids
from ffi.league_state import RosterRow, Transaction
from ffi.yahoo_client import get_league, yahoo_call

# yahoo_fantasy_api's `type` values -> canonical kinds.
_KIND_MAP = {
    "add": "add",
    "drop": "drop",
    "add/drop": "add_drop",
    "trade": "trade",
    "commish": "commish",
}


def _league_id(league_key: str) -> int:
    return int(league_key.rsplit(".l.", 1)[1])


def _team_slot(team_key) -> int | None:
    if not team_key:
        return None
    return ids.team_slot(str(team_key))


def _to_transaction(rec: dict, league_id: int, season: int, week: int | None) -> Transaction:
    raw_kind = rec.get("type")
    kind = _KIND_MAP.get(raw_kind)
    if kind is None:
        raise ValueError(
            f"yahoo: unknown transaction type {raw_kind!r} (expected one of "
            f"{sorted(_KIND_MAP)}) — schema drift, not a parse bug"
        )
    ts = datetime.datetime.fromtimestamp(
        int(rec["timestamp"]), tz=datetime.timezone.utc
    )
    team_key = rec.get("trader_team_key") or rec.get("team_key")
    return Transaction(
        league_id=league_id,
        season=season,
        week=week,
        kind=kind,
        source_transaction_id=str(rec["transaction_key"]),
        ts=ts,
        team_id=_team_slot(team_key),
        payload={"players": rec.get("players", {}), "status": rec.get("status")},
        source="yahoo",
        ts_precision="exact",
    )


def fetch_transactions(
    session, league_key: str, season: int, week: int | None = None
) -> list[Transaction]:
    """Backfill the canonical transactions for a season (optionally one week)."""
    league_id = _league_id(league_key)
    lg = get_league(session, league_key)
    raw = yahoo_call(lg.transactions, "add,drop,commish,trade", "")
    return [_to_transaction(r, league_id, season, week) for r in raw]


def _slot_type(selected_position: str) -> str:
    if selected_position == "BN":
        return "bench"
    if selected_position == "IR":
        return "ir"
    return "starter"


def fetch_rosters(
    session, league_key: str, season: int, as_of: datetime.date
) -> list[RosterRow]:
    """Backfill canonical roster rows as-of a date from Yahoo."""
    league_id = _league_id(league_key)
    lg = get_league(session, league_key)
    teams = yahoo_call(lg.teams)
    out: list[RosterRow] = []
    for team in teams:
        team_key = str(team["team_key"])
        slot = ids.team_slot(team_key)
        players = yahoo_call(lg.to_team, team_key).roster(day=as_of)
        for p in players:
            sel = str(p.get("selected_position") or "")
            slot_type = _slot_type(sel)
            position = (
                sel if slot_type == "starter" else (p.get("eligible_positions") or [None])[0]
            )
            out.append(
                RosterRow(
                    league_id=league_id,
                    season=season,
                    as_of=as_of,
                    team_id=slot,
                    player_id=str(p["player_id"]),
                    position=position,
                    slot_type=slot_type,
                    source="yahoo",
                    ts_precision="exact",
                )
            )
    return out
