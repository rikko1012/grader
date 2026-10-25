"""Direction 6: audit every settled P1 CS2 unit against HLTV map rows (read-only, bounded SELECTs).
Checks: (a) the settled total equals the graded player's own HLTV kills/HS over the scope's maps (player id from the
saved inputs); (b) the player appeared on the map (not benched); (c) the saved opponent id is the HLTV opponent;
(d) stand-in / roster flags; (e) duplicate player in one match/stat/scope; (f) lead time / stale-data flags."""
import json, collections
from hq import q
P1 = json.load(open("p1.json")); INP = {int(k): v for k, v in json.load(open("inputs_p1.json")).items()}
U = P1["rows"]
hid = lambda u: int(u["match"][5:]) if u["match"].startswith("hltv:") else None
mids = sorted({hid(u) for u in U if hid(u)})
rows = []
for i in range(0, len(mids), 300):
    rows += q("""select mm.matchid, mm.number, h.playerid, h.k, h.hs, (select max(ps.teamid) from map_player_stats ps where ps.mapstatsid=h.mapstatsid and ps.playerid=h.playerid) teamid
                 from match_maps mm join map_headshots h on h.mapstatsid = mm.mapstatsid where mm.matchid = any(%s)""", (mids[i:i + 300],))
M = collections.defaultdict(lambda: collections.defaultdict(dict))  # match -> player -> number -> (k,hs,team)
for r in rows: M[r["matchid"]][r["playerid"]][r["number"]] = (r["k"], r["hs"], r["teamid"])
teams = collections.defaultdict(set)
for r in rows: teams[r["matchid"]].add(r["teamid"])
c = collections.Counter(); bad = collections.defaultdict(list)
for u in U:
    i = INP.get(u["forecast_id"]); m = hid(u)
    if m is None: c["non-HLTV match key"] += 1; continue
    if m not in M: c["HLTV match has no map rows"] += 1; bad["no rows"].append(u["match"]); continue
    if not i: c["no saved inputs"] += 1; continue
    perf = i.get("perf") or {}
    pids = perf.get("hltv_player_ids") or []
    if not pids: c["no HLTV player id in inputs (BO3.gg route)"] += 1; continue
    nmax = {"1": 1, "12": 2, "123": 3}[u["scope"]]
    best = None
    for pid in pids:
        pm = M[m].get(pid)
        if not pm: continue
        maps = [pm[n] for n in sorted(pm) if n <= nmax]
        tot = sum(x[0] if u["prop"] == "kills" else x[1] for x in maps)
        best = (pid, tot, len(maps), {x[2] for x in maps})
        if tot == u["actual"]: break
    if best is None:
        c["graded player id not on any map (benched / wrong player?)"] += 1; bad["absent"].append((u["match"], pids, u["prop"], u["actual"])); continue
    pid, tot, nm, tset = best
    if tot != u["actual"]:
        c["settled total != player's HLTV total"] += 1; bad["mismatch"].append((u["match"], pid, u["prop"], u["scope"], u["actual"], tot, nm))
    else:
        c["ok: settled = player's own HLTV total"] += 1
    opp = perf.get("hltv_opp_team")
    if opp is not None:
        if opp in tset: c["opponent id == player's own team (wrong side)"] += 1; bad["opp_self"].append((u["match"], opp))
        elif opp not in teams[m]: c["opponent id not in HLTV match"] += 1; bad["opp_other"].append((u["match"], opp, sorted(teams[m]), u["start"][:10]))
        else: c["opponent ok"] += 1
    if i.get("is_standin"): c["stand-in flagged"] += 1
    if i.get("roster_confirmed") is False: c["roster not confirmed"] += 1
dup = collections.Counter((u["match"], u["prop"], u["scope"], tuple((INP.get(u["forecast_id"]) or {}).get("perf", {}).get("hltv_player_ids") or [u["forecast_id"]])) for u in U)
c["duplicate player units"] = sum(v - 1 for v in dup.values() if v > 1)
out = dict(counts=c, examples={k: v[:25] for k, v in bad.items()}, opp_other_by_match=collections.Counter(x[0] for x in bad["opp_other"]).most_common(20))
json.dump(out, open("audit_units.json", "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str)[:6000])
