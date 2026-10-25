"""SEL-1 (REGISTER.md section 5) on LW-1's walk-forward chances. Run once."""
import json
import numpy as np
from evalx import *
P1 = json.load(open("p1.json"))
U = sorted(P1["rows"], key=lambda u: u["start"][:10])
p_all = np.load("p_lw1.npy")
day = np.array([u["start"][:10] for u in U]); y = np.array([u["over"] for u in U], float)
pl = np.array([u["p"] for u in U]); ul = np.array([float(u["units"] or 0) for u in U]); mm = np.array([u["match"] for u in U])
bl = (np.maximum(pl, 1 - pl) >= .55) & (ul <= 0)
S = day > "2026-10-13"
pc = np.where(S, p_all, pl)
cell = np.array([f"{u['prop']}|{u['scope']}|{'O' if p >= .5 else 'U'}" for u, p in zip(U, pc)])
st = staked(pc, bl) > 0
hit = np.where(pc >= .5, y, 1 - y)
allow = np.zeros(len(U), bool); log = {}
for d in sorted(set(day[S])):
    te = day == d
    for c in sorted(set(cell[te])):
        h = S & (day < d) & (cell == c) & st
        ok = h.sum() >= 40 and hit[h].mean() >= .524
        allow[te & (cell == c)] = ok
        log.setdefault(d, {})[c] = (int(h.sum()), round(float(hit[h].mean()), 3) if h.sum() else None, ok)
bl_sel = bl | ~allow
m = S
r = dict(lw1=stake_summary(pc[m], y[m], bl[m], mm[m], "lw1"), sel1=stake_summary(pc[m], y[m], bl_sel[m], mm[m], "sel1"),
         live=stake_summary(pl[m], y[m], bl[m], mm[m], "live"))
# difference SEL-1 minus LW-1 staked hit, cluster bootstrap
sa = staked(pc[m], bl_sel[m]) > 0; sb = staked(pc[m], bl[m]) > 0; hh = hit[m]
def f(i):
    a, b = sa[i], sb[i]
    if a.sum() == 0 or b.sum() == 0: return None
    return hh[i][a].mean() - hh[i][b].mean()
r["diff_sel1_minus_lw1"] = (f(np.arange(m.sum())), cluster_boot(mm[m], f, B=1000))
r["log_last"] = log[max(log)]
json.dump(r, open("sel1_result.json", "w"), indent=1, default=str)
print(json.dumps(r, indent=1, default=str))
