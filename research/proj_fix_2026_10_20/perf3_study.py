"""perf-3 study (PROTOCOL.md): fit (alpha, beta, gamma) on FIT, score HOLD-OUT once. Read-only on the research records."""
import json, math, pickle, random, sys, statistics as st
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.optimize import minimize
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import perf
P = perf.PARAMS
SRC = Path(sys.argv[1])
FIT, HOLD = ("2025-12-01", "2026-07-01"), ("2026-07-01", "2026-10-17")

def lam2(h, lan, maps):
    ra = (h["k"] + P["k0"] * P["pop"] * P["prior_m"]) / (h["r"] + P["k0"])
    rate = (h["ke"] + P["ke"] * ra) / (h["re"] + P["ke"]) if lan is not None else ra
    opp = ((h["ck"] + P["ko"] * P["pop"] * 5) / (h["cr"] * 5 + P["ko"] * 5)) / P["pop"]
    return rate * opp * P["base_r"] * maps

def hsr(h):
    return (h["hk"] + P["k_hs"] * P["pop_hs"]) / (h["hkk"] + P["k_hs"])

def p_elo(e):
    return 1 / (1 + 10 ** (-(e[0] - e[1]) / 400))

R = pickle.load(open(SRC, "rb"))
X = [dict(r, l1=lam2(r["n0"], r["lan"], 1), q=p_elo(r["elo"]) - 0.5, hr=hsr(r["n0"])) for r in R
     if r.get("n0") and r.get("y") is not None and r.get("elo")]
fit = [x for x in X if FIT[0] <= x["date"] < FIT[1]]
hold = [x for x in X if HOLD[0] <= x["date"] < HOLD[1]]

def mu(th, x, maps):
    a, b, g = th
    return math.exp(a + b * math.log(x["l1"]) + g * x["q"]) * maps

def nll(th):
    a, b, g = th
    l1 = np.array([x["l1"] for x in fit]); q = np.array([x["q"] for x in fit]); y = np.array([x["y"] for x in fit], float)
    m = 2 * np.exp(a + b * np.log(l1) + g * q)
    gr_eta = y - m
    return -(y * np.log(m) - m).sum(), -np.array([gr_eta.sum(), (gr_eta * np.log(l1)).sum(), (gr_eta * q).sum()])

th = minimize(nll, np.array([0.0, 1.0, 0.0]), jac=True, method="BFGS").x

def boot(diff, groups, B=2000):
    g = defaultdict(list)
    for d, k in zip(diff, groups): g[k].append(d)
    ks = list(g); rnd = random.Random(7); v = []
    for _ in range(B):
        s = [d for k in (rnd.choice(ks) for _ in ks) for d in g[k]]
        v.append(sum(s) / len(s))
    v.sort(); return [round(v[int(.025 * B)], 4), round(v[int(.975 * B) - 1], 4)]

out = {"theta": {"alpha": th[0], "beta": th[1], "gamma": th[2]}, "fit_n": len(fit), "hold_n": len(hold),
       "hold_matches": len({x["m"] for x in hold})}
for name, maps, yk, hs in (("kills Maps 1-2", 2, "y", False), ("kills Map 1", 1, "y1", False), ("hs Maps 1-2", 2, "y_hs", True)):
    hs_ = [x for x in hold if x.get(yk) is not None]
    p2 = [x["l1"] * maps * (x["hr"] if hs else 1) for x in hs_]
    p3 = [mu(th, x, maps) * (x["hr"] if hs else 1) for x in hs_]
    y = [x[yk] for x in hs_]
    d = [abs(a - t) - abs(b - t) for a, b, t in zip(p2, p3, y)]
    out[name] = {"n": len(y), "mae_perf2": round(st.mean(abs(a - t) for a, t in zip(p2, y)), 4),
                 "mae_perf3": round(st.mean(abs(b - t) for b, t in zip(p3, y)), 4),
                 "mae_gain": round(st.mean(d), 4), "mae_gain_ci": boot(d, [x["m"] for x in hs_]),
                 "bias_perf2": round(st.mean(a - t for a, t in zip(p2, y)), 3), "bias_perf3": round(st.mean(b - t for b, t in zip(p3, y)), 3)}
print(json.dumps(out, indent=1))
json.dump(out, open(Path(__file__).with_name("perf3_result.json"), "w"), indent=1)
