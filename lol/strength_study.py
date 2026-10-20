"""A12 study (lol/EVALUATION.md): sp-1 sharper win chance in lol-1f. python -m lol.strength_study <history.db> <report.json>
a, c (win model) and EW are fitted / chosen on F-TRAIN only; F-VALID, the spent period and the fresh hold-out are scored once."""
import json
import math
import sys
from datetime import date

import numpy as np

from . import backtest, model, strength
from . import factor_study as FS
from .level_study import lol1f_mu

PERIODS = {"F-TRAIN": (date(2026, 1, 1), date(2026, 5, 1)), "F-VALID": (date(2026, 5, 1), date(2026, 8, 1)),
           "SPENT": (date(2026, 8, 1), date(2026, 10, 17)), "HOLDOUT": (date(2026, 10, 17), date(2026, 10, 25))}
EW_GRID = (0.5, 0.75, 1.0, 1.25, 1.5)


def base_lam(st, ts, stat, p_override=None, ew=None):
    """lol-1's per-game lambda for targets ts; optionally with other win chances and EW (targets are restored after)."""
    p = dict(model.PARAMS[stat])
    if ew is not None:
        p["ew"] = ew
    old = [t["p_win"] for t in ts]
    try:
        if p_override is not None:
            for t, x in zip(ts, p_override):
                t["p_win"] = float(x)
        return backtest.lam_game(backtest.features(st.data, ts, p["hl"]), ts, stat, p, dict(model.PRIORS))
    finally:
        for t, x in zip(ts, old):
            t["p_win"] = x


def run(db_path, log=print):
    st = FS.Study(db_path)
    fit = json.loads(FS.FIT_FILE.read_text(encoding="utf-8"))
    win = {}
    for r in st.data.rows:
        if int(r.get("game_number") or 0) == 1 and r.get("win") is not None:
            win[(r["match_key"], r.get("team_id") or r["team"])] = int(r["win"])
    kd_cache = {}

    def kdiff(t):
        k = (t["match"], t["team"], t["opp"])
        if k not in kd_cache:
            a = st.data.tarr.get(t["team"])
            b = st.data.tarr.get(t["opp"])
            kd_cache[k] = (strength.kd(a.tolist() if a is not None else [], t["day"]) - strength.kd(b.tolist() if b is not None else [], t["day"]))
        return kd_cache[k]

    def targets(a, b):
        return [t for t in st.data.targets if a.toordinal() <= t["day"] < b.toordinal() and t.get("p_win") is not None]

    # ---- win model on F-TRAIN game-1 results (each team-match once)
    tr = {}
    for t in targets(*PERIODS["F-TRAIN"]):
        w = win.get((t["match"], t["team_name"]))
        if w is not None:
            tr[(t["match"], t["team"])] = (math.log(min(max(t["p_win"], 1e-6), 1 - 1e-6) / (1 - min(max(t["p_win"], 1e-6), 1 - 1e-6))), kdiff(t), w)
    X = np.array([[x[0], x[1]] for x in tr.values()]); y = np.array([x[2] for x in tr.values()], float)
    beta = np.array([1.0, 0.0])
    for _ in range(50):                                   # Newton-Raphson, logistic regression without intercept
        pz = 1 / (1 + np.exp(-(X @ beta)))
        g = X.T @ (y - pz)
        H = -(X * (pz * (1 - pz))[:, None]).T @ X
        step = np.linalg.solve(H, g)
        beta = beta - step
        if np.max(np.abs(step)) < 1e-10:
            break
    a, c = float(beta[0]), float(beta[1])
    log(f"win model on F-TRAIN ({len(y)} team-matches): a={a:.4f} c={c:.5f}")
    rep = {"amendment": "A12", "win_model": {"a": a, "c": c, "train_n": len(y)}, "stats": {}}

    def psharp(ts):
        return np.array([strength.p_sharp(t["p_win"], 0, 0, a, 0) if False else strength.p_sharp(t["p_win"], kdiff(t), 0.0, a, c) for t in ts])

    # win-model calibration per period
    rep["win_calibration"] = {}
    for name, (d0, d1) in PERIODS.items():
        ll0 = ll1 = n = 0
        for t in targets(d0, d1):
            w = win.get((t["match"], t["team_name"]))
            if w is None:
                continue
            p0, p1 = min(max(t["p_win"], 1e-6), 1 - 1e-6), strength.p_sharp(t["p_win"], kdiff(t), 0.0, a, c)
            ll0 += -(w * math.log(p0) + (1 - w) * math.log(1 - p0))
            ll1 += -(w * math.log(p1) + (1 - w) * math.log(1 - p1))
            n += 1
        if n:
            rep["win_calibration"][name] = {"n_player_targets": n, "logloss_elo": ll0 / n, "logloss_sp1": ll1 / n}
            log(f"  {name}: game-1 log loss Elo {ll0 / n:.4f} -> sp-1 {ll1 / n:.4f} (n {n} player-targets)")

    for stat in ("kills", "assists"):
        J = {"kills": 0, "deaths": 1, "assists": 2}[stat]
        per = {}
        for name, (d0, d1) in PERIODS.items():
            ts = targets(d0, d1)
            us = st.units(stat, d0, d1)
            us = [u for u in us if u[0].get("p_win") is not None]
            mu_f = lol1f_mu(st, fit, stat, us)
            lam0 = base_lam(st, ts, stat)
            idx = {id(t): i for i, t in enumerate(ts)}
            n_of = {"1": 1, "12": 2, "123": 3}
            mult = np.array([m / (lam0[idx[id(u[0])]] * n_of[u[1]]) for u, m in zip(us, mu_f)])   # lol-1f's own multiplier
            ps = psharp(ts)
            per[name] = (ts, us, mu_f, mult, idx, ps, np.array([u[2] for u in us], float), np.array([u[4] for u in us]))

        def mu_new(name, ew):
            ts, us, mu_f, mult, idx, ps, y, r = per[name]
            lam1 = base_lam(st, ts, stat, ps, ew)
            return np.array([lam1[idx[id(u[0])]] * {"1": 1, "12": 2, "123": 3}[u[1]] for u in us]) * mult

        _, _, mu_f, _, _, _, y, r = per["F-TRAIN"]
        scores = {ew: float(np.mean(FS.nb_ll(y, mu_new("F-TRAIN", ew), r))) for ew in EW_GRID}
        ew = max(scores, key=scores.get)
        log(f"{stat}: EW chosen {ew} (F-TRAIN {scores[ew]:.5f} vs lol-1f {float(np.mean(FS.nb_ll(y, mu_f, r))):.5f}; grid {scores})")
        out = {"ew": ew, "ew_scores": scores, "periods": {}}
        for name in PERIODS:
            ts, us, mu_f, mult, idx, ps, y, r = per[name]
            if not us:
                continue
            m1 = mu_new(name, ew)
            res = {}
            for sc in FS.SCOPES:
                m = np.array([u[1] == sc for u in us])
                if m.sum() < 10:
                    continue
                g = [u[0]["match"] for u, mm in zip(us, m) if mm]
                ls = backtest.grouped_boot(FS.nb_ll(y[m], m1[m], r[m]) - FS.nb_ll(y[m], mu_f[m], r[m]), g, n=2000)
                res[sc] = {"units": int(m.sum()), "series": len(set(g)), "logscore_gain": {"mean": ls[0], "lo95": ls[1], "hi95": ls[2]},
                           "mae_lol1f": float(np.mean(np.abs(mu_f[m] - y[m]))), "mae_sp1": float(np.mean(np.abs(m1[m] - y[m]))),
                           "bias_lol1f": float(np.mean(mu_f[m] - y[m])), "bias_sp1": float(np.mean(m1[m] - y[m]))}
            if name == "HOLDOUT":
                res["passes"] = bool(res.get("1") and res["1"]["logscore_gain"]["mean"] >= 0)
            else:
                s1, s12, s123 = res.get("1"), res.get("12"), res.get("123")
                res["passes"] = bool(s1 and s12 and s123 and s12["logscore_gain"]["lo95"] > 0 and s123["logscore_gain"]["mean"] >= 0
                                     and s12["mae_sp1"] <= s12["mae_lol1f"] and s1["logscore_gain"]["mean"] >= 0)
            out["periods"][name] = res
            for sc, v in res.items():
                if sc == "passes":
                    continue
                log(f"  {stat} {name:8} scope {sc:3} n={v['units']:6d} s={v['series']:5d} LS {v['logscore_gain']['mean']:+.4f} "
                    f"[{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}] MAE {v['mae_lol1f']:.3f}->{v['mae_sp1']:.3f} "
                    f"bias {v['bias_lol1f']:+.3f}->{v['bias_sp1']:+.3f}")
            log(f"  {stat} {name}: A12 rule {'PASS' if res['passes'] else 'fail'}")
        P = out["periods"]
        out["decision"] = "pass" if all(P.get(k, {}).get("passes") for k in ("F-VALID", "SPENT", "HOLDOUT")) else "fail"
        log(f"{stat}: A12 decision {out['decision']}")
        rep["stats"][stat] = out
    return rep


if __name__ == "__main__":
    rep = run(sys.argv[1])
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1, default=float)
