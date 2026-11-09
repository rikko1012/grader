"""hit-1109: FRESH NFL book-line window (games after the 2026-11-06 export: 2026 week 9 Sunday Nov 8 and later finals).

Never looked at while nrm-1 was built (gamma frozen on box scores through the week-9 TNF game; the A12 registration
committed before this window was scored). Rows: the live service's saved sm-1 forecasts (read-only gradecore-comparison
pages, raw chance), one per offer = the last forecast >= 30 min before kick-off, PrizePicks / Underdog / every book.
Outcome: ESPN's public box score (cached under SPORTS_DATA_DIR - point it at a scratch folder). The base sm-1 PMF is
re-run offline from the frozen artifact on an upcoming-row frame built from history known before the game (as the
forward job does), and nrm-1 moves that PMF by its frozen multiplier.

  python -m sports.research.hit_1109.fresh_nfl <cmp.json> <history.db> <out.pkl>
"""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np
import pandas as pd

from sports import config, espn
from sports.model import dists, featurize, forward, nrm1, study

FRESH_FROM = "2026-11-06T12:00:00Z"
COL = {"rush_att": "rush_att", "rec": "rec", "rec_yds": "rec_yds", "rush_yds": "rush_yds", "pass_yds": "pass_yds"}


def build(cmp_json, hist_db, out_pkl):
    rows = json.load(open(cmp_json))["rows"]
    con = sqlite3.connect("file:" + hist_db.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    games = {str(r["espn_id"]): dict(r) for r in con.execute("SELECT * FROM games WHERE league = 'nfl' AND espn_id IS "
                                                              "NOT NULL")}
    ids = {}
    for r in con.execute("SELECT gsis_id, espn_id FROM nfl_ids WHERE espn_id IS NOT NULL"):
        try:
            ids[str(int(float(r[1])))] = r[0]
        except ValueError:
            continue
    recs = []
    for r in rows:
        ev = r["event"].split(":")[-1]
        g = games.get(ev)
        if g is None or g["start_utc"] < FRESH_FROM or r["stat"] not in COL or r.get("period", "full_game") != "full_game":
            continue
        if (pd.Timestamp(g["start_utc"]) - pd.Timestamp(r["at"])).total_seconds() < 1800:
            continue
        so, su = (r.get("sides") or {}).get("over") or {}, (r.get("sides") or {}).get("under") or {}
        ro, ru = so.get("raw"), su.get("raw")
        if ro is None or ru is None or not (ro + ru) > 0:
            continue
        recs.append({"espn_event": ev, "game_id": g["game_id"], "espn_player": r["player"].split(":")[-1],
                     "market": r["stat"], "line": float(r["line"]), "book": r["book"], "forecast_at": r["at"],
                     "start_at": g["start_utc"], "po_live": ro / (ro + ru)})
    c = pd.DataFrame(recs).sort_values("forecast_at")
    c = c.drop_duplicates(["game_id", "espn_player", "market", "line", "book"], keep="last").reset_index(drop=True)
    # outcomes
    lg = config.league("nfl")
    box = {}
    for ev in sorted(c["espn_event"].unique()):
        s = espn.summary(lg, ev)
        if not s.get("completed"):
            continue
        for rows_ in s["players"].values():
            for p in rows_:
                box[(ev, str(p["id"]))] = p
    c = c[[(e, p) in box for e, p in zip(c["espn_event"], c["espn_player"])]].reset_index(drop=True)
    c["y"] = [float(box[(e, p)]["parts"].get(COL[m], np.nan)) for e, p, m in zip(c["espn_event"], c["espn_player"],
                                                                                 c["market"])]
    c = c[np.isfinite(c["y"]) & (c["y"] != c["line"])].reset_index(drop=True)
    c["over"] = (c["y"] > c["line"]).astype(float)
    c["player_id"] = c["espn_player"].map(ids)
    c = c[c["player_id"].notna()].reset_index(drop=True)
    # upcoming rows from the player's last known row
    up, seen = [], set()
    for gid, pid in zip(c["game_id"], c["player_id"]):
        if (gid, pid) in seen:
            continue
        seen.add((gid, pid))
        last = con.execute("SELECT p.team, p.position FROM nfl_pg p JOIN games g ON g.game_id = p.game_id WHERE "
                           "p.player_id = ? ORDER BY g.start_utc DESC LIMIT 1", (pid,)).fetchone()
        g = con.execute("SELECT * FROM games WHERE game_id = ?", (gid,)).fetchone()
        if last is None or last[0] not in (g["home"], g["away"]):
            continue
        home = int(last[0] == g["home"])
        up.append({"game_id": gid, "player_id": pid, "team": last[0], "opp": g["away"] if home else g["home"],
                   "home": home, "start_utc": g["start_utc"], "position": last[1], "season": g["season"],
                   "week": g["week"], "season_type": g["season_type"]})
    as_of = min(u["start_utc"] for u in up)
    as_of = (pd.Timestamp(as_of) - pd.Timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    sm1 = forward.load_artifact("nfl")
    art = nrm1.load()
    c["po_base"], c["mean_base"], c["po_nrm1"], c["mean_nrm1"] = np.nan, np.nan, np.nan, np.nan
    for mk in sorted(c["market"].unique()):
        am = sm1["markets"][mk]
        df = study._nfl_fill(featurize.nfl_frame(con, h_opp=tuple(am["half_lives"])[0], upcoming=up, as_of=as_of,
                                                 with_lags=False))
        upi = np.flatnonzero(np.asarray(df["is_up"], float) == 1)
        pos = {(df["game_id"][i], df["player_id"][i]): i for i in upi}
        Z = nrm1.role_inputs(df, mk) if mk in nrm1.MARKETS else None
        lo, ks = dists.grid(mk)
        cache = {}
        for j in np.flatnonzero((c["market"] == mk).to_numpy()):
            gk = (c.at[j, "game_id"], c.at[j, "player_id"])
            if gk not in pos:
                continue
            if gk not in cache:
                p, _ = forward.pmf_from_row(am, forward._row_dict(df, pos[gk]), mk, "nfl")
                q = nrm1.remap(p, lo, nrm1.multiplier(Z[pos[gk]], art["markets"][mk]["gamma"])) if Z is not None else p
                cache[gk] = (np.asarray(p, float), np.asarray(q, float))
            p, q = cache[gk]
            for nm, x in (("base", p), ("nrm1", q)):
                po, pu, _ = dists.line_probs(x, lo, c.at[j, "line"])
                c.at[j, f"po_{nm}"] = po / max(po + pu, 1e-12)
                c.at[j, f"mean_{nm}"] = float(x @ ks)
    c = c[c["po_base"].notna()].reset_index(drop=True)
    c.to_pickle(out_pkl)
    print(len(c), c["game_id"].nunique(), c.groupby("market").size().to_dict(),
          "corr live/base", round(float(np.corrcoef(c["po_live"], c["po_base"])[0, 1]), 4),
          "mean |diff|", round(float(np.abs(c["po_live"] - c["po_base"]).mean()), 4))


if __name__ == "__main__":
    build(*sys.argv[1:4])
