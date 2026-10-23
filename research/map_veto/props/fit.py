"""Fit the vm-1 match-script constants (REGISTER.md section 1.2 / 2) by maximum likelihood of per-map final scores.

usage: python fit.py dev     -> <scratch>/veto/vprops/params_dev.json    (maps 2025-07-01 .. 2026-06-30)
       python fit.py frozen  -> research/map_veto/props/vm1_params.json  (maps 2025-10-22 .. 2026-10-21; same procedure)
"""
import gzip, json, math, os, sys
import numpy as np
from scipy.optimize import minimize, minimize_scalar
from scipy.special import betaln, expit, logit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vm1  # noqa: E402

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
OUT = os.path.join(SCR, "veto", "vprops")
WINDOWS = {"dev": ("2025-07-01", "2026-06-30"), "frozen": ("2025-10-22", "2026-10-21")}
K_SHRINK = 2000.0
OT_TOL = 0.010
F6 = os.path.join(SCR, "cs2acc", "cands", "params_F6.json")
LOGN = {(w, l): ln for w, l, ln, _ in vm1.SCORES}
OT_SCORES = [(w, l, ln) for w, l, ln, ot in vm1.SCORES if ot]


def load(lo, hi):
    with gzip.open(os.path.join(OUT, "maps.json.gz"), "rt", encoding="utf-8") as f:
        d = json.load(f)
    rows = [r for r in d["rows"] if lo <= r[1] <= hi]
    a = np.array([r[5] for r in rows], float); b = np.array([r[6] for r in rows], float)
    ea = np.array([r[7] for r in rows], float); ds = np.array([r[8] for r in rows], float)
    mp = np.array([r[2] for r in rows])
    ln = np.array([LOGN[(int(max(x, y)), int(min(x, y)))] for x, y in zip(a, b)])
    return {"a": a, "b": b, "x": logit(np.clip(ea, 1e-6, 1 - 1e-6)), "d": ds, "map": mp, "logN": ln,
            "mid": np.array([r[0] for r in rows]), "excluded": d["excluded"]}


def ll_rows(D, c0, c1, kap):
    mu = expit(c0 * D["x"] + c1 * D["d"])
    al, be = mu * kap, (1 - mu) * kap
    return D["logN"] + betaln(al + D["a"], be + D["b"]) - betaln(al, be)


def ot_prob(mu, kap):
    al, be = mu * kap, (1 - mu) * kap
    lb = betaln(al, be)
    q = np.zeros_like(mu)
    for w, l, ln in OT_SCORES:
        q += np.exp(ln + betaln(al + w, be + l) - lb) + np.exp(ln + betaln(al + l, be + w) - lb)
    return q


def fit(which):
    lo, hi = WINDOWS[which]
    D = load(lo, hi)
    n = len(D["a"])
    f = lambda v: -ll_rows(D, v[0], v[1], math.exp(v[2])).sum()
    r = minimize(f, np.array([1.0, 1.0, math.log(30.0)]), method="Nelder-Mead",
                 options={"xatol": 1e-6, "fatol": 1e-4, "maxiter": 2000})
    c0, c1, kap = float(r.x[0]), float(r.x[1]), float(math.exp(r.x[2]))
    kmap, nmap, kraw = {}, {}, {}
    for m in sorted(set(D["map"])):
        sel = D["map"] == m
        Dm = {k: (v[sel] if isinstance(v, np.ndarray) else v) for k, v in D.items()}
        rm_ = minimize_scalar(lambda lk: -ll_rows(Dm, c0, c1, math.exp(lk)).sum(), bounds=(math.log(2), math.log(2000)),
                              method="bounded")
        nm = int(sel.sum())
        kraw[m] = float(math.exp(rm_.x))
        kmap[m] = float(math.exp((nm * rm_.x + K_SHRINK * math.log(kap)) / (nm + K_SHRINK)))
        nmap[m] = nm
    kk = np.array([kmap[m] for m in D["map"]])
    mu = expit(c0 * D["x"] + c1 * D["d"])
    q = ot_prob(mu, kk)
    is_ot = (D["a"] + D["b"]) > 24
    obs_ot, mod_ot = float(is_ot.mean()), float(q.mean())
    tau = 1.0
    if abs(obs_ot - mod_ot) > OT_TOL:
        llb = D["logN"] + betaln(mu * kk + D["a"], (1 - mu) * kk + D["b"]) - betaln(mu * kk, (1 - mu) * kk)

        def g(t):
            q2 = np.minimum(0.5, t * q)
            return -(llb + np.where(is_ot, np.log(q2 / q), np.log((1 - q2) / (1 - q)))).sum()
        tau = float(minimize_scalar(g, bounds=(0.3, 3.0), method="bounded").x)
    P = {"c0": c0, "c1": c1, "kappa": kap, "kappa_map": kmap, "tau": tau, "rbar": float((D["a"] + D["b"]).mean()),
         "rho": {k: v["rho"] for k, v in json.load(open(F6))["rho"].items()},
         "meta": {"window": [lo, hi], "n_maps": n, "n_matches": int(len(set(D["mid"]))), "per_map_n": nmap,
                  "kappa_map_raw": kraw, "k_shrink": K_SHRINK, "nll_per_map": float(r.fun / n),
                  "ot_observed": obs_ot, "ot_model_before_tau": mod_ot, "ot_tol": OT_TOL,
                  "excluded_rows_all_windows": D["excluded"], "rho_source": "rm-1 params_F6.json (fit before 2026-10-01)",
                  "procedure": "REGISTER.md section 1.2: ML (c0, c1, log kappa) Nelder-Mead; per-map log kappa ML with "
                               "(c0, c1) fixed, shrunk with weight 2000 maps; tau only if |OT model - observed| > 1.0 pt"}}
    return P


if __name__ == "__main__":
    which = sys.argv[1]
    P = fit(which)
    path = os.path.join(OUT, "params_dev.json") if which == "dev" else os.path.join(HERE, "vm1_params.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(P, f, indent=1, sort_keys=True)
    print(path, vm1.version(P), json.dumps({k: P[k] for k in ("c0", "c1", "kappa", "tau", "rbar")}),
          json.dumps({k: P["meta"][k] for k in ("n_maps", "ot_observed", "ot_model_before_tau", "nll_per_map")}))
    print({m: round(v, 1) for m, v in P["kappa_map"].items()}, P["meta"]["per_map_n"])
