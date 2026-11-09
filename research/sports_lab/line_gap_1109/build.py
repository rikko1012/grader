"""lg-1 dataset (definitions frozen in RULES.md). Read-only inputs, local files only:

  python -m research.sports_lab.line_gap_1109.build <export.json> <stream_dir_or_files,...> <nba_full.db> <out.pkl>

export.json  = /api/sports/admin/model/gradecore-export?league=nba (window E)
stream files = paged /api/sports/admin/model/gradecore-comparison?league=nba dumps ({"rows": [...]}, comma separated)
nba_full.db  = local ESPN box-score copy (games, player_games)
"""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np
import pandas as pd

SPLIT = "2026-11-04T12:00:00Z"
STATS = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
KEY = ["game_id", "player_id", "stat", "book", "line"]


def _ts(s):
    return pd.to_datetime(s, utc=True)


def window_e(path):
    d = json.load(open(path))
    r = pd.DataFrame(d["rows"])
    r = r[(r["scope"] == "full_game") & r["market"].isin(STATS) & r["book"].isin(["prizepicks", "underdog"])]
    r = r[r["start_at"] < SPLIT]
    out = pd.DataFrame({"game_id": r["match_key"], "player_id": r["player_id"], "stat": r["market"],
                        "book": r["book"], "line": r["line"].astype(float), "forecast_at": r["forecast_at"],
                        "start_at": r["start_at"], "forecast_id": r["forecast_id"]})
    po = np.where(r["side"] == "over", r["p"], 1 - r["p"])
    out["p_over"] = po
    st, side = r["status"].values, r["side"].values
    out["result"] = np.select([st == "push", (st == "win") == (side == "over")], ["push", "over"], "under")
    out["q_mkt"] = np.nan
    out["be_over"] = np.nan
    out["window"] = "E"
    return out.reset_index(drop=True), {"export_rows": len(d["rows"]), "export_built_at": d.get("built_at")}


def window_s(files, db):
    rows = {}
    for f in files:
        for x in json.load(open(f))["rows"]:
            rows[x["cursor"]] = x
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    games = {g: s for g, s, st in con.execute("SELECT game_id, start_utc, status FROM games") if st == "final"}
    recs = []
    for x in rows.values():
        st = games.get(x["event"])
        if st is None or st < SPLIT or x["stat"] not in STATS or x.get("period", "full_game") != "full_game":
            continue
        if x["book"] not in ("prizepicks", "underdog"):
            continue
        if (_ts(st) - _ts(x["at"])).total_seconds() < 1800:
            continue
        so, su = (x.get("sides") or {}).get("over") or {}, (x.get("sides") or {}).get("under") or {}
        ro, ru = so.get("raw"), su.get("raw")
        if ro is None or ru is None or not (ro + ru) > 0:
            continue
        m = x.get("market") or {}
        recs.append({"game_id": x["event"], "player_id": x["player"], "stat": x["stat"], "book": x["book"],
                     "line": float(x["line"]), "forecast_at": x["at"], "start_at": st, "forecast_id": x["forecast_id"],
                     "p_over": ro / (ro + ru),
                     "q_mkt": m.get("p_over") if m.get("source") == "mc-1 consensus" else np.nan,
                     "be_over": (so.get("grade") or {}).get("break_even")})
    s = pd.DataFrame(recs).sort_values(["forecast_at", "forecast_id"])
    n_stream = len(s)
    # Amendment 1: opening = B's FIRST stream line; move time = first stream time the row's line appeared on B
    first_line = s.groupby(["game_id", "player_id", "stat", "book"])["line"].first().rename("L_open_s")
    first_seen = s.groupby(KEY)["forecast_at"].first().rename("line_first_at")
    s = s.drop_duplicates(KEY, keep="last").reset_index(drop=True)
    s = s.join(first_line, on=["game_id", "player_id", "stat", "book"]).join(first_seen, on=KEY)
    box = {}
    for g, p, played, *v in con.execute("SELECT game_id, player_id, played, pts, reb, ast, fg3m FROM player_games "
                                         "WHERE game_id IN (%s)" % ",".join("?" * s["game_id"].nunique()),
                                         list(s["game_id"].unique())):
        box[(g, p)] = (played, dict(zip(["pts", "reb", "ast", "fg3m"], v)))
    res = []
    for r in s.itertuples(index=False):
        b = box.get((r.game_id, r.player_id))
        if b is None or not b[0]:
            res.append("void")
            continue
        y = float(b[1][STATS[r.stat]])
        res.append("push" if y == r.line else ("over" if y > r.line else "under"))
    s["result"] = res
    s["window"] = "S"
    return s, {"stream_rows_unique_cursor": len(rows), "stream_rows_in_scope": n_stream}


def overlap_check(export_path, s):
    """Games present in both sources (Nov 4-5): agreement of the export's settled result with the box-score result."""
    d = pd.DataFrame(json.load(open(export_path))["rows"])
    d = d[d["start_at"] >= SPLIT]
    d = d.rename(columns={"match_key": "game_id", "market": "stat"})
    d["res_e"] = np.select([d["status"] == "push", (d["status"] == "win") == (d["side"] == "over")],
                           ["push", "over"], "under")
    m = d.merge(s[KEY + ["result", "forecast_at"]], on=KEY, suffixes=("_e", "_s"))
    m = m[m["result"] != "void"]
    return {"overlap_keys": int(len(m)), "result_agree": float((m["res_e"] == m["result"]).mean()) if len(m) else None,
            "forecast_at_equal": float((m["forecast_at_e"] == m["forecast_at_s"]).mean()) if len(m) else None}


def features(df):
    df = df.copy()
    df["t"] = _ts(df["forecast_at"])
    df["T"] = _ts(df["start_at"])
    df["side"] = np.where(df["p_over"] >= 0.5, "over", "under")
    df["p"] = np.where(df["side"] == "over", df["p_over"], 1 - df["p_over"])
    for c in ("L_O", "L_open", "bump_hrs", "gap"):
        df[c] = np.nan
    df["same_run"] = False
    g = df.groupby(["game_id", "player_id", "stat"])
    lo, lop, bh, sr = {}, {}, {}, {}
    for _, grp in g:
        by = {b: x.sort_values("t") for b, x in grp.groupby("book")}
        for b, x in by.items():
            first = x.iloc[0]
            o = by.get("underdog" if b == "prizepicks" else "prizepicks")
            for idx, r in x.iterrows():
                if r["window"] == "S":                    # Amendment 1
                    lop[idx] = r["L_open_s"]
                    if r["line"] != r["L_open_s"]:
                        bh[idx] = (r["T"] - _ts(r["line_first_at"])).total_seconds() / 3600
                else:
                    lop[idx] = first["line"]
                if o is None:
                    continue
                if r["window"] == "S":
                    if (o["t"] <= r["t"]).any():
                        after = o[o["t"] >= r["t"]]
                        if len(after):
                            lo[idx] = after.iloc[0]["line"]
                            sr[idx] = bool(after.iloc[0]["t"] == r["t"])
                else:
                    before = o[o["t"] <= r["t"]]
                    if len(before):
                        lo[idx] = before.iloc[-1]["line"]
                        sr[idx] = bool(before.iloc[-1]["t"] == r["t"])
    df["L_O"] = pd.Series(lo)
    df["L_open"] = pd.Series(lop)
    df["bump_hrs"] = pd.Series(bh)
    df["same_run"] = pd.Series(sr).reindex(df.index).fillna(False).astype(bool)
    df["gap"] = df["line"] - df["L_O"]
    df["gap_pct"] = df["gap"].abs() / ((df["line"] + df["L_O"]) / 2)
    df["D1"] = df["gap"].abs() >= 0.5
    df["D2"] = df["gap"].abs() >= np.maximum(1.0, 0.10 * df["L_O"])
    df["fav"] = np.where(df["gap"] < 0, "over", np.where(df["gap"] > 0, "under", None))
    df["agree"] = df["D1"] & (df["side"] == df["fav"])
    df["disagree"] = df["D1"] & (df["side"] != df["fav"])
    df["bump"] = df["line"] - df["L_open"]
    df["toward"] = ((df["side"] == "over") & (df["bump"] > 0)) | ((df["side"] == "under") & (df["bump"] < 0))
    df["away"] = (df["bump"] != 0) & ~df["toward"]
    df["big_bump"] = df["bump"].abs() >= np.maximum(1.0, 0.10 * df["L_open"])
    q = df["q_mkt"]
    df["price_side"] = np.where(q >= 0.52, "over", np.where(q <= 0.48, "under", None))
    df["price_disagree"] = df["price_side"].notna() & (df["price_side"] != df["side"])
    df["day"] = (df["T"] - pd.Timedelta(hours=6)).dt.strftime("%Y-%m-%d")
    return df.drop(columns=["t", "T"])


def build(export_path, stream_files, db):
    e, ie = window_e(export_path)
    s, is_ = window_s(stream_files, db)
    ov = overlap_check(export_path, s)
    df = pd.concat([e, s], ignore_index=True)
    dup = int(df.duplicated(KEY).sum())
    df = df.drop_duplicates(KEY, keep="last").reset_index(drop=True)
    df = features(df)
    info = {**ie, **is_, **ov, "duplicate_keys_dropped": dup, "rows": int(len(df)),
            "rows_decided": int(df["result"].isin(["over", "under"]).sum()),
            "push": int((df["result"] == "push").sum()), "void": int((df["result"] == "void").sum()),
            "games": int(df["game_id"].nunique()), "props": int(df.groupby(["game_id", "player_id", "stat"]).ngroups),
            "first_start": df["start_at"].min(), "last_start": df["start_at"].max(),
            "by_window": df.groupby("window").agg(rows=("line", "size"), games=("game_id", "nunique")).to_dict("index"),
            "by_book": df["book"].value_counts().to_dict(),
            "with_other_app_line": int(df["L_O"].notna().sum()), "with_D1": int(df["D1"].sum()),
            "with_D2": int(df["D2"].sum()), "same_run_pairs_rows": int(df["same_run"].sum()),
            "with_bump": int((df["bump"] != 0).sum()), "with_big_bump": int(df["big_bump"].sum()),
            "with_price": int(df["q_mkt"].notna().sum()), "with_price_side": int(df["price_side"].notna().sum())}
    return df, info


if __name__ == "__main__":
    df, info = build(sys.argv[1], sys.argv[2].split(","), sys.argv[3])
    df.to_pickle(sys.argv[4])
    print(json.dumps(info, indent=1, default=str))
