"""Read-only: for matches with Map 1 settlement conflicts, compare HLTV match_maps.number with mapstatsid order, the veto
order and the per-map totals production settled. DSN never printed."""
import json
import os
import pickle
import sys

import psycopg2

sys.stdout.reconfigure(encoding="utf-8")
url = [l.split("=", 1)[1].strip().strip('"') for l in open(r"C:\Users\tikic\cs2_scout\.env.render-db", encoding="utf-8")
       if l.startswith("DATABASE_URL")][0]
con = psycopg2.connect(url, connect_timeout=20)
con.set_session(readonly=True, autocommit=True)
cur = con.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'match_maps'")
print("match_maps cols", [r[0] for r in cur.fetchall()])
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'map_vetoes'")
print("map_vetoes cols", [r[0] for r in cur.fetchall()])
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'mapstats_pages'")
print("mapstats_pages cols", [r[0] for r in cur.fetchall()])
for mid in (2398999, 2398757, 2399077):
    pass
# find the og-vs-pure match id of 2026-10-07 and nut nut
cur.execute("""SELECT mo.matchid, mo.date, mo.event FROM match_overview mo JOIN matches m ON m.matchid = mo.matchid JOIN teams t ON t.teamid = m.teamid
               WHERE lower(t.name) IN ('og', 'pure') AND mo.date BETWEEN '2026-10-06' AND '2026-10-08' GROUP BY 1, 2, 3""")
ms = cur.fetchall()
print("og / pure matches:", ms)
for mid, d, ev in ms[:4]:
    cur.execute("SELECT * FROM match_maps WHERE matchid = %s ORDER BY number", (mid,))
    cols = [c[0] for c in cur.description]
    print("\nmatch", mid, d, ev)
    for r in cur.fetchall():
        print("  ", dict(zip(cols, [str(x)[:30] for x in r])))
    cur.execute("SELECT * FROM map_vetoes WHERE matchid = %s ORDER BY 1, 2", (mid,))
    vc = [c[0] for c in cur.description]
    for r in cur.fetchall()[:10]:
        print("   veto", dict(zip(vc, [str(x)[:30] for x in r])))
    cur.execute("""SELECT mm.number, mm.mapstatsid, p.name, ps.k, h.hs FROM match_maps mm JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
                   JOIN players p ON p.playerid = ps.playerid LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
                   WHERE mm.matchid = %s AND lower(p.name) IN ('nut nut', 'bodyy', 'spooke', 'andu') ORDER BY p.name, mm.number""", (mid,))
    for r in cur.fetchall():
        print("   ", r)
