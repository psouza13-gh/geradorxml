"""
Neon Postgres connection helper.
Creates a fresh connection per call — no persistent pool (serverless-safe).
"""
import os
from contextlib import contextmanager

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


@contextmanager
def transaction():
    """
    Run several statements in ONE atomic transaction on a single connection.

    Usage:
        with transaction() as cur:
            cur.execute(...)
            rows = cur.fetchall()
            cur.execute(...)
        # committed on clean exit; rolled back on exception

    Use this (with pg_advisory_xact_lock) for read-then-write logic that must
    not race under concurrency (e.g. enforcing usage limits).
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
