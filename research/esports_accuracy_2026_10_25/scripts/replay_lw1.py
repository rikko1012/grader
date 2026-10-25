"""Rule 4 replay + release impact. (1) research LW-1 chance (scipy, final params) == lw1.chance on every P1 unit;
(2) recent production forecasts regraded by the release code: lw1 OFF must reproduce the logged grade exactly (no unintended
change); lw1 ON must equal the transform of the logged raw chance; report the staked / side mix before and after."""
import json, sys, collections, os
sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\esp-accuracy")
import numpy as np
from scipy.stats import norm
import grader, pick_verdict, lw1, picklog
from lq import adm
print("release model tag:", picklog.MODEL_VERSION, "lw1 on:", bool(grader.CONFIG.get("lw1")))
P1 = json.load(open("p1.json")); FC = json.load(open("fc_p1.json"))
fin = json.load(open("lw1_final.json"))["params"]
mx = 0.0; n = 0
for u in P1["rows"]:
    pr = FC[str(u["forecast_id"])].get("probs_raw")
    if not pr: continue
    q = (json.loads(pr) if isinstance(pr, str) else pr).get("p_more_no_push")
    g = fin[f"{u['prop']}|{u['scope']}"]
    pr_ = norm.cdf(g["a"] + g["w"] * norm.ppf(min(max(q, .001), .999)))
    pc = lw1.chance(u["prop"], u["scope"], q)[0]
    mx = max(mx, abs(pr_ - pc)); n += 1
print("(1) research vs release transform: units", n, "max |diff|", mx)
p = adm("/admin/forecasts_page.json?limit=1"); cut = p["cutoff_id"]
rows = adm("/admin/forecasts_page.json?after=%d&cutoff=%d&limit=500&inputs=1" % (cut - 500, cut))["rows"]
off_same = off_n = 0; on_ok = on_n = 0; diffs = []; imp = collections.Counter()
for f in rows:
    if f.get("model_version") != "aa6696d5d6": continue
    gi = f["inputs"] if isinstance(f["inputs"], dict) else json.loads(f["inputs"] or "null")
    if not gi: continue
    g0 = grader.grade_leg(json.loads(json.dumps(gi)), dict(grader.CONFIG, lw1=None)); v0 = pick_verdict.verdict(g0)
    off_n += 1
    a = (round(g0.get("p_more") or 0, 3), v0.get("grade"), v0.get("units"), round(g0.get("lambda_final") or 0, 2))
    b = (round(float(f.get("p_more") or 0), 3), f.get("grade"), float(f.get("units") or 0), round(float(f.get("lambda") or 0), 2))
    if a == b: off_same += 1
    else: diffs.append((f["id"], a, b))
    g1 = grader.grade_leg(json.loads(json.dumps(gi))); v1 = pick_verdict.verdict(g1)
    if g1.get("lw1"):
        on_n += 1
        q = g0["probs_raw"]["p_more_no_push"]
        exp = lw1.chance(gi.get("prop"), gi.get("scope") or "12", q)[0]
        if gi.get("prop") == "kills": exp = min(max(exp, .22), .78)
        if round(exp, 3) == g1["p_more"]: on_ok += 1
        else: diffs.append(("on", f["id"], exp, g1["p_more"]))
    k = (gi.get("prop"), gi.get("scope") or "12")
    imp[k + ("live staked",)] += v0["units"] > 0; imp[k + ("lw1 staked",)] += v1["units"] > 0
    imp[k + ("n",)] += 1
    if v0["units"] > 0: imp[("live", v0["side"])] += 1
    if v1["units"] > 0: imp[("lw1", v1["side"])] += 1
    imp[("lw1 grade", v1["grade"])] += 1; imp[("live grade", v0["grade"])] += 1
print("(2) lw1 OFF reproduces logged:", off_same, "/", off_n, "| lw1 ON equals transform of the logged raw chance:", on_ok, "/", on_n)
print("first diffs:", diffs[:8])
for k in sorted(imp, key=str): print("  ", k, imp[k])
