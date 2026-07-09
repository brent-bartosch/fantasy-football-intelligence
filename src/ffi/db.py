import os
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


def connect(dbname: str | None = None):
    return psycopg2.connect(
        dbname=dbname or os.getenv("DB_NAME", "fantasy_football"),
        user=os.getenv("DB_USER", "brentbartosch"),
        password=os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
    )
