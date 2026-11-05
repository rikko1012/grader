"""pnf-1: NBA projection DISTRIBUTION from the full history (CANDIDATE / RESEARCH ONLY - nothing in the live board, the
worker or the grader imports this module; sm-1 5ec8adf73a6dad18 stays the live projection).

Owner 2026-11-05: "dont ruin calibration simply fix projections ... we have every possible inch of data". This model
changes the projection itself (the predictive distribution of the stat) - there is no shrink / balance / side layer on
the printed chance, and the offered book line is NOT an input.

Structure (minutes distribution x per-minute rate, stat-specific dispersion):

  minutes   m_hat = base + GBM(pre-game features) on played games (L2; base = the player's EW-10 minutes). The minutes DISTRIBUTION is m_hat + s_hat * z_q:
            s_hat = a GBM of the held-out |residual| (bench volatility, blowout risk, return from absence), z_q = the
            empirical held-out standardised-residual quantiles of the m_hat bin (blowouts, foul trouble and early exits
            live in that tail), clipped to [0.5, 53].
  rate      per stat a Poisson GBM with offset log(actual minutes) + log(base rate = EW-40 rate shrunk 10 games
            toward the position rate): r_hat = expected stat per minute given the pre-game
            features (role, usage, teammates out, opponent allowance by position, pace, rest, spread...).
  count     stat | minutes m ~ NegBin(mean r_hat * m, alpha), alpha per stat and per bin of r_hat * m, fitted by maximum
            likelihood on HELD-OUT rows (the last CAL_ROWS training rows, predicted by models fitted without them) at
            the actual minutes.
  PMF       equal-weight mixture over the Q minutes quantile points: sum_q NB(k; r_hat * m_q, alpha) / Q.

Every fitted number comes from the training rows passed to fit() (games that ended before the refit time); the
walk-forward harness (sports/research/proj_nba_full/walkforward.py) enforces the time cut.
"""
from __future__ import annotations

import numpy as np

VERSION = "pnf-1"
STATS = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
SUPPORT = {"points": (0, 100), "rebounds": (0, 40), "assists": (0, 35), "threes": (0, 20)}   # = sports.model.dists
# minutes quantile grid: tail-dense (iteration 4 - the 20-point equal grid put its lowest point at the 2.5% quantile and
# under-weighted early exits / blowout benchings: points PIT lowest decile 11.6%)
_QE = np.array([0, .005, .01, .02, .035, .05, .075, .1, .15, .2, .25, .3, .35, .4, .45, .5, .55, .6, .65, .7, .75, .8,
                .85, .9, .925, .95, .965, .98, .99, .995, 1.0])
QS = (_QE[1:] + _QE[:-1]) / 2
QW = np.diff(_QE)
Q = len(QS)
CAL_ROWS = 15000
MIN_BINS = (0, 8, 14, 20, 26, 31, 36, 60)
ALPHA_BIN_Q = (0.2, 0.4, 0.6, 0.8)
FAMILY = {"points": "ratio"}   # iteration 5: points NB left tail too thin (PIT lowest decile 11.5%)
RQ = (np.arange(40) + 0.5) / 40
SCALE_MODEL = False         # iteration 2 (dev window): the |residual| scale model lowered the log score -> off
BASE_K = 10.0               # games of shrinkage of the player's EW-40 rate toward the position rate (base offset)
GBM = dict(objective="regression", learning_rate=0.1, num_leaves=31, min_child_samples=100, subsample=0.8,
           subsample_freq=1, colsample_bytree=0.6, reg_lambda=1.0, n_estimators=400, verbose=-1, n_jobs=8)


# ------------------------------------------------------------------------------------------------------ distributions
def _lgamma(x):
    from scipy.special import gammaln
    return gammaln(x)


def nb_logpmf(k, mu, alpha):
    """log NegBin(k; mean mu, var mu + alpha mu^2). Broadcasts. alpha -> 0 is Poisson."""
    k = np.asarray(k, float)
    mu = np.maximum(np.asarray(mu, float), 1e-9)
    alpha = np.maximum(np.asarray(alpha, float), 1e-6)
    r = 1.0 / alpha
    return (_lgamma(k + r) - _lgamma(r) - _lgamma(k + 1) + r * np.log(r / (r + mu)) + k * np.log(mu / (r + mu)))


def nb_fit_alpha(y, mu, grid=None):
    grid = np.exp(np.linspace(np.log(1e-4), np.log(3.0), 80)) if grid is None else grid
    ll = [nb_logpmf(y, mu, a).sum() for a in grid]
    return float(grid[int(np.argmax(ll))])


def mixture_pmf(rate, mq, alpha_fn, lo, hi, w=None):
    """rate (n,), mq (n, Q) minutes points with weights w (Q,; default equal), alpha_fn(mean array) -> alpha array.
    -> (n, K) PMF on lo..hi."""
    ks = np.arange(lo, hi + 1, dtype=float)
    n = len(rate)
    w = np.full(mq.shape[1], 1.0 / mq.shape[1]) if w is None else np.asarray(w, float) / np.sum(w)
    out = np.zeros((n, len(ks)))
    for q in range(mq.shape[1]):
        mu = np.maximum(rate * mq[:, q], 1e-3)
        a = alpha_fn(mu)
        out += w[q] * np.exp(nb_logpmf(ks[None, :], mu[:, None], a[:, None]))
    out[:, -1] += np.maximum(1.0 - out.sum(1), 0.0)          # tail mass beyond the support onto the last point
    return out / out.sum(1, keepdims=True)


def ratio_mixture_pmf(rate, mq, edges, R, lo, hi, w):
    """Empirical-ratio family: stat | minutes m = rate*m * ratio, ratio drawn from the held-out y/mu quantiles R of the
    mu bin (edges); each point mass split linearly between its two neighbouring integers."""
    K = hi - lo + 1
    n, J = len(rate), R.shape[1]
    out = np.zeros(n * K)
    w = np.asarray(w, float) / np.sum(w)
    base = np.repeat(np.arange(n) * K, J)
    for q in range(mq.shape[1]):
        mu = np.maximum(rate * mq[:, q], 1e-3)
        v = (mu[:, None] * R[np.searchsorted(edges, mu, side="right")]).ravel() - lo
        v = np.clip(v, 0, K - 1)
        f = np.floor(v).astype(int)
        fr = v - f
        c = np.minimum(f + 1, K - 1)
        np.add.at(out, base + f, w[q] / J * (1 - fr))
        np.add.at(out, base + c, w[q] / J * fr)
    out = out.reshape(n, K)
    return out / out.sum(1, keepdims=True)


# --------------------------------------------------------------------------------------------------------- the model
class Model:
    def __init__(self, features, params=None):
        self.features = list(features)
        self.params = dict(GBM, **(params or {}))
        self.minutes = None
        self.rate = {}
        self.res_q = {}
        self.alpha = {}
        self.ratio = {}
        self.best_iter = {}

    # -- helpers
    def _X(self, df):
        return df[self.features].to_numpy(float)

    def _gbm(self, objective, n_est=None):
        import lightgbm as lgb
        p = dict(self.params, objective=objective)
        if n_est:
            p["n_estimators"] = n_est
        return lgb.LGBMRegressor(**p)

    def _min_bin(self, m):
        return np.clip(np.searchsorted(MIN_BINS, m, side="right") - 1, 0, len(MIN_BINS) - 2)

    # -- the base the GBMs correct (iteration 3: GBMs learn multiplicative / additive corrections to the player's own
    #    recent level instead of the level itself - out-of-window rates and minutes drift with the player)
    def _base_min(self, df):
        b = df["min_ew10"].to_numpy(float)
        for c in ("season_min_avg", "min_last"):
            b = np.where(np.isfinite(b), b, df[c].to_numpy(float))
        return np.where(np.isfinite(b), b, self.gmin)

    def _base_rate(self, df, st):
        c = STATS[st]
        pos = np.array([self.pos_rate[st].get(p, self.pos_rate[st]["_all"]) for p in df["pos_g"]])
        r = df[f"r_{c}_ew40"].to_numpy(float)
        n = np.nan_to_num(df["prior_n"].to_numpy(float))
        r = np.where(np.isfinite(r), r, pos)
        return np.maximum((n * r + BASE_K * pos) / (n + BASE_K), 1e-3)

    def _fit_base(self, tr):
        self.gmin = float(tr["min"].mean())
        self.pos_rate = {}
        for st, c in STATS.items():
            d = {"_all": float(tr[c].sum() / tr["min"].sum())}
            for pos, g in tr.groupby("pos_g"):
                d[pos] = float(g[c].sum() / max(g["min"].sum(), 1.0))
            self.pos_rate[st] = d

    def rate_of(self, models, df, st, X=None):
        X = self._X(df) if X is None else X
        return self._base_rate(df, st) * np.exp(models[st].predict(X, raw_score=True))

    def mhat_of(self, model, df, X=None):
        X = self._X(df) if X is None else X
        return self._base_min(df) + model.predict(X)

    def _alpha_fn(self, stat):
        edges, vals = self.alpha[stat]

        def fn(mu):
            return np.asarray(vals)[np.searchsorted(edges, mu, side="right")]
        return fn

    # -- fit
    def _fit_stage(self, tr, va=None):
        X = self._X(tr)
        mins = tr["min"].to_numpy(float)
        res = {}
        mm = self._gbm("regression", self.best_iter.get("minutes") if va is None else None)
        if va is not None:
            mm.fit(X, mins - self._base_min(tr),
                   eval_set=[(self._X(va), va["min"].to_numpy(float) - self._base_min(va))], callbacks=[_early(50)])
            self.best_iter["minutes"] = max(int(mm.best_iteration_ or self.params["n_estimators"]), 50)
        else:
            mm.fit(X, mins - self._base_min(tr))
        res["minutes"] = mm
        for st, c in STATS.items():
            off = np.log(np.maximum(mins, 0.5)) + np.log(self._base_rate(tr, st))
            m = self._gbm("poisson", self.best_iter.get(st) if va is None else None)
            y = tr[c].to_numpy(float)
            if va is not None:
                vo = np.log(np.maximum(va["min"].to_numpy(float), 0.5)) + np.log(self._base_rate(va, st))
                m.fit(X, y, init_score=off, eval_set=[(self._X(va), va[c].to_numpy(float))], eval_init_score=[vo],
                      callbacks=[_early(50)])
                self.best_iter[st] = max(int(m.best_iteration_ or self.params["n_estimators"]), 50)
            else:
                m.fit(X, y, init_score=off)
            res[st] = m
        return res

    def fit(self, train):
        """train: played rows (prior_n >= 1) of games that ENDED before the refit time, time-ordered."""
        train = train.sort_values("ts")
        cal = train.iloc[-CAL_ROWS:]
        fit_a = train.iloc[:-CAL_ROWS]
        self._fit_base(fit_a)
        a = self._fit_stage(fit_a, cal)
        # held-out minutes residuals: a scale model (GBM on |residual| from the pre-game features + m_hat: bench
        # volatility, blowout risk, return from absence) and the quantiles of the STANDARDISED residual by m_hat bin
        Xc = self._X(cal)
        mh = self.mhat_of(a["minutes"], cal, Xc)
        r = cal["min"].to_numpy(float) - mh
        self.scale = None
        if SCALE_MODEL:
            import lightgbm as lgb
            self.scale = lgb.LGBMRegressor(**dict(self.params, objective="regression", n_estimators=150,
                                                  learning_rate=0.05, min_child_samples=300))
            self.scale.fit(np.column_stack([Xc, mh]), np.abs(r))
            sc = self._scale(Xc, mh)
        else:
            sc = np.ones(len(r))
        z = r / sc
        b = self._min_bin(mh)
        allq = np.quantile(z, QS)
        self.res_q = {int(k): (np.quantile(z[b == k], QS) if (b == k).sum() >= 200 else allq)
                      for k in range(len(MIN_BINS) - 1)}
        # NB dispersion at the actual minutes, by bin of the conditional mean
        mins = np.maximum(cal["min"].to_numpy(float), 0.5)
        for st, c in STATS.items():
            mu = self.rate_of(a, cal, st, Xc) * mins
            y = cal[c].to_numpy(float)
            edges = np.quantile(mu, ALPHA_BIN_Q)
            bi = np.searchsorted(edges, mu, side="right")
            vals = [nb_fit_alpha(y[bi == k], mu[bi == k]) for k in range(len(edges) + 1)]
            self.alpha[st] = (edges.tolist(), vals)
            if FAMILY.get(st) == "ratio":                  # held-out y / mu quantiles by mu bin
                ratio = y / np.maximum(mu, 1e-3)
                self.ratio[st] = (edges.tolist(), np.vstack([np.quantile(ratio[bi == k], RQ)
                                                             for k in range(len(edges) + 1)]))
        self._fit_base(train)
        full = self._fit_stage(train)
        self.minutes = full["minutes"]
        self.rate = {st: full[st] for st in STATS}
        return self

    # -- predict
    def _scale(self, X, mh):
        if self.scale is None:
            return np.ones(len(mh))
        return np.maximum(self.scale.predict(np.column_stack([X, mh])), 0.5)

    def minutes_points(self, df):
        X = self._X(df)
        mh = self.mhat_of(self.minutes, df, X)
        b = self._min_bin(mh)
        R = np.vstack([self.res_q[int(k)] for k in b]) * self._scale(X, mh)[:, None]
        return np.clip(mh[:, None] + R, 0.5, 53.0), mh

    def predict(self, df, stats=None):
        """-> {stat: (pmf (n, K), mean (n,), lo)} plus '_minutes': m_hat."""
        X = self._X(df)
        mq, mh = self.minutes_points(df)
        out = {"_minutes": mh}
        for st in (stats or STATS):
            rate = self.rate_of(self.rate, df, st, X)
            lo, hi = SUPPORT[st]
            if FAMILY.get(st) == "ratio":
                p = ratio_mixture_pmf(rate, mq, *self.ratio[st], lo, hi, QW)
            else:
                p = mixture_pmf(rate, mq, self._alpha_fn(st), lo, hi, QW)
            ks = np.arange(lo, hi + 1, dtype=float)
            out[st] = (p, p @ ks, lo)
        return out


def _early(n):
    import lightgbm as lgb
    return lgb.early_stopping(n, verbose=False)


# ----------------------------------------------------------------------------------------------------------- metrics
def logscore(pmf, lo, y):
    idx = np.clip(np.rint(np.asarray(y, float)).astype(int) - lo, 0, pmf.shape[1] - 1)
    return np.log(np.maximum(pmf[np.arange(len(idx)), idx], 1e-12))


def pit_bounds(pmf, lo, y):
    """(P(X < y), P(X <= y)) per row; randomized PIT = lo + U (hi - lo)."""
    c = np.cumsum(pmf, 1)
    idx = np.clip(np.rint(np.asarray(y, float)).astype(int) - lo, 0, pmf.shape[1] - 1)
    hi = c[np.arange(len(idx)), idx]
    return hi - pmf[np.arange(len(idx)), idx], hi


def p_over(pmf, lo, t):
    """P(X > t) for a half-integer threshold t (scalar or per-row array)."""
    ks = lo + np.arange(pmf.shape[1], dtype=float)
    t = np.broadcast_to(np.asarray(t, float), (pmf.shape[0],))
    return (pmf * (ks[None, :] > t[:, None])).sum(1)
