"""A8 study (lol/EVALUATION.md): ep-1 event pace over lol-1f. python -m lol.event_pace_study <history.db> <report.json>
K and b are chosen on F-TRAIN only; F-VALID and the spent period are each scored once under A8's rule."""
import json
import sys
from datetime import date

import numpy as np

from . import backtest, event_pace, factors
from . import factor_study as FS

PERIODS = {"F-TRAIN": (date(2026, 1, 1), date(2026, 5, 1)), "F-VALID": (date(2026, 5, 1), date(2026, 8, 1)),
           "SPENT": (date(2026, 8, 1), date(2026, 10, 17))}
GRID = [(K, b) for K in (2, 5, 10, 20, 40, 80) for b in (0.5, 1.0)]
KW = ("world championship", "worlds", "mid-season invitational", "msi 20", "first stand", "esports world cup 20", "esports nations cup 20")


def intl(name):
    n = str(name or "").lower()
    return any(k in n for k in KW) and "qualifier" not in n and "road to" not in n


def run(db_path, log=print):
    st = FS.Study(db_path)
    fit = json.loads(FS.FIT_FILE.read_text(encoding="utf-8"))
    pace = event_pace.Pace(event_pace.games_from_rows(st.data.rows, st.games, st.data.match_day))
    rep = {"amendment": "A8", "grid": GRID, "stats": {}}
    for stat in ("kills", "assists"):
        f = fit["stats"][stat]
        per = {}
        for name, (a, b_) in PERIODS.items():
            us = st.units(stat, a, b_)
            X = np.array([[st.feats(u[0])[k] for k in factors.FEATS] for u in us])
            Z = (X - np.array([f["mean"][k] for k in factors.FEATS])) / np.array([f["sd"][k] for k in factors.FEATS])
            mu = np.array([u[3] for u in us]) * np.exp(Z @ np.array([f["beta"][k] for k in factors.FEATS]))
            per[name] = (us, mu, np.array([u[2] for u in us], float), np.array([u[4] for u in us]))
        us, mu, y, r = per["F-TRAIN"]
        T = [st.tourn.get(u[0]["match"]) for u in us]
        best = None
        for K, b in GRID:
            c = np.array([pace.factor(t, u[0]["day"], stat, K, b)[0] for t, u in zip(T, us)])
            ll = float(np.mean(FS.nb_ll(y, mu * c, r)))
            log(f"{stat} F-TRAIN K={K} b={b}: mean NB log score {ll:.5f}")
            if best is None or ll > best[0]:
                best = (ll, K, b)
        base_ll = float(np.mean(FS.nb_ll(y, mu, r)))
        _, K, b = best
        log(f"{stat}: chosen K={K} b={b} (F-TRAIN {best[0]:.5f} vs lol-1f {base_ll:.5f})")
        out = {"chosen": {"K": K, "b": b, "ftrain_ll": best[0], "ftrain_ll_lol1f": base_ll}, "periods": {}}
        for name in ("F-TRAIN", "F-VALID", "SPENT"):
            us, mu, y, r = per[name]
            info = [pace.factor(st.tourn.get(u[0]["match"]), u[0]["day"], stat, K, b) for u in us]
            c = np.array([x[0] for x in info])
            res = {}
            for label, sel in (("all", lambda u: True), ("international", lambda u: intl(st.tourn.get(u[0]["match"])))):
                for scope in FS.SCOPES:
                    m = np.array([u[1] == scope and sel(u) for u in us])
                    if m.sum() < 20:
                        continue
                    g = [u[0]["match"] for u, mm in zip(us, m) if mm]
                    ls = backtest.grouped_boot(FS.nb_ll(y[m], mu[m] * c[m], r[m]) - FS.nb_ll(y[m], mu[m], r[m]), g, n=2000)
                    res[f"{label} {scope}"] = {"units": int(m.sum()), "series": len(set(g)),
                                               "logscore_gain": {"mean": ls[0], "lo95": ls[1], "hi95": ls[2]},
                                               "mae_lol1f": float(np.mean(np.abs(mu[m] - y[m]))), "mae_ep1": float(np.mean(np.abs(mu[m] * c[m] - y[m]))),
                                               "bias_lol1f": float(np.mean(mu[m] - y[m])), "bias_ep1": float(np.mean(mu[m] * c[m] - y[m])),
                                               "share_adjusted": float(np.mean(c[m] != 1.0))}
            s1, s12, s123 = res.get("all 1"), res.get("all 12"), res.get("all 123")
            res["passes"] = bool(s1 and s12 and s123 and s12["logscore_gain"]["lo95"] > 0 and s123["logscore_gain"]["mean"] >= 0
                                 and s12["mae_ep1"] <= s12["mae_lol1f"] and s1["logscore_gain"]["mean"] >= 0)
            out["periods"][name] = res
            for k, v in res.items():
                if k != "passes":
                    log(f"  {stat} {name:8} {k:16} n={v['units']:6d} s={v['series']:5d} LS {v['logscore_gain']['mean']:+.4f} "
                        f"[{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}] MAE {v['mae_lol1f']:.3f}->{v['mae_ep1']:.3f} "
                        f"bias {v['bias_lol1f']:+.3f}->{v['bias_ep1']:+.3f} adj {v['share_adjusted']:.2f}")
            log(f"  {stat} {name}: A8 rule {'PASS' if res['passes'] else 'fail'}")
        out["decision"] = "pass" if out["periods"]["F-VALID"]["passes"] and out["periods"]["SPENT"]["passes"] else "fail"
        log(f"{stat}: A8 decision {out['decision']}")
        rep["stats"][stat] = out
    return rep


if __name__ == "__main__":
    rep = run(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=float)
