"""Section 4 subsets + correlated-Under check for LW-1 vs live (reading only)."""
import json, collections
import numpy as np
from evalx import *
from hq import q
P1 = json.load(open("p1.json")); FC = json.load(open("fc_p1.json"))
U = sorted(P1["rows"], key=lambda u: u["start"][:10])  # same order as lw1.py (stable sort by day)
p_lw = np.load("p_lw1.npy")
S = np.array([u["start"][:10] > "2026-10-13" for u in U])
U = [u for u, s in zip(U, S) if s]; pc = p_lw[S]
y = np.array([u["over"] for u in U], float); pl = np.array([u["p"] for u in U]); ul = np.array([float(u["units"] or 0) for u in U])
mm = np.array([u["match"] for u in U]); bl = (np.maximum(pl, 1 - pl) >= .55) & (ul <= 0)
pw = np.array([float(FC[str(u["forecast_id"])].get("p_win") or "nan") if FC[str(u["forecast_id"])].get("p_win") not in (None, "", "None") else np.nan for u in U])
hid = lambda u: int(u["match"][5:]) if u["match"].startswith("hltv:") else None
mids = sorted({hid(u) for u in U if hid(u)})
R = collections.defaultdict(dict)
for r in q("select mm.matchid, mm.number, count(*) r from map_rounds r join match_maps mm on mm.mapstatsid=r.mapstatsid where mm.matchid = any(%s) group by 1,2", (mids,)):
    R[r["matchid"]][r["number"]] = r["r"]
ot = np.array([any(v > 30 for k, v in R.get(hid(u), {}).items() if k <= (1 if u["scope"] == "1" else 2 if u["scope"] == "12" else 3)) for u in U])
print("units", len(U), "with rounds", sum(1 for u in U if hid(u) in R), "OT units", ot.sum())
grp = np.array([u["prop"] + "|" + u["scope"] for u in U])


def show(name, m):
    for arm, p in (("live", pl), ("lw1", pc)):
        s = staked(p, bl) > 0
        x = s & m
        if x.sum() == 0: print(f"  {name:38s} {arm:5s} n=0"); continue
        hit = np.where(p >= .5, y, 1 - y)[x]
        print(f"  {name:38s} {arm:5s} n={x.sum():4d} hit={hit.mean():.3f} printed={np.maximum(p,1-p)[x].mean():.3f} unders={(p[x]<.5).sum()}")


for g in sorted(set(grp)):
    for side, f in (("Over", lambda p: p >= .5), ("Under", lambda p: p < .5)):
        print(g, side)
        for arm, p in (("live", pl), ("lw1", pc)):
            s = (staked(p, bl) > 0) & (grp == g) & f(p)
            if s.sum():
                hit = np.where(p >= .5, y, 1 - y)[s]
                lo, hi = cluster_boot(mm[s], lambda i: hit[i].mean(), B=500)
                print(f"   {arm:5s} n={s.sum():4d} hit={hit.mean():.3f} [{lo:.3f},{hi:.3f}] printed={np.maximum(p,1-p)[s].mean():.3f}")
print("OT / no OT")
show("OT map in scope", ot); show("no OT map", ~ot)
print("favourite vs underdog team (p_win)")
show("team p_win >= .5", pw >= .5); show("team p_win < .5", pw < .5); show("p_win missing", np.isnan(pw))
show("UNDER on underdog team", (pw < .5));
for arm, p in (("live", pl), ("lw1", pc)):
    s = staked(p, bl) > 0
    un = s & (p < .5)
    per = collections.Counter(mm[un])
    print(arm, "staked Unders", un.sum(), "of", s.sum(), f"({un.sum()/max(s.sum(),1):.0%})", "max Unders in one match", max(per.values()) if per else 0,
          "matches with >=4 staked Unders", sum(1 for v in per.values() if v >= 4))
    # day-level: worst days
    days = np.array([u["start"][:10] for u in U])
    for d in sorted(set(days)):
        x = s & (days == d)
        if x.sum():
            h = np.where(p >= .5, y, 1 - y)[x]
            print(f"   {arm} {d} n={x.sum():3d} hit={h.mean():.3f} unders={(p[x]<.5).sum()}")
