"""gu-1111 shared scoring (FROZEN_RULES.md section 0): live side / pc-1 printed letters, game-grouped bootstrap,
the owner acceptance checks for letter arms and for projection / side arms. Research only."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
PC1 = json.loads((ROOT / "sports" / "model" / "artifacts" / "chance_cal_pc1.json").read_text(encoding="utf-8"))
B_CAL = {lg: float(PC1["leagues"][lg]["theta"][1]) for lg in ("nba", "nfl")}
BANDS = (0.5345, 0.5559, 0.60)             # C, B, A (sports-gp-1)
LET = ("Lean", "C", "B", "A")
NB, SEED = 2000, 1111


def logit(p):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def expit(x):
    return 1 / (1 + np.exp(-np.asarray(x, float)))


def printed(pc, league):
    """pc-1 printed chosen-side chance from the raw chosen-side chance."""
    return expit(B_CAL[league] * logit(pc))


def band_idx(q):
    """0 Lean, 1 C, 2 B, 3 A."""
    q = np.asarray(q, float)
    return (q >= BANDS[0]).astype(int) + (q >= BANDS[1]).astype(int) + (q >= BANDS[2]).astype(int)


def side_of(po):
    """-> (is_over bool, chosen-side raw chance)."""
    po = np.asarray(po, float)
    ov = po >= 0.5
    return ov, np.where(ov, po, 1 - po)


def step_up(L, q):
    """Promote one step; Lean -> C only when the printed chance is >= 0.52."""
    L = np.asarray(L).copy()
    ok = (L > 0) | (np.asarray(q) >= 0.52)
    return np.where(ok, np.minimum(L + 1, 3), L)


def step_down(L):
    return np.maximum(np.asarray(L) - 1, 0)


class Boot:
    def __init__(self, games, b=NB, seed=SEED):
        self.ug, self.g = np.unique(np.asarray(games), return_inverse=True)
        rng = np.random.default_rng(seed)
        G = len(self.ug)
        self.C = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(b)]).astype(float)

    def _s(self, x):
        return self.C @ np.bincount(self.g, weights=np.asarray(x, float), minlength=len(self.ug))

    def rate(self, hit, mask):
        m = np.asarray(mask, float)
        return self._s(np.asarray(hit, float) * m) / np.maximum(self._s(m), 1e-9)

    def diff_ci(self, hit_a, mask_a, hit_b, mask_b):
        d = self.rate(hit_a, mask_a) - self.rate(hit_b, mask_b)
        return [round(float(np.percentile(d, 2.5)) * 100, 2), round(float(np.percentile(d, 97.5)) * 100, 2)]


def _r(hit, m):
    m = np.asarray(m, bool)
    return float(np.asarray(hit, float)[m].mean()) if m.any() else float("nan")


def _pct(x):
    return None if x != x else round(100 * x, 2)


def tier_stats(hit, ov, mask):
    mask = np.asarray(mask, bool)
    n = int(mask.sum())
    return {"n": n, "hit": _pct(_r(hit, mask)), "over_n": int((mask & ov).sum()), "under_n": int((mask & ~ov).sum()),
            "over_hit": _pct(_r(hit, mask & ov)), "under_hit": _pct(_r(hit, mask & ~ov)),
            "over_share": _pct(float((mask & ov).sum() / n)) if n else None}


def _lop(share):
    return abs((share if share is not None else 50.0) - 50.0)


def _nl(a, b):
    """a not lower than b (None-safe: a missing tier side counts as not lower only when b is missing too)."""
    if b is None:
        return True
    return a is not None and a >= b - 1e-9


def near_miss_games(bt, d_pts, ci):
    """Games needed for the CI to clear 0 at the observed effect and spread."""
    if d_pts is None or d_pts <= 0 or ci is None:
        return None
    hw = (ci[1] - ci[0]) / 2
    return int(np.ceil(len(bt.ug) * (hw / d_pts) ** 2)) if d_pts > 0 else None


def letter_arm(bt, hit, ov, L0, L1):
    """Letter arm (sides unchanged): baseline letters L0 vs arm letters L1 -> stats + H1-H4 (frozen letter-arm rule)."""
    out = {"changed": int((L0 != L1).sum()), "up": int((L1 > L0).sum()), "down": int((L1 < L0).sum())}
    for tier, k in (("graded", 1), ("top", 2)):
        b, a = tier_stats(hit, ov, L0 >= k), tier_stats(hit, ov, L1 >= k)
        d = None if a["hit"] is None or b["hit"] is None else round(a["hit"] - b["hit"], 2)
        ci = bt.diff_ci(hit, L1 >= k, hit, L0 >= k) if a["n"] and b["n"] else None
        out[tier] = {"base": b, "arm": a, "diff": d, "ci": ci, "games_needed": near_miss_games(bt, d, ci)}
    g, t = out["graded"], out["top"]
    nd = lambda x: x["diff"] is not None and x["diff"] >= 0
    up = lambda x: x["diff"] is not None and x["diff"] > 0 and x["ci"] is not None and x["ci"][0] > 0
    H1 = nd(g) and nd(t) and (up(g) or up(t))
    H2 = all(_nl(x["arm"][f"{s}_hit"], x["base"][f"{s}_hit"]) for x in (g, t) for s in ("over", "under"))
    H3 = g["arm"]["n"] >= g["base"]["n"] and t["arm"]["n"] >= t["base"]["n"]
    H4 = all(_lop(x["arm"]["over_share"]) <= _lop(x["base"]["over_share"]) + 1e-9 for x in (g, t))
    out["checks"] = {"H1": bool(H1), "H2": bool(H2), "H3": bool(H3), "H4": bool(H4)}
    out["pass"] = bool(H1 and H2 and H3 and H4)
    out["failed"] = [k for k, v in out["checks"].items() if not v]
    return out


def side_arm(bt, over, po0, po1, league, fresh=None):
    """Projection / side arm: baseline raw P(Over) po0 vs arm po1 (letters from each arm's own printed chance)."""
    over = np.asarray(over, float)
    ov0, pc0 = side_of(po0)
    ov1, pc1 = side_of(po1)
    h0, h1 = np.where(ov0, over, 1 - over), np.where(ov1, over, 1 - over)
    L0, L1 = band_idx(printed(pc0, league)), band_idx(printed(pc1, league))
    allm = np.ones(len(over), bool)
    out = {"flips": int((ov0 != ov1).sum()), "letters_changed": int((L0 != L1).sum())}
    for tier, m0, m1 in (("all", allm, allm), ("graded", L0 >= 1, L1 >= 1), ("top", L0 >= 2, L1 >= 2)):
        b, a = tier_stats(h0, ov0, m0), tier_stats(h1, ov1, m1)
        d = None if a["hit"] is None or b["hit"] is None else round(a["hit"] - b["hit"], 2)
        ci = bt.diff_ci(h1, m1, h0, m0) if a["n"] and b["n"] else None
        out[tier] = {"base": b, "arm": a, "diff": d, "ci": ci, "games_needed": near_miss_games(bt, d, ci)}
    # information only: raw-band graded (the Nov 9 research rule) and log loss
    r0, r1 = pc0 >= BANDS[0], pc1 >= BANDS[0]
    out["raw_graded"] = {"base_n": int(r0.sum()), "arm_n": int(r1.sum()), "base_hit": _pct(_r(h0, r0)),
                         "arm_hit": _pct(_r(h1, r1)), "ci": bt.diff_ci(h1, r1, h0, r0) if r0.any() and r1.any() else None}
    ll = lambda p: float(-(over * np.log(np.clip(p, 1e-4, 1)) + (1 - over) * np.log(np.clip(1 - p, 1e-4, 1))).mean())
    out["logloss"] = {"base": round(ll(np.asarray(po0, float)), 5), "arm": round(ll(np.asarray(po1, float)), 5)}
    a, g = out["all"], out["graded"]
    H1 = (a["diff"] or 0) > 0 and (g["diff"] or 0) > 0 and g["ci"] is not None and g["ci"][0] > 0
    H2 = all(_nl(x["arm"][f"{s}_hit"], x["base"][f"{s}_hit"]) for x in (a, g) for s in ("over", "under"))
    H3 = g["arm"]["n"] >= g["base"]["n"]
    H4 = _lop(g["arm"]["over_share"]) <= _lop(g["base"]["over_share"]) + 1e-9
    out["checks"] = {"H1": bool(H1), "H2": bool(H2), "H3": bool(H3), "H4": bool(H4)}
    if fresh is not None and np.asarray(fresh).any():
        f = np.asarray(fresh, bool)
        d = _r(h1, f & (L1 >= 1)) - _r(h0, f & (L0 >= 1))
        out["fresh_graded_diff"] = _pct(d)
        out["fresh_all_diff"] = _pct(_r(h1, f) - _r(h0, f))
        out["checks"]["fresh"] = bool(d == d and d >= 0)
    out["pass"] = bool(all(out["checks"].values()))
    out["failed"] = [k for k, v in out["checks"].items() if not v]
    return out
