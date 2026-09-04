"""Canonical read/write API for `league_transactions` / `league_rosters`.

One output schema, two backends (manual + Yahoo). This is the only module that
writes the canonical tables, and the only place `source` + `ts_precision` are
attached to a row on the way in — so a reader can trust that a row with
`ts_precision='unknown'` was never laundered into an exact deadline.

Postgres access goes through `ffi.db` only (ARCHITECTURE §3a).
"""
from __future__ import annotations

import datetime
import json
from collections.abc import Sequence

from ffi import db
from ffi.league_state import (
    RosterRow,
    SOURCES,
    TS_PRECISIONS,
    Transaction,
    _check,
)

_TRANSACTION_COLS = (
    "league_id",
    "season",
    "week",
    "kind",
    "ts",
    "ts_precision",
    "source",
    "source_transaction_id",
    "team_id",
    "payload",
)
_ROSTER_COLS = (
    "league_id",
    "season",
    "as_of",
    "team_id",
    "player_id",
    "position",
    "slot_type",
    "source",
    "ts_precision",
)


def _own(conn):
    """Return (conn, should_close). A caller-supplied connection is reused; a
    missing one is opened and closed by this module."""
    if conn is not None:
        return conn, False
    return db.connect(), True


def _record_transactions(rows: Sequence[Transaction], source, ts_precision, conn):
    for row in rows:
        if not isinstance(row, Transaction):
            raise TypeError("record: mixed row types in a transaction batch")
    conn, close = _own(conn)
    try:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    f"INSERT INTO public.league_transactions "
                    f"({', '.join(_TRANSACTION_COLS)}) VALUES "
                    f"({', '.join(['%s'] * len(_TRANSACTION_COLS))}) "
                    f"ON CONFLICT (source, source_transaction_id) DO NOTHING",
                    (
                        row.league_id,
                        row.season,
                        row.week,
                        row.kind,
                        row.ts,
                        ts_precision,
                        source,
                        row.source_transaction_id,
                        row.team_id,
                        json.dumps(row.payload or {}),
                    ),
                )
        conn.commit()
    finally:
        if close:
            conn.close()


def _record_rosters(rows: Sequence[RosterRow], source, ts_precision, conn):
    for row in rows:
        if not isinstance(row, RosterRow):
            raise TypeError("record: mixed row types in a roster batch")
    conn, close = _own(conn)
    try:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    f"INSERT INTO public.league_rosters "
                    f"({', '.join(_ROSTER_COLS)}) VALUES "
                    f"({', '.join(['%s'] * len(_ROSTER_COLS))}) "
                    f"ON CONFLICT (league_id, season, as_of, team_id, player_id, "
                    f"source) DO UPDATE SET position=EXCLUDED.position, "
                    f"slot_type=EXCLUDED.slot_type, "
                    f"ts_precision=EXCLUDED.ts_precision",
                    (
                        row.league_id,
                        row.season,
                        row.as_of,
                        row.team_id,
                        row.player_id,
                        row.position,
                        row.slot_type,
                        source,
                        ts_precision,
                    ),
                )
        conn.commit()
    finally:
        if close:
            conn.close()


def record(rows, source: str, ts_precision: str, conn=None) -> None:
    """Write a homogeneous batch of `Transaction` or `RosterRow` rows.

    `source` and `ts_precision` apply to every row in the batch — they are
    attached here, exactly once, so the read path never has to guess.
    """
    _check(source, SOURCES, "record.source")
    _check(ts_precision, TS_PRECISIONS, "record.ts_precision")
    rows = list(rows)
    if not rows:
        return
    if isinstance(rows[0], Transaction):
        _record_transactions(rows, source, ts_precision, conn)
    elif isinstance(rows[0], RosterRow):
        _record_rosters(rows, source, ts_precision, conn)
    else:
        raise TypeError(
            "record: rows must be a list of Transaction or RosterRow, got "
            f"{type(rows[0]).__name__}"
        )


def load_transactions(week: int, conn=None, league_id: int | None = None) -> list[Transaction]:
    """All canonical transactions for a season-week, oldest first."""
    conn, close = _own(conn)
    try:
        with conn.cursor() as cur:
            if league_id is None:
                cur.execute(
                    "SELECT league_id, season, week, kind, ts, ts_precision, source, "
                    "source_transaction_id, team_id, payload "
                    "FROM public.league_transactions WHERE week=%s "
                    "ORDER BY ts NULLS LAST, tx_seq",
                    (week,),
                )
            else:
                cur.execute(
                    "SELECT league_id, season, week, kind, ts, ts_precision, source, "
                    "source_transaction_id, team_id, payload "
                    "FROM public.league_transactions WHERE week=%s AND league_id=%s "
                    "ORDER BY ts NULLS LAST, tx_seq",
                    (week, league_id),
                )
            return [_tx(r) for r in cur.fetchall()]
    finally:
        if close:
            conn.close()


def load_rosters(as_of: datetime.date, conn=None, league_id: int | None = None) -> list[RosterRow]:
    """All canonical roster rows as-of a date, ordered by team then player."""
    conn, close = _own(conn)
    try:
        with conn.cursor() as cur:
            if league_id is None:
                cur.execute(
                    "SELECT league_id, season, as_of, team_id, player_id, position, "
                    "slot_type, source, ts_precision "
                    "FROM public.league_rosters WHERE as_of=%s "
                    "ORDER BY team_id, player_id",
                    (as_of,),
                )
            else:
                cur.execute(
                    "SELECT league_id, season, as_of, team_id, player_id, position, "
                    "slot_type, source, ts_precision "
                    "FROM public.league_rosters WHERE as_of=%s AND league_id=%s "
                    "ORDER BY team_id, player_id",
                    (as_of, league_id),
                )
            return [_roster(r) for r in cur.fetchall()]
    finally:
        if close:
            conn.close()


def _json_payload(value) -> dict:
    """psycopg2 returns jsonb as a dict on some drivers and a str on others;
    normalize both so the read path never hands a raw string back."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return json.loads(value)


def _tx(row) -> Transaction:
    return Transaction(
        league_id=row[0],
        season=row[1],
        week=row[2],
        kind=row[3],
        ts=row[4],
        ts_precision=row[5],
        source=row[6],
        source_transaction_id=row[7],
        team_id=row[8],
        payload=_json_payload(row[9]),
    )


def _roster(row) -> RosterRow:
    return RosterRow(
        league_id=row[0],
        season=row[1],
        as_of=row[2],
        team_id=row[3],
        player_id=row[4],
        position=row[5],
        slot_type=row[6],
        source=row[7],
        ts_precision=row[8],
    )
