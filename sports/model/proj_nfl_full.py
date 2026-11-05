"""pnf-1: NFL full-history projection DISTRIBUTION (research / shadow only - never imported by the live path).

Owner 2026-11-05: "dont ruin calibration simply fix projections ... we have every possible inch of data". A projection
is a predictive distribution of the stat. pnf-1 builds it as VOLUME x EFFICIENCY with fitted dispersion, from every
pre-game input the full ds-1 history carries (numpy only, so it could run in the worker image):

  volume      targets / carries / pass attempts ~ (1 - pi) NegBin(mu1, alpha) + pi NegBin(rho mu1, alpha): a full-game
              and a partial-game component (injury exit / benching / rest), mean mu_v; alpha, pi, rho by MLE per
              dispersion cell (volume level x role: QB1 incumbent / snap share >= 60%)
              log mu_v = ridge Poisson GLM per position group on: recency-weighted volume at three half-lives, last
              game, season-to-date, snap share (EW / last / last 3), target share, air-yards share, the team's own
              volume level, team pass rate (neutral), team / opponent plays, game script (implied team total, spread,
              total), the opponent's allowance of that volume to the position, home, roof, wind, temperature, rest,
              lagged QB change, the player's QB1 role (QBs), return from a multi-week absence, team change, experience.
  conversion  receptions | targets ~ Binomial(t, p_catch); completions | attempts ~ Binomial(a, p_cmp), the game's
              rate logit-normal around p (spread tau by MLE); p = ridge logistic GLM (weights = opportunities) on the
              player's shrunk (EB, k per position chosen on training rows) rate, depth of target, opponent
              allowance, weather, game script.
  yards       yards | n units = n u + s_b sqrt(n v1 + n^2 v2) e_b: v1 = c1 u^(2 gamma) play-to-play spread, v2 = c2 u^2
              uncertainty of the per-unit mean (grows with n, not sqrt(n)); c1, c2, gamma per position fitted on squared
              training residuals; e_b = empirical standardised shape per position x unit-count bucket (a breakaway
              run is a bigger share of a 4-carry game than a 20-carry one); u = yards per reception / carry /
              completion from a weighted least-squares fit on the EB per-unit rate, depth of target, opponent
              per-unit allowance, script, weather.

Markets: rec, rec_yds, rush_att, rush_yds, pass_yds (the live sm-1 markets) and pass_cmp (completions, no live model).
Every feature is computed from games BEFORE the row's game (featurize.nfl_frame + augment below use prior rows only;
the row's own outcome never enters its features). There is no chance-level layer, no Over/Under intercept and no line
input: the book line is never read. Fitting uses only rows known before the refit cutoff (the caller enforces it).
"""
from __future__ import annotations

import numpy as np

from . import dists, model
from .frame import ew_prior, group_starts, gshift, key_of, lookup, roll_prior, take

VERSION = "pnf-1"
MARKETS = ("rec", "rec_yds", "rush_att", "rush_yds", "pass_yds", "pass_cmp")
VOL = {"rec": "targets", "rec_yds": "targets", "rush_att": "carries", "rush_yds": "carries", "pass_yds": "pass_att",
       "pass_cmp": "pass_att"}
STAT = {"rec": "rec", "rec_yds": "rec_yds", "rush_att": "carries", "rush_yds": "rush_yds", "pass_yds": "pass_yds",
        "pass_cmp": "pass_cmp"}
GRID = {"pass_cmp": (0, 60)}
NMAX = {"targets": 25, "carries": 45, "pass_att": 70}
# position groups that get their own fit (others pooled into the last group)
PGROUPS = {"targets": ("WR", "TE", "RB"), "carries": ("RB", "QB", "WR"), "pass_att": ("QB",)}
CONV = {"targets": ("rec", "catch"), "pass_att": ("pass_cmp", "cmp")}
UNITS = {"rec_yds": ("rec", "rec_yds"), "rush_yds": ("carries", "rush_yds"), "pass_yds": ("pass_cmp", "pass_yds")}
BASE = ("targets", "carries", "pass_att", "rec", "rec_yds", "rush_yds", "pass_yds", "pass_cmp", "offense_pct",
        "target_share", "air_yards_share", "rec_air_yds", "rz_targets", "rz_carries")
LONG_HL = 8.0
K_GRID = (2.0, 5.0, 10.0, 20.0, 40.0, 80.0, 160.0)
N_EDGES = {"rec_yds": (2.5, 5.5), "rush_yds": (4.5, 10.5, 17.5), "pass_yds": (12.5, 20.5)}


def grid(market):
    if market in GRID:
        lo, hi = GRID[market]
        return lo, np.arange(lo, hi + 1, dtype=float)
    return dists.grid(market)


def pgroup(vol, pos):
    g = PGROUPS[vol]
    return np.where(np.isin(pos, g), pos, g[-1]).astype(object)


# ================================================================================================= features (augment)
def _team_table(con):
    from .featurize import _team_frame
    tg = _team_frame(con, "nfl_tg", "nfl")
    return tg


def augment(df, con):
    """Add pnf-1 point-in-time columns to a featurize.nfl_frame() frame (sorted by player, ts). Returns df."""
    gs = group_starts(df["player_id"])
    n = len(df)
    for c in BASE:
        x = df.f(c)
        for hl in (2.0, LONG_HL):
            m, w = ew_prior(x, gs, hl)
            df[f"e{int(hl)}_{c}"] = m
            df[f"W{int(hl)}_{c}"] = m * w
        # last KNOWN prior value (= the previous game when it has the field; ESPN-only rows lack snaps / shares, and
        # a missing previous value must not look like "no history" to the fit)
        df[f"lag1_{c}"], _ = ew_prior(x, gs, 0.02)
    sk = key_of(df["player_id"], df["season"])
    gss = group_starts(sk)
    df["n_season"] = (np.arange(n) - gss).astype(float)
    for c in ("targets", "carries", "pass_att", "offense_pct", "target_share"):
        df[f"sm_{c}"] = roll_prior(df.f(c), gss, 40, 1)
    gap = (df["ts"] - gshift(df["ts"], gs, 1)) / 86400.0
    df["gap_days"] = gap
    df["back_from_absence"] = np.where(np.isfinite(gap) & (df["n_season"] > 0), (gap > 10).astype(float), 0.0)
    prev_team = np.empty(n, dtype=object)
    prev_team[:] = ""
    i = np.arange(n)
    ok = i - 1 >= gs
    prev_team[ok] = df["team"][i[ok] - 1]
    df["team_change"] = np.where(ok, (prev_team != df["team"]).astype(float), 0.0)
    df["log_prior_n"] = np.log1p(df.f("prior_n"))
    # ---- team volume levels (team-game rows; ESPN-only games carry NULL counts and are skipped by the EW mean)
    tg = _team_table(con)
    tgs = group_starts(tg["team"])
    for c in ("pass_att", "carries", "targets", "plays"):
        tg[f"te_{c}"], _ = ew_prior(tg.f(c), tgs, 4.0)
    tkey = key_of(tg["game_id"], tg["team"])
    ti = lookup(key_of(df["game_id"], df["team"]), tkey)
    for c in ("pass_att", "carries", "targets", "plays"):
        df[f"team_e_{c}"] = take(tg[f"te_{c}"], ti)
    # ---- QB1 of the team's previous game (main passer), lagged: is this player the incumbent starter?
    qb = {}
    for j in np.flatnonzero(df["position"] == "QB"):
        k = (df["game_id"][j], df["team"][j])
        pa = df["pass_att"][j]
        if pa == pa and (k not in qb or pa > df["pass_att"][qb[k]]):
            qb[k] = j
    qb1 = np.array([df["player_id"][qb[(g, t)]] if (g, t) in qb else "" for g, t in zip(tg["game_id"], tg["team"])],
                   dtype=object)
    prev = np.empty(len(tg), dtype=object)
    prev[:] = ""
    ii = np.arange(len(tg))
    okt = ii - 1 >= tgs
    prev[okt] = qb1[ii[okt] - 1]
    prev_qb1 = take(prev, ti)
    df["qb1_lag"] = np.where(ti >= 0, (np.asarray(prev_qb1, dtype=object) == df["player_id"]).astype(float), np.nan)
    # ---- opponent allowance per position from COMPLETE (nflverse) games only, last 8 opponent games
    src = np.asarray(df["src"], dtype=object) if "src" in df else np.array(["nflverse"] * n, dtype=object)
    acols = ("targets", "rec", "rec_yds", "carries", "rush_yds", "pass_att", "pass_cmp", "pass_yds")
    akeys = key_of(df["game_id"], df["opp"], df["position"])
    u, inv = np.unique(akeys, return_inverse=True)
    sums = {c: np.bincount(inv, weights=np.nan_to_num(df.f(c)), minlength=len(u)) for c in acols}
    bad = np.bincount(inv, weights=(src != "nflverse").astype(float), minlength=len(u)) > 0
    parts = [x.split("|") for x in u]
    from .frame import Frame
    ag = Frame({"game_id": np.array([p[0] for p in parts], dtype=object),
                "team": np.array([p[1] for p in parts], dtype=object),
                "position": np.array([p[2] for p in parts], dtype=object),
                **{c: np.where(bad, np.nan, sums[c]) for c in acols}})
    gi = lookup(key_of(ag["game_id"], ag["team"]), tkey)
    ag = ag[gi >= 0]
    ag["ts"] = tg["ts"][gi[gi >= 0]]
    ag["tp"] = key_of(ag["team"], ag["position"])
    ag = ag.sort("tp", "ts")
    ags = group_starts(ag["tp"])
    for c in acols:
        ag[f"a8_{c}"] = roll_prior(ag.f(c), ags, 8, 3, "sum")
    ai = lookup(key_of(df["game_id"], df["opp"], df["position"]), key_of(ag["game_id"], ag["team"], ag["position"]))
    ng = np.maximum(take(roll_prior(np.ones(len(ag)) * np.where(np.isfinite(ag.f("targets")), 1.0, np.nan), ags, 8, 3,
                                    "sum"), ai), 1.0)
    with np.errstate(all="ignore"):
        A = {c: take(ag[f"a8_{c}"], ai) for c in acols}
        df["oa_targets"] = A["targets"] / ng
        df["oa_carries"] = A["carries"] / ng
        df["oa_pass_att"] = A["pass_att"] / ng
        df["oa_catch"] = A["rec"] / A["targets"]
        df["oa_ypr"] = A["rec_yds"] / A["rec"]
        df["oa_ypc"] = A["rush_yds"] / A["carries"]
        df["oa_cmp"] = A["pass_cmp"] / A["pass_att"]
        df["oa_ypcmp"] = A["pass_yds"] / A["pass_cmp"]
        df["adot"] = df.f("e8_rec_air_yds") / df.f("e8_targets")
        df["wind_x"] = np.where(df.f("dome") == 1, 0.0, df.f("wind"))
        df["temp_x"] = np.where(df.f("dome") == 1, 70.0, df.f("temp"))
        df["implied_targets"] = df.f("e8_target_share") * df.f("team_e_targets")
    return df


def _l(x):
    return np.log1p(np.maximum(np.asarray(x, float), 0.0))


def vol_features(df, vol):
    v = vol
    cols = {
        "e2": _l(df.f(f"e2_{v}")), "e4": _l(df.f(f"ew_{v}")), "e8": _l(df.f(f"e8_{v}")), "lag1": _l(df.f(f"lag1_{v}")),
        "sm": _l(df.f(f"sm_{v}")), "snap_e4": df.f("ew_offense_pct"), "snap_lag1": df.f("lag1_offense_pct"),
        "snap_l3": df.f("l3_snap"), "snap_sm": df.f("sm_offense_pct"), "team_vol": _l(df.f(f"team_e_{v}")),
        "team_plays": df.f("plays_l6"), "opp_plays": df.f("opp_plays_l6"), "npass": df.f("npass_l6"),
        "implied_total": df.f("implied_total"), "spread": df.f("team_spread"), "total": df.f("total_line"),
        "opp_allow": _l(df.f(f"oa_{v}")), "home": df.f("home"), "dome": df.f("dome"), "wind": df.f("wind_x"),
        "temp": df.f("temp_x"), "rest": np.clip(df.f("rest_days"), 4, 14), "qb_chg": df.f("qb_change_lag"),
        "back": df.f("back_from_absence"), "team_chg": df.f("team_change"), "exp": df.f("log_prior_n"),
        "n_season": np.minimum(df.f("n_season"), 4.0)}
    if v == "targets":
        cols.update({"ts_e8": df.f("e8_target_share"), "ts_e2": df.f("e2_target_share"),
                     "ays": df.f("e8_air_yards_share"), "impl_t": _l(df.f("implied_targets")),
                     "rz": df.f("e8_rz_targets")})
    elif v == "carries":
        cols.update({"rz": df.f("e8_rz_carries"), "tgt": _l(df.f("e8_targets"))})
    else:
        cols.update({"qb1": df.f("qb1_lag")})
    return cols


def conv_features(df, kind, eb_logit):
    c = {"eb": eb_logit, "adot": df.f("adot"), "wind": df.f("wind_x"), "dome": df.f("dome"),
         "implied_total": df.f("implied_total"), "spread": df.f("team_spread")}
    c["oa"] = df.f("oa_catch") if kind == "catch" else df.f("oa_cmp")
    return c


def unit_features(df, market, eb):
    c = {"eb": eb, "wind": df.f("wind_x"), "dome": df.f("dome"), "implied_total": df.f("implied_total"),
         "spread": df.f("team_spread"), "exp": df.f("log_prior_n")}
    if market == "rec_yds":
        c.update({"adot": df.f("adot"), "oa": df.f("oa_ypr"), "ays": df.f("e8_air_yards_share")})
    elif market == "rush_yds":
        c.update({"oa": df.f("oa_ypc"), "snap": df.f("ew_offense_pct")})
    else:
        c.update({"oa": df.f("oa_ypcmp"), "adot_qb": df.f("e8_pass_att")})
    return c


# ================================================================================================= small solvers
class Design:
    """Standardise on train (mean / sd), missing -> 0 plus an indicator when > 1% missing on train."""

    def __init__(self, cols):
        self.stats = {}
        for k, x in cols.items():
            ok = np.isfinite(x)
            sd = float(np.std(x[ok])) if ok.sum() > 1 else 1.0
            self.stats[k] = (float(np.mean(x[ok])) if ok.any() else 0.0, sd if sd > 1e-9 else 1.0,
                             bool((~ok).mean() > 0.01))

    def X(self, cols):
        out = [np.ones(len(next(iter(cols.values()))))]
        for k, (m, s, ind) in self.stats.items():
            z = (np.asarray(cols[k], float) - m) / s
            miss = ~np.isfinite(z)
            out.append(np.clip(np.where(miss, 0.0, z), -6, 6))
            if ind:
                out.append(miss.astype(float))
        return np.column_stack(out)


def _ridge_pen(p, lam, n):
    P = np.eye(p) * lam * n
    P[0, 0] = 0.0
    return P


def fit_poisson(X, y, w=None, lam=1e-3, iters=60):
    n, p = X.shape
    w = np.ones(n) if w is None else w
    beta = np.zeros(p)
    beta[0] = np.log(max(np.average(y, weights=w), 1e-3))
    P = _ridge_pen(p, lam, w.sum())
    for _ in range(iters):
        eta = np.clip(X @ beta, -12, 8)
        mu = np.exp(eta)
        W = w * mu
        z = eta + (y - mu) / mu
        new = np.linalg.solve(X.T @ (X * W[:, None]) + P, X.T @ (W * z))
        if np.max(np.abs(new - beta)) < 1e-8:
            return new
        beta = new
    return beta


def fit_logistic(X, k, n_trials, lam=1e-3, iters=60):
    """Binomial logit GLM: k successes of n_trials per row."""
    n, p = X.shape
    m = n_trials > 0
    X, k, nt = X[m], k[m], n_trials[m]
    beta = np.zeros(p)
    r = np.clip(k.sum() / max(nt.sum(), 1), 1e-3, 1 - 1e-3)
    beta[0] = np.log(r / (1 - r))
    P = _ridge_pen(p, lam, nt.sum())
    for _ in range(iters):
        eta = np.clip(X @ beta, -10, 10)
        pr = 1 / (1 + np.exp(-eta))
        W = nt * pr * (1 - pr) + 1e-9
        z = eta + (k - nt * pr) / W
        new = np.linalg.solve(X.T @ (X * W[:, None]) + P, X.T @ (W * z))
        if np.max(np.abs(new - beta)) < 1e-8:
            return new
        beta = new
    return beta


def fit_wls(X, y, w, lam=1e-3):
    P = _ridge_pen(X.shape[1], lam, w.sum())
    return np.linalg.solve(X.T @ (X * w[:, None]) + P, X.T @ (w * y))


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


# ================================================================================================= the model
class PNF:
    """Fitted pnf-1 (all pieces from training rows only)."""

    def __init__(self, lam=2e-3):
        self.lam = lam
        self.vol, self.conv, self.unit, self.prior = {}, {}, {}, {}

    # ---- EB rates (prior per position group, strength k)
    def _eb(self, df, num, den, key, k):
        pr = self.prior[key]
        pos = np.asarray(df["position"], dtype=object)
        base = np.array([pr.get(p, pr["_all"]) for p in pos], float)
        if isinstance(k, dict):
            k = np.array([k.get(p, k["_all"]) for p in pos], float)
        W = np.nan_to_num(df.f(f"W{int(LONG_HL)}_{num}"))
        D = np.nan_to_num(df.f(f"W{int(LONG_HL)}_{den}"))
        return (W + k * base) / (D + k)

    def _choose_k(self, tr, num, den, key):
        """EB shrinkage strength per position: the k (grid) whose shrunk prior-games rate best predicts the game's
        rate (weighted squared error, weights = the game's opportunities) on training rows."""
        pos = np.asarray(tr["position"], dtype=object)
        n = np.nan_to_num(tr.f(den))
        ok = n > 0
        rate = np.where(ok, np.nan_to_num(tr.f(num)) / np.maximum(n, 1e-9), 0.0)
        out = {}
        for p in list(np.unique(pos[ok])) + ["_all"]:
            m = ok & ((pos == p) if p != "_all" else True)
            if m.sum() < 300:
                continue
            sub = tr[m]
            out[str(p)] = min(K_GRID, key=lambda k: float(np.sum(n[m] * (rate[m] - self._eb(sub, num, den, key, k))
                                                                 ** 2)))
        return out

    def _prior(self, tr, num, den, key):
        pos = np.asarray(tr["position"], dtype=object)
        a, b = np.nan_to_num(tr.f(num)), np.nan_to_num(tr.f(den))
        out = {"_all": float(a.sum() / max(b.sum(), 1))}
        for p in np.unique(pos):
            m = pos == p
            if b[m].sum() > 50:
                out[str(p)] = float(a[m].sum() / b[m].sum())
        self.prior[key] = out

    def fit(self, tr):
        """tr: training rows (a Frame subset known before the cutoff; outcomes finite)."""
        pos = np.asarray(tr["position"], dtype=object)
        for vol in ("targets", "carries", "pass_att"):
            y = tr.f(vol)
            okv = np.isfinite(y) & (tr.f("prior_n") >= 1)
            grp = pgroup(vol, pos)
            for g in PGROUPS[vol]:
                m = okv & (grp == g)
                if vol == "targets":
                    m &= np.nan_to_num(tr.f("e8_targets")) > 0.2
                elif vol == "carries":
                    m &= np.nan_to_num(tr.f("e8_carries")) > 0.2
                else:
                    m &= np.nan_to_num(tr.f("e8_pass_att")) > 3
                if m.sum() < 200:
                    continue
                sub = tr[m]
                cols = vol_features(sub, vol)
                d = Design(cols)
                X = d.X(cols)
                beta = fit_poisson(X, sub.f(vol), lam=self.lam)
                mu = np.exp(np.clip(X @ beta, -12, 8))
                self.vol[(vol, g)] = {"design": d, "beta": beta, "n": int(m.sum()),
                                      "alpha": fit_alpha_bins(sub.f(vol), mu, self._disp_key(sub, vol, mu))}
        # conversion rates
        for vol, (num, kind) in CONV.items():
            self._prior(tr, num, vol, kind)
            m = np.nan_to_num(tr.f(vol)) > 0
            if vol == "pass_att":
                m &= pos == "QB"
            sub = tr[m]
            k = self.prior[kind + "_k"] = self._choose_k(sub, num, vol, kind)
            eb = self._eb(sub, num, vol, kind, k)
            cols = conv_features(sub, kind, _logit(eb))
            d = Design(cols)
            X = d.X(cols)
            beta = fit_logistic(X, sub.f(num), sub.f(vol), lam=self.lam)
            eta = np.clip(X @ beta, -10, 10)
            self.conv[kind] = {"design": d, "beta": beta, "tau": fit_tau(sub.f(num), sub.f(vol), eta)}
        # yards per unit + standardised residual shape per position group
        for mk, (unit, yds) in UNITS.items():
            key = "u_" + mk
            self._prior(tr, yds, unit, key)
            m = np.nan_to_num(tr.f(unit)) > 0
            if mk == "pass_yds":
                m &= pos == "QB"
            sub = tr[m]
            k = self.prior[key + "_k"] = self._choose_k(sub, yds, unit, key)
            eb = self._eb(sub, yds, unit, key, k)
            cols = unit_features(sub, mk, eb)
            d = Design(cols)
            X = d.X(cols)
            nu = sub.f(unit)
            beta = fit_wls(X, sub.f(yds) / nu, nu, lam=self.lam)
            um = np.maximum(X @ beta, 0.5)
            res = sub.f(yds) - nu * um
            grp = np.asarray(sub["position"], dtype=object)
            # Var(yards | n) = n v1 + n^2 v2: v1 = c1 u^(2 gamma) (play-to-play spread), v2 = c2 u^2 (uncertainty of the
            # player's per-unit mean, which scales with n, not sqrt(n)); c1, c2 >= 0 by least squares on squared
            # residuals per position group, gamma by Gaussian likelihood - all on training rows
            var_fit = {}
            for g in list(np.unique(grp)) + ["_all"]:
                mg = np.ones(len(grp), bool) if g == "_all" else grp == g
                if mg.sum() < 300:
                    continue
                var_fit[str(g)] = fit_var(res[mg], nu[mg], um[mg])
            vv = np.array([var_fit.get(str(g), var_fit["_all"])["c"] for g in grp])
            gam = np.array([var_fit.get(str(g), var_fit["_all"])["gamma"] for g in grp])
            sd = np.sqrt(nu * vv[:, 0] * um ** (2 * gam) + nu * nu * vv[:, 1] * um * um)
            z = res / np.maximum(sd, 1e-6)
            # shape per position x unit-count bucket: a breakaway run or a deep catch adds the same yards to a
            # 3-unit game as to a 20-unit one, so the standardised tail is heavier when n is small
            nb = np.digitize(nu, N_EDGES[mk])
            shapes = {}
            for g in list(np.unique(grp)) + ["_all"]:
                mg = (grp == g) if g != "_all" else np.ones(len(grp), bool)
                if mg.sum() < 300:
                    continue
                pooled = self._shape(z[mg])
                shapes[str(g)] = [self._shape(z[mg & (nb == j)]) if (mg & (nb == j)).sum() >= 200 else pooled
                                  for j in range(len(N_EDGES[mk]) + 1)]
            self.unit[mk] = {"design": d, "beta": beta, "shapes": shapes, "var": var_fit}
        return self

    @staticmethod
    def _disp_key(df, vol, mu):
        """Dispersion cell: volume level (fixed log-spaced bins of the predicted mean) x incumbent QB1 for passers /
        snap-share level for the rest - the spread of a starter's volume is not a backup's."""
        lvl = np.digitize(np.log(np.maximum(mu, 1e-3)), DISP_EDGES[vol])
        if vol == "pass_att":
            role = np.nan_to_num(df.f("qb1_lag")) > 0.5
        else:
            role = np.nan_to_num(df.f("ew_offense_pct")) >= 0.6
        return lvl * 2 + role.astype(int)

    @staticmethod
    def _shape(z):
        """Standardised residual shape (unit variance, empirical quantiles): skew / tails of yards given units."""
        s = float(np.std(z))
        q = np.quantile(z / s, (np.arange(400) + 0.5) / 400)
        return {"s": s, "e": np.sort(q)}

    # ---- prediction pieces
    def vol_mean(self, df, vol):
        pos = np.asarray(df["position"], dtype=object)
        grp = pgroup(vol, pos)
        mu = np.full(len(df), np.nan)
        al = np.tile([0.5, 0.0, 1.0], (len(df), 1))
        for g in PGROUPS[vol]:
            m = grp == g
            fit = self.vol.get((vol, g)) or next((v for (vv, _), v in self.vol.items() if vv == vol), None)
            if not m.any() or fit is None:
                continue
            sub = df[m]
            X = fit["design"].X(vol_features(sub, vol))
            mu[m] = np.exp(np.clip(X @ fit["beta"], -12, 8))
            al[m] = alpha_of(fit["alpha"], self._disp_key(sub, vol, mu[m]))
        return np.maximum(np.nan_to_num(mu, nan=0.05), 0.02), al

    def conv_eta(self, df, vol):
        num, kind = CONV[vol]
        eb = self._eb(df, num, vol, kind, self.prior[kind + "_k"])
        f = self.conv[kind]
        X = f["design"].X(conv_features(df, kind, _logit(eb)))
        return np.clip(X @ f["beta"], -10, 10), f["tau"]

    def thin(self, T, df, vol, rmax):
        """Units = Binomial thinning of volume with a logit-normal conversion rate (Gauss-Hermite nodes)."""
        eta, tau = self.conv_eta(df, vol)
        if tau <= 0:
            return thin(T, np.clip(1 / (1 + np.exp(-eta)), 0.01, 0.99), rmax)
        out = 0.0
        for z, w in zip(GH_Z, GH_W):
            out = out + w * thin(T, np.clip(1 / (1 + np.exp(-(eta + tau * z))), 0.01, 0.99), rmax)
        return out

    def unit_mean(self, df, mk):
        unit, yds = UNITS[mk]
        key = "u_" + mk
        eb = self._eb(df, yds, unit, key, self.prior[key + "_k"])
        f = self.unit[mk]
        X = f["design"].X(unit_features(df, mk, eb))
        return np.maximum(X @ f["beta"], 0.5)

    def pmf(self, df, market):
        """-> (pmf matrix on grid(market), lo, mean)."""
        lo, ks = grid(market)
        vol = VOL[market]
        mu_v, alpha = self.vol_mean(df, vol)
        nmax = NMAX[vol]
        T = negbin_rows(mu_v, alpha, nmax)
        if market == "rush_att":
            p = _pad(T, lo, ks)
        elif market in ("rec", "pass_cmp"):
            p = _pad(self.thin(T, df, vol, len(ks) - 1 + lo), lo, ks)
        else:
            units = T if market == "rush_yds" else self.thin(T, df, vol, nmax)
            um = self.unit_mean(df, market)
            pos = np.asarray(df["position"], dtype=object)
            u = self.unit[market]
            p = np.zeros((len(df), len(ks)))
            for g in np.unique(pos):
                m = pos == g
                sh = u["shapes"].get(str(g), u["shapes"]["_all"])
                vf = u["var"].get(str(g), u["var"]["_all"])
                c1, c2 = vf["c"]
                v1 = c1 * um[m] ** (2 * vf["gamma"])
                v2 = c2 * um[m] ** 2
                p[m] = yards_given_units(units[m], um[m], v1, sh, lo, ks, v2=v2, n_edges=N_EDGES[market])
        p = p / np.maximum(p.sum(axis=1, keepdims=True), 1e-12)
        return p, lo, p @ ks


DISP_EDGES = {"targets": np.log([2.0, 4.0, 6.5]), "carries": np.log([3.0, 8.0, 14.0]),
              "pass_att": np.log([15.0, 28.0, 36.0])}


PI_GRID = (0.0, 0.03, 0.06, 0.1, 0.15, 0.22)
RHO_GRID = (0.1, 0.3, 0.55)
A_GRID = np.exp(np.linspace(np.log(1e-3), np.log(2.0), 30))


def nb_logpmf(y, mu, alpha):
    """Per-row NB log pmf of integer y, var = mu + alpha mu^2 (numpy only)."""
    y = np.maximum(np.rint(np.asarray(y, float)), 0).astype(int)
    mu = np.maximum(np.asarray(mu, float), 1e-6)
    r = 1.0 / max(float(alpha), 1e-8)
    T = np.concatenate([[0.0], np.cumsum(np.log(r + np.arange(max(int(y.max()), 1), dtype=float)))])
    return T[y] - dists.logfact(y) + r * np.log(r / (r + mu)) + y * np.log(mu / (r + mu))


def mix_loglik(y, mu, alpha, pi, rho):
    """Volume = (1 - pi) NB(mu1) + pi NB(rho mu1), mu1 = mu / (1 - pi + pi rho): a full-game component and a
    partial-game component (injury exit, benching, blowout rest) with the same overall mean mu."""
    mu1 = mu / (1 - pi + pi * rho)
    a = nb_logpmf(y, mu1, alpha)
    if pi <= 0:
        return float(a.sum())
    b = nb_logpmf(y, rho * mu1, alpha)
    m = np.maximum(a, b)
    return float(np.sum(m + np.log((1 - pi) * np.exp(a - m) + pi * np.exp(b - m))))


def fit_mix(y, mu):
    best = None
    for pi in PI_GRID:
        for rho in (RHO_GRID if pi > 0 else (1.0,)):
            for a in A_GRID:
                ll = mix_loglik(y, mu, a, pi, rho)
                if best is None or ll > best[0]:
                    best = (ll, float(a), pi, rho)
    return {"alpha": best[1], "pi": best[2], "rho": best[3]}


def fit_alpha_bins(y, mu, key, min_n=150):
    """Volume dispersion per cell (MLE of the two-component NB mixture); small cells use the pooled fit."""
    out = {"_all": fit_mix(y, mu)}
    for k in np.unique(key):
        m = key == k
        if m.sum() >= min_n:
            out[int(k)] = fit_mix(y[m], mu[m])
    return out


def alpha_of(table, key):
    """-> (n, 3) array of (alpha, pi, rho) per row."""
    return np.array([[table.get(int(k), table["_all"])[c] for c in ("alpha", "pi", "rho")] for k in key], float)


def negbin_rows(mu, par, nmax):
    ks = np.arange(nmax + 1, dtype=float)
    a, pi, rho = par[:, 0], par[:, 1], par[:, 2]
    mu1 = mu / (1 - pi + pi * rho)
    return (1 - pi)[:, None] * dists.negbin(mu1, a, 0, ks) + pi[:, None] * dists.negbin(rho * mu1, a, 0, ks)


GH_Z, GH_W = np.polynomial.hermite_e.hermegauss(7)
GH_W = GH_W / GH_W.sum()
TAU_GRID = (0.0, 0.1, 0.2, 0.3, 0.4, 0.55, 0.7)


def fit_tau(k, n, eta):
    """Logit-normal spread of the per-game conversion rate by maximum likelihood (grid)."""
    m = n > 0
    k, n, eta = k[m], n[m], eta[m]
    best = None
    for tau in TAU_GRID:
        L = []
        for z in GH_Z:
            p = np.clip(1 / (1 + np.exp(-(eta + tau * z))), 1e-6, 1 - 1e-6)
            L.append(k * np.log(p) + (n - k) * np.log(1 - p))
        L = np.array(L)
        mx = L.max(axis=0)
        tot = float(np.sum(mx + np.log(np.exp(L - mx).T @ GH_W)))
        if best is None or tot > best[0]:
            best = (tot, tau)
    return best[1]


def fit_var(res, n, u, gammas=(0.0, 0.5, 1.0)):
    """Var(res | n, u) = c1 n u^(2 gamma) + c2 n^2 u^2 (c >= 0, least squares on res^2); gamma by Gaussian loglik."""
    r2 = res * res
    best = None
    for g in gammas:
        A = np.column_stack([n * u ** (2 * g), n * n * u * u])
        c = np.linalg.lstsq(A, r2, rcond=None)[0]
        if c[1] < 0:
            c = np.array([float(np.sum(r2 * A[:, 0]) / np.sum(A[:, 0] ** 2)), 0.0])
        if c[0] < 0:
            c = np.array([0.0, float(np.sum(r2 * A[:, 1]) / np.sum(A[:, 1] ** 2))])
        v = np.maximum(A @ c, 1e-3)
        ll = -0.5 * float(np.sum(np.log(v) + r2 / v))
        if best is None or ll > best[0]:
            best = (ll, g, c)
    return {"gamma": best[1], "c": [float(best[2][0]), float(best[2][1])]}


def thin(T, p, rmax):
    """P(R = r) = sum_n T(n) Binom(r; n, p), r = 0..rmax."""
    nmax = T.shape[1] - 1
    B = model._binom_pmf_matrix(nmax, p, rmax)
    return np.einsum("rn,rnk->rk", T, B)


def _pad(P, lo, ks):
    """Count PMF over 0..n -> the market grid (count grids start at 0); mass beyond the grid goes to its last point."""
    assert lo == 0
    out = np.zeros((P.shape[0], len(ks)))
    k = min(P.shape[1], len(ks))
    out[:, :k] = P[:, :k]
    out[:, -1] += P[:, k:].sum(axis=1)
    return out


def yards_given_units(units_pmf, unit_mean, v1, shapes, lo, ks, chunk=256, v2=None, n_edges=()):
    """Yards PMF = sum_n P(N=n) P(yards | n), yards | n = n u + s_b sqrt(n v1 + n^2 v2) e_b (per-row u, v1, v2),
    e_b ~ the empirical standardised shape of n's bucket b (shapes: list of {"s", "e"} per bucket of n_edges, or one
    sorted array = a single unit-variance shape)."""
    if not isinstance(shapes, (list, tuple)):
        shapes = [{"s": 1.0, "e": np.asarray(shapes, float)}]
        n_edges = ()
    v2 = np.zeros(len(unit_mean)) if v2 is None else np.asarray(v2, float)
    v1 = np.asarray(v1, float)
    rows, nmax1 = units_pmf.shape
    out = np.zeros((rows, len(ks)))
    zero = int(0 - lo)
    up = ks[None, None, :] + 0.5
    nn = np.arange(1, nmax1).astype(float)
    n = nn[None, :, None]
    bucket = np.digitize(nn, n_edges)
    scale = np.array([shapes[j]["s"] for j in bucket])[None, :, None]
    for a in range(0, rows, chunk):
        b = min(rows, a + chunk)
        m = n * unit_mean[a:b, None, None]
        sd = scale * np.sqrt(np.maximum(n * v1[a:b, None, None] + n * n * v2[a:b, None, None], 1e-6))
        Z = (up - m) / sd
        F_up = np.empty_like(Z)
        for j in np.unique(bucket):
            cols = bucket == j
            cdf_x = np.asarray(shapes[j]["e"], float)
            cdf_y = (np.arange(len(cdf_x)) + 0.5) / len(cdf_x)
            F_up[:, cols, :] = np.interp(Z[:, cols, :], cdf_x, cdf_y, left=0.0, right=1.0)
        P = np.diff(np.concatenate([np.zeros((b - a, nmax1 - 1, 1)), F_up], axis=2), axis=2)
        P[:, :, -1] += 1 - F_up[:, :, -1]
        out[a:b] = np.einsum("rn,rnk->rk", units_pmf[a:b, 1:], P)
        out[a:b, zero] += units_pmf[a:b, 0]
    return out / np.maximum(out.sum(axis=1, keepdims=True), 1e-12)
