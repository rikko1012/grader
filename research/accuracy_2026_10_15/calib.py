import math, os, pickle, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from study import comp, ci, LIVE
R = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "series_v1.pkl"), "rb"))
GAP = lambda r: max(-1.0, min(1.0, (r["elo"][0] - r["elo"][1]) / 400.0))
for r in R:
    r["fold"] = r["date"][:7]; r["L"] = comp(r, "n0")["lam"]; r["C"] = comp(r, "n")["lam"]
ev = [r for r in R if r["fold"] >= "2026-01"]
z80, z50 = 1.2816, 0.6745
print("Predictive spread on history (live c 1.457): share of actual totals inside the central 50% / 80% intervals")
print("| month | current 50% | current 80% | full 50% | full 80% |\n|---|---:|---:|---:|---:|")
for f in sorted({r["fold"] for r in ev}):
    rs = [r for r in ev if r["fold"] == f]
    cov = lambda k, z: sum(abs(r["y"] - r[k]) <= z * LIVE["c"] * math.sqrt(max(r[k], 1)) for r in rs) / len(rs)
    print(f"| {f} | {cov('L', z50):.1%} | {cov('L', z80):.1%} | {cov('C', z50):.1%} | {cov('C', z80):.1%} |")
def spearman(rs, k):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i]); rk = [0] * len(v)
        for i, j in enumerate(o): rk[j] = i
        return rk
    a, b = rank([r[k] for r in rs]), rank([r["y"] for r in rs]); n = len(rs)
    ma, mb = sum(a) / n, sum(b) / n
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
print(f"\nDiscrimination of totals (Spearman rank correlation, projection vs actual kills): current {spearman(ev, 'L'):.3f}, full {spearman(ev, 'C'):.3f}")
for k, nm in (("L", "current"), ("C", "full")):
    xs = [r[k] for r in ev]; ys = [r["y"] for r in ev]; mx = sum(xs) / len(xs); my = sum(ys) / len(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    print(f"calibration slope of actual on projection ({nm}): {b:.3f} (1 = right)")
# Elo term on the full-history view: rolling fit, residual bias by Elo gap
fits = {}
for f in sorted({r["fold"] for r in R}):
    tr = [r for r in R if r["fold"] < f]
    num = sum(r["C"] * GAP(r) * (r["y"] - r["C"]) for r in tr); den = sum((r["C"] * GAP(r)) ** 2 for r in tr)
    fits[f] = num / den if den and len(tr) > 2000 else 0.0
for r in R:
    r["CE"] = r["C"] * (1 + fits[r["fold"]] * GAP(r))
print("\nResidual bias by Elo gap (projection - actual), Jan-Oct 2026")
print("| group | rows | current | full | full + Elo term | MAE gain of Elo term on full (95%) |\n|---|---:|---:|---:|---:|---|")
for lo, hi, nm in ((-1e9, -150, "big underdog"), (-150, -50, "underdog"), (-50, 50, "even"), (50, 150, "favourite"), (150, 1e9, "big favourite")):
    rs = [r for r in ev if lo <= r["elo"][0] - r["elo"][1] < hi]
    g = lambda r: abs(r["C"] - r["y"]) - abs(r["CE"] - r["y"]); c = ci(rs, g, B=300)
    b = lambda k: sum(r[k] - r["y"] for r in rs) / len(rs)
    print(f"| {nm} | {len(rs)} | {b('L'):+.2f} | {b('C'):+.2f} | {b('CE'):+.2f} | {sum(g(r) for r in rs)/len(rs):+.3f} ({c[0]:+.3f} to {c[1]:+.3f}) |")
