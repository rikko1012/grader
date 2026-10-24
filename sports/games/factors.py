"""Factors for a player's upcoming game, by the contract in contract.py ("sports-factors/1"), computed from the game
store only - no provider is called here. No look-ahead: a game's result is used only if the game ENDED before `as_of`
(stored last-play time), snapshots (lines, injuries, ratings, weather, prop lines) only if observed before it, and
EPA-based factors only when the frozen expected-points table was trained on games that all ended before it.
Known limit: box scores are ESPN's final versions as fetched; a later stat correction is not dated.

    data = LeagueData(db, "nba", as_of)                      # one load per board
    payload = compute(db, data, athlete_id, team_id, opp_id, event_id, start, market)

Every value is taken from its ONE source (contract.REGISTRY[league][key]["source"]); market columns the board row
already carries (book consensus, exchange chance, pick'em line) are not repeated. Plain Python (the server installs
no pandas).
"""
import json
from collections import defaultdict
from datetime import timedelta

from .. import config, markets
from . import contract, kalshi_lines
from .store import dt, iso

POS_NBA = {"PG": "G", "SG": "G", "G": "G", "SF": "F", "PF": "F", "F": "F", "GF": "F", "FC": "F", "C": "C"}
POS_NFL = {"WR": "WR", "TE": "TE", "RB": "RB", "FB": "RB", "QB": "QB"}
BOX = {
    "basketball": {"minutes": "min", "points": "pts", "fieldGoalsMade": "fgm", "fieldGoalsAttempted": "fga",
                   "threePointFieldGoalsMade": "fg3m", "threePointFieldGoalsAttempted": "fg3a",
                   "freeThrowsMade": "ftm", "freeThrowsAttempted": "fta", "rebounds": "reb",
                   "offensiveRebounds": "oreb", "defensiveRebounds": "dreb", "assists": "ast", "turnovers": "tov",
                   "steals": "stl", "blocks": "blk", "plusMinus": "pm"},
    "football": {"receiving.receivingTargets": "targets", "receiving.receptions": "rec",
                 "receiving.receivingYards": "rec_yds", "rushing.rushingAttempts": "rush_att",
                 "rushing.rushingYards": "rush_yds", "passing.completions": "pass_cmp",
                 "passing.passingAttempts": "pass_att", "passing.passingYards": "pass_yds",
                 "passing.passingTouchdowns": "pass_td", "passing.interceptions": "pass_int", "passing.adjQBR": "qbr",
                 "defensive.QBHits": "def_qbhits", "defensive.sacks": "def_sacks"},
}
TEAM_BOX_NBA = {"fieldGoalsMade": "fgm", "fieldGoalsAttempted": "fga", "threePointFieldGoalsMade": "fg3m",
                "threePointFieldGoalsAttempted": "fg3a", "freeThrowsAttempted": "fta", "offensiveRebounds": "oreb",
                "defensiveRebounds": "dreb", "totalTurnovers": "tov"}
DERIVED_NBA = ("rim_fga", "rim_fgm", "short_mid_fga", "short_mid_fgm", "long_mid_fga", "long_mid_fgm", "corner3_fga",
               "pullup_fga", "spotup_fga", "assisted_fgm", "on_s", "on_pts_for", "on_pts_against",
               "oreb_live_misses_on", "dreb_live_misses_on")
DERIVED_NFL = ("tgt_deep", "tgt_short", "tgt_rz", "tgt_i10", "tgt_air_yds_completed", "rush_rz", "rush_i5",
               "dropbacks", "scrambles", "sacks_taken", "att_deep", "att_short", "att_left", "att_middle", "att_right",
               "cmp_deep", "cmp_short", "air_yds_completed", "db_success", "db_epa")
TEAM_NFL = ("plays", "dropbacks", "rushes", "neutral_plays", "neutral_dropbacks", "neutral_clock_s", "neutral_gaps",
            "success", "epa", "epa_n", "explosive", "drives", "rz_drives", "rz_td_drives")
SHRINK_K = {"basketball": 10, "football": 4}
RESULT_AFTER_H = {"basketball": 3.5, "football": 4.5}       # same as record.SETTLE_AFTER_H
EPA_KEYS = ("qb_epa_db", "team_epa_play", "opp_def_epa_play", "opp_def_rank")
HALF_LIFE = 10
# league-wide baselines (league pace, defense vs position, defense ranks) use the current season once it has this many
# games, else the current and the previous season together - so an early-season team window and its baseline match
MIN_SEASON_GAMES = {"basketball": 150, "football": 32}
MIN_TEAM_GAMES = {"basketball": 10, "football": 4}


# ------------------------------------------------------------------------------------------------- small helpers

def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def S(rows, key):
    """Sum of a field over rows, missing values skipped."""
    return sum(v for v in (r.get(key) for r in rows) if v is not None)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _div(a, b):
    return None if a is None or not b else a / b


def _r(x, n=4):
    return None if x is None else round(float(x), n)


def _ewm_last(vals, half_life=HALF_LIFE):
    """Recency-weighted mean (newest weight 1, halving every half_life games back)."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    w = [0.5 ** ((len(vals) - 1 - i) / half_life) for i in range(len(vals))]
    return sum(a * b for a, b in zip(vals, w)) / sum(w)


def _shrink(ratio, n, k):
    return None if ratio is None else (n * ratio + k) / (n + k)


def _stats(js, keys):
    st = json.loads(js or "{}")
    if isinstance(keys, dict):
        return {v: _f(st.get(k)) for k, v in keys.items()}
    return {k: _f(st.get(k)) for k in keys}


class LeagueData:
    """Every stored game of the league whose result was in before as_of (within `days`), loaded once."""

    def __init__(self, db, league, as_of, days=430):
        self.db, self.lg, self.as_of = db, config.league(league), as_of
        self.memo = {}                          # league-wide tables reused across players (dvp, defense, pace)
        fam = self.lg["family"]
        args = (self.lg["key"], iso(as_of), iso(as_of - timedelta(days=days)))
        games = db.all("SELECT event_id, season, season_type, start, ended_at, home_id, away_id, home_score, "
                       "away_score FROM sports_game_games WHERE league = ? AND completed = 1 AND start < ? AND "
                       "start >= ?", args)
        keep = []
        for g in games:
            # a result counts only once it was in: the game ENDED before as_of (last play's wall clock; without one,
            # start + RESULT_AFTER_H)
            end = dt(g["ended_at"]) if g.get("ended_at") else dt(g["start"]) + timedelta(hours=RESULT_AFTER_H[fam])
            if end < as_of:
                keep.append(g)
        keep.sort(key=lambda g: g["start"])
        self.games = {g["event_id"]: g for g in keep}
        self.season = keep[-1]["season"] if keep else None
        n_cur = sum(1 for g in keep if g["season"] == self.season)
        self.window = {self.season} if n_cur >= MIN_SEASON_GAMES[fam] else {self.season, (self.season or 0) - 1}
        self.ep_trained_through = None
        if fam == "football":
            from . import derive
            self.ep_trained_through = (derive.load_ep() or {}).get("trained_through")
        self.P, self.T = [], {}
        self.by_ath, self.by_team, self.by_opp = defaultdict(list), defaultdict(list), defaultdict(list)
        self.team_games = defaultdict(list)
        if not keep:
            return
        pos = {r["athlete_id"]: r["position"] for r in db.all(
            "SELECT athlete_id, position FROM sports_game_athletes WHERE league = ?", (self.lg["key"],))}
        dkeys = DERIVED_NBA if fam == "basketball" else DERIVED_NFL
        derived = {(r["event_id"], r["entity_id"]): _stats(r["stats"], dkeys) for r in db.all(
            "SELECT d.event_id, d.entity_id, d.stats FROM sports_game_derived d JOIN sports_game_games g ON g.league = "
            "d.league AND g.event_id = d.event_id WHERE d.league = ? AND d.level = 'player' AND g.completed = 1 AND "
            "g.start < ? AND g.start >= ?", args) if r["event_id"] in self.games}
        for r in db.all("SELECT p.event_id, p.athlete_id, p.player_key, p.team_id, p.opp_id, p.home, p.position, "
                        "p.starter, p.played, p.dnp_reason, p.stats FROM sports_game_players p JOIN sports_game_games g "
                        "ON g.league = p.league AND g.event_id = p.event_id WHERE p.league = ? AND g.completed = 1 AND "
                        "g.start < ? AND g.start >= ?", args):
            gm = self.games.get(r["event_id"])
            if not gm:
                continue
            row = {k: r[k] for k in ("event_id", "athlete_id", "player_key", "team_id", "opp_id", "home", "starter",
                                     "played", "dnp_reason")}
            row["position"] = r["position"] or pos.get(r["athlete_id"])
            row["season"], row["start"] = gm["season"], gm["start"]
            row.update(_stats(r["stats"], BOX[fam]))
            row.update(derived.get((r["event_id"], r["athlete_id"])) or {k: None for k in dkeys})
            self.P.append(row)
        self.P.sort(key=lambda x: x["start"])
        (self._nba_tables if fam == "basketball" else self._nfl_tables)(db, args)
        for row in self.P:
            self.by_ath[row["athlete_id"]].append(row)
            self.by_team[row["team_id"]].append(row)
            self.by_opp[row["opp_id"]].append(row)

    def _nba_tables(self, db, args):
        tmin = defaultdict(float)
        for p in self.P:
            tmin[(p["event_id"], p["team_id"])] += p.get("min") or 0
        T = {}
        for r in db.all("SELECT t.event_id, t.team_id, t.opp_id, t.home, t.score, t.stats FROM sports_game_teams t JOIN "
                        "sports_game_games g ON g.league = t.league AND g.event_id = t.event_id WHERE t.league = ? AND "
                        "g.completed = 1 AND g.start < ? AND g.start >= ?", args):
            if r["event_id"] not in self.games:
                continue
            row = {"event_id": r["event_id"], "team_id": r["team_id"], "opp_id": r["opp_id"],
                   "pts": _f(r["score"]), "season": self.games[r["event_id"]]["season"],
                   "start": self.games[r["event_id"]]["start"], "tm_min": tmin.get((r["event_id"], r["team_id"]))}
            row.update(_stats(r["stats"], TEAM_BOX_NBA))
            T[(r["event_id"], r["team_id"])] = row
        for (ev, tid), t in T.items():
            o = T.get((ev, t["opp_id"]))
            if not o:
                continue
            for k in ("pts", "fga", "fgm", "fg3a", "fg3m", "fta", "oreb", "dreb", "tov"):
                t[f"o_{k}"] = o.get(k)
            try:
                t["poss"] = 0.5 * ((t["fga"] + 0.4 * t["fta"] - 1.07 * (t["oreb"] / (t["oreb"] + o["dreb"]))
                                    * (t["fga"] - t["fgm"]) + t["tov"])
                                   + (o["fga"] + 0.4 * o["fta"] - 1.07 * (o["oreb"] / (o["oreb"] + t["dreb"]))
                                      * (o["fga"] - o["fgm"]) + o["tov"]))
            except (TypeError, ZeroDivisionError):
                t["poss"] = None
        self.T = T
        for t in sorted(T.values(), key=lambda x: x["start"]):
            if t.get("poss") is not None:
                self.team_games[t["team_id"]].append(t)
        for p in self.P:
            t = T.get((p["event_id"], p["team_id"]))
            if not t or not t.get("tm_min") or p.get("min") is None or "o_dreb" not in t:
                continue
            share = p["min"] / (t["tm_min"] / 5)
            p["used"] = (p["fga"] or 0) + 0.44 * (p["fta"] or 0) + (p["tov"] or 0)
            p["tm_used_on"] = (t["fga"] + 0.44 * t["fta"] + t["tov"]) * share
            p["ast_den"] = share * t["fgm"] - (p["fgm"] or 0)
            p["oreb_den"] = share * (t["oreb"] + t["o_dreb"])
            p["dreb_den"] = share * (t["dreb"] + t["o_oreb"])
            p["tm_min"] = t["tm_min"]

    def _nfl_tables(self, db, args):
        T = {}
        for r in db.all("SELECT d.event_id, d.entity_id, d.stats FROM sports_game_derived d JOIN sports_game_games g ON "
                        "g.league = d.league AND g.event_id = d.event_id WHERE d.league = ? AND d.level = 'team' AND "
                        "g.completed = 1 AND g.start < ? AND g.start >= ?", args):
            gm = self.games.get(r["event_id"])
            if not gm:
                continue
            row = {k: v or 0.0 for k, v in _stats(r["stats"], TEAM_NFL).items()}
            row.update(event_id=r["event_id"], team_id=r["entity_id"], season=gm["season"], start=gm["start"],
                       opp_id=gm["away_id"] if r["entity_id"] == gm["home_id"] else gm["home_id"])
            T[(r["event_id"], r["entity_id"])] = row
        self.T = T
        for t in sorted(T.values(), key=lambda x: x["start"]):
            self.team_games[t["team_id"]].append(t)
        sums = defaultdict(lambda: defaultdict(float))
        for p in self.P:
            s = sums[(p["event_id"], p["team_id"])]
            for k in ("targets", "rush_att", "tgt_rz", "rush_rz", "rush_i5", "tgt_air_yds_completed"):
                s[k] += p.get(k) or 0
            if POS_NFL.get(p.get("position") or "") == "RB":
                s["rb_carries"] += p.get("rush_att") or 0
        for p in self.P:
            s = sums[(p["event_id"], p["team_id"])]
            for k, v in s.items():
                p[f"tm_{k}"] = v
            t = T.get((p["event_id"], p["team_id"]))
            p["tm_plays"] = t["plays"] if t else None

    def player_games(self, athlete_id):
        return [r for r in self.by_ath.get(str(athlete_id), []) if r.get("played") == 1]

    def stat(self, row, market):
        m = markets.market(self.lg["family"], market)
        if not m:
            return None
        vals = [row.get(p) for p in m["parts"]]
        return None if any(v is None for v in vals) else sum(vals)


def _snapshot(db, league, event_id, kind, as_of):
    r = db.one("SELECT data, observed_at FROM sports_game_snapshots WHERE league = ? AND event_id = ? AND kind = ? AND "
               "observed_at <= ? AND late = 0 ORDER BY id DESC LIMIT 1", (league, event_id, kind, iso(as_of)))
    return (json.loads(r["data"]), r["observed_at"]) if r else (None, None)


def _game_lines(db, league, event_id, team_id, home_id, away_id, as_of, out, missing):
    pc, _ = _snapshot(db, league, event_id, "pickcenter", as_of)
    home = team_id == home_id
    if pc and pc[0].get("spread") is not None and pc[0].get("total") is not None:
        sp, tot = float(pc[0]["spread"]), float(pc[0]["total"])
        out["spread"] = sp if home else -sp
        out["game_total"] = tot
        out["team_total"] = tot / 2 - out["spread"] / 2
        out["lines_provider"] = pc[0].get("provider")
    else:
        for k in ("spread", "game_total", "team_total"):
            missing[k] = "no ESPN pickcenter line before as_of"
    rows = db.all("SELECT m.* FROM sports_game_market_lines m JOIN (SELECT provider_market_id, MAX(id) AS id FROM "
                  "sports_game_market_lines WHERE league = ? AND event_id = ? AND observed_at <= ? AND late = 0 GROUP BY "
                  "provider_market_id) z ON z.id = m.id", (league, event_id, iso(as_of)))
    imp = kalshi_lines.implied(rows, home_id, away_id) if rows else None
    if imp and imp["home_win"] is not None:
        out["win_prob"] = imp["home_win"] if home else 1 - imp["home_win"]
    else:
        missing["win_prob"] = "no Kalshi winner market before as_of"
    target = 14.5 if config.league(league)["family"] == "basketball" else 13.5
    pm = (imp or {}).get("p_margin_over") or {}
    if pm:
        s = min(pm, key=lambda k: abs(k - target))
        out["blowout_prob"], out["blowout_strike"] = pm[s], s
    else:
        missing["blowout_prob"] = "no Kalshi spread ladder before as_of"
    if imp and config.league(league)["family"] == "basketball":
        tt = imp["home_team_total"] if home else imp["away_team_total"]
        if tt is not None:
            out["kalshi_team_total"] = tt                  # NBA team totals: Kalshi is the only ladder that has them


def _rest(data, team_id, start):
    s = dt(start)
    prev = [dt(g["start"]) for g in data.games.values() if team_id in (g["home_id"], g["away_id"]) and dt(g["start"]) < s]
    if not prev:
        return None, 0
    return (s - max(prev)).total_seconds() / 86400, sum(1 for t in prev if t >= s - timedelta(days=4))


def _injuries(db, league, team_id, as_of):
    inj, _ = _snapshot(db, league, "league", "injuries", as_of)
    return [i for i in inj or [] if str(i.get("team_id")) == str(team_id)
            and (i.get("status") or "").lower() in ("out", "doubtful")]


def _power(db, league, team_id, as_of):
    rows, _ = _snapshot(db, league, "league", "powerindex", as_of)
    return next((r.get("rating") for r in rows or [] if str(r.get("team_id")) == str(team_id)), None)


def _dk(db, league, event_id, athlete_id, market, as_of):
    r = db.one("SELECT line, line_open FROM sports_game_prop_lines WHERE id = (SELECT MAX(id) FROM "
               "sports_game_prop_lines WHERE league = ? AND event_id = ? AND athlete_id = ? AND market = ? AND "
               "period = 'game' AND kind = 'line' AND observed_at <= ? AND late = 0)",
               (league, event_id, str(athlete_id), market, iso(as_of)))
    return (r["line"], r["line_open"]) if r else (None, None)


def _form(data, games, market, home, out):
    vals = [(g, data.stat(g, market)) for g in games]
    vals = [(g, v) for g, v in vals if v is not None]
    if not vals:
        return
    v = [x for _, x in vals]
    out["stat_l3"], out["stat_l5"], out["stat_l10"] = (_r(_mean(v[-n:]), 2) for n in (3, 5, 10))
    out["stat_season"] = _r(_mean([x for g, x in vals if g["season"] == data.season]), 2)
    out["stat_prior"] = _r(_mean([x for g, x in vals if g["season"] == data.season - 1]), 2)
    out["stat_home_away"] = _r(_mean([x for g, x in vals if g["home"] == int(bool(home))
                                      and g["season"] >= data.season - 1]), 2)
    out["games_season"] = sum(1 for g in games if g["season"] == data.season)


# ------------------------------------------------------------------------------------------------------ basketball

def _team_window(data, games):
    """A team's games for season-level numbers: this season once it has MIN_TEAM_GAMES, else its last 2x that many."""
    fam = data.lg["family"]
    cur = [t for t in games if t["season"] == data.season]
    return cur if len(cur) >= MIN_TEAM_GAMES[fam] else games[-2 * MIN_TEAM_GAMES[fam]:]


def _pace(rows):
    return _div(48 * S(rows, "poss"), S(rows, "tm_min") / 5)


def _nba(data, ath, team_id, opp_id, start, market, out, missing):
    g = data.player_games(ath)
    if not g:
        missing["min_proj"] = "no games played in the window"
        return None
    cur = [r for r in g if r["season"] == data.season]
    base = cur if len(cur) >= 5 else [r for r in g if r["season"] >= data.season - 1]
    mins = [r["min"] for r in g]
    out["min_l5"], out["min_l10"] = _r(_mean(mins[-5:]), 1), _r(_mean(mins[-10:]), 1)
    out["min_season"] = _r(_mean([r["min"] for r in cur]), 1)
    ewm = _ewm_last(mins)
    if _mean(mins[-3:]) is not None and _mean(mins[-10:]) is not None:
        out["role_change"] = _r(_mean(mins[-3:]) - _mean(mins[-10:]), 1)
    l10 = g[-10:]
    out["usg_l10"] = _r(100 * (_div(S(l10, "used"), S(l10, "tm_used_on")) or 0), 1) if S(l10, "tm_used_on") else None
    out["usg_season"] = _r(100 * _div(S(base, "used"), S(base, "tm_used_on")), 1) if S(base, "tm_used_on") else None
    m = S(base, "min")
    for k, c in (("fga_per36", "fga"), ("fg3a_per36", "fg3a"), ("fta_per36", "fta")):
        out[k] = _r(36 * _div(S(base, c), m), 2) if m else None
    for k, num, den in (("ast_pct", "ast", "ast_den"), ("oreb_pct", "oreb", "oreb_den"), ("dreb_pct", "dreb", "dreb_den")):
        out[k] = _r(100 * _div(S(base, num), S(base, den)), 1) if S(base, den) else None
    fga = S(base, "fga")
    has_pbp = any(r.get("rim_fga") is not None for r in base)
    if fga:
        if has_pbp:
            out["rim_share"] = _r(S(base, "rim_fga") / fga, 3)
            out["mid_share"] = _r((S(base, "short_mid_fga") + S(base, "long_mid_fga")) / fga, 3)
            out["spotup_share"] = _r(S(base, "spotup_fga") / fga, 3)
            out["pullup_share"] = _r(S(base, "pullup_fga") / fga, 3)
        out["three_share"] = _r(S(base, "fg3a") / fga, 3)
        out["ts_pct"] = _r(_div(S(base, "pts"), 2 * (fga + 0.44 * S(base, "fta"))), 3)
        out["efg_pct"] = _r((S(base, "fgm") + 0.5 * S(base, "fg3m")) / fga, 3)
        out["ft_rate"] = _r(S(base, "fta") / fga, 3)
    if has_pbp:
        out["corner3_share"] = _r(_div(S(base, "corner3_fga"), S(base, "fg3a")), 3)
        out["assisted_share"] = _r(_div(S(base, "assisted_fgm"), S(base, "fgm")), 3)
        ch = S(base, "oreb_live_misses_on") + S(base, "dreb_live_misses_on")
        out["reb_per_live_miss_proxy"] = _r(_div(S(base, "reb"), ch), 3)
    out["starter_rate_l10"] = _r(_mean([r["starter"] or 0 for r in l10]), 2)
    out["started_last"] = bool(g[-1]["starter"]) if g[-1]["starter"] is not None else None
    T = data.team_games.get(str(team_id), [])
    if has_pbp and T:
        mine = {r["event_id"] for r in base}
        tg = [t for t in T if t["event_id"] in mine]
        team_s = S(tg, "tm_min") / 5 * 60
        on_s = S(base, "on_s")
        on_net = _div(S(base, "on_pts_for") - S(base, "on_pts_against"), on_s)
        off_net = _div((S(tg, "pts") - S(base, "on_pts_for")) - (S(tg, "o_pts") - S(base, "on_pts_against")),
                       team_s - on_s)
        if on_net is not None and off_net is not None:
            out["on_off_net"] = _r((on_net - off_net) * 48 * 60, 1)
            out["on_off_games"], out["on_off_on_min"] = len(tg), _r(on_s / 60, 1)   # its sample (helper keys)
    if "league_pace" not in data.memo:
        data.memo["league_pace"] = _pace([t for ts in data.team_games.values() for t in ts
                                          if t["season"] in data.window])
    lp = data.memo["league_pace"]
    O = data.team_games.get(str(opp_id), [])
    out["pace_team"] = _r(_pace(T[-10:]), 1) if T else None
    out["pace_opp"] = _r(_pace(O[-10:]), 1) if O else None
    if out["pace_team"] and out["pace_opp"] and lp:
        out["pace_expected"] = _r(out["pace_team"] + out["pace_opp"] - lp, 1)
    Oc = _team_window(data, O)
    if Oc:
        poss = S(Oc, "poss")
        out["opp_drtg"] = _r(100 * _div(S(Oc, "o_pts"), poss), 1)
        out["opp_fg3a_allowed_per100"] = _r(100 * _div(S(Oc, "o_fg3a"), poss), 1)
        out["opp_fg3_allowed"] = _r(_div(S(Oc, "o_fg3m"), S(Oc, "o_fg3a")), 3)
        out["opp_oreb_pct"] = _r(_div(S(Oc, "oreb"), S(Oc, "oreb") + S(Oc, "o_dreb")), 3)
        out["opp_dreb_pct"] = _r(_div(S(Oc, "dreb"), S(Oc, "dreb") + S(Oc, "o_oreb")), 3)
        seen = {t["event_id"] for t in Oc}
        vs = [r for r in data.by_opp.get(str(opp_id), []) if r["event_id"] in seen]
        if any(r.get("rim_fga") is not None for r in vs):
            out["opp_rim_fga_allowed_per100"] = _r(100 * _div(S(vs, "rim_fga"), poss), 1)
            out["opp_rim_fg_allowed"] = _r(_div(S(vs, "rim_fgm"), S(vs, "rim_fga")), 3)
            out["opp_mid_fg_allowed"] = _r(_div(S(vs, "short_mid_fgm") + S(vs, "long_mid_fgm"),
                                                S(vs, "short_mid_fga") + S(vs, "long_mid_fga")), 3)
        grp = POS_NBA.get(next((r["position"] for r in reversed(g) if r.get("position")), "") or "")
        if grp:
            key = ("dvp", market, grp)
            if key not in data.memo:
                per = defaultdict(float)
                for r in data.P:
                    if r["season"] in data.window and r.get("played") == 1 and \
                            POS_NBA.get(r.get("position") or "") == grp:
                        v = data.stat(r, market)
                        if v is not None:
                            per[(r["event_id"], r["opp_id"])] += v
                data.memo[key] = per
            per = data.memo[key]
            mine = [v for (ev, o), v in per.items() if o == str(opp_id)]
            if mine and per:
                out["dvp"] = _r(_shrink(_mean(mine) / _mean(list(per.values())), len(mine), SHRINK_K["basketball"]), 3)
                out["dvp_group"] = grp
    recent = {t["event_id"] for t in T[-10:]}
    tr = [r for r in data.by_team.get(str(team_id), []) if r["event_id"] in recent]
    if tr:
        tot = defaultdict(float)
        for r in tr:
            tot[r["athlete_id"]] += r.get("min") or 0
        n = len(recent) or 1
        out["rotation_depth"] = sum(1 for v in tot.values() if v / n >= 12)
        out["starters_min_share"] = _r(_div(sum(r.get("min") or 0 for r in tr if r.get("starter") == 1),
                                            sum(r.get("min") or 0 for r in tr)), 3)
    return ewm


def _nba_teammates(db, data, ath, team_id, market, out, missing, ewm_min):
    out_list = _injuries(db, data.lg["key"], team_id, data.as_of)
    names_, vac_min, vac_use, top = [], 0.0, 0.0, None
    tm = [r for r in data.by_team.get(str(team_id), []) if r["season"] >= data.season - 1]
    for i in out_list:
        aid = str(i.get("athlete_id"))
        if aid == str(ath):
            continue
        r = [x for x in tm if x["athlete_id"] == aid and x.get("played") == 1]
        if not r:
            continue
        names_.append(i.get("player"))
        mpg = _mean([x["min"] for x in r]) or 0
        vac_min += mpg
        vac_use += (_div(S(r, "used"), S(r, "tm_used_on")) or 0) * mpg / 48   # share of the team's 48 minutes
        if top is None or mpg > top[1]:
            top = (aid, mpg)
    out["teammates_out"] = ", ".join(n for n in names_ if n) or ("none" if _snapshot(
        db, data.lg["key"], "league", "injuries", data.as_of)[0] is not None else None)
    out["vacated_min"] = _r(vac_min, 1)
    out["vacated_usage"] = _r(vac_use, 3)
    if top:
        mate = {x["event_id"]: x.get("played") for x in tm if x["athlete_id"] == top[0]}
        me = [r for r in data.player_games(ath) if r["season"] >= data.season - 1 and r.get("min")]
        wo = [36 * data.stat(r, market) / r["min"] for r in me if mate.get(r["event_id"]) == 0
              and data.stat(r, market) is not None]
        w = [36 * data.stat(r, market) / r["min"] for r in me if mate.get(r["event_id"]) == 1
             and data.stat(r, market) is not None]
        if len(wo) >= 3 and len(w) >= 3:
            out["with_without_delta"] = _r(_mean(wo) - _mean(w), 2)
        else:
            missing["with_without_delta"] = f"fewer than 3 games with and without the top teammate out ({len(wo)})"
    else:
        missing["with_without_delta"] = "no teammate listed Out / Doubtful"
    if ewm_min is not None:
        proj = ewm_min
        if out.get("role_change") and abs(out["role_change"]) >= 4:
            proj += 0.5 * out["role_change"]
        if vac_min and out.get("rotation_depth"):
            proj += min(6.0, vac_min / max(5, out["rotation_depth"]))   # vacated minutes spread over the rotation
        out["min_proj"] = _r(min(proj, 42.0), 1)


# -------------------------------------------------------------------------------------------------------- football

def _nfl(db, data, ath, team_id, opp_id, start, market, out, missing):
    g = data.player_games(ath)
    if not g:
        missing["tgt_share_season"] = "no games played in the window"
        return
    cur = [r for r in g if r["season"] == data.season]
    base = cur if len(cur) >= 3 else [r for r in g if r["season"] >= data.season - 1]
    l3 = g[-3:]

    def share(rows, a, b):
        return _r(_div(S(rows, a), S(rows, b)), 3) if S(rows, b) else None
    out["tgt_share_l3"], out["tgt_share_season"] = share(l3, "targets", "tm_targets"), share(base, "targets", "tm_targets")
    out["carry_share_l3"], out["carry_share_season"] = share(l3, "rush_att", "tm_rush_att"), \
        share(base, "rush_att", "tm_rush_att")
    out["air_share"] = share(base, "tgt_air_yds_completed", "tm_tgt_air_yds_completed")
    deep, short = S(base, "tgt_deep"), S(base, "tgt_short")
    out["deep_tgt_share"] = _r(_div(deep, deep + short), 3) if deep + short else None
    out["rz_tgt_share"] = share(base, "tgt_rz", "tm_tgt_rz")
    out["rz_carry_share"] = share(base, "rush_rz", "tm_rush_rz")
    out["i5_carry_share"] = share(base, "rush_i5", "tm_rush_i5")
    pos = POS_NFL.get(next((r["position"] for r in reversed(g) if r.get("position")), "") or "")
    out["position"] = pos
    if pos == "RB":
        out["rb_committee"] = share(base, "rush_att", "tm_rb_carries")
    plays = S(base, "tm_plays")
    out["involvement_rate"] = _r(_div(S(base, "targets") + S(base, "rush_att"), plays), 3) if plays else None
    a, b = ("carry_share_l3", "carry_share_season") if pos == "RB" else ("tgt_share_l3", "tgt_share_season")
    if out.get(a) is not None and out.get(b) is not None:
        out["usage_trend"] = _r(out[a] - out[b], 3)
    db_ = S(base, "dropbacks")
    if pos == "QB" and db_:
        att = S(base, "att_deep") + S(base, "att_short")
        out["qb_deep_rate"] = _r(_div(S(base, "att_deep"), att), 3) if att else None
        lmr = [S(base, f"att_{d}") for d in ("left", "middle", "right")]
        out["qb_left_middle_right"] = "/".join(f"{x / sum(lmr):.2f}" for x in lmr) if sum(lmr) else None
        cmp_ = S(base, "cmp_deep") + S(base, "cmp_short")
        out["qb_air_per_cmp"] = _r(_div(S(base, "air_yds_completed"), cmp_), 2) if cmp_ else None
        out["qb_epa_db"] = _r(S(base, "db_epa") / db_, 3)
        out["qb_success_db"] = _r(S(base, "db_success") / db_, 3)
        out["qb_scramble_rate"] = _r(S(base, "scrambles") / db_, 3)
        out["qb_sack_rate"] = _r(S(base, "sacks_taken") / db_, 3)
        out["qb_adj_qbr"] = _r(_mean([r.get("qbr") for r in base]), 1)
    T = _team_window(data, data.team_games.get(str(team_id), []))
    if T:
        out["neutral_pass_rate"] = _r(_div(S(T, "neutral_dropbacks"), S(T, "neutral_plays")), 3)
        out["pace_neutral_s"] = _r(_div(S(T, "neutral_clock_s"), S(T, "neutral_gaps")), 1)
        out["plays_pg"] = _r(_mean([t["plays"] for t in data.team_games[str(team_id)][-5:]]), 1)
        out["team_epa_play"] = _r(_div(S(T, "epa"), S(T, "epa_n")), 3)
        out["team_success"] = _r(_div(S(T, "success"), S(T, "plays")), 3)
        out["team_explosive"] = _r(_div(S(T, "explosive"), S(T, "plays")), 3)
        out["rz_td_rate"] = _r(_div(S(T, "rz_td_drives"), S(T, "rz_drives")), 3)
    if "by_def" not in data.memo:
        agg = defaultdict(lambda: defaultdict(float))
        for t in data.T.values():
            if t["season"] in data.window:
                for k in ("epa", "epa_n", "success", "plays", "explosive", "dropbacks"):
                    agg[t["opp_id"]][k] += t[k]
        for d in agg.values():
            d["epa_pp"] = _div(d["epa"], d["epa_n"])
        data.memo["by_def"] = agg
    by_def = data.memo["by_def"]
    d = by_def.get(str(opp_id))
    if d and d.get("epa_pp") is not None:
        out["opp_def_epa_play"] = _r(d["epa_pp"], 3)
        out["opp_def_success"] = _r(_div(d["success"], d["plays"]), 3)
        out["opp_def_explosive"] = _r(_div(d["explosive"], d["plays"]), 3)
        out["opp_def_rank"] = 1 + sum(1 for x in by_def.values() if x.get("epa_pp") is not None
                                      and x["epa_pp"] < d["epa_pp"])
        defs = [r for r in data.by_team.get(str(opp_id), []) if r["season"] in data.window]
        out["opp_pressure"] = _r(_div(S(defs, "def_qbhits") + S(defs, "def_sacks"), d["dropbacks"]), 3)
    if pos in ("WR", "TE", "RB"):
        col = "rush_yds" if pos == "RB" and market in ("rush_yds", "rush_att") else "rec_yds"
        key = ("pos_allowed", pos, col)
        if key not in data.memo:
            per = defaultdict(float)
            for r in data.P:
                if r["season"] in data.window and r.get("played") == 1 and POS_NFL.get(r.get("position") or "") == pos:
                    per[(r["event_id"], r["opp_id"])] += r.get(col) or 0
            data.memo[key] = per
        per = data.memo[key]
        mine = [v for (ev, o), v in per.items() if o == str(opp_id)]
        if mine and per:
            out["opp_pos_allowed"] = _r(_shrink(_mean(mine) / _mean(list(per.values())), len(mine),
                                                SHRINK_K["football"]), 3)
    days, _ = _rest(data, str(team_id), start)
    if days is not None:
        out["short_week"] = days < 6
        out["off_bye"] = days >= 10
    out_list = [i for i in _injuries(db, data.lg["key"], team_id, data.as_of) if str(i.get("athlete_id")) != str(ath)]
    out["teammates_out"] = ", ".join(i.get("player") or "" for i in out_list) or ("none" if _snapshot(
        db, data.lg["key"], "league", "injuries", data.as_of)[0] is not None else None)
    tm = [r for r in data.by_team.get(str(team_id), []) if r["season"] == data.season]
    vac = 0.0
    for i in out_list:
        r = [x for x in tm if x["athlete_id"] == str(i.get("athlete_id"))]
        if r and S(r, "tm_targets"):
            vac += S(r, "targets") / S(r, "tm_targets")
    out["vacated_tgt_share"] = _r(vac, 3)


def _weather(db, league, event_id, as_of, out, missing):
    w, _ = _snapshot(db, league, event_id, "weather", as_of)
    if not w:
        for k in ("indoor", "weather_temp_f", "weather_wind_mph", "weather_precip_pct"):
            missing[k] = "no venue / forecast snapshot before as_of (NWS covers outdoor US venues only)"
        return
    out["indoor"] = bool(w.get("indoor"))
    if not w.get("indoor"):
        out["weather_temp_f"], out["weather_wind_mph"] = w.get("temp_f"), w.get("wind_mph")
        out["weather_precip_pct"] = w.get("precip_pct")


# ------------------------------------------------------------------------------------------------------- payload

def compute(db, data, athlete_id, team_id, opp_id, event_id, start, market, home=None):
    league = data.lg["key"]
    team_id, opp_id = str(team_id), str(opp_id)
    out, missing = {}, {}
    if home is None:
        g = db.one("SELECT home_id FROM sports_game_games WHERE league = ? AND event_id = ?", (league, event_id))
        home = bool(g and g["home_id"] == team_id)
    out["home"] = bool(home)
    home_id, away_id = (team_id, opp_id) if home else (opp_id, team_id)
    if not data.games:
        missing["*"] = "no stored game whose result was in before as_of"
    elif data.lg["family"] == "basketball":
        ewm = _nba(data, athlete_id, team_id, opp_id, start, market, out, missing)
        _nba_teammates(db, data, athlete_id, team_id, market, out, missing, ewm)
        days, n4 = _rest(data, team_id, start)
        od, _ = _rest(data, opp_id, start)
        out["rest_days"], out["b2b"], out["games_last4d"] = _r(days, 2), days is not None and days < 1.5, n4
        out["opp_rest_days"] = _r(od, 2)
    else:
        _nfl(db, data, athlete_id, team_id, opp_id, start, market, out, missing)
        days, _ = _rest(data, team_id, start)
        out["rest_days"] = _r(days, 2)
        _weather(db, league, event_id, data.as_of, out, missing)
    if data.games:
        _form(data, data.player_games(athlete_id), market, home, out)
    _game_lines(db, league, event_id, team_id, home_id, away_id, data.as_of, out, missing)
    out["team_rating"] = _power(db, league, team_id, data.as_of)
    out["opp_rating"] = _power(db, league, opp_id, data.as_of)
    line, opened = _dk(db, league, event_id, athlete_id, market, data.as_of)
    out["dk_line"], out["dk_line_open"] = line, opened
    out["dk_line_move"] = _r(line - opened, 2) if line is not None and opened is not None else None
    tt = data.ep_trained_through
    if data.lg["family"] == "football" and (not tt or iso(data.as_of) <= tt):
        for k in EPA_KEYS:                           # the expected-points table must predate the forecast
            out.pop(k, None)
            missing[k] = (f"expected-points table trained on games through {tt}, not before as_of" if tt
                          else "no frozen expected-points table")
    for k, spec in contract.REGISTRY[league].items():
        if k in contract.ON_ROW:
            continue
        if spec["status"] == "unavailable":
            missing[k] = spec["how"]
        elif out.get(k) is None and k not in missing:
            missing[k] = "no data for this player / game before as_of"
    return {"contract": contract.CONTRACT, "as_of": iso(data.as_of), "league": league, "event_id": str(event_id),
            "athlete_id": str(athlete_id), "team_id": team_id, "opp_id": opp_id, "market": market,
            "factors": {k: v for k, v in out.items() if v is not None}, "missing": missing}
