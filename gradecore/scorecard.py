"""Results scoring: hit rate, Brier, log loss, reliability bins, match-clustered bootstrap intervals, by any grouping.

A row is {"p": stated chance of the picked side, "status": win / loss / push / void / pending / None, "match_key": ...} plus
whatever it is grouped by (sport, tier, book, side). Scores use wins and losses only. Pushes, voids and pending rows are
counted apart: a push is never a win and never a loss. An interval resamples whole matches, because picks from one match
win and lose together; from fewer than 2 matches it is None.
"""
import numpy as np

REPS = 2000
SEED = 7
BIN_WIDTH = 0.05
EPS = 1e-12
STATUSES = ("win", "loss", "push", "void", "pending", None)


def cluster_ci(values, groups, reps=REPS, seed=SEED):
    """Bootstrap of a mean over whole groups. -> (mean, lo95, hi95); all None when empty, lo / hi None under 2 groups."""
    x = np.asarray(values, float)
    if len(x) == 0:
        return None, None, None
    _, inv = np.unique(np.asarray(groups, str), return_inverse=True)
    g = int(inv.max()) + 1
    if g < 2:
        return float(x.mean()), None, None
    s = np.bincount(inv, weights=x, minlength=g)
    n = np.bincount(inv, minlength=g).astype(float)
    idx = np.random.default_rng(seed).integers(0, g, size=(reps, g))
    lo, hi = np.quantile(s[idx].sum(axis=1) / n[idx].sum(axis=1), [0.025, 0.975])
    return float(x.mean()), float(lo), float(hi)


def decided(rows):
    """The win / loss rows. An unknown status raises: it must not fall silently out of the record."""
    out = []
    for r in rows:
        st = r.get("status")
        if st not in STATUSES:
            raise ValueError(f"unknown status {st!r}")
        if st in ("win", "loss"):
            out.append(r)
    return out


def brier(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def log_loss(p, y):
    p, y = np.clip(np.asarray(p, float), EPS, 1 - EPS), np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def reliability(p, y, groups, width=BIN_WIDTH, reps=REPS, seed=SEED):
    """Stated chance against hit rate in equal bins (empty bins left out)."""
    p, y, groups = np.asarray(p, float), np.asarray(y, float), np.asarray(groups, str)
    k = int(round(1.0 / width))
    idx = np.minimum(np.floor(p / width + 1e-9).astype(int), k - 1)
    out = []
    for b in np.unique(idx).tolist():
        m = idx == b
        _, lo, hi = cluster_ci(y[m], groups[m], reps, seed)
        stated, hit = float(p[m].mean()), float(y[m].mean())
        out.append({"lo": round(b * width, 6), "hi": round((b + 1) * width, 6), "n": int(m.sum()), "stated": stated,
                    "hit": hit, "gap": hit - stated, "ci": None if lo is None else [lo, hi]})
    return out


def score(rows, width=BIN_WIDTH, reps=REPS, seed=SEED):
    rows = list(rows)
    d = decided(rows)
    count = {k: sum(1 for r in rows if r.get("status") == k) for k in ("win", "loss", "push", "void")}
    out = {"n": len(d), "wins": count["win"], "losses": count["loss"], "pushes": count["push"], "voids": count["void"],
           "pending": sum(1 for r in rows if r.get("status") in ("pending", None))}
    if not d:
        return out
    p = np.array([r["p"] for r in d], float)
    y = np.array([1.0 if r["status"] == "win" else 0.0 for r in d])
    g = [str(r["match_key"]) for r in d]
    hit, lo, hi = cluster_ci(y, g, reps, seed)
    out.update({"matches": len(set(g)), "hit_rate": hit, "hit_ci": None if lo is None else [lo, hi],
                "stated": float(p.mean()), "gap": hit - float(p.mean()), "brier": brier(p, y), "log_loss": log_loss(p, y),
                "reliability": reliability(p, y, g, width, reps, seed)})
    return out


def by(rows, *keys, **kw):
    """score() per value of the grouping keys, e.g. by(rows, "sport", "tier"). Key = values joined with "|"."""
    groups = {}
    for r in rows:
        groups.setdefault("|".join(str(r.get(k)) for k in keys), []).append(r)
    return {k: score(v, **kw) for k, v in sorted(groups.items())}


def ev_ci(rows, reps=REPS, seed=SEED):
    """Mean return per unit at each row's own "break_even" (a win pays 1 / break_even - 1, a loss costs 1), wins and losses
    only, match-clustered. -> (mean, lo95, hi95)."""
    d = decided(rows)
    v = [(1.0 / r["break_even"] - 1.0) if r["status"] == "win" else -1.0 for r in d]
    return cluster_ci(v, [str(r["match_key"]) for r in d], reps, seed)
