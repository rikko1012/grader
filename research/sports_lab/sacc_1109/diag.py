"""sacc-1109 diagnosis at offered lines: where the stated chance runs above the hit rate.

  python -m research.sports_lab.sacc_1109.diag <lines.pkl> <out.json> [pcol=po_live]
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

B = 1000
SEED = 1109


class Boot:
    """Game-clustered bootstrap of ratio statistics (sum x / sum w per resample)."""

    def __init__(self, games, b=B, seed=SEED):
        self.ug, self.g = np.unique(np.asarray(games), return_inverse=True)
        rng = np.random.default_rng(seed)
        G = len(self.ug)
        self.C = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(b)]).astype(float)

    def ci(self, x, w=None):
        x = np.asarray(x, float)
        w = np.ones(len(x)) if w is None else np.asarray(w, float)
        s = self.C @ np.bincount(self.g, weights=x * w, minlength=len(self.ug))
        n = self.C @ np.bincount(self.g, weights=w, minlength=len(self.ug))
        v = s / np.maximum(n, 1e-9)
        return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]

    def slope_ci(self, x, y, w=None):
        """CI of the OLS slope of y on x via resampled per-game sums."""
        w = np.ones(len(x)) if w is None else np.asarray(w, float)
        S = {}
        for nm, v in (("n", w), ("x", x * w), ("y", y * w), ("xx", x * x * w), ("xy", x * y * w)):
            S[nm] = self.C @ np.bincount(self.g, weights=v, minlength=len(self.ug))
        n = S["n"]
        b = (S["xy"] - S["x"] * S["y"] / n) / (S["xx"] - S["x"] ** 2 / n)
        return [round(float(np.percentile(b, 2.5)), 3), round(float(np.percentile(b, 97.5)), 3)]


def chosen(d, pcol):
    p = d[pcol].to_numpy()
    over_side = p >= 0.5
    pc = np.where(over_side, p, 1 - p)
    hit = np.where(over_side, d["over"].to_numpy(), 1 - d["over"].to_numpy())
    return over_side, pc, hit


def summ(d, bt, pcol, mask=None):
    m = np.ones(len(d), bool) if mask is None else np.asarray(mask, bool)
    if m.sum() == 0:
        return None
    ov, pc, hit = chosen(d, pcol)
    gap = pc - hit
    w = m.astype(float)
    return {"n": int(m.sum()), "games": int(d.loc[m, "game_id"].nunique()),
            "stated": round(float(pc[m].mean()), 4), "hit": round(float(hit[m].mean()), 4),
            "gap": round(float(gap[m].mean()), 4), "gap_ci": bt.ci(gap, w),
            "over_share": round(float(ov[m].mean()), 3)}


def run(d, pcol="po_live"):
    d = d.reset_index(drop=True)
    bt = Boot(d["game_id"])
    ov, pc, hit = chosen(d, pcol)
    out = {"pcol": pcol, "all": summ(d, bt, pcol)}
    gradedA = pc >= 0.5345
    out["graded_C_plus"] = summ(d, bt, pcol, gradedA)
    out["by_band"] = {}
    for lo, hi in ((0.5, 0.5345), (0.5345, 0.56), (0.56, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.80), (0.80, 1.01)):
        out["by_band"][f"{lo:.3f}-{hi:.2f}"] = summ(d, bt, pcol, (pc >= lo) & (pc < hi))
    out["by_market"] = {mk: summ(d, bt, pcol, d["market"] == mk) for mk in sorted(d["market"].unique())}
    out["by_side"] = {"over": summ(d, bt, pcol, ov), "under": summ(d, bt, pcol, ~ov)}
    out["by_book"] = {b: summ(d, bt, pcol, d["book"] == b) for b in sorted(d["book"].unique())}
    # ---- mean vs line: how much of the model's distance from the line shows up in the result
    out["mean_vs_line"] = {}
    for mk in sorted(d["market"].unique()):
        m = (d["market"] == mk).to_numpy()
        x = (d["mean"] - d["line"]).to_numpy()[m]
        y = (d["y"] - d["line"]).to_numpy()[m]
        b = np.polyfit(x, y, 1)
        sub = Boot(d.loc[m, "game_id"])
        out["mean_vs_line"][mk] = {
            "n": int(m.sum()), "slope": round(float(b[0]), 3), "slope_ci": sub.slope_ci(x, y),
            "intercept": round(float(b[1]), 3),
            "mean_minus_line": round(float(x.mean()), 3), "actual_minus_line": round(float(y.mean()), 3),
            "actual_minus_line_ci": sub.ci(y),
            "bias_mean_minus_actual": round(float((x - y).mean()), 3), "bias_ci": sub.ci(x - y),
            "mae_model": round(float(np.abs(x - y).mean()), 3), "mae_line": round(float(np.abs(y).mean()), 3),
            "corr_x_y": round(float(np.corrcoef(x, y)[0, 1]), 3)}
    # ---- spread: z = (y - mean) / sd ; var(z) > 1 = too narrow
    z = ((d["y"] - d["mean"]) / d["sd"].clip(lower=0.3)).to_numpy()
    out["spread_var_z"] = {mk: {"var_z": round(float(np.var(z[(d["market"] == mk).to_numpy()])), 3),
                                "var_z_ci": Boot(d.loc[d["market"] == mk, "game_id"]).ci(
                                    z[(d["market"] == mk).to_numpy()] ** 2)}
                           for mk in sorted(d["market"].unique())}
    # ---- slices
    sl = {}
    q = lambda c, *e: pd.cut(d[c].astype(float), e).astype(str)  # noqa: E731
    spec = {
        "minutes_ew10": lambda: q("min_ew10", -1, 15, 22, 28, 33, 60),
        "starter_prev": lambda: d["starter_lag"].map({1.0: "starter", 0.0: "bench"}).fillna("na"),
        "minutes_volatility_sd10": lambda: q("min_sd10", -1, 3, 5, 8, 60),
        "season_games": lambda: q("season_n", -1, 0, 2, 5, 10, 100),
        "prior_games": lambda: q("prior_n", -1, 5, 10, 30, 80, 2000),
        "team_changed": lambda: d["team_changed"].fillna(0).astype(int).astype(str),
        "days_since_played": lambda: q("days_since_played", -1, 3, 7, 30, 10000),
        "abs_spread_blowout": lambda: q("abs_spread", -1, 3, 7, 11, 40),
        "nfl_abs_spread": lambda: pd.cut(d["spread_home"].astype(float).abs(), [-1, 3, 7, 40]).astype(str),
        "teammates_out_usage": lambda: q("usage_share_out", -1, 0.0001, 0.1, 0.25, 5),
        "b2b": lambda: d["b2b"].fillna(0).astype(int).astype(str),
        "total_line_pace": lambda: q("total_line", 0, 220, 230, 240, 300),
        "nfl_total_line": lambda: q("total_line", 0, 41, 45, 49, 80),
        "nfl_ew_targets": lambda: q("ew_targets", -1, 2, 4, 6, 30),
        "nfl_ew_carries": lambda: q("ew_carries", -1, 3, 8, 14, 40),
        "line_vs_mean_z": lambda: pd.cut((d["line"] - d["mean"]) / d["sd"].clip(lower=0.3),
                                         [-9, -1, -0.5, -0.2, 0.2, 0.5, 1, 9]).astype(str),
        "position": lambda: d["pos_g"].astype(str),
        "nfl_position": lambda: d["position"].astype(str),
        "market_x_side": lambda: d["market"] + ":" + np.where(chosen(d, pcol)[0], "over", "under"),
    }
    slices = {}
    for nm, fn in spec.items():
        try:
            slices[nm] = fn()
        except KeyError:
            continue
    for nm, lab in slices.items():
        sl[nm] = {}
        lab = np.array([str(x) for x in np.asarray(lab, dtype=object)], dtype=object)
        for v in sorted(set(lab.tolist())):
            m = lab == v
            if m.sum() < 80:
                continue
            s = summ(d, bt, pcol, m)
            x = (d["mean"] - d["y"]).to_numpy()
            s["bias_mean_minus_actual"] = round(float(x[m].mean()), 3)
            s["var_z"] = round(float(np.var(z[m])), 3)
            if "min_ew10" in d:
                s["minutes_mean_minus_actual"] = round(float((d["min_ew10"] - d["min"]).to_numpy()[m].mean()), 2)
            sl[nm][v] = s
    out["slices"] = sl
    return out


if __name__ == "__main__":
    d = pd.read_pickle(sys.argv[1])
    pcol = sys.argv[3] if len(sys.argv) > 3 else "po_live"
    res = run(d, pcol)
    json.dump(res, open(sys.argv[2], "w"), indent=1)
    print(json.dumps({k: res[k] for k in ("all", "graded_C_plus", "by_side", "by_market", "by_band")}, indent=0))
