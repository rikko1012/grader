"""LW-1 (REGISTER.md section 2), CS2 P1 units, walk-forward by UTC start day. Run once."""
import json, sys
import numpy as np
from scipy.stats import norm
from evalx import *

P1 = json.load(open("p1.json"))
U = P1["rows"]
import os
SUF = os.environ.get("SUF", "")
if os.environ.get("CORR"):
    C = json.load(open("corrections_2399658.json"))
    U = [dict(u, over=C[str(u["forecast_id"])]["over_hltv"], actual=C[str(u["forecast_id"])]["actual_hltv"]) if str(u["forecast_id"]) in C else u for u in U]
    U = [u for u in U if u["over"] is not None]
FC = json.load(open("fc_p1.json"))


def zraw(u):
    f = FC[str(u["forecast_id"])]
    pr = f.get("probs_raw")
    q = None
    if pr:
        try:
            q = json.loads(pr).get("p_more_no_push") if isinstance(pr, str) else pr.get("p_more_no_push")
        except Exception:
            q = None
    if q is None: q = u["p"]
    return float(norm.ppf(min(max(q, .001), .999))), q is not None and pr is not None


rows = []
for u in U:
    z, has_raw = zraw(u)
    rows.append(dict(u=u, z=z, has_raw=has_raw, day=u["start"][:10], g=(u["prop"], u["scope"]), stat=u["prop"]))
rows.sort(key=lambda r: r["day"])
days = sorted({r["day"] for r in rows})
burn = [d for d in days if d <= "2026-10-13"]
score_days = [d for d in days if d > "2026-10-13"]
y_all = np.array([r["u"]["over"] for r in rows], float)
z_all = np.array([r["z"] for r in rows])
day_all = np.array([r["day"] for r in rows])
p_live = np.array([r["u"]["p"] for r in rows])
units_live = np.array([float(r["u"]["units"] or 0) for r in rows])
match = np.array([r["u"]["match"] for r in rows])
stat = np.array([r["stat"] for r in rows])
grp = np.array([r["stat"] + "|" + r["g"][1] for r in rows])
fav_live = np.maximum(p_live, 1 - p_live)
blocked = (fav_live >= .55) & (units_live <= 0)
p_lw = np.full(len(rows), np.nan)
params = {}
MIN = 300


def transform(a, w, z, st):
    p = norm.cdf(a + w * z)
    return np.clip(p, .22, .78) if st == "kills" else p


for d in score_days:
    tr = day_all < d
    te = day_all == d
    params[d] = {}
    for g in sorted(set(grp[te])):
        st = g.split("|")[0]
        m = tr & (grp == g)
        how = "group"
        if m.sum() < MIN:
            m = tr & (stat == st); how = "stat"
        idx = te & (grp == g)
        if m.sum() < MIN:
            p_lw[idx] = p_live[idx]; params[d][g] = ("live", int(m.sum())); continue
        a, w = fit_probit(z_all[m], y_all[m])
        params[d][g] = (how, int(m.sum()), round(float(a), 4), round(float(w), 4))
        p_lw[idx] = transform(a, w, z_all[idx], st)

S = day_all > "2026-10-13"
out = {"data_at": P1["meta"]["at"], "units_total": len(rows), "burn_days": burn, "score_days": score_days,
       "raw_z_share": float(np.mean([r["has_raw"] for r in rows])), "params": params}
y, pl, pc, mm, bl = y_all[S], p_live[S], p_lw[S], match[S], blocked[S]
out["paired_vs_live"] = paired(pc, pl, y, mm)
out["paired_vs_50"] = paired(pc, np.full_like(pc, .5), y, mm)
out["live_vs_50"] = paired(pl, np.full_like(pl, .5), y, mm)
out["calib_live"] = calib(pl, y); out["calib_lw1"] = calib(pc, y)
# live staked set from logged units (actual), and from the rule (check)
ul = units_live[S] > 0
rule_live = staked(pl, bl) > 0
out["live_rule_agreement"] = dict(logged=int(ul.sum()), rule=int(rule_live.sum()), both=int((ul & rule_live).sum()))
out["stake_live"] = stake_summary(pl, y, bl, mm, "live")
out["stake_lw1"] = stake_summary(pc, y, bl, mm, "lw1")
out["hit_diff_lw1_minus_live"] = hit_diff(pc, pl, y, bl, mm)
# per group
out["by_group"] = {}
for g in sorted(set(grp[S])):
    m = grp[S] == g
    if m.sum() < 30: continue
    out["by_group"][g] = dict(paired=paired(pc[m], pl[m], y[m], mm[m], B=1000), live=stake_summary(pl[m], y[m], bl[m], mm[m], "live"),
                              lw1=stake_summary(pc[m], y[m], bl[m], mm[m], "lw1"))
# per week
for wk, lo, hi in (("W1", "2026-10-14", "2026-10-17"), ("W2", "2026-10-18", "2026-10-24"), ("W3", "2026-10-25", "2026-10-31")):
    m = (day_all[S] >= lo) & (day_all[S] <= hi)
    if m.sum(): out[f"week_{wk}"] = dict(paired=paired(pc[m], pl[m], y[m], mm[m], B=1000), live=stake_summary(pl[m], y[m], bl[m], mm[m], "live"), lw1=stake_summary(pc[m], y[m], bl[m], mm[m], "lw1"))
np.save(f"p_lw1{SUF}.npy", p_lw)
json.dump(out, open(f"lw1_result{SUF}.json", "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
print(json.dumps({k: out[k] for k in ("paired_vs_live", "paired_vs_50", "live_vs_50", "live_rule_agreement", "stake_live", "stake_lw1", "hit_diff_lw1_minus_live")}, indent=1, default=str))
print("calib live", out["calib_live"]); print("calib lw1", out["calib_lw1"])
last = score_days[-1]; print("params last day", last, params[last])
for g, v in out["by_group"].items():
    print(g, "LL", round(v["paired"]["ll_gain"], 4), [round(x, 4) for x in v["paired"]["ll_ci"]], "live", v["live"].get("n"), v["live"].get("hit"), "lw1", v["lw1"].get("n"), v["lw1"].get("hit"))
for wk in ("W1", "W2", "W3"):
    v = out.get(f"week_{wk}")
    if v: print(wk, round(v["paired"]["ll_gain"], 4), [round(x, 4) for x in v["paired"]["ll_ci"]], "live", v["live"].get("n"), round(v["live"].get("hit") or 0, 3), "lw1", v["lw1"].get("n"), v["lw1"].get("hit"))
