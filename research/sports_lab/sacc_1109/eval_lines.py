"""sacc-1109 book-line evaluation of the candidate layers (rows = settled service props at their own offered line,
pushes dropped; the baseline 'live' is the saved service chance, 'base' the frozen artifact re-run offline, which the
candidates sit on - so cand - base is like-for-like and cand - live is what members would see change).

Pass rule (written before any candidate was scored at a line; the diagnosis of the same window had been looked at,
so this window is DEVELOPMENT evidence, and a pass here only earns the fresh-window test):
  L1  log loss at the line below live, game-clustered 95% CI of the difference entirely < 0;
  L2  |stated - hit| on graded (C+, chosen side >= 53.45%) rows smaller than live's, AND on Over picks and on Under
      picks separately smaller than live's;
  L3  graded count >= 90% of live's;
  L4  graded hit rate >= live's (point estimate) and the CI of the difference not entirely < 0;
  L5  Over share among graded rows >= live's - 2 pts (no new Under lean).
  (A1, from the walk-forward report: all-outcome dev log score gain CI entirely > 0.)

  python -m research.sports_lab.sacc_1109.eval_lines <cand_dir> <out.json> <markets,comma> [period: dev_lines|fresh]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from research.sports_lab.sacc_1109.cand import Boot

C_BAND, B_BAND, A_BAND = 0.5345, 0.5559, 0.60


def side(p, over):
    ov = p >= 0.5
    pc = np.where(ov, p, 1 - p)
    hit = np.where(ov, over, 1 - over)
    return ov, pc, hit


def ll(p, over):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return -(over * np.log(p) + (1 - over) * np.log(1 - p))


def arm(d, col, bt):
    p, over = d[col].to_numpy(float), d["over"].to_numpy(float)
    ov, pc, hit = side(p, over)
    g = pc >= C_BAND
    r = {"n": int(len(d)), "logloss": round(float(ll(p, over).mean()), 5),
         "stated_all": round(float(pc.mean()), 4), "hit_all": round(float(hit.mean()), 4),
         "graded_n": int(g.sum()), "graded_stated": round(float(pc[g].mean()), 4),
         "graded_hit": round(float(hit[g].mean()), 4), "graded_gap": round(float((pc - hit)[g].mean()), 4),
         "graded_gap_ci": bt.ci(pc - hit, g),
         "over_share_graded": round(float(ov[g].mean()), 4), "over_share_all": round(float(ov.mean()), 4)}
    for nm, m in (("over", g & ov), ("under", g & ~ov)):
        r[f"graded_{nm}_n"] = int(m.sum())
        r[f"graded_{nm}_gap"] = round(float((pc - hit)[m].mean()), 4) if m.any() else None
        r[f"graded_{nm}_gap_ci"] = bt.ci(pc - hit, m) if m.any() else None
    ab = pc >= B_BAND
    r["AB_n"], r["AB_stated"], r["AB_hit"] = int(ab.sum()), round(float(pc[ab].mean()), 4), round(float(hit[ab].mean()), 4)
    r["bands"] = {}
    for lo, hi in ((0.5, C_BAND), (C_BAND, B_BAND), (B_BAND, A_BAND), (A_BAND, 0.65), (0.65, 0.70), (0.70, 1.01)):
        m = (pc >= lo) & (pc < hi)
        if m.sum():
            r["bands"][f"{lo:.4f}-{hi:.2f}"] = {"n": int(m.sum()), "stated": round(float(pc[m].mean()), 4),
                                                 "hit": round(float(hit[m].mean()), 4)}
    return r


def compare(d, a, b, bt):
    over = d["over"].to_numpy(float)
    pa, pb = d[a].to_numpy(float), d[b].to_numpy(float)
    _, pca, ha = side(pa, over)
    _, pcb, hb = side(pb, over)
    ga, gb = pca >= C_BAND, pcb >= C_BAND
    # graded hit difference: per-game (hits_a/n_a - hits_b/n_b) via ratio resampling
    sa = bt.C @ np.bincount(bt.g, weights=ha * ga, minlength=len(bt.ug))
    na = bt.C @ np.bincount(bt.g, weights=ga.astype(float), minlength=len(bt.ug))
    sb = bt.C @ np.bincount(bt.g, weights=hb * gb, minlength=len(bt.ug))
    nb = bt.C @ np.bincount(bt.g, weights=gb.astype(float), minlength=len(bt.ug))
    dh = sa / np.maximum(na, 1) - sb / np.maximum(nb, 1)
    dll = ll(pa, over) - ll(pb, over)
    return {"d_logloss": round(float(dll.mean()), 5), "d_logloss_ci": bt.ci(dll),
            "graded_hit_diff": round(float(ha[ga].mean() - hb[gb].mean()), 4),
            "graded_hit_diff_ci": [round(float(np.percentile(dh, 2.5)), 4), round(float(np.percentile(dh, 97.5)), 4)],
            "graded_count_ratio": round(float(ga.sum() / max(gb.sum(), 1)), 3)}


def verdict(r, live, cmp):
    L1 = cmp["d_logloss_ci"][1] < 0
    L2 = (abs(r["graded_gap"]) < abs(live["graded_gap"]) and
          abs(r["graded_over_gap"] or 0) < abs(live["graded_over_gap"] or 0) and
          abs(r["graded_under_gap"] or 0) < abs(live["graded_under_gap"] or 0))
    L3 = cmp["graded_count_ratio"] >= 0.90
    L4 = cmp["graded_hit_diff"] >= 0 and cmp["graded_hit_diff_ci"][1] > 0
    L5 = r["over_share_graded"] >= live["over_share_graded"] - 0.02
    return {"L1_logloss": L1, "L2_calibration": L2, "L3_count": L3, "L4_hit": L4, "L5_balance": L5,
            "pass": bool(L1 and L2 and L3 and L4 and L5)}


def main(cand_dir, out_json, markets, period=None):
    parts = []
    for mk in markets.split(","):
        p = Path(cand_dir) / f"lines_{mk}.pkl"
        if p.exists():
            parts.append(pd.read_pickle(p))
    d = pd.concat(parts, ignore_index=True)
    if period:
        d = d[d["period"] == period].reset_index(drop=True)
    arms = ["po_live", "po_base"] + [c for c in d.columns if c.startswith("po_r")]
    res = {"rule": __doc__.split("Pass rule", 1)[1].split("python -m", 1)[0].strip()}

    def block(dd):
        bt = Boot(dd["game_id"].to_numpy())
        out = {"n": int(len(dd)), "games": int(dd["game_id"].nunique()),
               "arms": {a: arm(dd, a, bt) for a in arms}}
        for a in arms[1:]:
            out["arms"][a]["vs_live"] = compare(dd, a, "po_live", bt)
            if a != "po_base":
                out["arms"][a]["vs_base"] = compare(dd, a, "po_base", bt)
                out["arms"][a]["verdict"] = verdict(out["arms"][a], out["arms"]["po_live"], out["arms"][a]["vs_live"])
        return out
    res["all"] = block(d)
    res["by_market"] = {mk: block(g) for mk, g in d.groupby("market")}
    Path(out_json).write_text(json.dumps(res, indent=1), encoding="utf-8")
    for a, r in res["all"]["arms"].items():
        print(f"{a:9s} n{r['n']} LL {r['logloss']:.4f} graded {r['graded_n']} st {r['graded_stated']:.3f} hit "
              f"{r['graded_hit']:.3f} gap {r['graded_gap']:+.3f} O {r['graded_over_gap']:+.3f} U {r['graded_under_gap']:+.3f}"
              f" ovsh {r['over_share_graded']:.2f} AB {r['AB_n']} {r['AB_stated']:.3f}/{r['AB_hit']:.3f}",
              r.get("vs_live", ""), r.get("verdict", ""))


if __name__ == "__main__":
    main(*sys.argv[1:5])
