"""audit.py-style consistency on the release code: 500 latest production forecasts regraded with lw-1 on."""
import json, sys, collections
sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\esp-accuracy")
import grader, pick_verdict, dist_view, sim, grade_display
from lq import adm
p = adm("/admin/forecasts_page.json?limit=1"); cut = p["cutoff_id"]
rows = adm("/admin/forecasts_page.json?after=%d&cutoff=%d&limit=500&inputs=1" % (cut - 500, cut))["rows"]
prob = collections.Counter(); n = 0; ex = collections.defaultdict(list)
UN = {"A+": 3, "A": 2, "A-": 1.5, "B+": 1, "B": .5, "B-": .5}
for f in rows:
    gi = f["inputs"] if isinstance(f["inputs"], dict) else json.loads(f["inputs"] or "null")
    if not gi: continue
    g = grader.grade_leg(json.loads(json.dumps(gi))); v = pick_verdict.verdict(g); n += 1
    pp = v["p"]; up, down = grader._cuts(g["line"]); mid = (up + down) / 2
    if g.get("lw1") is None: prob["no lw1 (non-perf)"] += 1
    if v["side"] == "More" and g["lambda_final"] < mid - 0.05: prob["More but projection under line"] += 1; ex["m"].append(f["id"])
    if v["side"] == "Less" and g["lambda_final"] > mid + 0.05: prob["Less but projection over line"] += 1; ex["l"].append(f["id"])
    mg = grader.grade_from_p(pp)
    if v["units"] > 0 and abs(v["units"] - min(UN.get(mg, 0), 9)) > 1e-9 and "capped" not in v["reason"]: prob["units != grade, no cap reason"] += 1
    if dist_view.view(g) is None: prob["no chart (dist does not reproduce chance)"] += 1
    s = sim.simulate_grade(g)
    if s and abs(s["p_more"] - g["probs_raw"]["p_more_no_push"]) > 0.02: prob["simulation != chance"] += 1
    if "Line-informed projection" not in v["reason"] and g.get("lw1") and v["state"] == "GRADE": prob["reason lacks line-informed wording"] += 1
print("regraded", n, dict(prob), {k: v[:5] for k, v in ex.items()})
