"""Chronological walk-forward of the NBA projection candidates (sports/model/proj_nba.py, pn-1) against the exact
live sm-1 chances on the same settled props. Read-only: reads a local recompute pickle, writes one JSON report.

Data (built by the 2026-11-05 diagnosis, scratch, not committed - player-level rows):
  recomp_oracle.pkl  every settled sm-1 NBA prop (read-only gradecore export + results report rows; the last saved
                     forecast >= 30 min before tip), with the frozen sm-1 PMF recomputed point-in-time at its own
                     forecast_at (90% of rows reproduce the live chance within 0.02).

Protocol:
  * test game-days = every game-day with >= 3 earlier game-days; for each test day D the stages are fitted ONLY on
    props whose game started >= 4 h before the EARLIEST forecast_at of D's props (so every result used had ended before
    any of D's forecasts was saved), then applied to D's props. No settled outcome of D (or later) is ever read by a fit.
  * live = the chance the live record printed (export p / results p_over,p_under); re = the recomputed sm-1 chance.
  * calibration (stated vs hit, Over and Under separately, 5-pt bins, log loss, Brier) and selection (hit rate at the
    offered book line of graded picks: chosen side >= 53.45% (sports-gp-1 C band) AND the projection mean on that side,
    the board's lean rule) are reported separately. 95% CIs resample whole games (1,000 draws).
  * pass = dLL CI entirely < 0 AND |stated-hit| <= 2.5 pts overall, Over and Under AND the Over share of sides not
    pushed more than 5 pts further from the data's Over rate than live AND graded count >= 90% of live.

  python -m sports.research.proj_nba.walkforward <recomp_oracle.pkl> <out.json>
"""
from __future__ import annotations

import datetime as dt
import json
import pickle
import sys
import time

import numpy as np

from sports.model import proj_nba

C_BAND = 0.5345
SPECS = ("ma", "md", "sk", "rm", "rm+sk", "rm+md", "rm+ma", "rm+sk+md")
B = 1000


def _t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def gday(s):
    return (_t(s) - dt.timedelta(hours=10)).date().isoformat()


def load(path):
    """One row per offer (event, player, stat, line, book): export rows first, then results rows not in the export
    (last forecast >= 30 min before start). Pushes dropped. Mirrors the diagnosis loader."""
    rows = pickle.load(open(path, "rb"))["rows"]
    seen, out = set(), []
    for r in rows:
        if r["src"] != "export":
            continue
        seen.add((r["event"], r["player"], r["stat"], r["line"], r["book"]))
        out.append(r)
    best = {}
    for r in rows:
        if r["src"] != "results" or (_t(r["start"]) - _t(r["forecast_at"])).total_seconds() < 1800:
            continue
        k = (r["event"], r["player"], r["stat"], r["line"], r["book"])
        if k in seen:
            continue
        if k not in best or r["forecast_at"] > best[k]["forecast_at"]:
            best[k] = r
    for r in best.values():
        po = r["p_over"] / (r["p_over"] + r["p_under"])
        r["side"] = "over" if po >= 0.5 else "under"
        r["p_side"] = po if r["side"] == "over" else 1 - po
        r["status"] = "push" if r["outcome"] == "push" else ("win" if r["outcome"] == r["side"] else "loss")
        out.append(r)
    out = [r for r in out if r["status"] in ("win", "loss") and r["actual"] != r["line"]]
    for r in out:
        r["gday"] = gday(r["start"])
        r["po_live"] = r["p_side"] if r["side"] == "over" else 1 - r["p_side"]
    return out


class Boot:
    """Game-clustered bootstrap: C (B x G) multinomial game counts; stats from per-game sums."""

    def __init__(self, games, seed=11):
        self.ug, self.g = np.unique(games, return_inverse=True)
        rng = np.random.default_rng(seed)
        G = len(self.ug)
        self.C = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(B)]).astype(float)

    def sums(self, x, mask=None):
        x = np.asarray(x, float)
        if mask is not None:
            x = np.where(mask, x, 0.0)
        return self.C @ np.bincount(self.g, weights=x, minlength=len(self.ug))

    def ci_mean(self, x, mask=None):
        n = self.sums(np.ones(len(x)), mask)
        v = self.sums(x, mask) / np.maximum(n, 1e-9)
        return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]

    def ci_fn(self, fn):
        v = fn()
        return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]


def evaluate(po, mean, line, over, bt):
    po = np.asarray(po, float)
    so = po >= 0.5
    ps = np.where(so, po, 1 - po)
    hit = np.where(so, over, 1 - over)
    ll = proj_nba.logloss(po, over)
    br = (po - over) ** 2
    graded = (ps >= C_BAND) & np.where(so, mean > line, mean < line)
    out = {"n": int(len(po)), "logloss": round(float(ll.mean()), 5), "brier": round(float(br.mean()), 5),
           "over_share": round(float(so.mean()), 4), "data_over_rate": round(float(over.mean()), 4)}

    def sg(m):
        if m.sum() == 0:
            return {"n": 0}
        return {"n": int(m.sum()), "stated": round(float(ps[m].mean()), 4), "hit": round(float(hit[m].mean()), 4),
                "gap": round(float(ps[m].mean() - hit[m].mean()), 4), "gap_ci": bt.ci_mean(ps - hit, m)}
    out["all"] = sg(np.ones(len(po), bool))
    out["over"] = sg(so)
    out["under"] = sg(~so)
    bins = []
    for side, m0 in (("over", so), ("under", ~so)):
        for lo in np.arange(0.50, 1.0, 0.05):
            m = m0 & (ps >= lo) & (ps < lo + 0.05 + (1e-9 if lo >= 0.95 else 0))
            if m.sum():
                bins.append({"side": side, "bin": f"{lo:.2f}-{lo + 0.05:.2f}", "n": int(m.sum()),
                             "stated": round(float(ps[m].mean()), 4), "hit": round(float(hit[m].mean()), 4)})
    out["bins"] = bins
    out["graded"] = sg(graded)
    out["graded"]["over_n"] = int((graded & so).sum())
    out["graded"]["under_n"] = int((graded & ~so).sum())
    for thr in (0.55, 0.60):
        out[f"at_{int(thr * 100)}"] = sg((ps >= thr) & graded)
    out["_ll"], out["_br"], out["_graded"], out["_hit"] = ll, br, graded, hit
    return out


def strip(e):
    return {k: v for k, v in e.items() if not k.startswith("_")}


def compare(live, cand, bt):
    dll = cand["_ll"] - live["_ll"]
    dbr = cand["_br"] - live["_br"]
    gl, gc = live["_graded"], cand["_graded"]

    def hitdiff():
        a = bt.sums(cand["_hit"], gc) / np.maximum(bt.sums(np.ones(len(gc)), gc), 1e-9)
        b = bt.sums(live["_hit"], gl) / np.maximum(bt.sums(np.ones(len(gl)), gl), 1e-9)
        return a - b
    res = {"dLL": round(float(dll.mean()), 5), "dLL_ci": bt.ci_mean(dll),
           "dBrier": round(float(dbr.mean()), 5), "dBrier_ci": bt.ci_mean(dbr),
           "graded_hit_diff": round(float(cand["_hit"][gc].mean() - live["_hit"][gl].mean()), 4) if gc.sum() else None,
           "graded_hit_diff_ci": bt.ci_fn(hitdiff) if gc.sum() else None,
           "graded_count_ratio": round(float(gc.sum() / max(gl.sum(), 1)), 4)}
    rate = cand["data_over_rate"]
    checks = {
        "ll_ci_below_0": res["dLL_ci"][1] < 0,
        "calib_all": abs(cand["all"]["gap"]) <= 0.025,
        "calib_over": cand["over"]["n"] > 0 and abs(cand["over"]["gap"]) <= 0.025,
        "calib_under": cand["under"]["n"] > 0 and abs(cand["under"]["gap"]) <= 0.025,
        "over_share": abs(cand["over_share"] - rate) <= abs(live["over_share"] - rate) + 0.05,
        "graded_count": res["graded_count_ratio"] >= 0.90,
    }
    res["checks"] = checks
    res["passes"] = all(checks.values())
    return res


def main(src, dst):
    t0 = time.time()
    rows = load(src)
    bk = proj_nba.Book(rows)
    days = np.array([r["gday"] for r in rows])
    fa = np.array([_t(r["forecast_at"]).timestamp() for r in rows])
    st = np.array([_t(r["start"]).timestamp() for r in rows])
    games = np.array([r["event"] for r in rows])
    ud = sorted(set(days))
    tdays = ud[3:]
    test = np.flatnonzero(np.isin(days, tdays))
    po = {k: np.zeros(bk.n) for k in SPECS}
    mean = {k: bk.mu.copy() for k in SPECS}
    params = {k: [] for k in SPECS}
    windows = []
    for D in tdays:
        te = np.flatnonzero(days == D)
        cutoff = fa[te].min()
        tr = np.flatnonzero(st + 4 * 3600 <= cutoff)
        assert not np.isin(tr, te).any()
        windows.append({"test_day": D, "n_test": int(len(te)), "n_train": int(len(tr)),
                        "train_through": dt.datetime.fromtimestamp(st[tr].max(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "first_forecast": dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        for k in SPECS:
            p, m, fitted = proj_nba.run(bk, k, tr, te)
            po[k][te], mean[k][te] = p, m
            params[k].append({"day": D, **{f.name: _par(f) for f in fitted}})
        print(D, len(tr), len(te), round(time.time() - t0), flush=True)
    bt = Boot(games[test])
    over = bk.over[test]
    line = bk.line[test]
    live_po = np.array([rows[i]["po_live"] for i in test])
    re_po = bk.p_over(test, np.zeros(len(test)), np.ones(len(test)))
    live = evaluate(live_po, bk.mu[test], line, over, bt)
    re = evaluate(re_po, bk.mu[test], line, over, bt)
    rep = {"version": proj_nba.VERSION, "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "protocol": __doc__.split("Protocol:")[1].split("python -m")[0].strip(),
           "n_rows_all": int(bk.n), "n_test": int(len(test)), "n_games_test": int(len(np.unique(games[test]))),
           "game_days": ud, "windows": windows, "live": strip(live), "recomputed_sm1": strip(re),
           "recomputed_vs_live": compare(live, re, bt), "candidates": {}}
    for k in SPECS:
        c = evaluate(po[k][test], mean[k][test], line, over, bt)
        rep["candidates"][k] = {"eval": strip(c), "vs_live": compare(live, c, bt), "vs_recomputed": compare(re, c, bt),
                                "last_params": params[k][-1]}
        cc = rep["candidates"][k]
        print(f"{k:10s} dLL {cc['vs_live']['dLL']:+.4f} {cc['vs_live']['dLL_ci']} gap {c['all']['gap']:+.3f} "
              f"O {c['over'].get('gap')} U {c['under'].get('gap')} overshare {c['over_share']:.3f} graded "
              f"{c['graded']['n']} hit {c['graded'].get('hit')} pass {cc['vs_live']['passes']} {cc['vs_live']['checks']}")
    print("live", live["all"], "graded", live["graded"]["n"], live["graded"].get("hit"), "overshare", live["over_share"])
    json.dump(rep, open(dst, "w"), indent=1, default=float)
    print("wrote", dst, round(time.time() - t0), "s")


def _par(f):
    if f.name == "ma":
        return {k: [round(float(x), 4) for x in v] for k, v in f.coef.items()}
    if f.name == "md":
        return {k: float(v) for k, v in f.a.items()}
    if f.name == "sk":
        return {k: [float(x) for x in v] for k, v in f.par.items()}
    if f.name == "rm":
        return {"beta": None if f.beta is None else [round(float(x), 4) for x in f.beta],
                "thin_rate": {k: round(v, 4) for k, v in f.rate.items()},
                "thin_sd_ratio": {k: round(v, 3) for k, v in f.sdr.items()}}
    return {}


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
