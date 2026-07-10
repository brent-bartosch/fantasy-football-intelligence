import polars as pl
import psycopg2.extras
from ffi.ids import yahoo_numeric_id_filter_sql, yahoo_numeric_id_sql
from ffi.ingest.base import IngestError

# DEF excluded: team defenses map by team abbreviation, not player IDs —
# ff_playerids structurally contains no team-defense entries. A separate DEF
# mapping ships with the scoring engine in Phase 2.
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K")
XWALK_COLS = [
    "name",
    "position",
    "team",
    "gsis_id",
    "sleeper_id",
    "yahoo_id",
    "fantasypros_id",
]


def load_xwalk_rows(conn) -> int:
    import nflreadpy

    df = nflreadpy.load_ff_playerids()
    missing = set(XWALK_COLS) - set(df.columns)
    if missing:
        raise IngestError(
            f"ff_playerids missing columns {sorted(missing)}; actual: {sorted(df.columns)[:40]}"
        )
    # Real-shape deviation: nflreadpy returns sleeper_id (and sometimes other
    # id columns) as Int64, but public.player_id_xwalk stores ids as TEXT.
    # Cast all id columns to Utf8 defensively before extracting rows.
    id_cols = ["gsis_id", "sleeper_id", "yahoo_id", "fantasypros_id"]
    df = df.with_columns([pl.col(c).cast(pl.Utf8) for c in id_cols])
    rows = df.select(XWALK_COLS).rows()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM public.player_id_xwalk WHERE manual_override = FALSE")
        psycopg2.extras.execute_values(
            cur,
            f"INSERT INTO public.player_id_xwalk ({', '.join(XWALK_COLS)}) VALUES %s",
            rows,
            page_size=5000,
        )
    conn.commit()
    dedupe_auto_vs_manual(conn)
    identical_deleted = dedupe_identical_players(conn)
    quarantined = quarantine_conflicting_ids(conn)
    print(
        f"crosswalk cleanup: identical-dupes removed={identical_deleted} "
        f"quarantined={len(quarantined)}"
    )
    assert_no_duplicate_ids(conn)
    return len(rows)


def dedupe_auto_vs_manual(conn) -> int:
    """Manual-override rows are authoritative: drop any auto row that shares a
    yahoo_id, sleeper_id, or gsis_id with a manual row (otherwise joins fan out).
    Returns rows deleted."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.player_id_xwalk a
            USING public.player_id_xwalk m
            WHERE a.manual_override = FALSE AND m.manual_override = TRUE
              AND (   (a.yahoo_id   IS NOT NULL AND a.yahoo_id   = m.yahoo_id)
                   OR (a.sleeper_id IS NOT NULL AND a.sleeper_id = m.sleeper_id)
                   OR (a.gsis_id    IS NOT NULL AND a.gsis_id    = m.gsis_id))
            """
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def dedupe_identical_players(conn) -> int:
    """Among auto rows, collapse groups that share an identical NON-NULL
    (gsis_id, sleeper_id, yahoo_id) triple — these are the same physical
    human double-entered upstream (e.g. once per listed position, or with a
    name-spelling variant), so collapsing to one row loses zero information.
    Keeps the row with the smallest xwalk_id per group. Returns rows deleted."""
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM public.player_id_xwalk a
            USING public.player_id_xwalk b
            WHERE a.manual_override = FALSE AND b.manual_override = FALSE
              AND a.xwalk_id > b.xwalk_id
              AND coalesce(a.gsis_id, '')    = coalesce(b.gsis_id, '')
              AND coalesce(a.sleeper_id, '') = coalesce(b.sleeper_id, '')
              AND coalesce(a.yahoo_id, '')   = coalesce(b.yahoo_id, '')
              AND NOT (a.gsis_id IS NULL AND a.sleeper_id IS NULL AND a.yahoo_id IS NULL)
            """
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted


def quarantine_conflicting_ids(conn) -> list:
    # FAIL-LOUD Level 2: visible fallback. Upstream ff_playerids contains rows
    # where DIFFERENT players share an id (fringe/retired players). Keeping either
    # row would silently corrupt joins; blocking forever on upstream junk breaks
    # the pipeline. Quarantined rows are printed here AND any that matter resurface
    # in the reviewed unmatched-player report; manual_override rows are the
    # escape hatch to restore a researched mapping.
    quarantined = []
    with conn.cursor() as cur:
        for col in ("yahoo_id", "sleeper_id", "gsis_id"):
            cur.execute(
                f"""SELECT {col} FROM public.player_id_xwalk
                    WHERE manual_override = FALSE AND {col} IS NOT NULL
                    GROUP BY {col} HAVING count(*) > 1"""
            )
            dup_ids = [r[0] for r in cur.fetchall()]
            if not dup_ids:
                continue
            cur.execute(
                f"""SELECT xwalk_id, {col}, name, position FROM public.player_id_xwalk
                    WHERE manual_override = FALSE AND {col} = ANY(%s)""",
                (dup_ids,),
            )
            rows = cur.fetchall()
            for xwalk_id, val, name, position in rows:
                print(
                    f"QUARANTINE [{col}={val}] xwalk_id={xwalk_id} "
                    f"name={name!r} position={position}"
                )
                quarantined.append((col, val, name, position))
            cur.execute(
                f"DELETE FROM public.player_id_xwalk "
                f"WHERE manual_override = FALSE AND {col} = ANY(%s)",
                (dup_ids,),
            )
    conn.commit()
    return quarantined


def assert_no_duplicate_ids(conn) -> None:
    """Tripwire: any id column mapping to >1 xwalk row corrupts every join
    downstream. Fail loud with the offending ids (risk R6)."""
    with conn.cursor() as cur:
        for col in ("yahoo_id", "sleeper_id", "gsis_id"):
            cur.execute(
                f"""SELECT {col}, count(*) FROM public.player_id_xwalk
                    WHERE {col} IS NOT NULL GROUP BY 1 HAVING count(*) > 1 LIMIT 10"""
            )
            dups = cur.fetchall()
            if dups:
                raise IngestError(
                    f"crosswalk has duplicate {col} values (joins would fan out): {dups}. "
                    "Resolve via manual_override rows before proceeding."
                )


def match_report(conn) -> dict:
    assert_no_duplicate_ids(conn)
    with conn.cursor() as cur:
        # Coverage denominator: fantasy-position players with real numeric
        # Yahoo ids only. Legacy slug-format ids (e.g. 'nfl.p.patrick_mahomes',
        # duplicates of numeric-ID rows from an earlier import) can never join
        # on yahoo_id, so they are excluded from coverage but counted below.
        yid = yahoo_numeric_id_sql("p.yahoo_player_id")
        cur.execute(
            f"""
            SELECT p.player_name, p.position, {yid} AS yid, x.xwalk_id
            FROM players p
            LEFT JOIN public.player_id_xwalk x ON x.yahoo_id = {yid}
            WHERE p.position IN %s
              AND {yahoo_numeric_id_filter_sql('p.yahoo_player_id')}
        """,
            (FANTASY_POSITIONS,),
        )
        rows = cur.fetchall()
        cur.execute("SELECT count(*) FROM players WHERE position = 'DEF'")
        def_rows = cur.fetchone()[0]
        cur.execute(
            f"""
            SELECT count(*) FROM players
            WHERE position IN %s
              AND NOT {yahoo_numeric_id_filter_sql('yahoo_player_id')}
        """,
            (FANTASY_POSITIONS,),
        )
        legacy_slug_rows = cur.fetchone()[0]
    unmatched = [(n, pos, yid) for (n, pos, yid, xid) in rows if xid is None]
    return {
        "total_fantasy_players": len(rows),
        "matched": len(rows) - len(unmatched),
        "unmatched": unmatched,
        "def_rows": def_rows,
        "legacy_slug_rows": legacy_slug_rows,
    }
