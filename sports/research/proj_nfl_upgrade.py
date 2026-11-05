"""pnu-1 walk-forward evaluation of NFL projection candidates against the exact live sm-1 chances.

    python -m sports.research.proj_nfl_upgrade --export <gradecore-export-1 nfl json> --history <ds-1 sqlite>
        [--out sports/research/results/proj_nfl_upgrade_<date>.json]

Read-only: reads a saved export file and a history db copy; writes one local JSON report. Never trains or writes
anything the service reads. Protocol (owner hard rules 2026-11-05):
  * expanding-window walk-forward by slate day: each candidate is refit only on rows SETTLED before the first kickoff
    of the test day, then scored on that day's rows; the first slate day only trains.
  * baseline = the exact saved live chance on the same rows.
  * 95% intervals resample whole games (match_key), B = 2,000.
  * pass = log-loss difference CI entirely below 0, stated-vs-hit within 2.5 pts overall AND Over AND Under,
    Over share of chosen sides not pushed more than 5 pts further from the realised Over rate than live,
    graded (C or better) count >= 90% of live.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from sports.model import proj_nfl_cand as pc

SINGLE = ("rr-1", "lr-1", "pp-1", "dv-1", "ma-1")
B = 2000


def boot_ci(vals, groups, seed=7, reps=B):
    vals = np.asarray(vals, float)
    keys = sorted(set(groups))
    idx = {k: np.where(np.asarray(groups) == k)[0] for k in keys}
    sums = np.array([vals[idx[k]].sum() for k in keys]); cnts = np.array([len(idx[k]) for k in keys])
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(reps):
        pick = rng.integers(0, len(keys), len(keys))
        out.append(sums[pick].sum() / max(cnts[pick].sum(), 1))
    out = np.sort(out)
    return [round(float(out[int(0.025 * reps)]), 4), round(float(out[int(0.975 * reps) - 1]), 4)]


def _ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def side_report(p_over, rows, y, games):
    side, pcs = pc.chosen(p_over, rows)
    hit = (side == y).astype(float)
    out = {"n": int(len(y)), "over_share": round(float(side.mean()), 4), "over_rate": round(float(y.mean()), 4)}
    for name, m in (("all", np.ones(len(y), bool)), ("over", side == 1), ("under", side == 0)):
        if m.sum() == 0:
            out[name] = {"n": 0}; continue
        g = [games[i] for i in np.where(m)[0]]
        out[name] = {"n": int(m.sum()), "stated": round(float(pcs[m].mean()), 4), "hit": round(float(hit[m].mean()), 4),
                     "gap": round(float((pcs[m] - hit[m]).mean()), 4), "gap_ci": boot_ci(pcs[m] - hit[m], g)}
    bins = []
    for lo in range(0, 100, 5):
        m = (pcs * 100 >= lo) & (pcs * 100 < lo + 5) if lo < 95 else (pcs * 100 >= lo)
        if m.sum():
            bins.append({"bin": f"{lo}-{lo + 5}", "n": int(m.sum()), "stated": round(float(pcs[m].mean()), 4),
                         "hit": round(float(hit[m].mean()), 4),
                         "over_n": int((m & (side == 1)).sum()), "under_n": int((m & (side == 0)).sum())})
    out["bins"] = bins
    gm = pcs >= pc.GRADE_C
    let = pc.letter(pcs)
    out["graded"] = {"n": int(gm.sum()), "stated": round(float(pcs[gm].mean()), 4) if gm.sum() else None,
                     "hit": round(float(hit[gm].mean()), 4) if gm.sum() else None,
                     "hit_ci": boot_ci(hit[gm], [games[i] for i in np.where(gm)[0]]) if gm.sum() else None,
                     "over_share": round(float(side[gm].mean()), 4) if gm.sum() else None,
                     "letters": {k: int((let == k).sum()) for k in ("A", "B", "C", "Lean")}}
    for k in ("A", "B"):
        m = let == k
        out["graded"][k + "_stated_hit"] = ([round(float(pcs[m].mean()), 4), round(float(hit[m].mean()), 4)]
                                            if m.sum() else None)
    return out


def compare(p_live, p_cand, rows):
    y = np.array([r["y"] for r in rows], float)
    games = [r["match_key"] for r in rows]
    d_ll = _ll(p_cand, y) - _ll(p_live, y)
    d_br = (p_cand - y) ** 2 - (p_live - y) ** 2
    live, cand = side_report(p_live, rows, y, games), side_report(p_cand, rows, y, games)
    res = {"n": len(rows), "games": len(set(games)),
           "logloss": {"live": round(float(_ll(p_live, y).mean()), 4), "cand": round(float(_ll(p_cand, y).mean()), 4),
                       "diff": round(float(d_ll.mean()), 4), "diff_ci": boot_ci(d_ll, games)},
           "brier": {"live": round(float(((p_live - y) ** 2).mean()), 4),
                     "cand": round(float(((p_cand - y) ** 2).mean()), 4),
                     "diff": round(float(d_br.mean()), 4), "diff_ci": boot_ci(d_br, games)},
           "live": live, "cand": cand, "by_market": {}}
    for mk in sorted({r["market"] for r in rows}):
        m = np.array([r["market"] == mk for r in rows])
        gm = [games[i] for i in np.where(m)[0]]
        res["by_market"][mk] = {"n": int(m.sum()), "ll_diff": round(float(d_ll[m].mean()), 4),
                                "ll_diff_ci": boot_ci(d_ll[m], gm)}
    res["graded_hit_diff"] = graded_hit_diff(p_live, p_cand, rows, y, games)
    res["gate"] = gate(res)
    return res


def graded_hit_diff(p_live, p_cand, rows, y, games, seed=7, reps=B):
    """Selection: hit rate of the candidate's graded picks minus live's graded picks (each its own pick set), with
    both rates recomputed on the same resampled games."""
    out = {}
    for arm, p in (("live", p_live), ("cand", p_cand)):
        side, pcs = pc.chosen(p, rows)
        g = pcs >= pc.GRADE_C
        out[arm] = (g.astype(float), ((side == y) & g).astype(float))
    keys = sorted(set(games)); garr = np.asarray(games)
    idx = [np.where(garr == k)[0] for k in keys]
    agg = {a: (np.array([v[0][i].sum() for i in idx]), np.array([v[1][i].sum() for i in idx])) for a, v in out.items()}
    rate = lambda a, pick: agg[a][1][pick].sum() / max(agg[a][0][pick].sum(), 1)
    allk = np.arange(len(keys))
    point = rate("cand", allk) - rate("live", allk)
    rng = np.random.default_rng(seed)
    sims = np.sort([rate("cand", k) - rate("live", k) for k in (rng.integers(0, len(keys), len(keys)) for _ in range(reps))])
    return {"diff": round(float(point), 4), "ci": [round(float(sims[int(0.025 * reps)]), 4),
                                                   round(float(sims[int(0.975 * reps) - 1]), 4)]}


def gate(res):
    c, l = res["cand"], res["live"]
    over_dist_live = abs(l["over_share"] - l["over_rate"]); over_dist_cand = abs(c["over_share"] - c["over_rate"])
    checks = {
        "logloss_ci_below_0": res["logloss"]["diff_ci"][1] < 0,
        "calib_all_2.5": abs(c["all"]["gap"]) <= 0.025,
        "calib_over_2.5": c["over"].get("n", 0) > 0 and abs(c["over"]["gap"]) <= 0.025,
        "calib_under_2.5": c["under"].get("n", 0) > 0 and abs(c["under"]["gap"]) <= 0.025,
        "over_share_not_pushed_5": over_dist_cand - over_dist_live <= 0.05,
        "graded_count_90pct": c["graded"]["n"] >= 0.9 * l["graded"]["n"],
    }
    checks["pass"] = all(checks.values())
    return checks


RIDGE_GRID = (0.0005, 0.002, 0.005, 0.02)
RIDGE_DEFAULT = 0.002
INNER_MIN = 300


def pick_ridge(cand, tr, grid=RIDGE_GRID):
    """Nested choice on TRAINING rows only: hold out the latest training slate days (>= INNER_MIN rows), fit each
    ridge on the earlier ones, keep the best held-out log loss. Too little history -> RIDGE_DEFAULT."""
    days = sorted({pc.slate_day(r["start_at"]) for r in tr}, reverse=True)
    hold = []
    for d in days[:-1]:                      # never hold out every training day
        hold.append(d)
        if sum(pc.slate_day(r["start_at"]) in hold for r in tr) >= INNER_MIN:
            break
    if not hold:
        return RIDGE_DEFAULT, None
    va = [r for r in tr if pc.slate_day(r["start_at"]) in hold]
    first = min(r["start_at"] for r in va)
    fit = [r for r in tr if r["settled_at"] < first and r["start_at"] < first]
    if len(fit) < 200 or len(va) < INNER_MIN:
        return RIDGE_DEFAULT, None
    y = np.array([r["y"] for r in va], float)
    scores = {g: float(_ll(cand.p_over(cand.fit(fit, ridge=g), va), y).mean()) for g in grid}
    return min(scores, key=scores.get), {str(k): round(v, 5) for k, v in scores.items()}


def walk_forward(rows, names, min_train=200, ridge=RIDGE_DEFAULT):
    """-> (test rows, live p_over, cand p_over, windows). Refit per slate day on rows settled before it.
    ridge="auto" chooses the ridge per window by pick_ridge (training rows only)."""
    dec = [r for r in rows if r["y"] is not None]
    days = sorted({pc.slate_day(r["start_at"]) for r in dec})
    cand = pc.Candidate(names)
    test, pl, pcand, windows = [], [], [], []
    for d in days:
        te = [r for r in dec if pc.slate_day(r["start_at"]) == d]
        first = min(r["start_at"] for r in te)
        tr = [r for r in dec if r["settled_at"] < first and r["start_at"] < first]
        if len(tr) < min_train:
            windows.append({"day": d, "train": len(tr), "test": len(te), "scored": False}); continue
        rg, inner = pick_ridge(cand, tr) if ridge == "auto" else (ridge, None)
        th = cand.fit(tr, ridge=rg)
        test += te
        pl.append(np.array([r["po"] for r in te])); pcand.append(cand.p_over(th, te))
        windows.append({"day": d, "train": len(tr), "test": len(te), "scored": True,
                        "test_games": len({r["match_key"] for r in te}), "ridge": rg, "inner_ll": inner,
                        "params": [round(float(x), 4) for x in th],
                        "ll_diff": round(float((_ll(pcand[-1], np.array([r["y"] for r in te], float))
                                                - _ll(pl[-1], np.array([r["y"] for r in te], float))).mean()), 4)})
    return test, np.concatenate(pl), np.concatenate(pcand), windows


def run(export_path, history_path, ridge="auto"):
    exp = json.loads(Path(export_path).read_text(encoding="utf-8"))
    hist = pc.load_history(history_path)
    rows = pc.prepare([dict(r) for r in exp["rows"]], hist)
    report = {"version": pc.VERSION, "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "export": {"schema": exp.get("schema"), "built_at": exp.get("built_at"), "model_version": exp.get("model_version"),
                         "rows": len(exp["rows"]), "decided": sum(r["y"] is not None for r in rows)},
              "history_last_game": max(g["start"] for gs in hist.values() for g in gs) if hist else None,
              "rows_with_history": sum(r["n_hist"] > 0 for r in rows if r["y"] is not None),
              "ridge": ridge, "candidates": {}}
    def one(names):
        te, pl, pcd, win = walk_forward(rows, names, ridge=ridge)
        out = compare(pl, pcd, te); out["windows"] = win
        return out
    for n in SINGLE:
        report["candidates"][n] = one((n,))
    passing = [n for n in SINGLE if report["candidates"][n]["gate"]["pass"] and n != "ma-1"]
    report["passing"] = passing
    combos = []
    if len(passing) >= 2:
        combos.append(tuple(passing))
    combos.append(("rr-1", "lr-1", "pp-1", "dv-1"))
    combos.append(("lr-1", "pp-1", "dv-1"))
    for c in dict.fromkeys(combos):
        report["candidates"]["+".join(c)] = one(c)
    # sensitivity: every candidate at each fixed ridge of the grid (headline numbers use `ridge`)
    report["ridge_sensitivity"] = {}
    for name in report["candidates"]:
        names = tuple(name.split("+"))
        report["ridge_sensitivity"][name] = {}
        for g in RIDGE_GRID:
            te, pl, pcd, _ = walk_forward(rows, names, ridge=g)
            c = compare(pl, pcd, te)
            report["ridge_sensitivity"][name][str(g)] = {
                "ll_diff": c["logloss"]["diff"], "ll_diff_ci": c["logloss"]["diff_ci"],
                "stated_hit": [c["cand"]["all"]["stated"], c["cand"]["all"]["hit"]],
                "over_stated_hit": [c["cand"]["over"].get("stated"), c["cand"]["over"].get("hit")],
                "under_stated_hit": [c["cand"]["under"].get("stated"), c["cand"]["under"].get("hit")],
                "graded_n": c["cand"]["graded"]["n"], "graded_hit": c["cand"]["graded"]["hit"],
                "pass": c["gate"]["pass"]}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", required=True)
    ap.add_argument("--history", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--ridge", default="auto", help='"auto" (nested, training rows only) or a number')
    a = ap.parse_args(argv)
    rep = run(a.export, a.history, a.ridge if a.ridge == "auto" else float(a.ridge))
    txt = json.dumps(rep, indent=1)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    for name, c in rep["candidates"].items():
        g = c["gate"]
        print(f"{name:18s} n={c['n']} games={c['games']} LL {c['logloss']['live']:.4f}->{c['logloss']['cand']:.4f} "
              f"diff {c['logloss']['diff']:+.4f} {c['logloss']['diff_ci']} | stated/hit live {c['live']['all']['stated']:.3f}/"
              f"{c['live']['all']['hit']:.3f} cand {c['cand']['all']['stated']:.3f}/{c['cand']['all']['hit']:.3f} "
              f"O {c['cand']['over'].get('stated', 0):.3f}/{c['cand']['over'].get('hit', 0):.3f} "
              f"U {c['cand']['under'].get('stated', 0):.3f}/{c['cand']['under'].get('hit', 0):.3f} | graded live "
              f"{c['live']['graded']['n']} hit {c['live']['graded']['hit']} cand {c['cand']['graded']['n']} hit "
              f"{c['cand']['graded']['hit']} | overshare {c['live']['over_share']:.2f}->{c['cand']['over_share']:.2f} "
              f"(rate {c['cand']['over_rate']:.2f}) pass={g['pass']}")
    for name, sens in rep["ridge_sensitivity"].items():
        print("ridge", name, {k: (v["ll_diff"], v["ll_diff_ci"], v["stated_hit"], v["under_stated_hit"], v["graded_n"],
                                  v["pass"]) for k, v in sens.items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
