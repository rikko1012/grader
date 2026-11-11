"""Build the study table for the CS2 grade-upgrade study (2026-11-11). Read-only, LOCAL files only.
Joins: line-gap app rows (Oct 11 - Nov 8, de-duplicated) + forecast -> pick context saved at forecast time
+ P1 unit projection + pre-match HLTV features (pd-1 feature table, extract ends 2026-11-05).
No outcome is scored here; outputs coverage counts only.  -> $GU_DIR/table.pkl, build_counts.json"""
import gzip, json, os, sys
import numpy as np, pandas as pd

OPS = "C:/Users/tikic/cs2_scout_ops/data/"
OUT = os.environ.get("GU_DIR", OPS + "grade_up_1111/")
LG = OPS + "line_gap_1109/"
CF = OPS + "cs2_full/"


def main():
    rows = pd.DataFrame(json.load(open(LG + "rows.json")))
    fc = {}
    with gzip.open(LG + "forecasts_compact.jsonl.gz", "rt") as f:
        for l in f:
            d = json.loads(l)
            fc[d["id"]] = d["pick_id"]
    picks = {}
    keep = ["player", "player_id", "team", "opponent", "lambda", "p_win", "market", "veto_known", "sample_maps",
            "rec_avg", "rec_median", "rec_hit", "flags", "opp_def", "line_type", "on_board", "test"]
    with gzip.open(LG + "picks.jsonl.gz", "rt") as f:
        for l in f:
            d = json.loads(l)
            picks[d["id"]] = {k: d.get(k) for k in keep}
    rows["pick_id"] = rows["fid"].map(fc)
    ctx = pd.DataFrame([picks.get(i, {}) for i in rows["pick_id"]]).add_prefix("pk_")
    rows = pd.concat([rows.reset_index(drop=True), ctx], axis=1)

    p1 = json.load(gzip.open(CF + "admin/p1_units.json.gz", "rt"))["rows"]
    p1d = {u["forecast_id"]: u for u in p1}
    rows["p1_lam"] = rows["fid"].map(lambda i: (p1d.get(i) or {}).get("lam"))
    rows["revision"] = rows["fid"].map(lambda i: (p1d.get(i) or {}).get("revision"))
    rows["lam"] = rows["p1_lam"].where(rows["p1_lam"].notna(), rows["pk_lambda"])
    rows["lam_src"] = np.where(rows["p1_lam"].notna(), "p1", np.where(rows["pk_lambda"].notna(), "pick", "none"))

    # HLTV ids: forecast -> HLTV player (Nov 5 working table), spread to every row of the same pick player id
    wp = pd.read_csv(CF + "work/p1_units_with_player.csv.gz", usecols=["forecast_id", "playerid", "matchid", "join"])
    wp = wp[wp["playerid"].notna()]
    f2p = dict(zip(wp["forecast_id"], wp["playerid"].astype(int)))
    rows["hltv_pid"] = rows["fid"].map(f2p)
    m = rows[rows["hltv_pid"].notna()].groupby("pk_player_id")["hltv_pid"].agg(lambda s: s.value_counts().index[0])
    amb = rows[rows["hltv_pid"].notna()].groupby("pk_player_id")["hltv_pid"].nunique()
    m = m[amb[m.index] == 1]          # exact, unambiguous id pairs only (no fuzzy matching)
    rows["hltv_pid"] = rows["hltv_pid"].where(rows["hltv_pid"].notna(), rows["pk_player_id"].map(m))
    rows["matchid"] = rows["match"].map(lambda s: int(s.split(":")[1]) if isinstance(s, str) and s.startswith("hltv:") else np.nan)

    sf = pd.read_pickle(CF + "proj_full/series_features.pkl")
    fcols = ["matchid", "playerid", "day", "lan", "elo_pre", "elo_n_pre", "opp_elo_pre", "elo_diff", "elo_mean",
             "rest_days_pre", "lineup_changes_vs_prev", "players_new_60d", "opp_rest_days_pre", "opp_players_new_60d",
             "kpr_20", "maps_20", "kpr_90", "r_90", "maps_90", "kpr_365", "r_365", "maps_365", "days_since",
             "maps_with_team_60", "o_conc_kpr", "o_maps", "t_rshare", "o_rshare", "t_ot", "o_ot", "kshare_90",
             "hsk_90", "okpr_90", "ev_qual", "ev_academy", "ev_major", "t_rpm", "o_rpm", "rating_90"]
    sfx = sf[fcols].rename(columns={c: "f_" + c for c in fcols if c not in ("matchid", "playerid")})
    rows = rows.merge(sfx, how="left", left_on=["matchid", "hltv_pid"], right_on=["matchid", "playerid"])
    # player roles: CURRENT snapshot (proxy; applied to history)
    try:
        pr = pd.read_csv(CF + "hltv/player_roles.csv.gz")
        rows.attrs["role_cols"] = list(pr.columns)
        pr.to_pickle(OUT + "roles_raw.pkl")
    except Exception as e:  # noqa
        print("roles", e)
    rows.to_pickle(OUT + "table.pkl")
    c = {"rows": len(rows), "units": int(rows["unit"].nunique()), "matches": int(rows["match"].nunique()),
         "with_pick_ctx": int(rows["pk_player"].notna().sum()), "with_p_win": int(rows["pk_p_win"].notna().sum()),
         "with_sample_maps": int(rows["pk_sample_maps"].notna().sum()), "lam_src": rows["lam_src"].value_counts().to_dict(),
         "with_hltv_pid": int(rows["hltv_pid"].notna().sum()), "with_matchid": int(rows["matchid"].notna().sum()),
         "with_features": int(rows["f_day"].notna().sum()), "staked_live": int((rows["units"] > 0).sum()),
         "first_start": rows["start"].min(), "last_start": rows["start"].max()}
    json.dump(c, open(OUT + "build_counts.json", "w"), indent=1)
    print(json.dumps(c, indent=1))


if __name__ == "__main__":
    main()
