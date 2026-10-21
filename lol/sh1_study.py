"""A13 study (lol/EVALUATION.md): sh-1 assists shape mixture over lol-1f.

  python -m lol.sh1_study <history.db (2026-10-19 pull)> <newest pull.db> <report.json>

s and w per scope are chosen on F-TRAIN only (grid fixed in A13: s = lol-1's size x {0.8, 0.9, ..., 1.4}, w in {0, 0.1, ..., 0.6});
F-VALID and the spent period (from the 2026-10-19 pull, the A7-A12 data) and the hold-out week (series from 2026-10-17 in the
newest pull) are each scored once. lol-1f's mean is unchanged; only the distribution around it changes. The vectorised
log likelihood here is checked against lol/sh1.py (the pure implementation production would use) before anything is scored."""
import hashlib
import json
import random
import sys
from datetime import date
from pathlib import Path

import numpy as np
from scipy.stats import norm

from . import backtest, evaluate, model, sh1
from . import factor_study as FS
from .level_study import lol1f_mu, prev_totals

PERIODS = {"F-TRAIN": (date(2026, 1, 1), date(2026, 5, 1)), "F-VALID": (date(2026, 5, 1), date(2026, 8, 1)),
           "SPENT": (date(2026, 8, 1), date(2026, 10, 17))}
HOLDOUT = (date(2026, 10, 17), date(2026, 10, 22))        # A12's window, extended to the 2026-10-21 pull (games to Oct 20)
S_MULT = (0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4)
W_GRID = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
STAT = "assists"


def mix_ll(y, mu, s, w):
    """Vectorised log sh1.pmf."""
    nb = np.exp(FS.nb_ll(y, mu, s))
    if not np.any(w):
        return np.log(nb)
    sd = np.sqrt(mu + mu * mu / s)
    z = norm.sf((-0.5 - mu) / sd)
    pn = (norm.cdf((y + 0.5 - mu) / sd) - norm.cdf((y - 0.5 - mu) / sd)) / z
    return np.log((1 - w) * nb + w * pn)


def arrays(st, fit, a, b):
    us = st.units(STAT, a, b)
    if not us:
        return us, np.zeros(0), np.zeros(0), np.zeros(0), np.array([], dtype=str)
    return (us, lol1f_mu(st, fit, STAT, us), np.array([u[2] for u in us], float), np.array([u[4] for u in us], float),
            np.array([u[1] for u in us]))


def under_gap(st, us, mu, r, sizes, ws):
    """(printed, won, n) of graded (55%+) assists Under picks at book-style lines, for lol-1f and sh-1."""
    acc = {"lol1f": [0.0, 0, 0], "sh1": [0.0, 0, 0]}
    for u, m in zip(us, mu):
        t, sc, y, _, rr = u
        line = evaluate.dev_line(prev_totals(st, t["player"], t["day"], FS.SCOPES[sc][0], STAT))
        if line is None or y == line:
            continue
        for name, (po, pu, _) in (("lol1f", model.probs(float(m), rr, line)), ("sh1", sh1.probs(float(m), sizes[sc], ws[sc], line))):
            p = pu / (po + pu)
            if p < 0.55:
                continue
            a = acc[name]
            a[0] += p
            a[1] += y < line
            a[2] += 1
    return {k: {"printed": v[0] / v[2] if v[2] else None, "won": v[1] / v[2] if v[2] else None, "n": v[2]} for k, v in acc.items()}


def score(us, mu, y, r, sc, sizes, ws):
    res = {}
    for scope in FS.SCOPES:
        m = sc == scope
        if not m.any():
            continue
        g = [u[0]["match"] for u, mm in zip(us, m) if mm]
        d = mix_ll(y[m], mu[m], sizes[scope], ws[scope]) - FS.nb_ll(y[m], mu[m], r[m])
        ls = backtest.grouped_boot(d, g, n=2000)
        res[scope] = {"units": int(m.sum()), "series": len(set(g)), "logscore_gain": {"mean": ls[0], "lo95": ls[1], "hi95": ls[2]},
                      "mae_lol1f": float(np.mean(np.abs(mu[m] - y[m]))), "bias_lol1f": float(np.mean(mu[m] - y[m]))}
    return res


def mean_shift(us, mu, sc, sizes, ws, n=1500, seed=5):
    """Mean over sampled units of (sh-1 mixture mean - lol-1f mu), per scope (the renormalised normal's cut tail)."""
    out = {}
    rng = random.Random(seed)
    for scope in FS.SCOPES:
        idx = [i for i in range(len(us)) if sc[i] == scope]
        if not idx:
            continue
        pick = rng.sample(idx, min(n, len(idx)))
        out[scope] = float(np.mean([sh1.mean(float(mu[i]), sizes[scope], ws[scope]) - float(mu[i]) for i in pick]))
    return out


def run(db_path, holdout_db, log=print):
    fit = json.loads(FS.FIT_FILE.read_text(encoding="utf-8"))
    st = FS.Study(db_path)
    per = {name: arrays(st, fit, a, b) for name, (a, b) in PERIODS.items()}
    us, mu, y, r, sc = per["F-TRAIN"]
    sizes, ws, grid_ll = {}, {}, {}
    for scope in FS.SCOPES:
        m = sc == scope
        r0 = model.R[(STAT, scope)]
        cells = {}
        for k in S_MULT:
            for w in W_GRID:
                cells[(k, w)] = float(np.mean(mix_ll(y[m], mu[m], r0 * k, w)))
        (k, w), best = max(cells.items(), key=lambda kv: kv[1])
        sizes[scope], ws[scope] = r0 * k, w
        grid_ll[scope] = {f"{kk}|{ww}": v for (kk, ww), v in cells.items()}
        log(f"F-TRAIN scope {scope} (n={int(m.sum())}): chosen s = {r0} x {k} = {r0 * k:.4f}, w = {w}; mean LL {best:.5f} vs lol-1f {cells[(1.0, 0.0)]:.5f}")
    # the vectorised likelihood equals the pure implementation (checked on F-TRAIN units only)
    rng = random.Random(1)
    chk = rng.sample(range(len(us)), 400)
    diff = max(abs(float(mix_ll(y[i:i + 1], mu[i:i + 1], sizes[sc[i]], ws[sc[i]])[0]) - sh1.logpmf(int(y[i]), float(mu[i]), sizes[sc[i]], ws[sc[i]]))
               for i in chk)
    assert diff < 1e-9, diff
    log(f"vectorised vs lol/sh1.py log pmf, 400 F-TRAIN units: max diff {diff:.2e}")
    rep = {"amendment": "A13", "candidate": "sh-1", "stat": STAT,
           "data": {"periods_db_sha256_16": hashlib.sha256(Path(db_path).read_bytes()).hexdigest()[:16],
                    "holdout_db_sha256_16": hashlib.sha256(Path(holdout_db).read_bytes()).hexdigest()[:16]},
           "grid": {"s_mult_of_lol1_size": S_MULT, "w": W_GRID},
           "chosen": {sc_: {"s": sizes[sc_], "s_mult": round(sizes[sc_] / model.R[(STAT, sc_)], 3), "w": ws[sc_], "lol1_size": model.R[(STAT, sc_)]}
                      for sc_ in FS.SCOPES},
           "f_train_grid_mean_ll": grid_ll, "check_pure_vs_vectorised_max_diff": diff, "periods": {}}
    for name in PERIODS:
        us, mu, y, r, sc = per[name]
        res = score(us, mu, y, r, sc, sizes, ws)
        res["mixture_mean_minus_mu"] = mean_shift(us, mu, sc, sizes, ws)
        if name != "F-TRAIN":
            res["under_gap"] = ug = under_gap(st, us, mu, r, sizes, ws)
            gap0, gap1 = (abs(ug[k]["printed"] - ug[k]["won"]) for k in ("lol1f", "sh1"))
            res["under_gap"]["gap_lol1f"], res["under_gap"]["gap_sh1"] = gap0, gap1
            res["passes"] = bool(res["12"]["logscore_gain"]["lo95"] > 0 and res["123"]["logscore_gain"]["mean"] >= 0
                                 and res["1"]["logscore_gain"]["mean"] >= 0 and gap1 < gap0)
        rep["periods"][name] = res
        _log(log, name, res)
    st2 = FS.Study(holdout_db)
    us, mu, y, r, sc = arrays(st2, fit, *HOLDOUT)
    res = score(us, mu, y, r, sc, sizes, ws)
    res["window"] = [HOLDOUT[0].isoformat(), HOLDOUT[1].isoformat()]
    res["passes"] = bool(res.get("1") and res["1"]["logscore_gain"]["mean"] >= 0)
    rep["periods"]["HOLDOUT"] = res
    _log(log, "HOLDOUT", res)
    P = rep["periods"]
    rep["decision"] = "pass" if all(P[k]["passes"] for k in ("F-VALID", "SPENT", "HOLDOUT")) else "fail"
    log(f"A13 decision: {rep['decision']}")
    return rep


def _log(log, name, res):
    for scope in FS.SCOPES:
        v = res.get(scope)
        if v:
            log(f"  {name:8} scope {scope:3} n={v['units']:6d} series={v['series']:5d} LS gain {v['logscore_gain']['mean']:+.4f} "
                f"[{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}]  MAE {v['mae_lol1f']:.3f} bias {v['bias_lol1f']:+.3f}")
    if "mixture_mean_minus_mu" in res:
        log(f"  {name} mixture mean - mu: {res['mixture_mean_minus_mu']}")
    ug = res.get("under_gap")
    if ug:
        log(f"  {name} Under gap: lol-1f printed {ug['lol1f']['printed']:.3f} won {ug['lol1f']['won']:.3f} (n {ug['lol1f']['n']}) gap {ug['gap_lol1f']:.3f}"
            f" -> sh-1 printed {ug['sh1']['printed']:.3f} won {ug['sh1']['won']:.3f} (n {ug['sh1']['n']}) gap {ug['gap_sh1']:.3f}")
    if "passes" in res:
        log(f"  {name}: A13 rule {'PASS' if res['passes'] else 'fail'}")


if __name__ == "__main__":
    rep = run(sys.argv[1], sys.argv[2], log=lambda m: print(m, file=sys.stderr, flush=True))
    with open(sys.argv[3], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=float)
