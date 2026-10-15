import math, os, pickle, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from study import comp, ci, LIVE
R = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "series.pkl"), "rb"))
ev = [r for r in R if r["date"] >= "2026-01" and r.get("y1") is not None]
for r in ev:
    a, b = comp(r, "n0"), comp(r, "n")
    r["L1"], r["C1"] = a["lam"] / 2, b["lam"] / 2                 # Map 1 = one map of the same model
    r["L1h"], r["C1h"] = a["lam_hs"] / 2, b["lam_hs"] / 2
g = lambda r: abs(r["L1"] - r["y1"]) - abs(r["C1"] - r["y1"]); c = ci(ev, g, B=400)
hs = [r for r in ev if r.get("y1_hs") is not None]; gh = lambda r: abs(r["L1h"] - r["y1_hs"]) - abs(r["C1h"] - r["y1_hs"]); ch = ci(hs, gh, B=400)
print(f"Map 1 ({len(ev)} rows / {len({r['m'] for r in ev})} matches): kills MAE current {sum(abs(r['L1']-r['y1']) for r in ev)/len(ev):.3f} full "
      f"{sum(abs(r['C1']-r['y1']) for r in ev)/len(ev):.3f} gain {sum(g(r) for r in ev)/len(ev):+.3f} ({c[0]:+.3f} to {c[1]:+.3f}); bias current "
      f"{sum(r['L1']-r['y1'] for r in ev)/len(ev):+.2f} full {sum(r['C1']-r['y1'] for r in ev)/len(ev):+.2f}; HS MAE gain {sum(gh(r) for r in hs)/len(hs):+.3f} ({ch[0]:+.3f} to {ch[1]:+.3f})")
