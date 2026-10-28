"""Light read-only context pulls: match format / stars / LAN / team ranks from the HLTV Postgres (indexed matchid lookups,
no map-level joins; research/cs2_accuracy_2y/inventory/db.py read-only session) and the stored learning dashboard."""
import sys, json, time
sys.path.insert(0, r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad")
sys.path.insert(0, r"C:\Users\tikic\OneDrive\Documents\cs2_scout\.claude\worktrees\agent-a63fa0bb4b1c57213\research\cs2_accuracy_2y\inventory")
from lq import adm
import db

d = json.load(open("p1_units.json"))
ids = sorted({int(x["match"][5:]) for x in d["rows"] if x["match"].startswith("hltv:")})
idl = ",".join(map(str, ids))
out = {}
out["upcoming"] = [dict(r) for r in db.q(f"select u.matchid, u.format, u.stars, u.event, u.time, o.lan, o.date from upcoming_matches u "
                                         f"left join match_overview o on o.matchid=u.matchid where u.matchid in ({idl})")]
print("upcoming", len(out["upcoming"]), out["upcoming"][:1], flush=True)
out["teams"] = [dict(r) for r in db.q(f"select distinct matchid, teamid from matches where matchid in ({idl})")]
print("teams", len(out["teams"]), flush=True)
tids = sorted({r["teamid"] for r in out["teams"] if r["teamid"]})
out["ranks"] = [dict(r) for r in db.q(f"select teamid, date, rank from team_rankings where teamid in ({','.join(map(str, tids))}) "
                                      f"and date >= '2026-10-01' order by teamid, date")]
print("ranks", len(out["ranks"]), flush=True)
json.dump(out, open("hltv_ctx.json", "w"), default=str)
time.sleep(0.5)
dash = adm("/admin/learning.json")
json.dump(dash, open("learning_dashboard.json", "w"), default=str)
print("dash keys", list(dash.keys())[:40])
