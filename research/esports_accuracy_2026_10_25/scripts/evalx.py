"""Shared evaluation for the 2026-10-25 esports accuracy register (REGISTER.md sections 4-6)."""
import numpy as np
from scipy.stats import norm
from scipy.optimize import minimize

EPS = 1e-6


def units_for_p(p):
    return np.where(p >= .80, 3.0, np.where(p >= .74, 2.0, np.where(p >= .68, 1.5, np.where(p >= .63, 1.0, np.where(p >= .55, .5, 0.0)))))


def fit_probit(z, y, X=None, ridge=0.0, start=None):
    """max likelihood of y ~ Phi(a + w z + X b); ridge on b only. returns params [a, w, b...]."""
    k = 0 if X is None else X.shape[1]
    def nll(t):
        eta = t[0] + t[1] * z + (X @ t[2:] if k else 0)
        p = np.clip(norm.cdf(eta), EPS, 1 - EPS)
        return -(y * np.log(p) + (1 - y) * np.log(1 - p)).sum() + 0.5 * ridge * (t[2:] ** 2).sum()
    t0 = np.zeros(2 + k) if start is None else start
    if start is None: t0[1] = 1.0
    r = minimize(nll, t0, method="L-BFGS-B")
    return r.x


def ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def cluster_boot(match, vals_fn, B=2000, seed=7):
    """vals_fn(idx) -> scalar; resample clusters (matches) with replacement."""
    um, inv = np.unique(match, return_inverse=True)
    groups = [np.where(inv == i)[0] for i in range(len(um))]
    rnd = np.random.default_rng(seed)
    out = []
    for _ in range(B):
        pick = rnd.integers(0, len(groups), len(groups))
        idx = np.concatenate([groups[i] for i in pick])
        v = vals_fn(idx)
        if v is not None and np.isfinite(v): out.append(v)
    out = np.sort(out)
    return out[int(.025 * len(out))], out[int(.975 * len(out)) - 1]


def staked(p, blocked):
    fav = np.maximum(p, 1 - p)
    u = units_for_p(fav)
    u[blocked] = 0.0
    return u


def stake_summary(p, y, blocked, match, name):
    u = staked(p, blocked)
    s = u > 0
    over = p >= .5
    hit = np.where(over, y, 1 - y)
    fav = np.maximum(p, 1 - p)
    n = s.sum()
    if n == 0:
        return dict(name=name, n=0)
    lo, hi = cluster_boot(match[s], lambda i: hit[s][i].mean(), B=1000)
    s57 = fav >= .57
    s57 &= ~blocked
    r = dict(name=name, n=int(n), matches=int(len(np.unique(match[s]))), w=int(hit[s].sum()), l=int(n - hit[s].sum()),
             hit=float(hit[s].mean()), ci=(float(lo), float(hi)), printed=float(fav[s].mean()),
             overs=int((over & s).sum()), unders=int((~over & s).sum()),
             units_net=float((u[s] * np.where(hit[s] == 1, 1, -1)).sum()), units_staked=float(u[s].sum()),
             n57=int(s57.sum()), hit57=float(hit[s57].mean()) if s57.sum() else None, printed57=float(fav[s57].mean()) if s57.sum() else None)
    return r


def hit_diff(pa, pb, y, blocked, match, B=1000):
    """hit rate of staked set a minus staked set b, cluster bootstrap."""
    sa, sb = staked(pa, blocked) > 0, staked(pb, blocked) > 0
    ha = np.where(pa >= .5, y, 1 - y); hb = np.where(pb >= .5, y, 1 - y)
    def f(i):
        a, b = sa[i], sb[i]
        if a.sum() == 0 or b.sum() == 0: return None
        return ha[i][a].mean() - hb[i][b].mean()
    pt = f(np.arange(len(y)))
    return pt, cluster_boot(match, f, B=B, seed=11)


def paired(pc, pl, y, match, B=2000):
    g = ll(pl, y) - ll(pc, y)
    gb = (pl - y) ** 2 - (pc - y) ** 2
    return dict(n=len(y), matches=int(len(np.unique(match))), ll_gain=float(g.mean()), ll_ci=cluster_boot(match, lambda i: g[i].mean(), B),
                brier_gain=float(gb.mean()), brier_ci=cluster_boot(match, lambda i: gb[i].mean(), B, seed=9))


def calib(p, y, blocked=None):
    fav = np.maximum(p, 1 - p); over = p >= .5; hit = np.where(over, y, 1 - y)
    rows = []
    for lo, hi in ((.5, .55), (.55, .57), (.57, .63), (.63, .68), (.68, 1.01)):
        m = (fav >= lo) & (fav < hi)
        if m.sum(): rows.append((f"{int(lo*100)}-{int(min(hi,1)*100)}", int(m.sum()), float(fav[m].mean()), float(hit[m].mean())))
    m55 = fav >= .55
    gap55 = float(fav[m55].mean() - hit[m55].mean()) if m55.sum() else None
    go = float(fav[over].mean() - hit[over].mean()) if over.sum() else None
    gu = float(fav[~over].mean() - hit[~over].mean()) if (~over).sum() else None
    # calibration slope: logistic y ~ logit(p)
    from scipy.special import logit
    x = logit(np.clip(p, 1e-4, 1 - 1e-4))
    def nll(t):
        q = 1 / (1 + np.exp(-(t[0] + t[1] * x)))
        q = np.clip(q, EPS, 1 - EPS)
        return -(y * np.log(q) + (1 - y) * np.log(1 - q)).sum()
    t = minimize(nll, [0, 1], method="L-BFGS-B").x
    return dict(bins=rows, gap55=gap55, gap_over=go, gap_under=gu, n_over=int(over.sum()), n_under=int((~over).sum()),
                cal_intercept=float(t[0]), cal_slope=float(t[1]))
