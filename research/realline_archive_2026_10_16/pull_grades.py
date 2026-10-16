"""Read-only: save the scraper's prop_grades archive (offered lines + HLTV grading) and print a day-by-day inventory."""
import collections
import json
import psycopg2

url = [l.split("=", 1)[1].strip().strip('"') for l in open(r"C:\Users\tikic\cs2_scout\.env.render-db", encoding="utf-8")
       if l.startswith("DATABASE_URL")][0]
con = psycopg2.connect(url, connect_timeout=20)
con.set_session(readonly=True, autocommit=True)
cur = con.cursor()
cur.execute("SELECT * FROM prop_grades ORDER BY start_time, book, player")
cols = [d[0] for d in cur.description]
rows = [{c: (v.isoformat() if hasattr(v, "isoformat") else (float(v) if hasattr(v, "is_finite") else v)) for c, v in zip(cols, r)} for r in cur.fetchall()]
json.dump(rows, open("prop_grades.json", "w"), default=str)
print(len(rows), "rows saved")
for k in ("book", "market", "maps", "odds_type", "status", "result"):
    print(k, collections.Counter(r[k] for r in rows).most_common(12))
by = collections.defaultdict(collections.Counter)
for r in rows:
    d = r["start_time"][:10]
    by[d]["lines"] += 1
    by[d][r["book"]] += 1
    by[d]["std" if r["odds_type"] == "standard" else "adj"] += 1
    by[d]["graded"] += r["status"] == "graded"
    by[d]["matchid"] += r["matchid"] is not None
    by[d]["map_values"] += bool(r["map_values"])
print("\nday: lines, underdog, prizepicks, standard, graded, with HLTV match id")
for d in sorted(by):
    c = by[d]
    print(" ", d, c["lines"], c["underdog"], c["prizepicks"], c["std"], c["graded"], c["matchid"],
          "matches", len({r["matchid"] for r in rows if r["start_time"][:10] == d and r["matchid"]}))
print("status x note:", collections.Counter((r["status"], (r["note"] or "")[:50]) for r in rows).most_common(15))
