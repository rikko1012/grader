"""cal-cs2lol-1 diagnosis (descriptive only; REGISTER.md: it cannot change the candidates, selection or pass rule).

Where does the overstatement come from? For every slice: stated (favoured-side chance) vs hit, with a match-clustered
interval of (hit - stated). Then three separate mechanisms, from projection-vs-actual residuals:
  bias    - the mean projection is off:            mean(projection - actual)
  gap     - the projection's distance from the line is overstated: OLS slope beta of (actual - line) on (projection -
            line); beta = 1 means the gap is real, beta = 0 means the line already knew everything the projection adds
  spread  - the model's sd is too narrow: SD of (actual - projection) / model sd   (1 = right)
and in probability space an in-sample probit recalibration p' = Phi(a + b z): a = shift, b = how much of the stated
confidence is real (b < 1 = overconfident).

usage: python diagnose.py <data_dir> <out_json>
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent))
import calib as C  # noqa: E402

data, out = sys.argv[1], Path(sys.argv[2])
SPREAD_C = {("kills", "12"): 1.457, ("kills", "1"): 1.442, ("kills", "123"): 1.457,     # perf.SPREAD (nominal live spread)
            ("hs", "12"): 1.264, ("hs", "1"): 1.241, ("hs", "123"): 1.264}


def model_sd(u):
    if u["sport"] == "cs2":
        return SPREAD_C.get((u["prop"], u["scope"]), 1.4) * math.sqrt(max(u["lam"] or 1.0, 1.0))
    lam, k = u["lam"], u.get("nb_size") or 1e9                                       # LoL: negative binomial
    return math.sqrt(lam + lam * lam / k)


def gap(units, key="p"):
    if not units:
        return {"n": 0}
    n = len(units)
    st = sum(C.fav(u[key]) for u in units) / n
    h = sum(C.fav_hit(u[key], u["y"]) for u in units) / n
    return {"n": n, "matches": len({u["cluster"] for u in units}), "stated": round(st, 4), "hit": round(h, 4),
            "over_by": round(st - h, 4), "hit_minus_stated_ci": C.boot(units, lambda u: C.fav_hit(u[key], u["y"]) - C.fav(u[key]))}


def mechanisms(units):
    us = [u for u in units if u["lam"] is not None and u["actual"] is not None and u["line"] is not None]
    if len(us) < 30:
        return {"n": len(us)}
    lam = np.array([u["lam"] for u in us], float)
    act = np.array([u["actual"] for u in us], float)
    line = np.array([u["line"] for u in us], float)
    sd = np.array([model_sd(u) for u in us], float)
    x, yv = lam - line, act - line
    X = np.c_[np.ones_like(x), x]
    a_, b_ = np.linalg.lstsq(X, yv, rcond=None)[0]
    # match-clustered interval for the slope
    cl = defaultdict(list)
    for i, u in enumerate(us):
        cl[u["cluster"]].append(i)
    keys = list(cl)
    rng = np.random.default_rng(C.SEED)
    bs = []
    for _ in range(1000):
        idx = np.concatenate([cl[keys[j]] for j in rng.integers(0, len(keys), len(keys))])
        Xi = X[idx]
        bs.append(np.linalg.lstsq(Xi, yv[idx], rcond=None)[0][1])
    p = np.array([u["p"] for u in us])
    y = np.array([u["y"] for u in us], float)
    z = norm.ppf(np.clip(p, 0.001, 0.999))
    f = lambda t: C._ll(norm.cdf(t[0] + t[1] * z), y).mean()
    ab = minimize(f, [0.0, 1.0], method="Nelder-Mead").x
    t = (act - lam) / sd
    return {"n": len(us), "matches": len(keys),
            "bias_proj_minus_actual": round(float((lam - act).mean()), 3),
            "bias_ci": C.boot(us, lambda u: u["lam"] - u["actual"]),
            "mean_actual_minus_line": round(float(yv.mean()), 3), "over_rate": round(float(y.mean()), 4),
            "mean_proj_minus_line": round(float(x.mean()), 3), "mean_abs_proj_minus_line": round(float(np.abs(x).mean()), 3),
            "gap_slope_beta": round(float(b_), 3), "gap_slope_ci": [round(float(np.percentile(bs, 2.5)), 3), round(float(np.percentile(bs, 97.5)), 3)],
            "gap_intercept": round(float(a_), 3),
            "mae_projection": round(float(np.abs(lam - act).mean()), 3), "mae_line": round(float(np.abs(line - act).mean()), 3),
            "resid_sd_over_model_sd": round(float(t.std()), 3), "resid_mean_in_sds": round(float(t.mean()), 3),
            "probit_shift_a": round(float(ab[0]), 3), "probit_slope_b": round(float(ab[1]), 3)}


def band(p):
    f = C.fav(p)
    for lo, name in ((0.68, "A- and up (68%+)"), (0.63, "B+ (63-68%)"), (0.57, "B (57-63%)"), (0.55, "B- (55-57%)")):
        if f >= lo:
            return name
    return "under 55%"


def dist(u):
    g = abs((u["lam"] or 0) - u["line"])
    return "0-1" if g < 1 else "1-2" if g < 2 else "2-3" if g < 3 else "3-5" if g < 5 else "5+"


res = {}
for sport, loader in (("cs2", C.load_cs2), ("lol", C.load_lol)):
    units = loader(data)
    R = res[sport] = {"units": len(units)}
    if sport == "cs2":
        cohort = [u for u in units if u["scope"] != "1" and C.fav(u["p"]) >= 0.57]
    else:
        for u in units:                                   # live favoured side read from shown() (tie rule included)
            pass
        cohort = [u for u in units if u["live_offered"] == 1]
    R["results_cohort"] = gap(cohort) if sport == "cs2" else {
        "n": len(cohort), "stated_printed": round(sum(u["live_printed"] for u in cohort) / len(cohort), 4),
        "hit": round(sum((u["y"] if u["live_side"] == "over" else 1 - u["y"]) for u in cohort) / len(cohort), 4)}
    slicers = {
        "prop": lambda u: u["prop"], "scope": lambda u: u["scope"], "prop x scope": lambda u: f"{u['prop']} {u['scope']}",
        "favoured side": lambda u: C.side_of(u["p"]), "prop x side": lambda u: f"{u['prop']} {C.side_of(u['p'])}",
        "letter band": lambda u: band(u["p"]), "model tag": lambda u: u["version"],
        "projection-line distance": dist, "book": lambda u: u["book"],
        "week": lambda u: "Oct 11-17" if u["day"] <= "2026-10-17" else "Oct 18-24" if u["day"] <= "2026-10-24" else "Oct 25-31" if u["day"] <= "2026-10-31" else "Nov 1-4",
    }
    if sport == "cs2":
        slicers["projection source"] = lambda u: u.get("source") or "not in forecast record"
        slicers["odds source"] = lambda u: u.get("odds_source") or "none"
        slicers["raw vs stated"] = lambda u: ("no raw saved" if u.get("raw") is None else
                                              "stated more confident than raw" if C.fav(u["p"]) > C.fav(u["raw"]) + 0.005 else
                                              "stated less confident than raw" if C.fav(u["p"]) < C.fav(u["raw"]) - 0.005 else "stated = raw")
    else:
        slicers["odds type"] = lambda u: u["odds_type"]
        slicers["series format"] = lambda u: f"BO{u['best_of']}"
        slicers["league"] = lambda u: u.get("league") or "unknown"
        slicers["role"] = lambda u: u.get("role") or "unknown"
    R["slices_all_units"] = {}
    R["slices_results_cohort"] = {}
    for name, fn in slicers.items():
        for tgt, pop in (("slices_all_units", units), ("slices_results_cohort", cohort)):
            g = defaultdict(list)
            for u in pop:
                g[fn(u)].append(u)
            R[tgt][name] = {k: gap(v) for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])) if len(v) >= 10}
    R["mechanisms"] = {"all": mechanisms(units)}
    for name in ("prop", "prop x scope", "prop x side", "letter band", "week"):
        g = defaultdict(list)
        for u in units:
            g[slicers[name](u)].append(u)
        R["mechanisms"][name] = {k: mechanisms(v) for k, v in sorted(g.items()) if len(v) >= 30}
    if sport == "cs2":
        rw = [u for u in units if u.get("raw") is not None]
        if rw:
            R["raw_vs_stated"] = {"n": len(rw), "stated": gap(rw, "p"), "raw": gap(rw, "raw"),
                                  "logloss_stated": round(sum(C.ll1(u["p"], u["y"]) for u in rw) / len(rw), 5),
                                  "logloss_raw": round(sum(C.ll1(u["raw"], u["y"]) for u in rw) / len(rw), 5),
                                  "mean_abs_diff": round(sum(abs(u["p"] - u["raw"]) for u in rw) / len(rw), 4)}
out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")

for sport, R in res.items():
    print(f"\n======== {sport.upper()} ({R['units']} units)  results cohort {R['results_cohort']}")
    for k, m in R["mechanisms"].items():
        if k == "all":
            print(" ALL", m)
            continue
        for kk, mm in m.items():
            print(f" {k:14s} {kk:22s} n {mm['n']:5d} bias {mm.get('bias_proj_minus_actual')} {mm.get('bias_ci')} slope {mm.get('gap_slope_beta')} {mm.get('gap_slope_ci')}"
                  f" sd-ratio {mm.get('resid_sd_over_model_sd')} probit a {mm.get('probit_shift_a')} b {mm.get('probit_slope_b')} MAE proj/line {mm.get('mae_projection')}/{mm.get('mae_line')} over {mm.get('over_rate')}")
    for tgt in ("slices_all_units", "slices_results_cohort"):
        print(" --", tgt)
        for name, g in R[tgt].items():
            print("   ", name, " | ".join(f"{k}: {v['n']} {v['stated']:.3f}/{v['hit']:.3f}" for k, v in list(g.items())[:9]))
    if "raw_vs_stated" in R:
        print(" raw vs stated", R["raw_vs_stated"])
