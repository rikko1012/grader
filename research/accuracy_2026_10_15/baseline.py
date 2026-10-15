import math, os, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from study import R, comp, logscore, ci, FOLDS, LIVE
for r in R:
    c = comp(r, "n0"); r["lam"] = c["lam"]; r["lam_hs"] = c["lam_hs"]; r["c0"] = c
    r["tier"] = "original top-200 set" if r["n0_target"] else "lower tier (backfill only)"
    r["hist"] = r["n0"]["r"]
def row(name, rs):
    if not rs: return
    mae = sum(abs(r["lam"] - r["y"]) for r in rs) / len(rs); b = sum(r["lam"] - r["y"] for r in rs) / len(rs)
    lo, hi = ci(rs, lambda r: r["lam"] - r["y"], B=400)
    hs = [r for r in rs if r["y_hs"] is not None]
    print(f"| {name} | {len(rs)} | {len({r['m'] for r in rs})} | {mae:.2f} | {b:+.2f} ({lo:+.2f} to {hi:+.2f}) | "
          f"{sum(abs(r['lam_hs'] - r['y_hs']) for r in hs)/len(hs):.2f} | {sum(r['lam_hs'] - r['y_hs'] for r in hs)/len(hs):+.2f} |")
H = "| slice | player-series | matches | kills MAE | kills bias (proj - actual, 95%) | HS MAE | HS bias |\n|---|---:|---:|---:|---|---:|---:|"
print("CURRENT MODEL (live perf-2 formula, old-history view = live pin), Maps 1-2\n" + H)
row("All", R)
for t in ("original top-200 set", "lower tier (backfill only)"):
    row(t, [r for r in R if r["tier"] == t])
print("\nBy month\n" + H)
for f in FOLDS:
    row(f, [r for r in R if r["fold"] == f])
print("\nBy month, original top-200 set only\n" + H)
for f in FOLDS:
    row(f, [r for r in R if r["fold"] == f and r["n0_target"]])
print("\nBy weighted history (old-history view)\n" + H)
for lo_, hi_, nm in ((0, 1e-9, "no history"), (1e-9, 150, "< 150 rounds"), (150, 600, "150-600"), (600, 1500, "600-1500"), (1500, 1e9, ">= 1500")):
    row(nm, [r for r in R if lo_ <= r["hist"] < hi_])
print("\nBy environment\n" + H)
row("LAN", [r for r in R if r["lan"]]); row("Online", [r for r in R if not r["lan"]])
print("\nBy opponent strength (as-of Elo quartile of the opponent)\n" + H)
qs = sorted(r["elo"][1] for r in R); cut = [qs[int(len(qs) * q)] for q in (0.25, 0.5, 0.75)]
for i, nm in enumerate(("weakest", "2nd", "3rd", "strongest")):
    lo_ = cut[i - 1] if i else -1e9; hi_ = cut[i] if i < 3 else 1e9
    row(nm, [r for r in R if lo_ <= r["elo"][1] < hi_])
print("\nBy Elo gap (own - opponent)\n" + H)
for lo_, hi_, nm in ((-1e9, -150, "big underdog"), (-150, -50, "underdog"), (-50, 50, "even"), (50, 150, "favourite"), (150, 1e9, "big favourite")):
    row(nm, [r for r in R if lo_ <= r["elo"][0] - r["elo"][1] < hi_])
print("\nBy actual rounds (Maps 1-2) - realised, for orientation only\n" + H)
for lo_, hi_, nm in ((0, 40, "< 40"), (40, 46, "40-45"), (46, 52, "46-51"), (52, 999, ">= 52")):
    row(nm, [r for r in R if lo_ <= r["rounds"] < hi_])
