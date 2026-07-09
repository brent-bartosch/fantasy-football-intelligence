import pathlib
import psycopg2
import psycopg2.extras
import pytest


@pytest.fixture()
def db():
    conn = psycopg2.connect(dbname="fantasy_football_test", host="localhost")
    mig = pathlib.Path(__file__).parent.parent / "migrations" / "001_foundation.sql"
    with conn.cursor() as cur:
        cur.execute(mig.read_text())
    conn.commit()
    yield conn
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE raw.ingest_runs RESTART IDENTITY CASCADE")
        cur.execute(
            "TRUNCATE raw.sleeper_projections, raw.nflverse_player_week, "
            "raw.yahoo_league_settings, raw.yahoo_player_week, public.player_id_xwalk"
        )
        cur.execute(
            "TRUNCATE players CASCADE"
        )  # tests seed players; keep runs idempotent
    conn.commit()
    conn.close()
