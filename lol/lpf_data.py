"""lpf-1 data layer (research / shadow only; nothing live imports it): the full LoL history from a read-only local copy of
production's tables, the per-(series, player) targets, and the reconstruction of the LIVE projection (lol-1 + lol-1f, the
245-day window, the windowed Elo, the A2 Maps 1-3 mixture) on every historical series - the baseline every candidate is
compared with.

  python -m lol.lpf_eval --db <lol_full.db> ...     (the harness; this module only loads and reconstructs)

Source: C:\\Users\\tikic\\cs2_scout_ops\\data\\lol_full\\lol_full.db (made 2026-11-05 from the admin read-only paged views; tables
hist_games / hist_player_games = production lol_games / lol_player_games, center_c_series, lp_Tournaments, forecasts,
settlements). The file is opened read-only (mode=ro).

Every feature for a target is built from games on days STRICTLY BEFORE its match day (the match day is the day of the
series' first game, as in lol/backtest.py and the live forecast).
"""
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

STATS = ("kills", "deaths", "assists")
J = {"kills": 0, "deaths": 1, "assists": 2}
HISTORY_DAYS = 245                      # lol/center/forecast.py HISTORY_DAYS (the live window)


def day_of(iso):
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).date().toordinal()


def connect(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


class Store:
    """Everything the harness reads, loaded once."""

    def __init__(self, path):
        con = connect(path)
        cols = ("game_key, player_id, team_id, opponent_id, team, opponent, side, role, champion, kills, deaths, assists, cs, gold, "
                "team_kills, opp_kills, win, duration_s, match_key, game_number, best_of, played_at, league, damage, vision")
        self.rows = [dict(r) for r in con.execute(f"SELECT {cols} FROM hist_player_games WHERE played_at IS NOT NULL")]
        self.games = {r["game_key"]: dict(r) for r in con.execute("SELECT * FROM hist_games")}
        self.series = {r["sid"]: dict(r) for r in con.execute(
            "SELECT sid, tournament_page, tournament, best_of, scheduled_utc, first_game_utc, n_games, score1, score2, ff, nullified "
            "FROM center_c_series")}
        self.tourn = {}
        for r in con.execute("SELECT skey, data FROM lp_Tournaments WHERE gone_at IS NULL"):
            try:
                self.tourn[r["skey"]] = json.loads(r["data"])
            except ValueError:
                pass
        self.con = con
        md = {}
        for r in self.rows:
            d = day_of(r["played_at"])
            r["_day_game"] = d
            md[r["match_key"]] = min(md.get(r["match_key"], d), d)
        self.match_day = md
        for r in self.rows:
            r["_day"] = md[r["match_key"]]
        self.rows.sort(key=lambda r: (r["_day"], r["match_key"], r["game_number"] or 0))

    def tournament_info(self, match_key):
        s = self.series.get(match_key) or {}
        t = self.tourn.get(s.get("tournament_page") or "") or {}
        return {"region": t.get("Region"), "level": t.get("TournamentLevel"), "playoffs": t.get("IsPlayoffs") == "1",
                "intl": t.get("Region") == "International", "page": s.get("tournament_page")}

    def best_of(self, match_key, rows):
        s = self.series.get(match_key) or {}
        bo = s.get("best_of") or max((r.get("best_of") or 0 for r in rows), default=0)
        if not bo:
            n = max((r.get("game_number") or 1 for r in rows), default=1)
            bo = {1: 1, 2: 3, 3: 3}.get(n, 5)
        return int(bo)


def targets(st):
    """One target per (series, player) that has game 1: games {n: (k, d, a)}, team / opponent ids, role, league,
    tournament, best_of, the number of games the series had (n_series)."""
    by_match = defaultdict(list)
    for r in st.rows:
        if r.get("kills") is None or r.get("team_kills") is None or r.get("opp_kills") is None or not r.get("role"):
            continue
        by_match[r["match_key"]].append(r)
    out = []
    for mk, rs in by_match.items():
        n_series = max(int(r.get("game_number") or 1) for r in rs)
        bo = st.best_of(mk, rs)
        ti = st.tournament_info(mk)
        per = defaultdict(dict)
        for r in rs:
            per[r["player_id"]][int(r.get("game_number") or 1)] = r
        for pid, g in per.items():
            if 1 not in g:
                continue
            g1 = g[1]
            game = st.games.get(g1["game_key"]) or {}
            out.append({"match": mk, "pid": pid, "day": st.match_day[mk], "team": g1["team_id"], "opp": g1["opponent_id"],
                        "role": g1["role"], "league": g1.get("league"), "tournament": game.get("tournament"),
                        "patch": game.get("patch"), "best_of": bo, "n_series": n_series,
                        "games": {n: (r["kills"], r["deaths"], r["assists"]) for n, r in g.items()},
                        "win1": g1.get("win"), "intl": ti["intl"], "level": ti["level"], "lp_playoffs": ti["playoffs"]})
    out.sort(key=lambda t: (t["day"], t["match"], t["pid"]))
    return out


def scope_total(t, stat, scope):
    """Realised total for a scope, or None when the target is not settleable for it:
    '1' = game 1; '12' = games 1 and 2 (both played by him); '123' = the games played among 1-3 (all played by him), which
    in a best-of-3 is 2 or 3 games (owner rule, as settle.py)."""
    j, g = J[stat], t["games"]
    if scope == "1":
        return g[1][j]
    if scope == "12":
        if t["n_series"] < 2 or 2 not in g:
            return None
        return g[1][j] + g[2][j]
    if scope == "123":
        need = min(3, t["n_series"])
        if need < 2 or any(n not in g for n in range(1, need + 1)):
            return None
        return sum(g[n][j] for n in range(1, need + 1))
    raise ValueError(scope)


# ============================================================================================ live reconstruction
class _Window(dict):
    """A dict of day-sorted tuple lists whose .get() returns only entries with day >= lo (the live 245-day window)."""

    lo = -math.inf

    def get(self, k, default=None):
        v = dict.get(self, k)
        if v is None:
            return default
        return [x for x in v if x[0] >= self.lo]


class LiveBaseline:
    """The live projection (lol/center/forecast.forecast_one) re-run on history: lol/model.project with the saved fit, the
    lol-1f factor multiplier (lol/factor_fit.json, kills and assists), the windowed Elo, history restricted to the 245
    days before the match day. Returns per target and stat the per-game lambda and the NB sizes."""

    def __init__(self, st, log=print):
        from . import factors, model, teams
        from .center import forecast as fc
        self.m, self.factors = model, factors
        self.ff = json.loads((model.config.PKG_DIR / fc.FACTOR_FIT_FILE).read_text(encoding="utf-8"))
        self.st = st
        rows = [r for r in st.rows if r.get("kills") is not None and r.get("team_kills") is not None and r.get("opp_kills") is not None]
        self.pin = defaultdict(list)       # pid -> (day, k, d, a, tk, ok, win)
        self.tin = defaultdict(list)       # team -> (day, tk, ok)
        seen = set()
        for r in rows:
            self.pin[r["player_id"]].append((r["_day"], r["kills"], r["deaths"], r["assists"], r["team_kills"], r["opp_kills"], r.get("win")))
            k = (r["game_key"], r["team_id"])
            if k not in seen:
                seen.add(k)
                self.tin[r["team_id"]].append((r["_day"], r["team_kills"], r["opp_kills"]))
        # Elo game list as elo.from_rows builds it (one entry per game, the first row's side), keyed like live (teams.key(id))
        seen, self.elo_games = set(), []
        tkey = {}
        for r in st.rows:
            if r["game_key"] in seen or r.get("win") is None:
                continue
            seen.add(r["game_key"])
            for t in (r["team_id"], r["opponent_id"]):
                if t not in tkey:
                    tkey[t] = teams.key(t)
            self.elo_games.append((r["_day"], r.get("game_number") or 0, tkey[r["team_id"]], tkey[r["opponent_id"]], int(r["win"])))
        self.tkey = tkey
        self._elo_day = {}
        games = {gk: {"patch": g.get("patch"), "tournament": g.get("tournament"), "league": g.get("league"),
                      "played_at": g.get("played_at")} for gk, g in st.games.items()}
        self.index = factors.Index(rows, games, match_day=st.match_day)
        self.index.pl = _Window(self.index.pl)
        self.index.tm = _Window(self.index.tm)
        log(f"live baseline: {len(rows)} player-games, {len(self.elo_games)} Elo games")

    def elo_p(self, team, opp, day):
        """Elo built on the games of the HISTORY_DAYS days before `day` (live: History(now) with since = now - 245 d)."""
        if day not in self._elo_day:
            lo = day - HISTORY_DAYS
            from .elo import BASE, K
            rating, by_day = {}, defaultdict(list)
            for g in self.elo_games:
                if lo <= g[0] < day:
                    by_day[g[0]].append(g)
            for d in sorted(by_day):
                for _, _, a, b, aw in sorted(by_day[d], key=lambda g: g[1]):
                    ra, rb = rating.get(a, BASE), rating.get(b, BASE)
                    ea = 1 / (1 + 10 ** ((rb - ra) / 400))
                    rating[a], rating[b] = ra + K * (aw - ea), rb - K * (aw - ea)
            self._elo_day = {day: rating}            # targets are processed in day order: keep one day
        rating = self._elo_day[day]
        from .elo import BASE
        ra, rb = rating.get(self.tkey.get(team, team), BASE), rating.get(self.tkey.get(opp, opp), BASE)
        return 1 / (1 + 10 ** ((rb - ra) / 400))

    def project(self, t):
        """{stat: (lambda_game, factor multiplier)}, p_win (Elo), for one target (target dict from targets())."""
        d, lo = t["day"], t["day"] - HISTORY_DAYS
        pin = [x for x in self.pin.get(t["pid"], ()) if lo <= x[0] < d]
        tin = [x for x in self.tin.get(t["team"], ()) if lo <= x[0] < d]
        oin = [x for x in self.tin.get(t["opp"], ()) if lo <= x[0] < d]
        p_win = self.elo_p(t["team"], t["opp"], d)
        self.index.pl.lo = self.index.tm.lo = lo
        feats = None
        out = {}
        for s in STATS:
            pj = self.m.project(pin, tin, oin, t["role"], d, "1", s, p_win=p_win, league=t["league"])
            fs = (self.ff.get("stats") or {}).get(s)
            mult = 1.0
            if fs and pj.get("form") in ("share", "rate"):
                if feats is None:
                    feats = self.index.features(t["pid"], t["team"], t["opp"], t["role"], d, t["league"], p_win,
                                                t["tournament"], self.ff["consts"])
                mult = self.factors.adjust(feats, fs)[0]
            out[s] = (pj["lambda_game"] * mult, mult, pj["history_games"])
        return out, p_win

    def size(self, stat, scope):
        return self.m.R[(stat, scope)]


def live_dist(m, stat, scope, lam_game, best_of, p_win):
    """The live distribution for a scope: [(weight, mean, size)] (A2: Maps 1-3 in a best-of-3 = 2-or-3-game mixture with
    P(game 3) = 2 p (1 - p) from the Elo p)."""
    if scope == "123" and best_of == 3 and p_win is not None:
        p3 = 2 * p_win * (1 - p_win)
        return [(p3, 3 * lam_game, m.R[(stat, "123")]), (1 - p3, 2 * lam_game, m.R[(stat, "12")])]
    n = {"1": 1, "12": 2, "123": 3}[scope]
    return [(1.0, n * lam_game, m.R[(stat, scope)])]
