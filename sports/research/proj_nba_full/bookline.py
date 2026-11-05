"""Second-stage test of pnf-1 at the OFFERED book lines: every settled sm-1 NBA prop held locally (nba_full.db
settled_props: the read-only gradecore export 2026-10-24..11-03 plus the latest-2,000 results report rows of Nov 5),
scored by (a) the chance the live record printed, (b) the frozen sm-1 artifact recomputed offline on the same row (how
close the offline reconstruction is), (c) pnf-1 from the final-window walk-forward predictions.

One row per offer (game, player, stat, line, book): export rows, then results rows not in the export (the last
forecast >= 30 min before tip). Pushes and actual == line dropped. The chance at a line is read from ONE distribution
per player-game: P(Over | no push) = P(X > line) / (P(X > line) + P(X < line)) - exactly how forward.build_forecast
reads sm-1. No shrink, no side rule, no line input.

Calibration (stated vs hit by chosen side, 5-pt bins, Over and Under separately) and selection (graded hit rate:
chosen side >= 53.45% (sports-gp-1 C band) AND the projection mean on that side - the first attempt's board lean rule)
are reported separately with whole-game bootstrap CIs.
Pass = all-outcome log score CI > 0 (walkforward report) AND |stated - hit| <= 2.5 pts overall, Over and Under AND
graded count >= 90% of live AND graded hit >= live.

  python -m sports.research.proj_nba_full.bookline <pred_dir> <out.json> [nba_full.db]
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from sports.model import proj_nba_full as pnf
from sports.research.proj_nba_full import data as D
from sports.research.proj_nba_full.walkforward import Boot, _load

C_BAND = 0.5345


def _t(s):
    return pd.to_datetime(s, utc=True)


def load_props(props):
    p = props[props["model_version"] == "sm-1"].copy()
    ex = p[p["src"].str.startswith("gradecore")].copy()
    ex["po_live"] = np.where(ex["side"] == "over", ex["p_side"], 1 - ex["p_side"])
    key = ["game_id", "player_id", "market", "line", "book"]
    seen = set(map(tuple, ex[key].to_numpy()))
    rs = p[p["src"].str.startswith("results")].copy()
    rs = rs[(_t(rs["start_at"]) - _t(rs["forecast_at"])).dt.total_seconds() >= 1800]
    rs = rs.sort_values("forecast_at").drop_duplicates(key, keep="last")
    rs = rs[[tuple(r) not in seen for r in rs[key].to_numpy()]].copy()
    rs["po_live"] = rs["p_over"] / (rs["p_over"] + rs["p_under"])
    out = pd.concat([ex, rs], ignore_index=True)
    out["actual"] = out["actual_box"]
    out = out[out["actual"].notna() & (out["actual"] != out["line"])].copy()
    out["over"] = (out["actual"] > out["line"]).astype(float)
    out["key"] = out["game_id"] + "|" + out["player_id"]
    return out.reset_index(drop=True)


def _p_line(pmf, lo, line):
    ks = lo + np.arange(pmf.shape[1], dtype=float)
    L = np.asarray(line, float)[:, None]
    po = (pmf * (ks[None, :] > L)).sum(1)
    pu = (pmf * (ks[None, :] < L)).sum(1)
    return np.clip(po / np.maximum(po + pu, 1e-12), 1e-4, 1 - 1e-4)


def attach(props, pred_dir, name, wtype="final", by_refit=True):
    """po_<name>, mean_<name> per prop from the saved window predictions (NaN when the player-game was not scored).
    Each prop takes the prediction of the LATEST refit made at or before its forecast_at (window files are named by
    refit date; 'xtra_' files carry a refit's predictions for games after its own window)."""
    po = np.full(len(props), np.nan)
    mu = np.full(len(props), np.nan)
    fa = pd.to_datetime(props["forecast_at"], utc=True).to_numpy()
    best = np.full(len(props), np.datetime64("NaT"), dtype="datetime64[ns]")
    for mk in pnf.STATS:
        W = _load(pred_dir, f"{name}_{mk}")
        m = (props["market"] == mk).to_numpy()
        for w, z in W.items():
            if not (w.startswith(wtype) or w.startswith("xtra")):
                continue
            if by_refit:
                refit = np.datetime64(pd.Timestamp(w.split("_", 1)[1], tz="UTC").tz_convert(None), "ns")
            else:                                        # one frozen artifact: any window file
                refit = np.datetime64("2000-01-01", "ns")
            ix = {k: i for i, k in enumerate(z["keys"])}
            sel = np.flatnonzero(m & (fa.astype("datetime64[ns]") >= refit)
                                 & (np.isnat(best) | (best < refit)))
            j = np.array([ix.get(k, -1) for k in props["key"].to_numpy()[sel]], dtype=int)
            ok = j >= 0
            if not ok.any():
                continue
            rows = sel[ok]
            best[rows] = refit
            po[rows] = _p_line(z["pmf"][j[ok]].astype(float), int(z["lo"]), props["line"].to_numpy()[rows])
            mu[rows] = z["mean"][j[ok]]
    props[f"po_{name}"] = po
    props[f"mean_{name}"] = mu
    return props


def evaluate(po, mean, line, over, bt):
    po = np.asarray(po, float)
    so = po >= 0.5
    ps = np.where(so, po, 1 - po)
    hit = np.where(so, over, 1 - over)
    pc = np.clip(po, 1e-4, 1 - 1e-4)
    ll = -(over * np.log(pc) + (1 - over) * np.log(1 - pc))
    graded = (ps >= C_BAND) & np.where(so, mean > line, mean < line)
    out = {"n": int(len(po)), "logloss": round(float(ll.mean()), 5), "over_share": round(float(so.mean()), 4),
           "data_over_rate": round(float(over.mean()), 4)}

    def sg(m):
        if m.sum() == 0:
            return {"n": 0}
        return {"n": int(m.sum()), "stated": round(float(ps[m].mean()), 4), "hit": round(float(hit[m].mean()), 4),
                "gap": round(float(ps[m].mean() - hit[m].mean()), 4), "gap_ci": bt.mean_ci(ps - hit, m)}
    out["all"], out["over"], out["under"] = sg(np.ones(len(po), bool)), sg(so), sg(~so)
    bins = []
    for side, m0 in (("over", so), ("under", ~so)):
        for b in np.arange(0.50, 1.0, 0.05):
            m = m0 & (ps >= b) & (ps < b + 0.05 + (1e-9 if b >= 0.95 else 0))
            if m.sum():
                bins.append({"side": side, "bin": f"{b:.2f}-{b + 0.05:.2f}", "n": int(m.sum()),
                             "stated": round(float(ps[m].mean()), 4), "hit": round(float(hit[m].mean()), 4)})
    out["bins"] = bins
    out["graded"] = sg(graded)
    out["graded"]["over_n"] = int((graded & so).sum())
    out["graded"]["under_n"] = int((graded & ~so).sum())
    out["graded_over"], out["graded_under"] = sg(graded & so), sg(graded & ~so)
    out["_ll"], out["_graded"], out["_hit"] = ll, graded, hit
    return out


def compare(live, cand, bt):
    dll = cand["_ll"] - live["_ll"]
    gl, gc = live["_graded"], cand["_graded"]
    C = bt.C
    n_c = C @ np.bincount(bt.g, weights=gc.astype(float), minlength=len(bt.ug))
    h_c = C @ np.bincount(bt.g, weights=cand["_hit"] * gc, minlength=len(bt.ug))
    n_l = C @ np.bincount(bt.g, weights=gl.astype(float), minlength=len(bt.ug))
    h_l = C @ np.bincount(bt.g, weights=live["_hit"] * gl, minlength=len(bt.ug))
    hd = h_c / np.maximum(n_c, 1e-9) - h_l / np.maximum(n_l, 1e-9)
    res = {"dLL": round(float(dll.mean()), 5), "dLL_ci": bt.mean_ci(dll),
           "graded_hit_diff": round(float(cand["_hit"][gc].mean() - live["_hit"][gl].mean()), 4),
           "graded_hit_diff_ci": [round(float(np.percentile(hd, 2.5)), 4), round(float(np.percentile(hd, 97.5)), 4)],
           "graded_count_ratio": round(float(gc.sum() / max(gl.sum(), 1)), 4)}
    res["checks"] = {
        "calib_all": abs(cand["all"]["gap"]) <= 0.025,
        "calib_over": cand["over"]["n"] > 0 and abs(cand["over"]["gap"]) <= 0.025,
        "calib_under": cand["under"]["n"] > 0 and abs(cand["under"]["gap"]) <= 0.025,
        "graded_count": res["graded_count_ratio"] >= 0.90,
        "graded_hit_not_worse": res["graded_hit_diff"] >= 0,
    }
    res["passes_line_checks"] = all(res["checks"].values())
    return res


def strip(e):
    return {k: v for k, v in e.items() if not k.startswith("_")}


def main(pred_dir, out_json, db=None):
    raw = D.load(db)
    props = load_props(raw[4])
    n_all = len(props)
    props = attach(props, pred_dir, "sm1frozen", by_refit=False)
    props = attach(props, pred_dir, "pnf")
    ok = props["po_sm1frozen"].notna() & props["po_pnf"].notna()
    dropped = props[~ok]
    P = props[ok].reset_index(drop=True)
    bt = Boot(P["game_id"].to_numpy())
    line, over = P["line"].to_numpy(float), P["over"].to_numpy(float)
    live = evaluate(P["po_live"], P["mean_sm1frozen"], line, over, bt)
    rec = evaluate(P["po_sm1frozen"], P["mean_sm1frozen"], line, over, bt)
    cand = evaluate(P["po_pnf"], P["mean_pnf"], line, over, bt)
    d = np.abs(P["po_sm1frozen"] - P["po_live"])
    rep = {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "protocol": __doc__.split("python -m")[0].strip(),
           "rows_loaded": n_all, "rows_scored": int(len(P)), "games": int(P["game_id"].nunique()),
           "dropped_unscored": {"n": int(len(dropped)), "by_market": dropped["market"].value_counts().to_dict()},
           "by_src": P["src"].value_counts().to_dict(),
           "reconstruction": {"abs_diff_median": round(float(d.median()), 4),
                              "share_within_0.02": round(float((d <= 0.02).mean()), 4),
                              "share_within_0.05": round(float((d <= 0.05).mean()), 4),
                              "same_side": round(float(((P["po_sm1frozen"] >= .5) == (P["po_live"] >= .5)).mean()), 4)},
           "live": strip(live), "recomputed_sm1": strip(rec), "pnf": strip(cand),
           "pnf_vs_live": compare(live, cand, bt), "pnf_vs_recomputed": compare(rec, cand, bt),
           "recomputed_vs_live": compare(live, rec, bt), "by_market": {}}
    for mk in pnf.STATS:
        m = (P["market"] == mk).to_numpy()
        if m.sum() < 50:
            continue
        b2 = Boot(P["game_id"].to_numpy()[m])
        lv = evaluate(P["po_live"][m], P["mean_sm1frozen"][m], line[m], over[m], b2)
        cd = evaluate(P["po_pnf"][m], P["mean_pnf"][m], line[m], over[m], b2)
        rep["by_market"][mk] = {"live": {k: strip(lv)[k] for k in ("n", "logloss", "all", "over", "under", "graded")},
                                "pnf": {k: strip(cd)[k] for k in ("n", "logloss", "all", "over", "under", "graded")},
                                "pnf_vs_live": compare(lv, cd, b2)}
    Path(out_json).write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    for nm, e in (("live", live), ("recomputed", rec), ("pnf", cand)):
        print(nm, "LL", e["logloss"], "all", e["all"]["stated"], e["all"]["hit"], "O", e["over"]["n"], e["over"]["gap"],
              "U", e["under"]["n"], e["under"]["gap"], "graded", e["graded"]["n"], e["graded"].get("hit"),
              "overshare", e["over_share"])
    print("pnf_vs_live", rep["pnf_vs_live"])
    print("reconstruction", rep["reconstruction"], "dropped", rep["dropped_unscored"])
    return rep


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
