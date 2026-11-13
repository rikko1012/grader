"""Miss decomposition (FACTOR_AUDIT round 10) - measurement, fits and walk-forward scoring exactly as frozen in
FROZEN_DEFINITIONS.md. Read-only on local files (build.py output + the saved picks export).

usage: python score.py            -> report on stdout, score.json + fit_params.json + forward_predictions.jsonl in OUT
"""
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

OPS = Path("C:/Users/tikic/cs2_scout_ops/data")
D = OPS / "lkb1_1113"
OUT = OPS / "miss_decomp_1113"
FIT_END = date(2026, 10, 31)
PICK_FIT0 = date(2026, 10, 19)
BOARD_FIT0 = date(2026, 10, 21)
HIST_FIT0 = date(2026, 10, 1)
A1_FIT0 = date(2026, 1, 1)
TEST0, TEST1, T1_END = date(2026, 11, 1), date(2026, 11, 10), date(2026, 11, 5)
FREEZE_FILE = Path(__file__).with_name("FREEZE_TIME.txt")
MOTIVATING = {"vitality-vs-falcons-esports-11-11-2026", "mouz-cs2-vs-g2-11-11-2026", "furia-vs-vitality-12-11-2026",
              "fut-cs2-vs-mouz-cs2-12-11-2026", "vitality-vs-mouz-cs2-13-11-2026"}
ND = NormalDist()
R_USED = 44.0
SEEDS = {"A1": 20261115, "A2": 20261116, "B1": 20261117, "C1": 20261118, "C2": 20261119, "C3": 20261120}
CANDS = ["A1", "A2", "B1", "C1", "C2", "C3"]


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def r4(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 4)


# ------------------------------------------------------------------ data
P = pd.read_pickle(OUT / "units_pick.pkl")
HU = pd.read_pickle(OUT / "units_hist.pkl")
picks = [json.loads(l) for l in gzip.open(D / "picks_7000.jsonl.gz", "rt")]
pre = [r for r in picks if r.get("match_start") and r.get("lambda") is not None and ts(r["logged_at"]) < ts(r["match_start"])]
for r in pre:
    r["day"] = ts(r["match_start"]).date()

# rr-1 market rounds (saved shadow) per slug, for the rounds measurement only
rr1 = defaultdict(list)
for r in pre:
    if r["scope"] != "12":
        continue
    try:
        sh = (json.loads(r.get("factors") or "{}").get("shadow") or {}).get("market_rounds") or {}
    except ValueError:
        sh = {}
    if sh.get("rounds_sum"):
        rr1[r["match_slug"]].append(float(sh["rounds_sum"]))
rr1 = {k: float(np.median(v)) for k, v in rr1.items()}


def q_from_series(p):
    """per-map chance q with BO3 p = q^2 (3 - 2q) (the board's map_prob_basis rule), by bisection."""
    if p is None or not (0 < p < 1):
        return None
    lo, hi = 0.0, 1.0
    for _ in range(60):
        m = (lo + hi) / 2
        if m * m * (3 - 2 * m) < p:
            lo = m
        else:
            hi = m
    return (lo + hi) / 2


def elo_p(eo, ep):
    return 1 / (1 + 10 ** ((ep - eo) / 400))


for df in (P, HU):
    df["S"] = (df["elo_own"] + df["elo_opp"]) / 800.0
    df["G"] = (df["elo_own"] - df["elo_opp"]) / 400.0
    df["d_elo"] = df.apply(lambda r: abs(q_from_series(elo_p(r.elo_own, r.elo_opp)) - 0.5) if pd.notna(r.elo_own) else np.nan, axis=1)
    df["form"] = df.apply(lambda r: (((r.k10 + 100 * r.rate_all) / (r.r10 + 100)) / r.rate_all)
                          if pd.notna(r.get("k10")) and pd.notna(r.get("rate_all")) else np.nan, axis=1)
    df["open_rate"] = df.apply(lambda r: r.open_att / r.open_r if pd.notna(r.get("open_r")) and r.open_r >= 200 else np.nan, axis=1)
    df["is_awp"] = df.apply(lambda r: bool(pd.notna(r.get("awp_share")) and r.awp_share >= 0.25 and (r.awp_kills or 0) >= 100), axis=1)
P["d_mkt"] = P["p_team_market"].map(lambda p: abs(q_from_series(p) - 0.5) if pd.notna(p) else np.nan)
P["rr1"] = P["match_slug"].map(rr1)


def map_ratio(r):
    """sum over maps of the chance the map is Map 1 or Map 2 (mean of the two) x the player's shrunk per-map kill-rate ratio."""
    if not r.get("pred_maps") or not isinstance(r.get("per_map"), dict) or not r.get("r365"):
        return np.nan
    try:
        pm = json.loads(r["pred_maps"])
    except (TypeError, ValueError):
        return np.nan
    if not isinstance(pm, dict):        # deviation 1 (code fix): saved pred_maps can be JSON null
        return np.nan
    w = defaultdict(float)
    for slot in ("map1", "map2"):
        ch = pm.get(slot) or {}
        tot = sum(v for v in ch.values() if isinstance(v, (int, float)))
        if tot <= 0:
            return np.nan
        for k, v in ch.items():
            w[str(k).lower().replace("de_", "").replace(" ", "")] += 0.5 * v / tot
    base = r["k365"] / r["r365"]
    if base <= 0:
        return np.nan
    out = 0.0
    for m, wm in w.items():
        k, rd = r["per_map"].get(m, (0.0, 0.0))
        out += wm * ((k + 150 * base) / (rd + 150)) / base
    return out


P["mapr"] = P.apply(lambda r: map_ratio(r.to_dict()), axis=1)

# ------------------------------------------------------------------ helpers
def poisson_fit(y, off, X):
    """Poisson MLE with offset (Newton); X includes an intercept column."""
    b = np.zeros(X.shape[1])
    b[0] = math.log(y.sum() / np.exp(off).sum())
    for _ in range(50):
        mu = np.exp(off + X @ b)
        g = X.T @ (y - mu)
        Hm = X.T @ (X * mu[:, None])
        step = np.linalg.solve(Hm, g)
        b += step
        if np.abs(step).max() < 1e-10:
            break
    return b


def boot_ci(df, fn, n=2000, seed=7, key="m"):
    ms = df[key].unique()
    idx = {m: np.where(df[key].values == m)[0] for m in ms}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = rng.choice(ms, size=len(ms), replace=True)
        ii = np.concatenate([idx[m] for m in pick])
        v = fn(df.iloc[ii])
        if v is not None and not np.isnan(v):
            vals.append(v)
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


# ------------------------------------------------------------------ measurement A / B (descriptive)
def unit_frame(df, lam_col):
    x = df[df[lam_col].notna() & df.act_k.notna() & df.R12.notna()].copy()
    x["lam"] = x[lam_col]
    x["kpr_act"] = x.act_k / x.R12
    x["kpr_proj"] = x.lam / R_USED
    return x


meas = {}
pm_ = P[P.hltv.notna() & P.R12.notna()].drop_duplicates("match_slug")
pm_ = pm_.assign(win=pm_.day.map(lambda d: "FIT" if d <= FIT_END else ("TEST" if TEST0 <= d <= TEST1 else "LATER")))
for win in ("FIT", "TEST", "LATER"):
    x = pm_[pm_.win == win]
    out = {"matches": len(x), "mean_R12": r4(x.R12.mean()), "mean_R12_minus_44": r4((x.R12 - R_USED).mean()),
           "ot_rate": r4(x.ot.mean()), "rr1_n": int(x.rr1.notna().sum()), "mean_R12_minus_rr1": r4((x.R12 - x.rr1).mean())}
    bands = {}
    for lab, lo, hi in (("q 40-60% (d<0.10)", 0, 0.10), ("d 0.10-0.20", 0.10, 0.20), ("d>=0.20", 0.20, 1)):
        y = x[(x.d_mkt >= lo) & (x.d_mkt < hi)]
        bands[lab] = {"matches": len(y), "mean_R12": r4(y.R12.mean()), "minus_44": r4((y.R12 - R_USED).mean()),
                      "minus_rr1": r4((y.R12 - y.rr1).mean()), "ot_rate": r4(y.ot.mean())}
    out["by_market_closeness"] = bands
    bands = {}
    for lab, lo, hi in (("Elo d<0.05", 0, 0.05), ("Elo d 0.05-0.10", 0.05, 0.10), ("Elo d>=0.10", 0.10, 1)):
        y = x[(x.d_elo >= lo) & (x.d_elo < hi)]
        bands[lab] = {"matches": len(y), "mean_R12": r4(y.R12.mean()), "minus_44": r4((y.R12 - R_USED).mean()), "ot_rate": r4(y.ot.mean())}
    out["by_elo_closeness"] = bands
    meas[f"A_board_{win}"] = out
hm = HU[HU.R12.notna() & HU.d_elo.notna() & (HU.day >= A1_FIT0) & (HU.day <= FIT_END)].drop_duplicates("hltv")
bands = {}
for lab, lo, hi in (("Elo d<0.05", 0, 0.05), ("Elo d 0.05-0.10", 0.05, 0.10), ("Elo d>=0.10", 0.10, 1)):
    y = hm[(hm.d_elo >= lo) & (hm.d_elo < hi)]
    bands[lab] = {"matches": len(y), "mean_R12": r4(y.R12.mean()), "minus_44": r4((y.R12 - R_USED).mean()), "ot_rate": r4(y.ot.mean())}
meas["A_hltv_2026_Jan_Oct"] = {"matches": len(hm), "mean_R12": r4(hm.R12.mean()), "ot_rate": r4(hm.ot.mean()), "bands": bands}

for lab, df, lamc, d0, d1 in (("B_hist_recon_Oct", HU, "lam_cm2_recon", HIST_FIT0, FIT_END),
                              ("B_board_saved_FIT", P, "lam_k", PICK_FIT0, FIT_END),
                              ("B_board_saved_TEST", P, "lam_k", TEST0, TEST1)):
    x = unit_frame(df[(df.day >= d0) & (df.day <= d1)], lamc)
    x = x[x.S.notna()]
    out = {"units": len(x), "mean_kpr_resid": r4((x.kpr_act - x.kpr_proj).mean()),
           "ratio_act_over_proj_kpr": r4(x.kpr_act.sum() / x.kpr_proj.sum())}
    for var in ("S", "G"):
        qs = pd.qcut(x[var], 5, labels=False, duplicates="drop")
        out[f"by_{var}_quintile"] = [{"q": int(q), "elo_mid": r4(g[var].median() * (800 if var == "S" else 400)), "units": len(g),
                                      "kpr_resid": r4((g.kpr_act - g.kpr_proj).mean()),
                                      "ratio": r4(g.kpr_act.sum() / g.kpr_proj.sum())} for q, g in x.groupby(qs)]
    meas[lab] = out

# reconstruction check: recomputed cm-2 vs saved perf-cm2 kills projections
chk = P[P.lam_cm2_recon.notna() & P.lam_k.notna() & (P.kills_src == "perf-cm2")]
meas["recon_check"] = {"units": len(chk), "corr": r4(np.corrcoef(chk.lam_k, chk.lam_cm2_recon)[0, 1]) if len(chk) > 2 else None,
                       "median_abs_diff": r4((chk.lam_k - chk.lam_cm2_recon).abs().median()),
                       "p90_abs_diff": r4((chk.lam_k - chk.lam_cm2_recon).abs().quantile(0.9))}

# ------------------------------------------------------------------ fits (data through 2026-10-31 only)
params = {}
# A1: Maps 1-2 rounds vs Elo closeness, HLTV BO3 matches 2026-01-01 .. 10-31
x = hm
b = np.polyfit(x.d_elo, x.R12, 1)
params["A1"] = {"slope": float(b[0]), "icpt": float(b[1]), "Rbar": float(np.polyval(b, x.d_elo).mean()), "n_matches": len(x)}
# A2: Maps 1-2 rounds vs market closeness, board matches Oct 19 - 31
x = pm_[(pm_.win == "FIT") & pm_.d_mkt.notna()]
b = np.polyfit(x.d_mkt, x.R12, 1)
params["A2"] = {"slope": float(b[0]), "icpt": float(b[1]), "Rbar": float(np.polyval(b, x.d_mkt).mean()), "n_matches": len(x)}

hf = unit_frame(HU[(HU.day >= HIST_FIT0) & (HU.day <= FIT_END)], "lam_cm2_recon")
hf["off"] = np.log(hf.lam * hf.R12 / R_USED)
# B1: Elo level + gap
x = hf[hf.S.notna()]
Sm = float(x.S.mean())
bb = poisson_fit(x.act_k.values, x.off.values, np.column_stack([np.ones(len(x)), x.S - Sm, x.G]))
params["B1"] = {"S_mean": Sm, "b_S": float(bb[1]), "b_G": float(bb[2]), "n_units": len(x), "n_matches": int(x.hltv.nunique())}
# C1: recent form
x = hf[hf.form.notna() & (hf.form > 0)]
bb = poisson_fit(x.act_k.values, x.off.values, np.column_stack([np.ones(len(x)), np.log(x.form)]))
params["C1"] = {"beta": float(bb[1]), "n_units": len(x)}
# C2: role
x = hf.copy()
tau = float(x[~x.is_awp & x.open_rate.notna()].open_rate.quantile(0.8))


def role_of(r, tau=tau):
    if r.is_awp:
        return "awp"
    if pd.notna(r.open_rate) and r.open_rate >= tau:
        return "entry"
    return "support"


x["role"] = x.apply(role_of, axis=1)
X = np.column_stack([np.ones(len(x)), (x.role == "awp").astype(float), (x.role == "entry").astype(float)])
bb = poisson_fit(x.act_k.values, x.off.values, X)
g = {"support": 0.0, "awp": float(bb[1]), "entry": float(bb[2])}
share = x.role.value_counts(normalize=True).to_dict()
gbar = sum(g[k] * share.get(k, 0) for k in g)
params["C2"] = {"tau_open_rate": tau, "gamma": {k: v - gbar for k, v in g.items()}, "share": share, "n_units": len(x)}
# C3: map-specific, board pick units Oct 19-31 (saved projection, map chances as saved)
x = unit_frame(P[(P.day >= PICK_FIT0) & (P.day <= FIT_END)], "lam_k")
x = x[x.mapr.notna() & (x.mapr > 0)]
off = np.log(x.lam * x.R12 / x.rounds_used)
bb = poisson_fit(x.act_k.values, off.values, np.column_stack([np.ones(len(x)), np.log(x.mapr)]))
params["C3"] = {"beta": float(bb[1]), "n_units": len(x)}


# ------------------------------------------------------------------ factor per unit
def factors(df, cand, placebo=False):
    """f per row of df (index aligned); NaN -> 1 (live)."""
    p = params[cand]
    rng = np.random.default_rng(SEEDS[cand])
    if cand in ("A1", "A2"):
        col = "d_elo" if cand == "A1" else "d_mkt"
        dm = df.groupby("match_slug")[col].first()
        if placebo:
            dm = pd.Series(rng.permutation(dm.values), index=dm.index)
        d = df.match_slug.map(dm)
        return ((p["icpt"] + p["slope"] * d) / p["Rbar"]).fillna(1.0)
    if cand == "B1":
        sg = df.groupby(["match_slug", "team"])[["S", "G"]].first()
        if placebo:     # each match takes another match's (S, G) pair
            slugs = sorted(df.match_slug.unique())
            perm = dict(zip(slugs, rng.permutation(slugs)))
            src = {s: df[df.match_slug == s].groupby("team")[["S", "G"]].first() for s in slugs}
            S = []
            G = []
            for r in df.itertuples():
                s2 = src[perm[r.match_slug]]
                if len(s2) == 0 or s2.S.isna().all():
                    S.append(np.nan)
                    G.append(np.nan)
                    continue
                # own-team position: same rank order (sorted team names) in the donor match
                own_teams = sorted(src[r.match_slug].index)
                k = own_teams.index(r.team) if r.team in own_teams else 0
                dt = sorted(s2.index)
                row = s2.loc[dt[min(k, len(dt) - 1)]]
                S.append(row.S)
                G.append(row.G)
            S, G = np.array(S, float), np.array(G, float)
        else:
            S, G = df.S.values, df.G.values
        return pd.Series(np.exp(p["b_S"] * (S - p["S_mean"]) + p["b_G"] * G), index=df.index).fillna(1.0)
    if cand == "C1":
        v = df.form.values.copy()
        if placebo:
            ok = ~np.isnan(v)
            v[ok] = rng.permutation(v[ok])
        return pd.Series(np.power(v, p["beta"]), index=df.index).fillna(1.0)
    if cand == "C2":
        roles = df.apply(lambda r: role_of(r, p["tau_open_rate"]) if pd.notna(r.get("elo_own")) else None, axis=1)
        if placebo:
            ok = roles.map(lambda k: isinstance(k, str)).values
            vals = roles.values.copy()
            vals[ok] = rng.permutation(vals[ok])
            roles = pd.Series(vals, index=df.index)
        return roles.map(lambda k: math.exp(p["gamma"][k]) if isinstance(k, str) else np.nan).fillna(1.0)  # deviation 2: None became NaN
    if cand == "C3":
        if placebo:     # each match takes another match's map chances; the player's own per-map rates
            slugs = sorted(df.match_slug.unique())
            pmap = df.groupby("match_slug")["pred_maps"].first()
            perm = dict(zip(slugs, rng.permutation(slugs)))
            v = df.apply(lambda r: map_ratio(dict(r.to_dict(), pred_maps=pmap.get(perm[r.match_slug]))), axis=1).values
        else:
            v = df.mapr.values
        return pd.Series(np.power(v, p["beta"]), index=df.index).fillna(1.0)
    raise KeyError(cand)


# ------------------------------------------------------------------ board rows (lkb-1 rule, Maps 1-2 only)
seen, board = set(), []
for r in sorted(pre, key=lambda r: r["id"]):
    if not (r.get("on_board") == 1 and r.get("test") in (0, 2) and r["line_type"] == "standard" and r["status"] == "settled"
            and r.get("actual") is not None and r["prop"] in ("kills", "hs") and r["scope"] == "12" and r["pick"] in ("More", "Less")):
        continue
    k = (r["player_id"], r["prop"], r["scope"], float(r["line"]), r["pick"], r["day"])
    if k in seen:
        continue
    seen.add(k)
    a, ln = float(r["actual"]), float(r["line"])
    if a == ln:
        continue
    r["over"] = int(a > ln)
    board.append(r)

cs = defaultdict(list)
for r in board:
    if BOARD_FIT0 <= r["day"] <= FIT_END:
        lam, ln, p = float(r["lambda"]), float(r["line"]), float(r["p_more"])
        if abs(lam - ln) >= 1 and 0.02 < p < 0.98 and abs(ND.inv_cdf(p)) > 1e-9:
            cs[r["prop"]].append(abs(lam - ln) / (abs(ND.inv_cdf(p)) * math.sqrt(lam)))
C = {k: float(np.median(v)) for k, v in cs.items()}

# series result per slug (upset / OT) from the units
res = {}
for slug, g_ in P.groupby("match_slug"):
    g1 = g_[g_.hltv.notna()]
    if g1.empty or pd.isna(g1.R12.iloc[0]):
        res[slug] = None
        continue
    ot = int(g1.ot.iloc[0])
    pw = g1.groupby("team")["p_win"].median().dropna().to_dict()
    tt = g1.groupby("team")["team_tid"].first().to_dict()
    w = g1.winner_tid.iloc[0]
    upset = None
    if w is not None and not pd.isna(w) and pw:
        dog = [t for t, p in pw.items() if p < 0.5]
        if dog and tt.get(dog[0]) is not None:
            upset = int(tt[dog[0]] == w)
        elif all(p >= 0.5 for p in pw.values()):
            upset = 0
    res[slug] = {"ot": ot, "upset": upset}


def apply_rows(rows, fmap):
    out = []
    for r in rows:
        lam, ln, p = float(r["lambda"]), float(r["line"]), float(r["p_more"])
        f = fmap.get((r["match_slug"], r["player_id"]), 1.0)
        lam2 = lam * f
        lo = r["pick"] == "More"
        co = lo
        if (lam - ln) * (lam2 - ln) < 0:
            co = not lo
        c = C[r["prop"]]
        pc = min(max(p, 1e-4), 1 - 1e-4)
        p2 = ND.cdf(ND.inv_cdf(pc) + (lam2 - lam) / (c * math.sqrt(lam))) if f != 1.0 else p
        out.append({"m": r["match_slug"], "day": r["day"], "prop": r["prop"], "over": r["over"], "lo": int(lo), "co": int(co),
                    "lh": int(lo == bool(r["over"])), "ch": int(co == bool(r["over"])), "p": p, "p2": p2, "f": f,
                    "units": float(r.get("units") or 0)})
    return pd.DataFrame(out)


def dhit(d):
    return d.ch.mean() - d.lh.mean()


def side_hit(d, cs_, ch_, over):
    s = d[d[cs_] == over]
    return s[ch_].mean() if len(s) else np.nan


def brier(d, col):
    return float(((d[col] - d.over) ** 2).mean())


def score_rows(d, ci=True):
    s = {"rows": len(d), "matches": int(d.m.nunique()), "flips": int((d.lo != d.co).sum()),
         "flips_to_over": int(((d.lo != d.co) & (d.co == 1)).sum()), "live_hit": d.lh.mean(), "cand_hit": d.ch.mean(), "d_hit": dhit(d),
         "live_over_n": int(d.lo.sum()), "cand_over_n": int(d.co.sum()),
         "live_over_hit": side_hit(d, "lo", "lh", 1), "cand_over_hit": side_hit(d, "co", "ch", 1),
         "live_under_hit": side_hit(d, "lo", "lh", 0), "cand_under_hit": side_hit(d, "co", "ch", 0),
         "live_over_share": d.lo.mean(), "cand_over_share": d.co.mean(), "brier_live": brier(d, "p"), "brier_cand": brier(d, "p2"),
         "f_min": d.f.min(), "f_med": d.f.median(), "f_max": d.f.max()}
    s["d_brier"] = s["brier_cand"] - s["brier_live"]
    if ci and len(d):
        s["ci_d_hit"] = boot_ci(d, dhit)
        s["ci_d_over"] = boot_ci(d, lambda x: side_hit(x, "co", "ch", 1) - side_hit(x, "lo", "lh", 1))
        s["ci_d_under"] = boot_ci(d, lambda x: side_hit(x, "co", "ch", 0) - side_hit(x, "lo", "lh", 0))
        s["ci_d_brier"] = boot_ci(d, lambda x: brier(x, "p2") - brier(x, "p"))
    return s


def mae_units(U, f, cand):
    """unit-level MAE (test player-matches with actual kills and rounds): kills per round, rounds, kills, HS per round."""
    x = U.assign(f=f)
    x = x[x.lam_k.notna() & x.act_k.notna() & x.R12.notna()]
    fr = x.f if cand in ("A1", "A2") else 1.0                   # rounds prediction moves only for A
    fk = 1.0 if cand in ("A1", "A2") else x.f                   # kills-per-round prediction moves only for B / C
    out = {"units": len(x), "matches": int(x.match_slug.nunique())}
    e_live = (x.act_k / x.R12 - x.lam_k / x.rounds_used).abs()
    e_cand = (x.act_k / x.R12 - x.lam_k * fk / x.rounds_used).abs()
    out["kpr_mae_live"], out["kpr_mae_cand"] = e_live.mean(), e_cand.mean()
    m = x.drop_duplicates("match_slug")
    fr_m = m.f if cand in ("A1", "A2") else 1.0
    out["rounds_mae_live"] = (m.R12 - m.rounds_used).abs().mean()
    out["rounds_mae_cand"] = (m.R12 - m.rounds_used * fr_m).abs().mean()
    out["kills_mae_live"] = (x.act_k - x.lam_k).abs().mean()
    out["kills_mae_cand"] = (x.act_k - x.lam_k * x.f).abs().mean()
    h = x[x.lam_h.notna() & x.act_h.notna()]
    out["hs_pr_mae_live"] = (h.act_h / h.R12 - h.lam_h / h.rounds_used).abs().mean()
    fk_h = 1.0 if cand in ("A1", "A2") else h.f
    out["hs_pr_mae_cand"] = (h.act_h / h.R12 - h.lam_h * fk_h / h.rounds_used).abs().mean()
    out["hs_mae_live"] = (h.act_h - h.lam_h).abs().mean()
    out["hs_mae_cand"] = (h.act_h - h.lam_h * h.f).abs().mean()
    xx = x.assign(m=x.match_slug, el=e_live, ec=e_cand, ekl=(x.act_k - x.lam_k).abs(), ekc=(x.act_k - x.lam_k * x.f).abs())
    out["ci_d_kpr_mae"] = boot_ci(xx, lambda z: z.ec.mean() - z.el.mean())
    out["ci_d_kills_mae"] = boot_ci(xx, lambda z: z.ekc.mean() - z.ekl.mean())
    if cand in ("A1", "A2"):
        mm = m.assign(m=m.match_slug, rl=(m.R12 - m.rounds_used).abs(), rc=(m.R12 - m.rounds_used * m.f).abs())
        out["ci_d_rounds_mae"] = boot_ci(mm, lambda z: z.rc.mean() - z.rl.mean())
    return out


def evaluate(cand, rows, U, placebo=False, full=True):
    f = factors(U, cand, placebo)
    fmap = {(r.match_slug, r.player_id): float(v) for r, v in zip(U.itertuples(), f.values)}
    d = apply_rows(rows, fmap)
    s = score_rows(d, ci=True)
    if not full:
        return s, d, f
    crit = {}
    crit["C1"] = s["ci_d_hit"][0] > 0
    crit["C2"] = bool(s["cand_over_hit"] >= s["live_over_hit"] and s["cand_under_hit"] >= s["live_under_hit"])
    crit["C3"] = True     # every row keeps a side by construction; counts reported
    crit["C4"] = bool(abs(s["cand_over_share"] - 0.5) <= abs(s["live_over_share"] - 0.5) + 0.01)
    crit["C5"] = bool(s["d_brier"] <= 0)
    sub = {}
    for name, fn in (("upset", lambda m: (res.get(m) or {}).get("upset") == 1), ("ot", lambda m: (res.get(m) or {}).get("ot") == 1),
                     ("rest", lambda m: (res.get(m) or {}).get("upset") == 0 and (res.get(m) or {}).get("ot") == 0)):
        x = d[d.m.map(fn).astype(bool)]
        sub[name] = {"rows": len(x), "matches": int(x.m.nunique()), "live_hit": r4(x.lh.mean()) if len(x) else None,
                     "cand_hit": r4(x.ch.mean()) if len(x) else None, "d_hit": r4(dhit(x)) if len(x) else None,
                     "flips": int((x.lo != x.co).sum())}
    unk = d[d.m.map(lambda m: res.get(m) is None or (res.get(m) or {}).get("upset") is None)]
    sub["unclassified"] = {"rows": len(unk), "matches": int(unk.m.nunique())}
    crit["C6"] = all(sub[k]["d_hit"] is not None and sub[k]["d_hit"] >= 0 for k in ("upset", "ot", "rest"))
    halves = {"T1": dhit(d[d.day <= T1_END]), "T2": dhit(d[d.day > T1_END])}
    crit["C7"] = all(v >= 0 for v in halves.values())
    mae = mae_units(U, f, cand)
    c9 = mae["kpr_mae_cand"] <= mae["kpr_mae_live"]
    if cand in ("A1", "A2"):
        c9 = mae["rounds_mae_cand"] <= mae["rounds_mae_live"] and mae["kills_mae_cand"] <= mae["kills_mae_live"]
    crit["C9"] = bool(c9)
    return s, d, f, crit, sub, halves, mae


def fmt(s):
    return json.dumps({k: (r4(v) if isinstance(v, (float, np.floating)) else v) for k, v in s.items()}, default=str)


if __name__ == "__main__":
    print("MEASUREMENT", json.dumps(meas, default=str, indent=1))
    print("PARAMS", json.dumps(params, default=str, indent=1))
    print("C (chance mapping)", C)
    test_rows = [r for r in board if TEST0 <= r["day"] <= TEST1 and r["match_slug"] not in MOTIVATING]
    mot_rows = [r for r in board if r["match_slug"] in MOTIVATING]
    UT = P[(P.day >= TEST0) & (P.day <= TEST1) & ~P.match_slug.isin(MOTIVATING)].copy()
    UM = P[P.match_slug.isin(MOTIVATING)].copy()
    print("TEST rows", len(test_rows), "matches", len({r["match_slug"] for r in test_rows}), "motivating rows", len(mot_rows))
    out = {"measurement": meas, "params": params, "C": C, "test_rows": len(test_rows), "results": {}}
    for cand in CANDS:
        s, d, f, crit, sub, halves, mae = evaluate(cand, test_rows, UT)
        ps, pd_, pf = evaluate(cand, test_rows, UT, placebo=True, full=False)
        crit["C8"] = not (ps["ci_d_hit"][0] > 0)
        verdict = all(crit.values())
        ms_, md_, mf_ = evaluate(cand, mot_rows, UM, full=False) if mot_rows else (None, None, None)
        sl = {}
        for lab, mask in (("kills", d.prop == "kills"), ("hs", d.prop == "hs"), ("staked", d.units > 0)):
            x = d[mask]
            sl[lab] = score_rows(x, ci=False) if len(x) else {}
        out["results"][cand] = {"summary": s, "criteria": crit, "PASS": verdict, "subsets": sub, "halves": halves, "mae": mae,
                                "placebo": ps, "motivating": ms_, "slices": sl,
                                "units_with_factor": int((f != 1.0).sum()), "units_test": len(UT)}
        print(f"\n=== {cand}  PASS={verdict} ===")
        print("summary", fmt(s))
        print("criteria", crit)
        print("subsets", json.dumps(sub, default=str))
        print("halves", {k: r4(v) for k, v in halves.items()})
        print("mae", fmt(mae))
        print("placebo", fmt(ps))
        print("motivating", fmt(ms_) if ms_ else None)
        for k, v in sl.items():
            print("slice", k, fmt(v))
    json.dump(params, open(OUT / "fit_params.json", "w"), indent=1, default=str)
    json.dump(out, open(OUT / "score.json", "w"), indent=1,
              default=lambda v: v.item() if isinstance(v, (np.floating, np.integer, np.bool_)) else str(v))
    # forward: frozen candidate factors for every already-logged unit whose match starts after the freeze commit
    if FREEZE_FILE.exists():
        fz = ts(FREEZE_FILE.read_text().strip())
        UF = P[P.match_start.map(lambda s: ts(s) > fz)].copy()
        fw = []
        for cand in CANDS:
            f = factors(UF, cand)
            for r, v in zip(UF.itertuples(), f.values):
                fw.append({"cand": cand, "match_slug": r.match_slug, "player_id": r.player_id, "player": r.player,
                           "match_start": r.match_start, "lam_k": r.lam_k, "lam_h": r.lam_h, "f": float(v),
                           "lam_k_cand": None if pd.isna(r.lam_k) else r.lam_k * float(v),
                           "lam_h_cand": None if pd.isna(r.lam_h) else r.lam_h * float(v)})
        txt = "".join(json.dumps(x, default=str) + "\n" for x in fw)
        (OUT / "forward_predictions.jsonl").write_text(txt)
        print("forward predictions", len(fw), "units", len(UF), "sha256", hashlib.sha256(txt.encode()).hexdigest()[:16],
              "params sha256", hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16])
