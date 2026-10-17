"""Chronological test of the LoL projection (lol/model.py) on the stored game history - how the model's parameters are
chosen and how good it is, before any grade uses it (the CS2 rule: a factor is display only until it passes a
chronological test).

  TRAIN  matches from --train-from to --test-from: role priors, the form (rate / share), HL / K0 / KO / KT (grid, lowest
         per-game squared error), then the negative binomial size per (prop, scope) by maximum likelihood.
  TEST   matches from --test-from on, never seen by the fit: MAE / bias against two baselines (role average; the
         player's last 10 games), log-likelihood against Poisson, 80% interval coverage and calibration of P(over) at
         the x.5 line nearest the projection.
History before a match = games on EARLIER days only (same as live). The fit is written to lol/model_fit.json
(read by model.py) only with --write.

  python -m lol.backtest                   # fit + report (stdout)
  python -m lol.backtest --write           # ... and save the fit for the live model
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

import numpy as np

from . import config, elo, history, model, teams

PROPS = ("kills", "assists", "deaths")
GRID = {"hl": (60.0, 120.0, 240.0, 480.0, 960.0), "k0": (4.0, 8.0, 16.0, 32.0, 64.0),
        "ko": (5.0, 10.0, 20.0, 40.0, 80.0, 160.0), "kt": (5.0, 10.0, 20.0, 40.0, 80.0), "ew": (0.0, 0.5, 1.0, 1.5)}
FIT_FILE = config.PKG_DIR / "model_fit.json"
# where the books post most LoL props (a TEST breakdown, not a filter)
TOP_LEAGUES = {"worlds", "msi", "first_stand", "lck", "lpl", "lec", "lcs", "lcp", "cblol-brazil", "lta_n", "lta_s", "ewc_lol",
               "demacia_cup"}


def _day(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).date().toordinal()


class Data:
    """Stored player-games arranged for the test: per-player and per-team game arrays, and one target per
    (match, player) with his per-game results in that match."""

    def __init__(self, rows):
        self.rows = [r for r in rows if r.get("played_at") and r.get("role") and r.get("kills") is not None
                     and r.get("team_kills") is not None and r.get("opp_kills") is not None]
        by_match = defaultdict(list)
        for r in self.rows:
            by_match[r["match_key"]].append(r)
        self.player = defaultdict(list)       # pid (history._dedup) -> [(day, k, d, a, tk, ok, win 0/1, win known 0/1)]
        team_seen = set()
        self.team = defaultdict(list)         # teams.key(team) -> [(day, team_kills, opp_kills)]
        self.match_day = {}
        self.targets = []
        for mk, rs in by_match.items():
            d0 = min(_day(r["played_at"]) for r in rs)
            self.match_day[mk] = d0
            per_player = defaultdict(dict)
            for r in rs:
                win = r.get("win")
                self.player[r["pid"]].append((d0, r["kills"], r["deaths"], r["assists"], r["team_kills"], r["opp_kills"],
                                              win or 0, 0 if win is None else 1))
                if (r["game_key"], r["team"]) not in team_seen:
                    team_seen.add((r["game_key"], r["team"]))
                    self.team[teams.key(r["team"])].append((d0, r["team_kills"], r["opp_kills"]))
                if r.get("game_number"):
                    per_player[r["pid"]][int(r["game_number"])] = r
            for pk, games in per_player.items():
                g1 = games.get(1) or games[min(games)]
                self.targets.append({"match": mk, "player": pk, "day": d0, "team": teams.key(g1["team"]),
                                     "opp": teams.key(g1["opponent"]), "team_name": g1["team"], "opp_name": g1["opponent"],
                                     "role": g1["role"], "league": g1.get("league"), "best_of": g1.get("best_of"),
                                     "games": {n: (r["kills"], r["deaths"], r["assists"]) for n, r in games.items()}})
        self.arr = {k: np.array(sorted(v), dtype=float) for k, v in self.player.items()}
        self.tarr = {k: np.array(sorted(v), dtype=float) for k, v in self.team.items()}
        self.elo = elo.from_rows(self.rows, lambda r: self.match_day[r["match_key"]])
        for t in self.targets:
            t["p_win"] = self.elo.p_win(t["team_name"], t["opp_name"], t["day"])

    def priors(self, d_from, d_to):
        tot, n, tk = defaultdict(lambda: defaultdict(float)), defaultdict(int), []
        seen = set()
        for r in self.rows:
            d = _day(r["played_at"])
            if not d_from <= d < d_to:
                continue
            for p in PROPS:
                tot[p][r["role"]] += r[p]
            n[r["role"]] += 1
            if (r["game_key"], r["team"]) not in seen:
                seen.add((r["game_key"], r["team"]))
                tk.append(r["team_kills"])
        out = {p: {role: round(tot[p][role] / n[role], 4) for role in model.ROLES if n[role]} for p in PROPS}
        out["team_kills"] = round(sum(tk) / len(tk), 4)
        wl = {p: [[0.0, 0], [0.0, 0]] for p in PROPS}        # per prop: [sum, n] in losses, in wins
        for r in self.rows:
            if r.get("win") is not None and d_from <= _day(r["played_at"]) < d_to:
                for p in PROPS:
                    wl[p][int(r["win"])][0] += r[p]
                    wl[p][int(r["win"])][1] += 1
        out["win_ratio"] = {p: round((v[1][0] / max(v[1][1], 1)) / max(v[0][0] / max(v[0][1], 1), 1e-9), 4)
                            for p, v in wl.items()}
        return out


def _wsums(arr, days, hl, cols):
    """For each target day: sum of w and sum of w * column, over rows strictly before that day."""
    if arr is None or not len(arr):
        z = np.zeros(len(days))
        return z, [z.copy() for _ in cols]
    dd = days[:, None] - arr[None, :, 0]
    w = np.where(dd > 0, np.exp(-np.clip(dd, 0, None) / hl), 0.0)
    return w.sum(1), [w @ arr[:, c] for c in cols]


def features(data, targets, hl):
    """Per target, the weighted sums the model needs at half-life hl (computed once per hl, reused across K0/KO/KT)."""
    by_p = defaultdict(list)
    for i, t in enumerate(targets):
        by_p[t["player"]].append(i)
    n = len(targets)
    f = {k: np.zeros(n) for k in ("pw", "pk", "pd", "pa", "ptk", "pok", "pwin", "pwk", "tw", "ttk", "tok", "ow", "otk",
                                  "ook", "last10k", "last10d", "last10a", "hist")}
    f["p_win"] = np.array([t["p_win"] for t in targets])
    for pk, idx in by_p.items():
        arr = data.arr.get(pk)
        days = np.array([targets[i]["day"] for i in idx], dtype=float)
        w, (k, d, a, tk, ok, wins, known) = _wsums(arr, days, hl, (1, 2, 3, 4, 5, 6, 7))
        for j, i in enumerate(idx):
            f["pw"][i], f["pk"][i], f["pd"][i], f["pa"][i], f["ptk"][i], f["pok"][i] = w[j], k[j], d[j], a[j], tk[j], ok[j]
            f["pwin"][i], f["pwk"][i] = wins[j], known[j]
            prev = arr[arr[:, 0] < days[j]] if arr is not None else np.zeros((0, 8))
            f["hist"][i] = len(prev)
            if len(prev):
                last = prev[-10:]
                f["last10k"][i], f["last10d"][i], f["last10a"][i] = last[:, 1].mean(), last[:, 2].mean(), last[:, 3].mean()
            else:
                f["last10k"][i] = f["last10d"][i] = f["last10a"][i] = np.nan
    for key, prefix, field in (("team", "t", "team"), ("opp", "o", "opp")):
        groups = defaultdict(list)
        for i, t in enumerate(targets):
            groups[t[field]].append(i)
        for name, idx in groups.items():
            days = np.array([targets[i]["day"] for i in idx], dtype=float)
            w, (tk, ok) = _wsums(data.tarr.get(name), days, hl, (1, 2))
            f[prefix + "w"][idx], f[prefix + "tk"][idx], f[prefix + "ok"][idx] = w, tk, ok
    return f


def lam_game(f, targets, prop, p, pri):
    """The model's per-game lambda for every target, vectorised (the same arithmetic as model.project)."""
    tk = pri["team_kills"]
    role = np.array([pri[prop].get(t["role"], np.mean(list(pri[prop].values()))) for t in targets])
    x = {"kills": f["pk"], "deaths": f["pd"], "assists": f["pa"]}[prop]
    pool = f["ptk"] if model.POOL[prop] == "team_kills" else f["pok"]
    opp_x = f["ook"] if model.POOL[prop] == "team_kills" else f["otk"]
    opp = ((opp_x + p["ko"] * tk) / (f["ow"] + p["ko"])) / tk
    if p["form"] == "share":
        share = (x + p["k0"] * role) / (pool + p["k0"] * tk)
        team_x = f["ttk"] if model.POOL[prop] == "team_kills" else f["tok"]
        team = ((team_x + p["kt"] * tk) / (f["tw"] + p["kt"])) / tk
        lam = share * tk * team * opp
    else:
        lam = (x + p["k0"] * role) / (f["pw"] + p["k0"]) * opp
    if p.get("ew"):
        ratio = pri["win_ratio"][prop]
        w_bar = (f["pwin"] + p["k0"] * 0.5) / (f["pwk"] + p["k0"])
        lam = lam * ((f["p_win"] * ratio + 1 - f["p_win"]) / (w_bar * ratio + 1 - w_bar)) ** p["ew"]
    return lam


def _actual_games(targets, prop):
    """Every game of every target, flattened: (target index per game, the game's value)."""
    j = {"kills": 0, "deaths": 1, "assists": 2}[prop]
    idx, val = [], []
    for i, t in enumerate(targets):
        for _, g in sorted(t["games"].items()):
            idx.append(i)
            val.append(g[j])
    return np.array(idx, dtype=int), np.array(val, dtype=float)


def per_game_err(lam, games, sq=True):
    """Mean squared (or absolute) per-game error - squared for the fit: lambda is the distribution's MEAN, and squared
    error is minimised by the mean (absolute error by the median, which sits below the mean for these skewed counts)."""
    idx, val = games
    d = lam[idx] - val
    return float((d * d).mean() if sq else np.abs(d).mean())


def series(targets, prop, scope):
    """(indices, totals) of targets where the player played every game of the scope (games 1..n)."""
    n = model.SCOPE_GAMES[scope]
    j = {"kills": 0, "deaths": 1, "assists": 2}[prop]
    idx, tot = [], []
    for i, t in enumerate(targets):
        if all(g in t["games"] for g in range(1, n + 1)):
            idx.append(i)
            tot.append(sum(t["games"][g][j] for g in range(1, n + 1)))
    return np.array(idx, dtype=int), np.array(tot, dtype=float)


def fit_size(lam, y):
    best = None
    for size in np.exp(np.linspace(math.log(0.5), math.log(500), 120)):
        ll = sum(model.nb_logpmf(int(k), m, size) for k, m in zip(y, lam))
        if best is None or ll > best[0]:
            best = (ll, float(size))
    return round(best[1], 3)


def evaluate(lam, y, size, base=None):
    """Held-out numbers for one (prop, scope)."""
    out = {"n": int(len(y)), "mean_actual": float(y.mean()), "mean_projection": float(lam.mean()),
           "bias": float((lam - y).mean()), "mae": float(np.abs(lam - y).mean())}
    for name, b in (base or {}).items():
        ok = ~np.isnan(b)
        out[f"mae_{name}"] = float(np.abs(b[ok] - y[ok]).mean())
        out[f"mae_model_same_rows_{name}"] = float(np.abs(lam[ok] - y[ok]).mean())
    ll_nb = np.mean([model.nb_logpmf(int(k), m, size) for k, m in zip(y, lam)])
    ll_po = np.mean([int(k) * math.log(m) - m - math.lgamma(int(k) + 1) for k, m in zip(y, lam)])
    out["loglik_nb"], out["loglik_poisson"] = float(ll_nb), float(ll_po)
    inside, bins = 0, defaultdict(lambda: [0, 0.0, 0])
    for k, m in zip(y, lam):
        lo, hi = model.interval(m, size, 0.8)
        inside += lo <= k <= hi
        line = math.floor(m) + 0.5
        po, _, _ = model.probs(m, size, line)
        b = min(int(po * 10), 9)
        bins[b][0] += 1
        bins[b][1] += po
        bins[b][2] += int(k > line)
    out["cover80"] = inside / len(y)
    out["calibration"] = [{"bin": f"{b / 10:.1f}-{(b + 1) / 10:.1f}", "n": v[0], "predicted_over": round(v[1] / v[0], 3),
                           "actual_over": round(v[2] / v[0], 3)} for b, v in sorted(bins.items()) if v[0] >= 20]
    return out


def run(data, train_from, test_from, test_to=None, log=print):
    d_tr, d_te = train_from.toordinal(), test_from.toordinal()
    d_end = (test_to or date(2100, 1, 1)).toordinal()
    pri = data.priors(d_tr, d_te)
    train = [t for t in data.targets if d_tr <= t["day"] < d_te]
    test = [t for t in data.targets if d_te <= t["day"] < d_end]
    log(f"targets: train {len(train)} (match-players), test {len(test)}; priors {json.dumps(pri)}")
    feats_tr = {hl: features(data, train, hl) for hl in GRID["hl"]}
    fit = {"priors": pri, "params": {}, "nb_size": {}, "train": [train_from.isoformat(), test_from.isoformat()],
           "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": model.VERSION}
    report = {}
    for prop in PROPS:
        games_tr = _actual_games(train, prop)
        best = None
        for form in ("rate", "share"):
            for hl in GRID["hl"]:
                for k0 in GRID["k0"]:
                    for ko in GRID["ko"]:
                        for kt in (GRID["kt"] if form == "share" else (0.0,)):
                            for ew in GRID["ew"]:
                                p = {"form": form, "hl": hl, "k0": k0, "ko": ko, "kt": kt, "ew": ew}
                                mse = per_game_err(lam_game(feats_tr[hl], train, prop, p, pri), games_tr)
                                if best is None or mse < best[0]:
                                    best = (mse, p)
        no_strength = per_game_err(lam_game(feats_tr[best[1]["hl"]], train, prop, dict(best[1], ew=0.0), pri), games_tr)
        p = best[1]
        fit["params"][prop] = p
        log(f"{prop}: best on TRAIN {p} (per-game MSE {best[0]:.4f}; same without match strength {no_strength:.4f})")
        lam_tr = lam_game(feats_tr[p["hl"]], train, prop, p, pri)
        for scope, n in model.SCOPE_GAMES.items():
            idx, y = series(train, prop, scope)
            fit["nb_size"][f"{prop}|{scope}"] = fit_size(lam_tr[idx] * n, y) if len(y) else 8.0
        f_te = features(data, test, p["hl"])
        lam_te = lam_game(f_te, test, prop, p, pri)
        lam_te0 = lam_game(f_te, test, prop, dict(p, ew=0.0), pri)
        role_base = np.array([pri[prop].get(t["role"], np.nan) for t in test])
        last10 = f_te[{"kills": "last10k", "deaths": "last10d", "assists": "last10a"}[prop]]
        for scope, n in model.SCOPE_GAMES.items():
            idx, y = series(test, prop, scope)
            if not len(y):
                continue
            size = fit["nb_size"][f"{prop}|{scope}"]
            report[(prop, scope)] = evaluate(lam_te[idx] * n, y, size,
                                             {"role_average": role_base[idx] * n, "last10": last10[idx] * n,
                                              "no_strength": lam_te0[idx] * n})
            if prop == "kills" and scope == "12":
                by_role = {}
                for role in model.ROLES:
                    sel = np.array([test[i]["role"] == role for i in idx])
                    if sel.sum() >= 30:
                        by_role[role] = evaluate(lam_te[idx][sel] * n, y[sel], size,
                                                 {"role_average": role_base[idx][sel] * n})
                for group, member in (("top leagues", True), ("other leagues", False)):
                    sel = np.array([(test[i]["league"] in TOP_LEAGUES) == member for i in idx])
                    if sel.sum() >= 30:
                        by_role[group] = evaluate(lam_te[idx][sel] * n, y[sel], size,
                                                  {"role_average": role_base[idx][sel] * n})
                report[("kills", "12", "by_role")] = by_role
    return fit, report


def fmt(report):
    lines = []
    for key, r in report.items():
        if len(key) == 3:
            lines.append("\nkills Maps 1-2 by role and league group:")
            for role, v in r.items():
                lines.append(f"  {role:8s} n={v['n']:5d}  avg {v['mean_actual']:5.2f}  bias {v['bias']:+.2f}  "
                             f"MAE {v['mae']:.3f} (role avg {v['mae_role_average']:.3f})  80% range {v['cover80']:.1%}")
            continue
        prop, scope = key
        lines.append(f"\n{prop} scope {scope}: n={r['n']}  avg actual {r['mean_actual']:.2f}  avg projection "
                     f"{r['mean_projection']:.2f}  bias {r['bias']:+.3f}")
        lines.append(f"  MAE model {r['mae']:.3f} | without match strength {r['mae_no_strength']:.3f} | role average "
                     f"{r['mae_role_average']:.3f} | last 10 games {r['mae_last10']:.3f} (model on the same rows "
                     f"{r['mae_model_same_rows_last10']:.3f})")
        lines.append(f"  log-likelihood NB {r['loglik_nb']:.4f} vs Poisson {r['loglik_poisson']:.4f}; "
                     f"80% range holds {r['cover80']:.1%}")
        for c in r["calibration"]:
            lines.append(f"    P(over) {c['bin']}: n={c['n']:5d}  predicted {c['predicted_over']:.3f}  "
                         f"actual {c['actual_over']:.3f}")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-from", default="2025-03-01")
    ap.add_argument("--test-from", default="2026-04-01")
    ap.add_argument("--test-to")
    ap.add_argument("--write", action="store_true", help="save the TRAIN fit to lol/model_fit.json")
    ap.add_argument("--json", help="also save the full report as JSON here")
    a = ap.parse_args(argv)
    db = history.DB()
    data = Data(history.all_player_games(db))
    log = lambda m: print(m, file=sys.stderr, flush=True)
    fit, report = run(data, date.fromisoformat(a.train_from), date.fromisoformat(a.test_from),
                      date.fromisoformat(a.test_to) if a.test_to else None, log)
    print(json.dumps({k: v for k, v in fit.items()}, indent=1))
    print(fmt(report))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"fit": fit, "report": {"|".join(k): v for k, v in report.items()}}, fh, indent=1)
    if a.write:
        FIT_FILE.write_text(json.dumps(fit, indent=1) + "\n", encoding="utf-8")
        print(f"\nsaved {FIT_FILE}", file=sys.stderr)


# ------------------------------------------------------------------ lol/EVALUATION.md amendments A1-A3: B0 vs lol-1
SCOPE_FORMAT = {"1": None, "12": 3, "123": 5}        # A3: Maps 1-2 = best-of-3, Maps 1-3 = best-of-5 (owner 2026-10-17)
B0_K = (2.0, 5.0, 10.0, 20.0, 40.0)
A1_PERIODS = {"history_from": date(2026, 2, 16), "train_from": date(2026, 4, 1), "test_from": date(2026, 8, 1),
              "test_to": date(2026, 10, 17)}


def series_fmt(targets, prop, scope):
    """(indices, totals): targets of the scope's series format where the player played every game of the scope."""
    n, bo = model.SCOPE_GAMES[scope], SCOPE_FORMAT[scope]
    j = {"kills": 0, "deaths": 1, "assists": 2}[prop]
    idx, tot = [], []
    for i, t in enumerate(targets):
        if bo and t.get("best_of") != bo:
            continue
        if all(g in t["games"] for g in range(1, n + 1)):
            idx.append(i)
            tot.append(sum(t["games"][g][j] for g in range(1, n + 1)))
    return np.array(idx, dtype=int), np.array(tot, dtype=float)


def role_league_priors(data, d_from, d_to):
    """{prop: {"league|role": [TRAIN mean per game, games]}} - B0's prior."""
    tot = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for r in data.rows:
        if d_from <= _day(r["played_at"]) < d_to:
            key = "%s|%s" % (r.get("league"), r["role"])
            for p in PROPS:
                c = tot[p][key]
                c[0] += r[p]
                c[1] += 1
    return {p: {k: [round(v[0] / v[1], 4), v[1]] for k, v in d.items()} for p, d in tot.items()}


def b0_lam(f_unweighted, targets, prop, k0, pri):
    x = {"kills": f_unweighted["pk"], "deaths": f_unweighted["pd"], "assists": f_unweighted["pa"]}[prop]
    prior = np.array([model.baseline_prior(pri, prop, t["role"], t.get("league")) for t in targets])
    return (x + k0 * prior) / (f_unweighted["pw"] + k0)


def _logscores(lam, y, size):
    return np.array([model.nb_logpmf(int(k), m, size) for k, m in zip(y, lam)])


def grouped_boot(diff, groups, n=2000, seed=11):
    """Series-grouped bootstrap of the mean of `diff` -> (mean, lo95, hi95, units, groups)."""
    by = defaultdict(list)
    for d, g in zip(diff, groups):
        by[g].append(d)
    keys = list(by)
    sums = np.array([sum(by[k]) for k in keys])
    cnts = np.array([len(by[k]) for k in keys])
    rng = np.random.default_rng(seed)
    est = np.sort([sums[i].sum() / cnts[i].sum() for i in (rng.integers(0, len(keys), len(keys)) for _ in range(n))])
    return float(sums.sum() / cnts.sum()), float(est[int(0.025 * n)]), float(est[int(0.975 * n) - 1]), int(cnts.sum()), len(keys)


def _breakdown(sel_of, keys, l1, l0, y, ls1, ls0, min_n):
    out = {}
    for k in keys:
        sel = np.array([sel_of(i) == k for i in range(len(y))])
        if sel.sum() >= min_n:
            out[str(k)] = {"n": int(sel.sum()), "mae_lol1": round(float(np.abs(l1[sel] - y[sel]).mean()), 4),
                           "mae_b0": round(float(np.abs(l0[sel] - y[sel]).mean()), 4),
                           "logscore_gain": round(float((ls1[sel] - ls0[sel]).mean()), 4)}
    return out


def compare(data, periods=None, log=print):
    """B0 vs lol-1 exactly as amendments A1-A3: same targets, same day cutoff, same scopes; parameters and NB sizes on
    TRAIN; TEST reported once. Returns (fit of the selected forms, report)."""
    P = dict(A1_PERIODS, **(periods or {}))
    d_tr, d_te, d_end = (P[k].toordinal() for k in ("train_from", "test_from", "test_to"))
    pri = data.priors(d_tr, d_te)
    pri["role_league"] = role_league_priors(data, d_tr, d_te)
    train = [t for t in data.targets if d_tr <= t["day"] < d_te]
    test = [t for t in data.targets if d_te <= t["day"] < d_end]
    log("A1: targets train %d, test %d (match-players)" % (len(train), len(test)))
    feats_tr = {hl: features(data, train, hl) for hl in GRID["hl"]}
    fu_tr, fu_te = features(data, train, 1e9), features(data, test, 1e9)
    fit = {"priors": pri, "params": {}, "nb_size": {}, "train": [P["train_from"].isoformat(), P["test_from"].isoformat()],
           "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "version": model.VERSION,
           "protocol": "lol/EVALUATION.md A1-A3", "selection": {}}
    report = {"periods": {k: v.isoformat() for k, v in P.items()}, "n_train_targets": len(train), "n_test_targets": len(test),
              "stats": {}}
    for prop in PROPS:
        games_tr = _actual_games(train, prop)
        best = None                                                    # lol-1: the same grid and objective as run()
        for form in ("rate", "share"):
            for hl in GRID["hl"]:
                for k0 in GRID["k0"]:
                    for ko in GRID["ko"]:
                        for kt in (GRID["kt"] if form == "share" else (0.0,)):
                            for ew in GRID["ew"]:
                                p = {"form": form, "hl": hl, "k0": k0, "ko": ko, "kt": kt, "ew": ew}
                                mse = per_game_err(lam_game(feats_tr[hl], train, prop, p, pri), games_tr)
                                if best is None or mse < best[0]:
                                    best = (mse, p)
        p1 = best[1]
        b0 = min((per_game_err(b0_lam(fu_tr, train, prop, k, pri), games_tr), k) for k in B0_K)
        p0 = {"form": "baseline", "k0": b0[1], "hl": None, "ko": None, "kt": None, "ew": 0.0}
        lam1_tr, lam0_tr = lam_game(feats_tr[p1["hl"]], train, prop, p1, pri), b0_lam(fu_tr, train, prop, p0["k0"], pri)
        f1_te = features(data, test, p1["hl"])
        lam1_te, lam0_te = lam_game(f1_te, test, prop, p1, pri), b0_lam(fu_te, test, prop, p0["k0"], pri)
        out = {"lol1_params": p1, "lol1_train_mse": round(best[0], 4), "b0_params": p0, "b0_train_mse": round(b0[0], 4), "scopes": {}}
        sizes = {}
        for scope, n in model.SCOPE_GAMES.items():
            i_tr, y_tr = series_fmt(train, prop, scope)
            s1 = fit_size(lam1_tr[i_tr] * n, y_tr) if len(y_tr) else 8.0
            s0 = fit_size(lam0_tr[i_tr] * n, y_tr) if len(y_tr) else 8.0
            sizes[scope] = (s1, s0)
            i_te, y = series_fmt(test, prop, scope)
            if not len(y):
                out["scopes"][scope] = {"n": 0}
                continue
            l1, l0 = lam1_te[i_te] * n, lam0_te[i_te] * n
            ls1, ls0 = _logscores(l1, y, s1), _logscores(l0, y, s0)
            boot = grouped_boot(ls1 - ls0, [test[i]["match"] for i in i_te])
            e1, e0 = evaluate(l1, y, s1), evaluate(l0, y, s0)
            leagues = sorted({str(test[i].get("league")) for i in i_te})
            out["scopes"][scope] = {
                "format": SCOPE_FORMAT[scope], "n": int(len(y)), "series": boot[4], "nb_size_lol1": s1, "nb_size_b0": s0,
                "lol1": {k: e1[k] for k in ("mae", "bias", "loglik_nb", "cover80", "calibration")},
                "b0": {k: e0[k] for k in ("mae", "bias", "loglik_nb", "cover80", "calibration")},
                "logscore_gain_lol1_minus_b0": {"mean": boot[0], "lo95": boot[1], "hi95": boot[2]},
                "by_league": _breakdown(lambda j: str(test[i_te[j]].get("league")), leagues, l1, l0, y, ls1, ls0, 50),
                "by_role": _breakdown(lambda j: test[i_te[j]]["role"], model.ROLES, l1, l0, y, ls1, ls0, 30)}
        ok = all(out["scopes"].get(sc, {}).get("n") and out["scopes"][sc]["logscore_gain_lol1_minus_b0"]["lo95"] > 0
                 and out["scopes"][sc]["lol1"]["mae"] <= out["scopes"][sc]["b0"]["mae"] for sc in ("12", "123"))
        chosen = "lol-1" if ok else "B0"
        fit["selection"][prop] = chosen
        fit["params"][prop] = p1 if ok else p0
        for scope, (s1, s0) in sizes.items():
            fit["nb_size"]["%s|%s" % (prop, scope)] = s1 if ok else s0
        out["selected"] = chosen
        report["stats"][prop] = out
        log("%s: selected %s" % (prop, chosen))
    return fit, report


def main_compare(argv=None):
    ap = argparse.ArgumentParser(description="A1-A3: B0 vs lol-1 on the consolidated 8-month history")
    ap.add_argument("--write", action="store_true", help="save the selected TRAIN fit to lol/model_fit.json")
    ap.add_argument("--json", help="save the report here")
    a = ap.parse_args(argv)
    db = history.DB()
    data = Data(history.all_player_games(db, since=A1_PERIODS["history_from"].isoformat()))
    fit, report = compare(data, log=lambda m: print(m, file=sys.stderr, flush=True))
    out = json.dumps({"fit": fit, "report": report}, indent=1, default=str)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(out)
    else:
        print(out)
    if a.write:
        FIT_FILE.write_text(json.dumps(fit, indent=1) + "\n", encoding="utf-8")
        print("saved %s" % FIT_FILE, file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "compare":
        main_compare(sys.argv[2:])
    else:
        main()
