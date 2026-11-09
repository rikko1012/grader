"""lp-1 - LoL PROJECTION candidates (shadow / research only; nothing here is imported by the live forecast).

Owner, 2026-11-05: "dont ruin calibration simply fix projections". The settled Worlds 2026 record (1,795 graded props) states
63.8% and hits 57.2%; the diagnosis (lol/EVALUATION.md, "lp-1") traced most of the gap to one
missing INPUT, not to the chance arithmetic: lol/elo.py starts every team at 1500 with no league term, so at an international
event a player's domestic rate is carried across regions unchanged (non-LCK/LPL players vs LCK/LPL opponents ran 0.68-0.70 of
their projection; LCK/LPL players vs minor regions 1.16-1.19).

The candidates here change the PROJECTED DISTRIBUTION only (the mean, the NB size); the chance is then read off the same
negative binomial as live. There is no multiplier on chances, no forced Over/Under balance, no intercept on sides, no stake.

  LE   league-aware Elo: every team has a rating, every home league an offset that moves ONLY on cross-league games (K_L),
       p_win = Elo(rating + home-league offset). Home league = the team's most frequent domestic (non-international) league
       in the HOME_WINDOW days before the match day. p_win enters the live match-strength factor in place of the plain Elo.
  G    league gap on the projected mean: lambda x exp(g x dL), dL = (offset[home] - offset[opponent's home]) / 400; g refit for
       every test day on cross-league history targets of the GAP_WINDOW days before it (Poisson score, grid G_GRID).
  MKT  where a pre-match Polymarket game price was saved with the forecast (inputs.team_win.market.p), that price is p_win
       instead of LE (fallback LE).
  DISP the NB size per (stat, scope) refit for every test day on cross-league history series totals of the GAP_WINDOW days
       before it, with the candidate mean (the live sizes are the Apr-Aug all-league TRAIN fit).
  TEMPO event pace on top of LE+G: lambda x (A + K m) / (E + K m) over the same international event's earlier days (A actual,
       E the LE+G expectation, m = E / n), K chosen for every test day from KS on earlier international targets.
  SHR  cross-region transfer shrinkage on top of LE+G (cross-league games only): lambda_game^a x role prior^(1 - a), a refit
       for every test day on cross-league history targets before it.

Every input is known before the match: games strictly before the match day; parameters fitted on days strictly before the
test day. Pure functions + numpy (the harness lol/lp1_eval.py adds the data loading and scoring).
"""
import bisect
import math
from collections import Counter, defaultdict

import numpy as np

VERSION = "lp-1"
K_TEAM = 24.0            # same K as lol/elo.py
K_LEAGUE = 12.0          # league-offset K (robust 6 / 12 / 20 in the diagnosis)
HOME_WINDOW = 180
GAP_WINDOW = 365
MIN_FIT = 200            # fewest cross-league targets for a g / size fit (else no change)
G_GRID = np.round(np.arange(-1.5, 1.5001, 0.05), 2)
SIZE_GRID = np.exp(np.linspace(math.log(0.5), math.log(500), 120))
A_GRID = np.round(np.arange(0.5, 1.2001, 0.05), 2)   # SHR exponent grid (1 = no shrinkage)
KS = (10.0,25.0, 50.0, 100.0, 200.0, 400.0, math.inf)   # tempo pseudo-count in player-games; inf = no tempo
SCOPE_GAMES = {"1": 1, "12": 2, "123": 3}

INTL = frozenset({
    "World Championship", "Mid-Season Invitational", "Esports World Cup", "First Stand", "Esports Nations Cup",
    "Asia Masters", "Asia Masters Elite Invitational Tournament", "Asian Games 2018", "Demacia Cup Global Invitational",
    "World Star Challengers Invitational", "EMEA Masters", "Americas Cup", "Academic Esports World Tournament Sydney",
    "University Esports Masters", "KeSPA Cup", "Equal Esports Cup", "KOREA Pre Evaluation for Asian Games", "Demacia Cup"})


def is_intl(league):
    return league in INTL


class LeagueElo:
    """Team rating + home-league offset, game by game in day order. Ratings for day d = after every game on days < d."""

    def __init__(self, games, k=K_TEAM, kl=K_LEAGUE, home_window=HOME_WINDOW):
        """games: iterable of (day ordinal, game order, team_a, team_b, a_won 0/1, league)."""
        self.k, self.kl, self.window = float(k), float(kl), int(home_window)
        games = [g for g in games if g[4] is not None]
        dom = defaultdict(list)
        for d, _, a, b, _, lg in games:
            if lg and lg not in INTL:
                dom[a].append((d, lg))
                dom[b].append((d, lg))
        self._dom = {t: sorted(v) for t, v in dom.items()}
        self._dom_days = {t: [x[0] for x in v] for t, v in self._dom.items()}
        self._home = {}
        self.rt_tl = defaultdict(list)       # team -> [(day, rating after that day)]
        self.lo_tl = defaultdict(list)       # league -> [(day, offset after that day)]
        rt, lo = defaultdict(lambda: 1500.0), defaultdict(float)
        by_day = defaultdict(list)
        for g in games:
            by_day[g[0]].append(g)
        for d in sorted(by_day):
            touched_t, touched_l = set(), set()
            for _, _, a, b, aw, _ in sorted(by_day[d], key=lambda g: g[1]):
                ha, hb = self.home(a, d), self.home(b, d)
                la = lo[ha] if ha else 0.0
                lb = lo[hb] if hb else 0.0
                e = 1 / (1 + 10 ** (((rt[b] + lb) - (rt[a] + la)) / 400))
                rt[a] += self.k * (aw - e)
                rt[b] -= self.k * (aw - e)
                touched_t |= {a, b}
                if ha and hb and ha != hb:
                    lo[ha] += self.kl * (aw - e)
                    lo[hb] -= self.kl * (aw - e)
                    touched_l |= {ha, hb}
            for t in touched_t:
                self.rt_tl[t].append((d, rt[t]))
            for lg in touched_l:
                self.lo_tl[lg].append((d, lo[lg]))

    def home(self, team, day):
        """Most frequent domestic league of the team in [day - window, day) (None = no domestic game)."""
        key = (team, day)
        if key not in self._home:
            days = self._dom_days.get(team)
            h = None
            if days:
                i, j = bisect.bisect_left(days, day - self.window), bisect.bisect_left(days, day)
                if j > i:
                    c = Counter(lg for _, lg in self._dom[team][i:j])
                    h = max(c.items(), key=lambda x: (x[1], x[0]))[0]
            self._home[key] = h
        return self._home[key]

    @staticmethod
    def _before(tl, day, default):
        if not tl:
            return default
        i = bisect.bisect_left(tl, (day, -math.inf))
        return tl[i - 1][1] if i else default

    def rating(self, team, day):
        return self._before(self.rt_tl.get(team), day, 1500.0)

    def offset(self, league, day):
        return self._before(self.lo_tl.get(league), day, 0.0) if league else 0.0

    def p_win(self, team, opp, day):
        ra = self.rating(team, day) + self.offset(self.home(team, day), day)
        rb = self.rating(opp, day) + self.offset(self.home(opp, day), day)
        return 1 / (1 + 10 ** ((rb - ra) / 400))

    def d_league(self, team, opp, day):
        """(dL, cross): the league-offset gap in Elo/400 units, and whether the two home leagues differ (both known)."""
        ha, hb = self.home(team, day), self.home(opp, day)
        if not ha or not hb:
            return 0.0, False
        return (self.offset(ha, day) - self.offset(hb, day)) / 400.0, ha != hb


def fit_gap(x, lam, y, grid=G_GRID):
    """g maximising the Poisson score sum(y g x - lam e^{g x}) (the NB mean score has the same root for a fixed size)."""
    x, lam, y = (np.asarray(v, dtype=float) for v in (x, lam, y))
    sc = (y[None, :] * grid[:, None] * x[None, :] - lam[None, :] * np.exp(grid[:, None] * x[None, :])).sum(1)
    return float(grid[int(np.argmax(sc))])


class DayFits:
    """Per test day, parameters fitted only on history targets of earlier days (cached per day)."""

    def __init__(self, day, cross, x, lam, y, window=GAP_WINDOW, min_n=MIN_FIT):
        self.day, self.cross = np.asarray(day), np.asarray(cross, dtype=bool)
        self.x, self.lam, self.y = (np.asarray(v, dtype=float) for v in (x, lam, y))
        self.window, self.min_n = window, min_n
        self._g = {}

    def mask(self, d):
        return self.cross & (self.day < d) & (self.day >= d - self.window) & np.isfinite(self.lam) & (self.lam > 0)

    def g(self, d):
        if d not in self._g:
            m = self.mask(d)
            self._g[d] = fit_gap(self.x[m], self.lam[m], self.y[m]) if m.sum() >= self.min_n else 0.0
        return self._g[d]


def sf_ratio(p_old, sf_old, p_new, win_ratio, ew):
    """New / old live match-strength factor, from the saved (p_win, strength_factor): the factor is
    ((p R + 1 - p) / D) ^ ew with D the player's own win-rate term, so D is recovered exactly from the saved pair."""
    if not ew or p_old is None or p_new is None or not sf_old:
        return 1.0
    d = (p_old * win_ratio + 1 - p_old) / sf_old ** (1 / ew)
    return ((p_new * win_ratio + 1 - p_new) / d) ** ew / sf_old


def stomp_ratio(p_old, p_new, beta, sd):
    """lol-1f's script_stomp feature is 4 (p_win - 0.5)^2: the factor change when p_win moves (exp(beta z) model)."""
    if not beta or not sd or p_old is None or p_new is None:
        return 1.0
    return math.exp(beta / sd * (4 * (p_new - 0.5) ** 2 - 4 * (p_old - 0.5) ** 2))


def nb_ll(y, mu, size):
    """Vectorised NB log pmf."""
    from scipy.special import gammaln
    y, mu = np.asarray(y, dtype=float), np.asarray(mu, dtype=float)
    return (gammaln(y + size) - gammaln(size) - gammaln(y + 1) + size * np.log(size / (size + mu))
            + y * np.log(mu / (size + mu)))


def fit_size(mu, y, grid=SIZE_GRID):
    mu, y = np.asarray(mu, dtype=float), np.asarray(y, dtype=float)
    best = max(grid, key=lambda s: float(nb_ll(y, mu, s).sum()))
    return float(best)


def shrink(lam_game, prior_game, a):
    """SHR: cross-region transfer shrinkage of the per-game mean, lam^a x prior^(1 - a) (log-linear pooling toward the
    role's TRAIN per-game mean). a = 1 is no change; a < 1 = the player's domestic level carries only partly abroad."""
    return np.asarray(lam_game, dtype=float) ** a * np.asarray(prior_game, dtype=float) ** (1 - a)


def fit_shrink(lam_game, prior_game, y, size, grid=A_GRID):
    """a maximising the game-1 NB log score of shrink(lam, prior, a)."""
    lam_game, prior_game, y = (np.asarray(v, dtype=float) for v in (lam_game, prior_game, y))
    return float(max(grid, key=lambda a: float(nb_ll(y, shrink(lam_game, prior_game, a), size).sum())))


def tempo_factor(a, e, n, k):
    """(A + K m) / (E + K m), m = E / n; 1 with no earlier game or K = inf."""
    if not n or e <= 0 or math.isinf(k):
        return 1.0
    m = e / n
    return (a + k * m) / (e + k * m)


class EventSums:
    """Running (actual, expected, n) per international event over its earlier days, from history targets (game 1)."""

    def __init__(self, event, day, y, mu):
        self.by = defaultdict(list)
        for ev, d, a, m in zip(event, day, y, mu):
            if ev and np.isfinite(m) and m > 0:
                self.by[ev].append((int(d), float(a), float(m)))
        self.cum = {}
        for ev, v in self.by.items():
            v.sort()
            days = [x[0] for x in v]
            ca, ce = np.cumsum([x[1] for x in v]), np.cumsum([x[2] for x in v])
            self.cum[ev] = (days, ca, ce)

    def sums(self, ev, d):
        if ev not in self.cum:
            return 0.0, 0.0, 0
        days, ca, ce = self.cum[ev]
        i = bisect.bisect_left(days, d)
        return (float(ca[i - 1]), float(ce[i - 1]), i) if i else (0.0, 0.0, 0)


def event_key(league, day):
    """International event = league name + the season year of its match day (Worlds spans Sep-Nov of one year)."""
    import datetime as dt
    return f"{league}|{dt.date.fromordinal(int(day)).year}" if is_intl(league) else None
