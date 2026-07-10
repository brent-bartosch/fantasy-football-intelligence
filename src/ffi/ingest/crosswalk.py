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
