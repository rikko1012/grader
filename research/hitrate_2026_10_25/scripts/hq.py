import os, re, sys
def dsn():
    t = open(r"C:\Users\tikic\cs2_scout\.env.render-db").read()
    m = re.search(r'^\s*(?:export\s+)?(HLTV_DB_DSN|DATABASE_URL|[A-Z_]*DSN[A-Z_]*)\s*=\s*"?([^"\n]+)"?', t, re.M)
    return m.group(2).strip()
def q(sql, args=()):
    import psycopg2
    con = psycopg2.connect(os.environ.get("HLTV_DB_DSN") or dsn(), connect_timeout=20)
    con.set_session(readonly=True)
    cur = con.cursor(); cur.execute("SET statement_timeout = 60000"); cur.execute(sql, args)
    cols = [c[0] for c in cur.description]; rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    con.close(); return rows
