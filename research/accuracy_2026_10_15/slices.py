import math, os, pickle, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import study
from study import comp, logscore, ci, LIVE
R = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "series_v1.pkl"), "rb"))
for r in R:
    r["fold"] = r["date"][:7]
    a, b = comp(r, "n0"), comp(r, "n")
    r["L"], r["Lh"], r["C"], r["Ch"] = a["lam"], a["lam_hs"], b["lam"], b["lam_hs"]
ev = [r for r in R if r["fold"] >= "2026-01"]
def line(nm, rs):
    if len({r['m'] for r in rs}) < 20:
        return
    g = lambda r: abs(r["L"] - r["y"]) - abs(r["C"] - r["y"])
    gl = lambda r: logscore(r["y"], r["C"], LIVE["c"]) - logscore(r["y"], r["L"], LIVE["c"])
    hs = [r for r in rs if r["y_hs"] is not None]
    gh = lambda r: abs(r["Lh"] - r["y_hs"]) - abs(r["Ch"] - r["y_hs"])
    c1, c2, c3 = ci(rs, g, B=300), ci(rs, gl, B=300), ci(hs, gh, B=300)
    bl = sum(r["L"] - r["y"] for r in rs) / len(rs); bc = sum(r["C"] - r["y"] for r in rs) / len(rs)
    print(f"| {nm} | {len(rs)} | {len({r['m'] for r in rs})} | {bl:+.2f} -> {bc:+.2f} | {sum(g(r) for r in rs)/len(rs):+.3f} ({c1[0]:+.3f} to {c1[1]:+.3f}) | "
          f"{sum(gl(r) for r in rs)/len(rs):+.4f} ({c2[0]:+.4f} to {c2[1]:+.4f}) | {sum(gh(r) for r in hs)/len(hs):+.3f} ({c3[0]:+.3f} to {c3[1]:+.3f}) |")
H = ("| slice | player-series | matches | kills bias current -> full | kills MAE gain (95%) | log-score gain (95%) | HS MAE gain (95%) |\n"
     "|---|---:|---:|---|---|---|---|")
print("Full history vs the current pinned history, Maps 1-2, Jan-Oct 2026 (+ = full history better)\n" + H)
line("All", ev)
line("original top-200 set", [r for r in ev if r["n0_target"]]); line("lower tier (backfill only)", [r for r in ev if not r["n0_target"]])
for lo, hi, nm in ((0, 1e-9, "no old-history"), (1e-9, 150, "< 150 rounds old-history"), (150, 600, "150-600"), (600, 1500, "600-1500"), (1500, 1e9, ">= 1500")):
    line(nm, [r for r in ev if lo <= r["n0"]["r"] < hi])
line("LAN", [r for r in ev if r["lan"]]); line("Online", [r for r in ev if not r["lan"]])
qs = sorted(r["elo"][1] for r in ev); cut = [qs[int(len(qs) * q)] for q in (0.25, 0.5, 0.75)]
for i, nm in enumerate(("opponent weakest quartile", "opponent 2nd", "opponent 3rd", "opponent strongest")):
    lo = cut[i - 1] if i else -1e9; hi = cut[i] if i < 3 else 1e9
    line(nm, [r for r in ev if lo <= r["elo"][1] < hi])
for lo, hi, nm in ((-1e9, -50, "underdogs"), (-50, 50, "even"), (50, 1e9, "favourites")):
    line(nm, [r for r in ev if lo <= r["elo"][0] - r["elo"][1] < hi])
print("\nBy month, top-200 set only\n" + H)
for f in sorted({r["fold"] for r in ev}):
    line(f, [r for r in ev if r["fold"] == f and r["n0_target"]])
print("\nBy month, all\n" + H)
for f in sorted({r["fold"] for r in ev}):
    line(f, [r for r in ev if r["fold"] == f])
