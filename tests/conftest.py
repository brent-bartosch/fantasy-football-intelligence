import pathlib
import psycopg2
import psycopg2.extras
import pytest

import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))


@pytest.fixture()
def db():
    conn = psycopg2.connect(dbname="fantasy_football_test", host="localhost")
    repo_root = pathlib.Path(__file__).parent.parent
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.players')")
        if cur.fetchone()[0] is None:
            cur.execute((repo_root / "schema" / "create_tables.sql").read_text())
        for mig in sorted((repo_root / "migrations").glob("*.sql")):
            cur.execute(mig.read_text())
    conn.commit()
    yield conn
    conn.rollback()
    with conn.cursor() as cur:
        # Truncate every table in the derived schemas + the mutable public ones.
        cur.execute(
            """SELECT schemaname, tablename FROM pg_tables
               WHERE schemaname IN ('raw','scoring','valuation','signals','sim','draft')"""
        )
        tables = [f"{s}.{t}" for s, t in cur.fetchall()]
        cur.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
        cur.execute(
            "TRUNCATE public.player_id_xwalk, public.matchup_results, "
            "public.manager_slot_annotations, public.leagues RESTART IDENTITY CASCADE"
        )
        cur.execute("TRUNCATE players CASCADE")
    conn.commit()
    conn.close()
