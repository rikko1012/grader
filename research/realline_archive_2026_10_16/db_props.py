"""Read-only: which prop / line tables exist in the HLTV scraper database and what dates they cover. DSN never printed."""
import psycopg2

url = [l.split("=", 1)[1].strip().strip('"') for l in open(r"C:\Users\tikic\cs2_scout\.env.render-db", encoding="utf-8")
       if l.startswith("DATABASE_URL")][0]
con = psycopg2.connect(url, connect_timeout=20)
con.set_session(readonly=True, autocommit=True)
cur = con.cursor()
cur.execute("SET statement_timeout = 120000")
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1")
tabs = [r[0] for r in cur.fetchall()]
print("tables:", tabs)
for t in tabs:
    if not any(s in t for s in ("prop", "line", "pp", "underdog", "odds", "thunder", "snapshot", "grade")):
        continue
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position", (t,))
    cols = cur.fetchall()
    cur.execute(f'SELECT count(*) FROM "{t}"')
    n = cur.fetchone()[0]
    print(f"\n{t}: {n} rows; cols {[c[0] for c in cols]}")
    for c, typ in cols:
        if typ.startswith("timestamp") or typ == "date" or c in ("start_time", "game_date", "date", "fetched_at", "graded_at", "created_at"):
            try:
                cur.execute(f'SELECT min("{c}"), max("{c}") FROM "{t}"')
                print(f"   {c}: {cur.fetchone()}")
            except Exception as e:
                print("   ", c, "err", e)
                con.rollback() if not con.autocommit else None
    cur.execute(f'SELECT * FROM "{t}" LIMIT 2')
    for r in cur.fetchall():
        print("   e.g.", [str(x)[:40] for x in r])
