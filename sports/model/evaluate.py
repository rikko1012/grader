"""Scores, game-clustered / date-block bootstrap, reliability and slices exactly as REGISTER.md section 6 declares."""
from __future__ import annotations

import numpy as np

from . import dists

SEED = 20261023
B = 2000


def scores(pmf, lo, y, mean, l_ref):
    """Per-row metric arrays for one candidate on one market."""
    y = np.asarray(y, float)
    p_over = dists.over_prob(pmf, lo, l_ref)
    hit = (y > l_ref).astype(float)
    pc = np.clip(p_over, 1e-6, 1 - 1e-6)
    return {
        "ae": np.abs(mean - y), "se": (mean - y) ** 2, "err": mean - y,
        "crps": dists.crps(pmf, lo, y), "nll": dists.nll(pmf, lo, y),
        "p_over_ref": p_over, "hit_ref": hit,
        "logloss_ref": -(hit * np.log(pc) + (1 - hit) * np.log(1 - pc)),
        "brier_ref": (p_over - hit) ** 2,
        "cov50": dists.pit_cover(pmf, lo, y, 0.5), "cov80": dists.pit_cover(pmf, lo, y, 0.8),
        "cov90": dists.pit_cover(pmf, lo, y, 0.9),
        "cov80_inclusive_interval": dists.central_cover(pmf, lo, y, 0.8),
    }


def binary_scores(p_over, y, l_ref):
    """B0 at the reference line (no full PMF needed for these)."""
    y = np.asarray(y, float)
    hit = (y > l_ref).astype(float)
    pc = np.clip(p_over, 1e-6, 1 - 1e-6)
    return {"p_over_ref": p_over, "hit_ref": hit, "logloss_ref": -(hit * np.log(pc) + (1 - hit) * np.log(1 - pc)),
            "brier_ref": (p_over - hit) ** 2}


def _groups(keys):
    u, inv = np.unique(np.asarray(keys), return_inverse=True)
    return u, inv


def boot_mean(x, groups, b=B, seed=SEED):
    """Cluster bootstrap of a mean: resample whole groups (games or date blocks). -> (mean, lo95, hi95)."""
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    x, groups = x[ok], np.asarray(groups)[ok]
    if len(x) == 0:
        return None, None, None
    _, inv = _groups(groups)
    G = inv.max() + 1
    s = np.bincount(inv, weights=x, minlength=G)
    n = np.bincount(inv, minlength=G).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, G, size=(b, G))
    bs = s[idx].sum(axis=1) / n[idx].sum(axis=1)
    lo, hi = np.quantile(bs, [0.025, 0.975])
    return float(x.mean()), float(lo), float(hi)


def summarize(sc, games, blocks=None):
    out = {"n": int(len(next(iter(sc.values())))), "games": int(len(np.unique(games)))}
    for k, v in sc.items():
        if k in ("p_over_ref", "hit_ref"):
            continue
        m, lo, hi = boot_mean(v, games)
        out[k] = {"mean": m, "ci": [lo, hi]}
    if "err" in sc:
        out["rmse"] = float(np.sqrt(np.mean(sc["se"])))
    out["calibration"] = reliability(sc["p_over_ref"], sc["hit_ref"])
    return out


def paired(a, b, games, blocks=None, metric="crps"):
    """Candidate a minus baseline b on the same rows: game-clustered (and date-block) 95% intervals."""
    d = np.asarray(a[metric], float) - np.asarray(b[metric], float)
    m, lo, hi = boot_mean(d, games)
    out = {"diff": m, "ci_game": [lo, hi]}
    if blocks is not None:
        _, blo, bhi = boot_mean(d, blocks, seed=SEED + 1)
        out["ci_date_block"] = [blo, bhi]
    return out


def reliability(p, hit, bins=10, min_n=50):
    p, hit = np.asarray(p, float), np.asarray(hit, float)
    idx = np.minimum((p * bins).astype(int), bins - 1)
    rows, ece, n_all = [], 0.0, len(p)
    for j in range(bins):
        m = idx == j
        n = int(m.sum())
        if n == 0:
            continue
        mp, mh = float(p[m].mean()), float(hit[m].mean())
        rows.append({"bin": [j / bins, (j + 1) / bins], "n": n, "mean_p": mp, "hit": mh})
        if n >= min_n:
            ece += n * abs(mp - mh)
    return {"bins": rows, "ece": ece / n_all if n_all else None,
            "in_the_large": float(p.mean() - hit.mean()) if n_all else None}


def slice_masks(df, sport, p_over_ref):
    """The declared slices (REGISTER.md 6) -> {name: bool mask}."""
    out = {}
    out["side:over"] = p_over_ref > 0.5
    out["side:under"] = p_over_ref <= 0.5
    if sport == "nba":
        sl = np.asarray(df["starter_lag"], float)
        out["role:started_prev"] = sl == 1
        out["role:bench_prev"] = sl == 0
        n = np.asarray(df["prior_n"])
        for a, b in ((5, 10), (10, 20), (20, 40), (40, 10 ** 6)):
            out[f"depth:{a}-{b - 1 if b < 10 ** 6 else '+'}"] = (n >= a) & (n < b)
    else:
        pos = np.asarray(df["position"])
        for p in ("QB", "RB", "WR", "TE"):
            out[f"role:{p}"] = pos == p
        n = np.asarray(df["prior_n"])
        for a, b in ((3, 6), (6, 12), (12, 10 ** 6)):
            out[f"depth:{a}-{b - 1 if b < 10 ** 6 else '+'}"] = (n >= a) & (n < b)
    out["band:<0.4"] = p_over_ref < 0.4
    out["band:0.4-0.6"] = (p_over_ref >= 0.4) & (p_over_ref < 0.6)
    out["band:>=0.6"] = p_over_ref >= 0.6
    st = np.asarray(df["season_type"])
    out["season:regular"] = st == "regular"
    out["season:postseason"] = (st == "postseason") | (st == "play_in")
    return out
