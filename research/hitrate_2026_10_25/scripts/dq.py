import json, collections, datetime as dt
from stats import *
U = json.load(open("cs2_units.json"))
P = lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
for u in U:
    u["d"] = u["start"][:10]; u["wk"] = week(u["d"]); u["pp"] = max(u["p"], 1 - u["p"]); u["hit"] = u["over"] if u["p"] >= 0.5 else 1 - u["over"]
    u["lead_h"] = (P(u["start"]) - P(u["logged"])).total_seconds() / 3600
def r(name, us):
    if not us: return
    n=len(us); w=sum(u["hit"] for u in us); lo,hi=boot_ratio(us, lambda u:u["hit"])
    print(f"{name:40s} n={n:4d} m={len({u['match'] for u in us}):3d} hit={w/n:.3f} [{lo:.3f},{hi:.3f}] printed={sum(u['pp'] for u in us)/n:.3f}")
print("## lead time (start - P1 forecast)")
for wk in sorted({u["wk"] for u in U}):
    for lo, hi in ((0.5,1),(1,3),(3,8),(8,100)):
        r(f"{wk} lead {lo}-{hi}h", [u for u in U if u["wk"]==wk and lo<=u["lead_h"]<hi])
print("## lead>=3h staked/all share by week", {wk: round(sum(1 for u in U if u['wk']==wk and u['lead_h']>=3)/sum(1 for u in U if u['wk']==wk),3) for wk in sorted({u['wk'] for u in U})})
print("## sample_maps")
for wk in sorted({u["wk"] for u in U}):
    for lo, hi in ((0,10),(10,25),(25,50),(50,10000)):
        r(f"{wk} maps {lo}-{hi}", [u for u in U if u["wk"]==wk and u["sample_maps"] is not None and lo<=u["sample_maps"]<hi])
print("## underdog (p_win)")
for wk in sorted({u["wk"] for u in U}):
    for lo, hi in ((0,0.35),(0.35,0.65),(0.65,1.01)):
        for side in ("Over","Under"):
            r(f"{wk} pwin {lo}-{hi} {side}", [u for u in U if u["wk"]==wk and u["p_win"] is not None and lo<=u["p_win"]<hi and (u["p"]>=0.5)==(side=="Over")])
print("## suspicious actuals (kills M1-2 < 8, HS M1-2 < 2)")
sus=[u for u in U if u["scope"]=="12" and ((u["prop"]=="kills" and u["actual"]<8) or (u["prop"]=="hs" and u["actual"]<2))]
for u in sus: print(u["match"], u["prop"], u["line"], u["lam"], u["actual"], u["logged"][:16], u["version"], u["src"])
print("## same match/prop/scope with identical actual AND lam (possible dup players)")
g=collections.Counter((u["match"],u["prop"],u["scope"],u["actual"],u["lam"]) for u in U)
print([k for k,v in g.items() if v>1][:20])
print("## units per match/prop/scope > 10")
g=collections.Counter((u["match"],u["prop"],u["scope"]) for u in U)
print([(k,v) for k,v in g.items() if v>10])
