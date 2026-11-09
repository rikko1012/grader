"""hit-1109: score projection candidates purely on HIT RATE at the real offered lines (owner rule 2026-11-09).

Side = Scout's lean (P(Over) >= 0.5 -> Over). Every settled prop has a lean ("all"); "graded" = chosen-side chance
>= 53.45% (C and above). Pushes / voids are already dropped by the line builders. Hit = wins / (wins + losses).

Accept rule (owner 2026-11-09, plus the same-day update):
  H1  hit rate on all props AND on graded props: point estimate up vs live, game-grouped 95% CI of the difference not
      entirely < 0;
  H2  Over picks and Under picks scored SEPARATELY: neither side's hit rate down (point estimate) vs live;
  H3  graded count >= live's;
  H4  Over share of graded picks no further from 50% than live's (no more lopsided).
  Stated-vs-hit gap, log loss and calibration are reported for information only - never a veto. Chances are never
  shrunk by any arm here.

A candidate column po_<arm> that was built on the offline re-run (po_base) is transported to the live chance with
  logit(po_<arm>_xl) = logit(po_live) + logit(po_<arm>) - logit(po_base)
so it is "what members would see" (for rebounds / assists / threes / NFL po_base == po_live already).

  python -m research.sports_lab.hit_1109.hit_eval <out.json> <pkl>[,<pkl>...] [period] [arm,arm,...]
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

C_BAND = 0.5345
B = 1000


def _logit(p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _expit(x):
    return 1 / (1 + np.exp(-x))


def transport(d, arm):
    return _expit(_logit(d["po_live"]) + _logit(d[f"po_{arm}"]) - _logit(d["po_base"]))


class GameBoot:
    def __init__(self, games, b=B, seed=1109):
        self.ug, self.g = np.unique(np.asarray(games), return_inverse=True)
        rng = np.random.default_rng(seed)
        G = len(self.ug)
        self.C = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(b)]).astype(float)

    def sums(self, x):
        return self.C @ np.bincount(self.g, weights=np.asarray(x, float), minlength=len(self.ug))

    def rate(self, hit, mask):
        return self.sums(hit * mask) / np.maximum(self.sums(mask), 1)


def _pct(v):
    return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]


def sides(p, over):
    ov = np.asarray(p, float) >= 0.5
    pc = np.where(ov, p, 1 - p)
    hit = np.where(ov, over, 1 - over).astype(float)
    return ov, pc, hit


def arm_stats(d, col, mean_col, bt):
    over = d["over"].to_numpy(float)
    p = d[col].to_numpy(float)
    ov, pc, hit = sides(p, over)
    g = pc >= C_BAND
    r = {}
    for scope, m in (("all", np.ones(len(d), bool)), ("graded", g)):
        s = {"n": int(m.sum()), "hit": round(float(hit[m].mean()), 4) if m.any() else None,
             "hit_ci": _pct(bt.rate(hit, m.astype(float))) if m.any() else None,
             "over_share": round(float(ov[m].mean()), 4) if m.any() else None,
             "stated": round(float(pc[m].mean()), 4) if m.any() else None}
        for nm, mm in (("over", m & ov), ("under", m & ~ov)):
            s[f"{nm}_n"] = int(mm.sum())
            s[f"{nm}_hit"] = round(float(hit[mm].mean()), 4) if mm.any() else None
            s[f"{nm}_hit_ci"] = _pct(bt.rate(hit, mm.astype(float))) if mm.sum() >= 5 else None
        r[scope] = s
    if mean_col and mean_col in d:
        mu, y, line = d[mean_col].to_numpy(float), d["y"].to_numpy(float), d["line"].to_numpy(float)
        e = mu - y
        r["proj"] = {"mae": round(float(np.abs(e).mean()), 3), "bias": round(float(e.mean()), 3),
                     "line_mae": round(float(np.abs(line - y).mean()), 3)}
        for nm, mm in (("over_picks", ov), ("under_picks", ~ov)):
            if mm.any():
                r["proj"][nm] = {"n": int(mm.sum()), "mae": round(float(np.abs(e[mm]).mean()), 3),
                                 "bias": round(float(e[mm].mean()), 3),
                                 "line_mae": round(float(np.abs(line - y)[mm].mean()), 3)}
    return r, ov, pc, hit


def compare(d, a, ref, bt):
    over = d["over"].to_numpy(float)
    ova, pca, ha = sides(d[a].to_numpy(float), over)
    ovr, pcr, hr = sides(d[ref].to_numpy(float), over)
    ga, gr = pca >= C_BAND, pcr >= C_BAND
    out = {"flips_all": int((ova != ovr).sum()), "flips_graded": int(((ova != ovr) & (ga | gr)).sum()),
           "graded_count_ratio": round(float(ga.sum() / max(gr.sum(), 1)), 4)}
    for scope, ma, mr in (("all", np.ones(len(d), bool), np.ones(len(d), bool)), ("graded", ga, gr)):
        for nm, xa, xr in (("", ma, mr), ("over_", ma & ova, mr & ovr), ("under_", ma & ~ova, mr & ~ovr)):
            if not xa.any() or not xr.any():
                continue
            pt = float(ha[xa].mean() - hr[xr].mean())
            v = bt.rate(ha, xa.astype(float)) - bt.rate(hr, xr.astype(float))
            out[f"{scope}_{nm}hit_diff"] = round(pt, 4)
            out[f"{scope}_{nm}hit_diff_ci"] = _pct(v)
    # the flipped rows themselves: who was right
    fl = ova != ovr
    if fl.any():
        out["flipped_rows_hit_new"] = round(float(ha[fl].mean()), 4)
    return out


def verdict(st, live, cmp):
    def lop(x):
        return abs((x or 0.5) - 0.5)
    H1 = all(cmp.get(f"{s}_hit_diff", -1) > 0 and cmp.get(f"{s}_hit_diff_ci", [-1, -1])[1] > 0 for s in ("all", "graded"))
    H2 = all((cmp.get(f"{s}_{nm}_hit_diff") is None) or cmp[f"{s}_{nm}_hit_diff"] >= 0
             for s in ("all", "graded") for nm in ("over", "under"))
    H3 = st["graded"]["n"] >= live["graded"]["n"]
    H4 = lop(st["graded"]["over_share"]) <= lop(live["graded"]["over_share"]) + 1e-9
    return {"H1_hit_up": H1, "H2_no_side_worse": H2, "H3_count": H3, "H4_balance": H4,
            "pass": bool(H1 and H2 and H3 and H4)}


def block(d, arms, ref="po_live"):
    bt = GameBoot(d["game_id"].to_numpy())
    res = {"n": int(len(d)), "games": int(d["game_id"].nunique()), "arms": {}}
    for a in arms:
        mean_col = {"po_live": "mean_base" if "mean_base" in d else "mean", "po_off": "mean",
                    "po_base": "mean_base"}.get(a, "mean_" + a[3:].replace("_xl", ""))
        st, *_ = arm_stats(d, a, mean_col if mean_col in d else None, bt)
        if a != ref:
            st["vs_live"] = compare(d, a, ref, bt)
            st["verdict"] = verdict(st, res["arms"][ref], st["vs_live"])
        res["arms"][a] = st
    return res


def load(paths, period=None):
    d = pd.concat([pd.read_pickle(p) for p in paths.split(",")], ignore_index=True)
    if period and "period" in d:
        d = d[d["period"] == period].reset_index(drop=True)
    for c in [c for c in d.columns if c.startswith("po_r")]:
        if "po_base" in d and not c.endswith("_xl"):
            d[c + "_xl"] = transport(d, c[3:])
    return d


def fmt(name, r):
    a, g = r["all"], r["graded"]
    s = (f"{name:12s} all {a['n']:5d} hit {a['hit']:.3f} O {a['over_n']}/{a['over_hit'] or 0:.3f} "
         f"U {a['under_n']}/{a['under_hit'] or 0:.3f} | graded {g['n']:5d} hit {g['hit']:.3f} "
         f"O {g['over_n']}/{g['over_hit'] or 0:.3f} U {g['under_n']}/{g['under_hit'] or 0:.3f} ovsh {g['over_share']:.2f}")
    if "vs_live" in r:
        c = r["vs_live"]
        s += (f" | d_all {c.get('all_hit_diff', 0):+.3f} {c.get('all_hit_diff_ci')} d_gr {c.get('graded_hit_diff', 0):+.3f}"
              f" {c.get('graded_hit_diff_ci')} flips {c['flips_all']} -> {r['verdict']['pass']}")
    return s


def main(out_json, paths, period=None, arms=None):
    d = load(paths, period or None)
    arms = arms.split(",") if arms else ["po_live"] + [c for c in d.columns if c.startswith("po_") and c != "po_live"]
    res = {"rule": __doc__.split("Accept rule", 1)[1].split("A candidate column", 1)[0].strip(),
           "period": period, "all": block(d, arms)}
    if "market" in d:
        res["by_market"] = {mk: block(g.reset_index(drop=True), arms) for mk, g in d.groupby("market")}
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    for k, r in res["all"]["arms"].items():
        print(fmt(k, r))
    for mk, b in res.get("by_market", {}).items():
        print("--", mk, b["n"], b["games"])
        for k, r in b["arms"].items():
            print(fmt(k, r))
    return res


if __name__ == "__main__":
    main(*sys.argv[1:5])
