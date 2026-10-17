"""LoL projection - ONE implementation shared by the backtest (lol/backtest.py) and the live scout (the perf.py rule).

Per game, for prop = kills / assists / deaths (the pool is the team's kills for kills and assists, the kills the team
concedes for deaths):
  form "rate":  lambda_game = player's per-game average (time-weighted, half-life HL days, shrunk toward his role's
                average by K0 games) x opponent factor (the opponent's kills conceded per game - kills scored per game
                for deaths - time-weighted, shrunk by KO games, over the league average)
  form "share": lambda_game = player's share of his team's pool (time-weighted ratio, shrunk toward his role's share
                by K0 games) x expected pool = average x team factor (his team's kills per game, shrunk by KT)
                x opponent factor (as above)
  match strength (when the fit keeps it, EW > 0): x ((p R + 1 - p) / (w R + 1 - w)) ^ EW, p = his team's win chance in
                this match (lol/elo.py), w = his team's time-weighted win rate in his past games (shrunk to 0.5 by K0),
                R = the league's per-game stat in wins / in losses (TRAIN) - more kills when he is more likely to win
                than usual, fewer deaths. EW = 0 turns it off.
  scope "1" = Map 1, "12" = Maps 1-2, "123" = Maps 1-3: lambda = lambda_game x games.
  Distribution: negative binomial, mean lambda, size R[(prop, scope)] (variance = lambda + lambda^2 / R) - LoL kills are
  overdispersed (a stomp or a loss moves every player's count together), measured on TRAIN.

Only games on days BEFORE the match day are used. Missing data stays missing: no history = the role prior, and the
result says so (data_quality). PRIORS / PARAMS / R come from lol/model_fit.json, written by the backtest's TRAIN fit
(python -m lol.backtest --write; results in lol/EVALUATION.md). Without that file the model refuses to project
(FIT is None) rather than run on numbers nobody measured.
"""
import json
import math

from . import config

VERSION = "lol-1"
SCOPE_GAMES = {"1": 1, "12": 2, "123": 3}
ROLES = ("top", "jungle", "mid", "bot", "support")
POOL = {"kills": "team_kills", "assists": "team_kills", "deaths": "opp_kills"}
FIT_FILE = config.PKG_DIR / "model_fit.json"


def load_fit(path=FIT_FILE):
    """(priors, params, R, fit metadata) from the backtest's saved TRAIN fit, or None when there is none."""
    try:
        fit = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    r = {tuple(k.split("|")): float(v) for k, v in (fit.get("nb_size") or {}).items()}
    return fit["priors"], fit["params"], r, {k: fit.get(k) for k in ("train", "fitted_at", "version")}


FIT = load_fit()
PRIORS, PARAMS, R, FIT_META = FIT if FIT else (None, None, None, None)


def _wsum(rows, day, hl, f):
    """Time-weighted sums over rows strictly before `day`: (sum w*a, sum w*b, sum w) for f(row) = (a, b)."""
    sa = sb = sw = 0.0
    for r in rows:
        if r[0] >= day:
            continue
        a, b = f(r)
        if a is None or b is None:
            continue
        w = math.exp(-(day - r[0]) / hl)
        sa += w * a
        sb += w * b
        sw += w
    return sa, sb, sw


def strength(p_win, w_bar, ratio, ew):
    """The match-strength factor (1.0 when off or unknown)."""
    if not ew or p_win is None or w_bar is None:
        return 1.0
    return ((p_win * ratio + 1 - p_win) / (w_bar * ratio + 1 - w_bar)) ** ew


def project(player_games, team_games, opp_games, role, day, scope="12", prop="kills", params=None, priors=None, r=None,
            p_win=None):
    """player_games: [(day, kills, deaths, assists, team_kills, opp_kills, win)]  - this player's games (win 1 / 0 / None)
    team_games:   [(day, team_kills, opp_kills)]                               - his team's games (any lineup)
    opp_games:    [(day, team_kills, opp_kills)]                               - the opponent's games, from ITS side
    day: the match day (date ordinal); p_win: his team's pre-match win chance (lol/elo.py), None = unknown.
    Returns the projection dict (lambda, sd, factors, data quality)."""
    if scope not in SCOPE_GAMES:
        raise ValueError(f"lol.model.project: scope {scope!r} not modelled")
    if (params or PARAMS) is None:
        raise RuntimeError("lol.model: no fitted parameters (run python -m lol.backtest --write)")
    p = (params or PARAMS)[prop]
    pri = priors or PRIORS
    rsize = (r or R)[(prop, scope)]
    tk = pri["team_kills"]
    role_mean = pri[prop].get(role)
    if role_mean is None:
        role_mean = sum(pri[prop].values()) / len(pri[prop])
    idx = {"kills": 1, "deaths": 2, "assists": 3}[prop]
    pool_i = 4 if POOL[prop] == "team_kills" else 5
    hl = p["hl"]
    # opponent factor: kills it concedes (kills / assists) or scores (deaths), per game, over the league average
    oc, _, ow = _wsum(opp_games, day, hl, (lambda g: (g[2], 0.0)) if POOL[prop] == "team_kills" else (lambda g: (g[1], 0.0)))
    opp = ((oc + p["ko"] * tk) / (ow + p["ko"])) / tk
    if p["form"] == "share":
        x, pool, w = _wsum(player_games, day, hl, lambda g: (g[idx], g[pool_i]))
        share = (x + p["k0"] * role_mean) / (pool + p["k0"] * tk)
        tc, _, tw = _wsum(team_games, day, hl, (lambda g: (g[1], 0.0)) if POOL[prop] == "team_kills" else (lambda g: (g[2], 0.0)))
        team = ((tc + p["kt"] * tk) / (tw + p["kt"])) / tk
        lam_game = share * tk * team * opp
        rate = share * tk
    else:
        x, _, w = _wsum(player_games, day, hl, lambda g: (g[idx], 0.0))
        rate = (x + p["k0"] * role_mean) / (w + p["k0"])
        team = None
        lam_game = rate * opp
    ew = p.get("ew") or 0.0
    w_bar = None
    if ew:
        ws, _, wk = _wsum([g for g in player_games if len(g) > 6 and g[6] is not None], day, hl, lambda g: (g[6], 0.0))
        w_bar = (ws + p["k0"] * 0.5) / (wk + p["k0"])
    sf = strength(p_win, w_bar, (pri.get("win_ratio") or {}).get(prop, 1.0), ew)
    lam_game *= sf
    n = SCOPE_GAMES[scope]
    lam = lam_game * n
    sd = math.sqrt(lam + lam * lam / rsize)
    games_seen = sum(1 for g in player_games if g[0] < day)
    return {"version": VERSION, "prop": prop, "scope": scope, "games": n, "lambda": lam, "lambda_game": lam_game,
            "sd": sd, "nb_size": rsize, "player_rate": rate, "team_factor": team, "opp_factor": opp, "form": p["form"],
            "strength_factor": sf, "p_win": p_win, "team_win_rate": w_bar,
            "role": role, "role_prior": role_mean, "history_weight": w, "history_games": games_seen,
            "opp_weight": ow, "data_quality": "no history - role average only" if games_seen == 0 else
            ("thin history" if games_seen < 10 else "ok")}


# ------------------------------------------------------------------ negative binomial
def nb_pmf(k, mu, size):
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(math.lgamma(k + size) - math.lgamma(size) - math.lgamma(k + 1)
                    + size * math.log(size / (size + mu)) + k * math.log(mu / (size + mu)))


def nb_logpmf(k, mu, size):
    if mu <= 0:
        return 0.0 if k == 0 else -math.inf
    return (math.lgamma(k + size) - math.lgamma(size) - math.lgamma(k + 1)
            + size * math.log(size / (size + mu)) + k * math.log(mu / (size + mu)))


def probs(lam, size, line):
    """(over, under, push) for a line: over = more than the line, under = fewer, push = exactly the line (whole lines)."""
    top = int(lam + 12 * math.sqrt(lam + lam * lam / size)) + 10
    over = under = push = 0.0
    for k in range(0, max(top, int(line) + 2)):
        pk = nb_pmf(k, lam, size)
        if k > line:
            over += pk
        elif k < line:
            under += pk
        else:
            push += pk
    tot = over + under + push
    return over / tot, under / tot, push / tot


def interval(lam, size, cover=0.8):
    """Central interval [lo, hi] holding at least `cover` of the mass."""
    lo_q, hi_q = (1 - cover) / 2, 1 - (1 - cover) / 2
    c, k, lo = 0.0, 0, None
    while True:
        c += nb_pmf(k, lam, size)
        if lo is None and c >= lo_q:
            lo = k
        if c >= hi_q or k > 10000:
            return lo, k
        k += 1
