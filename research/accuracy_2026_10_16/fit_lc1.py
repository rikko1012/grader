"""lc-1 frozen fit: CORR on every settled real offered line (P1 units), ridge chosen leave-last-day-out -> lc1_fit.json
(= perf_shadow.LC1 / learning.LINE_CORRECTION). Run from this folder with RL_DATA pointing at the export."""
import hashlib, json, math
import realline as R

units = R.dedupe(R.rows_for("board"))
lr = R.choose_lambda(units, "CORR")
W = R.fit_by_stat(units, "CORR", lr)
key = sorted((r["f"], r["over"]) for r in units)
h = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:12]
print("lambda_r", lr, "units", len(units), "matches", len({r["match"] for r in units}), "data hash", h)
for s, w in W.items():
    print(s, [round(float(x), 5) for x in w], [round(1 / (1 + math.exp(-(w[0] + w[1] * z))), 4) for z in (-1.5, 0, 1.5)])
json.dump({"lambda_r": lr, "w": {s: [float(x) for x in w] for s, w in W.items()}, "units": len(units),
           "matches": len({r["match"] for r in units}), "data_hash": h, "cutoff_day": max(r["day"] for r in units)},
          open("lc1_fit.json", "w"), indent=1)
