import os
from contextlib import contextmanager
import psycopg2, psycopg2.extras

def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"],
                            cursor_factory=psycopg2.extras.RealDictCursor)

@contextmanager
def db_cursor(commit=True):
    conn = _connect()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit() if commit else conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    path = os.path.join(os.path.dirname(__file__), "schema.sql")
    with db_cursor() as cur, open(path) as f:
        cur.execute(f.read())
