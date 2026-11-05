"""lpf-1 - a full-history LoL projection DISTRIBUTION (research / shadow only; nothing live imports this module - a test
enforces it). Owner 2026-11-05: "dont ruin calibration simply fix projections ... we have every possible inch of data".

What it predicts: for a player's kills / deaths / assists, the whole distribution of the scope total (Map 1, Maps 1-2,
Maps 1-3), not a chance at a line. Lines are read off that distribution exactly as live reads them off lol-1's
(over = more than the line). No chance multiplier, no forced Over/Under balance, no side intercept, no stake.

Inputs (all pre-match; every feature from games on days STRICTLY BEFORE the target's match day):
  player   per-game kills / deaths / assists at three half-lives (20 / 60 / 180 days) shrunk to the role, last-5 form,
           per-minute rates, kill / assist / death share of the team's pool, kill participation, damage / gold share,
           CS and vision per minute, win rate, his results in wins and in losses separately, games played (total, last
           30 days), days since his last game, games with this team (stand-ins / new signings), share of his recent games
           in this league (league switch), champion-pool depth
  team / opponent   kills scored and conceded per game, game length, combined kill pace, win rate (half-lives 30 / 90),
           rest days, games in the last 7 days, lineup continuity, the lane opponent's kills and deaths per game
  strength league-aware Elo (lp-1's LeagueElo: team rating + home-league offset that moves only on cross-league games),
           the league gap dL, plain full-history Elo, recent kill-differential gap
  context  league kill pace and game length (last 60 / 365 days), the game's patch (pace on that patch so far, days since
           it appeared), the same tournament's pace on its earlier days, best-of, international / tier-1 / playoffs flags
           (Leaguepedia Tournaments), role, game number within the series
Model: a gradient-boosted Poisson regression for the PER-GAME mean (one row per player-game, games 1-3 of a series), refit
walk-forward; the scope total is negative binomial with a size per (stat, scope, role) fitted by maximum likelihood on
out-of-sample predictions of the window before the refit. Maps 1-3 in a best-of-3 = the 2-or-3-game mixture with
P(game 3) from a walk-forward win model (logistic on the strength inputs).
Variant W (win/loss conditional): the same booster also sees the game's result; the forecast mixes the won-game and
lost-game means over the number of games won (Binomial over the walk-forward win chance; exact series paths in a
best-of-3), each path's total negative binomial.
"""
import bisect
import math
from collections import Counter, defaultdict

import numpy as np

from . import lp1

VERSION = "lpf-1"
SCOPE_N = {"1": 1, "12": 2, "123": 3}
ROLES = ("top", "jungle", "mid", "bot", "support")
ROLE_I = {r: i for i, r in enumerate(ROLES)}
J = {"kills": 0, "deaths": 1, "assists": 2}
STATS = ("kills", "deaths", "assists")


# ============================================================================================ entity arrays
class Arrays:
    """Day-sorted numpy arrays per player / team / league / patch / tournament built from history rows (lpf_data.Store)."""

    PCOLS = ("day", "gn", "k", "d", "a", "tk", "ok", "win", "dur", "dmg", "tdmg", "cs", "gold", "tgold", "vis", "team", "league",
             "champ")
    TCOLS = ("day", "gn", "tk", "ok", "dur", "win", "game")

    def __init__(self, st):
        rows = [r for r in st.rows if r.get("kills") is not None and r.get("team_kills") is not None and r.get("opp_kills") is not None]
        self.tid, self.lid, self.cid = {}, {}, {}
        tdmg, tgold, lineup = defaultdict(float), defaultdict(float), defaultdict(dict)
        for r in rows:
            k = (r["game_key"], r["team_id"])
            tdmg[k] += r.get("damage") or 0
            tgold[k] += r.get("gold") or 0
            lineup[k][r.get("role")] = r["player_id"]
        self.lineup = lineup
        pl, tm, seen = defaultdict(list), defaultdict(list), set()
        self.game_idx = {}
        for r in rows:
            k = (r["game_key"], r["team_id"])
            dur = (r.get("duration_s") or 0) / 60.0 or np.nan
            win = np.nan if r.get("win") is None else float(r["win"])
            pl[r["player_id"]].append((r["_day"], r.get("game_number") or 1, r["kills"], r["deaths"], r["assists"], r["team_kills"],
                                       r["opp_kills"], win, dur, r.get("damage") if r.get("damage") is not None else np.nan,
                                       tdmg[k] or np.nan, r.get("cs") if r.get("cs") is not None else np.nan,
                                       r.get("gold") if r.get("gold") is not None else np.nan, tgold[k] or np.nan,
                                       r.get("vision") if r.get("vision") is not None else np.nan,
                                       self._id(self.tid, r["team_id"]), self._id(self.lid, r.get("league")),
                                       self._id(self.cid, r.get("champion"))))
            if k not in seen:
                seen.add(k)
                gi = self._id(self.game_idx, r["game_key"])
                tm[r["team_id"]].append((r["_day"], r.get("game_number") or 1, r["team_kills"], r["opp_kills"], dur, win, gi))
        self.pl = {p: np.array(sorted(v, key=lambda x: (x[0], x[1])), dtype=float) for p, v in pl.items()}
        self.tm = {t: np.array(sorted(v, key=lambda x: (x[0], x[1])), dtype=float) for t, v in tm.items()}
        self.game_key = {i: g for g, i in self.game_idx.items()}
        # per-game context rows: league pace, patch pace, tournament pace (one row per game)
        lg, pa, tu = defaultdict(list), defaultdict(list), defaultdict(list)
        self.patch_first = {}
        for gk, g in st.games.items():
            if g.get("played_at") is None or g.get("blue_kills") is None or g.get("red_kills") is None:
                continue
            d = st.match_day.get(g["match_key"])
            if d is None:
                continue
            ck, dur = g["blue_kills"] + g["red_kills"], (g.get("duration_s") or 0) / 60.0 or np.nan
            lg[g.get("league")].append((d, ck, dur))
            if g.get("patch"):
                pa[g["patch"]].append((d, ck, dur))
                self.patch_first[g["patch"]] = min(self.patch_first.get(g["patch"], d), d)
            tu[g.get("tournament")].append((d, ck, dur))
        allg = [x for v in lg.values() for x in v]
        self.glob = np.array(sorted(allg), dtype=float)
        self.lg = {k: np.array(sorted(v), dtype=float) for k, v in lg.items()}
        self.pa = {k: np.array(sorted(v), dtype=float) for k, v in pa.items()}
        self.tu = {k: np.array(sorted(v), dtype=float) for k, v in tu.items()}

    @staticmethod
    def _id(d, k):
        if k not in d:
            d[k] = len(d)
        return d[k]


def _before(arr, day):
    """Rows of a day-sorted array strictly before `day`."""
    if arr is None or not len(arr):
        return None
    i = int(np.searchsorted(arr[:, 0], day, side="left"))
    return arr[:i] if i else None


def _ew(prev, day, hl, num, den=None, prior=None, k=0.0):
    """Exponentially weighted mean of prev[:, num] (/ prev[:, den]) with a pseudo-count k at `prior`; nan if nothing."""
    if prev is None:
        return prior if (prior is not None and k > 0) else np.nan
    w = np.exp(-(day - prev[:, 0]) / hl)
    x = prev[:, num]
    if den is None:
        m = np.isfinite(x)
        sw, sx = (w[m]).sum(), (w[m] * x[m]).sum()
    else:
        y = prev[:, den]
        m = np.isfinite(x) & np.isfinite(y)
        sw, sx = (w[m] * y[m]).sum(), (w[m] * x[m]).sum()
    if prior is not None and k > 0:
        return (sx + k * prior) / (sw + k)
    return sx / sw if sw > 0 else np.nan


def _pace(arr, day, lo_days):
    """(combined kills per game, minutes per game, n) over [day - lo_days, day)."""
    p = _before(arr, day)
    if p is None:
        return np.nan, np.nan, 0
    p = p[p[:, 0] >= day - lo_days]
    if not len(p):
        return np.nan, np.nan, 0
    return float(p[:, 1].mean()), float(np.nanmean(p[:, 2])) if np.isfinite(p[:, 2]).any() else np.nan, len(p)


# ============================================================================================ strength
def league_elo(st):
    """lp-1's LeagueElo on every game of the history (day = match day; first row's side)."""
    seen, games = set(), []
    for r in st.rows:
        if r["game_key"] in seen or r.get("win") is None:
            continue
        seen.add(r["game_key"])
        games.append((r["_day"], r.get("game_number") or 0, r["team_id"], r["opponent_id"], int(r["win"]), r.get("league")))
    return lp1.LeagueElo(games), games


class PlainElo:
    """Full-history Elo (no window), K = 24, by team id; rating before `day`."""

    def __init__(self, games, k=24.0):
        rt, by_day = defaultdict(lambda: 1500.0), defaultdict(list)
        self.tl = defaultdict(list)
        for g in games:
            by_day[g[0]].append(g)
        for d in sorted(by_day):
            touched = set()
            for _, _, a, b, aw, _ in sorted(by_day[d], key=lambda g: g[1]):
                e = 1 / (1 + 10 ** ((rt[b] - rt[a]) / 400))
                rt[a] += k * (aw - e)
                rt[b] -= k * (aw - e)
                touched |= {a, b}
            for t in touched:
                self.tl[t].append((d, rt[t]))

    def rating(self, t, day):
        tl = self.tl.get(t)
        if not tl:
            return 1500.0
        i = bisect.bisect_left(tl, (day, -math.inf))
        return tl[i - 1][1] if i else 1500.0

    def p_win(self, a, b, day):
        return 1 / (1 + 10 ** ((self.rating(b, day) - self.rating(a, day)) / 400))


def logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


# ============================================================================================ features
class Features:
    """The pre-match feature vector of one target (series, player); every input from days < the match day."""

    def __init__(self, st, A=None, log=print):
        self.st = st
        self.A = A or Arrays(st)
        self.le, games = league_elo(st)
        self.pe = PlainElo(games)
        self._team = {}
        self._role_prior = None
        log(f"lpf features: {len(self.A.pl)} players, {len(self.A.tm)} teams")

    DEFAULT_PRIOR = {"kills": 3.0, "deaths": 3.0, "assists": 6.0}

    def role_prior(self, role, stat, day):
        """The role's per-game average over every game on days before `day` (running; never later games)."""
        if self._role_prior is None:
            acc = defaultdict(list)
            for r in self.st.rows:
                if r.get("role") and r.get("kills") is not None:
                    acc[r["role"]].append((r["_day"], r["kills"], r["deaths"], r["assists"]))
            self._role_prior = {}
            for ro, v in acc.items():
                a = np.array(sorted(v), dtype=float)
                self._role_prior[ro] = (a[:, 0], np.cumsum(a[:, 1:], axis=0))
        ent = self._role_prior.get(role)
        if ent is None:
            return self.DEFAULT_PRIOR[stat]
        days, cs = ent
        i = int(np.searchsorted(days, day, side="left"))
        if i < 50:
            return self.DEFAULT_PRIOR[stat]
        return float(cs[i - 1, J[stat]] / i)

    def team(self, t, day):
        key = (t, day)
        if key in self._team:
            return self._team[key]
        A = self.A
        prev = _before(A.tm.get(t), day)
        f = {}
        for hl in (30, 90):
            f[f"tk{hl}"] = _ew(prev, day, hl, 2, prior=17.0, k=2)
            f[f"ok{hl}"] = _ew(prev, day, hl, 3, prior=17.0, k=2)
            f[f"dur{hl}"] = _ew(prev, day, hl, 4, prior=31.5, k=2)
            f[f"wr{hl}"] = _ew(prev, day, hl, 5, prior=0.5, k=3)
        f["ckpm60"] = _ew(prev, day, 60, 2, 4) + _ew(prev, day, 60, 3, 4) if prev is not None else np.nan
        f["kd60"] = (_ew(prev, day, 60, 2) - _ew(prev, day, 60, 3)) if prev is not None else 0.0
        f["n"] = 0 if prev is None else len(prev)
        f["rest"] = np.nan if prev is None else min(60.0, day - prev[-1, 0])
        f["g7"] = 0 if prev is None else int((prev[:, 0] >= day - 7).sum())
        cont, lane = np.nan, {}
        if prev is not None:
            last = A.lineup.get((A.game_key[int(prev[-1, 6])], t)) or {}
            lane = last
            if len(prev) >= 4:
                old = set((A.lineup.get((A.game_key[int(prev[-4, 6])], t)) or {}).values())
                cont = len(set(last.values()) & old) / max(1, len(last))
        f["cont"] = cont
        self._team[key] = (f, lane)
        return self._team[key]

    def player(self, pid, day, role, team_id, league):
        A = self.A
        prev = _before(A.pl.get(pid), day)
        f = {}
        for s, c in (("k", 2), ("d", 3), ("a", 4)):
            pri = self.role_prior(role, {"k": "kills", "d": "deaths", "a": "assists"}[s], day)
            for hl in (20, 60, 180):
                f[f"{s}{hl}"] = _ew(prev, day, hl, c, prior=pri, k=2)
            f[f"{s}last5"] = float(prev[-5:, c].mean()) if prev is not None else np.nan
            f[f"{s}pm60"] = _ew(prev, day, 60, c, 8)
            if prev is not None:
                won = prev[prev[:, 7] == 1]
                lost = prev[prev[:, 7] == 0]
                f[f"{s}win"] = _ew(won if len(won) else None, day, 90, c, prior=pri * 1.3, k=3)
                f[f"{s}loss"] = _ew(lost if len(lost) else None, day, 90, c, prior=pri * 0.7, k=3)
            else:
                f[f"{s}win"] = f[f"{s}loss"] = np.nan
        f["kshare"] = _ew(prev, day, 60, 2, 5)
        f["ashare"] = _ew(prev, day, 60, 4, 5)
        f["dshare"] = _ew(prev, day, 60, 3, 6)
        if prev is not None:
            w = np.exp(-(day - prev[:, 0]) / 60)
            m = prev[:, 5] > 0
            f["kp"] = float((w[m] * (prev[m, 2] + prev[m, 4])).sum() / max(1e-9, (w[m] * prev[m, 5]).sum())) if m.any() else np.nan
        else:
            f["kp"] = np.nan
        f["dmgshare"] = _ew(prev, day, 60, 9, 10)
        f["goldshare"] = _ew(prev, day, 60, 12, 13)
        f["cspm"] = _ew(prev, day, 60, 11, 8)
        f["vspm"] = _ew(prev, day, 60, 14, 8)
        f["pwr"] = _ew(prev, day, 60, 7, prior=0.5, k=3)
        f["pn"] = 0 if prev is None else len(prev)
        f["pn30"] = 0 if prev is None else int((prev[:, 0] >= day - 30).sum())
        f["plast"] = np.nan if prev is None else min(120.0, day - prev[-1, 0])
        tix = A.tid.get(team_id, -1)
        lix = A.lid.get(league, -1)
        f["p_team_n"] = 0 if prev is None else int((prev[:, 15] == tix).sum())
        f["p_team_frac10"] = np.nan if prev is None else float((prev[-10:, 15] == tix).mean())
        f["p_league_frac10"] = np.nan if prev is None else float((prev[-10:, 16] == lix).mean())
        f["champ_pool"] = np.nan if prev is None else len(set(prev[-20:, 17].tolist())) / min(20, len(prev))
        return f

    def lane_opp(self, lane, role, day):
        pid = lane.get(role)
        prev = _before(self.A.pl.get(pid), day) if pid else None
        if prev is None:
            return {"lo_k": np.nan, "lo_d": np.nan, "lo_a": np.nan}
        return {"lo_k": _ew(prev, day, 60, 2, prior=self.role_prior(role, "kills", day), k=3),
                "lo_d": _ew(prev, day, 60, 3, prior=self.role_prior(role, "deaths", day), k=3),
                "lo_a": _ew(prev, day, 60, 4, prior=self.role_prior(role, "assists", day), k=3)}

    def context(self, t):
        A, d = self.A, t["day"]
        f = {}
        f["lg_ck60"], f["lg_dur60"], f["lg_n60"] = _pace(A.lg.get(t["league"]), d, 60)
        f["lg_ck365"], f["lg_dur365"], f["lg_n365"] = _pace(A.lg.get(t["league"]), d, 365)
        f["gl_ck30"], f["gl_dur30"], _ = _pace(A.glob, d, 30)
        f["pa_ck"], f["pa_dur"], f["pa_n"] = _pace(A.pa.get(t["patch"]), d, 400)
        pf = A.patch_first.get(t["patch"])
        f["pa_age"] = np.nan if pf is None or pf >= d else float(d - pf)
        f["ev_ck"], f["ev_dur"], f["ev_n"] = _pace(A.tu.get(t["tournament"]), d, 120)
        f["bo"] = t["best_of"]
        f["intl"] = float(bool(t["intl"]) or lp1.is_intl(t["league"]))
        f["primary"] = float(t["level"] == "Primary")
        f["playoffs"] = float(bool(t["lp_playoffs"]))
        f["role"] = ROLE_I.get(t["role"], -1)
        return f

    def target(self, t):
        d = t["day"]
        f = self.player(t["pid"], d, t["role"], t["team"], t["league"])
        tf, _ = self.team(t["team"], d)
        of, olane = self.team(t["opp"], d)
        f.update({"t_" + k: v for k, v in tf.items()})
        f.update({"o_" + k: v for k, v in of.items()})
        f.update(self.lane_opp(olane, t["role"], d))
        f.update(self.context(t))
        p_le = self.le.p_win(t["team"], t["opp"], d)
        dL, cross = self.le.d_league(t["team"], t["opp"], d)
        f["p_le"] = p_le
        f["dL"] = dL
        f["cross"] = float(cross)
        f["p_elo"] = self.pe.p_win(t["team"], t["opp"], d)
        f["kd_gap"] = (tf["kd60"] or 0.0) - (of["kd60"] or 0.0)
        return f


def matrix(feats, names=None):
    names = names or sorted({k for f in feats for k in f})
    X = np.array([[f.get(k, np.nan) for k in names] for f in feats], dtype=float)
    return X, names


# ============================================================================================ win model
class WinModel:
    """P(team wins a game) = logistic(a . [logit p_le, logit p_elo, kd_gap, cross * dL, intl]) fitted on game-1 results of
    earlier series (each series once, the first-listed team)."""

    COLS = ("p_le", "p_elo", "kd_gap", "dL", "cross")

    def __init__(self):
        self.coef = None
        self.p3coef = None

    def fit_p3(self, p, y3):
        """P(a best-of-3 reaches game 3) = logistic(a logit(2 p (1 - p)) + b) on earlier best-of-3 series - games of one
        series are not independent coin flips (2 p (1 - p) over-states deciders)."""
        from sklearn.linear_model import LogisticRegression
        x = logit(2 * np.asarray(p) * (1 - np.asarray(p)))
        m = LogisticRegression(C=1.0, max_iter=500).fit(x[:, None], y3)
        self.p3coef = np.array([m.coef_[0][0], m.intercept_[0]])
        return self

    def p3(self, p):
        p = np.asarray(p, dtype=float)
        if self.p3coef is None:
            return 2 * p * (1 - p)
        z = self.p3coef[0] * logit(2 * p * (1 - p)) + self.p3coef[1]
        return 1 / (1 + np.exp(-z))

    def design(self, F):
        return np.column_stack([logit(F["p_le"]), logit(F["p_elo"]), np.nan_to_num(F["kd_gap"]) / 10.0,
                                np.nan_to_num(F["dL"]) * F["cross"], np.ones(len(F["p_le"]))])

    def fit(self, F, y):
        from sklearn.linear_model import LogisticRegression
        X = self.design(F)
        m = LogisticRegression(C=1.0, fit_intercept=False, max_iter=500)
        m.fit(X, y)
        self.coef = m.coef_[0]
        return self

    def p(self, F):
        z = self.design(F) @ self.coef
        return 1 / (1 + np.exp(-z))


# ============================================================================================ distributions
def nb_logpmf(y, mu, size):
    from scipy.special import gammaln
    y, mu, size = (np.asarray(v, dtype=float) for v in (y, mu, size))
    mu = np.maximum(mu, 1e-6)
    return (gammaln(y + size) - gammaln(size) - gammaln(y + 1) + size * np.log(size / (size + mu)) + y * np.log(mu / (size + mu)))


def nb_cdf(k, mu, size):
    """P(X <= k) (k may be negative -> 0)."""
    from scipy.stats import nbinom
    mu, size = np.maximum(np.asarray(mu, dtype=float), 1e-6), np.asarray(size, dtype=float)
    p = size / (size + mu)
    return np.where(np.asarray(k) < 0, 0.0, nbinom.cdf(np.maximum(k, 0), size, p))


class Mix:
    """A per-row mixture of negative binomials: weights W (n x c), means M (n x c), sizes S (n x c)."""

    def __init__(self, W, M, S):
        self.W, self.M, self.S = (np.atleast_2d(np.asarray(v, dtype=float)) for v in (W, M, S))
        self.W = self.W / self.W.sum(1, keepdims=True)

    @classmethod
    def single(cls, mu, size):
        mu = np.asarray(mu, dtype=float)
        return cls(np.ones((len(mu), 1)), mu[:, None], np.broadcast_to(np.asarray(size, dtype=float), mu.shape)[:, None])

    def mean(self):
        return (self.W * self.M).sum(1)

    def logpmf(self, y):
        y = np.asarray(y, dtype=float)[:, None]
        lp = nb_logpmf(np.broadcast_to(y, self.M.shape), self.M, self.S) + np.log(np.maximum(self.W, 1e-300))
        mx = lp.max(1, keepdims=True)
        return (mx + np.log(np.exp(lp - mx).sum(1, keepdims=True)))[:, 0]

    def cdf(self, k):
        k = np.asarray(k, dtype=float)
        if k.ndim == 1:
            k = k[:, None]
        return (self.W * nb_cdf(np.floor(k), self.M, self.S)).sum(1)

    def p_over(self, line):
        """P(X > line) and P(X == line) (push, whole lines) per row."""
        line = np.asarray(line, dtype=float)
        under_eq = self.cdf(np.floor(line))
        whole = np.isclose(line, np.round(line))
        push = np.where(whole, under_eq - self.cdf(np.round(line) - 1), 0.0)
        return 1 - under_eq, push

    def pit(self, y, rng):
        y = np.asarray(y, dtype=float)
        lo = self.cdf(y - 1)
        hi = self.cdf(y)
        return lo + rng.random(len(y)) * (hi - lo)


def fit_size(mu, y, grid=None):
    """ML NB size for fixed means (grid, log-spaced)."""
    grid = grid if grid is not None else np.exp(np.linspace(math.log(0.5), math.log(400), 90))
    mu, y = np.asarray(mu, dtype=float), np.asarray(y, dtype=float)
    if len(y) < 30:
        return None
    sc = [float(nb_logpmf(y, mu, s).sum()) for s in grid]
    return float(grid[int(np.argmax(sc))])


def bo3_paths(p):
    """Maps 1-3 in a best-of-3: [(prob, games, wins)] - WW, LL (2 games), WLW/LWW, WLL/LWL (3 games)."""
    q = 1 - p
    return [(p * p, 2, 2), (q * q, 2, 0), (2 * p * q * p, 3, 2), (2 * p * q * q, 3, 1)]


# ============================================================================================ booster
LGB_PARAMS = dict(objective="poisson", learning_rate=0.03, num_leaves=31, min_child_samples=200, feature_fraction=0.7,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, verbose=-1, num_threads=8, seed=7,
                  deterministic=True, force_row_wise=True)
N_ROUNDS = 600


def fit_booster(X, y, names, rounds=N_ROUNDS, params=None, init=None):
    import lightgbm as lgb
    ds = lgb.Dataset(X, y, feature_name=list(names), categorical_feature=["role"] if "role" in names else "auto",
                     init_score=init, free_raw_data=False)
    return lgb.train(dict(params or LGB_PARAMS), ds, num_boost_round=rounds)


def no_live_import_guard():
    """Marker for the isolation test: this module is research only."""
    return VERSION
