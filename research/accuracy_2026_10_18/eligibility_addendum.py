"""Eligibility-aligned addendum to the 2026-10-18 accuracy research (finish-fixes item 3). Read-only; no refit.

Exclusion rule (the shipped cm-2 rule): a prop is eligible only if the player has at least one HLTV map in the 420 days
before the match day - feature x[5] = log1p(history maps) > 0. The original research tables are kept as they are; this
recomputes the strict Maps 1-2 comparison on the eligible rows only.

Inputs (private research outputs, not in the repo): strict_development_predictions.json and model_features.pkl from
outputs/accuracy-study-2026-10-18. Usage: python eligibility_addendum.py <that folder> [out.json]"""
import json
import math
import pickle
import random
import sys
from pathlib import Path

D = Path(sys.argv[1])
B, SEED = 4000, 20261018
pred = json.loads((D / "strict_development_predictions.json").read_text())
with (D / "model_features.pkl").open("rb") as f:
    X = {(r["m"], r["p"]): r["x"] for r in pickle.load(f)}


def ll(p, y):
    p = min(max(p, 1e-12), 1 - 1e-12)
    return -math.log(p if y else 1 - p)


def block(rows):
    g = {}
    for r in rows:
        d = ll(r["p"], r["over"]) - ll(r["corrected_glm"], r["over"])
        g.setdefault(r["match"], []).append(d)
    keys = sorted(g)
    tot = sum(sum(v) for v in g.values()) / sum(len(v) for v in g.values())
    rnd, bs = random.Random(SEED), []
    for _ in range(B):
        s = n = 0
        for k in (rnd.choice(keys) for _ in keys):
            s += sum(g[k]); n += len(g[k])
        bs.append(s / n)
    bs.sort()
    return {"props": len(rows), "matches": len(keys), "log_loss_saved": sum(ll(r["p"], r["over"]) for r in rows) / len(rows),
            "log_loss_candidate": sum(ll(r["corrected_glm"], r["over"]) for r in rows) / len(rows),
            "gain": tot, "ci95": [bs[int(.025 * B)], bs[int(.975 * B) - 1]]}


rows = [r for r in pred if r.get("corrected_glm") is not None and r.get("over") in (0, 1) and r.get("scope") == "12"]
elig = lambda r: X[(r["hltv_match"], r["hltv_player"])][5] > 0
excluded = [{"match": r["match"], "hltv_match": r["hltv_match"], "hltv_player": r["hltv_player"], "player": r.get("player"),
             "prop": r["prop"], "line": r["line"]} for r in rows if not elig(r)]
out = {"rule": "eligible = at least one HLTV map in the 420 days before the match day (x[5] = log1p(maps) > 0); same as cm-2",
       "bootstrap": {"B": B, "seed": SEED, "unit": "match"}, "excluded": excluded, "original": {}, "eligible": {}}
for name, sel in (("kills", lambda r: r["prop"] == "kills"), ("hs", lambda r: r["prop"] == "hs"), ("combined", lambda r: True)):
    out["original"][name] = block([r for r in rows if sel(r)])
    out["eligible"][name] = block([r for r in rows if sel(r) and elig(r)])
print(json.dumps(out, indent=1))
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(json.dumps(out, indent=1))
