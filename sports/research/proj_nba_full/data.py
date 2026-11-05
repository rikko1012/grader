"""Full-history NBA feature table for the pnf-1 projection rebuild (RESEARCH ONLY - nothing in the live board, worker or
grader imports this module).

Source: the consolidated read-only working copy nba_full.db (ESPN box scores 2023-10-24 .. 2026-11-05, schedule-derived
rest, pre-game spread/total, DNP reasons, settled props). Path via --db or $NBA_FULL_DB.

POINT-IN-TIME RULE: every feature of a player-game row is computed from rows of games that started BEFORE that game
(per-player series are shifted by one played game before any rolling / EW statistic; team series by one team game). The
only same-game inputs are facts known before tip: home/away, neutral site, schedule rest, the pre-game spread/total and
the teammates-out list restricted to absences announced before tip (see teammates_out below). The box-score values of
the game itself appear only as targets (y_*).

Teammates out (the injury-report proxy): ESPN does not serve historical injury reports, so the absent list is derived
from the box score, restricted to reasons that are announced pre-game - injury/illness, rest, suspension, personal
(nba_full.db absences.kind != 'coach_or_unlisted') - plus rotation players missing from the box entirely (long absence,
trade). Coach's-decision DNPs are NOT counted as known (they are not announced before tip). The live sm-1 forward path
uses the ESPN injury report's Out list for the same quantity.
"""
from __future__ import annotations

import os
import sqlite3

import numpy as np
import pandas as pd

STATS = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
RATE_COLS = ("pts", "reb", "ast", "fg3m", "fg3a", "fga", "fta", "oreb", "dreb", "tov", "usage")
KNOWN_KINDS = ("injury_illness", "rest", "suspension", "personal")
ROT_MIN = 15.0          # rotation player: EW minutes (hl 10 played games) >= ROT_MIN ...
ROT_RECENT = 5          # ... and played in one of the team's last ROT_RECENT games


def default_db():
    return os.environ.get("NBA_FULL_DB", r"C:\Users\tikic\cs2_scout_ops\data\nba_full\nba_full.db")


def _ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _pos(p):
    p = (p or "").upper()
    return "C" if p.startswith("C") else ("F" if p.startswith(("F", "SF", "PF")) else "G")


def _ts(s):
    """epoch seconds (independent of the pandas datetime resolution)."""
    return (pd.to_datetime(s, utc=True) - pd.Timestamp("1970-01-01", tz="UTC")) / pd.Timedelta(seconds=1)


def _ew_prior(g, col, hl):
    """EW mean of the PREVIOUS values within each group (shift 1, then ewm; NaN ignored)."""
    return g[col].transform(lambda s: s.shift(1).ewm(halflife=hl, ignore_na=True).mean())


def _ew_incl(g, col, hl):
    """EW mean INCLUDING the current value (used only to carry a player's state to LATER games)."""
    return g[col].transform(lambda s: s.ewm(halflife=hl, ignore_na=True).mean())


def load(db=None):
    con = _ro(db or default_db())
    pg = pd.read_sql("SELECT p.game_id, p.player_id, p.name, p.team, p.opp, p.home, p.starter, p.played, p.dnp_reason, "
                     "p.position, p.min, p.pts, p.fga, p.fg3m, p.fg3a, p.fta, p.oreb, p.dreb, p.reb, p.ast, p.tov, "
                     "p.pm, g.start_utc, g.season, g.season_type, g.spread_home, g.total_line, g.neutral "
                     "FROM player_games p JOIN games g ON g.game_id = p.game_id WHERE g.status = 'final'", con)
    tg = pd.read_sql("SELECT t.*, g.spread_home, g.total_line, g.home AS home_team FROM team_games t "
                     "JOIN games g ON g.game_id = t.game_id WHERE g.status='final'", con)
    sch = pd.read_sql("SELECT game_id, team, days_rest, b2b, games_last_7d FROM team_schedule", con)
    ab = pd.read_sql("SELECT game_id, team, player_id, kind FROM absences", con)
    props = pd.read_sql("SELECT * FROM settled_props", con)
    con.close()
    return pg, tg, sch, ab, props


# ----------------------------------------------------------------------------------------------------------- team
def team_features(tg, sch):
    t = tg.copy()
    t["ts"] = _ts(t["start_utc"])
    opp = t[["game_id", "team", "pts", "poss_est"]].rename(columns={"team": "opp", "pts": "opp_pts",
                                                                     "poss_est": "opp_poss"})
    t = t.merge(opp, on=["game_id", "opp"], how="left")
    poss = (t["poss_est"] + t["opp_poss"]) / 2
    t["pace"] = poss * 48 / np.maximum(t["player_min"] / 5, 48)
    t["ortg"] = 100 * t["pts"] / poss
    t["drtg"] = 100 * t["opp_pts"] / poss
    t["margin"] = t["pts"] - t["opp_pts"]
    t = t.sort_values(["team", "ts"]).reset_index(drop=True)
    g = t.groupby("team", sort=False)
    out = t[["game_id", "team", "opp", "ts", "spread_home", "total_line", "home"]].copy()
    for c in ("pace", "ortg", "drtg", "margin"):
        out[f"t_{c}_ew10"] = _ew_prior(g, c, 10)
        out[f"t_{c}_ew40"] = _ew_prior(g, c, 40)
    out["t_gidx"] = g.cumcount()
    out = out.merge(sch, on=["game_id", "team"], how="left")
    o = out[["game_id", "team"] + [c for c in out.columns if c.startswith("t_") and c != "t_gidx"]
            + ["days_rest", "b2b", "games_last_7d"]]
    o = o.rename(columns={c: "o_" + c[2:] if c.startswith("t_") else "o_" + c for c in o.columns
                          if c not in ("game_id", "team")}).rename(columns={"team": "opp"})
    out = out.merge(o, on=["game_id", "opp"], how="left")
    out["team_spread"] = np.where(out["home"] == 1, out["spread_home"], -out["spread_home"])
    # strength-gap proxy (prior margins) where the market line is missing
    out["margin_gap_ew40"] = out["t_margin_ew40"] - out["o_margin_ew40"]
    return out


def opp_position_allowance(pl):
    """Per (defending team, position group): EW (hl 15 games) prior per-minute rates the team allowed."""
    keys = ["game_id", "opp", "pos_g"]
    a = pl.groupby(keys, sort=False)[["min", "pts", "reb", "ast", "fg3m", "fg3a"]].sum().reset_index()
    ts = pl.groupby("game_id")["ts"].first()
    a["ts"] = a["game_id"].map(ts)
    a = a.sort_values(["opp", "pos_g", "ts"]).reset_index(drop=True)
    g = a.groupby(["opp", "pos_g"], sort=False)
    out = a[keys].copy()
    em = _ew_prior(g, "min", 15)
    for c in ("pts", "reb", "ast", "fg3m", "fg3a"):
        out[f"allow_{c}_pm"] = _ew_prior(g, c, 15) / em
    return out


# ------------------------------------------------------------------------------------------------- teammates out
def teammates_out(pg, ab, played_state):
    """Per (game, team): rotation teammates out with a pre-game-known reason (or missing from the box), summed over
    their state as of their last played game: minutes, usage, pts, reb, ast, fg3m per game, count, starters out.
    played_state: per played row the player's EW-including-this-game state (min, usage, stat per game, starter rate)."""
    known = set(zip(ab.loc[ab["kind"].isin(KNOWN_KINDS), "game_id"], ab.loc[ab["kind"].isin(KNOWN_KINDS), "player_id"]))
    games = pg[["game_id", "team", "ts", "season"]].drop_duplicates(["game_id", "team"]).sort_values(["team", "ts"])
    rows_by = {k: v for k, v in pg.groupby(["game_id", "team"], sort=False).groups.items()}
    st_cols = ["s_min", "s_usage", "s_pts", "s_reb", "s_ast", "s_fg3m", "s_start"]
    S = played_state[st_cols].to_numpy()
    pid = pg["player_id"].to_numpy()
    played = pg["played"].to_numpy()
    out = []
    cur, k, last, state = None, 0, {}, {}
    for gid, team, season in zip(games["game_id"].to_numpy(), games["team"].to_numpy(), games["season"].to_numpy()):
        if (team, season) != cur:                 # new team or new season: last season's roster is not "out"
            cur, k, last, state = (team, season), 0, {}, {}
        idx = rows_by.get((gid, team), [])
        present = {pid[i]: i for i in idx}
        rot = [p for p, kk in last.items() if k - kk <= ROT_RECENT and state[p][0] >= ROT_MIN]
        tot = np.zeros(len(st_cols))
        n_out = n_st = n_miss = 0
        for p in rot:
            i = present.get(p)
            if i is None or (played[i] == 0 and (gid, p) in known):
                tot += state[p]
                n_out += 1
                n_st += state[p][6] >= 0.5
                n_miss += i is None
        out.append((gid, team, *tot[:6], n_out, n_st, n_miss))
        for i in idx:
            if played[i] == 1:
                last[pid[i]] = k
                state[pid[i]] = S[i]
        k += 1
    cols = ["game_id", "team", "out_min", "out_usage", "out_pts", "out_reb", "out_ast", "out_fg3m", "out_n",
            "out_starters", "out_missing_box"]
    return pd.DataFrame(out, columns=cols)


# ------------------------------------------------------------------------------------------------------ player
def build(db=None, data=None):
    """-> (frame, props). frame: one row per PLAYED player-game with pre-game features + targets; props: settled props."""
    pg, tg, sch, ab, props = data if data is not None else load(db)
    pg = pg.copy()
    pg["ts"] = _ts(pg["start_utc"])
    pg["pos_g"] = [_pos(p) for p in pg["position"]]
    pg = pg.sort_values(["player_id", "ts"]).reset_index(drop=True)
    pg["usage"] = pg["fga"] + 0.44 * pg["fta"] + pg["tov"]
    pl = pg[pg["played"] == 1].copy()
    g = pl.groupby("player_id", sort=False)
    # state carried to later games (INCLUDES the game: used only for teammates' absences in later games)
    st = pd.DataFrame(index=pl.index)
    st["s_min"] = _ew_incl(g, "min", 10)
    for c in ("usage", "pts", "reb", "ast", "fg3m"):
        st[f"s_{c}"] = _ew_incl(g, c, 10)
    st["s_start"] = _ew_incl(g, "starter", 5)
    full_state = pd.DataFrame(np.nan, index=pg.index, columns=st.columns)
    full_state.loc[st.index] = st
    tout = teammates_out(pg, ab, full_state)

    f = pl[["game_id", "player_id", "name", "team", "opp", "home", "pos_g", "ts", "start_utc", "season", "season_type",
            "neutral", "min", "pts", "reb", "ast", "fg3m", "starter"]].copy()
    f["prior_n"] = g.cumcount()
    for hl in (3, 10, 30):
        f[f"min_ew{hl}"] = _ew_prior(g, "min", hl)
    f["min_last"] = g["min"].shift(1)
    f["min_l3"] = g["min"].transform(lambda s: s.shift(1).rolling(3, 1).mean())
    f["min_sd10"] = g["min"].transform(lambda s: s.shift(1).rolling(10, 3).std())
    f["starter_lag"] = g["starter"].shift(1)
    f["starter_r10"] = g["starter"].transform(lambda s: s.shift(1).rolling(10, 1).mean())
    for hl in (10, 40):
        em = _ew_prior(g, "min", hl)
        for c in RATE_COLS:
            f[f"r_{c}_ew{hl}"] = _ew_prior(g, c, hl) / em
    # season-to-date (same season, prior games)
    gs = pl.groupby(["player_id", "season"], sort=False)
    f["season_n"] = gs.cumcount()
    cs_min = gs["min"].cumsum() - pl["min"]
    f["season_min_avg"] = cs_min / f["season_n"].replace(0, np.nan)
    for c in ("pts", "reb", "ast", "fg3m"):
        f[f"season_r_{c}"] = (gs[c].cumsum() - pl[c]) / cs_min.replace(0, np.nan)
    f["days_since_played"] = (f["ts"] - g["ts"].shift(1)) / 86400
    f["team_changed"] = (g["team"].shift(1) != f["team"]).astype(float).where(f["prior_n"] > 0)
    # shares of the team (prior): player's EW usage per game vs his team's, via the teammates' state at last game
    f["usage_pg_ew10"] = _ew_prior(g, "usage", 10)
    for c in ("pts", "reb", "ast", "fg3m"):
        f[f"{c}_pg_ew10"] = _ew_prior(g, c, 10)
    # games the team played since this player's last played game (missed games: DNP or not in the box)
    tf = team_features(tg, sch)
    f = f.merge(tf.drop(columns=["opp", "ts", "home"]), on=["game_id", "team"], how="left")
    f["gidx_prev"] = f.groupby("player_id", sort=False)["t_gidx"].shift(1)
    f["missed_since_last"] = np.where(f["team_changed"] == 0, f["t_gidx"] - f["gidx_prev"] - 1, np.nan)
    # opponent allowance by position
    pl2 = pl[["game_id", "opp", "pos_g", "ts", "min", "pts", "reb", "ast", "fg3m", "fg3a"]]
    oa = opposition = opp_position_allowance(pl2)
    f = f.merge(oa, on=["game_id", "opp", "pos_g"], how="left")
    del opposition
    # teammates out (this player's own absence is impossible: he played)
    f = f.merge(tout, on=["game_id", "team"], how="left")
    o2 = tout.rename(columns={c: "opp_" + c for c in tout.columns if c.startswith("out_")}).rename(columns={"team": "opp"})
    f = f.merge(o2[["game_id", "opp", "opp_out_min", "opp_out_usage", "opp_out_n"]], on=["game_id", "opp"], how="left")
    f["usage_share_out"] = f["out_usage"] / (f["usage_pg_ew10"] + 1e-6)
    f["postseason"] = f["season_type"].isin(["postseason", "play_in"]).astype(float)
    f["abs_spread"] = f["team_spread"].abs()
    f["month"] = pd.to_datetime(f["start_utc"]).dt.month
    f = f.sort_values(["ts", "game_id", "player_id"]).reset_index(drop=True)
    return f, props


FEATURES = (
    ["home", "neutral", "postseason", "prior_n", "season_n", "min_ew3", "min_ew10", "min_ew30", "min_last", "min_l3",
     "min_sd10", "starter_lag", "starter_r10", "season_min_avg", "days_since_played", "team_changed",
     "missed_since_last", "usage_pg_ew10", "pts_pg_ew10", "reb_pg_ew10", "ast_pg_ew10", "fg3m_pg_ew10"]
    + [f"r_{c}_ew{hl}" for hl in (10, 40) for c in RATE_COLS]
    + [f"season_r_{c}" for c in ("pts", "reb", "ast", "fg3m")]
    + [f"t_{c}_ew{hl}" for c in ("pace", "ortg", "drtg", "margin") for hl in (10, 40)]
    + [f"o_{c}_ew{hl}" for c in ("pace", "ortg", "drtg", "margin") for hl in (10, 40)]
    + ["days_rest", "b2b", "games_last_7d", "o_days_rest", "o_b2b", "o_games_last_7d", "team_spread", "abs_spread",
       "total_line", "margin_gap_ew40"]
    + [f"allow_{c}_pm" for c in ("pts", "reb", "ast", "fg3m", "fg3a")]
    + ["out_min", "out_usage", "out_pts", "out_reb", "out_ast", "out_fg3m", "out_n", "out_starters", "out_missing_box", "opp_out_min",
       "opp_out_usage", "opp_out_n", "usage_share_out"]
    + ["pos_c", "pos_f"]
)


def add_pos(f):
    f["pos_c"] = (f["pos_g"] == "C").astype(float)
    f["pos_f"] = (f["pos_g"] == "F").astype(float)
    return f


if __name__ == "__main__":       # python -m sports.research.proj_nba_full.data <nba_full.db> <frame.pkl>
    import sys
    fr, _ = build(sys.argv[1])
    fr.to_pickle(sys.argv[2])
    print("frame", len(fr), "->", sys.argv[2])
