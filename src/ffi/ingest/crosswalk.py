import polars as pl
import psycopg2.extras
from ffi.ingest.base import IngestError

FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
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
    return len(rows)


def match_report(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.player_name, p.position, split_part(p.yahoo_player_id, '.p.', 2) AS yid,
                   x.xwalk_id
            FROM players p
            LEFT JOIN public.player_id_xwalk x
                   ON x.yahoo_id = split_part(p.yahoo_player_id, '.p.', 2)
            WHERE p.position IN %s
        """,
            (FANTASY_POSITIONS,),
        )
        rows = cur.fetchall()
    unmatched = [(n, pos, yid) for (n, pos, yid, xid) in rows if xid is None]
    return {
        "total_fantasy_players": len(rows),
        "matched": len(rows) - len(unmatched),
        "unmatched": unmatched,
    }
