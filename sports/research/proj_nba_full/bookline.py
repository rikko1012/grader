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

Rerun 2026-11-06 (optional --comparison / --export): adds the read-only gradecore-comparison download (every saved sm-1
raw forecast 2026-11-04 00:24Z .. 11-06 03:17Z, NO outcomes) for the 14 games of Nov 4 US (Nov 5 UTC). Per offer the
LAST forecast >= 30 min before tip (start from nba_full.db games) is kept and scored OFFLINE against the nba_full.db box
score (player must have played; DNP = void). Source priority per offer: service export > service results ledger >
offline-scored comparison. --export restricts every source to the CURRENT live tag: export rows must carry that
artifact_version; comparison rows are sm-1 raw forecasts only (gradecore adapter rule) produced while that artifact
was the live one.

  python -m sports.research.proj_nba_full.bookline <pred_dir> <out.json> [nba_full.db]
                                                   [--comparison <gradecore_comparison_nba.json>]
                                                   [--export <gradecore_export_nba.json>] [--live-tag sm-1:<sha>]
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


KEY = ["game_id", "player_id", "market", "line", "book"]


def export_tags(path):
    """forecast_id -> artifact_version from the read-only gradecore export (settled_props does not carry it)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["rows"]
    return {r["forecast_id"]: r.get("artifact_version") for r in rows}


def load_comparison(path, games, box):
    """gradecore-comparison rows (sm-1 raw forecasts, no outcomes) -> one row per offer: the last forecast >= 30 min
    before tip, scored offline against the box score. games: game_id -> start_utc of FINAL games; box: (game_id,
    player_id) -> {stat: value} for PLAYED rows. Returns (frame, counts)."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))["rows"]
    cnt = {"rows": len(rows), "not_final_game": 0, "under_30_min": 0, "bad_probability": 0}
    recs = []
    for r in rows:
        st = games.get(r["event"])
        if st is None:
            cnt["not_final_game"] += 1
            continue
        if (_t(st) - _t(r["at"])).total_seconds() < 1800:
            cnt["under_30_min"] += 1
            continue
        so, su = (r.get("sides") or {}).get("over") or {}, (r.get("sides") or {}).get("under") or {}
        ro, ru = so.get("raw"), su.get("raw")
        if ro is None or ru is None or not (ro + ru) > 0:
            cnt["bad_probability"] += 1
            continue
        recs.append({"src": "gradecore_comparison_2026-11-06_offline_scored", "forecast_id": r["forecast_id"],
                     "model_version": "sm-1", "game_id": r["event"], "player_id": r["player"], "market": r["stat"],
                     "line": float(r["line"]), "book": r["book"], "forecast_at": r["at"], "start_at": st,
                     "po_live": ro / (ro + ru), "grade_policy": r.get("policy")})
    c = pd.DataFrame(recs)
    c = c.sort_values("forecast_at").drop_duplicates(KEY, keep="last").reset_index(drop=True)
    cnt["offers"] = int(len(c))
    act, played = [], []
    for g, p, mk in zip(c["game_id"], c["player_id"], c["market"]):
        b = box.get((g, p))
        played.append(b is not None)
        act.append(np.nan if b is None else b.get(pnf.STATS.get(mk, mk), np.nan))
    c["actual_box"] = act
    cnt["void_did_not_play"] = int((~np.asarray(played)).sum())
    cnt["games"] = int(c["game_id"].nunique())
    return c[np.asarray(played)].reset_index(drop=True), cnt


def load_props(props, comparison=None, tags=None, live_tag=None):
    p = props[props["model_version"] == "sm-1"].copy()
    ex = p[p["src"].str.startswith("gradecore")].copy()
    ex["po_live"] = np.where(ex["side"] == "over", ex["p_side"], 1 - ex["p_side"])
    if tags is not None:
        ex["artifact_version"] = ex["forecast_id"].map(tags)
        if live_tag is not None:
            ex = ex[ex["artifact_version"] == live_tag].copy()
    seen = set(map(tuple, ex[KEY].to_numpy()))
    rs = p[p["src"].str.startswith("results")].copy()
    rs = rs[(_t(rs["start_at"]) - _t(rs["forecast_at"])).dt.total_seconds() >= 1800]
    rs = rs.sort_values("forecast_at").drop_duplicates(KEY, keep="last")
    rs = rs[np.array([tuple(r) not in seen for r in rs[KEY].to_numpy()], dtype=bool)].copy()
    rs["po_live"] = rs["p_over"] / (rs["p_over"] + rs["p_under"])
    parts = [ex, rs]
    if comparison is not None and len(comparison):
        seen |= set(map(tuple, rs[KEY].to_numpy()))
        cm = comparison[np.array([tuple(r) not in seen for r in comparison[KEY].to_numpy()], dtype=bool)].copy()
        parts.append(cm)
    out = pd.concat(parts, ignore_index=True)
    out["actual"] = out["actual_box"]
    return _finish(out)


def _finish(out):
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


def _games_box(db):
    con = D._ro(db or D.default_db())
    games = dict(con.execute("SELECT game_id, start_utc FROM games WHERE status = 'final'").fetchall())
    cols = sorted(set(pnf.STATS.values()))
    box = {(g, p): dict(zip(cols, v)) for g, p, *v in
           con.execute(f"SELECT game_id, player_id, {', '.join(cols)} FROM player_games WHERE played = 1").fetchall()}
    con.close()
    return games, box


def main(pred_dir, out_json, db=None, comparison=None, export=None, live_tag=None):
    raw = D.load(db)
    comp, comp_counts = (None, None)
    if comparison:
        games, box = _games_box(db)
        comp, comp_counts = load_comparison(comparison, games, box)
    tags = export_tags(export) if export else None
    props = load_props(raw[4], comp, tags, live_tag)
    n_all = len(props)
    props = attach(props, pred_dir, "sm1frozen", by_refit=False)
    props = attach(props, pred_dir, "pnf")
    ok = props["po_sm1frozen"].notna() & props["po_pnf"].notna()
    dropped = props[~ok]
    P = props[ok].reset_index(drop=True)
    rep = _report(P, n_all, dropped)
    rep["live_tag"] = live_tag
    rep["export_tags_seen"] = (pd.Series(tags).value_counts().to_dict() if tags else None)
    rep["comparison_counts"] = comp_counts
    rep["subsets"] = {}
    for nm, m in (("service_settled_only", ~P["src"].str.startswith("gradecore_comparison")),
                  ("nov5utc_games_all_sources", _t(P["start_at"]).dt.strftime("%Y-%m-%d") == "2026-11-05"),
                  ("offline_scored_only", P["src"].str.startswith("gradecore_comparison"))):
        m = m.to_numpy()
        if m.sum() >= 50:
            sub = _report(P[m].reset_index(drop=True), int(m.sum()), P.iloc[:0], markets=False)
            rep["subsets"][nm] = {k: sub[k] for k in ("rows_scored", "games", "by_src", "live", "pnf", "pnf_vs_live")}
            for k in ("live", "pnf"):
                rep["subsets"][nm][k] = {kk: sub[k][kk] for kk in ("n", "logloss", "all", "over", "under", "graded")}
    Path(out_json).write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    for nm, k in (("live", "live"), ("recomputed", "recomputed_sm1"), ("pnf", "pnf")):
        e = rep[k]
        print(nm, "LL", e["logloss"], "all", e["all"]["stated"], e["all"]["hit"], "O", e["over"]["n"], e["over"]["gap"],
              "U", e["under"]["n"], e["under"]["gap"], "graded", e["graded"]["n"], e["graded"].get("hit"),
              "overshare", e["over_share"])
    print("pnf_vs_live", rep["pnf_vs_live"])
    print("reconstruction", rep["reconstruction"], "dropped", rep["dropped_unscored"], "comparison", comp_counts)
    return rep


def _report(P, n_all, dropped, markets=True):
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
    for mk in (pnf.STATS if markets else ()):
        m = (P["market"] == mk).to_numpy()
        if m.sum() < 50:
            continue
        b2 = Boot(P["game_id"].to_numpy()[m])
        lv = evaluate(P["po_live"][m], P["mean_sm1frozen"][m], line[m], over[m], b2)
        cd = evaluate(P["po_pnf"][m], P["mean_pnf"][m], line[m], over[m], b2)
        rep["by_market"][mk] = {"live": {k: strip(lv)[k] for k in ("n", "logloss", "all", "over", "under", "graded")},
                                "pnf": {k: strip(cd)[k] for k in ("n", "logloss", "all", "over", "under", "graded")},
                                "pnf_vs_live": compare(lv, cd, b2)}
    return rep


def _cli(argv):
    pos, opt, i = [], {}, 0
    while i < len(argv):
        if argv[i].startswith("--"):
            opt[argv[i][2:].replace("-", "_")] = argv[i + 1]
            i += 2
        else:
            pos.append(argv[i])
            i += 1
    return main(pos[0], pos[1], pos[2] if len(pos) > 2 else None, **opt)


if __name__ == "__main__":
    _cli(sys.argv[1:])
