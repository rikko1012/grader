"""
The owner's earlier LoL decision rules and the proposed factor groups, tested as HYPOTHESES on the development split
(lol/EVALUATION.md A4) - never hard-coded.

Input: the per-target rows of `python -m lol.evaluate dev --json X` (X_units.json: actual total, the development line,
every model's prediction) plus the history (for the context each test needs). For a chosen model:

  rules     each rule is a condition on information known before the match. Its effect is the model's mean residual
            (actual - projection) inside the condition minus outside, with a series-grouped 95% interval, plus the
            calibration of P(over) at the development line inside the condition. "Adds information" = the interval
            excludes 0. A rule that adds information is a candidate correction for the single TEST comparison - not
            a rule, and never a grade cap.
  groups    each factor group's pre-match features predict the model's residual with a ridge fitted on the first half
            of VALID and scored on the second half (MAE and squared error with series-grouped intervals). A group is
            promising only if it improves the second half.

    python -m lol.hypotheses <units.json> [--model lol-1] [--db history.db] [--json out]
"""
import argparse
import json
import math
from collections import defaultdict

import numpy as np

from . import backtest, history, model

LCK = {"lck", "lol champions korea", "lck cup"}


def _boot_diff(vals_in, vals_out, groups_in, groups_out, n=2000, seed=5):
    """Series-grouped bootstrap of mean(in) - mean(out)."""
    rng = np.random.default_rng(seed)
    gi, go = defaultdict(list), defaultdict(list)
    for v, g in zip(vals_in, groups_in):
        gi[g].append(v)
    for v, g in zip(vals_out, groups_out):
        go[g].append(v)
    ki, ko = list(gi), list(go)
    si, ci = np.array([sum(gi[k]) for k in ki]), np.array([len(gi[k]) for k in ki])
    so, co = np.array([sum(go[k]) for k in ko]), np.array([len(go[k]) for k in ko])
    est = []
    for _ in range(n):
        a = rng.integers(0, len(ki), len(ki))
        b = rng.integers(0, len(ko), len(ko))
        est.append(si[a].sum() / ci[a].sum() - so[b].sum() / co[b].sum())
    est = np.sort(est)
    return float(si.sum() / ci.sum() - so.sum() / co.sum()), float(est[int(0.025 * n)]), float(est[int(0.975 * n) - 1])


def _p_over(pred, line, name):
    mean, size = pred                                        # every model is stored as (mean, NB size) - lol-2 moment-matched
    return model.probs(mean, size, line)[0]


def conditions(units, rows):
    """Pre-match conditions for every unit (rule name -> bool)."""
    meet = defaultdict(list)                                 # team pair -> [series days]
    first_with = {}                                          # (player, team) -> first day together
    md = {}
    for r in rows:
        d = backtest._day(r["played_at"])
        md[r["match_key"]] = min(md.get(r["match_key"], d), d)
    for r in rows:
        k = (r["player_id"], r["team_id"])
        first_with[k] = min(first_with.get(k, 10 ** 9), md[r["match_key"]])
        meet[frozenset((r["team_id"], r["opponent_id"]))].append(md[r["match_key"]])
    team_of = {}
    for r in rows:
        team_of[(r["match_key"], r["player_id"])] = (r["team_id"], r["opponent_id"])
    out = []
    for u in units:
        tid, oid = team_of.get((u["match"], u["player"]), (None, None))
        prev = u["prev"]
        z = None
        if len(prev) >= 5:
            mu, sd = float(np.mean(prev[:-1])), float(np.std(prev[:-1])) or 1.0
            z = (prev[-1] - mu) / sd
        days = sorted(set(meet.get(frozenset((tid, oid)), [])))
        p = u.get("p_win")
        out.append({
            "lck": str(u.get("league") or "").lower() in LCK,
            "support_kills": u["role"] == "support" and u["stat"] == "kills",
            # odds-based rules use the history's Elo win chance as a PROXY: no timestamped pre-match match odds are saved
            "favourite_sweet_spot_-150_-400 (Elo proxy)": p is not None and 0.60 <= p <= 0.80,
            "heavy_favourite_over_-1000 (Elo proxy)": p is not None and p > 1000 / 1100,
            "rematch_within_14_days": any(0 < u["day"] - d <= 14 for d in days),
            "new_roster_under_60_days": tid is not None and u["day"] - first_with.get((u["player"], tid), u["day"]) < 60,
            "after_spike_z_over_1.5": z is not None and z > 1.5,
            "after_dud_z_under_-1.5": z is not None and z < -1.5})
    return out


def test_rules(units, conds, name):
    res = {}
    for rule in conds[0]:
        inside = [i for i, c in enumerate(conds) if c[rule]]
        outside = [i for i, c in enumerate(conds) if not c[rule]]
        if len(inside) < 30:
            res[rule] = {"n": len(inside), "note": "fewer than 30 units - not testable"}
            continue
        r = lambda i: units[i]["y"] - units[i]["pred"][name][0]
        eff = _boot_diff([r(i) for i in inside], [r(i) for i in outside], [units[i]["match"] for i in inside],
                         [units[i]["match"] for i in outside])
        cal = [( _p_over(units[i]["pred"][name], units[i]["line"], name), 1.0 if units[i]["y"] > units[i]["line"] else 0.0)
               for i in inside if units[i]["line"] is not None]
        res[rule] = {"n": len(inside), "series": len({units[i]["match"] for i in inside}),
                     "residual_effect": {"mean": round(eff[0], 3), "lo95": round(eff[1], 3), "hi95": round(eff[2], 3)},
                     "adds_information": eff[1] > 0 or eff[2] < 0,
                     "p_over_at_dev_line": {"n": len(cal), "predicted": round(float(np.mean([c[0] for c in cal])), 3) if cal else None,
                                            "actual": round(float(np.mean([c[1] for c in cal])), 3) if cal else None}}
    return res


def gap_calibration(units, name):
    """The 'one-kill discrepancy' rule: does a projection-vs-line gap mean the same thing for every role and scope?
    Brier of P(over) and the hit rate of the model's side, by |projection - line| band x role x scope."""
    out = defaultdict(lambda: [0, 0.0, 0.0])
    for u in units:
        if u["line"] is None:
            continue
        mean = u["pred"][name][0]
        gap = abs(mean - u["line"])
        band = "<=1" if gap <= 1 else "1-2" if gap <= 2 else ">2"
        po = _p_over(u["pred"][name], u["line"], name)
        side_over = po >= 0.5
        hit = (u["y"] > u["line"]) if side_over else (u["y"] < u["line"])
        k = f"{u['stat']}|{u['scope']}|{u['role']}|gap {band}"
        out[k][0] += 1
        out[k][1] += max(po, 1 - po)
        out[k][2] += hit
    return {k: {"n": v[0], "predicted_side_hit": round(v[1] / v[0], 3), "actual_side_hit": round(v[2] / v[0], 3)}
            for k, v in sorted(out.items()) if v[0] >= 30}


def group_features(units, rows):
    """Pre-match feature groups per unit (earlier games only)."""
    md = {}
    for r in rows:
        d = backtest._day(r["played_at"])
        md[r["match_key"]] = min(md.get(r["match_key"], d), d)
    byp = defaultdict(list)
    for r in rows:
        byp[r["player_id"]].append(r)
    for v in byp.values():
        v.sort(key=lambda r: r["played_at"])
    opp_of = {(r["match_key"], r["player_id"]): r.get("opponent_id") for r in rows}
    last_match_day = defaultdict(list)
    for r in rows:
        last_match_day[r["team_id"]].append(md[r["match_key"]])
    feats = []
    for u in units:
        prev = [r for r in byp.get(u["player"], []) if md[r["match_key"]] < u["day"]][-30:]
        mins = sum((r.get("duration_s") or 0) / 60 for r in prev) or 1
        dmg = [r.get("damage") for r in prev if r.get("damage") is not None]
        vis = [r.get("vision") for r in prev if r.get("vision") is not None]
        champs = defaultdict(int)
        for r in prev[-20:]:
            champs[r.get("champion")] += 1
        top3 = sum(sorted(champs.values(), reverse=True)[:3]) / max(len(prev[-20:]), 1)
        team = prev[-1]["team_id"] if prev else None
        days = sorted(d for d in set(last_match_day.get(team, [])) if d < u["day"])
        # head-to-head: per-game difference vs this opponent, shrunk toward 0 by 10 pseudo-games (modest by design)
        opp = opp_of.get((u["match"], u["player"]))
        allp = [r[u["stat"]] for r in prev if r.get(u["stat"]) is not None]
        vs = [r[u["stat"]] for r in byp.get(u["player"], []) if md[r["match_key"]] < u["day"] and r.get("opponent_id") == opp]
        h2h = ((np.mean(vs) - np.mean(allp)) * len(vs) / (len(vs) + 10.0)) if vs and allp else 0.0
        feats.append({
            "player": [sum(r.get("cs") or 0 for r in prev) / mins, (sum(dmg) / mins) if dmg else np.nan,
                       (sum(vis) / mins) if vis else np.nan,
                       (sum(r["kills"] + r["assists"] for r in prev) / max(sum(r["deaths"] for r in prev), 1)) if prev else np.nan,
                       sum(r.get("gold") or 0 for r in prev) / mins],
            "context": [u["day"] - days[-1] if days else np.nan, 1.0 if u["best_of"] == 5 else 0.0,
                        1.0 if str(u.get("league") or "").lower() in LCK else 0.0],
            "draft": [top3, float(len(champs))],
            "head_to_head": [h2h, float(len(vs))]})
    return feats


def test_groups(units, feats, name):
    """Ridge on each group's standardised features predicts the residual: fitted on VALID's first half (by date), scored
    on the second half - change in MAE / squared error vs the model alone, series-grouped intervals."""
    days = sorted(u["day"] for u in units)
    cut = days[len(days) // 2]
    out = {}
    for group in feats[0]:
        X = np.array([f[group] for f in feats], dtype=float)
        ok = ~np.isnan(X).any(1)
        res = np.array([u["y"] - u["pred"][name][0] for u in units])
        tr = np.array([u["day"] < cut for u in units]) & ok
        te = np.array([u["day"] >= cut for u in units]) & ok
        if tr.sum() < 200 or te.sum() < 200:
            out[group] = {"note": "too few units with this group's data", "n_fit": int(tr.sum()), "n_test": int(te.sum())}
            continue
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        Z = (X - mu) / sd
        A = np.c_[np.ones(len(Z)), Z]
        lam = 10.0
        coef = np.linalg.solve(A[tr].T @ A[tr] + lam * np.eye(A.shape[1]), A[tr].T @ res[tr])
        adj = A[te] @ coef
        # the comparison is against an intercept-only correction fitted on the same first half, so only the group's
        # features are credited (a drift in the average residual is not "information" from the group)
        base_ae, new_ae = np.abs(res[te] - res[tr].mean()), np.abs(res[te] - adj)
        groups = [units[i]["match"] for i in np.where(te)[0]]
        g = backtest.grouped_boot(base_ae - new_ae, groups)
        out[group] = {"n_fit": int(tr.sum()), "n_test": int(te.sum()), "mae_gain": {"mean": round(g[0], 4), "lo95": round(g[1], 4),
                                                                                   "hi95": round(g[2], 4)},
                      "promising": g[1] > 0, "coef": [round(float(c), 4) for c in coef]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("units")
    ap.add_argument("--model", default="lol-1")
    ap.add_argument("--db")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    units = json.load(open(a.units, encoding="utf-8"))
    from pathlib import Path
    db = history.DB(dsn="", path=Path(a.db)) if a.db else history.DB()
    rows = history.all_player_games(db, since="2026-02-16")
    out = {"model": a.model, "by_stat_scope": {}}
    for key in sorted({(u["stat"], u["scope"]) for u in units}):
        us = [u for u in units if (u["stat"], u["scope"]) == key]
        conds = conditions(us, rows)
        feats = group_features(us, rows)
        out["by_stat_scope"]["|".join(key)] = {"n": len(us), "rules": test_rules(us, conds, a.model),
                                              "groups": test_groups(us, feats, a.model)}
    out["one_kill_gap"] = gap_calibration(units, a.model)
    txt = json.dumps(out, indent=1, default=str)
    if a.json:
        open(a.json, "w", encoding="utf-8").write(txt)
    else:
        print(txt)


if __name__ == "__main__":
    main()
