"""NBA projection candidates pn-1 (CANDIDATE / SHADOW ONLY - nothing here is imported by the live board, the worker or
the grader; sm-1 5ec8adf73a6dad18 stays the live projection).

Owner 2026-11-05: "dont ruin calibration simply fix projections". Every candidate below changes the PROJECTION
DISTRIBUTION of one prop (its location and/or its spread) - never the printed chance after the fact:

  * no multiplier / shrink on chances, no forced Over/Under balance, no global side intercept, no stake change;
  * the adjusted distribution is the sm-1 PMF moved by an additive location shift d and a spread scale s around its
    mean:  x -> mu + d + s * (x - mu)   (mass split between the two neighbouring integers, support clipped), so the
    chance of every line is read from ONE distribution exactly as forward.build_forecast reads sm-1;
  * every parameter is fitted on settled props whose games ENDED before the forecast time of the props it is applied
    to (sports/research/proj_nba/walkforward.py enforces this per test game-day).

Stages (each returns per-row (d, s); stages compose: d = d1 + d2, s = s1 * s2):

  ma  market-anchored mean      mean' = a + b * mu + c * line per stat (OLS on past props; the offered line is known
                                at forecast time and is an INPUT to the mean, the shape is sm-1's)
  md  median-anchored location  median' = median + (1 - a) * (line - median), a per stat x reliability group
                                (starter / bench / thin) by log loss on past props
  sk  skew / spread refit       median' = median + lam * (mu - median), spread * s; (lam, s) per stat by log loss on
                                past props - line-free: the board population is less right-skewed than the families
  rm  role / minutes            minutes' from a regression of actual minutes on pre-game minute / start features
                                (past player-games); mean scaled by minutes' / ew_min; thin history (prior_n < 20) gets
                                a per-minute floor on the mean and a spread floor from past thin-row residuals
"""
from __future__ import annotations

import numpy as np

VERSION = "pn-1"
STATS = ("points", "rebounds", "assists", "threes")
MA_MIN_ROWS = 150          # fewer past props of a stat -> no market-anchored fit (stage returns identity)
MD_GRID = np.round(np.arange(0.0, 1.0001, 0.05), 2)
MD_MIN_ROWS = 80
SK_LAM = np.round(np.arange(-0.5, 2.0001, 0.25), 2)
SK_S = (0.9, 0.95, 1.0, 1.05, 1.1, 1.15)
RM_MIN_PG = 200            # player-games needed to fit the minutes regression
RM_CLIP = (0.7, 1.6)       # minutes' / ew_min bounds
THIN_N = 20


# ------------------------------------------------------------------------------------------------------ distribution
def shift_scale(pmf, lo, mu, d, s):
    """Move each support point x of each row to mu + d + s*(x - mu); mass split between neighbouring integers (mean
    exact unless clipped at the support ends). pmf (n, K); mu, d, s (n,)."""
    pmf = np.asarray(pmf, float)
    if pmf.ndim == 1:
        return shift_scale(pmf[None, :], lo, np.atleast_1d(mu), np.atleast_1d(d), np.atleast_1d(s))[0]
    n, K = pmf.shape
    ks = lo + np.arange(K, dtype=float)
    mu = np.asarray(mu, float)[:, None]
    new = mu + np.asarray(d, float)[:, None] + np.asarray(s, float)[:, None] * (ks[None, :] - mu)
    pos = np.clip(new - lo, 0, K - 1)
    f = np.floor(pos).astype(int)
    w = pos - f
    c2 = np.minimum(f + 1, K - 1)
    out = np.zeros_like(pmf)
    rows = np.repeat(np.arange(n), K)
    np.add.at(out, (rows, f.ravel()), (pmf * (1 - w)).ravel())
    np.add.at(out, (rows, c2.ravel()), (pmf * w).ravel())
    return out


def p_over_no_push(pmf, lo, line):
    """P(Over | no push) per row, clipped to [1e-4, 1-1e-4]."""
    pmf = np.asarray(pmf, float)
    ks = lo + np.arange(pmf.shape[1], dtype=float)
    L = np.asarray(line, float)[:, None]
    po = (pmf * (ks[None, :] > L)).sum(1)
    pu = (pmf * (ks[None, :] < L)).sum(1)
    return np.clip(po / np.maximum(po + pu, 1e-12), 1e-4, 1 - 1e-4)


def cont_median(pmf, lo):
    """Continuous (interpolated) median per row."""
    pmf = np.asarray(pmf, float)
    c = np.cumsum(pmf, 1)
    j = np.argmax(c >= 0.5, axis=1)
    rows = np.arange(len(pmf))
    prev = np.where(j > 0, c[rows, np.maximum(j - 1, 0)], 0.0)
    return lo + j - 0.5 + (0.5 - prev) / np.maximum(pmf[rows, j], 1e-9)


def moments(pmf, lo):
    pmf = np.asarray(pmf, float)
    ks = lo + np.arange(pmf.shape[1], dtype=float)
    m = (pmf * ks).sum(1)
    sd = np.sqrt(np.maximum((pmf * (ks[None, :] - m[:, None]) ** 2).sum(1), 1e-12))
    return m, sd


def logloss(p_over, over):
    p = np.clip(np.asarray(p_over, float), 1e-4, 1 - 1e-4)
    return -(over * np.log(p) + (1 - over) * np.log(1 - p))


def reliability(prior_n, ew_min, starter_lag):
    """thin (prior_n < 60 or ew_min < 20) / bench (did not start last game) / starter."""
    pn = np.nan_to_num(np.asarray(prior_n, float), nan=0.0)
    em = np.nan_to_num(np.asarray(ew_min, float), nan=0.0)
    st = np.nan_to_num(np.asarray(starter_lag, float), nan=0.0)
    return np.where((pn < 60) | (em < 20), "thin", np.where(st != 1, "bench", "starter"))


# --------------------------------------------------------------------------------------------------------- the data
class Book:
    """Per-stat arrays of the props under study. `base` arrays are the sm-1 PMFs; `d`/`s` the cumulative adjustment.
    Fields: stat, line, mu, cmed, sd, over (1/0, only read for TRAINING rows), actual, minutes, feats (dict of arrays)."""

    def __init__(self, rows):
        self.n = len(rows)
        self.stat = np.array([r["stat"] for r in rows])
        self.line = np.array([float(r["line"]) for r in rows])
        self.mu = np.array([float(r["mu"]) for r in rows])
        self.actual = np.array([float(r["actual"]) for r in rows])
        self.minutes = np.array([np.nan if r.get("minutes") is None else float(r["minutes"]) for r in rows])
        self.over = (self.actual > self.line).astype(float)
        self.player_game = np.array([f'{r["event"]}|{r["player"]}' for r in rows])
        self.feats = {k: np.array([np.nan if (r["row"].get(k) is None) else float(r["row"][k]) for r in rows])
                      for k in ("prior_n", "ew_min", "l5_min", "l10_min", "starter_lag", "starter_rate10")}
        self.rel = reliability(self.feats["prior_n"], self.feats["ew_min"], self.feats["starter_lag"])
        self.lo = {}
        self.pmf = {}
        self.idx = {}
        for st in np.unique(self.stat):
            ii = np.flatnonzero(self.stat == st)
            los = {int(rows[i]["pmf_lo"]) for i in ii}
            assert len(los) == 1, "one support per stat"
            self.lo[st] = los.pop()
            self.pmf[st] = np.vstack([np.asarray(rows[i]["pmf"], float) for i in ii])
            self.idx[st] = ii
        self.cmed = np.zeros(self.n)
        self.sd = np.zeros(self.n)
        for st, ii in self.idx.items():
            self.cmed[ii] = cont_median(self.pmf[st], self.lo[st])
            self.sd[ii] = moments(self.pmf[st], self.lo[st])[1]
        self.pos = np.zeros(self.n, int)
        for st, ii in self.idx.items():
            self.pos[ii] = np.arange(len(ii))

    def p_over(self, ii, d, s):
        """P(Over | no push) of rows ii under cumulative adjustment (d, s) (arrays aligned with ii)."""
        ii = np.asarray(ii)
        out = np.zeros(len(ii))
        d = np.broadcast_to(np.asarray(d, float), ii.shape)
        s = np.broadcast_to(np.asarray(s, float), ii.shape)
        for st in np.unique(self.stat[ii]):
            m = self.stat[ii] == st
            rows = ii[m]
            pm = shift_scale(self.pmf[st][self.pos[rows]], self.lo[st], self.mu[rows], d[m], s[m])
            out[m] = p_over_no_push(pm, self.lo[st], self.line[rows])
        return out

    def state(self, ii, d, s):
        """(mean, median, sd) of rows ii after (d, s) - affine, so exact up to the integer split / clipping."""
        mu = self.mu[ii] + d
        return mu, mu + s * (self.cmed[ii] - self.mu[ii]), self.sd[ii] * s


# ----------------------------------------------------------------------------------------------------------- stages
class Stage:
    name = "identity"

    def fit(self, bk, tr, d, s):
        """tr = training rows (games ended before the test forecasts); d, s = cumulative adjustment of ALL rows from the
        earlier stages (fitted on the same past)."""
        return self

    def adjust(self, bk, ii, d, s):
        return np.zeros(len(ii)), np.ones(len(ii))


class MarketMean(Stage):
    """ma: mean' = a + b*mu + c*line per stat (OLS on past props)."""
    name = "ma"

    def fit(self, bk, tr, d, s):
        self.coef = {}
        mu, _, _ = bk.state(tr, d[tr], s[tr])
        for st in STATS:
            m = bk.stat[tr] == st
            if m.sum() < MA_MIN_ROWS:
                continue
            X = np.column_stack([np.ones(m.sum()), mu[m], bk.line[tr][m]])
            self.coef[st] = np.linalg.lstsq(X, bk.actual[tr][m], rcond=None)[0]
        return self

    def adjust(self, bk, ii, d, s):
        mu, _, _ = bk.state(ii, d[ii], s[ii])
        dd = np.zeros(len(ii))
        for st, b in self.coef.items():
            m = bk.stat[ii] == st
            new = np.maximum(b[0] + b[1] * mu[m] + b[2] * bk.line[ii][m], 0.05)
            dd[m] = new - mu[m]
        return dd, np.ones(len(ii))


class MedianAnchor(Stage):
    """md: median' = median + (1 - a)(line - median), a per stat x reliability group by past log loss."""
    name = "md"

    def _cands(self, bk, rows, d, s, a):
        mu, med, _ = bk.state(rows, d[rows], s[rows])
        return d[rows] + (1 - a) * (bk.line[rows] - med)

    def fit(self, bk, tr, d, s):
        self.a = {}
        keys = np.char.add(np.char.add(bk.stat.astype(str), "|"), bk.rel.astype(str))
        self.keys = keys
        ll = {}
        for a in MD_GRID:
            po = bk.p_over(tr, self._cands(bk, tr, d, s, a), s[tr])
            ll[a] = logloss(po, bk.over[tr])
        for k in np.unique(keys):
            m = keys[tr] == k
            if m.sum() < MD_MIN_ROWS:
                m = bk.stat[tr] == k.split("|")[0]
            if m.sum() < MD_MIN_ROWS:
                continue
            self.a[k] = min(MD_GRID, key=lambda a: ll[a][m].mean())
        return self

    def adjust(self, bk, ii, d, s):
        a = np.array([self.a.get(k, np.nan) for k in self.keys[ii]], float)
        ok = ~np.isnan(a)
        _, med, _ = bk.state(ii, d[ii], s[ii])
        dd = np.where(ok, (1 - np.nan_to_num(a, nan=1.0)) * (bk.line[ii] - med), 0.0)
        return dd, np.ones(len(ii))


class SkewSpread(Stage):
    """sk: median' = median + lam (mu - median), spread * s; (lam, s) per stat by past log loss (line-free)."""
    name = "sk"

    def fit(self, bk, tr, d, s):
        self.par = {}
        mu, med, _ = bk.state(tr, d[tr], s[tr])
        best = {}
        for lam in SK_LAM:
            for sc in SK_S:
                # the median of the scaled distribution is mu + sc*(med - mu); move it by lam*(mu - that)
                dd = d[tr] + lam * sc * (mu - med)
                ll = logloss(bk.p_over(tr, dd, s[tr] * sc), bk.over[tr])
                for st in STATS:
                    m = bk.stat[tr] == st
                    if m.sum() < MD_MIN_ROWS:
                        continue
                    v = ll[m].mean()
                    if st not in best or v < best[st][0] - 1e-12:
                        best[st] = (v, lam, sc)
        self.par = {st: (lam, sc) for st, (v, lam, sc) in best.items()}
        return self

    def adjust(self, bk, ii, d, s):
        mu, med, _ = bk.state(ii, d[ii], s[ii])
        dd = np.zeros(len(ii))
        ss = np.ones(len(ii))
        for st, (lam, sc) in self.par.items():
            m = bk.stat[ii] == st
            dd[m] = lam * sc * (mu[m] - med[m])
            ss[m] = sc
        return dd, ss


class RoleMinutes(Stage):
    """rm: minutes' = OLS(actual minutes ~ ew_min, l5_min, l10_min, starter_lag, starter_rate10) on past player-games
    (one row per player-game, minutes > 0 or a DNP counted as 0 only when the player was projected - every settled prop
    is a projected player); mean scaled by clip(minutes' / ew_min). Thin history (prior_n < 20): mean floored at the
    past per-minute rate of thin rows x minutes', spread floored at the past thin-row residual sd ratio."""
    name = "rm"
    COLS = ("ew_min", "l5_min", "l10_min", "starter_lag", "starter_rate10")

    def _X(self, bk, ii):
        f = bk.feats
        ew = f["ew_min"][ii]
        l5 = np.where(np.isnan(f["l5_min"][ii]), ew, f["l5_min"][ii])
        l10 = np.where(np.isnan(f["l10_min"][ii]), ew, f["l10_min"][ii])
        sl = np.nan_to_num(f["starter_lag"][ii], nan=0.0)
        sr = np.where(np.isnan(f["starter_rate10"][ii]), sl, f["starter_rate10"][ii])
        return np.column_stack([np.ones(len(ii)), ew, l5, l10, sl, sr])

    def fit(self, bk, tr, d, s):
        self.beta = None
        self.rate = {}
        self.sdr = {}
        ok = ~np.isnan(bk.feats["ew_min"][tr]) & ~np.isnan(bk.minutes[tr])
        t = tr[ok]
        _, first = np.unique(bk.player_game[t], return_index=True)
        t1 = t[first]
        if len(t1) >= RM_MIN_PG:
            self.beta = np.linalg.lstsq(self._X(bk, t1), bk.minutes[t1], rcond=None)[0]
        thin = tr[np.nan_to_num(bk.feats["prior_n"][tr], nan=0) < THIN_N]
        mu, _, sd = bk.state(tr, d[tr], s[tr])
        thin_m = np.nan_to_num(bk.feats["prior_n"][tr], nan=0) < THIN_N
        for st in STATS:
            m = (bk.stat[thin] == st) & (bk.minutes[thin] > 0)
            if m.sum() >= 20:
                self.rate[st] = float(np.sum(bk.actual[thin][m]) / np.sum(bk.minutes[thin][m]))
            mm = thin_m & (bk.stat[tr] == st)
            if mm.sum() >= 20:
                z = (bk.actual[tr][mm] - mu[mm]) / np.maximum(sd[mm], 0.5)
                self.sdr[st] = float(np.clip(np.sqrt(np.mean(z ** 2)), 1.0, 3.0))
        return self

    def minutes(self, bk, ii):
        if self.beta is None:
            return np.full(len(ii), np.nan)
        out = self._X(bk, ii) @ self.beta
        return np.where(np.isnan(bk.feats["ew_min"][ii]), np.nan, np.maximum(out, 0.0))

    def adjust(self, bk, ii, d, s):
        mu, _, sd = bk.state(ii, d[ii], s[ii])
        dd = np.zeros(len(ii))
        ss = np.ones(len(ii))
        mp = self.minutes(bk, ii)
        ew = bk.feats["ew_min"][ii]
        ok = ~np.isnan(mp) & (ew > 1)
        r = np.ones(len(ii))
        r[ok] = np.clip(mp[ok] / ew[ok], *RM_CLIP)
        new = mu * r
        thin = np.nan_to_num(bk.feats["prior_n"][ii], nan=0) < THIN_N
        for st in STATS:
            m = thin & (bk.stat[ii] == st)
            if st in self.rate:
                mins = np.where(np.isnan(mp[m]), 0.0, mp[m])
                new[m] = np.maximum(new[m], self.rate[st] * mins)
            if st in self.sdr:
                ss[m] = self.sdr[st]
        dd = new - mu
        return dd, ss


STAGES = {"ma": MarketMean, "md": MedianAnchor, "sk": SkewSpread, "rm": RoleMinutes}


def run(bk, spec, tr, te):
    """Fit the stages in `spec` (e.g. "rm+sk") in order on training rows tr, apply to test rows te. Returns
    (p_over_test, mean_test, fitted stages). Earlier stages are applied to tr before the next stage is fitted."""
    d = np.zeros(bk.n)
    s = np.ones(bk.n)
    fitted = []
    both = np.concatenate([tr, te])
    for name in [x for x in spec.split("+") if x and x != "live"]:
        st = STAGES[name]().fit(bk, tr, d, s)
        dd, ss = st.adjust(bk, both, d, s)
        d[both] = d[both] + dd
        s[both] = s[both] * ss
        fitted.append(st)
    return bk.p_over(te, d[te], s[te]), bk.mu[te] + d[te], fitted
