"""Factor round 2 (fr-2): point-in-time columns for the factors that were derivable but not built in fr-1
(registered in sports/research/REGISTER.md amendment A5 before any of them was scored). numpy only, worker only.

augment(df, sport, con, hl, as_of=None, absences=None) adds columns to a featurize.frame() Frame IN PLACE. It never
changes an existing column, so sm-1 (which reads only fr-1 columns) is unchanged.

Point-in-time rules (same as featurize.py):
- own-player history: the player's earlier rows of the frame (already cut to rows known at the forecast time);
- team / opponent history: the team's FINAL games whose known_at (start + 4 h NBA / 6 h NFL) <= the row's forecast time
  = min(row start - FORECAST_LEAD_H, as_of) - computed by searchsorted per team, so an upcoming row sees exactly the
  games public before its forecast, never a game played between the forecast and the start;
- same-game facts (starter tonight, teammates out tonight) are used only by factors whose registry availability says
  "known at the lineup / inactive report"; for an upcoming row they come from the forward report or are unknown (NaN).
"""
from __future__ import annotations

import math

import numpy as np

from . import datasets
from .featurize import FORECAST_LEAD_H, epoch
from .frame import ew_prior, gshift, group_starts, key_of, lag_matrix, lookup, take

VERSION = "fr-2"

# home venue of each team (city-level public facts, lat / lon) - travel = great-circle km from the previous game's venue
VENUE = {
    "nba": {"ATL": (33.757, -84.396), "BKN": (40.683, -73.976), "BOS": (42.366, -71.062), "CHA": (35.225, -80.839),
            "CHI": (41.881, -87.674), "CLE": (41.496, -81.688), "DAL": (32.790, -96.810), "DEN": (39.749, -105.008),
            "DET": (42.341, -83.055), "GS": (37.768, -122.388), "HOU": (29.751, -95.362), "IND": (39.764, -86.156),
            "LAC": (33.945, -118.343), "LAL": (34.043, -118.267), "MEM": (35.138, -90.051), "MIA": (25.781, -80.188),
            "MIL": (43.045, -87.917), "MIN": (44.980, -93.276), "NO": (29.949, -90.082), "NY": (40.751, -73.993),
            "OKC": (35.463, -97.515), "ORL": (28.539, -81.384), "PHI": (39.901, -75.172), "PHX": (33.446, -112.071),
            "POR": (45.532, -122.667), "SAC": (38.580, -121.500), "SA": (29.427, -98.438), "TOR": (43.643, -79.379),
            "UTAH": (40.768, -111.901), "WSH": (38.898, -77.021)},
    "nfl": {"ARI": (33.528, -112.263), "ATL": (33.755, -84.401), "BAL": (39.278, -76.623), "BUF": (42.774, -78.787),
            "CAR": (35.226, -80.853), "CHI": (41.862, -87.617), "CIN": (39.095, -84.516), "CLE": (41.506, -81.700),
            "DAL": (32.748, -97.093), "DEN": (39.744, -105.020), "DET": (42.340, -83.046), "GB": (44.501, -88.062),
            "HOU": (29.685, -95.411), "IND": (39.760, -86.164), "JAX": (30.324, -81.637), "KC": (39.049, -94.484),
            "LA": (33.953, -118.339), "LAC": (33.953, -118.339), "LV": (36.091, -115.184), "MIA": (25.958, -80.239),
            "MIN": (44.974, -93.258), "NE": (42.091, -71.264), "NO": (29.951, -90.081), "NYG": (40.814, -74.074),
            "NYJ": (40.814, -74.074), "PHI": (39.901, -75.168), "PIT": (40.447, -80.016), "SEA": (47.595, -122.332),
            "SF": (37.403, -121.970), "TB": (27.976, -82.503), "TEN": (36.166, -86.771), "WAS": (38.908, -76.864)},
}


def _km(a, b):
    if a is None or b is None:
        return np.nan
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


def _cut_ts(df, league, as_of):
    """Per row: latest game start whose result is public at the row's forecast time."""
    fa = df["ts"] - FORECAST_LEAD_H * 3600
    if as_of is not None:
        fa = np.minimum(fa, epoch([as_of])[0])
    return fa - datasets.KNOWN_AFTER_H[league] * 3600


def team_prior(df_team, df_cut, tg_team, tg_ts, tg_vals, n, min_periods=1, how="mean"):
    """For each row: mean (sum) of the last <= n values of its team's final games that started at or before
    df_cut (strictly public at forecast time). tg_* must be sorted by (team, ts)."""
    out = np.full(len(df_team), np.nan)
    vals = np.asarray(tg_vals, float)
    order = {}
    for i, t in enumerate(tg_team):
        order.setdefault(t, []).append(i)
    idx = {t: (np.asarray(ix), tg_ts[np.asarray(ix)]) for t, ix in order.items()}
    for t in np.unique(df_team):
        if t not in idx:
            continue
        ix, ts = idx[t]
        rows = np.flatnonzero(df_team == t)
        k = np.searchsorted(ts, df_cut[rows], side="right")       # games with start <= cut
        v = vals[ix]
        ok = np.isfinite(v)
        cs = np.concatenate([[0.0], np.cumsum(np.where(ok, v, 0.0))])
        cc = np.concatenate([[0], np.cumsum(ok)])
        lo = np.maximum(0, k - n)
        s, c = cs[k] - cs[lo], cc[k] - cc[lo]
        out[rows] = np.where(c >= min_periods, s / np.maximum(c, 1) if how == "mean" else s, np.nan)
    return out


def _last_before(df_team, df_cut, tg_team, tg_ts, tg_vals):
    """Value of the team's latest final game started at or before the cut (NaN if none)."""
    out = np.full(len(df_team), np.nan)
    vals = np.asarray(tg_vals, float)
    for t in np.unique(df_team):
        ix = np.flatnonzero(tg_team == t)
        if not len(ix):
            continue
        rows = np.flatnonzero(df_team == t)
        k = np.searchsorted(tg_ts[ix], df_cut[rows], side="right")
        ok = k > 0
        out[rows[ok]] = vals[ix[k[ok] - 1]]
    return out


def _schedule(con, league):
    """Every scheduled or final game (known in advance): -> sorted arrays per team-game (team, ts, home team, neutral)."""
    rows = con.execute("SELECT game_id, start_utc, home, away, neutral, status FROM games WHERE league = ? AND "
                       "start_utc IS NOT NULL", (league,)).fetchall()
    out = []
    for r in rows:
        for team in (r["home"], r["away"]):
            out.append((team, r["start_utc"], r["game_id"], r["home"], int(r["neutral"] or 0)))
    out.sort(key=lambda x: (x[0], x[1]))
    team = np.array([x[0] for x in out], dtype=object)
    ts = epoch([x[1] for x in out]) if out else np.zeros(0)
    gid = np.array([x[2] for x in out], dtype=object)
    host = np.array([x[3] for x in out], dtype=object)
    neutral = np.array([x[4] for x in out], float)
    return team, ts, gid, host, neutral


def _travel_rest(df, con, league):
    """Schedule facts known in advance: travel km from the previous game's venue, games in the previous 4 days (NBA),
    rest days. Neutral-site games have no known venue -> NaN."""
    team, ts, gid, host, neutral = _schedule(con, league)
    venue = VENUE[league]
    gs = group_starts(team)
    loc = [None if neutral[i] else venue.get(host[i]) for i in range(len(team))]
    km = np.full(len(team), np.nan)
    g4 = np.full(len(team), np.nan)
    for i in range(len(team)):
        if i > gs[i]:
            km[i] = _km(loc[i - 1], loc[i])
            j, c = i - 1, 0
            while j >= gs[i] and ts[i] - ts[j] <= 4 * 86400:
                c += 1
                j -= 1
            g4[i] = c
    key = key_of(gid, team)
    i = lookup(key_of(df["game_id"], df["team"]), key)
    return take(km, i), take(g4, i)


# ================================================================================================================ NBA
def augment_nba(df, con, hl, as_of=None, absences=None):
    gs = group_starts(df["player_id"])
    h = hl[1] if isinstance(hl, (tuple, list)) else hl
    g = lambda c: np.asarray(df[c], float)                                             # noqa: E731
    with np.errstate(divide="ignore", invalid="ignore"):
        df["x_ts_pct"] = g("S_pts") / (2 * (g("S_fga") + 0.44 * g("S_fta")))
        df["x_ft_rate"] = g("S_fta") / g("S_fga")
        df["x_three_share"] = g("S_fg3a") / g("S_fga")
        df["x_ast_to"] = g("S_ast") / np.maximum(g("S_tov"), 0.5)
    # same-game lineup fact: started TONIGHT (box flag; forward = lineup report or unknown)
    df["x_starter_today"] = np.where(g("is_up") == 1, np.nan, g("starter"))
    # team context from final team games, public at each row's forecast time
    t = con.execute("SELECT t.game_id, t.team, t.opp, t.pts, t.fga, t.oreb, t.tov, t.fta, g.start_utc FROM nba_tg t "
                    "JOIN games g ON g.league = 'nba' AND g.game_id = t.game_id WHERE g.status = 'final'").fetchall()
    t = sorted(t, key=lambda r: (r["team"], r["start_utc"]))
    team = np.array([r["team"] for r in t], dtype=object)
    ts = epoch([r["start_utc"] for r in t]) if t else np.zeros(0)
    poss = np.array([(r["fga"] or 0) - (r["oreb"] or 0) + (r["tov"] or 0) + 0.44 * (r["fta"] or 0) for r in t], float)
    pts = np.array([r["pts"] or np.nan for r in t], float)
    k = key_of(np.array([r["game_id"] for r in t], dtype=object), team)
    ok = lookup(key_of(np.array([r["game_id"] for r in t], dtype=object),
                       np.array([r["opp"] for r in t], dtype=object)), k)
    opp_pts, opp_poss = take(pts, ok), take(poss, ok)
    cut = _cut_ts(df, "nba", as_of)
    with np.errstate(divide="ignore", invalid="ignore"):
        ortg_num = team_prior(df["team"], cut, team, ts, pts, 10, 3, "sum")
        ortg_den = team_prior(df["team"], cut, team, ts, poss, 10, 3, "sum")
        df["x_team_ortg"] = 100 * ortg_num / ortg_den
        drtg_num = team_prior(df["opp"], cut, team, ts, opp_pts, 10, 3, "sum")       # points the OPPONENT allowed
        drtg_den = team_prior(df["opp"], cut, team, ts, opp_poss, 10, 3, "sum")
        df["x_opp_drtg"] = 100 * drtg_num / drtg_den
    # opponent 3PA allowed per minute to the player's position group (last 15 opponent games, public at forecast)
    played = df[g("is_up") == 0]
    ak = key_of(played["game_id"], played["opp"], played["pos_g"])
    u, inv = np.unique(ak, return_inverse=True)
    s3 = np.bincount(inv, weights=np.nan_to_num(played["fg3a"]), minlength=len(u))
    sm = np.bincount(inv, weights=np.nan_to_num(played["min"]), minlength=len(u))
    parts = [x.split("|") for x in u]
    gts = {r["game_id"]: e for r, e in zip(t, ts)}
    tp = np.array([p[1] + "|" + p[2] for p in parts], dtype=object)
    ats = np.array([gts.get(p[0], np.nan) for p in parts], float)
    o = np.lexsort((ats, tp))
    tp, ats, s3, sm = tp[o], ats[o], s3[o], sm[o]
    dtp = key_of(df["opp"], df["pos_g"])
    with np.errstate(divide="ignore", invalid="ignore"):
        df["x_opp_3pa_allow"] = (team_prior(dtp, cut, tp, ats, s3, 15, 5, "sum") /
                                 team_prior(dtp, cut, tp, ats, sm, 15, 5, "sum"))
    km, g4 = _travel_rest(df, con, "nba")
    df["x_travel_km"], df["x_games_in_4"] = km, g4
    # teammates out TONIGHT weighted by their usage (same-game fact, known at the injury report / inactive list)
    use = g("fga") + 0.44 * g("fta") + g("tov")
    ew_use, _ = ew_prior(use, gs, h)
    df["x_absent_usage"] = _absent_usage(df, ew_use, absences or {})
    # market game lines (ESPN pickcenter, near closing; forward: games-store pickcenter snapshot before forecast)
    with np.errstate(invalid="ignore"):
        df["x_implied_team_total"] = g("total_line") / 2 - g("team_spread") / 2
    return df


def _absent_usage(df, ew_use, absences):
    """Share of the rotation's EW usage (rotation = played one of the team's last 3 games with prior EW min >= 20)
    held by rotation players who are not playing this game. Upcoming game: the report's Out list, else unknown."""
    order = np.lexsort((df["ts"], df["team"]))
    out = np.full(len(df), np.nan)
    by_game = {}
    for i in order:
        by_game.setdefault((df["team"][i], df["game_id"][i]), []).append(i)
    games = sorted(by_game, key=lambda k: (k[0], df["ts"][by_game[k][0]]))
    cur, k, last, ew, ewm = None, 0, {}, {}, {}
    for team, gid in games:
        if team != cur:
            cur, k, last, ew, ewm = team, 0, {}, {}, {}
        rows = by_game[(team, gid)]
        for i in rows:                                    # prior (pre-game) EW values of the players in this game
            p = df["player_id"][i]
            if ew_use[i] == ew_use[i]:
                ew[p] = ew_use[i]
            if df["ew_min"][i] == df["ew_min"][i]:
                ewm[p] = df["ew_min"][i]
        rot = [p for p, lk in last.items() if k - lk <= 3 and ewm.get(p, 0) >= 20]
        up = df["is_up"][rows[0]] == 1
        here = {df["player_id"][i] for i in rows}
        den = sum(ew.get(p, 0) for p in set(rot) | here)
        if up:
            outs = absences.get((gid, team))
            val = np.nan if outs is None else (sum(ew.get(p, 0) for p in rot if p in set(outs)) / den if den else 0.0)
        else:
            val = sum(ew.get(p, 0) for p in rot if p not in here) / den if den else 0.0
        for i in rows:
            out[i] = val
        if not up:
            for p in here:
                last[p] = k
        k += 1
    return out


# ================================================================================================================ NFL
def augment_nfl(df, con, hl, as_of=None, absences=None):
    gs = group_starts(df["player_id"])
    h = hl[0] if isinstance(hl, (tuple, list)) else hl
    g = lambda c: np.asarray(df[c], float)                                             # noqa: E731
    up = g("is_up") == 1
    have_x = con.execute("SELECT name FROM sqlite_master WHERE name = 'nfl_tgx'").fetchone() is not None
    # player-level EW from the frame's own prior rows
    for c in ("i10_targets", "i10_carries", "pass_air_yds", "pass_epa", "rush_epa", "rec_epa"):
        v = np.where(up, np.nan, np.nan_to_num(g(c)) if c.startswith("i10") else g(c))
        m, _ = ew_prior(v, gs, h)
        df[f"x_ew_{c}"] = m
    with np.errstate(divide="ignore", invalid="ignore"):
        df["x_i10_looks"] = df["x_ew_i10_targets"] + df["x_ew_i10_carries"]
        # per-opportunity rates only where the EW opportunity is >= 1 a game (a tiny denominator is noise, not a rate)
        rate = lambda a, b: np.where(g(b) >= 1, g(a) / g(b), np.nan)                 # noqa: E731
        df["x_qb_adot"] = rate("x_ew_pass_air_yds", "ew_pass_att")
        df["x_pass_epa_att"] = rate("x_ew_pass_epa", "ew_pass_att")
        df["x_rush_epa_carry"] = rate("x_ew_rush_epa", "ew_carries")
        df["x_rec_epa_target"] = rate("x_ew_rec_epa", "ew_targets")
        df["x_route_proxy"] = g("ew_offense_pct") * g("npass_l6")
        l3_ts = np.nanmean(lag_matrix(np.where(up, np.nan, g("target_share")), gs, 3), axis=1)
        df["x_tshare_trend"] = l3_ts - g("ew_target_share")
    # team totals per game (from the frame's played rows) -> carry share, red-zone share
    cut = _cut_ts(df, "nfl", as_of)
    t = con.execute("SELECT t.game_id, t.team, t.carries, g.start_utc FROM nfl_tg t JOIN games g ON g.league = 'nfl' "
                    "AND g.game_id = t.game_id WHERE g.status = 'final'").fetchall()
    tx = {}
    if have_x:
        tx = {(r["game_id"], r["team"]): dict(r) for r in con.execute("SELECT * FROM nfl_tgx")}
    t = sorted(t, key=lambda r: (r["team"], r["start_utc"]))
    team = np.array([r["team"] for r in t], dtype=object)
    ts = epoch([r["start_utc"] for r in t]) if t else np.zeros(0)
    gidt = np.array([r["game_id"] for r in t], dtype=object)
    car = np.array([r["carries"] if r["carries"] is not None else np.nan for r in t], float)

    def xcol(name):
        return np.array([(tx.get((a, b)) or {}).get(name) if tx.get((a, b)) is not None else np.nan
                         for a, b in zip(gidt, team)], float) if have_x else np.full(len(t), np.nan)
    rz = xcol("rz_looks")
    with np.errstate(divide="ignore", invalid="ignore"):
        car_share = np.where(up, np.nan, g("carries")) / take(car, lookup(key_of(df["game_id"], df["team"]),
                                                                          key_of(gidt, team)))
        m, _ = ew_prior(car_share, gs, h)
        df["x_carry_share"] = m
        df["x_carry_share_trend"] = np.nanmean(lag_matrix(car_share, gs, 3), axis=1) - m
        df["x_rz_share"] = (g("ew_rz_targets") + g("ew_rz_carries")) / team_prior(df["team"], cut, team, ts, rz, 6, 2)
        epa_s, epa_n = xcol("epa_sum"), xcol("epa_n")
        df["x_team_epa_play"] = (team_prior(df["team"], cut, team, ts, epa_s, 6, 2, "sum") /
                                 team_prior(df["team"], cut, team, ts, epa_n, 6, 2, "sum"))
        df["x_team_explosive"] = (team_prior(df["team"], cut, team, ts, xcol("explosive"), 6, 2, "sum") /
                                  team_prior(df["team"], cut, team, ts, epa_n, 6, 2, "sum"))
        df["x_neutral_sec_play"] = (team_prior(df["team"], cut, team, ts, xcol("neutral_secs"), 6, 2, "sum") /
                                    team_prior(df["team"], cut, team, ts, xcol("neutral_gaps"), 6, 2, "sum"))
        # opponent DEFENCE: the offences it faced (rows of the teams that played it)
        ok = lookup(key_of(gidt, np.array([_opp(con_cache(con), a, b) for a, b in zip(gidt, team)], dtype=object)),
                    key_of(gidt, team))
        f_s, f_n, f_x, f_succ = take(epa_s, ok), take(epa_n, ok), take(xcol("explosive"), ok), take(xcol("success"), ok)
        df["x_opp_def_epa"] = (team_prior(df["opp"], cut, team, ts, f_s, 6, 2, "sum") /
                               team_prior(df["opp"], cut, team, ts, f_n, 6, 2, "sum"))
        df["x_opp_def_success"] = (team_prior(df["opp"], cut, team, ts, f_succ, 6, 2, "sum") /
                                   team_prior(df["opp"], cut, team, ts, f_n, 6, 2, "sum"))
        df["x_opp_def_explosive"] = (team_prior(df["opp"], cut, team, ts, f_x, 6, 2, "sum") /
                                     team_prior(df["opp"], cut, team, ts, f_n, 6, 2, "sum"))
    # QB scrambles / designed runs (player extras)
    if have_x:
        px = {(r["game_id"], r["player_id"]): (r["dropbacks"], r["scrambles"])
              for r in con.execute("SELECT * FROM nfl_pgx")}
    else:
        px = {}
    is_qb = np.asarray(df["position"]) == "QB"
    db_ = np.array([px.get((a, b), (0, 0))[0] if q and not u_ else np.nan
                    for a, b, q, u_ in zip(df["game_id"], df["player_id"], is_qb, up)], float)
    sc_ = np.array([px.get((a, b), (0, 0))[1] if q and not u_ else np.nan
                    for a, b, q, u_ in zip(df["game_id"], df["player_id"], is_qb, up)], float)
    if not have_x:
        db_[:], sc_[:] = np.nan, np.nan
    m_db, _ = ew_prior(db_, gs, h)
    m_sc, _ = ew_prior(sc_, gs, h)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["x_scramble_rate"] = m_sc / m_db
        df["x_designed_runs"] = np.where(is_qb, g("ew_carries") - m_sc, np.nan)
    km, _ = _travel_rest(df, con, "nfl")
    df["x_travel_km"] = km
    rd = g("rest_days")
    df["x_rest_log"] = np.log(np.clip(rd, 3, 21))
    df["x_post_bye"] = np.where(np.isfinite(rd), (rd >= 10).astype(float), np.nan)
    tso, cso = _teammates_out(df, absences or {})
    df["x_mates_out_tshare"], df["x_mates_out_cshare"] = tso, cso
    return df


_OPP = {}


def con_cache(con):
    key = id(con)
    if key not in _OPP:
        _OPP.clear()
        _OPP[key] = {(r["game_id"], r["home"]): r["away"] for r in con.execute(
            "SELECT game_id, home, away FROM games WHERE league = 'nfl'")}
        _OPP[key].update({(r["game_id"], r["away"]): r["home"] for r in con.execute(
            "SELECT game_id, home, away FROM games WHERE league = 'nfl'")})
    return _OPP[key]


def _opp(m, gid, team):
    return m.get((gid, team), "")


def _teammates_out(df, absences):
    """EW target share / carry share of rotation teammates (played one of the team's last 3 games; EW target share >=
    0.08 or carry share >= 0.15) NOT playing this game. NFL inactives are announced ~90 min before kickoff, before the
    registered forecast time (start - 60 min); forward = the report's Out / inactive list or unknown (NaN)."""
    order = np.lexsort((df["ts"], df["team"]))
    by_game = {}
    for i in order:
        by_game.setdefault((df["team"][i], df["game_id"][i]), []).append(i)
    games = sorted(by_game, key=lambda k: (k[0], df["ts"][by_game[k][0]]))
    to, co = np.full(len(df), np.nan), np.full(len(df), np.nan)
    cur, k, last, ts_, cs_ = None, 0, {}, {}, {}
    for team, gid in games:
        if team != cur:
            cur, k, last, ts_, cs_ = team, 0, {}, {}, {}
        rows = by_game[(team, gid)]
        for i in rows:
            p = df["player_id"][i]
            v = df["ew_target_share"][i]
            if v == v:
                ts_[p] = v
            c = df["x_carry_share"][i]
            if c == c:
                cs_[p] = c
        rot = [p for p, lk in last.items() if k - lk <= 3 and (ts_.get(p, 0) >= 0.08 or cs_.get(p, 0) >= 0.15)]
        up = df["is_up"][rows[0]] == 1
        here = {df["player_id"][i] for i in rows}
        if up:
            outs = absences.get((gid, team))
            if outs is None:
                a = b = np.nan
            else:
                o = set(outs)
                a, b = sum(ts_.get(p, 0) for p in rot if p in o), sum(cs_.get(p, 0) for p in rot if p in o)
        else:
            a = sum(ts_.get(p, 0) for p in rot if p not in here)
            b = sum(cs_.get(p, 0) for p in rot if p not in here)
        for i in rows:
            to[i], co[i] = a, b
        if not up:
            for p in here:
                last[p] = k
        k += 1
    return to, co


def augment(df, sport, con, hl, as_of=None, absences=None):
    (augment_nba if sport == "nba" else augment_nfl)(df, con, hl, as_of, absences)
    for c in [c for c in df.cols if c.startswith("x_")]:          # a ratio over an empty window is unknown, not inf
        v = np.asarray(df[c], float)
        df[c] = np.where(np.isfinite(v), v, np.nan)
    return df
