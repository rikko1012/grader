"""ED-1 predictability: how much of each error component could have been known BEFORE the match?

usage: python predict.py <out dir with series.csv.gz>  ->  <out>/predict.json, <out>/pred_factors.csv.gz
For each stat x scope and each component ratio (realized / model): rounds R/R_hat (and its regulation / overtime parts), team kills
per round, share of team kills, HS per kill, and the TOTAL ratio actual / projection, a gradient-boosted regressor on pre-match-only
features (all computed strictly before the match day by build.py) is fitted on every row dated before a quarter and predicts that
quarter (2025-07 .. 2026-10-21; 6 forward folds, nothing from the scored quarter or later is used). Squared loss weighted by
projection^2, so the fit targets kills-scale squared error. Component predictions are divided by the component's
weighted TRAINING mean (a component alone must not move the projection's level: e.g. regulation rounds / 21.74 averages < 1 by
construction); 'tot' = the raw predicted total ratio (level + variation), 'totc' = the same centred (variation only). The 'fixable' part of a component = MSE / MAE reduction when the
model's projection is multiplied by the PREDICTED ratio (that component only), vs the oracle reduction with the REALIZED ratio on
the same rows. Fixed hyper-parameters (no tuning on scored folds)."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import boot, live_frame  # noqa: E402

OUT = Path(sys.argv[1])
ser = pd.read_csv(OUT / "series.csv.gz")
FEATS = [f"x{i}" for i in range(14)] + ["lan", "bo", "p2_rate", "p2_opp", "hsr", "elo_own", "elo_opp", "r_w", "re_w", "nmaps_hist",
         "tm_rpm", "tm_rws", "tm_ot", "tm_tkpr", "tm_okpr", "tm_wmaps", "tm_rest", "op_rpm", "op_rws", "op_ot", "op_tkpr", "op_okpr",
         "op_wmaps", "op_rest", "pl_share", "pl_rest", "pl_team_frac", "pl_team_maps", "roster_cont", "team_gap_days", "opp_roster_cont",
         "lam", "Rhat", "tau_hat", "s_hat", "elo_diff", "dup"]
Q = ["2025-07-01", "2025-10-01", "2026-01-01", "2026-04-01", "2026-07-01", "2026-10-01", "2026-10-22"]
HP = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=400, l2_regularization=1.0, early_stopping=False, random_state=0)
t0 = time.time()
res, preds_all, imp = {}, [], {}
for scope in ("1", "12", "123"):
    d = live_frame(ser, scope).reset_index(drop=True)
    d["elo_diff"] = d.elo_own - d.elo_opp
    d["date"] = pd.to_datetime(d.day.map(lambda x: pd.Timestamp.fromordinal(int(x))))
    tg = {"R": d.R / d.Rhat, "Rreg": d.Rreg / d.Rhat, "Rot": d.R / d.Rreg, "T": (d["T"] / d.R) / d.tau_hat,
          "S": (d.K / d["T"]) / d.s_hat, "tot": d.K / d.lam}
    hs_ok = d.HS.notna() & (d.K > 0)
    tg["H"] = pd.Series(np.where(hs_ok, (d.HS / d.K) / d.hsr, np.nan))
    tg["totH"] = pd.Series(np.where(d.HS.notna(), d.HS / d.lam_hs, np.nan))
    P = pd.DataFrame(np.nan, index=d.index, columns=list(tg) + ["totc"])
    X = d[FEATS].astype(float).values
    for qi in range(len(Q) - 1):
        tr = (d.date < Q[qi]).values
        te = ((d.date >= Q[qi]) & (d.date < Q[qi + 1])).values
        for k, y in tg.items():
            w = (d.lam_hs if k in ("H", "totH") else d.lam).values ** 2
            ok = tr & np.isfinite(y.values)
            m = HistGradientBoostingRegressor(**HP).fit(X[ok], y.values[ok], sample_weight=w[ok])
            if qi == len(Q) - 3 and k in ("tot", "R", "T", "S", "H"):   # the 2026-07 fold: which pre-match inputs carry it
                tt = np.where(te & np.isfinite(y.values))[0]
                pi = permutation_importance(m, X[tt], y.values[tt], sample_weight=w[tt], n_repeats=3, random_state=0,
                                            scoring="neg_mean_squared_error")
                o = np.argsort(-pi.importances_mean)[:8]
                imp.setdefault(scope, {})[k] = [(FEATS[i], float(pi.importances_mean[i])) for i in o]
            mu = 1.0 if k in ("tot", "totH") else float(np.average(y.values[ok], weights=w[ok]))
            P.loc[te, k] = m.predict(X[te]) / mu     # components: centred on their training mean (the projection's level is the total's job)
            if k == "tot":
                P.loc[te, "totc"] = m.predict(X[te]) / float(np.average(y.values[ok], weights=w[ok]))
        print(scope, Q[qi], int(tr.sum()), int(te.sum()), round(time.time() - t0), flush=True)
    sc = P[["R", "T", "S", "tot"]].notna().all(axis=1)
    e = d[sc].copy()
    p = P[sc]
    r = {"rows": int(len(e)), "matches": int(e.match.nunique()), "from": Q[0], "to": "2026-10-21"}
    K, lam = e.K.values.astype(float), e.lam.values
    se0, ae0 = (K - lam) ** 2, np.abs(K - lam)
    cols, rat = {"se0": se0, "ae0": ae0}, {}
    for k, real in (("R", e.R / e.Rhat), ("Rreg", e.Rreg / e.Rhat), ("Rot", e.R / e.Rreg), ("T", (e["T"] / e.R) / e.tau_hat),
                    ("S", (e.K / e["T"]) / e.s_hat), ("tot", e.K / e.lam), ("totc", e.K / e.lam)):
        pf, of = lam * p[k].values, lam * real.values
        cols[f"fix_se_{k}"] = se0 - (K - pf) ** 2
        cols[f"fix_ae_{k}"] = ae0 - np.abs(K - pf)
        cols[f"orc_se_{k}"] = se0 - (K - of) ** 2
        cols[f"fix_bias_{k}"] = (K - lam) - (K - pf)
        rat[f"fix_share_{k}"] = (f"fix_se_{k}", "se0")
        rat[f"orc_share_{k}"] = (f"orc_se_{k}", "se0")
        rat[f"pred_of_oracle_{k}"] = (f"fix_se_{k}", f"orc_se_{k}")
        r[f"corr_pred_real_{k}"] = float(np.corrcoef(p[k].values, real.values)[0, 1])
        r[f"sd_pred_{k}"] = float(np.std(p[k].values))
        r[f"sd_real_{k}"] = float(np.std(real.values))
    bk, _ = boot(cols, e.match, ratio=rat)
    r["kills"] = {"mse0": float(se0.mean()), "mae0": float(ae0.mean()), **bk}
    h = e[e.HS.notna()]
    ph = p.loc[h.index]
    HS, lh = h.HS.values.astype(float), h.lam_hs.values
    se0, ae0 = (HS - lh) ** 2, np.abs(HS - lh)
    cols, rat = {"se0": se0, "ae0": ae0}, {}
    hreal = np.where(h.K > 0, (h.HS / h.K.clip(lower=1)) / h.hsr, 1.0)
    for k, mult, real in (("H", ph["H"].values, hreal), ("totH", ph["totH"].values, (h.HS / h.lam_hs).values),
                          ("R", ph["R"].values, (h.R / h.Rhat).values)):
        pf, of = lh * mult, lh * real
        cols[f"fix_se_{k}"] = se0 - (HS - pf) ** 2
        cols[f"fix_ae_{k}"] = ae0 - np.abs(HS - pf)
        cols[f"orc_se_{k}"] = se0 - (HS - of) ** 2
        rat[f"fix_share_{k}"] = (f"fix_se_{k}", "se0")
        rat[f"orc_share_{k}"] = (f"orc_se_{k}", "se0")
        rat[f"pred_of_oracle_{k}"] = (f"fix_se_{k}", f"orc_se_{k}")
    bh, _ = boot(cols, h.match, ratio=rat)
    r["hs"] = {"rows": int(len(h)), "matches": int(h.match.nunique()), "mse0": float(se0.mean()), "mae0": float(ae0.mean()), **bh}
    # by quarter (point) for stability
    r["by_quarter"] = {}
    for qi in range(len(Q) - 1):
        mm = ((e.date >= Q[qi]) & (e.date < Q[qi + 1])).values
        s0 = ((K - lam) ** 2)[mm].sum()
        r["by_quarter"][Q[qi]] = {"rows": int(mm.sum()), "matches": int(e.match[mm].nunique()),
                                  **{f"fix_share_{k}": float(((K - lam) ** 2 - (K - lam * p[k].values) ** 2)[mm].sum() / s0) for k in ("R", "T", "S", "tot")}}
    res[scope] = r
    pp = p.copy()
    pp.columns = [f"f_{c}_{scope}" for c in pp.columns]
    pp["match"], pp["player"] = e.match.values, e.player.values
    preds_all.append(pp)
    print(scope, {k: [round(x, 4) for x in bk[f"fix_share_{k}"]] for k in ("R", "Rreg", "Rot", "T", "S", "tot")}, flush=True)

res["importance_2026Q3"] = imp
json.dump(res, open(OUT / "predict.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
pr = preds_all[0]
for x in preds_all[1:]:
    pr = pr.merge(x, on=["match", "player"], how="outer")
pr.to_csv(OUT / "pred_factors.csv.gz", index=False, compression="gzip")
print("done", round(time.time() - t0))
