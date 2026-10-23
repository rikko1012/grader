"""ED-1 (6) distribution shape: the live predictive distribution is a SYMMETRIC discretised normal (sd = c sqrt(lambda)); realized
kills are right-skewed (population a z-skew 0.48-0.72). Check, with NO fitted parameter: the same mean and the same variance
(the live c) but a gamma shape (skewness 2c/sqrt(lambda)), discretised to integers and conditioned >= 0. HS: the live beta-binomial
HS|kills mixed over the gamma kills pmf instead of the normal one.

usage: python dist_shape.py <out dir>  ->  <out>/dist_shape.json
Reported: P(actual > projection) vs the model's own chance at that point; mean log score of the integer actual (population a);
log loss / Brier of P(Over) at the real P1 lines (pushes excluded), with match-grouped bootstrap 95% CIs (B=2000)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import ndtr
from scipy.stats import betabinom, gamma

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import KAPPA, boot, disc_normal_cdf, disc_normal_logpmf, live_frame, logloss  # noqa: E402

OUT = Path(sys.argv[1])
ser = pd.read_csv(OUT / "series.csv.gz")
p1 = pd.read_csv(OUT / "p1.csv.gz")
p1["scope"] = p1.scope.astype(str)


def g_cdf(y, lam, c):
    """discretised gamma (mean lam, var (c sqrt(max(lam,1)))^2), conditioned >= 0: P(Y <= y)."""
    var = (c * np.sqrt(np.maximum(lam, 1.0))) ** 2
    a, sc = lam ** 2 / var, var / lam
    z0 = 1 - gamma.cdf(-0.5, a, scale=sc)                      # = 1 (support >= 0) - kept for symmetry with the normal
    return np.where(y < 0, 0.0, gamma.cdf(y + 0.5, a, scale=sc) / z0)


def g_logpmf(y, lam, c):
    return np.log(np.maximum(g_cdf(y, lam, c) - g_cdf(y - 1, lam, c), 1e-300))


def kills_pmf_grid(lam, c, kind, kmax):
    ks = np.arange(kmax + 1)
    if kind == "normal":
        sd = c * np.sqrt(np.maximum(lam, 1.0))
        pk = ndtr((ks[None, :] + 0.5 - lam[:, None]) / sd[:, None]) - ndtr((ks[None, :] - 0.5 - lam[:, None]) / sd[:, None])
    else:
        pk = g_cdf(ks[None, :], lam[:, None], c[:, None]) - g_cdf(ks[None, :] - 1, lam[:, None], c[:, None])
    return pk / pk.sum(1, keepdims=True)


def hs_cdf(lam, c, hsr, y, kind):
    """P(HS <= y) for HS|kills beta-binomial (kappa 100) over the kills pmf of the given kind; vector y (may be fractional -> floor)."""
    out = []
    for s in range(0, len(lam), 8000):
        l_, c_, h_, y_ = lam[s:s + 8000], c[s:s + 8000], np.clip(hsr[s:s + 8000], 1e-6, 1 - 1e-6), np.floor(y[s:s + 8000])
        kmax = int(np.max(l_ + 8 * c_ * np.sqrt(np.maximum(l_, 1)))) + 2
        pk = kills_pmf_grid(l_, c_, kind, kmax)
        K = np.broadcast_to(np.arange(kmax + 1)[None, :], pk.shape)
        yy = y_[:, None]
        cd = np.where(yy < 0, 0.0, np.where(yy <= K, betabinom.cdf(yy, K, (h_ * KAPPA)[:, None], ((1 - h_) * KAPPA)[:, None]), 1.0))
        out.append((pk * cd).sum(1))
    return np.concatenate(out)


res = {"a": {}, "p1": {}}
for scope in ("1", "12", "123"):
    d = live_frame(ser, scope).reset_index(drop=True)
    y, lam, c = d.K.values.astype(float), d.lam.values, d.c.values
    # chance that the actual exceeds the projection (line at the projection, half-point rule ignored: continuous comparison)
    above = (y > lam).astype(float)
    pn = 1 - disc_normal_cdf(np.floor(lam), lam, c)
    pg = 1 - g_cdf(np.floor(lam), lam, c)
    ln, lg = disc_normal_logpmf(y, lam, c), g_logpmf(y, lam, c)
    b, G = boot({"above": above, "p_normal": pn, "p_gamma": pg, "nll_normal": -ln, "nll_gamma": -lg, "d_nll": ln - lg,
                 "gap_normal": above - pn, "gap_gamma": above - pg}, d.match)
    res["a"][f"kills_{scope}"] = {"rows": int(len(d)), "matches": G, **b}
    h = d[d.HS.notna()].reset_index(drop=True)
    yh, lh = h.HS.values.astype(float), h.lam_hs.values
    an = (yh > lh).astype(float)
    pn = 1 - hs_cdf(h.lam.values, h.c.values, h.hsr.values, np.floor(lh), "normal")
    pg = 1 - hs_cdf(h.lam.values, h.c.values, h.hsr.values, np.floor(lh), "gamma")
    b, G = boot({"above": an, "p_normal": pn, "p_gamma": pg, "gap_normal": an - pn, "gap_gamma": an - pg}, h.match)
    res["a"][f"hs_{scope}"] = {"rows": int(len(h)), "matches": G, **b}
    print(scope, {k: round(v[0], 4) for k, v in res["a"][f"kills_{scope}"].items() if isinstance(v, tuple)}, flush=True)

# real lines: same mean / same c as the SAVED forecast, gamma vs normal shape
s = p1.copy()
s["lam_k"] = np.where(s.lam_kills.notna(), s.lam_kills, s.lam)
s["c_k"] = np.where(s.c_kills.notna(), s.c_kills, s.sd / np.sqrt(np.maximum(s.lam, 1)))
out, pool = {}, []
for (prop, scope), g in s.groupby(["prop", "scope"]):
    g = g.reset_index(drop=True)
    line, yv = g.line.values.astype(float), g.over.values.astype(float)
    whole = line == np.floor(line)
    up = np.where(whole, line, np.floor(line))            # Over = actual >= up + 1
    dn = np.where(whole, line - 1, np.floor(line))        # Under = actual <= dn
    if prop == "kills":
        c_ = (g.sd / np.sqrt(np.maximum(g.lam, 1))).values
        Fn_up, Fn_dn = disc_normal_cdf(up, g.lam.values, c_), disc_normal_cdf(dn, g.lam.values, c_)
        Fg_up, Fg_dn = g_cdf(up, g.lam.values, c_), g_cdf(dn, g.lam.values, c_)
    else:
        hsr = (g.lam / g.lam_k).values
        Fn_up, Fn_dn = hs_cdf(g.lam_k.values, g.c_k.values, hsr, up, "normal"), hs_cdf(g.lam_k.values, g.c_k.values, hsr, dn, "normal")
        Fg_up, Fg_dn = hs_cdf(g.lam_k.values, g.c_k.values, hsr, up, "gamma"), hs_cdf(g.lam_k.values, g.c_k.values, hsr, dn, "gamma")
    pn = (1 - Fn_up) / np.maximum((1 - Fn_up) + Fn_dn, 1e-12)
    pg = (1 - Fg_up) / np.maximum((1 - Fg_up) + Fg_dn, 1e-12)
    if prop == "kills":                                    # the live kills cap
        pn, pg = np.clip(pn, 0.22, 0.78), np.clip(pg, 0.22, 0.78)
    b, G = boot({"over": yv, "p_live": g.p_over.values, "p_normal_replay": pn, "p_gamma": pg,
                 "ll_live": logloss(g.p_over.values, yv), "ll_normal": logloss(pn, yv), "ll_gamma": logloss(pg, yv),
                 "d_ll_gamma_vs_live": logloss(g.p_over.values, yv) - logloss(pg, yv),
                 "d_br_gamma_vs_live": (g.p_over.values - yv) ** 2 - (pg - yv) ** 2,
                 "replay_abs_gap": np.abs(pn - g.p_over.values)}, g.match)
    out[f"{prop}_{scope}"] = {"units": int(len(g)), "matches": G, **b}
    pool.append(pd.DataFrame({"match": g.match, "prop": prop, "d_ll": logloss(g.p_over.values, yv) - logloss(pg, yv),
                              "d_br": (g.p_over.values - yv) ** 2 - (pg - yv) ** 2, "ll_live": logloss(g.p_over.values, yv),
                              "over": yv, "p_live": g.p_over.values, "p_gamma": pg}))
    print(prop, scope, len(g), G, {k: round(v[0], 4) for k, v in b.items()}, flush=True)
pl = pd.concat(pool)
for nm, q in (("all", pl), ("kills", pl[pl.prop == "kills"]), ("hs", pl[pl.prop == "hs"])):
    b, G = boot({c: q[c].values for c in ("d_ll", "d_br", "ll_live", "over", "p_live", "p_gamma")}, q.match.values)
    out[f"pooled_{nm}"] = {"units": int(len(q)), "matches": G, **b}
res["p1"] = out
json.dump(res, open(OUT / "dist_shape.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
