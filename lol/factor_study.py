"""lol/EVALUATION.md amendments A7 / A7.1: the owner's full factor list as increments over the deployed lol-1.

  python -m lol.factor_study run --db <history sqlite copy> [--json out.json]   F-TRAIN fit, F-VALID scored
  python -m lol.factor_study fit --db <...> --report <run json>                  lol/factor_fit.json for the stats that passed

Base = the deployed lol-1 (lol/model_fit.json params, priors and NB sizes; Elo win chance from the same history). Each
candidate multiplies lol-1's per-game mean by exp(beta . z); z = lol/factors.py features (games on days BEFORE the match day;
lineups = each team's last-fielded lineup, A7.1). beta per stat by negative-binomial maximum likelihood (ridge 1.0 per
standardised coefficient). Development evidence - never real-line evidence.
"""
import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

from . import backtest, config, factors, history, model

HISTORY_FROM = date(2025, 11, 1)
F_TRAIN = (date(2026, 1, 1), date(2026, 5, 1))
F_VALID = (date(2026, 5, 1), date(2026, 8, 1))
RIDGE = 1.0
SCOPES = {"1": (1, None), "12": (2, 3), "123": (3, 5)}
STATS = ("kills", "assists", "deaths")
FIT_FILE = config.PKG_DIR / "factor_fit.json"
VERSION = "lol-1f"


def nb_ll(y, mu, r):
    return gammaln(y + r) - gammaln(r) - gammaln(y + 1) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))


def fit_beta(X, off, y, r):
    def obj(b):
        mu = np.exp(off + X @ b)
        return -nb_ll(y, mu, r).sum() + RIDGE * b @ b, -(X.T @ (r * (y - mu) / (r + mu))) + 2 * RIDGE * b
    return minimize(obj, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B").x


class Study:
    def __init__(self, db_path):
        db = history.DB(dsn="", path=Path(db_path))
        self.rows = history.all_player_games(db, since=HISTORY_FROM.isoformat())
        games = {g["game_key"]: g for g in db.query("SELECT game_key, patch, tournament, league, played_at FROM lol_games")}
        self.games = games
        self.data = backtest.Data(self.rows)
        self.ix = factors.Index(self.rows, games, match_day=self.data.match_day)
        a, b = F_TRAIN[0].toordinal(), F_TRAIN[1].toordinal()
        self.consts = dict(self.ix.consts(a, b), role=factors.role_consts(self.rows, self.data.match_day, a, b))
        self.tourn = {}
        for r in self.data.rows:
            if int(r.get("game_number") or 1) == 1:
                self.tourn[r["match_key"]] = (games.get(r["game_key"]) or {}).get("tournament")
        self._f = {}

    def feats(self, t):
        k = (t["match"], t["player"])
        if k not in self._f:
            self._f[k] = self.ix.features(t["player"], t["team"], t["opp"], t["role"], t["day"], t.get("league"), t.get("p_win"),
                                          self.tourn.get(t["match"]), self.consts)
        return self._f[k]

    def units(self, stat, d_from, d_to):
        d = self.data
        p = model.PARAMS[stat]
        ts = [t for t in d.targets if d_from.toordinal() <= t["day"] < d_to.toordinal()]
        lam = backtest.lam_game(backtest.features(d, ts, p["hl"]), ts, stat, p, dict(model.PRIORS))
        j = {"kills": 0, "deaths": 1, "assists": 2}[stat]
        out = []
        for i, t in enumerate(ts):
            for scope, (n, bo) in SCOPES.items():
                if (bo and t.get("best_of") != bo) or not all(g in t["games"] for g in range(1, n + 1)):
                    continue
                out.append((t, scope, sum(t["games"][g][j] for g in range(1, n + 1)), float(lam[i]) * n, model.R[(stat, scope)]))
        return out


def _arrays(st, us, mean=None, sd=None):
    X = np.array([[st.feats(u[0])[f] for f in factors.FEATS] for u in us])
    if mean is None:
        mean, sd = X.mean(0), X.std(0) + 1e-9
    return (X - mean) / sd, mean, sd, np.log([u[3] for u in us]), np.array([u[2] for u in us], float), np.array([u[4] for u in us])


def run(db_path, log=print):
    st = Study(db_path)
    report = {"amendment": "A7.1", "periods": {"history_from": HISTORY_FROM.isoformat(), "f_train": [x.isoformat() for x in F_TRAIN],
                                               "f_valid": [x.isoformat() for x in F_VALID]},
              "ridge": RIDGE, "groups": {k: list(v) for k, v in factors.GROUPS.items()}, "base": {"version": model.VERSION, "fit": model.FIT_META},
              "consts": st.consts, "results": {}}
    for stat in STATS:
        tr, va = st.units(stat, *F_TRAIN), st.units(stat, *F_VALID)
        log(f"{stat}: F-TRAIN units {len(tr)}, F-VALID units {len(va)}")
        Ztr, mean, sd, off_tr, y_tr, r_tr = _arrays(st, tr)
        Zva, _, _, off_va, y_va, r_va = _arrays(st, va, mean, sd)
        res = {}
        for name, cols in list(factors.GROUPS.items()) + [("ALL", tuple(factors.FEATS))]:
            idx = [factors.FEATS.index(c) for c in cols]
            b = fit_beta(Ztr[:, idx], off_tr, y_tr, r_tr)
            mu1, mu0 = np.exp(off_va + Zva[:, idx] @ b), np.exp(off_va)
            per = {}
            for scope in SCOPES:
                m = np.array([u[1] == scope for u in va])
                if not m.any():
                    continue
                groups = [u[0]["match"] for u, mm in zip(va, m) if mm]
                g = backtest.grouped_boot(nb_ll(y_va[m], mu1[m], r_va[m]) - nb_ll(y_va[m], mu0[m], r_va[m]), groups, n=2000)
                a = backtest.grouped_boot(np.abs(mu0[m] - y_va[m]) - np.abs(mu1[m] - y_va[m]), groups, n=2000)
                per[scope] = {"units": int(m.sum()), "series": len(set(groups)), "logscore_gain": {"mean": g[0], "lo95": g[1], "hi95": g[2]},
                              "mae_lol1": float(np.mean(np.abs(mu0[m] - y_va[m]))), "mae_candidate": float(np.mean(np.abs(mu1[m] - y_va[m]))),
                              "mae_gain": {"mean": a[0], "lo95": a[1], "hi95": a[2]},
                              "bias_lol1": float(np.mean(mu0[m] - y_va[m])), "bias_candidate": float(np.mean(mu1[m] - y_va[m]))}
            s12, s123 = per.get("12"), per.get("123")
            passed = bool(s12 and s123 and s12["logscore_gain"]["lo95"] > 0 and s123["logscore_gain"]["mean"] >= 0
                          and s12["mae_candidate"] <= s12["mae_lol1"])
            res[name] = {"scopes": per, "passes_A7_rule": passed, "beta": {factors.FEATS[i]: round(float(v), 5) for i, v in zip(idx, b)}}
            log(f"  {name:12} " + " | ".join(f"{s}: LS {v['logscore_gain']['mean']:+.4f} [{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}] "
                                              f"MAE {v['mae_lol1']:.3f}->{v['mae_candidate']:.3f}" for s, v in per.items()) + ("  PASS" if passed else ""))
        report["results"][stat] = res
    return report


def write_fit(db_path, report_path, log=print):
    """ALL's coefficients refitted on F-TRAIN + F-VALID for every stat whose A7.1 ALL passed (A7: the fit of record)."""
    rep = json.loads(Path(report_path).read_text(encoding="utf-8"))
    st = Study(db_path)
    out = {"version": VERSION, "base": model.VERSION, "base_fit": model.FIT_META, "protocol": "lol/EVALUATION.md A7 / A7.1",
           "report_sha256": hashlib.sha256(Path(report_path).read_bytes()).hexdigest(), "consts": st.consts, "ridge": RIDGE,
           "fit_targets": [F_TRAIN[0].isoformat(), F_VALID[1].isoformat()], "stats": {},
           "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for stat in STATS:
        if not rep["results"][stat]["ALL"]["passes_A7_rule"]:
            out["stats"][stat] = None
            continue
        us = st.units(stat, F_TRAIN[0], F_VALID[1])
        Z, mean, sd, off, y, r = _arrays(st, us)
        b = fit_beta(Z, off, y, r)
        out["stats"][stat] = {"beta": dict(zip(factors.FEATS, map(float, b))), "mean": dict(zip(factors.FEATS, map(float, mean))),
                              "sd": dict(zip(factors.FEATS, map(float, sd))), "units": len(us)}
        log(f"{stat}: fitted on {len(us)} units")
    FIT_FILE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "fit"))
    ap.add_argument("--db", required=True)
    ap.add_argument("--json")
    ap.add_argument("--report")
    a = ap.parse_args(argv)
    lg = lambda m: print(m, file=sys.stderr, flush=True)
    if a.cmd == "run":
        rep = run(a.db, log=lg)
        if a.json:
            Path(a.json).write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        print(json.dumps({s: {g: v["passes_A7_rule"] for g, v in r.items()} for s, r in rep["results"].items()}))
    else:
        out = write_fit(a.db, a.report, log=lg)
        print(json.dumps({s: (v is not None) for s, v in out["stats"].items()}))


if __name__ == "__main__":
    main()
