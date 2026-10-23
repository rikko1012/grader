"""Read-only helper for the production HLTV Postgres. DSN is loaded from .env.render-db into the
process environment only; it is never printed or written. Every session is READ ONLY with a
statement_timeout."""
import os, re
import psycopg2, psycopg2.extras

ENV = r"C:\Users\tikic\cs2_scout\.env.render-db"

def _dsn():
    if os.environ.get("HLTV_DB_DSN"):
        return os.environ["HLTV_DB_DSN"]
    vals = {}
    for line in open(ENV, encoding="utf-8"):
        m = re.match(r"\s*([A-Z_]+)\s*=\s*(.+?)\s*$", line)
        if m:
            vals[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    os.environ["HLTV_DB_DSN"] = vals.get("DATABASE_URL_UNPOOLED") or vals["DATABASE_URL"]
    return os.environ["HLTV_DB_DSN"]

_conn = None
def conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(_dsn(), connect_timeout=20)
        _conn.set_session(readonly=True, autocommit=True)
        with _conn.cursor() as c:
            c.execute("SET statement_timeout = '120s'")
    return _conn

def q(sql, args=None):
    with conn().cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
        c.execute(sql, args)
        return [dict(r) for r in c.fetchall()]
