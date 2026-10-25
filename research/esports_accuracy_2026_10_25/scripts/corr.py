"""HLTV-truth corrections for units settled on the partial map (hltv:2399658, mapstatsid 241953)."""
import json
from hq import q
P1 = json.load(open("p1.json")); INP = {int(k): v for k, v in json.load(open("inputs_p1.json")).items()}
rows = q("select mm.number, h.playerid, h.k, h.hs from match_maps mm join map_headshots h on h.mapstatsid=mm.mapstatsid where mm.matchid=2399658")
T = {}
for r in rows: T.setdefault(r["playerid"], {})[r["number"]] = (r["k"], r["hs"])
out = {}
for u in P1["rows"]:
    if u["match"] != "hltv:2399658": continue
    pids = ((INP.get(u["forecast_id"]) or {}).get("perf") or {}).get("hltv_player_ids") or []
    for pid in pids:
        if pid in T:
            tot = sum(v[0 if u["prop"] == "kills" else 1] for n, v in T[pid].items() if n <= 2)
            if tot != u["actual"]:
                out[u["forecast_id"]] = dict(actual_settled=u["actual"], actual_hltv=tot, line=u["line"], over_settled=u["over"],
                                             over_hltv=None if tot == u["line"] else int(tot > u["line"]), p=u["p"], units=u["units"])
json.dump(out, open("corrections_2399658.json", "w"), indent=1)
print(len(out), "corrected;", sum(1 for v in out.values() if v["over_hltv"] != v["over_settled"]), "outcome flips;",
      [(v["actual_settled"], v["actual_hltv"], v["line"], v["p"], v["units"]) for v in out.values()])
