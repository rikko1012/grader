"""LW-LoL (REGISTER.md section 2). Run once."""
import json
import numpy as np
from scipy.stats import norm
from evalx import *
R = json.load(open("lol_p1.json"))
best = {}
for r in R:
    if r["st"] != "graded": continue
    k = (r["sid"], r["pid"], r["stat"], r["scope"])
    if k not in best or r["id"] > best[k]["id"]: best[k] = r
D = sorted(best.values(), key=lambda r: r["series_start"][:10])
p = np.array([r["p_over"] / max(r["p_over"] + r["p_under"], 1e-9) for r in D])
y = np.array([float(r["over_hit"]) for r in D]); z = norm.ppf(np.clip(p, .001, .999))
day = np.array([r["series_start"][:10] for r in D]); mm = np.array([str(r["sid"]) for r in D])
stat = np.array([r["stat"] for r in D]); grp = np.array([f"{r['stat']}|{r['scope']}" for r in D])
ver = np.array([r["model_version"] for r in D])
days = sorted(set(day)); burn = days[:2]; score = days[2:]
pc = np.full(len(D), np.nan); params = {}
for d in score:
    tr, te = day < d, day == d; params[d] = {}
    for g in sorted(set(grp[te])):
        st = g.split("|")[0]; m = tr & (grp == g); how = "group"
        if m.sum() < 150: m = tr & (stat == st); how = "stat"
        idx = te & (grp == g)
        if m.sum() < 150: pc[idx] = p[idx]; params[d][g] = ("live", int(m.sum())); continue
        a, w = fit_probit(z[m], y[m]); params[d][g] = (how, int(m.sum()), round(float(a), 4), round(float(w), 4))
        pc[idx] = norm.cdf(a + w * z[idx])
S = day >= score[0]
bl = np.zeros(S.sum(), bool)
out = dict(units=len(D), series=len(set(mm)), burn=burn, score=score, params=params)
out["paired"] = paired(pc[S], p[S], y[S], mm[S]); out["vs50"] = paired(pc[S], np.full(S.sum(), .5), y[S], mm[S])
out["live_vs50"] = paired(p[S], np.full(S.sum(), .5), y[S], mm[S])
out["calib_live"] = calib(p[S], y[S]); out["calib_lw"] = calib(pc[S], y[S])
out["set55_live"] = stake_summary(p[S], y[S], bl, mm[S], "live"); out["set55_lw"] = stake_summary(pc[S], y[S], bl, mm[S], "lw")
for st in ("kills", "assists"):
    m = S & (stat == st)
    if m.sum() > 20:
        out[st] = dict(paired=paired(pc[m], p[m], y[m], mm[m], B=1000), live=stake_summary(p[m], y[m], np.zeros(m.sum(), bool), mm[m], "live"),
                       lw=stake_summary(pc[m], y[m], np.zeros(m.sum(), bool), mm[m], "lw"), calib_live=calib(p[m], y[m]), calib_lw=calib(pc[m], y[m]))
json.dump(out, open("lwlol_result.json", "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str)[:9000])
