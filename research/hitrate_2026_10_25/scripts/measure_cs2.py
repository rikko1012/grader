import json, collections, datetime as dt
from stats import *
U = json.load(open("cs2_units.json"))
for u in U:
    u["d"] = u["start"][:10]; u["wk"] = week(u["d"])
    u["side"] = "Over" if u["p"] >= 0.5 else "Under"
    u["pp"] = max(u["p"], 1 - u["p"]); u["hit"] = u["over"] if u["p"] >= 0.5 else 1 - u["over"]
    u["err"] = u["actual"] - u["lam"]
    u["src2"] = u["src"] or "pre-10-15 (src not logged)"
    u["tier"] = "staked (units>0)" if (u["units"] or 0) > 0 else ("graded 57%+ 0U" if u["pp"] >= 0.57 else "lean <57%")
    u["dayi"] = (dt.date.fromisoformat(u["d"]) - dt.date(2026, 10, 11)).days
def row(name, us):
    if not us: return
    w = sum(u["hit"] for u in us); n = len(us)
    lo, hi = boot_ratio(us, lambda u: u["hit"])
    pr = sum(u["pp"] for u in us) / n
    elo, ehi = boot_ratio(us, lambda u: u["err"])
    print(f"{name:52s} n={n:4d} m={len({u['match'] for u in us}):3d} W-L={w}-{n-w:<4d} hit={w/n:.3f} [{lo:.3f},{hi:.3f}] printed={pr:.3f} gap={w/n-pr:+.3f}  err={sum(u['err'] for u in us)/n:+.2f} [{elo:+.2f},{ehi:+.2f}]")
import sys
sel = {"all": lambda u: True, "57%+": lambda u: u["pp"] >= 0.57, "staked": lambda u: (u["units"] or 0) > 0}
for sname, f in sel.items():
    print(f"\n===== {sname} =====")
    us = [u for u in U if f(u)]
    for wk in sorted({u["wk"] for u in U}): row(wk, [u for u in us if u["wk"] == wk])
    s0, ci = boot_slope(us, lambda u: u["dayi"], lambda u: u["hit"]); print(f"  trend hit/day {s0:+.4f} CI [{ci[0]:+.4f},{ci[1]:+.4f}]")
    s0, ci = boot_slope(us, lambda u: u["dayi"], lambda u: u["hit"] - u["pp"]); print(f"  trend (hit-printed)/day {s0:+.4f} CI [{ci[0]:+.4f},{ci[1]:+.4f}]")
print("\n===== by day (all / staked) =====")
for d in sorted({u["d"] for u in U}):
    row(d + " all", [u for u in U if u["d"] == d]); row(d + " staked", [u for u in U if u["d"] == d and (u["units"] or 0) > 0])
for dim in ("prop", "scope", "src2", "side", "version", "tier", "book", "online"):
    print(f"\n===== week x {dim} (all lines) =====")
    for wk in sorted({u["wk"] for u in U}):
        for k in sorted({str(u[dim]) for u in U}):
            row(f"{wk} {dim}={k}", [u for u in U if u["wk"] == wk and str(u[dim]) == k])
print("\n===== week x prop x side (57%+) =====")
for wk in sorted({u["wk"] for u in U}):
    for p in ("kills", "hs"):
        for s in ("Over", "Under"):
            row(f"{wk} {p} {s}", [u for u in U if u["wk"] == wk and u["prop"] == p and u["side"] == s and u["pp"] >= 0.57])
print("\n===== calibration by week (band of printed) =====")
for wk in sorted({u["wk"] for u in U}):
    for lo, hi in ((0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 1.01)):
        row(f"{wk} printed {lo:.2f}-{hi:.2f}", [u for u in U if u["wk"] == wk and lo <= u["pp"] < hi])
