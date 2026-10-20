"""A9 study (lol/EVALUATION.md): lr-1 assists level over lol-1f. python -m lol.level_study <history.db> <report.json>
W and K are chosen on F-TRAIN only; F-VALID and the spent period are each scored once under A9's rule."""
import json
import sys
from collections import defaultdict
from datetime import date

import numpy as np

from . import backtest, evaluate, factors, level, model
from . import factor_study as FS

PERIODS = {"F-TRAIN": (date(2026, 1, 1), date(2026, 5, 1)), "F-VALID": (date(2026, 5, 1), date(2026, 8, 1)),
           "SPENT": (date(2026, 8, 1), date(2026, 10, 17))}
GRID = [(W, K) for W in (14, 30, 60) for K in (10, 40, 160)]
J = {"kills": 1, "deaths": 2, "assists": 3}


def lol1f_mu(st, fit, stat, us):
    f = fit["stats"][stat]
    X = np.array([[st.feats(u[0])[k] for k in factors.FEATS] for u in us])
    Z = (X - np.array([f["mean"][k] for k in factors.FEATS])) / np.array([f["sd"][k] for k in factors.FEATS])
    return np.array([u[3] for u in us]) * np.exp(Z @ np.array([f["beta"][k] for k in factors.FEATS]))


def prev_totals(st, pid, day, n, stat):
    a = st.data.arr.get(pid)
    if a is None:
        return []
    by = defaultdict(list)
    for row in a[a[:, 0] < day]:
        by[int(row[0])].append(row[J[stat]])
    return [sum(v[:n]) for _, v in sorted(by.items()) if len(v) >= n]


def under_gap(st, us, mu, stat):
    """(printed, won, n) for graded (55%+) Under picks at book-style lines."""
    p_sum = w = n = 0
    for u, m in zip(us, mu):
        t, sc, y, _, r = u
        line = evaluate.dev_line(prev_totals(st, t["player"], t["day"], FS.SCOPES[sc][0], stat))
        if line is None or y == line:
            continue
        po, pu, _ = model.probs(float(m), r, line)
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
    rep = {"amendment": "A9", "grid": GRID, "stats": {}}
    for stat in ("assists", "kills"):
        allu = st.units(stat, date(2025, 11, 15), date(2026, 10, 17))
        mu_all = lol1f_mu(st, fit, stat, allu)
        resid = [(u[0]["day"], u[0].get("league"), float(u[2]), float(m)) for u, m in zip(allu, mu_all) if u[1] == "1"]
        per = {}
        for name, (a, b) in PERIODS.items():
            idx = [i for i, u in enumerate(allu) if a.toordinal() <= u[0]["day"] < b.toordinal()]
            per[name] = ([allu[i] for i in idx], mu_all[idx])
        us, mu = per["F-TRAIN"]
        y, r = np.array([u[2] for u in us], float), np.array([u[4] for u in us])
        best = None
        for W, K in GRID:
            L = level.Level(resid, W, K)
            c = np.array([L.factor(u[0].get("league"), u[0]["day"])[0] for u in us])
            ll = float(np.mean(FS.nb_ll(y, mu * c, r)))
            log(f"{stat} F-TRAIN W={W} K={K}: {ll:.5f}")
            if best is None or ll > best[0]:
                best = (ll, W, K)
        _, W, K = best
        L = level.Level(resid, W, K)
        log(f"{stat}: chosen W={W} K={K} (F-TRAIN {best[0]:.5f} vs lol-1f {float(np.mean(FS.nb_ll(y, mu, r))):.5f})")
        out = {"chosen": {"W": W, "K": K}, "periods": {}}
        for name in PERIODS:
            us, mu = per[name]
            y, r = np.array([u[2] for u in us], float), np.array([u[4] for u in us])
            c = np.array([L.factor(u[0].get("league"), u[0]["day"])[0] for u in us])
            res = {"c_mean": float(c.mean()), "c_p10": float(np.percentile(c, 10)), "c_p90": float(np.percentile(c, 90))}
            for scope in FS.SCOPES:
                m = np.array([u[1] == scope for u in us])
                g = [u[0]["match"] for u, mm in zip(us, m) if mm]
                ls = backtest.grouped_boot(FS.nb_ll(y[m], mu[m] * c[m], r[m]) - FS.nb_ll(y[m], mu[m], r[m]), g, n=2000)
                res[scope] = {"units": int(m.sum()), "series": len(set(g)), "logscore_gain": {"mean": ls[0], "lo95": ls[1], "hi95": ls[2]},
                              "mae_lol1f": float(np.mean(np.abs(mu[m] - y[m]))), "mae_lr1": float(np.mean(np.abs(mu[m] * c[m] - y[m]))),
                              "bias_lol1f": float(np.mean(mu[m] - y[m])), "bias_lr1": float(np.mean(mu[m] * c[m] - y[m]))}
            if name != "F-TRAIN":
                g0, g1 = under_gap(st, us, mu, stat), under_gap(st, us, mu * c, stat)
                res["under_gap"] = {"lol1f": {"printed": g0[0], "won": g0[1], "n": g0[2]}, "lr1": {"printed": g1[0], "won": g1[1], "n": g1[2]}}
            s1, s12, s123 = res["1"], res["12"], res["123"]
            ug = res.get("under_gap")
            res["passes"] = bool(s12["logscore_gain"]["lo95"] > 0 and s123["logscore_gain"]["mean"] >= 0 and s12["mae_lr1"] <= s12["mae_lol1f"]
                                 and s1["logscore_gain"]["mean"] >= 0
                                 and (ug is None or abs(ug["lr1"]["printed"] - ug["lr1"]["won"]) < abs(ug["lol1f"]["printed"] - ug["lol1f"]["won"])))
            out["periods"][name] = res
            for sc in FS.SCOPES:
                v = res[sc]
                log(f"  {stat} {name:8} scope {sc:3} n={v['units']:6d} s={v['series']:5d} LS {v['logscore_gain']['mean']:+.4f} "
                    f"[{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}] MAE {v['mae_lol1f']:.3f}->{v['mae_lr1']:.3f} "
                    f"bias {v['bias_lol1f']:+.3f}->{v['bias_lr1']:+.3f}")
            if ug:
                log(f"  {stat} {name} Under gap at book-style lines: lol-1f printed {ug['lol1f']['printed']:.3f} won {ug['lol1f']['won']:.3f} "
                    f"(n {ug['lol1f']['n']}) -> lr-1 printed {ug['lr1']['printed']:.3f} won {ug['lr1']['won']:.3f} (n {ug['lr1']['n']})")
            log(f"  {stat} {name}: c mean {res['c_mean']:.3f} (p10 {res['c_p10']:.3f}, p90 {res['c_p90']:.3f}) A9 rule {'PASS' if res['passes'] else 'fail'}")
        out["decision"] = "pass" if out["periods"]["F-VALID"]["passes"] and out["periods"]["SPENT"]["passes"] else "fail"
        log(f"{stat}: A9 decision {out['decision']}{' (evidence only)' if stat == 'kills' else ''}")
        rep["stats"][stat] = out
    return rep


if __name__ == "__main__":
    rep = run(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=float)
