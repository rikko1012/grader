import json, math
from stats import *
d = json.load(open("p1.json")); U = d["rows"]
ch = {c["id"]: c for c in d["meta"]["challengers"]}
ll = lambda p, y: -math.log(max(1e-6, p if y else 1 - p))
for cid, c in ch.items():
    rs = [u for u in U if cid in u["challengers"] and u["logged"] > c["frozen_at"] and u["start"] > c["frozen_at"]]
    rs = [u for u in rs if u["challengers"][cid]["p"] is not None]
    if not rs: print(cid, c["status"], c["frozen_at"], "no units"); continue
    pc = lambda u: min(max(u["challengers"][cid]["p"], 1e-4), 1 - 1e-4)
    g = lambda u: ll(u["p"], u["over"]) - ll(pc(u), u["over"])
    gb = lambda u: (u["p"] - u["over"]) ** 2 - (pc(u) - u["over"]) ** 2
    n = len(rs); lo, hi = boot_ratio(rs, g); blo, bhi = boot_ratio(rs, gb)
    sel = [u for u in rs if max(pc(u), 1 - pc(u)) >= 0.57]
    sh = sum((u["over"] if pc(u) >= .5 else 1 - u["over"]) for u in sel)
    live = [u for u in rs if u["pp"] >= 0.57] if False else [u for u in rs if max(u["p"], 1 - u["p"]) >= 0.57]
    lh = sum((u["over"] if u["p"] >= .5 else 1 - u["over"]) for u in live)
    print(f"{cid:18s} {c['family']:24s} {c['status']:10s} frozen {c['frozen_at'][:16]} n={n:4d} m={len({u['match'] for u in rs}):3d} LLgain={sum(map(g, rs))/n:+.4f} [{lo:+.4f},{hi:+.4f}] Brier={sum(map(gb, rs))/n:+.4f} [{blo:+.4f},{bhi:+.4f}] | 57%+ ch {sh}/{len(sel)} live {lh}/{len(live)}")
# baseline: 50% everywhere
g = lambda u: ll(u["p"], u["over"]) - math.log(2) * -1 * -1
rs = U; n = len(rs)
g0 = lambda u: ll(u["p"], u["over"]) - math.log(2)
print("live vs coin LL gain (+ = 50% better):", sum(map(g0, rs)) / n, boot_ratio(rs, g0))
for wk in ("W1", "W2", "W3"):
    us = [u for u in rs if week(u["start"][:10]).startswith(wk)]
    print(wk, len(us), sum(map(g0, us)) / len(us), boot_ratio(us, g0))
