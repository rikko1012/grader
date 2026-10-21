"""
lol-2: whole-match simulation (lol/EVALUATION.md amendment A4) - both teams together, one game at a time, summed over the
games of the market's scope. Uses only games that ended on days before the match day (the same rule as lol-1 / B0).

Per simulated game between team A and team B:
  duration D            log D ~ Normal(mu_D, sd_D); mu_D from both teams' recent game lengths and how close the match
                        is (|p_win - 0.5|) - fitted, not assumed
  combined kills K      NegBin(mean = exp(b0 + b1 log D + b2 log pace), size sK); pace = both teams' recent kills for +
                        against per minute (combined kill pace)
  winner                Bernoulli(p_win) from the history's Elo (pre-match)
  kill allocation       the winner's share of K ~ Beta(mean m(D), precision phiW); m(D) = logistic(c0 + c1 log D) -
                        fitted (a short game is usually a lopsided one)
  players               the team's kills are split across its five players by a Dirichlet-multinomial on their recent
                        kill shares (shrunk toward the role) - player kills always sum to team kills
  assists               each player assists on a share of the team's OTHER kills: Binomial(team kills - own kills, q),
                        q ~ Beta around the player's recent participation (shrunk toward the role) - not an exclusive
                        share
  deaths                the team's deaths = opposing kills + executions (Poisson, fitted); split across the five by a
                        Dirichlet-multinomial on recent death shares
  CS                    recent CS per minute (shrunk toward the role) x D x lognormal noise (fitted)
  fantasy points        3 K + 2 A - D + 0.02 CS (Underdog's published LoL scoring) from the same simulated components
The lineup is the team's last fielded lineup before the match (the posted player in their role's slot); nothing from the
match itself (duration, draft, sides) is an input. Probabilities come straight from the simulated scope totals.
"""
import math
from collections import defaultdict

import numpy as np

ROLES = ("top", "jungle", "mid", "bot", "support")
VERSION = "lol-2"


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


class Decayed:
    """Exponentially time-weighted sums (half-life hl days), decayed lazily to the day asked."""
    __slots__ = ("day", "v")

    def __init__(self, n):
        self.day, self.v = None, np.zeros(n)

    def at(self, day, hl):
        if self.day is None:
            return self.v * 0
        return self.v * math.exp(-(day - self.day) * math.log(2) / hl)

    def add(self, day, x, hl):
        self.v = self.at(day, hl) + np.asarray(x, dtype=float) if self.day is not None else np.asarray(x, dtype=float)
        self.day = day


# player sums: [w, kills, team_kills, assists, team_kills - kills, deaths, team_deaths, cs, minutes]
P_N = 9
# team sums: [w, kills_for, kills_against, minutes, log_duration_sum]
T_N = 5


class State:
    """Pre-match state built game by game (day cutoff: only games on earlier days are visible)."""

    def __init__(self, hl):
        self.hl = hl
        self.p = defaultdict(lambda: Decayed(P_N))
        self.t = defaultdict(lambda: Decayed(T_N))
        self.role = defaultdict(lambda: defaultdict(int))
        self.lineup = {}                      # team -> {role: pid} last fielded lineup
        self.lineup_day = {}

    def add_game(self, day, game):
        dur = game["minutes"]
        for side in ("a", "b"):
            tm, opp = game[side], game["b" if side == "a" else "a"]
            kf, ka = game["kills_" + side], game["kills_" + ("b" if side == "a" else "a")]
            self.t[tm].add(day, [1, kf, ka, dur, math.log(max(dur, 1))], self.hl)
            tdeaths = sum(p["deaths"] for p in game["players"] if p["team"] == tm)
            lu = {}
            for p in game["players"]:
                if p["team"] != tm:
                    continue
                self.p[p["pid"]].add(day, [1, p["kills"], kf, p["assists"], kf - p["kills"], p["deaths"], tdeaths,
                                           p["cs"] if p["cs"] is not None else 0.0, dur if p["cs"] is not None else 0.0], self.hl)
                self.role[p["pid"]][p["role"]] += 1
                lu[p["role"]] = p["pid"]
            if len(lu) == 5:
                self.lineup[tm], self.lineup_day[tm] = lu, day


def games_from_rows(rows):
    """History rows (lol_player_games dicts) -> chronological games with both teams, players, kills, duration."""
    by = defaultdict(list)
    for r in rows:
        by[r["game_key"]].append(r)
    out = []
    for gk, rs in by.items():
        teams = sorted({r["team_id"] for r in rs if r.get("team_id")})
        if len(teams) != 2 or len(rs) != 10 or any(r.get("role") not in ROLES or r.get("kills") is None for r in rs):
            continue
        dur = rs[0].get("duration_s")
        if not dur:
            continue
        a, b = teams
        ka = next(r["team_kills"] for r in rs if r["team_id"] == a)
        kb = next(r["team_kills"] for r in rs if r["team_id"] == b)
        win_a = next((r["win"] for r in rs if r["team_id"] == a and r.get("win") is not None), None)
        out.append({"game_key": gk, "match_key": rs[0]["match_key"], "n": rs[0].get("game_number"), "best_of": rs[0].get("best_of"),
                    "played_at": min(r["played_at"] for r in rs), "league": rs[0].get("league"), "a": a, "b": b,
                    "kills_a": ka, "kills_b": kb, "win_a": win_a, "minutes": dur / 60.0,
                    "players": [{"pid": r["player_id"], "team": r["team_id"], "role": r["role"], "kills": r["kills"],
                                 "deaths": r["deaths"] or 0, "assists": r["assists"] or 0, "cs": r.get("cs")} for r in rs]})
    return out


# ------------------------------------------------------------------ priors and features
def role_priors(games):
    """Per role: kill share, assist participation (of the team's other kills), death share, CS per minute."""
    s = defaultdict(lambda: np.zeros(6))
    for g in games:
        for p in g["players"]:
            tk = g["kills_a"] if p["team"] == g["a"] else g["kills_b"]
            td = sum(q["deaths"] for q in g["players"] if q["team"] == p["team"])
            s[p["role"]] += [p["kills"], tk, p["assists"], tk - p["kills"], p["deaths"], td]
    out = {}
    for r in ROLES:
        k, tk, a, ok, d, td = s[r]
        out[r] = {"kill_share": k / max(tk, 1), "participation": a / max(ok, 1), "death_share": d / max(td, 1)}
    cs = defaultdict(lambda: [0.0, 0.0])
    for g in games:
        for p in g["players"]:
            if p["cs"] is not None:
                cs[p["role"]][0] += p["cs"]
                cs[p["role"]][1] += g["minutes"]
    for r in ROLES:
        out[r]["cspm"] = cs[r][0] / max(cs[r][1], 1)
    allk = [g["kills_a"] + g["kills_b"] for g in games]
    allm = [g["minutes"] for g in games]
    out["_pace"] = sum(allk) / max(sum(allm), 1)                  # combined kills per minute
    out["_logdur"] = float(np.mean([math.log(m) for m in allm])) if allm else math.log(32)
    out["_executions"] = float(np.mean([sum(p["deaths"] for p in g["players"]) - (g["kills_a"] + g["kills_b"]) for g in games])) / 2 if games else 0.1
    return out


def team_feats(st, tid, day, pri, k_team):
    v = st.t[tid].at(day, st.hl) if tid in st.t else np.zeros(T_N)
    w, kf, ka, mins, ld = v
    pace = (kf + ka + k_team * pri["_pace"] * pri_minutes(pri)) / (mins + k_team * pri_minutes(pri))
    logdur = (ld + k_team * pri["_logdur"]) / (w + k_team)
    return {"w": w, "pace": pace, "logdur": logdur}


def pri_minutes(pri):
    return math.exp(pri["_logdur"])


def player_feats(st, pid, role, day, pri, k0):
    v = st.p[pid].at(day, st.hl) if pid in st.p else np.zeros(P_N)
    w, k, tk, a, ok, d, td, cs, mins = v
    r = pri[role]
    tk_prior = 7.0                                                    # pseudo team kills per prior game (scale only)
    return {"w": w,
            "kill_share": (k + k0 * tk_prior * r["kill_share"]) / (tk + k0 * tk_prior),
            "participation": (a + k0 * tk_prior * r["participation"]) / (ok + k0 * tk_prior),
            "death_share": (d + k0 * tk_prior * r["death_share"]) / (td + k0 * tk_prior),
            "cspm": (cs + k0 * 30.0 * r["cspm"]) / (mins + k0 * 30.0)}


# ------------------------------------------------------------------ the fitted components
def fit(train_rows, hl=120.0, k0=8.0, k_team=8.0, elo_fn=None, log=lambda m: None, fit_from=None):
    """Fit every component on the training games (features from earlier days only). Games before `fit_from` (a day
    ordinal) only build the pre-match state; the regressions and priors use games from `fit_from` on - the same fit
    period as B0 / lol-1. Each game counts on its series' day (the match day), like the backtest. -> params dict."""
    allg = games_from_rows(train_rows)
    sday = {}
    for g in allg:
        d = _day(g["played_at"])
        sday[g["match_key"]] = min(sday.get(g["match_key"], d), d)
    games = sorted(allg, key=lambda g: (sday[g["match_key"]], g["played_at"]))
    pri = role_priors([g for g in games if fit_from is None or sday[g["match_key"]] >= fit_from])
    st = State(hl)
    X_d, y_d, X_k, y_k, lnd_k, share_rows = [], [], [], [], [], []
    dm_rows = {"kills": [], "deaths": []}
    part_rows = []
    cs_res = []
    by_day = defaultdict(list)
    for g in games:
        by_day[sday[g["match_key"]]].append(g)
    for day in sorted(by_day):
        fitting = fit_from is None or day >= fit_from
        for g in by_day[day] if fitting else ():
            fa, fb = team_feats(st, g["a"], day, pri, k_team), team_feats(st, g["b"], day, pri, k_team)
            p = elo_fn(g["a"], g["b"], day) if elo_fn else 0.5
            close = abs(p - 0.5)
            X_d.append([1.0, (fa["logdur"] + fb["logdur"]) / 2 - pri["_logdur"], close])
            y_d.append(math.log(g["minutes"]))
            X_k.append([1.0, math.log(g["minutes"]), math.log((fa["pace"] + fb["pace"]) / 2 / pri["_pace"])])
            y_k.append(g["kills_a"] + g["kills_b"])
            if g["win_a"] is not None and g["kills_a"] + g["kills_b"] > 0:
                wk = g["kills_a"] if g["win_a"] else g["kills_b"]
                share_rows.append((math.log(g["minutes"]), wk / (g["kills_a"] + g["kills_b"]), g["kills_a"] + g["kills_b"]))
            for side in ("a", "b"):
                tm = g[side]
                tk = g["kills_" + side]
                pl = [q for q in g["players"] if q["team"] == tm]
                pf = {q["pid"]: player_feats(st, q["pid"], q["role"], day, pri, k0) for q in pl}
                ks = np.array([pf[q["pid"]]["kill_share"] for q in pl])
                ds = np.array([pf[q["pid"]]["death_share"] for q in pl])
                if tk > 0:
                    dm_rows["kills"].append((ks / ks.sum(), np.array([q["kills"] for q in pl]), tk))
                td = sum(q["deaths"] for q in pl)
                if td > 0:
                    dm_rows["deaths"].append((ds / ds.sum(), np.array([q["deaths"] for q in pl]), td))
                for q in pl:
                    other = tk - q["kills"]
                    if other > 0:
                        part_rows.append((pf[q["pid"]]["participation"], q["assists"], other))
                    if q["cs"] is not None and pf[q["pid"]]["cspm"] > 0:
                        cs_res.append(math.log(max(q["cs"], 1) / (pf[q["pid"]]["cspm"] * g["minutes"])))
        for g in by_day[day]:
            st.add_game(day, g)
    Xd, yd = np.array(X_d), np.array(y_d)
    bd = np.linalg.lstsq(Xd, yd, rcond=None)[0]
    sd_d = float(np.std(yd - Xd @ bd))
    Xk, yk = np.array(X_k), np.array(y_k, dtype=float)
    bk = _poisson_irls(Xk, yk)
    mu_k = np.exp(Xk @ bk)
    size_k = _nb_size(yk, mu_k)
    # winner's kill share: logistic mean on log D (least squares on the logit), Beta precision by moments
    if len(share_rows) < 50:
        raise ValueError(f"lol-2 needs games with a known winner to fit the kill split ({len(share_rows)} found)")
    L = np.array([[1.0, r[0]] for r in share_rows])
    s = np.clip(np.array([r[1] for r in share_rows]), 0.02, 0.98)
    bw = np.linalg.lstsq(L, np.log(s / (1 - s)), rcond=None)[0]
    m = 1 / (1 + np.exp(-(L @ bw)))
    n = np.array([r[2] for r in share_rows], dtype=float)
    # Var(share) = m(1-m)/(phi+1) + binomial part m(1-m)/n (the share of a finite count) -> solve for phi
    extra = np.mean((s - m) ** 2 - m * (1 - m) / n)
    phi_w = float(max(np.mean(m * (1 - m)) / max(extra, 1e-4) - 1, 2.0))
    alpha = {k: _dm_alpha(v) for k, v in dm_rows.items()}
    q = np.array([r[0] for r in part_rows])
    obs = np.array([r[1] / r[2] for r in part_rows])
    nn = np.array([r[2] for r in part_rows], dtype=float)
    extra_a = np.mean((obs - q) ** 2 - q * (1 - q) / nn)
    phi_a = float(max(np.mean(q * (1 - q)) / max(extra_a, 1e-4) - 1, 2.0))
    params = {"version": VERSION, "hl": hl, "k0": k0, "k_team": k_team, "priors": pri, "fit_from": fit_from,
              "duration": {"coef": bd.tolist(), "sd": sd_d}, "kills": {"coef": bk.tolist(), "size": size_k},
              "winner_share": {"coef": bw.tolist(), "phi": phi_w}, "alloc_alpha": alpha, "participation_phi": phi_a,
              "executions": max(pri["_executions"], 0.0), "cs_sd": float(np.std(cs_res)) if cs_res else 0.25,
              "cs_bias": float(np.mean(cs_res)) if cs_res else 0.0, "n_games": len(games)}
    log(f"lol-2 fit on {len(games)} games: duration sd {sd_d:.3f}, kills size {size_k:.1f}, winner phi {phi_w:.1f}, "
        f"alloc alpha {alpha}, participation phi {phi_a:.1f}")
    return params


def _day(iso):
    from datetime import datetime, timezone
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).astimezone(timezone.utc).date().toordinal()


def _poisson_irls(X, y, iters=50):
    b = np.zeros(X.shape[1])
    b[0] = math.log(max(y.mean(), 1e-3))
    for _ in range(iters):
        eta = np.clip(X @ b, -20, 20)
        mu = np.exp(eta)
        z = eta + (y - mu) / mu
        nb = np.linalg.solve(X.T @ (mu[:, None] * X) + 1e-6 * np.eye(X.shape[1]), X.T @ (mu * z))
        if np.max(np.abs(nb - b)) < 1e-9:
            return nb
        b = nb
    return b


def _nb_size(y, mu):
    from . import model
    best = None
    for s in np.exp(np.linspace(math.log(1), math.log(1000), 100)):
        ll = sum(model.nb_logpmf(int(k), m, s) for k, m in zip(y, mu))
        if best is None or ll > best[0]:
            best = (ll, float(s))
    return best[1]


def _dm_alpha(rows):
    """Dirichlet-multinomial concentration by moments: Var(x_i) = n p(1-p) (n + a) / (1 + a)."""
    num = den = 0.0
    for p, x, n in rows:
        if n < 2:
            continue
        num += float(((x - n * p) ** 2).sum())
        den += float((n * p * (1 - p)).sum())
    ratio = num / max(den, 1e-9)                                      # = (n + a) / (1 + a), averaged
    nbar = np.mean([r[2] for r in rows]) if rows else 10
    if ratio <= 1.0001:
        return 1000.0
    return float(max((nbar - ratio) / (ratio - 1), 0.5))


# ------------------------------------------------------------------ simulation
def simulate(params, ctx, n_games, sims=4000, seed=None):
    """ctx: {"p_win": P(team A wins a game), "team_a": feats, "team_b": feats, "lineup_a": [pfeat x5 with role/pid],
    "lineup_b": [...]} -> dict of arrays (sims,) per (pid, stat) for every player of both lineups, summed over n_games."""
    rng = np.random.default_rng(seed)
    pri = params["priors"]
    bd, sd_d = np.array(params["duration"]["coef"]), params["duration"]["sd"]
    bk, size_k = np.array(params["kills"]["coef"]), params["kills"]["size"]
    bw, phi_w = np.array(params["winner_share"]["coef"]), params["winner_share"]["phi"]
    ak, ad = params["alloc_alpha"]["kills"], params["alloc_alpha"]["deaths"]
    phi_a, ex, cs_sd, cs_bias = params["participation_phi"], params["executions"], params["cs_sd"], params["cs_bias"]
    fa, fb, p = ctx["team_a"], ctx["team_b"], ctx["p_win"]
    mu_d = bd @ np.array([1.0, (fa["logdur"] + fb["logdur"]) / 2 - pri["_logdur"], abs(p - 0.5)])
    pace = math.log((fa["pace"] + fb["pace"]) / 2 / pri["_pace"])
    lus = {"a": ctx["lineup_a"], "b": ctx["lineup_b"]}
    tot = {(q["pid"], s): np.zeros(sims) for side in lus for q in lus[side] for s in ("kills", "assists", "deaths", "cs", "fantasy")}
    for _ in range(n_games):
        D = np.exp(rng.normal(mu_d, sd_d, sims))
        mean_k = np.exp(bk[0] + bk[1] * np.log(D) + bk[2] * pace)
        K = rng.negative_binomial(size_k, size_k / (size_k + mean_k))
        a_wins = rng.random(sims) < p
        m = 1 / (1 + np.exp(-(bw[0] + bw[1] * np.log(D))))
        w = rng.beta(np.maximum(m * phi_w, 1e-3), np.maximum((1 - m) * phi_w, 1e-3))
        share_a = np.where(a_wins, w, 1 - w)
        Ka = rng.binomial(K, share_a)
        Kb = K - Ka
        team_k = {"a": Ka, "b": Kb}
        for side, opp in (("a", "b"), ("b", "a")):
            lu = lus[side]
            ks = np.array([q["kill_share"] for q in lu])
            ks = ks / ks.sum()
            g = rng.gamma(ak * ks, 1.0, (sims, 5))
            pk = g / g.sum(1, keepdims=True)
            kills = rng.multinomial(team_k[side], pk)
            Dt = team_k[opp] + rng.poisson(ex, sims)
            ds = np.array([q["death_share"] for q in lu])
            ds = ds / ds.sum()
            gd = rng.gamma(ad * ds, 1.0, (sims, 5))
            deaths = rng.multinomial(Dt, gd / gd.sum(1, keepdims=True))
            for j, q in enumerate(lu):
                qq = min(max(q["participation"], 1e-3), 1 - 1e-3)
                qa = rng.beta(qq * phi_a, (1 - qq) * phi_a, sims)
                assists = rng.binomial(team_k[side] - kills[:, j], qa)
                cs = q["cspm"] * D * np.exp(rng.normal(cs_bias, cs_sd, sims))
                tot[(q["pid"], "kills")] += kills[:, j]
                tot[(q["pid"], "assists")] += assists
                tot[(q["pid"], "deaths")] += deaths[:, j]
                tot[(q["pid"], "cs")] += cs
                tot[(q["pid"], "fantasy")] += 3 * kills[:, j] + 2 * assists - deaths[:, j] + 0.02 * cs
    return tot


def summarize(x, line=None):
    """Simulated totals -> mean, median, 80% range, and P(over / under / push) at a line (straight from the draws)."""
    out = {"mean": float(x.mean()), "median": float(np.median(x)), "p10": float(np.percentile(x, 10)),
           "p90": float(np.percentile(x, 90)), "sd": float(x.std())}
    if line is not None:
        over, under = float((x > line).mean()), float((x < line).mean())
        out.update(p_over=over, p_under=under, p_push=1.0 - over - under)
    return out


def state_until(rows, hl, day):
    """The pre-match state from every history game whose series day is before `day` (production: one per cycle)."""
    allg = games_from_rows(rows)
    sday = {}
    for g in allg:
        d = _day(g["played_at"])
        sday[g["match_key"]] = min(sday.get(g["match_key"], d), d)
    st = State(hl)
    for g in sorted(allg, key=lambda g: (sday[g["match_key"]], g["played_at"])):
        if sday[g["match_key"]] < day:
            st.add_game(sday[g["match_key"]], g)
    return st


def context(st, params, team_a, team_b, p_win, day, posted=None):
    """Both lineups = each team's last fielded lineup before `day`; the posted player (pid, role) takes their role's slot
    on their team when not in it. -> ctx for simulate() or None (a team without a known lineup)."""
    ctx = {"p_win": p_win if p_win is not None else 0.5,
           "team_a": team_feats(st, team_a, day, params["priors"], params["k_team"]),
           "team_b": team_feats(st, team_b, day, params["priors"], params["k_team"])}
    for side, tm in (("a", team_a), ("b", team_b)):
        lu = dict(st.lineup.get(tm) or {})
        if posted and posted.get("team") == tm and posted.get("role") in ROLES:
            # audit 45: a posted player listed in another slot swaps with the player of his posted role - never two slots
            old = [r for r, p in lu.items() if p == posted["pid"] and r != posted["role"]]
            displaced = lu.get(posted["role"])
            for r in old:
                del lu[r]
            if old and displaced is not None and displaced != posted["pid"]:
                lu[old[0]] = displaced
            lu[posted["role"]] = posted["pid"]
        if len(lu) != 5:
            return None
        ctx["lineup_" + side] = [dict(player_feats(st, pid, role, day, params["priors"], params["k0"]), pid=pid, role=role)
                                 for role, pid in sorted(lu.items())]
    return ctx
