"""Point-in-time feature frames from ds-1 (numpy only, worker only).

Every feature of a row uses ONLY rows of games that started before the row's game and whose rule-based known_at is
<= the row's forecast time (start - FORECAST_LEAD_H). Own-player history = the player's earlier rows (frames sorted by
player, start; asserted that every earlier game was public before the forecast time). Team / opponent history = the
team's earlier games the same way. Same-game box-score facts (did a teammate play?) are used ONLY by features whose
registry availability says so (nba.teammate_absent_sameday = known at the injury report / inactive list).

frame(sport, con) -> Frame with one row per PLAYED player-game (the distribution is conditional on playing; DNP counts
are reported separately), targets y_<market>, base-model inputs and context columns.
"""
from __future__ import annotations

import os

import numpy as np

from . import datasets
from .frame import Frame, concat, ew_prior, group_starts, gshift, key_of, lag_matrix, lookup, roll_prior, rows_frame, take

FORECAST_LEAD_H = 1.0
NBA_MARKETS = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
NFL_MARKETS = {"pass_yds": "pass_yds", "rush_yds": "rush_yds", "rec_yds": "rec_yds", "rush_att": "carries",
               "rec": "rec"}
LAGS = 40


def _pos_nba(p):
    p = (p or "").upper()
    return "C" if p.startswith("C") else ("F" if p.startswith(("F", "SF", "PF")) else "G")


def epoch(start_utc):
    return np.array([s.replace("Z", "") for s in start_utc], dtype="datetime64[s]").astype(np.int64).astype(float)


def iso_week(ts):
    d = ts.astype("int64").astype("datetime64[s]").astype("datetime64[D]")
    return ["%d-W%02d" % tuple(x.item().isocalendar()[:2]) for x in d]


def _assert_pit(f, gs, league):
    prev = gshift(f["ts"], gs, 1)
    lag_h = (f["ts"] - prev) / 3600
    bad = np.sum(lag_h < datasets.KNOWN_AFTER_H[league] + FORECAST_LEAD_H)
    if bad:
        raise AssertionError(f"{bad} rows whose previous game was not known at forecast time")


def _team_frame(con, table, league):
    t = Frame.from_sql(con, f"SELECT t.*, g.start_utc FROM {table} t JOIN games g ON g.league=? AND "
                            f"g.game_id = t.game_id WHERE g.status='final'", (league,))
    t["ts"] = epoch(t["start_utc"])
    return t.sort("team", "ts")


def fix_prior_games_on():
    """SPORTS_FIX_PRIOR_GAMES=1 (bug sweep 2026-11-06 #9, default OFF - it changes live inputs, owner decides): an
    upcoming (unplayed) game is never a prior game of the same player's / team's LATER upcoming game. Off: unchanged."""
    return os.environ.get("SPORTS_FIX_PRIOR_GAMES", "").strip().lower() in ("1", "true", "yes", "on")


def split_upcoming(upcoming):
    """Upcoming rows -> layers in which no team and no player has more than one upcoming game; games in start order,
    each in the first layer that has room (one layer when nothing repeats)."""
    games = {}
    for r in upcoming:
        games.setdefault(r["game_id"], []).append(r)
    layers = []                                     # [(teams, players, rows)]
    for gid in sorted(games, key=lambda g: (min(str(r["start_utc"]) for r in games[g]), str(g))):
        rows = games[gid]
        teams = {t for r in rows for t in (r["team"], r["opp"])}
        players = {r["player_id"] for r in rows}
        for lt, lp, lr in layers:
            if not (teams & lt) and not (players & lp):
                lt |= teams
                lp |= players
                lr.extend(rows)
                break
        else:
            layers.append((set(teams), set(players), list(rows)))
    return [lr for _, _, lr in layers]


def _layered(build, upcoming, with_lags):
    """build(upcoming_layer) per layer; the first layer's whole frame + every later layer's upcoming rows, sorted by
    (player, time) as the frame contract requires. None when no layering is needed (flag off / one layer / lags)."""
    if not upcoming or with_lags or not fix_prior_games_on():
        return None
    layers = split_upcoming(upcoming)
    if len(layers) < 2:
        return None
    out = build(layers[0])
    for lay in layers[1:]:
        d = build(lay)
        out = concat(out, d[np.flatnonzero(d["is_up"] == 1)])
        del d
    out = out.sort("player_id", "ts")
    out.attrs["prior_games_fix"] = len(layers)
    return out


# ================================================================================================================ NBA
def _with_upcoming(pg, league, as_of, upcoming, team_like=None):
    """Point-in-time cut (rows known at as_of) + the upcoming rows to forecast (stats unknown = NaN, is_up = 1)."""
    pg["is_up"] = np.zeros(len(pg))
    if as_of is not None:
        cut = epoch([as_of])[0]
        pg = pg[pg["ts"] + datasets.KNOWN_AFTER_H[league] * 3600 <= cut]
    if not upcoming:
        return pg, set()
    rows = [dict(r, is_up=1.0, played=1.0) for r in upcoming]
    up = rows_frame(rows, pg)
    up["ts"] = epoch(up["start_utc"])
    return concat(pg, up), {(r["game_id"], r["team"]) for r in upcoming}


def _team_upcoming(tg, league, as_of, up_keys, upcoming):
    if as_of is not None:
        cut = epoch([as_of])[0]
        tg = tg[tg["ts"] + datasets.KNOWN_AFTER_H[league] * 3600 <= cut]
    if not upcoming:
        return tg
    seen, rows = set(), []
    for r in upcoming:
        for team, opp, home in ((r["team"], r["opp"], r["home"]), (r["opp"], r["team"], 1 - r["home"])):
            if (r["game_id"], team) not in seen:
                seen.add((r["game_id"], team))
                rows.append({"game_id": r["game_id"], "team": team, "opp": opp, "home": home,
                             "start_utc": r["start_utc"]})
    up = rows_frame(rows, tg)
    up["ts"] = epoch(up["start_utc"])
    return concat(tg, up).sort("team", "ts")


def nba_frame(con, h_min=5, h_rate=20, upcoming=None, as_of=None, absences=None, with_lags=True):
    """absences: {(game_id, team): [player_id, ...] | None} for UPCOMING games - the players the injury report lists
    as Out (forward source: ESPN injury report in the store). None / missing = no report -> unknown (NaN)."""
    lay = _layered(lambda u: nba_frame(con, h_min, h_rate, upcoming=u, as_of=as_of, absences=absences,
                                       with_lags=with_lags), upcoming, with_lags)
    if lay is not None:
        return lay
    pg = Frame.from_sql(con, "SELECT p.*, g.start_utc, g.season, g.season_type, g.spread_home, g.total_line "
                             "FROM nba_pg p JOIN games g ON g.league='nba' AND g.game_id = p.game_id "
                             "WHERE g.status = 'final'")
    pg["ts"] = epoch(pg["start_utc"])
    pg, up_keys = _with_upcoming(pg, "nba", as_of, upcoming)
    pg = pg.sort("player_id", "ts")
    pg["pos_g"] = np.array([_pos_nba(p) for p in pg["position"]], dtype=object)
    gs_all = group_starts(pg["player_id"])
    pg["ew_min_all"], _ = ew_prior(np.where(pg["played"] == 1, pg["min"], np.nan), gs_all, 10)
    # ---- team context --------------------------------------------------------------------------------------------
    tg = _team_upcoming(_team_frame(con, "nba_tg", "nba"), "nba", as_of, up_keys, upcoming)
    tgs = group_starts(tg["team"])
    poss = tg["fga"] - tg["oreb"] + tg["tov"] + 0.44 * tg["fta"]
    min48 = np.maximum(tg["player_min"] / 5, 48)
    tg["pace"] = poss * 48 / min48
    tg["pace_l10"] = roll_prior(tg["pace"], tgs, 10, 3)
    tg["rest_days"] = (tg["ts"] - gshift(tg["ts"], tgs, 1)) / 86400
    absent = _team_absence(pg, tg, tgs, absences or {}, {k[0] for k in up_keys})
    tg["absent_share"] = absent
    tg["absent_share_lag"] = gshift(absent, tgs, 1)
    tkey = key_of(tg["game_id"], tg["team"])
    # opponent allowance per position group (stat and minutes of the OPPOSING players, by defending team)
    played = pg[pg["played"] == 1]
    akeys = key_of(played["game_id"], played["opp"], played["pos_g"])
    u, inv = np.unique(akeys, return_inverse=True)
    sums = {c: np.bincount(inv, weights=np.nan_to_num(played[c]), minlength=len(u))
            for c in ("min", "pts", "reb", "ast", "fg3m")}
    parts = [x.split("|") for x in u]
    ag = Frame({"game_id": np.array([p[0] for p in parts], dtype=object),
                "team": np.array([p[1] for p in parts], dtype=object),
                "pos_g": np.array([p[2] for p in parts], dtype=object), **sums})
    ti = lookup(key_of(ag["game_id"], ag["team"]), tkey)
    ag = ag[ti >= 0]
    ag["ts"] = tg["ts"][ti[ti >= 0]]
    ag["tp"] = key_of(ag["team"], ag["pos_g"])
    ag = ag.sort("tp", "ts")
    ags = group_starts(ag["tp"])
    for c in ("min", "pts", "reb", "ast", "fg3m"):
        ag[f"allow_{c}"] = roll_prior(ag[c], ags, 15, 5, how="sum")
    # ---- player history (played rows only) ---------------------------------------------------------------------
    df = played.sort("player_id", "ts")
    gs = group_starts(df["player_id"])
    _assert_pit(df, gs, "nba")
    df["prior_n"] = np.arange(len(df)) - gs
    df["ew_min"], df["w_min_n"] = ew_prior(df["min"], gs, h_min)
    for c in ("min", "pts", "reb", "ast", "fg3m", "fg3a", "fga", "fta", "tov"):
        m, w = ew_prior(df[c], gs, h_rate)
        df[f"S_{c}"] = m * w
    m, w = ew_prior(df["fga"] + 0.44 * df["fta"] + df["tov"], gs, h_rate)
    df["S_usage"] = m * w
    lag_min = lag_matrix(df["min"], gs, 10)
    with np.errstate(all="ignore"):
        df["l10_min"] = np.nanmean(lag_min, axis=1)
        df["l5_min"] = np.nanmean(lag_min[:, :5], axis=1)
        fga5 = lag_matrix(df["fga"], gs, 5)
        df["l5_fga_pm"] = np.nansum(fga5, axis=1) / np.maximum(np.nansum(lag_min[:, :5], axis=1), 1)
        df["starter_lag"] = gshift(df["starter"], gs, 1)
        df["starter_rate10"] = np.nanmean(lag_matrix(df["starter"], gs, 10), axis=1)
    lags = {}
    for mk, c in NBA_MARKETS.items():
        if with_lags:                                   # the B0 / B1 baselines need them (study); forward does not
            lags[mk] = lag_matrix(df[c], gs, LAGS).astype(np.float32)
        df[f"y_{mk}"] = df[c]
    # ---- join context -------------------------------------------------------------------------------------------
    ti = lookup(key_of(df["game_id"], df["team"]), tkey)
    for c in ("pace_l10", "rest_days", "absent_share", "absent_share_lag"):
        df[c] = take(tg[c], ti)
    oi = lookup(key_of(df["game_id"], df["opp"]), tkey)
    df["opp_pace_l10"] = take(tg["pace_l10"], oi)
    ai = lookup(key_of(df["game_id"], df["opp"], df["pos_g"]), key_of(ag["game_id"], ag["team"], ag["pos_g"]))
    for c in ("min", "pts", "reb", "ast", "fg3m"):
        df[f"allow_{c}"] = take(ag[f"allow_{c}"], ai)
    df["spread_abs"] = np.abs(df["spread_home"])
    df["team_spread"] = np.where(df["home"] == 1, df["spread_home"], -df["spread_home"])
    df.attrs["lags"] = lags
    df.attrs["dnp"] = _nba_dnp_rate(pg)
    return df


def _team_absence(pg, tg, tgs, absences=None, up_games=()):
    """Share (EW minutes / 240) of rotation players (prior EW min >= 20, played within the team's last 3 games) who
    did not play the team's game (did-not-play row or missing from the box). For an UPCOMING game the absentees are
    the injury report's Out players (absences); without a report the value is unknown (NaN), never "nobody"."""
    absences = absences or {}
    rows = {}
    for i in range(len(pg)):
        rows.setdefault((pg["game_id"][i], pg["team"][i]), []).append(i)
    out = np.zeros(len(tg))
    last_played, ew = {}, {}
    cur_team = None
    k = 0
    for j in range(len(tg)):
        team = tg["team"][j]
        if team != cur_team:
            cur_team, last_played, ew, k = team, {}, {}, 0
        played_now = set()
        for i in rows.get((tg["game_id"][j], team), []):
            pid = pg["player_id"][i]
            e = pg["ew_min_all"][i]
            if e == e:
                ew[pid] = e
            if pg["played"][i] == 1:
                played_now.add(pid)
        rot = [p for p, last in last_played.items() if k - last <= 3 and ew.get(p, 0) >= 20]
        gid = tg["game_id"][j]
        if gid in up_games:
            outs = absences.get((gid, team))
            out[j] = np.nan if outs is None else sum(ew[p] for p in rot if p in set(outs)) / 240.0
        else:
            out[j] = sum(ew[p] for p in rot if p not in played_now) / 240.0
        for p in played_now:
            last_played[p] = k
        k += 1
    return out


def _nba_dnp_rate(pg):
    m = pg["ew_min_all"] >= 15
    n = int(m.sum())
    d = int(((pg["played"] == 0) & m).sum())
    return {"rows": n, "dnp": d, "share": d / n if n else None,
            "definition": "rows of players with prior EW minutes >= 15 listed in the box who did not play"}


# ================================================================================================================ NFL
NFL_POS = ("QB", "RB", "WR", "TE")
NFL_FILL0 = ("pass_att", "pass_cmp", "pass_yds", "carries", "rush_yds", "targets", "rec", "rec_yds", "rz_targets",
             "rz_carries", "i10_targets", "i10_carries", "rec_air_yds")


# offensive line and specialists play offensive snaps without being two-way skill players: never pulled in (walk-forward
# 2026-11-05 amendment: the first version labelled every lineman with snaps "WR")
NFL_NOT_TWO_WAY = ("OT", "T", "G", "OG", "C", "OL", "K", "P", "LS", "")
NFL_NOT_TWO_WAY_SQL = "(" + ",".join(f"'{p}'" for p in NFL_NOT_TWO_WAY) + ")"
NFL_RECENT_POS = 8     # upcoming position = most frequent mapped position over the player's last N rows


def nfl_two_way_on():
    """SCOUT_NFL_TWO_WAY=1: keep the offensive rows of defensive-listed (two-way) players and give every upcoming row a
    deterministic position. Off by default: it changes inputs, so it moves projections only after a walk-forward test
    (owner rule) - see sports/research/two_way_position.md."""
    return os.environ.get("SCOUT_NFL_TWO_WAY", "").strip().lower() in ("1", "true", "yes", "on")


def nfl_row_role(position, pass_att, carries, targets):
    """Offensive role of one nfl_pg row. Offensive listings keep their position (FB -> RB, as training always did);
    a defensive / special-teams listing (nflverse lists Travis Hunter as CB in every row) is read from the row's own
    usage: passes -> QB, more carries than targets -> RB, else WR. None when the row has no role to read."""
    if position == "FB":
        return "RB"
    if position in NFL_POS:
        return position
    pa, ca, tg = (0.0 if v != v else float(v) for v in (pass_att, carries, targets))
    if pa > 0 and pa >= max(ca, tg):
        return "QB"
    if ca > tg:
        return "RB"
    if tg > 0:
        return "WR"
    return None


def nfl_positions(pg, recent=NFL_RECENT_POS):
    """Point-in-time position per row for two-way players + one deterministic position per player.

    Rows are walked per player in (ts, game_id) order. An offensive-listed row keeps its mapped listing. A
    defensive-listed row takes the player's most frequent OFFENSIVE LISTING so far (Scott Matlock: DT / FB rows -> RB);
    a player never listed on offense (Travis Hunter: CB in every row) takes the role of his summed usage so far
    (passes / carries / targets, the row's own included - nfl_row_role on the totals); nothing after the row is read.
    Listing ties -> the most recent; no role yet -> WR.
    -> (per-row position array, {player_id: the same rule over the player's last `recent` rows})."""
    n = len(pg)
    pos = np.array(pg["position"], dtype=object)
    order = sorted(range(n), key=lambda i: (pg["player_id"][i], pg["ts"][i], str(pg["game_id"][i])))
    last = {}

    def pick(seq):
        if not seq:
            return None
        cnt = {}
        for p in seq:
            cnt[p] = cnt.get(p, 0) + 1
        best = max(cnt.values())
        for p in reversed(seq):                       # tie -> most recent
            if cnt[p] == best:
                return p

    def usage(used):
        return nfl_row_role("", *np.sum(used, axis=0)) if used else None

    def settle(listed, used):
        return pick(listed[-recent:]) or pick(listed) or usage(used[-recent:])

    cur, listed, used = None, [], []
    for i in order:
        pid = pg["player_id"][i]
        if pid != cur:
            if cur is not None:
                last[cur] = settle(listed, used)
            cur, listed, used = pid, [], []
        p = pg["position"][i]
        if p in NFL_POS:
            listed.append(p)
        else:                                         # defensive / ST listing (FB already mapped by the caller)
            used.append(np.nan_to_num(np.array([pg["pass_att"][i], pg["carries"][i], pg["targets"][i]], float)))
            pos[i] = pick(listed) or usage(used) or "WR"
    if cur is not None:
        last[cur] = settle(listed, used)
    return pos, {k: v for k, v in last.items() if v}


def nfl_frame(con, h_opp=4, upcoming=None, as_of=None, with_lags=True):
    lay = _layered(lambda u: nfl_frame(con, h_opp, upcoming=u, as_of=as_of, with_lags=with_lags), upcoming, with_lags)
    if lay is not None:
        return lay
    two_way = nfl_two_way_on()
    # two-way (sanity 2026-11-05): nflverse lists Travis Hunter (JAX WR/CB) as CB in all 13 rows, so the offensive-only
    # filter dropped his whole history and his upcoming row kept "CB" (no fitted prior) -> PMF collapsed onto 0 (0.05
    # mean at rec 1.5 / rec_yds 9.5). Flag on: a defensive-listed row is kept when that row itself shows offense
    # (offensive snaps, targets, carries or passes) - a per-row test, so no future data decides inclusion.
    keep = ("p.position IN ('QB','RB','WR','TE','FB')" if not two_way else
            "(p.position IN ('QB','RB','WR','TE','FB') OR (COALESCE(p.position, '') NOT IN " + NFL_NOT_TWO_WAY_SQL +
            " AND (COALESCE(p.offense_snaps, 0) > 0 OR "
            "COALESCE(p.targets, 0) + COALESCE(p.carries, 0) + COALESCE(p.pass_att, 0) > 0)))")
    pg = Frame.from_sql(con, "SELECT p.*, g.start_utc, g.spread_home, g.total_line, g.roof, g.temp, g.wind "
                             "FROM nfl_pg p JOIN games g ON g.league='nfl' AND g.game_id = p.game_id "
                             f"WHERE g.status = 'final' AND {keep}")
    pg["position"] = np.where(pg["position"] == "FB", "RB", pg["position"]).astype(object)
    pg["ts"] = epoch(pg["start_utc"])
    for c in NFL_FILL0:
        pg[c] = np.nan_to_num(pg[c])
    if two_way:
        pg, _ = _with_upcoming(pg, "nfl", as_of, None)            # point-in-time cut first, then positions
        pg["position"], recent_pos = nfl_positions(pg)
        upcoming = [dict(r, position=recent_pos.get(r["player_id"], r.get("position", ""))) for r in upcoming or []]
        pg, up_keys = _with_upcoming(pg, "nfl", None, upcoming or None)
    else:
        pg, up_keys = _with_upcoming(pg, "nfl", as_of, upcoming)
    # the SAME position map for the upcoming rows (sanity 2026-10-24, N2): forward rows carry nfl_pg's raw position,
    # and an "FB" row matched no fitted position prior (eff_pos / catch_pos / allow_* keys are QB/RB/WR/TE) -> NaN
    # efficiency -> a PMF collapsed onto 0 (Kyle Juszczyk rec_yds 0.05 projected at an 11.5 line; 11.2 once mapped)
    pg["position"] = np.where(pg["position"] == "FB", "RB", pg["position"]).astype(object)
    if up_keys:                                 # upcoming rows: results unknown (NaN), not 0
        up = pg["is_up"] == 1
        for c in NFL_FILL0:
            pg[c] = np.where(up, np.nan, pg[c])
    played_no_target = pg["targets"] == 0
    for c in ("target_share", "air_yards_share"):
        pg[c] = np.where(np.isnan(pg[c]) & played_no_target, 0.0, pg[c])
    tg = _team_upcoming(_team_frame(con, "nfl_tg", "nfl"), "nfl", as_of, up_keys, upcoming)
    tgs = group_starts(tg["team"])
    tg["plays_l6"] = roll_prior(tg["plays"], tgs, 6, 2)
    with np.errstate(all="ignore"):
        tg["npass_l6"] = roll_prior(tg["neutral_pass"], tgs, 6, 2, "sum") / roll_prior(tg["neutral_plays"], tgs, 6, 2,
                                                                                           "sum")
    tg["rest_days"] = (tg["ts"] - gshift(tg["ts"], tgs, 1)) / 86400
    tkey = key_of(tg["game_id"], tg["team"])
    # main passer per team-game; QB change (same-game fact) is only used LAGGED
    qb = {}
    for i in np.flatnonzero(pg["position"] == "QB"):
        k = (pg["game_id"][i], pg["team"][i])
        if k not in qb or pg["pass_att"][i] > pg["pass_att"][qb[k]]:
            qb[k] = i
    qb1 = np.array([pg["player_id"][qb[(g, t)]] if (g, t) in qb else "" for g, t in zip(tg["game_id"], tg["team"])],
                   dtype=object)
    prev = np.empty(len(tg), dtype=object)
    prev[:] = ""
    idx = np.arange(len(tg))
    ok = idx - 1 >= tgs
    prev[ok] = qb1[idx[ok] - 1]
    chg = np.where(ok, (qb1 != prev).astype(float), np.nan)
    tg["qb_change_lag"] = gshift(chg, tgs, 1)
    # opponent allowance per position (mean of the last 8 opponent games)
    akeys = key_of(pg["game_id"], pg["opp"], pg["position"])
    u, inv = np.unique(akeys, return_inverse=True)
    acols = ("targets", "rec", "rec_yds", "carries", "rush_yds", "pass_att", "pass_yds")
    sums = {c: np.bincount(inv, weights=np.nan_to_num(pg[c]), minlength=len(u)) for c in acols}
    parts = [x.split("|") for x in u]
    ag = Frame({"game_id": np.array([p[0] for p in parts], dtype=object),
                "team": np.array([p[1] for p in parts], dtype=object),
                "position": np.array([p[2] for p in parts], dtype=object), **sums})
    ti = lookup(key_of(ag["game_id"], ag["team"]), tkey)
    ag = ag[ti >= 0]
    ag["ts"] = tg["ts"][ti[ti >= 0]]
    ag["tp"] = key_of(ag["team"], ag["position"])
    ag = ag.sort("tp", "ts")
    ags = group_starts(ag["tp"])
    for c in acols:
        ag[f"allow_{c}"] = roll_prior(ag[c], ags, 8, 3)
    df = pg.sort("player_id", "ts")
    gs = group_starts(df["player_id"])
    _assert_pit(df, gs, "nfl")
    df["prior_n"] = np.arange(len(df)) - gs
    for c in ("targets", "carries", "pass_att", "rec", "rec_yds", "rush_yds", "pass_yds", "pass_cmp", "offense_pct",
              "target_share", "air_yards_share", "rz_targets", "rz_carries", "rec_air_yds"):
        m, w = ew_prior(df[c], gs, h_opp)
        df[f"ew_{c}"] = m
        df[f"S_{c}"] = m * w
    lags = {}
    for mk, c in NFL_MARKETS.items():
        if with_lags:
            lags[mk] = lag_matrix(df[c], gs, 20).astype(np.float32)
        df[f"y_{mk}"] = df[c]
    with np.errstate(all="ignore"):
        df["l3_snap"] = np.nanmean(lag_matrix(df["offense_pct"], gs, 3), axis=1)
        df["l6_targets"] = np.nanmean(lag_matrix(df["targets"], gs, 6), axis=1)
        df["l6_carries"] = np.nanmean(lag_matrix(df["carries"], gs, 6), axis=1)
        df["l6_pass_att"] = np.nanmean(lag_matrix(df["pass_att"], gs, 6), axis=1)
    ti = lookup(key_of(df["game_id"], df["team"]), tkey)
    for c in ("plays_l6", "npass_l6", "rest_days", "qb_change_lag"):
        df[c] = take(tg[c], ti)
    oi = lookup(key_of(df["game_id"], df["opp"]), tkey)
    df["opp_plays_l6"] = take(tg["plays_l6"], oi)
    ai = lookup(key_of(df["game_id"], df["opp"], df["position"]), key_of(ag["game_id"], ag["team"], ag["position"]))
    for c in acols:
        df[f"allow_{c}"] = take(ag[f"allow_{c}"], ai)
    df["team_spread"] = np.where(df["home"] == 1, df["spread_home"], -df["spread_home"])
    df["implied_total"] = df["total_line"] / 2 - df["team_spread"] / 2
    df["dome"] = np.isin(df["roof"], ["dome", "closed"]).astype(float)
    df.attrs["lags"] = lags
    return df


def frame(sport, con, **kw):
    return nba_frame(con, **kw) if sport == "nba" else nfl_frame(con, **kw)
