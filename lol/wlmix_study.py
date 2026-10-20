"""A10 study (lol/EVALUATION.md): wl-mix over lol-1f. python -m lol.wlmix_study <history.db> <report.json>
The size s per (stat, scope) is fitted on F-TRAIN only; F-VALID and the spent period are each scored once under A9's rule."""
import json
import math
import sys
from datetime import date

import numpy as np
from scipy.special import gammaln, logsumexp
from scipy.stats import nbinom

from . import backtest, evaluate, model, wlmix
from . import factor_study as FS
from .level_study import PERIODS, lol1f_mu, prev_totals

SIZES = np.exp(np.linspace(math.log(0.5), math.log(200.0), 60))


def comp_arrays(mu, n, p, R):
    """(log weights [N, n+1], means [N, n+1])."""
    p = np.clip(p, 1e-6, 1 - 1e-6)
    den = p * R + 1 - p
    mw, ml = (mu / n) * R / den, (mu / n) / den
    ks = np.arange(n + 1)
    lw = np.array([gammaln(n + 1) - gammaln(k + 1) - gammaln(n - k + 1) for k in ks])[None, :] \
        + ks[None, :] * np.log(p)[:, None] + (n - ks)[None, :] * np.log(1 - p)[:, None]
    return lw, ks[None, :] * mw[:, None] + (n - ks)[None, :] * ml[:, None]


def mix_ll(y, mu, n, p, R, s):
    lw, m = comp_arrays(mu, n, p, R)
    return logsumexp(lw + FS.nb_ll(y[:, None], m, s), axis=1)


def mix_probs(mu, n, p, R, s, line):
    lw, m = comp_arrays(np.array([mu]), n, np.array([p]), R)
    w, m = np.exp(lw[0]), m[0]
    pr = s / (s + m)
    lo = np.floor(line) if line != int(line) else line - 1          # under = y <= lo
    under = float(np.sum(w * nbinom.cdf(lo, s, pr)))
    if line == int(line):
        push = float(np.sum(w * nbinom.pmf(line, s, pr)))
        over = 1 - under - push
    else:
        push, over = 0.0, 1 - under
    return over, under, push


def under_gap(st, us, chance):
    p_sum = w = n = 0
    for u, pu_fn in zip(us, chance):
        t, sc, y, _, r = u
        line = evaluate.dev_line(prev_totals(st, t["player"], t["day"], FS.SCOPES[sc][0], "assists" if pu_fn[1] == "assists" else "kills"))
        if line is None or y == line:
            continue
        po, pu, _ = pu_fn[0](line)
        p = pu / (po + pu)
        if p < 0.55:
            continue
        p_sum += p
        w += y < line
        n += 1
    return (p_sum / n if n else None, w / n if n else None, n)


def run(db_path, log=print):
    st = FS.Study(db_path)
    fit = json.loads(FS.FIT_FILE.read_text(encoding="utf-8"))
    rep = {"amendment": "A10", "stats": {}}
    for stat in ("assists", "kills"):
        R = model.PRIORS["win_ratio"][stat]
        per = {}
        for name, (a, b) in PERIODS.items():
            us = st.units(stat, a, b)
            per[name] = (us, lol1f_mu(st, fit, stat, us), np.array([u[2] for u in us], float), np.array([u[4] for u in us]),
                         np.array([u[0]["p_win"] if u[0].get("p_win") is not None else 0.5 for u in us], float))
        sizes = {}
        us, mu, y, r, p = per["F-TRAIN"]
        for sc, (n, _) in FS.SCOPES.items():
            m = np.array([u[1] == sc for u in us])
            best = max(SIZES, key=lambda s: float(np.mean(mix_ll(y[m], mu[m], n, p[m], R, s))))
            sizes[sc] = float(best)
            log(f"{stat} scope {sc}: size {best:.3f} (lol-1f NB size {model.R[(stat, sc)]})")
        out = {"R": R, "sizes": sizes, "periods": {}}
        for name in PERIODS:
            us, mu, y, r, p = per[name]
            res = {}
            for sc, (n, _) in FS.SCOPES.items():
                m = np.array([u[1] == sc for u in us])
                g = [u[0]["match"] for u, mm in zip(us, m) if mm]
                d = mix_ll(y[m], mu[m], n, p[m], R, sizes[sc]) - FS.nb_ll(y[m], mu[m], r[m])
                ls = backtest.grouped_boot(d, g, n=2000)
                res[sc] = {"units": int(m.sum()), "series": len(set(g)), "logscore_gain": {"mean": ls[0], "lo95": ls[1], "hi95": ls[2]}}
            if name != "F-TRAIN":
                base = [((lambda line, mm=float(m_), rr=float(r_): model.probs(mm, rr, line)), stat) for m_, r_ in zip(mu, r)]
                cand = [((lambda line, mm=float(m_), nn=FS.SCOPES[u[1]][0], pp=float(p_), ss=sizes[u[1]]: mix_probs(mm, nn, pp, R, ss, line)), stat)
                        for u, m_, p_ in zip(us, mu, p)]
                g0, g1 = under_gap(st, us, base), under_gap(st, us, cand)
                res["under_gap"] = {"lol1f": {"printed": g0[0], "won": g0[1], "n": g0[2]}, "wlmix": {"printed": g1[0], "won": g1[1], "n": g1[2]}}
            ug = res.get("under_gap")
            # MAE is identical by construction (the mixture keeps lol-1f's mean): A7's MAE condition holds with equality
            res["passes"] = bool(res["12"]["logscore_gain"]["lo95"] > 0 and res["123"]["logscore_gain"]["mean"] >= 0
                                 and res["1"]["logscore_gain"]["mean"] >= 0
                                 and (ug is None or abs(ug["wlmix"]["printed"] - ug["wlmix"]["won"]) < abs(ug["lol1f"]["printed"] - ug["lol1f"]["won"])))
            out["periods"][name] = res
            for sc in FS.SCOPES:
                v = res[sc]
                log(f"  {stat} {name:8} scope {sc:3} n={v['units']:6d} s={v['series']:5d} LS {v['logscore_gain']['mean']:+.4f} "
                    f"[{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}]")
            if ug:
                log(f"  {stat} {name} Under gap: lol-1f printed {ug['lol1f']['printed']:.3f} won {ug['lol1f']['won']:.3f} (n {ug['lol1f']['n']}) "
                    f"-> wl-mix printed {ug['wlmix']['printed']:.3f} won {ug['wlmix']['won']:.3f} (n {ug['wlmix']['n']})")
            log(f"  {stat} {name}: A10 rule {'PASS' if res['passes'] else 'fail'}")
        out["decision"] = "pass" if out["periods"]["F-VALID"]["passes"] and out["periods"]["SPENT"]["passes"] else "fail"
        log(f"{stat}: A10 decision {out['decision']}{' (evidence only)' if stat == 'kills' else ''}")
        rep["stats"][stat] = out
    return rep


if __name__ == "__main__":
    rep = run(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=float)
