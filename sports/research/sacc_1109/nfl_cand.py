"""sacc-1109 NFL: the same candidate layers as cand.py (rs-1 role spread, rm-1 role mean, rsm-1 both) with NFL role
inputs. Windows from nfl_wf (dev = monthly in-season 2023-09 .. 2026-02, final = weekly 2026 season). The layer for a
window is fitted on the out-of-sample predictions of all EARLIER windows (dev and final), never on book lines.

  python -m sports.research.sacc_1109.nfl_cand <nflwf_dir> <nfl_lines.pkl> <out_dir>
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from sports.research.sacc_1109 import cand as C

MARKETS = ("rec", "rec_yds", "rush_att", "rush_yds", "pass_yds")
EVAL_FROM = "2024-09-01"
OPP = {"rec": "targets", "rec_yds": "targets", "rush_att": "carries", "rush_yds": "carries", "pass_yds": "pass_att"}


def load(wf_dir, mk):
    parts = []
    for p in sorted(Path(wf_dir).glob(f"sm1_{mk}__dev_*.npz")) + sorted(Path(wf_dir).glob(f"sm1frozen_{mk}__final_*.npz")):
        z = np.load(p)
        d = pd.DataFrame({k[2:]: z[k] for k in z.files if k.startswith("f_")})
        d["key"], d["y"], d["mean"], d["elig"], d["window"] = z["keys"], z["y"], z["mean"], z["elig"], p.stem.split("__")[1]
        d["_pmf"] = list(z["pmf"])
        lo = int(z["lo"])
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    P = np.stack(d.pop("_pmf").to_numpy()).astype(np.float64)
    P /= P.sum(1, keepdims=True)
    return d, P, lo


def _g(d, c, fill):
    return np.nan_to_num(d[c].to_numpy(float), nan=fill) if c in d else np.full(len(d), fill)


def sigma_X(d, mk):
    o = OPP[mk]
    ew = np.maximum(_g(d, f"ew_{o}", 1.0), 0.3)
    lag1 = _g(d, f"lag1_{o}", np.nan)
    lag1 = np.where(np.isfinite(lag1), lag1, ew)
    e2, e8 = _g(d, f"e2_{o}", np.nan), _g(d, f"e8_{o}", np.nan)
    e2, e8 = np.where(np.isfinite(e2), e2, ew), np.where(np.isfinite(e8), e8, ew)
    snap = _g(d, "ew_offense_pct", 0.5)
    l3 = _g(d, "l3_snap", np.nan)
    l3 = np.where(np.isfinite(l3), l3, snap)
    pos = _g(d, "pos", 4)
    pn = np.maximum(_g(d, "prior_n", 1.0), 1.0)
    return np.column_stack([np.ones(len(d)), np.abs(np.log((lag1 + 1) / (ew + 1))), np.abs(np.log((e2 + 1) / (e8 + 1))),
                            np.abs(l3 - snap), np.log(ew), 1 / np.sqrt(pn), (_g(d, "n_season", 0) <= 2).astype(float),
                            _g(d, "back_from_absence", 0), _g(d, "team_change", 0), _g(d, "qb_change_lag", 0),
                            np.abs(_g(d, "spread_home", 3.0)) / 7.0, (pos == 1).astype(float),
                            (pos == 3).astype(float), (pos == 0).astype(float)])


SIGMA_NAMES = ["const", "abs_log_lag1_vs_ew_opp", "abs_log_e2_vs_e8_opp", "abs_snap_trend", "log_ew_opp",
               "inv_sqrt_prior_n", "early_season", "back_from_absence", "team_change", "qb_change", "abs_spread",
               "RB", "TE", "QB"]


def mean_Z(d, mk):
    o = OPP[mk]
    ew = np.maximum(_g(d, f"ew_{o}", 1.0), 0.3)
    lag1 = _g(d, f"lag1_{o}", np.nan)
    lag1 = np.where(np.isfinite(lag1), lag1, ew)
    e2 = _g(d, f"e2_{o}", np.nan)
    e2 = np.where(np.isfinite(e2), e2, ew)
    snap = _g(d, "ew_offense_pct", 0.5)
    l3 = _g(d, "l3_snap", np.nan)
    l3 = np.where(np.isfinite(l3), l3, snap)
    imp = _g(d, "implied_total", 22.0)
    return np.column_stack([np.ones(len(d)), np.log((lag1 + 1) / (ew + 1)), np.log((e2 + 1) / (ew + 1)), l3 - snap,
                            _g(d, "team_spread", 0.0) / 7.0, (imp - 22.0) / 5.0, _g(d, "back_from_absence", 0),
                            _g(d, "qb_change_lag", 0), _g(d, "team_change", 0)])


MEAN_NAMES = ["const", "log_lag1_vs_ew_opp", "log_e2_vs_ew_opp", "snap_trend", "team_spread", "implied_total",
              "back_from_absence", "qb_change", "team_change"]


def walk(d, P, lo, mk):
    wins = sorted(d["window"].unique(), key=lambda w: w.split("_", 1)[1])
    start = d["window"].str.split("_", n=1).str[1].to_numpy()
    out = {c: P.copy() for c in ("rs1", "rm1", "rsm1")}
    params = {}
    y, mu = d["y"].to_numpy(float), d["mean"].to_numpy(float)
    X, Z = sigma_X(d, mk), mean_Z(d, mk)
    L_all = C.lik_matrix(P, lo, y)
    for w in wins:
        ws = w.split("_", 1)[1]
        if ws < EVAL_FROM:
            continue
        tr = start < ws
        te = (d["window"] == w).to_numpy()
        th = C.fit_sigma(L_all[tr], X[tr])
        sig = np.exp(np.clip(X[te] @ th, -8, 1.0))
        out["rs1"][te] = C.apply_mix(P[te], lo, sig)
        g = C.fit_mean(np.maximum(y[tr], 0), np.maximum(np.abs(mu[tr]), 0.05), Z[tr]) if mk != "rush_yds" and mk != "rec_yds" and mk != "pass_yds" else \
            C.fit_mean(np.maximum(y[tr], 0), np.maximum(mu[tr], 0.5), Z[tr])
        sh = np.exp(np.clip(Z[te] @ g, -2, 2))
        out["rm1"][te] = C.remap_rows(P[te], lo, sh)
        tri = np.flatnonzero(tr)[C._sub(int(tr.sum()))]
        sh_tr = np.exp(np.clip(Z[tri] @ g, -2, 2))
        th2 = C.fit_sigma(C.lik_matrix(P[tri], lo, y[tri], shift=sh_tr), X[tri])
        sig2 = np.exp(np.clip(X[te] @ th2, -8, 1.0))
        out["rsm1"][te] = C.apply_mix(P[te], lo, sig2, shift=sh)
        params[w] = {"rs1_theta": dict(zip(SIGMA_NAMES, np.round(th, 4).tolist())),
                     "rs1_sigma_test_median": round(float(np.median(sig)), 4),
                     "rm1_gamma": dict(zip(MEAN_NAMES, np.round(g, 4).tolist())),
                     "rsm1_theta": dict(zip(SIGMA_NAMES, np.round(th2, 4).tolist())),
                     "n_train": int(tr.sum()), "n_test": int(te.sum())}
        print(mk, w, params[w]["n_train"], params[w]["n_test"], "sig med", params[w]["rs1_sigma_test_median"], flush=True)
    return out, params


def main(wf_dir, lines_pkl, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = pd.read_pickle(lines_pkl)
    report = {"eval_from": EVAL_FROM, "markets": {}}
    import os
    for mk in (os.environ.get("SACC_MARKETS") or ",".join(MARKETS)).split(","):
        t0 = time.time()
        d, P, lo = load(wf_dir, mk)
        Cd, params = walk(d, P, lo, mk)
        games = d["key"].str.split("|").str[0].to_numpy()
        ws = d["window"].str.split("_", n=1).str[1]
        dev = (d["window"].str.startswith("dev_") & (ws >= EVAL_FROM)).to_numpy()
        fin = d["window"].str.startswith("final_").to_numpy()
        el = d["elig"].to_numpy(bool)
        rep = {"params": params,
               "dev_all": C.all_outcome_report(d, P, lo, Cd, dev, games),
               "dev_population": C.all_outcome_report(d, P, lo, Cd, dev & el, games),
               "final_all": C.all_outcome_report(d, P, lo, Cd, fin, games)}
        lm = lines[lines["market"] == mk]
        idx = pd.Series(np.flatnonzero(fin), index=d.loc[fin, "key"].to_numpy())
        idx = idx[~idx.index.duplicated()]
        lm = lm[lm["key"].isin(idx.index).to_numpy()].copy()
        ii = idx.loc[lm["key"]].to_numpy()
        ks = lo + np.arange(P.shape[1], dtype=float)
        Lr = lm["line"].to_numpy(float)[:, None]
        for nm, Q in [("base", P)] + list(Cd.items()):
            q = Q[ii]
            po, pu = (q * (ks[None, :] > Lr)).sum(1), (q * (ks[None, :] < Lr)).sum(1)
            lm[f"po_{nm}"] = po / np.maximum(po + pu, 1e-12)
            lm[f"mean_{nm}"] = q @ ks
        lm.to_pickle(out_dir / f"lines_{mk}.pkl")
        rep["book_rows_matched"] = int(len(lm))
        report["markets"][mk] = rep
        (out_dir / f"report_{mk}.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
        print(mk, "done", round(time.time() - t0), json.dumps({k: rep[k] for k in ("dev_all", "final_all")})[:1500],
              flush=True)
    (out_dir / "cand_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:4])
