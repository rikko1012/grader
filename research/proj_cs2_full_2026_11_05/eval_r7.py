"""Round 7 evaluation (research only): pd-2r (pd-1 + r7 groups) vs pd-1, rules in REGISTER_R7.md.

  python eval_r7.py fit [DROP_GROUP]   walk-forward refit; writes proj_full/r7_preds[_minus_G].pkl (never pd1 files)
  python eval_r7.py score [DROP_GROUP] all-outcome + book-line comparison vs pd1_preds.pkl -> proj_full/r7_score*.json
  python eval_r7.py table              per-prop table for every settled book-line prop -> proj_full/r7_prop_table.csv.gz"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

import dist
import model
from evaluate import (CELLS, EVAL_FROM, END, FINAL_FROM, SCOPE, STAT, _ll, boot_ci, load, month_starts, side_stats,
                      live_pmf)
from features import OUT
from features_r7 import group_columns


def load_r7():
    F = load()
    R = pd.read_pickle(os.path.join(OUT, "r7_features.pkl")).drop_duplicates(["matchid", "playerid"])
    return F.merge(R, on=["matchid", "playerid"], how="left")


def suffix(drop):
    return f"_minus_{drop}" if drop else ""


def run_fit(F, drop=None):
    base_cols = model.feature_cols(F.drop(columns=[c for c in F.columns if c.startswith("r7")]))
    r7 = [c for g, cs in group_columns().items() if g != drop for c in cs]
    cols = base_cols + r7
    ms = month_starts()
    preds = []
    for a, b in zip(ms[:-1], ms[1:]):
        train = F[F.day < a]
        test = F[(F.day >= a) & (F.day < b)]
        for stat, scope in CELLS:
            M = model.fit(train, stat, scope, cols)
            t = test[test[model.TARGET[(stat, scope)]].notna()]
            if not len(t):
                continue
            mu, v = model.predict(M, t)
            preds.append(pd.DataFrame({"matchid": t.matchid.values, "playerid": t.playerid.values, "stat": stat,
                                       "scope": scope, "mu": mu, "v": v, "fold": date.fromordinal(a).isoformat()}))
            print(date.fromordinal(a), stat, scope, "train", M["n_train"], "test", len(t), flush=True)
        if a == ms[-2]:
            # keep the last fold's models for the per-prop factor attribution
            pass
    P = pd.concat(preds, ignore_index=True)
    P.to_pickle(os.path.join(OUT, f"r7_preds{suffix(drop)}.pkl"))
    return P


def _rows(F, P, stat, scope, window):
    tcol = model.TARGET[(stat, scope)]
    pr = P[(P.stat == stat) & (P.scope == scope)][["matchid", "playerid", "mu", "v"]]
    rows = F.merge(pr, on=["matchid", "playerid"])
    return rows[(rows.day >= window[0]) & (rows.day < window[1]) & rows[tcol].notna() & rows.live_p2_lam1.notna()]


def compare(F, PA, PB, window):
    """Pooled and per-cell log-score gain of A over B (same rows), match-clustered."""
    ms, ds, cells = [], [], {}
    for stat, scope in CELLS:
        tcol = model.TARGET[(stat, scope)]
        a = _rows(F, PA, stat, scope, window)
        b = PB[(PB.stat == stat) & (PB.scope == scope)][["matchid", "playerid", "mu", "v"]].rename(columns={"mu": "mu_b", "v": "v_b"})
        r = a.merge(b, on=["matchid", "playerid"]).reset_index(drop=True)
        y = r[tcol].to_numpy().astype(int)
        la = dist.logscore(model.pmf(r.mu.to_numpy(), r.v.to_numpy(), scope), y)
        lb = dist.logscore(model.pmf(r.mu_b.to_numpy(), r.v_b.to_numpy(), scope), y)
        d = la - lb
        m = r.matchid.to_numpy()
        cells[f"{stat}_{scope}"] = {"rows": int(len(r)), "dlog": boot_ci(m, d),
                                     "mae_a": float(np.abs(r.mu - y).mean()), "mae_b": float(np.abs(r.mu_b - y).mean()),
                                     "bias_a": float((r.mu - y).mean()), "bias_b": float((r.mu_b - y).mean())}
        ms.append(m)
        ds.append(d)
    return {"pooled": boot_ci(np.concatenate(ms), np.concatenate(ds)), "cells": cells}


def line_frame(F, P, name):
    g = pd.read_csv(os.path.join(os.path.dirname(OUT), "work", "prop_grades_with_playerid.csv.gz"))
    g = g[(g.status == "graded") & (g.odds_type == "standard") & g.maps.isin(list(SCOPE)) & g.playerid.notna() & g.matchid.notna()]
    g = g.assign(matchid=g.matchid.astype(int), playerid=g.playerid.astype(int), scope=g.maps.map(SCOPE), stat=g.market.map(STAT))
    g = g[g.actual != g.line]
    out = []
    for stat, scope in CELLS:
        q = g[(g.stat == stat) & (g.scope == scope)]
        pr = P[(P.stat == stat) & (P.scope == scope)][["matchid", "playerid", "mu", "v"]]
        r = q.merge(F[["matchid", "playerid", "day"] + [c for c in F.columns if c.startswith(("live_", "cm2_", "full_"))]],
                    on=["matchid", "playerid"]).merge(pr, on=["matchid", "playerid"])
        r = r[r.live_p2_lam1.notna()].reset_index(drop=True)
        if not len(r):
            continue
        PN = model.pmf(r.mu.to_numpy(), r.v.to_numpy(), scope)
        o, u = dist.p_over_line(PN, r.line.to_numpy(float))
        r[f"p_{name}"] = o / np.maximum(o + u, 1e-12)
        r[f"mean_{name}"] = dist.pmf_mean(PN)
        PL = live_pmf(r, stat, scope)
        o, u = dist.p_over_line(PL, r.line.to_numpy(float))
        r["p_live_recon"] = o / np.maximum(o + u, 1e-12)
        r["mean_live_recon"] = dist.pmf_mean(PL)
        r["y_over"] = (r.actual > r.line).astype(float)
        out.append(r[["book", "line_id", "start_time", "player", "team", "opponent", "matchid", "playerid", "stat", "scope",
                      "line", "actual", "y_over", "p_live_recon", "mean_live_recon", f"p_{name}", f"mean_{name}", "day"]])
    return pd.concat(out, ignore_index=True)


def run_score(F, drop=None):
    PA = pd.read_pickle(os.path.join(OUT, f"r7_preds{suffix(drop)}.pkl"))
    PB = pd.read_pickle(os.path.join(OUT, "pd1_preds.pkl"))
    res = {"dev": compare(F, PA, PB, (EVAL_FROM, FINAL_FROM)), "final": compare(F, PA, PB, (FINAL_FROM, END))}
    if drop is None:
        LA = line_frame(F, PA, "r7")
        LB = line_frame(F, PB, "pd1")[["line_id", "book", "p_pd1", "mean_pd1"]]
        L = LA.merge(LB, on=["line_id", "book"]).drop_duplicates(["line_id", "book"])
        m, y = L.matchid.to_numpy(), L.y_over.to_numpy()
        res["lines"] = {"n": int(len(L)), "r7": side_stats(L.p_r7, y, m), "pd1": side_stats(L.p_pd1, y, m),
                        "live_recon": side_stats(L.p_live_recon, y, m),
                        "dlogloss_r7_minus_pd1": boot_ci(m, _ll(L.p_r7, y) - _ll(L.p_pd1, y)),
                        "dlogloss_r7_minus_live_recon": boot_ci(m, _ll(L.p_r7, y) - _ll(L.p_live_recon, y))}
        p1 = pd.read_csv(os.path.join(os.path.dirname(OUT), "work", "p1_units_with_player.csv.gz"))
        p1 = p1[p1.line_id.notna()][["line_id", "book", "p", "lam", "version"]].rename(columns={"p": "p_live_exact", "lam": "lam_live_exact"})
        J = L.merge(p1, on=["line_id", "book"]).drop_duplicates(["line_id", "book"])
        mj, yj = J.matchid.to_numpy(), J.y_over.to_numpy()
        res["p1_exact"] = {"n": int(len(J)), "live_exact": side_stats(J.p_live_exact, yj, mj), "pd1": side_stats(J.p_pd1, yj, mj),
                           "r7": side_stats(J.p_r7, yj, mj),
                           "dlogloss_r7_minus_live_exact": boot_ci(mj, _ll(J.p_r7, yj) - _ll(J.p_live_exact, yj)),
                           "dlogloss_r7_minus_pd1": boot_ci(mj, _ll(J.p_r7, yj) - _ll(J.p_pd1, yj))}
        L.merge(p1, on=["line_id", "book"], how="left").drop_duplicates(["line_id", "book"]).to_csv(
            os.path.join(OUT, "r7_line_rows.csv.gz"), index=False)
    json.dump(res, open(os.path.join(OUT, f"r7_score{suffix(drop)}.json"), "w"), indent=1, default=str)
    print(json.dumps({"dev": res["dev"]["pooled"], "final": res["final"]["pooled"]}))
    return res


if __name__ == "__main__":
    cmd = sys.argv[1]
    drop = sys.argv[2] if len(sys.argv) > 2 else None
    F = load_r7()
    if cmd == "fit":
        run_fit(F, drop)
    elif cmd == "score":
        run_score(F, drop)
