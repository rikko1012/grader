"""Two-way NFL position: refit walk-forward (pre-registered in sports/research/two_way_position.md).

python -m sports.research.two_way_walkforward <history.db> [out.json]

Weekly: refit the frozen model_config_nfl configuration on everything known at as_of (forward.fit's recipe), forecast the
week's rows through upcoming rows with nfl_pg's raw position (the forward path), score against the actual results.
Arms A0 (SCOUT_NFL_TWO_WAY off), A1 (on), A2 (on + nfl.snap_trend in rec / rec_yds). Read-only on the history DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import numpy as np

from sports.model import datasets, dists, evaluate, featurize, forward, model, study

OFF = ("QB", "RB", "WR", "TE", "FB")
ARMS = {"A0": (False, {}), "A1": (True, {}), "A2": (True, {"rec": ["nfl.snap_trend"], "rec_yds": ["nfl.snap_trend"]})}
YCOL = featurize.NFL_MARKETS
CTX = ("home",)


def weeks(con):
    rows = con.execute("SELECT season, week, season_type, MIN(start_utc) s FROM games WHERE league='nfl' AND "
                       "status='final' AND ((season = 2025 AND week >= 10) OR season >= 2026) "
                       "GROUP BY season, week, season_type ORDER BY s").fetchall()
    return [dict(r) for r in rows]


def week_rows(con, w):
    q = ("SELECT p.*, g.start_utc FROM nfl_pg p JOIN games g ON g.league='nfl' AND g.game_id = p.game_id "
         "WHERE g.status='final' AND g.season=? AND g.week=? AND g.season_type=? AND (p.position IN "
         "('QB','RB','WR','TE','FB') OR (COALESCE(p.position,'') NOT IN " + featurize.NFL_NOT_TWO_WAY_SQL +
         " AND (COALESCE(p.offense_snaps,0) > 0 OR "
         "COALESCE(p.targets,0) + COALESCE(p.carries,0) + COALESCE(p.pass_att,0) > 0)))")
    return [dict(r) for r in con.execute(q, (w["season"], w["week"], w["season_type"]))]


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_week(con, cfg, w, arm, log=print):
    flag, extra = ARMS[arm]
    os.environ["SCOUT_NFL_TWO_WAY"] = "1" if flag else "0"
    first = datetime.fromisoformat(w["s"].replace("Z", "+00:00"))
    as_of = _iso(first - timedelta(hours=1))
    raw = week_rows(con, w)
    up = [{"game_id": r["game_id"], "player_id": r["player_id"], "team": r["team"], "opp": r["opp"], "home": r["home"],
           "start_utc": r["start_utc"], "position": r["position"] or "", "season": r["season"], "week": r["week"],
           "season_type": r["season_type"]} for r in raw]
    act = {(r["game_id"], r["player_id"]): r for r in raw}
    used = dict(con.execute("SELECT p.player_id, COUNT(*) FROM nfl_pg p JOIN games g ON g.league='nfl' AND "
                            "g.game_id = p.game_id WHERE g.start_utc < ? AND COALESCE(p.targets,0) + "
                            "COALESCE(p.carries,0) > 0 GROUP BY p.player_id", (w["s"],)).fetchall())
    out = []
    cur, df = None, None
    for mk in forward._ordered("nfl", cfg["markets"]):
        c = cfg["markets"][mk]
        hl = tuple(c["half_lives"])
        if hl != cur:
            df = None
            df = study._nfl_fill(featurize.nfl_frame(con, h_opp=hl[0], upcoming=up, as_of=as_of, with_lags=True))
            cur = hl
        is_up = np.asarray(df["is_up"], float) == 1
        el = model.eligible(df, mk)
        tr = study.sub(df, (~is_up) & el & np.isfinite(df.f(f"y_{mk}")))
        f = study.Fitted(mk, hl)
        study.mean_model(f, tr, tr, mk, "nfl", list(c["features"]) + extra.get(mk, []))
        study.fit_family_params(f, tr, mk, "nfl")
        f.family, f.cal = c["family"], tuple(c["cal"])
        te = study.sub(df, is_up)
        lo, ks = dists.grid(mk)
        p, mu = study.pmf_for(f, te, mk, "nfl")
        y = np.array([act[(g, pid)][YCOL[mk]] if act[(g, pid)][YCOL[mk]] is not None else 0.0
                      for g, pid in zip(te["game_id"], te["player_id"])], float)
        l10 = te.attrs["lags"][mk][:, :10]
        with np.errstate(all="ignore"):
            l_ref = np.floor(np.nanmean(l10, axis=1)) + 0.5
        l_ref = np.where(np.isfinite(l_ref), l_ref, 0.5)
        sc = evaluate.scores(p, lo, y, dists.mean_of(p, ks), l_ref)
        el_te = model.eligible(te, mk)
        for i in range(len(te)):
            r = act[(te["game_id"][i], te["player_id"][i])]
            out.append({"arm": arm, "week": f"{w['season']}-{w['season_type']}-{w['week']:02d}", "market": mk,
                        "game_id": te["game_id"][i], "player_id": te["player_id"][i], "name": r.get("name"),
                        "raw_position": r["position"], "position": te["position"][i],
                        "two_way": r["position"] not in OFF,
                        "prior_usage_games": int(used.get(r["player_id"], 0)), "eligible": bool(el_te[i]), "y": float(y[i]),
                        "mu": float(mu[i]), "l_ref": float(l_ref[i]), "crps": float(sc["crps"][i]),
                        "ae": float(sc["ae"][i]), "logloss_ref": float(sc["logloss_ref"][i]),
                        "p_over_ref": float(sc["p_over_ref"][i])})
    return out


def compare(rows, a, b, mk, pop):
    """Paired a - b on rows present in both arms; pop 'P' = eligible under A0, 'T' = two-way rows."""
    ka = {(r["game_id"], r["player_id"]): r for r in rows if r["arm"] == a and r["market"] == mk}
    kb = {(r["game_id"], r["player_id"]): r for r in rows if r["arm"] == b and r["market"] == mk}
    k0 = {(r["game_id"], r["player_id"]): r for r in rows if r["arm"] == "A0" and r["market"] == mk}
    if pop == "P":
        keys = [k for k in ka if k in kb and k in k0 and k0[k]["eligible"]]
    elif pop == "T":
        keys = [k for k in ka if k in kb and ka[k]["two_way"]]
    else:                                   # T2 (post-hoc): two-way rows of players with >= 2 prior usage games
        keys = [k for k in ka if k in kb and ka[k]["two_way"] and ka[k]["prior_usage_games"] >= 2]
    if not keys:
        return {"n": 0}
    games = np.array([k[0] for k in keys], dtype=object)
    res = {"n": len(keys), "games": int(len(set(games)))}
    # logloss only on P: for two-way rows A0 has no history, so its reference line differs from A1's (not comparable)
    for m in (("crps", "ae", "logloss_ref") if pop == "P" else ("crps", "ae")):
        A = {m: np.array([ka[k][m] for k in keys])}
        B = {m: np.array([kb[k][m] for k in keys])}
        res[m] = {"a": float(A[m].mean()), "b": float(B[m].mean()), **evaluate.paired(A, B, games, metric=m)}
    return res


def main(argv):
    db = argv[0]
    out_path = argv[1] if len(argv) > 1 else "two_way_wf.json"
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cfg = json.loads((forward.ART_DIR / "model_config_nfl.json").read_text(encoding="utf-8"))
    rows, t0 = [], time.time()
    ws = weeks(con)
    for w in ws:
        for arm in ARMS:
            rows += run_week(con, cfg, w, arm)
        print(f"{w['season']} {w['season_type']} W{w['week']}: {len(rows)} rows, {time.time() - t0:.0f}s", flush=True)
    os.environ.pop("SCOUT_NFL_TWO_WAY", None)
    summary = {"weeks": [f"{w['season']}-{w['season_type']}-{w['week']}" for w in ws], "comparisons": {}}
    for mk in featurize.NFL_MARKETS:
        for a, b in (("A1", "A0"), ("A2", "A1"), ("A2", "A0")):
            for pop in ("P", "T", "T2"):
                summary["comparisons"][f"{mk}|{a}-{b}|{pop}"] = compare(rows, a, b, mk, pop)
    summary["two_way_rows"] = [r for r in rows if r["two_way"] and r["market"] in ("rec", "rec_yds")]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1, default=float)
    print("wrote", out_path)


if __name__ == "__main__":
    main(sys.argv[1:])
