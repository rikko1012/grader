"""lol/EVALUATION.md amendment A7: the owner's full factor list as increments over the deployed lol-1.

  python -m lol.factor_study --db <history sqlite copy> [--json out.json]

Base = the deployed lol-1 (lol/model_fit.json params, priors and NB sizes; Elo win chance from the same history). Each
candidate multiplies lol-1's per-game mean by exp(beta . z); z = pre-match features from games on days BEFORE the match day
(A7 table). beta per stat by negative-binomial maximum likelihood on F-TRAIN (ridge 1.0 per standardised coefficient),
scored once on F-VALID: log score, MAE, series-grouped bootstrap intervals. Development evidence - never real-line evidence.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

from . import backtest, history, model

HISTORY_FROM = date(2025, 11, 1)
F_TRAIN = (date(2026, 1, 1), date(2026, 5, 1))
F_VALID = (date(2026, 5, 1), date(2026, 8, 1))
HL = 60.0
RIDGE = 1.0
SCOPES = {"1": (1, None), "12": (2, 3), "123": (3, 5)}
STATS = ("kills", "assists", "deaths")
J = {"kills": 1, "deaths": 2, "assists": 3}            # column in the per-player array
GROUPS = {
    "pace": ("pace_len", "pace_ckpm"),
    "script": ("script_stomp",),
    "matchup": ("lane_opp_deaths", "lane_opp_kills", "lane_missing"),
    "form": ("form_k", "form_a", "form_d", "sample_opp_wr"),
    "individual": ("kp", "dmg_share", "dpm", "cspm", "vspm", "death_share", "kill_share"),
    "team": ("team_form", "playoffs", "lg_lck", "lg_lpl", "lg_lec", "lg_americas"),
    "patch": ("patch_age", "patch_share", "patch_missing"),
    "situational": ("rest", "roster", "champ_pool"),
}
FEATS = [f for g in GROUPS.values() for f in g] + ["no_history"]


def _day(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).date().toordinal()


def _lg(league):
    s = str(league or "").lower()
    return {"lg_lck": "lck" in s and "cl" not in s and "challengers" not in s, "lg_lpl": "lpl" in s,
            "lg_lec": s.startswith("lec") or "emea championship" in s,
            "lg_americas": any(x in s for x in ("lcs", "lta", "cblol", "lla"))}


class Study:
    def __init__(self, db_path):
        db = history.DB(dsn="", path=Path(db_path))
        self.rows = history.all_player_games(db, since=HISTORY_FROM.isoformat())
        games = db.query("SELECT game_key, patch, tournament, league, played_at FROM lol_games")
        self.game = {g["game_key"]: g for g in games}
        self.data = backtest.Data(self.rows)
        d = self.data
        # per game: team damage, lineup; per team: arrays of games
        tdmg, lineup = defaultdict(float), defaultdict(set)
        for r in d.rows:
            tk = r.get("team_id") or r["team"]
            tdmg[(r["game_key"], tk)] += r.get("damage") or 0
            lineup[(r["game_key"], tk)].add(r["pid"])
        self.pl = defaultdict(list)    # pid -> rows (day, k, d, a, tk, ok, dur_min, dmg, tdmg, cs, vis, win, opp, champ, patch)
        self.tm = defaultdict(list)    # team -> (day, tk, ok, dur_min, win, game_key)
        seen = set()
        for r in d.rows:
            day = d.match_day[r["match_key"]]
            tk = r.get("team_id") or r["team"]
            opp = r.get("opponent_id") or r["opponent"]
            dur = (r.get("duration_s") or 0) / 60.0 or None
            g = self.game.get(r["game_key"]) or {}
            self.pl[r["pid"]].append((day, r["kills"], r["deaths"], r["assists"], r["team_kills"], r["opp_kills"], dur,
                                      r.get("damage"), tdmg[(r["game_key"], tk)] or None, r.get("cs"), r.get("vision"),
                                      r.get("win"), opp, r.get("champion"), g.get("patch"), r["game_key"]))
            if (r["game_key"], tk) not in seen:
                seen.add((r["game_key"], tk))
                self.tm[tk].append((day, r["team_kills"], r["opp_kills"], dur, r.get("win"), r["game_key"]))
        for v in self.pl.values():
            v.sort(key=lambda x: x[0])
        for v in self.tm.values():
            v.sort(key=lambda x: x[0])
        self.lineup = lineup
        # patches: first day each patch appears; per league the patch of its latest game before a day
        self.patch_first = {}
        self.league_games = defaultdict(list)
        for g in games:
            if not g.get("played_at"):
                continue
            dd = _day(g["played_at"])
            if g.get("patch"):
                self.patch_first[g["patch"]] = min(self.patch_first.get(g["patch"], dd), dd)
                self.league_games[g.get("league")].append((dd, g["patch"]))
        for v in self.league_games.values():
            v.sort()
        tr = [r for r in d.rows if F_TRAIN[0].toordinal() <= d.match_day[r["match_key"]] < F_TRAIN[1].toordinal()]
        self.role_avg = self._role_avgs(tr)
        durs = [r["duration_s"] / 60 for r in tr if r.get("duration_s")]
        self.len_avg = float(np.mean(durs))
        self.ckpm_avg = float(np.mean([(r["team_kills"] + r["opp_kills"]) / (r["duration_s"] / 60) for r in tr if r.get("duration_s")]))
        self._twr = {}

    def _role_avgs(self, rows):
        acc = defaultdict(lambda: defaultdict(list))
        for r in rows:
            dur = (r.get("duration_s") or 0) / 60
            ro = r["role"]
            if r["team_kills"]:
                acc[ro]["kp"].append((r["kills"] + r["assists"]) / r["team_kills"])
                acc[ro]["kill_share"].append(r["kills"] / r["team_kills"])
            if r["opp_kills"]:
                acc[ro]["death_share"].append(r["deaths"] / r["opp_kills"])
            if dur:
                for k, v in (("dpm", r.get("damage")), ("cspm", r.get("cs")), ("vspm", r.get("vision"))):
                    if v is not None:
                        acc[ro][k].append(v / dur)
            for k in ("kills", "deaths", "assists"):
                acc[ro][k].append(r[k])
        out = {ro: {k: float(np.mean(v)) for k, v in m.items()} for ro, m in acc.items()}
        ds = defaultdict(list)
        tdmg = defaultdict(float)
        for r in rows:
            tdmg[(r["game_key"], r.get("team_id") or r["team"])] += r.get("damage") or 0
        for r in rows:
            t = tdmg[(r["game_key"], r.get("team_id") or r["team"])]
            if t and r.get("damage") is not None:
                ds[r["role"]].append(r["damage"] / t)
        for ro, v in ds.items():
            out[ro]["dmg_share"] = float(np.mean(v))
        return out

    def team_wr(self, team, day):
        k = (team, day)
        if k not in self._twr:
            g = [x for x in self.tm.get(team, ()) if x[0] < day and x[4] is not None]
            w = np.array([math.exp(-(day - x[0]) / HL) for x in g])
            self._twr[k] = float((np.dot(w, [x[4] for x in g]) + 5 * 0.5) / (w.sum() + 5)) if len(g) else 0.5
        return self._twr[k]

    def features(self, t, starters):
        day, ro = t["day"], t["role"]
        ra = self.role_avg.get(ro) or {}
        f = dict.fromkeys(FEATS, 0.0)
        prev = [x for x in self.pl.get(t["player"], ()) if x[0] < day]
        if not prev:
            f["no_history"] = 1.0
        w = np.array([math.exp(-(day - x[0]) / HL) for x in prev])

        def wmean(num, den, prior, K=5.0):
            nn = dd = 0.0
            for wi, x in zip(w, prev):
                a, b = num(x), den(x)
                if a is None or b is None or not b:
                    continue
                nn += wi * a
                dd += wi * b
            return (nn + K * prior) / (dd + K) if prior else None

        lg = lambda v, base: math.log(v / base) if v and base else 0.0
        # individual
        f["kp"] = lg(wmean(lambda x: x[1] + x[3], lambda x: x[4], ra.get("kp")), ra.get("kp"))
        f["kill_share"] = lg(wmean(lambda x: x[1], lambda x: x[4], ra.get("kill_share")), ra.get("kill_share"))
        f["death_share"] = lg(wmean(lambda x: x[2], lambda x: x[5], ra.get("death_share")), ra.get("death_share"))
        f["dmg_share"] = lg(wmean(lambda x: x[7], lambda x: x[8], ra.get("dmg_share")), ra.get("dmg_share"))
        for k, c in (("dpm", 7), ("cspm", 9), ("vspm", 10)):
            f[k] = lg(wmean(lambda x, c=c: x[c], lambda x: x[6], ra.get(k), K=150.0), ra.get(k))
        # form: last 5 vs the long-run rate of each stat
        for key, j, stat in (("form_k", 1, "kills"), ("form_a", 3, "assists"), ("form_d", 2, "deaths")):
            if prev:
                rate = (float(np.dot(w, [x[j] for x in prev])) + 5 * ra.get(stat, 1)) / (w.sum() + 5)
                l5 = [x[j] for x in prev[-5:]]
                f[key] = math.log(((sum(l5) + 3 * rate) / (len(l5) + 3)) / rate) if rate else 0.0
        if prev:
            f["sample_opp_wr"] = float(np.mean([self.team_wr(x[12], day) for x in prev[-10:]])) - 0.5
            pool = {x[13] for x in prev[-20:] if x[13]}
            f["champ_pool"] = len(pool) / max(1, min(20, len(prev)))
        # pace: both teams' time-weighted game length and combined kills per minute
        def tpace(team):
            g = [x for x in self.tm.get(team, ()) if x[0] < day and x[3]]
            if not g:
                return self.len_avg, self.ckpm_avg
            ww = np.array([math.exp(-(day - x[0]) / HL) for x in g])
            ln = (np.dot(ww, [x[3] for x in g]) + 5 * self.len_avg) / (ww.sum() + 5)
            ck = (np.dot(ww, [(x[1] + x[2]) / x[3] for x in g]) + 5 * self.ckpm_avg) / (ww.sum() + 5)
            return ln, ck
        (l1, c1), (l2, c2) = tpace(t["team"]), tpace(t["opp"])
        f["pace_len"] = math.log(((l1 + l2) / 2) / self.len_avg)
        f["pace_ckpm"] = math.log(((c1 + c2) / 2) / self.ckpm_avg)
        f["script_stomp"] = 4 * ((t.get("p_win") or 0.5) - 0.5) ** 2
        # team form, stage, league
        tg = [x for x in self.tm.get(t["team"], ()) if x[0] < day and x[4] is not None]
        if tg:
            f["team_form"] = float(np.mean([x[4] for x in tg[-10:]])) - self.team_wr(t["team"], day)
            last = max(x[0] for x in tg)
            f["rest"] = math.log1p(min(30, day - last))
            starters_t = starters.get((t["match"], t["team"])) or set()
            l5 = tg[-5:]
            f["roster"] = float(np.mean([starters_t <= self.lineup[(x[5], t["team"])] for x in l5])) if starters_t else 0.0
        g1 = t.get("_g1") or {}
        f["playoffs"] = 1.0 if "playoff" in str(g1.get("tournament") or "").lower() else 0.0
        f.update({k: float(v) for k, v in _lg(t.get("league")).items()})
        # patch: the league's current patch (its latest game before the day)
        lgs = [p for dd, p in self.league_games.get(t.get("league"), ()) if dd < day]
        if lgs:
            cur = lgs[-1]
            f["patch_age"] = math.log1p(max(0, day - self.patch_first.get(cur, day)))
            f["patch_share"] = float(np.mean([x[14] == cur for x in prev[-10:]])) if prev else 0.0
        else:
            f["patch_missing"] = 1.0
        # lane matchup: the opponent's starter in the same role
        opp_pid = starters.get((t["match"], t["opp"], ro))
        if opp_pid:
            op = [x for x in self.pl.get(opp_pid, ()) if x[0] < day]
            if op:
                ow = np.array([math.exp(-(day - x[0]) / HL) for x in op])
                od = (np.dot(ow, [x[2] for x in op]) + 5 * ra.get("deaths", 3)) / (ow.sum() + 5)
                ok = (np.dot(ow, [x[1] for x in op]) + 5 * ra.get("kills", 3)) / (ow.sum() + 5)
                f["lane_opp_deaths"] = math.log(od / ra.get("deaths", 3))
                f["lane_opp_kills"] = math.log(ok / ra.get("kills", 3))
            else:
                f["lane_missing"] = 1.0
        else:
            f["lane_missing"] = 1.0
        return f


def units(st, stat):
    """(target, scope, y, base mean, size, features) for every target in F-TRAIN and F-VALID."""
    d = st.data
    p = model.PARAMS[stat]
    pri = dict(model.PRIORS)
    ts = [t for t in d.targets if F_TRAIN[0].toordinal() <= t["day"] < F_VALID[1].toordinal()]
    f = backtest.features(d, ts, p["hl"])
    lam = backtest.lam_game(f, ts, stat, p, pri)
    j = {"kills": 0, "deaths": 1, "assists": 2}[stat]
    out = []
    for i, t in enumerate(ts):
        for scope, (n, bo) in SCOPES.items():
            if bo and t.get("best_of") != bo:
                continue
            if not all(g in t["games"] for g in range(1, n + 1)):
                continue
            y = sum(t["games"][g][j] for g in range(1, n + 1))
            out.append((i, scope, y, float(lam[i]) * n, model.R[(stat, scope)]))
    return ts, out


def nb_ll(y, mu, r):
    return gammaln(y + r) - gammaln(r) - gammaln(y + 1) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))


def fit(X, off, y, r):
    def obj(b):
        eta = off + X @ b
        mu = np.exp(eta)
        ll = nb_ll(y, mu, r).sum()
        g = X.T @ (r * (y - mu) / (r + mu))
        return -ll + RIDGE * b @ b, -g + 2 * RIDGE * b
    res = minimize(obj, np.zeros(X.shape[1]), jac=True, method="L-BFGS-B")
    return res.x


def run(db_path, log=print):
    st = Study(db_path)
    d = st.data
    starters = {}
    for t in d.targets:
        starters.setdefault((t["match"], t["team"]), set()).add(t["player"])
        starters[(t["match"], t["team"], t["role"])] = t["player"]
    first_game = {}
    for r in d.rows:
        if int(r.get("game_number") or 1) == 1:
            first_game[r["match_key"]] = st.game.get(r["game_key"]) or {}
    report = {"periods": {"history_from": HISTORY_FROM.isoformat(), "f_train": [x.isoformat() for x in F_TRAIN],
                          "f_valid": [x.isoformat() for x in F_VALID]}, "ridge": RIDGE, "groups": {k: list(v) for k, v in GROUPS.items()},
              "base": {"version": model.VERSION, "fit": model.FIT_META}, "results": {}, "coefficients": {}}
    feat_cache = {}
    for stat in STATS:
        ts, us = units(st, stat)
        for t in ts:
            t["_g1"] = first_game.get(t["match"])
        rows = []
        for i, scope, y, mu0, r in us:
            k = (ts[i]["match"], ts[i]["player"])
            if k not in feat_cache:
                feat_cache[k] = st.features(ts[i], starters)
            rows.append((ts[i], scope, y, mu0, r, feat_cache[k]))
        tr = [u for u in rows if u[0]["day"] < F_TRAIN[1].toordinal()]
        va = [u for u in rows if u[0]["day"] >= F_VALID[0].toordinal()]
        log(f"{stat}: F-TRAIN units {len(tr)}, F-VALID units {len(va)}")
        Xall = np.array([[u[5][f] for f in FEATS] for u in tr])
        mean, sd = Xall.mean(0), Xall.std(0) + 1e-9
        z = lambda us_: (np.array([[u[5][f] for f in FEATS] for u in us_]) - mean) / sd
        Ztr, Zva = z(tr), z(va)
        off_tr = np.log([u[3] for u in tr]); off_va = np.log([u[3] for u in va])
        y_tr = np.array([u[2] for u in tr], float); y_va = np.array([u[2] for u in va], float)
        r_tr = np.array([u[4] for u in tr]); r_va = np.array([u[4] for u in va])
        res = {}
        for name, cols in list(GROUPS.items()) + [("ALL", tuple(FEATS))]:
            idx = [FEATS.index(c) for c in cols if c in FEATS]
            b = fit(Ztr[:, idx], off_tr, y_tr, r_tr)
            mu_va = np.exp(off_va + Zva[:, idx] @ b)
            mu0 = np.exp(off_va)
            per_scope = {}
            for scope in SCOPES:
                m = np.array([u[1] == scope for u in va])
                if not m.any():
                    continue
                ls1, ls0 = nb_ll(y_va[m], mu_va[m], r_va[m]), nb_ll(y_va[m], mu0[m], r_va[m])
                groups = [u[0]["match"] for u, mm in zip(va, m) if mm]
                g = backtest.grouped_boot(ls1 - ls0, groups, n=2000)
                ae = backtest.grouped_boot(np.abs(mu0[m] - y_va[m]) - np.abs(mu_va[m] - y_va[m]), groups, n=2000)
                per_scope[scope] = {"units": int(m.sum()), "series": len(set(groups)),
                                    "logscore_gain": {"mean": g[0], "lo95": g[1], "hi95": g[2]},
                                    "mae_lol1": float(np.mean(np.abs(mu0[m] - y_va[m]))), "mae_candidate": float(np.mean(np.abs(mu_va[m] - y_va[m]))),
                                    "mae_gain": {"mean": ae[0], "lo95": ae[1], "hi95": ae[2]},
                                    "bias_lol1": float(np.mean(mu0[m] - y_va[m])), "bias_candidate": float(np.mean(mu_va[m] - y_va[m]))}
            s12, s123 = per_scope.get("12"), per_scope.get("123")
            passed = bool(s12 and s123 and s12["logscore_gain"]["lo95"] > 0 and s123["logscore_gain"]["mean"] >= 0
                          and s12["mae_candidate"] <= s12["mae_lol1"])
            res[name] = {"scopes": per_scope, "passes_A7_rule": passed,
                         "beta": {c: round(float(v), 5) for c, v in zip([FEATS[i] for i in idx], b)}}
            log(f"  {name:12} " + " | ".join(f"{s}: LS {v['logscore_gain']['mean']:+.4f} [{v['logscore_gain']['lo95']:+.4f},{v['logscore_gain']['hi95']:+.4f}] "
                                              f"MAE {v['mae_lol1']:.3f}->{v['mae_candidate']:.3f}" for s, v in per_scope.items())
                + ("  PASS" if passed else ""))
        report["results"][stat] = res
        report["coefficients"][stat] = {"feature_mean": dict(zip(FEATS, map(float, mean))), "feature_sd": dict(zip(FEATS, map(float, sd)))}
    return report, st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    rep, _ = run(a.db, log=lambda m: print(m, file=sys.stderr, flush=True))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps({s: {g: v["passes_A7_rule"] for g, v in r.items()} for s, r in rep["results"].items()}))


if __name__ == "__main__":
    main()
