"""sacc-1109 FRESH book-line window (NBA games after the 2026-11-06 export, i.e. tip >= 2026-11-06T03:00Z): the live
service's saved sm-1 raw forecasts (read-only gradecore-comparison pages), one per offer = the last forecast >= 30 min
before tip, scored offline against the ESPN box score (a player who did not play = void, dropped). Same columns as
nba_lines.build so cand / eval_lines run unchanged. Nothing here was looked at while the candidates were built.

  python -m research.sports_lab.sacc_1109.fresh_lines <cmp.json> <nba_full.db> <frame.pkl> <wf_dir> <out.pkl>
"""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np
import pandas as pd

from research.sports_lab.sacc_1109.nba_lines import STATS, load_preds, p_line, sd_of

FRESH_FROM = "2026-11-06T03:00:00Z"


def build(cmp_json, db, frame, wf_dir):
    rows = json.load(open(cmp_json))["rows"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    starts = {g: s for g, s, st in con.execute("SELECT game_id, start_utc, status FROM games") if st == "final"}
    recs = []
    for r in rows:
        st = starts.get(r["event"])
        if st is None or st < FRESH_FROM or r["stat"] not in STATS or r.get("period", "full_game") != "full_game":
            continue
        if (pd.Timestamp(st) - pd.Timestamp(r["at"])).total_seconds() < 1800:
            continue
        so, su = (r.get("sides") or {}).get("over") or {}, (r.get("sides") or {}).get("under") or {}
        ro, ru = so.get("raw"), su.get("raw")
        if ro is None or ru is None or not (ro + ru) > 0:
            continue
        recs.append({"game_id": r["event"], "player_id": r["player"], "market": r["stat"], "line": float(r["line"]),
                     "book": r["book"], "forecast_at": r["at"], "start_at": st, "po_live": ro / (ro + ru)})
    c = pd.DataFrame(recs).sort_values("forecast_at")
    c = c.drop_duplicates(["game_id", "player_id", "market", "line", "book"], keep="last").reset_index(drop=True)
    c["key"] = c["game_id"] + "|" + c["player_id"]
    f = pd.read_pickle(frame)
    f["key"] = f["game_id"] + "|" + f["player_id"]
    f = f.drop_duplicates("key").set_index("key")
    preds = {mk: load_preds(wf_dir, "sm1frozen", mk) for mk in STATS}
    out, void = [], 0
    for r in c.itertuples(index=False):
        if r.key not in f.index:
            void += 1                                   # did not play (or no box row): pick'em void
            continue
        pr = preds[r.market].get(r.key)
        if pr is None:
            continue
        y = float(f.loc[r.key, STATS[r.market]])
        if y == r.line:
            continue
        pmf, lo, mean = pr
        po, pu = p_line(pmf, lo, r.line)
        out.append({**r._asdict(), "po_off": po / max(po + pu, 1e-12), "mean": mean, "sd": sd_of(pmf, lo), "y": y,
                    "over": float(y > r.line), "min": float(f.loc[r.key, "min"])})
    d = pd.DataFrame(out)
    feats = ["prior_n", "min_ew10", "min_sd10", "min_l3", "min_last", "starter_lag", "starter_r10", "season_n",
             "days_since_played", "team_changed", "spread_home", "total_line", "team_spread", "abs_spread", "b2b",
             "days_rest", "out_min", "out_usage", "out_n", "out_starters", "usage_share_out", "home", "pos_g",
             "missed_since_last", "season_min_avg", "usage_pg_ew10", "name", "team"]
    d = d.join(f[feats], on="key")
    return d, {"offers": int(len(c)), "void_or_no_box": void, "rows": int(len(d)), "games": int(d["game_id"].nunique())}


if __name__ == "__main__":
    d, info = build(*sys.argv[1:5])
    d.to_pickle(sys.argv[5])
    print(info, "corr live/off", float(np.corrcoef(d["po_live"], d["po_off"])[0, 1]),
          d["start_at"].min(), d["start_at"].max())
