"""LW-2 (REGISTER.md sections 2-3), CS2 P1 units, walk-forward. Run once."""
import json, math, re, datetime as dt
import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize
from evalx import *
P1 = json.load(open("p1.json")); FC = json.load(open("fc_p1.json"))
INP = {int(k): v for k, v in json.load(open("inputs_p1.json")).items()}
U = sorted(P1["rows"], key=lambda u: u["start"][:10])
p_lw1 = np.load("p_lw1.npy")
n = len(U)


def zraw(u):
    pr = FC[str(u["forecast_id"])].get("probs_raw"); q = None
    if pr:
        try: q = (json.loads(pr) if isinstance(pr, str) else pr).get("p_more_no_push")
        except Exception: q = None
    if q is None: q = u["p"]
    return float(norm.ppf(min(max(q, .001), .999)))


def num(x):
    try:
        v = float(x); return v if math.isfinite(v) else None
    except Exception: return None


def feats(u):
    f = FC[str(u["forecast_id"])]; i = INP.get(u["forecast_id"]) or {}
    perf = i.get("perf") or {}
    pw = num(f.get("p_win"))
    hr = num(perf.get("history_rounds_weighted"))
    ver = str(perf.get("version") or "")
    env = (i.get("environment") or {}).get("tonight")
    rec = i.get("recent") or {}
    rn, ro = num(rec.get("n")), num(rec.get("overs"))
    t0 = dt.datetime.fromisoformat(u["logged"]); t1 = dt.datetime.fromisoformat(u["start"])
    lead = max((t1 - t0).total_seconds() / 3600, 0)
    orank = num(i.get("opp_rank"))
    gs = str((i.get("gate_sources") or {}).get("awp") or "")
    awp = 1.0 if re.search(r":\s*awp\b", gs, re.I) else (0.0 if gs else None)
    if u["prop"] != "hs": awp = 0.0
    return [None if pw is None else pw - .5,
            None if hr is None else float(hr < 150),
            None if not i else float("bo3" in ver or not perf),
            1.0 if u.get("book") == "underdog" else 0.0,
            float(u["line"]),  # standardised within group below
            None if env is None else float(env == "lan"),
            None if not i else float(bool(i.get("is_standin")) or i.get("roster_confirmed") is False),
            (ro / rn - .5) if (rn and rn >= 5 and ro is not None) else 0.0,
            math.log1p(lead),
            None if not orank or orank <= 0 else math.log(orank),
            awp]


NAMES = ["p_win", "thin", "bo3_route", "underdog_book", "line_level", "lan", "roster_risk", "last10", "lead_time", "log_opp_rank", "awper_hs"]
F = np.array([[np.nan if v is None else v for v in feats(u)] for u in U], float)
cover = {nm: float(np.isfinite(F[:, k]).mean()) for k, nm in enumerate(NAMES)}
z = np.array([zraw(u) for u in U]); y = np.array([u["over"] for u in U], float)
day = np.array([u["start"][:10] for u in U]); stat = np.array([u["prop"] for u in U]); grp = np.array([u["prop"] + "|" + u["scope"] for u in U])
pl = np.array([u["p"] for u in U]); ul = np.array([float(u["units"] or 0) for u in U]); mm = np.array([u["match"] for u in U])
bl = (np.maximum(pl, 1 - pl) >= .55) & (ul <= 0)
pc = np.full(n, np.nan); coefs = {}
for d in sorted(set(day[day > "2026-10-13"])):
    tr = day < d; te = day == d
    # group map (>=300 group, else stat, else live)
    gmap = {}
    for g in sorted(set(grp[tr | te])):
        st = g.split("|")[0]
        if (tr & (grp == g)).sum() >= 300: gmap[g] = g
        elif (tr & (stat == st)).sum() >= 300: gmap[g] = st
        else: gmap[g] = None
    gg = np.array([gmap.get(g) for g in grp], object)
    use = tr & np.array([x is not None for x in gg])
    keys = sorted({x for x in gg[use]})
    if not keys:
        pc[te] = pl[te]; continue
    X = F.copy()
    # line level standardised by (mapped) group on training
    for k in keys:
        m = use & (gg == k); mu, sd = X[m, 4].mean(), X[m, 4].std() or 1
        X[gg == k, 4] = (X[gg == k, 4] - mu) / sd
    for j in range(X.shape[1]):
        if j == 4: continue
        col = X[use, j]; ok = np.isfinite(col)
        mu = col[ok].mean() if ok.any() else 0; sd = col[ok].std() if ok.sum() > 1 else 1
        X[:, j] = (X[:, j] - mu) / (sd if sd > 0 else 1)
    X = np.where(np.isfinite(X), X, 0.0)
    XX = np.hstack([X, X * z[:, None]])
    D = np.array([[1.0 if gg[i] == k else 0.0 for k in keys] for i in range(n)])
    def nll(t):
        a, w, b = t[:len(keys)], t[len(keys):2 * len(keys)], t[2 * len(keys):]
        eta = D[use] @ a + (D[use] @ w) * z[use] + XX[use] @ b
        p = np.clip(norm.cdf(eta), EPS, 1 - EPS)
        return -(y[use] * np.log(p) + (1 - y[use]) * np.log(1 - p)).sum() + 0.5 * 25 * (b ** 2).sum()
    t0 = np.concatenate([np.zeros(len(keys)), np.ones(len(keys)), np.zeros(XX.shape[1])])
    t = minimize(nll, t0, method="L-BFGS-B").x
    a, w, b = t[:len(keys)], t[len(keys):2 * len(keys)], t[2 * len(keys):]
    coefs[d] = dict(groups={k: (round(float(a[i]), 4), round(float(w[i]), 4)) for i, k in enumerate(keys)},
                    b={NAMES[j]: round(float(b[j]), 4) for j in range(len(NAMES))}, c={NAMES[j]: round(float(b[len(NAMES) + j]), 4) for j in range(len(NAMES))})
    for i in np.where(te)[0]:
        if gg[i] is None: pc[i] = pl[i]; continue
        ki = keys.index(gg[i])
        p = norm.cdf(a[ki] + w[ki] * z[i] + XX[i] @ b)
        pc[i] = min(max(p, .22), .78) if stat[i] == "kills" else p
S = day > "2026-10-13"
p1 = np.where(S, p_lw1, pl)
out = dict(cover=cover, inputs_found=int(sum(1 for u in U if u["forecast_id"] in INP)), coefs_last=coefs[max(coefs)])
out["vs_live"] = paired(pc[S], pl[S], y[S], mm[S]); out["vs_lw1"] = paired(pc[S], p1[S], y[S], mm[S])
out["calib"] = calib(pc[S], y[S]); out["stake"] = stake_summary(pc[S], y[S], bl[S], mm[S], "lw2")
out["hit_diff_vs_live"] = hit_diff(pc[S], pl[S], y[S], bl[S], mm[S]); out["hit_diff_vs_lw1"] = hit_diff(pc[S], p1[S], y[S], bl[S], mm[S])
np.save("p_lw2.npy", pc)
json.dump(dict(out, coefs=coefs), open("lw2_result.json", "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
