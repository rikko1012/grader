"""ED-1 population (a): oracle decomposition of (actual - projection) and the predictive-distribution checks.

usage: python hltv_decomp.py <out dir with series.csv.gz>  ->  <out>/hltv_decomp.json
Kills factors (multiplicative, product = actual exactly): Rreg = regulation rounds / model rounds; Rot = all rounds / regulation
rounds; Tw = team kills per round predicted from REALIZED round-win share (cubic OLS - an oracle mapping); Tr = rest of team kills
per round vs model (model team kills per round = sum of the five teammates' projections / model rounds); S = player share of team
kills vs model share (his projection / team sum). HS adds H = realized HS per kill / model HS per kill.
Reported per stat x scope: one-at-a-time, chained (order Rreg, Rot, Tw, Tr, S, H) and Shapley (all orders) changes in MSE / MAE /
bias, log loss with the spread multiplier re-fitted per substitution, and match-grouped bootstrap 95% CIs (B=2000)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (KILL_F, boot, disc_normal_cdf, disc_normal_logpmf, factors, fit_c, fit_gw, hs_mix, live_frame,  # noqa: E402
                    pred_subset, shapley_rows)

OUT = Path(sys.argv[1])
ser = pd.read_csv(OUT / "series.csv.gz")
res = {}
rng = np.random.default_rng(7)


def tier_depth(d):
    em = (d.elo_own + d.elo_opp) / 2
    q = np.nanpercentile(d.drop_duplicates("match").pipe(lambda x: (x.elo_own + x.elo_opp) / 2), [20, 40, 60, 80])
    d["tier"] = np.digitize(em, q) + 1                       # 1 = lowest-Elo fifth of matches, 5 = top fifth
    d["env"] = np.where(d.lan.isna(), "unknown", np.where(d.lan == 1, "LAN", "online"))
    d["ot_outcome"] = np.where(d[f"ot_{SC[0]}"] > 0, "OT in scope (retrospective)", "no OT (retrospective)")
    d["depth"] = pd.cut(d.r_w, [-1, 150, 500, 1500, 1e9], labels=["<150", "150-500", "500-1500", "1500+"]).astype(str)
    return d


def decompose(d, f, base, actual, names, label, c0):
    out = {"rows": int(len(d)), "matches": int(d.match.nunique())}
    e0 = actual - base
    b0, G = boot({"se": e0 ** 2, "ae": np.abs(e0), "bias": e0, "act": actual, "proj": base}, d.match)
    out["baseline"] = {"mse": b0["se"], "mae": b0["ae"], "bias": b0["bias"], "mean_actual": b0["act"][0], "mean_proj": b0["proj"][0]}
    # exact-live log loss (kills: live c; HS computed by caller) and refit-c baseline
    sh, preds = shapley_rows(base.values, actual.values, f, names)
    cols, rat = {"se0": e0 ** 2, "ae0": np.abs(e0)}, {}
    for k in names:
        cols[f"sh_se_{k}"] = sh["se"][k].values
        cols[f"sh_ae_{k}"] = sh["ae"][k].values
        cols[f"sh_b_{k}"] = sh["bias"][k].values
        one = pred_subset(base.values, f, frozenset([k]))
        cols[f"one_se_{k}"] = e0 ** 2 - (actual - one) ** 2
        cols[f"one_ae_{k}"] = np.abs(e0) - np.abs(actual - one)
        rat[f"sh_se_share_{k}"] = (f"sh_se_{k}", "se0")
        rat[f"one_se_share_{k}"] = (f"one_se_{k}", "se0")
    chain, prev = [], base.values
    S = frozenset()
    for k in names:
        S = S | {k}
        p = pred_subset(base.values, f, S)
        cols[f"ch_se_{k}"] = (actual - prev) ** 2 - (actual - p) ** 2
        cols[f"ch_ae_{k}"] = np.abs(actual - prev) - np.abs(actual - p)
        rat[f"ch_se_share_{k}"] = (f"ch_se_{k}", "se0")
        prev = p
    bb, _ = boot(cols, d.match, ratio=rat)
    out["factors"] = {}
    for k in names:
        out["factors"][k] = {m: bb[f"{m}_{k}"] for m in ("sh_se", "sh_ae", "sh_b", "one_se", "one_ae", "ch_se", "ch_ae", "sh_se_share", "one_se_share", "ch_se_share")}
        out["factors"][k]["mean_log_ratio"] = float(np.log(np.clip(f[k].values, 1e-3, None)).mean())
        out["factors"][k]["sd_log_ratio"] = float(np.log(np.clip(f[k].values, 1e-3, None)).std())
    # log loss (discretised normal, c re-fitted per substitution; integer target)
    sub = rng.choice(len(d), size=min(len(d), 60000), replace=False)
    y = actual.values
    ll = {}
    for S in [frozenset()] + [frozenset([k]) for k in names] + [frozenset(names[:i]) for i in range(2, len(names))]:
        p = np.maximum(pred_subset(base.values, f, S), 0.05)
        c = fit_c(y[sub], p[sub])
        lp = -disc_normal_logpmf(y, p, c)
        ll["+".join(sorted(S)) or "none"] = {"c": c, "nll": float(lp.mean())}
    out["logloss_refit"] = ll
    # stability by month / tier / depth: Shapley shares of MSE (point) + bootstrap for tier & depth
    st = {}
    for key in ("month", "tier", "depth", "env", "ot_outcome", "model"):
        st[key] = {}
        for gname, gi in d.groupby(key).groups.items():
            ii = d.index.get_indexer(gi)
            se0 = (e0.values[ii] ** 2)
            row = {"rows": int(len(ii)), "matches": int(d.match.iloc[ii].nunique()), "bias": float(e0.values[ii].mean()),
                   "mae": float(np.abs(e0.values[ii]).mean()), "mse": float(se0.mean())}
            for k in names:
                row[f"share_{k}"] = float(sh["se"][k].values[ii].sum() / se0.sum())
                row[f"bias_{k}"] = float(sh["bias"][k].values[ii].mean())
            if True:
                cc = {"se0": se0, "bias": e0.values[ii], "ae": np.abs(e0.values[ii])}
                rr = {}
                for k in names:
                    cc[k] = sh["se"][k].values[ii]
                    rr[f"share_{k}"] = (k, "se0")
                bq, _ = boot(cc, d.match.iloc[ii].values, ratio=rr, b=1000)
                for k in names:
                    row[f"share_{k}_ci"] = bq[f"share_{k}"]
                row["bias_ci"], row["mae_ci"] = bq["bias"], bq["ae"]
            st[key][str(gname)] = row
    out["stability"] = st
    return out


SC = [None]
for scope in ("1", "12", "123"):
    SC[0] = scope
    d = tier_depth(live_frame(ser, scope).reset_index(drop=True))
    gw, coef = fit_gw(d)
    f = factors(d, gw)
    r = {"gw_coef": coef.tolist(), "model_mix": d.model.value_counts().to_dict(),
         "realized": {"mean_R": float(d.R.mean()), "mean_Rhat": float(d.Rhat.mean()), "ot_share_series": float((d.ot > 0).mean()),
                      "mean_ot_rounds": float((d.R - d.Rreg).mean()), "mean_T": float(d["T"].mean()), "mean_Tsum_hat": float(d.Tsum.mean())}}
    # ---- kills
    kd = decompose(d, f[KILL_F], d.lam, d.K.astype(float), KILL_F, "kills", None)
    y = d.K.values.astype(float)
    lp_live = -disc_normal_logpmf(y, d.lam.values, d.c.values)
    kd["logloss_live_exact"] = float(lp_live.mean())
    # PIT (randomized) / coverage / z
    F1 = disc_normal_cdf(y - 1, d.lam.values, d.c.values)
    F0 = disc_normal_cdf(y, d.lam.values, d.c.values)
    u = F1 + rng.random(len(y)) * (F0 - F1)
    z = (y - d.lam.values) / (d.c.values * np.sqrt(np.maximum(d.lam.values, 1)))
    cov = {f"{int(q*100)}": boot({"in": ((u > (1 - q) / 2) & (u < (1 + q) / 2)).astype(float)}, d.match)[0]["in"] for q in (0.5, 0.8, 0.95)}
    kd["dist"] = {"pit_deciles": np.histogram(u, bins=10, range=(0, 1))[0].tolist(), "coverage": cov,
                  "z_mean": float(z.mean()), "z_sd": float(z.std()), "z_skew": float(((z - z.mean()) ** 3).mean() / z.std() ** 3),
                  "z_exkurt": float(((z - z.mean()) ** 4).mean() / z.std() ** 4 - 3), "p_abs_z_gt2": float((np.abs(z) > 2).mean()),
                  "pit_below_0.1": float((u < 0.1).mean()), "pit_above_0.9": float((u > 0.9).mean())}
    # spread by pre-match OT propensity, by lambda tercile, by depth (ML c on each group)
    ot_pre = (d.tm_ot + d.op_ot) / 2
    by = {}
    for nm_, key in (("ot_prematch_tercile", pd.qcut(ot_pre, 3, labels=False)), ("lambda_tercile", pd.qcut(d.lam, 3, labels=False)),
                     ("depth", d.depth), ("lan", d.lan.fillna(-1)), ("model", d.model)):
        by[nm_] = {}
        for gname, gi in d.groupby(key).groups.items():
            ii = d.index.get_indexer(gi)
            by[nm_][str(gname)] = {"rows": int(len(ii)), "c_fit": fit_c(y[ii], d.lam.values[ii]), "z_sd": float(z[ii].std()),
                                   "bias": float((y[ii] - d.lam.values[ii]).mean()), "ot_share": float((d.ot.values[ii] > 0).mean())}
    kd["spread_by"] = by
    r["kills"] = kd
    # ---- HS
    h = d[d.HS.notna()].reset_index(drop=True)
    fh = factors(h, gw)
    hd = decompose(h, fh[KILL_F + ["H"]], h.lam_hs, h.HS.astype(float), KILL_F + ["H"], "hs", None)
    yy = h.HS.values.astype(float)
    pm, cd, cd1 = [], [], []
    for s0 in range(0, len(h), 15000):
        a, b_, c_ = hs_mix(h.lam.values[s0:s0 + 15000], h.c.values[s0:s0 + 15000], h.hsr.values[s0:s0 + 15000], yy[s0:s0 + 15000])
        pm.append(a); cd.append(b_); cd1.append(c_)
    pm, cd, cd1 = map(np.concatenate, (pm, cd, cd1))
    hd["logloss_live_exact"] = float(-np.log(np.maximum(pm, 1e-300)).mean())
    u = cd1 + rng.random(len(yy)) * (cd - cd1)
    zh = (yy - h.lam_hs.values) / np.sqrt(np.maximum(h.lam_hs.values, 1))
    hd["dist"] = {"pit_deciles": np.histogram(u, bins=10, range=(0, 1))[0].tolist(),
                  "coverage": {f"{int(q*100)}": boot({"in": ((u > (1 - q) / 2) & (u < (1 + q) / 2)).astype(float)}, h.match)[0]["in"] for q in (0.5, 0.8, 0.95)},
                  "pit_below_0.1": float((u < 0.1).mean()), "pit_above_0.9": float((u > 0.9).mean()),
                  "z_over_sqrt_lambda_sd": float(zh.std()),
                  "hs_per_kill_model": float(h.hsr.mean()), "hs_per_kill_real": float(h.HS.sum() / h.K.sum())}
    r["hs"] = hd
    res[scope] = r
    print(scope, "kills", kd["rows"], kd["matches"], {k: round(v["sh_se_share"][0], 3) for k, v in kd["factors"].items()},
          "| hs", {k: round(v["sh_se_share"][0], 3) for k, v in hd["factors"].items()}, flush=True)

json.dump(res, open(OUT / "hltv_decomp.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print("done")
