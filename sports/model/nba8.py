"""nba8-1: the eight NBA prop stats sm-1 does not model - Pts+Rebs+Asts, Pts+Rebs, Pts+Asts, Rebs+Asts, steals, blocks,
Blks+Stls and turnovers (numpy only; worker and API safe).

Same shape as sm-1's base (sports/model/model.py, MODEL_CARD_NBA.md), fitted on ds-1 ESPN box scores:

  per component c in {pts, reb, ast, stl, blk, tov}:
    E[minutes]   = EW mean of prior played minutes (half-life h_min games)
    rate_c       = (S_c + k_c * r_pos_c) / (S_min + k_c)       EW sums (half-life h_c), empirical-Bayes shrinkage to the
                                                                train position-group rate r_pos_c, k_c fitted on train
    mu_c         = cal_c * E[minutes] * rate_c                  cal_c = mean factor fitted on the tune block
  outcome distribution (the sm-1 "minutes" mixture):
    rho ~ the 40 train quantiles of minutes / E[minutes]       (one shared minutes draw for every component)
    X_c | rho    : mean m_c = mu_c rho, var v_c = m_c + alpha_c m_c^2      (alpha_c by ML on train, rho known)
    combo | rho  : mean M = sum m_c, var V = sum v_c + 2 sum_{i<j} r_ij sqrt(v_i v_j) -> negative binomial with that
                   mean and variance; r_ij = within-player residual correlation of the components GIVEN minutes,
                   fitted on train game logs per position group.
  So a combo is a JOINT model: the shared minutes draw plus the fitted residual correlation - never a sum of
  independent chances (capabilities.py). A single stat is the same formula with one component.

Every number above is fitted (train / tune / refit split of REGISTER.md 4); nothing is hand-set except the grids.
The artifact (artifacts/nba8_nba.json) is frozen; forecasts read it. pOver / pUnder / pPush come from ONE PMF per prop.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from . import dists

VERSION = "nba8-1"
COMPONENTS = ("pts", "reb", "ast", "stl", "blk", "tov")
MARKETS = {
    "pra": ("pts", "reb", "ast"), "pr": ("pts", "reb"), "pa": ("pts", "ast"), "ra": ("reb", "ast"),
    "steals": ("stl",), "blocks": ("blk",), "stocks": ("blk", "stl"), "turnovers": ("tov",),
}
SUPPORT = {"pra": 130, "pr": 110, "pa": 110, "ra": 60, "steals": 15, "blocks": 15, "stocks": 20, "turnovers": 20}
POS = ("G", "F", "C")
K_GRID = (0.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0, 400.0, 800.0)
HL_RATE_GRID = (5, 10, 20, 40)
HL_MIN_GRID = (3, 5, 10)
N_RHO = 40
MIN_PRIOR_GAMES = 5                     # sm-1 validated population: >= 5 prior games and L10 minutes >= 15
MIN_L10_MIN = 15.0
ART_PATH = Path(__file__).resolve().parent / "artifacts" / "nba8_nba.json"


def pos_group(p):
    p = (p or "").upper()
    return "C" if p.startswith("C") else ("F" if p.startswith(("F", "SF", "PF")) else "G")


# ------------------------------------------------------------------------------------------------- per-player features
def _ew_state(vals, hl):
    """EW (sum, weight) over vals oldest -> newest, newest weight 1, decay 0.5 per hl rows (sm-1 ew_prior)."""
    lam = 0.5 ** (1.0 / hl)
    s = w = 0.0
    for x in vals:
        s *= lam
        w *= lam
        if x == x:
            s += x
            w += 1.0
    return s, w


def features(logs, art_or_hl):
    """logs: the player's PLAYED games before the forecast game, oldest -> newest, dicts with min + components.
    -> {"prior_n", "l10_min", "ew_min", "S_min_<c>", "S_<c>"} (S_ sums at each component's half-life)."""
    hl = art_or_hl["half_lives"] if "half_lives" in art_or_hl else art_or_hl
    mins = [float(g["min"]) for g in logs]
    out = {"prior_n": len(logs), "l10_min": float(np.mean(mins[-10:])) if mins else float("nan")}
    s, w = _ew_state(mins, hl["min"])
    out["ew_min"] = s / w if w > 0 else float("nan")
    for c in COMPONENTS:
        h = hl[c]
        sm, _ = _ew_state(mins, h)
        sc, _ = _ew_state([float(g[c]) for g in logs], h)
        out[f"S_min_{c}"] = sm
        out[f"S_{c}"] = sc
    return out


def component_means(art, feat, pos_g):
    """-> {c: mu_c} (calibrated) for one player-game; NaN when the player has no played history."""
    mus = {}
    for c in COMPONENTS:
        p = art["components"][c]
        r_pos = p["rate_pos"].get(pos_g, p["rate_all"])
        rate = (feat[f"S_{c}"] + p["k"] * r_pos) / (feat[f"S_min_{c}"] + p["k"] + 1e-9)
        mus[c] = p["cal"] * feat["ew_min"] * rate
    return mus


# ------------------------------------------------------------------------------------------------------ distribution
def _nb_rows(m, alpha, ks):
    return dists.negbin(np.asarray(m, float), np.asarray(alpha, float), 0, ks)


def corr_of(art, g, a, b):
    """Fitted residual correlation of components a and b for position group g (either key order)."""
    t = art["corr"].get(g) or {}
    return t.get(f"{a}|{b}", t.get(f"{b}|{a}", 0.0))


def pmf(art, market, mus, pos_g, independent=False):
    """mus: {c: array of calibrated means} (rows) -> PMF matrix over 0..SUPPORT[market]. independent=True drops the
    fitted residual correlation (research comparison only, never a forecast)."""
    return pmf_comps(art, MARKETS[market], SUPPORT[market], mus, pos_g, independent)


def pmf_comps(art, comps, support, mus, pos_g, independent=False):
    ks = np.arange(support + 1, dtype=float)
    rho = np.asarray(art["rho"], float)
    mu = {c: np.maximum(np.asarray(mus[c], float), 1e-4) for c in comps}
    n = len(mu[comps[0]])
    pos = np.asarray(pos_g if not isinstance(pos_g, str) else [pos_g] * n, dtype=object)
    out = np.zeros((n, len(ks)))
    for r in rho:
        m = {c: mu[c] * r for c in comps}
        v = {c: m[c] + art["components"][c]["alpha"] * m[c] ** 2 for c in comps}
        M = sum(m.values())
        V = sum(v.values())
        if not independent:
            for i in range(len(comps)):
                for j in range(i + 1, len(comps)):
                    a, b = comps[i], comps[j]
                    rij = np.array([corr_of(art, g, a, b) for g in pos])
                    V = V + 2 * rij * np.sqrt(v[a] * v[b])
        alpha = np.maximum((V - M) / np.maximum(M, 1e-6) ** 2, 1e-5)
        out += _nb_rows(M, alpha, ks)
    out /= len(rho)
    return out / np.maximum(out.sum(axis=1, keepdims=True), 1e-12)


def side_probs(pmf_row, line):
    """-> (p_over, p_under, p_push), unconditional, from one PMF row on 0..K."""
    k = np.arange(len(pmf_row))
    return (float(pmf_row[k > line].sum()), float(pmf_row[k < line].sum()), float(pmf_row[k == line].sum()))


def in_population(feat):
    return feat["prior_n"] >= MIN_PRIOR_GAMES and (feat["l10_min"] or 0) >= MIN_L10_MIN


def forecast(art, market, logs, pos_g, line):
    """One prop -> dict (mean, p_over, p_under, p_push, reason codes) or None when the player has no played history."""
    if market not in MARKETS or not logs:
        return None
    feat = features(logs, art)
    if not (feat["ew_min"] == feat["ew_min"]):
        return None
    mus = component_means(art, feat, pos_g)
    row = pmf(art, market, {c: [mus[c]] for c in MARKETS[market]}, pos_g)[0]
    po, pu, pp = side_probs(row, line)
    codes = [] if in_population(feat) else ["outside_validated_population"]
    return {"model_version": VERSION, "artifact": art.get("sha"), "market": market, "line": line,
            "mean": float(sum(mus[c] for c in MARKETS[market])), "p_over": po, "p_under": pu, "p_push": pp,
            "reason_codes": codes}


# ---------------------------------------------------------------------------------------------------------- loading
_ART = {}


def load(path=None):
    p = Path(path or ART_PATH)
    key = str(p)
    if key not in _ART:
        _ART[key] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
    return _ART[key]


def artifact_sha(obj):
    body = {k: v for k, v in obj.items() if k not in ("sha", "fitted_at")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=float).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------------------------------------- fitting (worker)
def _ew_arrays(x, starts, hl):
    """Vectorisable per-row prior EW (sum, weight) of x over each player's earlier rows (rows sorted by player, time)."""
    lam = 0.5 ** (1.0 / hl)
    n = len(x)
    S = np.zeros(n)
    W = np.zeros(n)
    s = w = 0.0
    for i in range(n):
        if starts[i]:
            s = w = 0.0
        S[i], W[i] = s, w
        s *= lam
        w *= lam
        if x[i] == x[i]:
            s += x[i]
            w += 1.0
    return S, W


def build_frame(rows, hl):
    """rows: played player-games (dict: player_id, ts, min, pos_g, components) -> dict of arrays with prior features,
    sorted by player then time."""
    rows = sorted(rows, key=lambda r: (r["player_id"], r["ts"]))
    pid = np.array([r["player_id"] for r in rows], dtype=object)
    starts = np.r_[True, pid[1:] != pid[:-1]]
    F = {"player_id": pid, "ts": np.array([r["ts"] for r in rows], float),
         "pos_g": np.array([r["pos_g"] for r in rows], dtype=object), "min": np.array([r["min"] for r in rows], float)}
    for c in COMPONENTS:
        F[c] = np.array([r[c] for r in rows], float)
    gidx = np.cumsum(starts) - 1
    first = np.flatnonzero(starts)
    F["prior_n"] = np.arange(len(rows)) - first[gidx]
    s, w = _ew_arrays(F["min"], starts, hl["min"])
    with np.errstate(invalid="ignore", divide="ignore"):
        F["ew_min"] = np.where(w > 0, s / np.maximum(w, 1e-12), np.nan)
    cache = {}
    for c in COMPONENTS:
        h = hl[c]
        if h not in cache:
            cache[h] = _ew_arrays(F["min"], starts, h)[0]
        F[f"S_min_{c}"] = cache[h]
        F[f"S_{c}"] = _ew_arrays(F[c], starts, h)[0]
    # L10 minutes (prior)
    l10 = np.full(len(rows), np.nan)
    m = F["min"]
    for i in range(len(rows)):
        a = max(first[gidx[i]], i - 10)
        if i > a:
            l10[i] = m[a:i].mean()
    F["l10_min"] = l10
    return F


def _rate_pos(F, c, mask):
    out = {}
    for g in POS:
        mm = mask & (F["pos_g"] == g)
        out[g] = float(F[c][mm].sum() / max(F["min"][mm].sum(), 1.0))
    return out


def _mu(F, c, rate_pos, k, cal=1.0):
    r_pos = np.array([rate_pos.get(g, 0.0) for g in F["pos_g"]])
    return cal * F["ew_min"] * (F[f"S_{c}"] + k * r_pos) / (F[f"S_min_{c}"] + k + 1e-9)


def poisson_dev(y, mu):
    mu = np.maximum(mu, 1e-6)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(y > 0, y * np.log(y / mu), 0.0)
    return float(np.mean(2 * (t - (y - mu))))


def _nb_alpha(y, mu):
    y = np.maximum(np.rint(y), 0)
    mu = np.maximum(mu, 1e-4)
    grid = np.exp(np.linspace(np.log(1e-4), np.log(3.0), 60))
    lls = [dists.nb_loglik(y, mu, a) for a in grid]
    i = int(np.argmax(lls))
    lo, hi = grid[max(0, i - 1)], grid[min(len(grid) - 1, i + 1)]
    for _ in range(30):
        m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if dists.nb_loglik(y, mu, m1) > dists.nb_loglik(y, mu, m2):
            hi = m2
        else:
            lo = m1
    return float((lo + hi) / 2)


def fit_components(F, fit_mask, cal=None):
    """Priors, k, alpha (rho known) and residual correlations on the rows in fit_mask (eligible, finite ew_min)."""
    rho_act = F["min"] / np.maximum(F["ew_min"], 1.0)
    q = np.quantile(rho_act[fit_mask], (np.arange(N_RHO) + 0.5) / N_RHO)
    q = q / q.mean()
    comps, resid = {}, {}
    for c in COMPONENTS:
        rp = _rate_pos(F, c, fit_mask)
        rate_all = float(F[c][fit_mask].sum() / max(F["min"][fit_mask].sum(), 1.0))
        y = F[c][fit_mask]
        best = None
        for k in K_GRID:
            d = poisson_dev(y, _mu(F, c, rp, k)[fit_mask])
            if best is None or d < best[1]:
                best = (k, d)
        k = best[0]
        mu = _mu(F, c, rp, k)[fit_mask]
        m_c = mu * rho_act[fit_mask] / np.mean(rho_act[fit_mask])
        alpha = _nb_alpha(y, m_c)
        resid[c] = (y - m_c) / np.sqrt(np.maximum(m_c + alpha * m_c ** 2, 1e-6))
        comps[c] = {"k": k, "rate_pos": rp, "rate_all": rate_all, "alpha": alpha,
                    "cal": (cal or {}).get(c, 1.0)}
    corr = {}
    pos = F["pos_g"][fit_mask]
    for g in POS:
        mm = pos == g
        corr[g] = {}
        for i, a in enumerate(COMPONENTS):
            for b in COMPONENTS[i + 1:]:
                corr[g][f"{a}|{b}"] = float(np.corrcoef(resid[a][mm], resid[b][mm])[0, 1]) if mm.sum() > 50 else 0.0
    return {"components": comps, "corr": corr, "rho": q.tolist()}


def mask_eligible(F):
    with np.errstate(invalid="ignore"):
        return (F["prior_n"] >= MIN_PRIOR_GAMES) & (F["l10_min"] >= MIN_L10_MIN) & np.isfinite(F["ew_min"])
