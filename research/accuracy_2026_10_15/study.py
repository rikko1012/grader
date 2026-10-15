"""Accuracy study (2026-10-15): the live perf-2 projection on 12 months of HLTV history with strictly pre-match inputs, and
one-component-at-a-time comparisons. Every period here is DEVELOPMENT data (inspected); matches after 2026-10-15 are kept
for forward confirmation. Folds = calendar months; anything fitted is fitted on EARLIER months only (rolling origin).
Metrics on identical rows: kills MAE, bias (projection - actual), log score of the actual total under the live
discretised normal (sd = c sqrt(lam)); HS MAE. Intervals: 95%, matches resampled."""
import collections, math, os, pickle, random, sys
import numpy as np
ACC = os.path.dirname(os.path.abspath(__file__))
R = pickle.load(open(os.path.join(ACC, "series.pkl"), "rb"))
LIVE = {"pop": 0.66442, "pm": 0.93, "k0": 300.0, "ke": 800.0, "ko": 300.0, "base_r": 21.74132969862184, "k_hs": 25.0, "pop_hs": 0.5106,
        "c": 1.457, "c_hs": 1.264}
for r in R:
    r["fold"] = r["date"][:7]
FOLDS = sorted({r["fold"] for r in R})
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def comp(r, view="n0", **o):
    """The live components for one row; o overrides (prior_m, k0, ke, ko, env, opp, base_r, k_hs, rounds)."""
    p = dict(LIVE, **{k: v for k, v in o.items() if k in LIVE})
    h = r[view]
    rate_all = (h["k"] + p["k0"] * p["pop"] * p["pm"]) / (h["r"] + p["k0"]) if p["k0"] > 0 else (h["k"] / h["r"] if h["r"] > 0 else p["pop"] * p["pm"])
    rate = rate_all if o.get("env") is False else (h["ke"] + p["ke"] * rate_all) / (h["re"] + p["ke"])
    opp = 1.0 if o.get("opp") is False else ((h["ck"] + p["ko"] * p["pop"] * 5) / (h["cr"] * 5 + p["ko"] * 5)) / p["pop"]
    rounds = o.get("rounds_fn", lambda r: p["base_r"])(r)
    hsr = (h["hk"] + p["k_hs"] * p["pop_hs"]) / (h["hkk"] + p["k_hs"]) if p["k_hs"] > 0 else (h["hk"] / h["hkk"] if h["hkk"] > 0 else p["pop_hs"])
    if o.get("hs_const"):
        hsr = p["pop_hs"]
    lam = rate * opp * rounds * 2
    return {"lam": lam, "lam_hs": lam * hsr, "rate": rate, "rate_all": rate_all, "opp": opp, "rounds": rounds * 2, "hsr": hsr}


def logscore(y, lam, c):
    sd = c * math.sqrt(max(lam, 1.0))
    pk = Phi((y + 0.5 - lam) / sd) - Phi((y - 0.5 - lam) / sd)
    z = 1 - Phi((-0.5 - lam) / sd)
    return math.log(max(pk / z, 1e-12))


def ci(rows, f, B=1000, seed=7):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["m"]].append(f(r))
    g = [(sum(v), len(v)) for v in by.values()]
    rnd = random.Random(seed); n = len(g); vals = []
    for _ in range(B):
        s = c = 0.0
        for _ in range(n):
            a, b = g[rnd.randrange(n)]; s += a; c += b
        vals.append(s / c)
    vals.sort()
    return vals[int(0.025 * B)], vals[int(0.975 * B) - 1]


if __name__ == "__main__":
    print(f"rows {len(R)} | matches {len({r['m'] for r in R})} | folds {FOLDS[0]}..{FOLDS[-1]}")
