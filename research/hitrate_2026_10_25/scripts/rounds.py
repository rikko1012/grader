import json, collections
from hq import q
from stats import week, boot_ratio
U = json.load(open("cs2_units.json"))
hid = lambda u: int(u["match"][5:]) if u["match"].startswith("hltv:") else None
mids = sorted({hid(u) for u in U if hid(u)})
R = collections.defaultdict(dict)
for r in q("select mm.matchid, mm.number, count(*) r from map_rounds r join match_maps mm on mm.mapstatsid=r.mapstatsid where mm.matchid = any(%s) and mm.number<=2 group by 1,2", (mids,)):
    R[r["matchid"]][r["number"]] = r["r"]
for wk in ("W1", "W2", "W3"):
    ms = {hid(u) for u in U if hid(u) and week(u["start"][:10]).startswith(wk)}
    maps = [v for m in ms for v in R.get(m, {}).values()]
    print(wk, "matches", len(ms), "maps", len(maps), "rounds/map", round(sum(maps) / len(maps), 2), "OT share", round(sum(x > 30 for x in maps) / len(maps), 3),
          "matches with OT", sum(1 for m in ms if any(v > 30 for v in R.get(m, {}).values())))
for u in U:
    u["ot"] = any(v > 30 for v in R.get(hid(u), {}).values()); u["hit"] = u["over"] if u["p"] >= .5 else 1 - u["over"]; u["pp"] = max(u["p"], 1 - u["p"])
for wk in ("W1", "W2", "W3"):
    for ot in (False, True):
        for sd in ("Over", "Under"):
            x = [u for u in U if week(u["start"][:10]).startswith(wk) and u["ot"] == ot and (u["p"] >= .5) == (sd == "Over") and u["pp"] >= .57]
            if x: print(wk, "OT" if ot else "noOT", sd, "57%+", f"{sum(u['hit'] for u in x)}-{len(x)-sum(u['hit'] for u in x)}", "matches", len({u['match'] for u in x}))
    x = [u for u in U if week(u["start"][:10]).startswith(wk) and (u["units"] or 0) > 0 and not u["ot"]]
    print(wk, "staked, matches without an OT map:", f"{sum(u['hit'] for u in x)}/{len(x)}", round(sum(u['hit'] for u in x) / len(x), 3), boot_ratio(x, lambda u: u["hit"]))
us = [u for u in U if u["rounds_sum"] and u["prop"] == "kills" and u["scope"] == "12" and len(R.get(hid(u), {})) == 2]
for wk in ("W1", "W2", "W3"):
    x = [u for u in us if week(u["start"][:10]).startswith(wk)]
    d = lambda u: sum(R[hid(u)].values()) - u["rounds_sum"]
    if x: print(wk, "kills M1-2 units", len(x), "actual - rr-1 expected rounds", round(sum(map(d, x)) / len(x), 2), boot_ratio(x, d))
