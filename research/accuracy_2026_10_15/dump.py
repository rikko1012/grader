"""Read-only dump of the HLTV database (all tiers) for the accuracy study: one row per player per map, same query as
/admin/hltv_export, plus each match's first-fetch time (to separate the original set from the 2026-10-13 backfill).
Never prints the DSN."""
import json, os, sys
from datetime import date, timedelta
REPO = r"C:/Users/tikic/cs2_scout"
sys.path.insert(0, REPO)
for line in open(os.path.join(REPO, ".env.render-db"), encoding="utf-8"):
    if line.startswith("DATABASE_URL="):
        os.environ["HLTV_DB_DSN"] = line.split("=", 1)[1].strip().strip('"')
import hltv
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dump"); os.makedirs(OUT, exist_ok=True)
con = hltv._connect()
cur = con.cursor()
cur.execute("SET statement_timeout = 300000")
cur.execute("SELECT min(date), max(date), count(*) FROM match_overview"); print("match_overview", cur.fetchone())
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'match_overview'"); mo_cols = [r[0] for r in cur.fetchall()]
print("match_overview cols", mo_cols)
ft = next((c for c in ("first_fetched", "fetched_at", "found_at", "created_at", "inserted_at") if c in mo_cols), None)
cols = ["match", "date", "event", "lan", "map_no", "map", "player", "team", "opp", "k", "d", "ek", "ed", "adr", "eadr", "kast", "ekast",
        "rating", "swing", "hs", "own", "opp_score"]
d0 = date(2025, 9, 1)
while d0 <= date(2026, 10, 15):
    d1 = min(date(d0.year + (d0.month == 12), d0.month % 12 + 1, 1) - timedelta(days=1), date(2026, 10, 15))
    cur.execute("""SELECT mo.matchid, mo.date, mo.event, mo.lan, mm.number, mp.name, ps.playerid, ps.teamid, opp.teamid,
                          ps.k, ps.d, ps.ek, ps.ed, ps.adr, ps.eadr, ps.kast, ps.ekast, ps.rating, ps.roundswing, h.hs, own.score, opp.score
                   FROM match_maps mm JOIN match_overview mo USING (matchid) JOIN maps mp ON mp.mapid = mm.mapid
                   JOIN player_stats ps ON ps.matchid = mm.matchid AND ps.mapid = mm.mapid AND ps.sideid = 0
                   JOIN matches own ON own.matchid = mm.matchid AND own.teamid = ps.teamid AND own.mapid = mm.mapid AND own.sideid = 0
                   JOIN matches opp ON opp.matchid = mm.matchid AND opp.teamid <> ps.teamid AND opp.mapid = mm.mapid AND opp.sideid = 0
                   LEFT JOIN map_headshots h ON h.mapstatsid = mm.mapstatsid AND h.playerid = ps.playerid
                   WHERE mo.date >= %s AND mo.date <= %s ORDER BY mo.date, mo.matchid, mm.number""", (d0, d1))
    rows = [[str(v) if c == "date" else (float(v) if hasattr(v, "is_finite") else v) for c, v in zip(cols, r)] for r in cur.fetchall()]
    json.dump({"cols": cols, "rows": rows}, open(os.path.join(OUT, f"{d0.isoformat()[:7]}.json"), "w"))
    print(d0.isoformat()[:7], len(rows), flush=True)
    d0 = d1 + timedelta(days=1)
if ft:
    cur.execute(f"SELECT matchid, {ft} FROM match_overview"); fetched = {m: (str(t) if t else None) for m, t in cur.fetchall()}
else:
    fetched = {}
cur.execute("SELECT playerid, name FROM players"); names = dict(cur.fetchall())
cur.execute("SELECT teamid, name, country FROM teams"); teams = {t: [n, c] for t, n, c in cur.fetchall()}
json.dump({"fetched_col": ft, "fetched": fetched, "players": names, "teams": teams}, open(os.path.join(OUT, "meta.json"), "w"))
print("meta: fetched col", ft, len(fetched))
con.close()
