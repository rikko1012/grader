"""sacc-1109: NBA book-line population joined to the frozen live sm-1 distribution + pre-game features.

Read-only. Inputs (local copies, never production):
  export  : gradecore export (service-settled sm-1 forecasts at offered lines, >= 30 min before tip)
  frame   : pnf-1 feature frame (one row per played player-game, pre-game features)
  preds   : sm1frozen_<stat>__final_*.npz (the frozen live artifact sm1_nba.json applied offline)

  python -m sports.research.sacc_1109.nba_lines <export.json> <frame.pkl> <pred_dir> <out.pkl>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

STATS = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}


def load_preds(pred_dir, name, stat):
    out = {}
    for p in sorted(Path(pred_dir).glob(f"{name}_{stat}__final_*.npz")):
        z = np.load(p)
        lo = int(z["lo"])
        for i, k in enumerate(z["keys"]):
            out[str(k)] = (z["pmf"][i].astype(float), lo, float(z["mean"][i]))
    return out


def p_line(pmf, lo, line):
    ks = lo + np.arange(len(pmf), dtype=float)
    po = pmf[ks > line].sum()
    pu = pmf[ks < line].sum()
    return po, pu


def sd_of(pmf, lo):
    ks = lo + np.arange(len(pmf), dtype=float)
    m = (pmf * ks).sum() / pmf.sum()
    return float(np.sqrt((pmf * (ks - m) ** 2).sum() / pmf.sum()))


def build(export, frame, pred_dir, name="sm1frozen"):
    rows = json.loads(Path(export).read_text(encoding="utf-8"))["rows"]
    ex = pd.DataFrame(rows)
    ex = ex[ex["status"].isin(["win", "loss"])].copy()
    ex["po_live"] = np.where(ex["side"] == "over", ex["p"], 1 - ex["p"])
    ex["game_id"], ex["key"] = ex["match_key"], ex["match_key"] + "|" + ex["player_id"]
    f = pd.read_pickle(frame)
    f["key"] = f["game_id"] + "|" + f["player_id"]
    f = f.drop_duplicates("key").set_index("key")
    preds = {mk: load_preds(pred_dir, name, mk) for mk in STATS}
    recs = []
    for r in ex.itertuples(index=False):
        pr = preds[r.market].get(r.key)
        if pr is None or r.key not in f.index:
            continue
        pmf, lo, mean = pr
        po, pu = p_line(pmf, lo, r.line)
        fr = f.loc[r.key]
        y = float(fr[STATS[r.market]])
        if y == r.line:
            continue
        recs.append({"game_id": r.game_id, "player_id": r.player_id, "key": r.key, "market": r.market,
                     "line": float(r.line), "book": r.book, "forecast_at": r.forecast_at, "start_at": r.start_at,
                     "po_live": float(r.po_live), "po_off": po / max(po + pu, 1e-12), "mean": mean,
                     "sd": sd_of(pmf, lo), "y": y, "over": float(y > r.line), "min": float(fr["min"])})
    d = pd.DataFrame(recs)
    feats = ["prior_n", "min_ew10", "min_sd10", "min_l3", "min_last", "starter_lag", "starter_r10", "season_n",
             "days_since_played", "team_changed", "spread_home", "total_line", "team_spread", "abs_spread", "b2b",
             "days_rest", "out_min", "out_usage", "out_n", "out_starters", "usage_share_out", "home", "pos_g",
             "missed_since_last", "season_min_avg", "usage_pg_ew10", "name", "team"]
    d = d.join(f[feats], on="key")
    return d


if __name__ == "__main__":
    d = build(*sys.argv[1:4])
    d.to_pickle(sys.argv[4])
    print(len(d), d["game_id"].nunique(), "corr live vs offline", np.corrcoef(d["po_live"], d["po_off"])[0, 1],
          "mean abs diff", float(np.abs(d["po_live"] - d["po_off"]).mean()))
