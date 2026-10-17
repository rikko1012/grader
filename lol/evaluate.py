"""
lol/EVALUATION.md amendment A4: B0, lol-1 and lol-2 on identical targets, the same day cutoff and the A3 scopes.

  python -m lol.evaluate dev [--json out]      DEV-TRAIN fit, VALID scored (development; may be repeated)
  python -m lol.evaluate final [--json out]    DEV-TRAIN + VALID fit, TEST scored ONCE - writes lol/test_opened.json

Targets: one (series, player) per scope - Map 1 (any format), Maps 1-2 (best-of-3), Maps 1-3 (best-of-5) - where the
player played every game of the scope. Lineups: the players of the series' game 1 (announced starters); nothing else
from the series is used. Metrics per (stat, scope): MAE, squared error, bias; mean log score of the total; Brier and
log-loss of P(over) at the development line (the player's median total of the same scope over up to 10 earlier
comparable series, at least 3, rounded to x.5 - model-independent, known before the match); calibration; 80% interval
coverage; series-grouped bootstrap intervals of the differences.
"""
import argparse
import json
import math
import sys
import zlib
from collections import defaultdict
from datetime import date, datetime, timezone

import numpy as np

from . import backtest, config, elo, history, model, sim

PERIODS = {"dev": {"history_from": date(2026, 2, 16), "train_from": date(2026, 4, 1), "eval_from": date(2026, 6, 15),
                   "eval_to": date(2026, 8, 1)},
           "final": {"history_from": date(2026, 2, 16), "train_from": date(2026, 4, 1), "eval_from": date(2026, 8, 1),
                     "eval_to": date(2026, 10, 17)}}
STATS = ("kills", "assists", "deaths")
SCOPES = {"1": (1, None), "12": (2, 3), "123": (3, 5)}
TEST_MARK = config.PKG_DIR / "test_opened.json"


def dev_line(prev_totals):
    if len(prev_totals) < 3:
        return None
    return math.floor(float(np.median(prev_totals[-10:]))) + 0.5


def run(rows, which="dev", sims=2000, log=print, lol2=None):
    P = PERIODS[which]
    d_tr, d_ev, d_end = (P[k].toordinal() for k in ("train_from", "eval_from", "eval_to"))
    data = backtest.Data(rows)
    pri = data.priors(d_tr, d_ev)
    pri["role_league"] = backtest.role_league_priors(data, d_tr, d_ev)
    train = [t for t in data.targets if d_tr <= t["day"] < d_ev]
    test = [t for t in data.targets if d_ev <= t["day"] < d_end]
    log(f"{which}: train targets {len(train)}, scored targets {len(test)}")
    # ---- B0 and lol-1 (the backtest's own code: same grids, objective, NB sizes per (stat, scope) on the fit period)
    fit_b, rep_b = backtest.compare(data, {"train_from": P["train_from"], "test_from": P["eval_from"], "test_to": P["eval_to"]},
                                    log=lambda m: None)
    feats = {}
    preds = {"B0": {}, "lol-1": {}}
    fu = backtest.features(data, test, 1e9)
    for prop in STATS:
        p1, p0 = rep_b["stats"][prop]["lol1_params"], rep_b["stats"][prop]["b0_params"]
        if p1["hl"] not in feats:
            feats[p1["hl"]] = backtest.features(data, test, p1["hl"])
        l1 = backtest.lam_game(feats[p1["hl"]], test, prop, p1, pri)
        l0 = backtest.b0_lam(fu, test, prop, p0["k0"], pri)
        for scope in SCOPES:
            s1 = rep_b["stats"][prop]["scopes"].get(scope, {}).get("nb_size_lol1") or 8.0
            s0 = rep_b["stats"][prop]["scopes"].get(scope, {}).get("nb_size_b0") or 8.0
            for i, t in enumerate(test):
                n = SCOPES[scope][0]
                preds["lol-1"][(t["match"], t["player"], prop, scope)] = (l1[i] * n, s1)
                preds["B0"][(t["match"], t["player"], prop, scope)] = (l0[i] * n, s0)
    samples, params = lol2_samples(data, test, d_tr, d_ev, d_end, lol2 or {}, sims, log)
    # ---- score: identical targets for every model (a target missing any model's prediction is dropped and counted)
    report = {"which": which, "periods": {k: v.isoformat() for k, v in P.items()}, "lol2_params": {k: v for k, v in params.items() if k != "priors"},
              "b0_lol1_params": {p: {"lol-1": rep_b["stats"][p]["lol1_params"], "B0": rep_b["stats"][p]["b0_params"]} for p in STATS},
              "results": {},
              "market_variant": "not evaluable: no timestamped pre-match prices exist for the evaluated period (the prop-line "
                                "archive starts 2026-10-17; no saved match-odds history) - evaluated forward only"}
    hist = _comparable_totals(data)
    unit_rows = []
    for prop in STATS:
        j = {"kills": 0, "deaths": 1, "assists": 2}[prop]
        for scope, (n, bo) in SCOPES.items():
            units = []
            dropped = unknown_bo = 0
            for t in test:
                if bo and t.get("best_of") is None:
                    unknown_bo += 1
                    continue
                if bo and t.get("best_of") != bo:
                    continue
                if not all(g in t["games"] for g in range(1, n + 1)):
                    continue
                y = sum(t["games"][g][j] for g in range(1, n + 1))
                k = (t["match"], t["player"], prop, scope)
                if k not in samples or k not in preds["B0"]:
                    dropped += 1
                    continue
                line = dev_line([v for d, v in hist[(t["player"], prop, scope)] if d < t["day"]])
                units.append((t, y, k, line))
                prev_tot = [v for d, v in hist[(t["player"], prop, scope)] if d < t["day"]]
                unit_rows.append({"match": t["match"], "player": t["player"], "day": t["day"], "role": t["role"],
                                  "league": t.get("league"), "team": t["team"], "opp": t["opp"], "best_of": t.get("best_of"),
                                  "p_win": t.get("p_win"), "stat": prop, "scope": scope, "y": y, "line": line, "prev": prev_tot,
                                  "pred": {"B0": preds["B0"][k], "lol-1": preds["lol-1"][k],
                                           "lol-2": nb_from_moments(*samples[k])}})
            if not units:
                continue
            res = {"n": len(units), "series": len({u[0]["match"] for u in units}), "dropped_missing_prediction": dropped,
                   "dropped_unknown_best_of": unknown_bo}
            per = {}
            for name in ("B0", "lol-1", "lol-2"):
                mae, se, bias, ls, cov, brier, ll, cal, mass = [], [], [], [], [], [], [], [], []
                for t, y, k, line in units:
                    if name == "lol-2":
                        mean, size = nb_from_moments(*samples[k])
                    else:
                        mean, size = preds[name][k]
                    ls.append(model.nb_logpmf(int(y), mean, size))
                    lo, hi = model.interval(mean, size, 0.8)
                    mass.append(sum(model.nb_pmf(v, mean, size) for v in range(int(lo), int(hi) + 1)))
                    po = model.probs(mean, size, line)[0] if line is not None else None
                    mae.append(abs(mean - y))
                    se.append((mean - y) ** 2)
                    bias.append(mean - y)
                    cov.append(lo <= y <= hi)
                    if po is not None:
                        hit = 1.0 if y > line else 0.0
                        pc = min(max(po, 1e-4), 1 - 1e-4)
                        brier.append((po - hit) ** 2)
                        ll.append(-(hit * math.log(pc) + (1 - hit) * math.log(1 - pc)))
                        cal.append((po, hit))
                per[name] = {"mae": float(np.mean(mae)), "mse": float(np.mean(se)), "bias": float(np.mean(bias)),
                             "logscore": float(np.mean(ls)), "cover80": float(np.mean(cov)),
                             "cover80_expected": float(np.mean(mass)), "n_lines": len(brier),
                             "_ae": mae, "_br": [None] * 0 + brier,
                             "brier": float(np.mean(brier)) if brier else None, "logloss": float(np.mean(ll)) if ll else None,
                             "calibration": _cal(cal), "_ls": ls}
            groups = [u[0]["match"] for u in units]
            for a, b in (("lol-1", "B0"), ("lol-2", "B0"), ("lol-2", "lol-1")):
                d = np.array(per[a]["_ls"]) - np.array(per[b]["_ls"])
                bt = backtest.grouped_boot(d, groups)
                res[f"logscore_gain_{a}_vs_{b}"] = {"mean": bt[0], "lo95": bt[1], "hi95": bt[2]}
            for a, b in (("lol-1", "B0"), ("lol-2", "B0"), ("lol-2", "lol-1")):
                d = np.array(per[b]["_ae"]) - np.array(per[a]["_ae"])
                bt = backtest.grouped_boot(d, groups)
                res[f"mae_gain_{a}_vs_{b}"] = {"mean": bt[0], "lo95": bt[1], "hi95": bt[2]}
            for name in per:
                per[name].pop("_ls")
                per[name].pop("_ae")
                per[name].pop("_br")
            res["models"] = per
            res["by_role"] = _by(units, per, lambda u: u[0]["role"], samples, preds, prop, scope)
            res["by_history_depth"] = _by(units, per, lambda u: _depth(data, u[0]), samples, preds, prop, scope)
            res["by_league"] = _by(units, per, lambda u: str(u[0].get("league")), samples, preds, prop, scope)
            report["results"][f"{prop}|{scope}"] = res
            log(f"{prop} {scope}: n={res['n']} " + " | ".join(f"{m} MAE {per[m]['mae']:.3f} LS {per[m]['logscore']:.4f}" for m in per))
    report["_units"] = unit_rows
    return report, params, fit_b


def lol2_samples(data, test, d_tr, d_ev, d_end, settings, sims, log=print):
    """lol-2 fitted on the fit period's games; every scored series simulated once with both lineups (game 1's players).
    -> ({(match, pid, stat, scope): moment summary (mean, var)}, params)"""
    hl, k0, k_team = settings.get("hl", 120.0), settings.get("k0", 8.0), settings.get("k_team", 8.0)
    day_of = {mk: d for mk, d in data.match_day.items()}
    fit_rows = [r for r in data.rows if day_of[r["match_key"]] < d_ev]
    eg = elo.from_rows([dict(r, team=r["team_id"], opponent=r["opponent_id"]) for r in data.rows if r.get("team_id")],
                       lambda r: day_of[r["match_key"]])
    elo_fn = lambda a, b, day: eg.p_win(a, b, day)
    params = sim.fit(fit_rows, hl=hl, k0=k0, k_team=k_team, elo_fn=elo_fn, log=log, fit_from=d_tr)
    games = sorted(sim.games_from_rows(data.rows), key=lambda g: g["played_at"])
    st = sim.State(params["hl"])
    by_day, series_games = defaultdict(list), defaultdict(list)
    for g in games:
        by_day[day_of[g["match_key"]]].append(g)               # each game counts on its series' day (the match day)
        series_games[g["match_key"]].append(g)
    wanted = {t["match"] for t in test}
    out = {}
    for day in sorted(by_day):
        if d_ev <= day < d_end:
            for mk in {g["match_key"] for g in by_day[day]}:
                if mk not in wanted or day_of.get(mk) != day:
                    continue
                g1 = min(series_games[mk], key=lambda g: g["n"] or 99)
                if (g1["n"] or 1) != 1:
                    continue
                ctx = {"p_win": elo_fn(g1["a"], g1["b"], day),
                       "team_a": sim.team_feats(st, g1["a"], day, params["priors"], params["k_team"]),
                       "team_b": sim.team_feats(st, g1["b"], day, params["priors"], params["k_team"])}
                for side in ("a", "b"):
                    ctx["lineup_" + side] = [dict(sim.player_feats(st, q["pid"], q["role"], day, params["priors"], params["k0"]),
                                                  pid=q["pid"], role=q["role"]) for q in g1["players"] if q["team"] == g1[side]]
                if len(ctx["lineup_a"]) != 5 or len(ctx["lineup_b"]) != 5:
                    continue
                for scope, (n, bo) in SCOPES.items():
                    tot = sim.simulate(params, ctx, n, sims=sims, seed=zlib.crc32(f"{mk}|{scope}".encode()))
                    for (pid, stat), x in tot.items():
                        if stat in STATS:
                            out[(mk, pid, stat, scope)] = (float(x.mean()), float(x.var()))
        for g in by_day[day]:
            st.add_game(day, g)
    return out, params


def nb_from_moments(mean, var):
    """The simulated total as a negative binomial with the simulation's mean and variance (smooth, monotone in the line;
    scored like B0 and lol-1 - no Monte Carlo noise in the log score)."""
    return mean, (mean * mean / (var - mean)) if var > mean + 1e-9 else 1e6


def tune(rows, grid, sims=1000, log=print):
    """Development only (VALID): lol-2 settings by mean log score over kills and assists, Maps 1-2 and Maps 1-3."""
    P = PERIODS["dev"]
    d_tr, d_ev, d_end = (P[k].toordinal() for k in ("train_from", "eval_from", "eval_to"))
    data = backtest.Data(rows)
    test = [t for t in data.targets if d_ev <= t["day"] < d_end]
    out = []
    for setting in grid:
        samples, _ = lol2_samples(data, test, d_tr, d_ev, d_end, setting, sims, log=lambda m: None)
        ls = []
        for t in test:
            for prop, j in (("kills", 0), ("assists", 2)):
                for scope in ("12", "123"):
                    n, bo = SCOPES[scope]
                    if t.get("best_of") != bo or not all(g in t["games"] for g in range(1, n + 1)):
                        continue
                    k = (t["match"], t["player"], prop, scope)
                    if k in samples:
                        mean, size = nb_from_moments(*samples[k])
                        ls.append(model.nb_logpmf(sum(t["games"][g][j] for g in range(1, n + 1)), mean, size))
        out.append(dict(setting, logscore=float(np.mean(ls)) if ls else None, n=len(ls)))
        log(f"lol-2 {setting}: mean log score {out[-1]['logscore']:.4f} on {len(ls)}")
    return sorted(out, key=lambda r: -(r["logscore"] or -1e9))


def _depth(data, t):
    arr = data.arr.get(t["player"])
    n = int((arr[:, 0] < t["day"]).sum()) if arr is not None else 0
    return "0-9 games" if n < 10 else "10-29 games" if n < 30 else "30+ games"


def _by(units, per, key, samples, preds, prop, scope):
    out = defaultdict(lambda: defaultdict(list))
    for t, y, k, line in units:
        g = key((t, y, k, line))
        out[g]["B0"].append(abs(preds["B0"][k][0] - y))
        out[g]["lol-1"].append(abs(preds["lol-1"][k][0] - y))
        out[g]["lol-2"].append(abs(samples[k][0] - y))
    return {g: {"n": len(v["B0"]), **{m: round(float(np.mean(v[m])), 4) for m in v}} for g, v in out.items() if len(v["B0"]) >= 30}


def _cal(pairs):
    bins = defaultdict(lambda: [0, 0.0, 0.0])
    for p, h in pairs:
        b = min(int(p * 10), 9)
        bins[b][0] += 1
        bins[b][1] += p
        bins[b][2] += h
    return [{"bin": f"{b / 10:.1f}-{(b + 1) / 10:.1f}", "n": v[0], "predicted": round(v[1] / v[0], 3), "actual": round(v[2] / v[0], 3)}
            for b, v in sorted(bins.items()) if v[0] >= 20]


def _comparable_totals(data):
    """(pid, stat, scope) -> [(match day, total)] over series of the scope's format where every scope game was played."""
    out = defaultdict(list)
    for t in sorted(data.targets, key=lambda t: t["day"]):
        for scope, (n, bo) in SCOPES.items():
            if bo and t.get("best_of") != bo:
                continue
            if not all(g in t["games"] for g in range(1, n + 1)):
                continue
            for prop, j in (("kills", 0), ("deaths", 1), ("assists", 2)):
                out[(t["player"], prop, scope)].append((t["day"], sum(t["games"][g][j] for g in range(1, n + 1))))
    return out


def _passes(res, a, b):
    """The A1 rule for model a over model b on one (stat, scope) result: log-score gain interval above 0, MAE not higher."""
    if a == b:
        return False
    if f"logscore_gain_{a}_vs_{b}" in res:
        lo = res[f"logscore_gain_{a}_vs_{b}"]["lo95"]
    elif f"logscore_gain_{b}_vs_{a}" in res:
        lo = -res[f"logscore_gain_{b}_vs_{a}"]["hi95"]
    else:
        return False
    return lo > 0 and res["models"][a]["mae"] <= res["models"][b]["mae"]


def make_fit(final_path, snapshot_sha=None):
    """Amendment A5: the production fit from the single final comparison. -> fit dict (lol/model_fit.json)."""
    import hashlib
    raw = open(final_path, "rb").read()
    d = json.loads(raw)
    rep, fit = d["report"], d["b0_lol1_fit"]
    assert rep["which"] == "final", "the fit comes from the final comparison only"
    evidence = {}
    for prop in ("kills", "assists", "deaths"):
        base = fit["selection"][prop]
        res = [rep["results"].get(f"{prop}|{sc}") for sc in ("12", "123")]
        lol2 = all(r and _passes(r, "lol-2", base) for r in res)
        if lol2:
            fit["selection"][prop] = "lol-2"
        evidence[prop] = {"b0_vs_lol1": base, "lol2_replaces": lol2,
                          **{sc: {m: {k: round(r["models"][m][k], 4) for k in ("mae", "logscore")} for m in r["models"]}
                             for sc, r in zip(("Maps 1-2", "Maps 1-3"), res) if r}}
    if any(v == "lol-2" for v in fit["selection"].values()):
        fit["sim"] = d["lol2_params"]
    fit.update(protocol="lol/EVALUATION.md A1-A5", selection_rule="A1 rule per stat; lol-2 replaces only under the same rule",
               selection_evidence=evidence,
               fit_period={"targets_from": rep["periods"]["train_from"], "targets_before": rep["periods"]["eval_from"],
                           "history_from": rep["periods"]["history_from"]},
               final_report_sha256=hashlib.sha256(raw).hexdigest(), history_snapshot_sha256=snapshot_sha,
               seed_rule="lol-2 simulation seed = crc32(market_key|day); evaluation seed = crc32(match|scope)",
               written_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return fit


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("which", choices=["dev", "final", "tune", "fit"])
    ap.add_argument("--final", help="fit: the final comparison's JSON")
    ap.add_argument("--snapshot-sha", help="fit: the history snapshot's sha256")
    ap.add_argument("--lol2", help='lol-2 settings as JSON, e.g. {"hl": 120, "k0": 8, "k_team": 8}')
    ap.add_argument("--json")
    ap.add_argument("--db", help="a history SQLite file (default: the configured history)")
    ap.add_argument("--sims", type=int, default=2000)
    a = ap.parse_args(argv)
    from pathlib import Path
    if a.which == "fit":
        fit = make_fit(a.final, a.snapshot_sha)
        model.FIT_FILE.write_text(json.dumps(fit, indent=1, default=str) + chr(10), encoding="utf-8")
        print(json.dumps({"selection": fit["selection"], "evidence": fit["selection_evidence"]}, indent=1))
        return
    db = history.DB(dsn="", path=Path(a.db)) if a.db else history.DB()
    rows = history.all_player_games(db, since=PERIODS["dev"]["history_from"].isoformat())
    if a.which == "tune":
        grid = [{"hl": hl, "k0": k0, "k_team": kt} for hl in (60.0, 120.0, 240.0) for k0 in (4.0, 8.0, 16.0) for kt in (8.0,)]
        res = tune(rows, grid, sims=a.sims, log=lambda m: print(m, file=sys.stderr, flush=True))
        txt = json.dumps(res, indent=1)
        if a.json:
            open(a.json, "w", encoding="utf-8").write(txt)
        print(txt)
        return
    if a.which == "final":
        if TEST_MARK.exists():
            sys.exit(f"TEST was already opened ({TEST_MARK.read_text().strip()}); a second scoring is not allowed (A4)")
        TEST_MARK.write_text(json.dumps({"opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}) + "\n")
    rep, params, fit_b = run(rows, a.which, sims=a.sims, log=lambda m: print(m, file=sys.stderr, flush=True),
                             lol2=json.loads(a.lol2) if a.lol2 else None)
    units = rep.pop("_units")
    out = json.dumps({"report": rep, "lol2_params": params, "b0_lol1_fit": fit_b}, indent=1, default=str)
    if a.json:
        with open(a.json.replace(".json", "_units.json"), "w", encoding="utf-8") as fh:
            json.dump(units, fh, default=str)
    if a.json:
        open(a.json, "w", encoding="utf-8").write(out)
    else:
        print(out)


if __name__ == "__main__":
    main()
