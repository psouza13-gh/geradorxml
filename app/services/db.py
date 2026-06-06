"""
Neon Postgres connection helper.
Creates a fresh connection per call — no persistent pool (serverless-safe).
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor


def get_db():
    """Return a new psycopg2 connection using DATABASE_URL."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable not set.")
    conn = psycopg2.connect(url, cursor_factory=RealDictCursor)
    conn.autocommit = False
    return conn


def execute(sql: str, params=None, fetch: str | None = None):
    """
    Execute SQL in its own connection.

    Args:
        sql:    SQL statement
        params: tuple/list of bind parameters
        fetch:  None | 'one' | 'all'

    Returns:
        None, a single RealDictRow, or a list of RealDictRows
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(sql, params or ())
        conn.commit()
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return None
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
