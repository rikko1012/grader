"""T4b timing robustness (added after the registered run, on review): the offset pool for a row = unique player-markets
whose settlement time (settled_at) is BEFORE the row's own forecast_at (what a live version could see), instead of 'UTC days
before d'. Same form otherwise (first line per pm_key, >= 30 pm per stat x scope, c = log(sum actual / sum lambda))."""
import sys, math, json
sys.argv = [sys.argv[0]]
exec(open("r2_tests.py").read().split("# =============================================================================================== Base P")[0])
POOL = sorted([r for r in ROWS if fl(r["actual"]) is not None and r["settle_status"] == "graded" and fl(r["lambda"]) is not None],
              key=lambda r: r["unit_key"])
T4S = dict(SAVED)
cache = {}
for r in ROWS:
    fa = r["forecast_at"]
    key = (fa, r["stat"], r["scope"])
    if key not in cache:
        seen, a, m, n = set(), 0.0, 0.0, 0
        for q in POOL:
            if q["stat"] != r["stat"] or q["scope"] != r["scope"] or q["settled_at"] >= fa or q["pm_key"] in seen:
                continue
            seen.add(q["pm_key"]); a += fl(q["actual"]); m += fl(q["lambda"]); n += 1
        cache[key] = math.log(a / m) if n >= 30 and a > 0 and m > 0 else 0.0
    c = cache[key]
    if c and fl(r["lambda"]) is not None:
        T4S[r["unit_key"]] = nb_shift_dec(r, fl(r["lambda"]) * math.exp(c))
res = compare(ROWS, SAVED, T4S)
pr = res["playable_current"]
out = {"res": res}
json.dump(out, open("r2_t4b_timing.json", "w"), indent=1, default=str)
print("T4b-strict", pr["base"]["W"], pr["base"]["L"], pr["base"]["hit"], "->", pr["cand"]["W"], pr["cand"]["L"], pr["cand"]["hit"], "d", pr["pooled_delta"],
      pr["ci_day"], pr["ci_series"], "p", round(pr["p_signflip"], 4), "bad", pr["base"]["bad_days"], "->", pr["cand"]["bad_days"], "over",
      pr["base"]["over_share"], "->", pr["cand"]["over_share"], "vol", pr["volume_ratio"])
print(" all_graded", res["all_graded"]["pooled_delta"], res["all_graded"]["ci_series"], "by_mv", res["by_model_version"])
print(" daily base", pr["base"]["daily"]); print(" daily cand", pr["cand"]["daily"])
print(" split", res["orig_train_days"], res["audit_holdout_days"], "changes", res["changes"])
