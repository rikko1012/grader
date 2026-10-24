"""Point-in-time feature frames from ds-1 (pandas, worker only).

Every feature of a row uses ONLY rows of games that started before the row's game and whose rule-based known_at is
<= the row's forecast time (start - FORECAST_LEAD_H). Own-player history uses groupby(...).shift(1) on games sorted by
start (a player never plays twice within KNOWN_AFTER_H + FORECAST_LEAD_H; asserted). Team / opponent history uses the
team's earlier games the same way. Same-game box-score facts (did a teammate play?) are used ONLY by features whose
registry availability says so (e.g. nba.teammate_absent_sameday = known at the injury report / inactive list).

frame(sport, con) -> DataFrame with one row per player-game (played rows only: the distribution is conditional on
playing; DNP counts are reported separately), the targets y_<market>, base-model inputs and feature columns f_*.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import datasets

FORECAST_LEAD_H = 1.0
NBA_MARKETS = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
NFL_MARKETS = {"pass_yds": "pass_yds", "rush_yds": "rush_yds", "rec_yds": "rec_yds", "rush_att": "carries",
               "rec": "rec"}
LAGS = 40


def _pos_nba(p):
    p = (p or "").upper()
    return "C" if p.startswith("C") else ("F" if p.startswith(("F", "SF", "PF")) else "G")


def _ts(s):
    return pd.to_datetime(s, utc=True)


def _ew_prior(df, col, hl, by="player_id"):
    """EW mean and EW weight-sum of a column over each player's PRIOR rows (current row excluded)."""
    g = df.groupby(by, sort=False)[col]
    m = g.transform(lambda s: s.ewm(halflife=hl, adjust=True).mean().shift(1))
    n = df.groupby(by, sort=False).cumcount()                       # prior rows
    lam = 0.5 ** (1.0 / hl)
    wsum = (1 - lam ** n) / (1 - lam)
    return m, wsum


def _lags(df, col, k=LAGS, by="player_id"):
    g = df.groupby(by, sort=False)[col]
    return np.column_stack([g.shift(j + 1).to_numpy(dtype=float) for j in range(k)])


def _assert_pit(df, league):
    """No player row may have a previous row whose result was not public at this row's forecast time."""
    prev = df.groupby("player_id", sort=False)["start"].shift(1)
    lag = (df["start"] - prev).dt.total_seconds() / 3600
    bad = (lag < datasets.KNOWN_AFTER_H[league] + FORECAST_LEAD_H).sum()
    if bad:
        raise AssertionError(f"{bad} rows whose previous game was not known at forecast time")


# ================================================================================================================ NBA
def nba_frame(con, h_min=5, h_rate=20):
    pg = pd.read_sql_query("SELECT p.*, g.start_utc, g.season, g.season_type, g.spread_home, g.total_line "
                           "FROM nba_pg p JOIN games g ON g.league='nba' AND g.game_id = p.game_id "
                           "WHERE g.status = 'final'", con)
    pg["start"] = _ts(pg["start_utc"])
    pg = pg.sort_values(["player_id", "start"], kind="mergesort").reset_index(drop=True)
    pg["pos_g"] = pg["position"].map(_pos_nba)
    # ---- team-level context (all box rows, played or not) -------------------------------------------------------
    tg = pd.read_sql_query("SELECT t.*, g.start_utc FROM nba_tg t JOIN games g ON g.league='nba' AND "
                           "g.game_id = t.game_id WHERE g.status='final'", con)
    tg["start"] = _ts(tg["start_utc"])
    tg = tg.sort_values(["team", "start"], kind="mergesort").reset_index(drop=True)
    tg["poss"] = tg["fga"] - tg["oreb"] + tg["tov"] + 0.44 * tg["fta"]
    tg["min48"] = (tg["player_min"] / 5).clip(lower=48)
    tg["pace"] = tg["poss"] * 48 / tg["min48"]
    gt = tg.groupby("team", sort=False)
    tg["pace_l10"] = gt["pace"].transform(lambda s: s.rolling(10, min_periods=3).mean().shift(1))
    tg["prev_start"] = gt["start"].shift(1)
    tg["rest_days"] = (tg["start"] - tg["prev_start"]).dt.total_seconds() / 86400
    # opponent allowance per position group: stat and minutes of the OPPOSING players, by defending team
    played = pg[pg["played"] == 1]
    agg = played.groupby(["game_id", "opp", "pos_g"])[["min", "pts", "reb", "ast", "fg3m"]].sum().reset_index()
    agg = agg.rename(columns={"opp": "team"}).merge(tg[["game_id", "team", "start"]], on=["game_id", "team"])
    agg = agg.sort_values(["team", "pos_g", "start"], kind="mergesort")
    ga = agg.groupby(["team", "pos_g"], sort=False)
    for c in ("min", "pts", "reb", "ast", "fg3m"):
        agg[f"allow_{c}"] = ga[c].transform(lambda s: s.rolling(15, min_periods=5).sum().shift(1))
    # teammate absence: rotation players (prior EW minutes >= 20, played within the team's last 3 games) who did
    # not play this game. Built per team in game order from the box rows (DNP rows and players missing from the box).
    pg["ew_min_all"], _ = _ew_prior(pg.assign(m=pg["min"].where(pg["played"] == 1)), "m", 10)
    absent = _team_absence(pg, tg)
    tg = tg.merge(absent, on=["game_id", "team"], how="left")
    tg["absent_share"] = tg["absent_share"].fillna(0.0)
    tg["absent_share_lag"] = tg.groupby("team", sort=False)["absent_share"].shift(1)
    # ---- player history (played rows only) ----------------------------------------------------------------------
    df = played.copy().sort_values(["player_id", "start"], kind="mergesort").reset_index(drop=True)
    _assert_pit(df, "nba")
    df["prior_n"] = df.groupby("player_id", sort=False).cumcount()
    df["ew_min"], df["w_min_n"] = _ew_prior(df, "min", h_min)
    for c in ("min", "pts", "reb", "ast", "fg3m", "fg3a", "fga", "fta", "tov"):
        m, w = _ew_prior(df, c, h_rate)
        df[f"S_{c}"] = m * w                                         # EW sum of prior values (half-life h_rate)
    df["usage_raw"] = df["fga"] + 0.44 * df["fta"] + df["tov"]
    m, w = _ew_prior(df, "usage_raw", h_rate)
    df["S_usage"] = m * w
    lag_min = _lags(df, "min")
    df["l10_min"] = np.nanmean(np.where(np.isnan(lag_min[:, :10]), np.nan, lag_min[:, :10]), axis=1)
    df["l5_min"] = np.nanmean(lag_min[:, :5], axis=1)
    fga_l = _lags(df, "fga", 5)
    df["l5_fga_pm"] = np.nansum(fga_l, axis=1) / np.maximum(np.nansum(lag_min[:, :5], axis=1), 1)
    df["starter_lag"] = df.groupby("player_id", sort=False)["starter"].shift(1)
    st_l = _lags(df, "starter", 10)
    df["starter_rate10"] = np.nanmean(st_l, axis=1)
    lag_store = {}
    for mk, c in NBA_MARKETS.items():
        lag_store[mk] = _lags(df, c)
        df[f"y_{mk}"] = df[c]
    # ---- join context -------------------------------------------------------------------------------------------
    df = df.merge(tg[["game_id", "team", "pace_l10", "rest_days", "absent_share", "absent_share_lag"]],
                  on=["game_id", "team"], how="left")
    opp = tg[["game_id", "team", "pace_l10"]].rename(columns={"team": "opp", "pace_l10": "opp_pace_l10"})
    df = df.merge(opp, on=["game_id", "opp"], how="left")
    al = agg[["game_id", "team", "pos_g"] + [f"allow_{c}" for c in ("min", "pts", "reb", "ast", "fg3m")]]
    df = df.merge(al.rename(columns={"team": "opp"}), on=["game_id", "opp", "pos_g"], how="left")
    df["spread_abs"] = df["spread_home"].abs()
    df["team_spread"] = np.where(df["home"] == 1, df["spread_home"], -df["spread_home"])
    df["forecast_at"] = df["start"] - pd.Timedelta(hours=FORECAST_LEAD_H)
    df["dnp_share_diag"] = np.nan
    df.attrs["lags"] = lag_store
    df.attrs["lag_min"] = lag_min
    df.attrs["dnp"] = _nba_dnp_rate(pg)
    return df


def _team_absence(pg, tg):
    rows = []
    order = tg[["game_id", "team", "start"]].sort_values(["team", "start"], kind="mergesort")
    by_game = {k: v for k, v in pg.groupby(["game_id", "team"], sort=False)}
    for team, games in order.groupby("team", sort=False):
        last_played = {}          # player -> index of the team game he last played
        ew = {}                   # player -> prior EW minutes (as of his last row)
        for i, gid in enumerate(games["game_id"]):
            box = by_game.get((gid, team))
            in_box_played = set()
            if box is not None:
                for pid, played, emin, reason in zip(box["player_id"], box["played"], box["ew_min_all"],
                                                     box["dnp_reason"]):
                    if not np.isnan(emin):
                        ew[pid] = emin
                    if played == 1:
                        in_box_played.add(pid)
            rot = [p for p, j in last_played.items() if i - j <= 3 and ew.get(p, 0) >= 20]
            miss = [p for p in rot if p not in in_box_played]
            rows.append((gid, team, sum(ew[p] for p in miss) / 240.0, len(miss)))
            for p in in_box_played:
                last_played[p] = i
    return pd.DataFrame(rows, columns=["game_id", "team", "absent_share", "absent_n"])


def _nba_dnp_rate(pg):
    """Diagnostic: share of rows of eligible-looking players (prior EW minutes >= 15) that did not play."""
    x = pg[pg["ew_min_all"] >= 15]
    return {"rows": int(len(x)), "dnp": int((x["played"] == 0).sum()),
            "share": float((x["played"] == 0).mean()) if len(x) else None}


# ================================================================================================================ NFL
NFL_POS = ("QB", "RB", "WR", "TE")


def nfl_frame(con, h_opp=4):
    pg = pd.read_sql_query("SELECT p.*, g.start_utc, g.spread_home, g.total_line, g.roof, g.temp, g.wind "
                           "FROM nfl_pg p JOIN games g ON g.league='nfl' AND g.game_id = p.game_id "
                           "WHERE g.status = 'final'", con)
    pg = pg[pg["position"].isin(NFL_POS + ("FB",))].copy()
    pg["position"] = pg["position"].replace({"FB": "RB"})
    pg["start"] = _ts(pg["start_utc"])
    pg = pg.sort_values(["player_id", "start"], kind="mergesort").reset_index(drop=True)
    for c in ("pass_att", "pass_cmp", "pass_yds", "carries", "rush_yds", "targets", "rec", "rec_yds", "rz_targets",
              "rz_carries", "i10_targets", "i10_carries", "rec_air_yds"):
        pg[c] = pg[c].fillna(0.0)
    tg = pd.read_sql_query("SELECT t.*, g.start_utc FROM nfl_tg t JOIN games g ON g.league='nfl' AND "
                           "g.game_id = t.game_id WHERE g.status='final'", con)
    tg["start"] = _ts(tg["start_utc"])
    tg = tg.sort_values(["team", "start"], kind="mergesort").reset_index(drop=True)
    gt = tg.groupby("team", sort=False)
    tg["plays_l6"] = gt["plays"].transform(lambda s: s.rolling(6, min_periods=2).mean().shift(1))
    tg["npass_l6"] = gt["neutral_pass"].transform(lambda s: s.rolling(6, min_periods=2).sum().shift(1)) / \
        gt["neutral_plays"].transform(lambda s: s.rolling(6, min_periods=2).sum().shift(1))
    tg["prev_start"] = gt["start"].shift(1)
    tg["rest_days"] = (tg["start"] - tg["prev_start"]).dt.total_seconds() / 86400
    # the team's main passer per game; QB change = this game's main passer differs from the previous game's -
    # same-game fact, so only the LAGGED version (QB changed last game) is point-in-time for any forecast time
    qb = pg[pg["position"] == "QB"].sort_values("pass_att", ascending=False).drop_duplicates(["game_id", "team"])
    tg = tg.merge(qb[["game_id", "team", "player_id"]].rename(columns={"player_id": "qb1"}), on=["game_id", "team"],
                  how="left")
    tg["qb_change"] = (tg["qb1"] != tg.groupby("team", sort=False)["qb1"].shift(1)).astype(float)
    tg.loc[tg.groupby("team", sort=False).cumcount() == 0, "qb_change"] = np.nan
    tg["qb_change_lag"] = tg.groupby("team", sort=False)["qb_change"].shift(1)
    # opponent allowance per position: yards / receptions / carries by players of that position, defending team
    agg = pg.groupby(["game_id", "opp", "position"])[["targets", "rec", "rec_yds", "carries", "rush_yds", "pass_att",
                                                       "pass_yds"]].sum().reset_index()
    agg = agg.rename(columns={"opp": "team"}).merge(tg[["game_id", "team", "start"]], on=["game_id", "team"])
    agg = agg.sort_values(["team", "position", "start"], kind="mergesort")
    ga = agg.groupby(["team", "position"], sort=False)
    for c in ("targets", "rec", "rec_yds", "carries", "rush_yds", "pass_att", "pass_yds"):
        agg[f"allow_{c}"] = ga[c].transform(lambda s: s.rolling(8, min_periods=3).mean().shift(1))
    df = pg.copy()
    _assert_pit(df, "nfl")
    df["prior_n"] = df.groupby("player_id", sort=False).cumcount()
    for c in ("targets", "carries", "pass_att", "rec", "rec_yds", "rush_yds", "pass_yds", "pass_cmp", "offense_pct",
              "target_share", "air_yards_share", "rz_targets", "rz_carries", "rec_air_yds"):
        src = df[c] if c not in ("offense_pct", "target_share", "air_yards_share") else df[c]
        m, w = _ew_prior(df.assign(_v=src), "_v", h_opp)
        df[f"ew_{c}"] = m
        df[f"S_{c}"] = m * w
        df[f"W_{c}"] = w
    lag_store = {}
    for mk, c in NFL_MARKETS.items():
        lag_store[mk] = _lags(df, c, 20)
        df[f"y_{mk}"] = df[c]
    l3 = _lags(df, "offense_pct", 3)
    df["l3_snap"] = np.nanmean(l3, axis=1)
    df["l6_targets"] = np.nanmean(_lags(df, "targets", 6), axis=1)
    df["l6_carries"] = np.nanmean(_lags(df, "carries", 6), axis=1)
    df["l6_pass_att"] = np.nanmean(_lags(df, "pass_att", 6), axis=1)
    df = df.merge(tg[["game_id", "team", "plays_l6", "npass_l6", "rest_days", "qb_change_lag"]],
                  on=["game_id", "team"], how="left")
    al = agg[["game_id", "team", "position"] + [c for c in agg.columns if c.startswith("allow_")]]
    df = df.merge(al.rename(columns={"team": "opp"}), on=["game_id", "opp", "position"], how="left")
    opp_t = tg[["game_id", "team", "plays_l6"]].rename(columns={"team": "opp", "plays_l6": "opp_plays_l6"})
    df = df.merge(opp_t, on=["game_id", "opp"], how="left")
    df["team_spread"] = np.where(df["home"] == 1, df["spread_home"], -df["spread_home"])
    df["implied_total"] = df["total_line"] / 2 - df["team_spread"] / 2
    df["dome"] = df["roof"].isin(["dome", "closed"]).astype(float)
    df["forecast_at"] = df["start"] - pd.Timedelta(hours=FORECAST_LEAD_H)
    df.attrs["lags"] = lag_store
    return df


def frame(sport, con, **kw):
    return nba_frame(con, **kw) if sport == "nba" else nfl_frame(con, **kw)
