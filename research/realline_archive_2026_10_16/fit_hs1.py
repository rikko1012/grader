"""ONE fit (no refits) of the proposed HS-only prospective test 'hs-1': logit P(Over) = a + b z, z = (lambda_full - line) /
(c_hs * sqrt(lambda_full)), on every E1 HS Maps 1-2 observation (Oct 2-16 development data); ridge from {0.3,1,3,10,30,100}
chosen leave-last-day-out. Prints the coefficients and a data hash for the proposal. Development data only."""
import hashlib
import json
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eval20 as E  # noqa: E402

D = pickle.load(open(os.path.join(E.RL, "eval20_rows.pkl"), "rb"))
rows = [r for r in D["E1"] if r["prop"] == "hs" and r["scope"] == "12"]
days = sorted({r["day"] for r in rows})
tr = [r for r in rows if r["day"] < days[-1]]
te = [r for r in rows if r["day"] == days[-1]]
best = min((0.3, 1, 3, 10, 30, 100), key=lambda lr: sum(E.ll(E.corr_p(r, E.fit_corr(tr, "lam_n", lr), "lam_n"), r["y"]) for r in te))
W = E.fit_corr(rows, "lam_n", best)
h = hashlib.sha256(json.dumps(sorted((r["m"], r["p_id"], r["line"], r["y"]) for r in rows)).encode()).hexdigest()[:12]
a, b = W["hs"]
print(json.dumps({"rows": len(rows), "matches": len({r["m"] for r in rows}), "days": [days[0], days[-1]], "ridge": best,
                  "a": round(a, 5), "b": round(b, 5), "data_hash": h, "c_hs": E.SPREAD("hs", "12")}, indent=1))
for z in (-1.0, -0.5, 0.0, 0.5, 1.0):
    import math
    print(f"  z {z:+.1f}: P(Over) {1 / (1 + math.exp(-(a + b * z))):.3f}")
