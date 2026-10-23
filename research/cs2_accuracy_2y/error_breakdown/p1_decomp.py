"""ED-1 population (b): the real offered P1 lines (matches starting before 2026-10-22 00:00 UTC).

usage: python p1_decomp.py <out dir>  (reads p1.csv.gz, series.csv.gz, hltv_decomp.json, pred_factors.csv.gz) -> <out>/p1_decomp.json
- projection error of the SAVED projection decomposed with the same oracle factors as population (a) (Shapley / one-at-a-time /
  chained; Tw mapping and the per-substitution spread multipliers taken from population (a), never fitted on P1);
- probability at the offered line: live saved chance, a discretised-normal baseline with population (a)'s re-fitted spread, and the
  same with each oracle substitution -> log loss / Brier (pushes are already excluded by P1);
- calibration of the live chance (bins, logistic slope / intercept), PIT / interval coverage of the saved distribution;
- the 'fixable' check: the saved projection x the PRE-MATCH predicted ratio (predict.py, forward folds) at the line;
- line freshness / scope / identity / settlement counts (AU-1 owns the full audit)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (KILL_F, boot, disc_normal_cdf, fit_gw, hs_mix, live_frame, logloss, p_over_line, pred_subset,  # noqa: E402
                    shapley_rows)

OUT = Path(sys.argv[1])
p1 = pd.read_csv(OUT / "p1.csv.gz")
p1["scope"] = p1.scope.astype(str)
ser = pd.read_csv(OUT / "series.csv.gz")
hd = json.load(open(OUT / "hltv_decomp.json"))
pred = pd.read_csv(OUT / "pred_factors.csv.gz")
res = {"units_pre_cutoff": int(len(p1)), "matches_pre_cutoff": int(p1.match.nunique())}
rng = np.random.default_rng(11)

# ------------------------------------------------------------------ (7) coverage / identity / settlement / freshness
iss = {}
iss["non_hltv_match_key"] = int((~p1.match.str.startswith("hltv:")).sum())
iss["no_hltv_player_id"] = int((p1.match.str.startswith("hltv:") & p1.player_found.isna()).sum())
iss["match_not_in_export"] = int((p1.hltv_in_export == 0).sum())
iss["player_not_on_scoreboard"] = int((p1.player_found == 0).sum())
iss["realized_found"] = int(p1.K.notna().sum())
iss["incomplete_scoreboard"] = int((p1.complete == 0).sum())
k_ = p1[p1.K.notna()]
iss["settle_mismatch_kills"] = int(((k_.prop == "kills") & (k_.actual != k_.K)).sum())
iss["settle_mismatch_hs"] = int(((k_.prop == "hs") & (k_.actual != k_.HS)).sum())
iss["opponent_mismatch"] = int((k_.opp_matches_saved == 0).sum())
iss["standin_flag"] = int((p1.is_standin == True).sum() + (p1.standin_for == True).sum())  # noqa: E712
iss["roster_not_confirmed"] = int((p1.roster_confirmed == False).sum())  # noqa: E712
iss["by_source"] = p1.source.fillna("none").value_counts().to_dict()
iss["by_source_unmatched"] = p1[p1.K.isna()].source.fillna("none").value_counts().to_dict()
res["issues"] = iss

p1["fav_hit"] = np.where(p1.p_over >= 0.5, p1.over, 1 - p1.over)
p1["ll_live"] = logloss(p1.p_over.values, p1.over.values)
p1["brier_live"] = (p1.p_over - p1.over) ** 2
fr = {}
for nm, key, bins in (("lead_min", "lead_min", [-1e9, 60, 180, 720, 1e9]), ("quote_age_min", "quote_age_min", [-1e9, 10, 60, 240, 1e9])):
    fr[nm] = {}
    cut = pd.cut(p1[key], bins)
    for g, gi in p1.groupby(cut, observed=True).groups.items():
        s = p1.loc[gi]
        b, G = boot({"ll": s.ll_live, "hit": s.fav_hit, "printed": np.maximum(s.p_over, 1 - s.p_over), "bias": s.actual - s.lam}, s.match)
        fr[nm][str(g)] = {"units": int(len(s)), "matches": G, **b}
    fr[nm]["missing"] = int(p1[key].isna().sum())
res["freshness"] = fr
src = {}
for g, s in p1.groupby(p1.source.fillna("none")):
    b, G = boot({"ll": s.ll_live, "hit": s.fav_hit, "printed": np.maximum(s.p_over, 1 - s.p_over), "bias": s.actual - s.lam}, s.match)
    src[g] = {"units": int(len(s)), "matches": G, **b}
res["by_source_all_units"] = src

# ------------------------------------------------------------------ calibration of the live chance (all pre-cutoff units)
def logit_fit(p, y, w=None):
    x = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
    w = np.ones_like(x) if w is None else w
    b = np.zeros(2)
    X = np.vstack([np.ones_like(x), x]).T
    for _ in range(25):
        q = 1 / (1 + np.exp(-X @ b))
        H = (X * (w * q * (1 - q))[:, None]).T @ X + 1e-9 * np.eye(2)
        b = b + np.linalg.solve(H, X.T @ (w * (y - q)))
    return b


def calib(s):
    b = logit_fit(s.p_over.values, s.over.values)
    g, gi = np.unique(s.match.values, return_inverse=True)
    reps = []
    for _ in range(2000):
        wm = np.bincount(rng.integers(0, len(g), len(g)), minlength=len(g)).astype(float)
        reps.append(logit_fit(s.p_over.values, s.over.values, wm[gi]))
    reps = np.array(reps)
    bins = [0, 0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 1.0001]
    tab = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (s.p_over >= lo) & (s.p_over < hi)
        if m.sum():
            tab.append({"bin": f"{lo:.2f}-{min(hi,1):.2f}", "units": int(m.sum()), "matches": int(s.match[m].nunique()),
                        "mean_p_over": float(s.p_over[m].mean()), "over_rate": float(s.over[m].mean())})
    bb, G = boot({"ll": s.ll_live, "ll50": np.full(len(s), np.log(2)), "brier": s.brier_live, "hit": s.fav_hit,
                  "printed": np.maximum(s.p_over, 1 - s.p_over), "over": s.over, "p_over": s.p_over}, s.match)
    return {"units": int(len(s)), "matches": G, "intercept": [float(b[0]), *np.percentile(reps[:, 0], [2.5, 97.5])],
            "slope": [float(b[1]), *np.percentile(reps[:, 1], [2.5, 97.5])], "bins": tab, **bb}


cal = {"all": calib(p1)}
for (pr, sc), s in p1.groupby(["prop", "scope"]):
    cal[f"{pr}_{sc}"] = calib(s)
res["calibration_live"] = cal

# ------------------------------------------------------------------ decomposition on units with HLTV realized components
d = p1[p1.K.notna() & p1.s_hat.notna() & p1.R_hat.notna()].copy().reset_index(drop=True)
d["mid"] = d.match.str.split(":").str[1].astype(int)
out, rows_sh = {}, []
for (prop, scope), s in d.groupby(["prop", "scope"]):
    s = s.reset_index(drop=True)
    pop = live_frame(ser, scope)
    gw, _ = fit_gw(pop)
    lam_k = np.where(s.lam_kills.notna(), s.lam_kills, s.lam).astype(float)
    tau_hat = lam_k / (s.s_hat * s.R_hat)
    f = pd.DataFrame({"Rreg": s.Rreg / s.R_hat, "Rot": s.R / s.Rreg})
    fT = (s["T"] / s.R) / tau_hat
    f["Tw"] = np.exp(gw(s.W / s.R))
    f["Tr"] = fT / f.Tw
    f["S"] = (s.K / s["T"]) / s.s_hat
    if prop == "hs":
        hsr_impl = s.lam / lam_k
        f["H"] = np.where(s.K > 0, (s.HS / s.K) / hsr_impl, 1.0)
        names, actual = KILL_F + ["H"], s.HS.values.astype(float)
        cdict = hd[scope]["hs"]["logloss_refit"]
    else:
        names, actual = KILL_F, s.K.values.astype(float)
        cdict = hd[scope]["kills"]["logloss_refit"]
    base = s.lam.values.astype(float)
    e0 = actual - base
    sh, preds = shapley_rows(base, actual, f[names], names)
    cols, rat = {"se0": e0 ** 2, "ae0": np.abs(e0), "bias0": e0}, {}
    for k in names:
        cols[f"sh_se_{k}"] = sh["se"][k].values
        cols[f"sh_ae_{k}"] = sh["ae"][k].values
        cols[f"sh_b_{k}"] = sh["bias"][k].values
        rat[f"sh_se_share_{k}"] = (f"sh_se_{k}", "se0")
    # at the line
    y = s.over.values.astype(float)
    c0 = cdict["none"]["c"]
    p_base = p_over_line(base, c0 * np.sqrt(np.maximum(base, 1)), s.line.values)
    cols["ll_live"] = logloss(s.p_over.values, y)
    cols["ll_base"] = logloss(p_base, y)
    cols["br_live"] = (s.p_over.values - y) ** 2
    cols["br_base"] = (p_base - y) ** 2
    for k in names:
        pk = np.maximum(pred_subset(base, f[names], frozenset([k])), 0.05)
        ck = cdict.get(k, cdict["none"])["c"]
        po = p_over_line(pk, ck * np.sqrt(np.maximum(pk, 1)), s.line.values)
        cols[f"dll_one_{k}"] = cols["ll_base"] - logloss(po, y)
        cols[f"dbr_one_{k}"] = cols["br_base"] - (po - y) ** 2
    # rounds both parts together (realized total rounds)
    pr_ = np.maximum(base * f.Rreg.values * f.Rot.values, 0.05)
    ckey = "+".join(sorted(["Rot", "Rreg"]))
    ck = cdict.get(ckey, cdict["none"])["c"]
    po = p_over_line(pr_, ck * np.sqrt(np.maximum(pr_, 1)), s.line.values)
    cols["dll_one_R"] = cols["ll_base"] - logloss(po, y)
    # PIT / coverage of the saved distribution
    if prop == "kills":
        c_s = (s.sd / np.sqrt(np.maximum(s.lam, 1))).values
        F1, F0 = disc_normal_cdf(actual - 1, base, c_s), disc_normal_cdf(actual, base, c_s)
    else:
        hsr_impl = (s.lam / lam_k).values
        ck_ = np.where(s.c_kills.notna(), s.c_kills, 1.457).astype(float)
        _, F0, F1 = hs_mix(lam_k, ck_, hsr_impl, actual)
    u = F1 + rng.random(len(actual)) * (F0 - F1)
    for q in (0.5, 0.8, 0.95):
        cols[f"cov{int(q*100)}"] = ((u > (1 - q) / 2) & (u < (1 + q) / 2)).astype(float)
    # fixable at the line: saved projection x forward-predicted ratios (pre-match only)
    pj = s[["mid", "pid"]].merge(pred, left_on=["mid", "pid"], right_on=["match", "player"], how="left")
    have = {}
    for k in ("R", "T", "S", "tot") + (("H", "totH") if prop == "hs" else ()):
        col = f"f_{k}_{scope}"
        if col not in pj:
            continue
        fk = pj[col].values
        ok = np.isfinite(fk)
        have[k] = int(ok.sum())
        pk = np.where(ok, base * np.where(ok, fk, 1.0), base)
        po = p_over_line(pk, c0 * np.sqrt(np.maximum(pk, 1)), s.line.values)
        cols[f"dll_fix_{k}"] = cols["ll_base"] - logloss(po, y)
        cols[f"dse_fix_{k}"] = e0 ** 2 - (actual - pk) ** 2
    rows_sh.append(pd.DataFrame({"match": s.match, "prop": prop, "scope": scope, "forecast_id": s.forecast_id, "se0": e0 ** 2,
                                 **{f"sh_{k}": sh["se"][k].values for k in names}}))
    bb, G = boot(cols, s.match, ratio=rat)
    out[f"{prop}_{scope}"] = {"units": int(len(s)), "matches": G, "sources": s.source.value_counts().to_dict(),
                              "ot_series_share": float((s.ot > 0).mean()), "mean_R": float(s.R.mean()), "mean_R_hat": float(s.R_hat.mean()),
                              "pit_deciles": np.histogram(u, bins=10, range=(0, 1))[0].tolist(), "fix_rows_with_pred": have,
                              "c_base": c0, **bb}
    print(prop, scope, len(s), G, {k: round(bb[f"sh_se_share_{k}"][0], 3) for k in names}, flush=True)
res["decomp"] = out

# ------------------------------------------------------------------ owner breakdowns (every pre-cutoff unit; decomposition shares where realized)
SH = pd.concat(rows_sh)
p1["depth"] = pd.cut(p1.hist_rounds, [-1, 150, 500, 1500, 1e9], labels=["<150", "150-500", "500-1500", "1500+"]).astype(str)
p1.loc[p1.hist_rounds.isna(), "depth"] = "unknown"
p1["history"] = np.where(p1.source == "perf-cm2", "full history (cm-2)",
                         np.where(p1.source == "perf-2-bo3", "BO3.gg fallback", np.where(p1.revision.astype(str).str.startswith("N0"), "HLTV N0-pinned", "HLTV " + p1.revision.astype(str))))
p1["env"] = p1.tonight.fillna("unknown")
p1["ot_outcome"] = np.where(p1.ot.isna(), "no HLTV join", np.where(p1.ot > 0, "OT in scope (retrospective)", "no OT (retrospective)"))
p1["stat_scope"] = p1.prop + " " + p1.scope.map({"1": "Map 1", "12": "Maps 1-2", "123": "Maps 1-3"})
m = p1.merge(SH.drop(columns=["match", "prop", "scope"]), on="forecast_id", how="left")
grp = {}
for key in ("version", "history", "stat_scope", "depth", "env", "ot_outcome", "book"):
    grp[key] = {}
    for (g, pr), s in m.groupby([key, "prop"]):
        if len(s) < 10:
            grp[key][f"{g} | {pr}"] = {"units": int(len(s)), "matches": int(s.match.nunique()), "note": "under 10 units"}
            continue
        cols = {"bias": s.actual - s.lam, "ae": (s.actual - s.lam).abs(), "hit": s.fav_hit, "printed": np.maximum(s.p_over, 1 - s.p_over),
                "ll": s.ll_live, "over": s.over, "p_over": s.p_over}
        b, G = boot(cols, s.match)
        row = {"units": int(len(s)), "matches": G, **b}
        q = s[s.se0.notna()]
        if len(q) >= 10:
            cc = {"se0": q.se0.values, "rounds": (q.sh_Rreg + q.sh_Rot).values, "team": (q.sh_Tw + q.sh_Tr).values, "share": q.sh_S.values}
            rr = {"share_rounds": ("rounds", "se0"), "share_team": ("team", "se0"), "share_share": ("share", "se0")}
            if pr == "hs":
                cc["hs_per_kill"] = q.sh_H.values
                rr["share_hs_per_kill"] = ("hs_per_kill", "se0")
            bq, Gq = boot(cc, q.match.values, ratio=rr)
            row["decomp_units"], row["decomp_matches"] = int(len(q)), Gq
            row.update({k: v for k, v in bq.items() if k.startswith("share_")})
        grp[key][f"{g} | {pr}"] = row
res["breakdowns"] = grp
json.dump(res, open(OUT / "p1_decomp.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print(json.dumps(res["issues"]))
