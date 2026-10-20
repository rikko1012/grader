"""A11 study (lol/EVALUATION.md): assists prior weight K0 in lol-1f. python -m lol.k0_study <history.db> <report.json>
K0 is chosen on F-TRAIN only; F-VALID and the spent period are each scored once under A9's rule."""
import json
import sys

import numpy as np

from . import backtest, model
from . import factor_study as FS
from .level_study import PERIODS, lol1f_mu, under_gap

GRID = (8.0, 16.0, 32.0)
STAT = "assists"


def units_with(st, stat, d_from, d_to, p):
    """factor_study.Study.units with lol-1's parameters p for the base projection."""
    d = st.data
    ts = [t for t in d.targets if d_from.toordinal() <= t["day"] < d_to.toordinal()]
    lam = backtest.lam_game(backtest.features(d, ts, p["hl"]), ts, stat, p, dict(model.PRIORS))
    j = {"kills": 0, "deaths": 1, "assists": 2}[stat]
    out = []
    for i, t in enumerate(ts):
        for scope, (n, bo) in FS.SCOPES.items():
            if (bo and t.get("best_of") != bo) or not all(g in t["games"] for g in range(1, n + 1)):
                continue
            out.append((t, scope, sum(t["games"][g][j] for g in range(1, n + 1)), float(lam[i]) * n, model.R[(stat, scope)]))
    return out


def run(db_path, log=print):
    st = FS.Study(db_path)
    fit = json.loads(FS.FIT_FILE.read_text(encoding="utf-8"))
    p0 = dict(model.PARAMS[STAT])
    rep = {"amendment": "A11", "grid": GRID, "k0_lol1": p0["k0"], "periods": {}}

    def scored(name, k0):
        a, b = PERIODS[name]
        us = units_with(st, STAT, a, b, dict(p0, k0=k0))
        return us, lol1f_mu(st, fit, STAT, us)

    base = {name: scored(name, p0["k0"]) for name in PERIODS}
    us, mu0 = base["F-TRAIN"]
    y, r = np.array([u[2] for u in us], float), np.array([u[4] for u in us])
    best = (float(np.mean(FS.nb_ll(y, mu0, r))), p0["k0"])
    log(f"F-TRAIN K0={p0['k0']} (lol-1): {best[0]:.5f}")
    for k0 in GRID:
        _, mu = scored("F-TRAIN", k0)
        ll = float(np.mean(FS.nb_ll(y, mu, r)))
        log(f"F-TRAIN K0={k0}: {ll:.5f}")
        if ll > best[0]:
            best = (ll, k0)
    k0 = best[1]
    rep["chosen_k0"] = k0
    log(f"chosen K0={k0}")
    for name in PERIODS:
        us, mu0 = base[name]
        _, mu1 = scored(name, k0)
        y, r = np.array([u[2] for u in us], float), np.array([u[4] for u in us])
        res = {}
        for sc in FS.SCOPES:
            m = np.array([u[1] == sc for u in us])
            g = [u[0]["match"] for u, mm in zip(us, m) if mm]
            ls = backtest.grouped_boot(FS.nb_ll(y[m], mu1[m], r[m]) - FS.nb_ll(y[m], mu0[m], r[m]), g, n=2000)
            res[sc] = {"units": int(m.sum()), "series": len(set(g)), "logscore_gain": {"mean": ls[0], "lo95": ls[1], "hi95": ls[2]},
                       "mae_lol1f": float(np.mean(np.abs(mu0[m] - y[m]))), "mae_k0a": float(np.mean(np.abs(mu1[m] - y[m]))),
                       "bias_lol1f": float(np.mean(mu0[m] - y[m])), "bias_k0a": float(np.mean(mu1[m] - y[m]))}
        if name != "F-TRAIN":
            g0, g1 = under_gap(st, us, mu0, STAT), under_gap(st, us, mu1, STAT)
            res["under_gap"] = {"lol1f": {"printed": g0[0], "won": g0[1], "n": g0[2]}, "k0a": {"printed": g1[0], "won": g1[1], "n": g1[2]}}
        ug = res.get("under_gap")
        res["passes"] = bool(k0 != p0["k0"] and res["12"]["logscore_gain"]["lo95"] > 0 and res["123"]["logscore_gain"]["mean"] >= 0
                             and res["12"]["mae_k0a"] <= res["12"]["mae_lol1f"] and res["1"]["logscore_gain"]["mean"] >= 0
                             and (ug is None or abs(ug["k0a"]["printed"] - ug["k0a"]["won"]) < abs(ug["lol1f"]["printed"] - ug["lol1f"]["won"])))
        rep["periods"][name] = res
        for sc in FS.SCOPES:
            v = res[sc]
            log(f"  {name:8} scope {sc:3} n={v['units']:6d} s={v['series']:5d} LS {v['logscore_gain']['mean']:+.4f} "
                f"[{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}] MAE {v['mae_lol1f']:.3f}->{v['mae_k0a']:.3f} "
                f"bias {v['bias_lol1f']:+.3f}->{v['bias_k0a']:+.3f}")
        if ug:
            log(f"  {name} Under gap: lol-1f printed {ug['lol1f']['printed']:.3f} won {ug['lol1f']['won']:.3f} (n {ug['lol1f']['n']}) "
                f"-> k0-a printed {ug['k0a']['printed']:.3f} won {ug['k0a']['won']:.3f} (n {ug['k0a']['n']})")
        log(f"  {name}: A11 rule {'PASS' if res['passes'] else 'fail'}")
    rep["decision"] = "pass" if rep["periods"]["F-VALID"]["passes"] and rep["periods"]["SPENT"]["passes"] else "fail"
    log(f"A11 decision {rep['decision']}")
    return rep


if __name__ == "__main__":
    rep = run(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=float)
