"""Final LW-1 fit for release (all P1 units, registered fallback rules) + a frozen-weight reading (fit <= Oct 17, score Oct 18-25)."""
import json, hashlib, os
import numpy as np
from scipy.stats import norm
from evalx import *
P1 = json.load(open("p1.json")); FC = json.load(open("fc_p1.json"))
C = json.load(open("corrections_2399658.json"))
U = [dict(u, over=C[str(u["forecast_id"])]["over_hltv"]) if str(u["forecast_id"]) in C else u for u in P1["rows"]]
U = [u for u in U if u["over"] is not None]
U.sort(key=lambda u: u["start"][:10])
def zraw(u):
    pr = FC[str(u["forecast_id"])].get("probs_raw"); q = None
    if pr:
        q = (json.loads(pr) if isinstance(pr, str) else pr).get("p_more_no_push")
    if q is None: q = u["p"]
    return float(norm.ppf(min(max(q, .001), .999)))
z = np.array([zraw(u) for u in U]); y = np.array([u["over"] for u in U], float)
day = np.array([u["start"][:10] for u in U]); stat = np.array([u["prop"] for u in U]); grp = np.array([u["prop"] + "|" + u["scope"] for u in U])
pl = np.array([u["p"] for u in U]); ul = np.array([float(u["units"] or 0) for u in U]); mm = np.array([u["match"] for u in U])
bl = (np.maximum(pl, 1 - pl) >= .55) & (ul <= 0)
def fit_all(mask):
    out = {}
    for st in ("kills", "hs"):
        ms = mask & (stat == st)
        pooled = tuple(float(x) for x in fit_probit(z[ms], y[ms])) if ms.sum() >= 300 else None
        for sc in ("1", "12", "123"):
            g = f"{st}|{sc}"; m = mask & (grp == g)
            if m.sum() >= 300: out[g] = dict(a=float(fit_probit(z[m], y[m])[0]), w=float(fit_probit(z[m], y[m])[1]), n=int(m.sum()), basis="group")
            elif pooled: out[g] = dict(a=pooled[0], w=pooled[1], n=int(ms.sum()), basis="stat")
    return out
final = fit_all(np.ones(len(U), bool))
data_hash = hashlib.sha256(json.dumps([(u["forecast_id"], u["over"]) for u in U]).encode()).hexdigest()[:12]
print("FINAL", json.dumps(final, indent=1), "units", len(U), "data hash", data_hash, "days", min(day), max(day))
json.dump(dict(params=final, units=len(U), data_hash=data_hash, first_day=min(day), last_day=max(day), at=P1["meta"]["at"]), open("lw1_final.json", "w"), indent=1)
# frozen reading
fz = fit_all(day <= "2026-10-17")
S = day >= "2026-10-18"
pc = pl.copy()
for i in np.where(S)[0]:
    g = fz.get(grp[i])
    if g:
        p = norm.cdf(g["a"] + g["w"] * z[i]); pc[i] = min(max(p, .22), .78) if stat[i] == "kills" else p
print("frozen<=10-17 params", {k: (round(v["a"], 4), round(v["w"], 4)) for k, v in fz.items()})
print("frozen paired", paired(pc[S], pl[S], y[S], mm[S]))
print("frozen stake live", stake_summary(pl[S], y[S], bl[S], mm[S], "live"))
print("frozen stake lw1", stake_summary(pc[S], y[S], bl[S], mm[S], "lw1"))
print("frozen hit diff", hit_diff(pc[S], pl[S], y[S], bl[S], mm[S]))
print("frozen calib gap55 live/lw", calib(pl[S], y[S])["gap55"], calib(pc[S], y[S])["gap55"])
# what the final params would stake on the whole period (in-sample, reading only)
pf = pl.copy()
for i in range(len(U)):
    g = final.get(grp[i])
    if g:
        p = norm.cdf(g["a"] + g["w"] * z[i]); pf[i] = min(max(p, .22), .78) if stat[i] == "kills" else p
print("in-sample final staked", stake_summary(pf, y, bl, mm, "final"), "live", stake_summary(pl, y, bl, mm, "live"))
