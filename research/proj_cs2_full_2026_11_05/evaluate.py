"""Walk-forward evaluation of pd-1 against the reconstructed live distribution (research only).

  python evaluate.py fit       monthly refits; each month's rows predicted by a model fitted on strictly earlier days
  python evaluate.py score     all-outcome scores (development months and the untouched final window, separately)
  python evaluate.py lines     settled graded book props at the offered lines (+ exact live chances from P1 units)

Development window: 2025-11-01 .. 2026-09-30 (feature / model choices were made here only).
Final untouched window: 2026-10-01 .. 2026-11-05 (scored once, reported separately; every settled book line is in it).
CIs: 95% bootstrap over MATCHES (all rows of a match resampled together), 2,000 draws."""
from __future__ import annotations

import json
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

import dist
import model
from baseline import live_params
from features import OUT

EVAL_FROM = date(2025, 11, 1).toordinal()
FINAL_FROM = date(2026, 10, 1).toordinal()
END = date(2026, 11, 6).toordinal()
CELLS = [("kills", "1"), ("kills", "12"), ("kills", "123"), ("hs", "1"), ("hs", "12"), ("hs", "123")]


def month_starts():
    out, y, m = [], 2025, 11
    while date(y, m, 1).toordinal() < END:
        out.append(date(y, m, 1).toordinal())
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out + [END]


def load():
    F = pd.read_pickle(os.path.join(OUT, "series_features.pkl"))
    B = pd.read_pickle(os.path.join(OUT, "baseline.pkl"))
    keep = [c for c in B.columns if c.startswith(("live_", "full_", "cm2_"))]
    return F.merge(B[["matchid", "playerid"] + keep], on=["matchid", "playerid"], how="left")


def run_fit(F, cells=CELLS):
    ms = month_starts()
    preds = []
    for a, b in zip(ms[:-1], ms[1:]):
        train = F[F.day < a]
        test = F[(F.day >= a) & (F.day < b)]
        for stat, scope in cells:
            M = model.fit(train, stat, scope)
            t = test[test[model.TARGET[(stat, scope)]].notna()]
            if not len(t):
                continue
            mu, v = model.predict(M, t)
            preds.append(pd.DataFrame({"matchid": t.matchid.values, "playerid": t.playerid.values, "stat": stat,
                                       "scope": scope, "mu": mu, "v": v, "fold": date.fromordinal(a).isoformat()}))
            print(date.fromordinal(a), stat, scope, "train", M["n_train"], "test", len(t), flush=True)
    P = pd.concat(preds, ignore_index=True)
    P.to_pickle(os.path.join(OUT, "pd1_preds.pkl"))
    return P


# ------------------------------------------------------------------------------------------- distributions per cell
def live_pmf(rows, stat, scope, tag="live"):
    lam, c, hsr = live_params(rows, stat, scope, tag)
    ymax = dist.YMAX[scope]
    K = dist.live_kills_pmf(lam, c, ymax)
    if stat == "kills":
        return K
    return dist.betabinom_mix(K, hsr)


def boot_ci(match, x, n=2000, seed=11):
    """Mean of x with a 95% match-clustered bootstrap interval."""
    codes, inv = np.unique(match, return_inverse=True)
    s = np.bincount(inv, weights=x)
    c = np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    k = len(codes)
    draws = np.empty(n)
    for i in range(n):
        w = np.bincount(rng.integers(0, k, k), minlength=k)
        draws[i] = (w * s).sum() / (w * c).sum()
    return float(x.mean()), float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))


def calib(p, y):
    """Reliability at thresholds: stated side chance vs hit, overall and by the side the chance favours."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    over = p >= 0.5
    st = np.where(over, p, 1 - p)
    hit = np.where(over, y, 1 - y)
    out = {"n": int(len(p)), "stated": float(st.mean()), "hit": float(hit.mean()), "gap_pts": float(100 * (st.mean() - hit.mean()))}
    for name, m in (("over", over), ("under", ~over)):
        out[name] = {"n": int(m.sum()), "stated": float(st[m].mean()) if m.any() else None,
                     "hit": float(hit[m].mean()) if m.any() else None,
                     "gap_pts": float(100 * (st[m].mean() - hit[m].mean())) if m.any() else None}
    bins = {}
    b = np.clip((st * 20).astype(int), 10, 19)
    for k in np.unique(b):
        m = b == k
        bins[f"{k * 5}-{k * 5 + 5}"] = {"n": int(m.sum()), "stated": round(float(st[m].mean()), 4), "hit": round(float(hit[m].mean()), 4)}
    out["bins"] = bins
    return out


def pit_hist(u):
    h, _ = np.histogram(u, bins=10, range=(0, 1))
    return [round(float(x), 4) for x in h / max(len(u), 1)]


def score_cell(F, P, stat, scope, family, window, seed=5):
    tcol = model.TARGET[(stat, scope)]
    lo, hi = window
    pr = P[(P.stat == stat) & (P.scope == scope)]
    rows = F.merge(pr[["matchid", "playerid", "mu", "v"]], on=["matchid", "playerid"], how="inner")
    rows = rows[(rows.day >= lo) & (rows.day < hi) & rows[tcol].notna() & rows.live_p2_lam1.notna()].reset_index(drop=True)
    y = rows[tcol].to_numpy().astype(int)
    rng = np.random.default_rng(seed)
    PL = live_pmf(rows, stat, scope)
    PF = live_pmf(rows, stat, scope, "full")
    PN = model.pmf(rows.mu.to_numpy(), rows.v.to_numpy(), scope, family)
    ls = {"live": dist.logscore(PL, y), "full_hist_perf2": dist.logscore(PF, y), "pd1": dist.logscore(PN, y)}
    mean = {"live": dist.pmf_mean(PL), "full_hist_perf2": dist.pmf_mean(PF), "pd1": dist.pmf_mean(PN)}
    m = rows.matchid.to_numpy()
    has = rows.live_p2_nmaps.to_numpy() > 0
    out = {"stat": stat, "scope": scope, "family": family, "rows": int(len(rows)), "matches": int(len(np.unique(m))),
           "rows_live_has_hltv_history": int(has.sum())}
    for name in ls:
        out[f"logscore_{name}"] = float(ls[name].mean())
        out[f"mae_{name}"] = float(np.abs(mean[name] - y).mean())
        out[f"bias_{name}"] = float((mean[name] - y).mean())
        out[f"pit_{name}"] = pit_hist(dist.pit({"live": PL, "full_hist_perf2": PF, "pd1": PN}[name], y, rng))
    for ref in ("live", "full_hist_perf2"):
        d = ls["pd1"] - ls[ref]
        out[f"dlog_vs_{ref}"] = boot_ci(m, d)
        out[f"dlog_vs_{ref}_has_hist"] = boot_ci(m[has], d[has]) if has.any() else None
        dm = np.abs(mean["pd1"] - y) - np.abs(mean[ref] - y)
        out[f"dmae_vs_{ref}"] = boot_ci(m, dm)
    # P(X > t) at half-integer thresholds around the live projection (typical line region)
    base = np.floor(mean["live"]) + 0.5
    cal = {}
    for name, PP in (("live", PL), ("pd1", PN)):
        ps, ys = [], []
        for j in (-4, -2, -1, 0, 1, 2, 4):
            t = np.maximum(base + j, 0.5)
            ps.append(dist.p_over(PP, t))
            ys.append((y > t).astype(float))
        cal[name] = calib(np.concatenate(ps), np.concatenate(ys))
    out["threshold_calibration"] = cal
    return out


def run_score(F, P, family_by_cell=None):
    res = {"development": [], "final_untouched": []}
    for stat, scope in CELLS:
        fam = (family_by_cell or {}).get(f"{stat}_{scope}", "negbin")
        res["development"].append(score_cell(F, P, stat, scope, fam, (EVAL_FROM, FINAL_FROM)))
        res["final_untouched"].append(score_cell(F, P, stat, scope, fam, (FINAL_FROM, END)))
        d = res["development"][-1]
        print(stat, scope, fam, "dev rows", d["rows"], "dlog", [round(x, 4) for x in d["dlog_vs_live"]],
              "mae", round(d["mae_live"], 3), "->", round(d["mae_pd1"], 3), flush=True)
    return res


def pooled(F, P, window, family_by_cell=None):
    """All six cells pooled: mean log score difference per row, clustered by match."""
    ms, ds = [], []
    for stat, scope in CELLS:
        fam = (family_by_cell or {}).get(f"{stat}_{scope}", "negbin")
        tcol = model.TARGET[(stat, scope)]
        pr = P[(P.stat == stat) & (P.scope == scope)]
        rows = F.merge(pr[["matchid", "playerid", "mu", "v"]], on=["matchid", "playerid"])
        rows = rows[(rows.day >= window[0]) & (rows.day < window[1]) & rows[tcol].notna() & rows.live_p2_lam1.notna()].reset_index(drop=True)
        y = rows[tcol].to_numpy().astype(int)
        d = dist.logscore(model.pmf(rows.mu.to_numpy(), rows.v.to_numpy(), scope, fam), y) - dist.logscore(live_pmf(rows, stat, scope), y)
        ms.append(rows.matchid.to_numpy())
        ds.append(d)
    return boot_ci(np.concatenate(ms), np.concatenate(ds))


# ------------------------------------------------------------------------------------------- book lines
SCOPE = {"1": "1", "1-2": "12", "1-3": "123"}
STAT = {"kills": "kills", "headshots": "hs"}


def _ll(p, y):
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def side_stats(p, y, match):
    """p = P(Over | no push), y = 1 if Over won. Picked side, stated chance, graded (>= 55%) count and hit, calibration."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    match = np.asarray(match)
    over = p >= 0.5
    st = np.where(over, p, 1 - p)
    hit = np.where(over, y, 1 - y)
    g = st >= 0.55
    return {"n": int(len(p)), "over_share": float(over.mean()), "calibration_all": calib(p, y),
            "graded": int(g.sum()), "graded_over": int((g & over).sum()), "graded_under": int((g & ~over).sum()),
            "graded_hit": boot_ci(match[g], hit[g]) if g.any() else None,
            "graded_calibration": calib(p[g], y[g]) if g.any() else None,
            "logloss": float(_ll(p, y).mean())}


def line_rows(F, P, family_by_cell=None):
    g = pd.read_csv(os.path.join(os.path.dirname(OUT), "work", "prop_grades_with_playerid.csv.gz"))
    g = g[(g.status == "graded") & (g.odds_type == "standard") & g.maps.isin(list(SCOPE)) & g.playerid.notna() & g.matchid.notna()]
    g = g.assign(matchid=g.matchid.astype(int), playerid=g.playerid.astype(int), scope=g.maps.map(SCOPE), stat=g.market.map(STAT))
    g = g[g.actual != g.line]                                   # pushes excluded
    out = []
    for stat, scope in CELLS:
        fam = (family_by_cell or {}).get(f"{stat}_{scope}", "negbin")
        q = g[(g.stat == stat) & (g.scope == scope)]
        pr = P[(P.stat == stat) & (P.scope == scope)]
        r = q.merge(F, on=["matchid", "playerid"], how="inner").merge(pr[["matchid", "playerid", "mu", "v"]],
                                                                      on=["matchid", "playerid"], how="inner")
        r = r[r.live_p2_lam1.notna()].reset_index(drop=True)
        if not len(r):
            continue
        PL = live_pmf(r, stat, scope)
        PN = model.pmf(r.mu.to_numpy(), r.v.to_numpy(), scope, fam)
        line = r.line.to_numpy(float)
        o, u = dist.p_over_line(PL, line)
        r["p_live_recon"] = o / np.maximum(o + u, 1e-12)
        o, u = dist.p_over_line(PN, line)
        r["p_pd1"] = o / np.maximum(o + u, 1e-12)
        r["mean_live_recon"] = dist.pmf_mean(PL)
        r["mean_pd1"] = dist.pmf_mean(PN)
        r["y_over"] = (r.actual > r.line).astype(float)
        out.append(r[["book", "line_id", "start_time", "player", "matchid", "playerid", "stat", "scope", "line", "actual",
                      "y_over", "p_live_recon", "p_pd1", "mean_live_recon", "mean_pd1", "day"]])
    return pd.concat(out, ignore_index=True)


def run_lines(F, P, family_by_cell=None):
    L = line_rows(F, P, family_by_cell)
    m = L.matchid.to_numpy()
    y = L.y_over.to_numpy()
    res = {"all_graded_standard": {
        "note": "every settled graded standard PP/UD prop joined to an HLTV player and match; live_recon = the offline "
                "reconstruction of the live distribution",
        "live_recon": side_stats(L.p_live_recon, y, m), "pd1": side_stats(L.p_pd1, y, m),
        "dlogloss_pd1_minus_live_recon": boot_ci(m, _ll(L.p_pd1, y) - _ll(L.p_live_recon, y)),
        "by_cell": {f"{s}_{c}": {"n": int(len(x)), "live_recon": side_stats(x.p_live_recon, x.y_over, x.matchid),
                                 "pd1": side_stats(x.p_pd1, x.y_over, x.matchid)} for (s, c), x in L.groupby(["stat", "scope"])}}}
    # exact live chances: P1 units (the board's logged chance) matched one-to-one to a line id
    p1 = pd.read_csv(os.path.join(os.path.dirname(OUT), "work", "p1_units_with_player.csv.gz"))
    p1 = p1[p1.line_id.notna()][["line_id", "book", "p", "version", "lam"]].rename(columns={"p": "p_live_exact", "lam": "lam_live_exact"})
    J = L.merge(p1, on=["line_id", "book"], how="inner").drop_duplicates(["line_id", "book"])
    mj = J.matchid.to_numpy()
    yj = J.y_over.to_numpy()
    res["p1_exact_live"] = {"note": "P1 settled board units matched by line id; live_exact = the chance the board logged",
                            "live_exact": side_stats(J.p_live_exact, yj, mj),
                            "live_recon": side_stats(J.p_live_recon, yj, mj),
                            "pd1": side_stats(J.p_pd1, yj, mj),
                            "dlogloss_pd1_minus_live_exact": boot_ci(mj, _ll(J.p_pd1, yj) - _ll(J.p_live_exact, yj)),
                            "recon_vs_exact_chance_mae_pts": float(100 * np.abs(J.p_live_recon - J.p_live_exact).mean()),
                            "recon_vs_exact_same_side": float(((J.p_live_recon >= .5) == (J.p_live_exact >= .5)).mean()),
                            "versions": {str(k): int(v) for k, v in J.version.value_counts().items()}}
    L.to_csv(os.path.join(OUT, "line_rows.csv.gz"), index=False)
    return res


if __name__ == "__main__":
    cmd = sys.argv[1]
    F = load()
    if cmd == "fit":
        run_fit(F)
    elif cmd == "score":
        P = pd.read_pickle(os.path.join(OUT, "pd1_preds.pkl"))
        fam = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
        res = run_score(F, P, fam)
        res["pooled_dev"] = pooled(F, P, (EVAL_FROM, FINAL_FROM), fam)
        res["pooled_final"] = pooled(F, P, (FINAL_FROM, END), fam)
        json.dump(res, open(os.path.join(OUT, "score.json"), "w"), indent=1)
        print("pooled dev", res["pooled_dev"], "final", res["pooled_final"])
    elif cmd == "lines":
        P = pd.read_pickle(os.path.join(OUT, "pd1_preds.pkl"))
        fam = json.loads(sys.argv[2]) if len(sys.argv) > 2 else None
        res = run_lines(F, P, fam)
        json.dump(res, open(os.path.join(OUT, "lines.json"), "w"), indent=1, default=str)
        for k in ("live_exact", "live_recon", "pd1"):
            x = res["p1_exact_live"][k]
            c = x["calibration_all"]
            print("P1", k, "n", x["n"], "graded", x["graded"], "hit", x["graded_hit"], "gap", round(c["gap_pts"], 2),
                  "over", c["over"]["gap_pts"], "under", c["under"]["gap_pts"], "ll", round(x["logloss"], 4),
                  "over_share", round(x["over_share"], 3))
        print("dlogloss vs exact", res["p1_exact_live"]["dlogloss_pd1_minus_live_exact"])
        for k in ("live_recon", "pd1"):
            x = res["all_graded_standard"][k]
            c = x["calibration_all"]
            print("ALL", k, "n", x["n"], "graded", x["graded"], "hit", x["graded_hit"], "gap", round(c["gap_pts"], 2),
                  "over", c["over"]["gap_pts"], "under", c["under"]["gap_pts"], "ll", round(x["logloss"], 4))
