"""Does the study reproduce the live projection? Production forecasts (recovered log, inputs) vs the study's as-of inputs for
the same HLTV player id and match day. N0-pinned versions (23bea2fe37 / 6b6a43f2a6) vs the n0 view; 6f6e2d39d7 (repaired
history live) vs the n view."""
import json, os, sys, statistics as st
from collections import defaultdict
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from study import R, comp
idx = defaultdict(list)
for r in R:
    idx[(r["p"], r["date"])].append(r)
cmp = defaultdict(list)
for x in open("ck/pages/forecasts_26575_inputs.jsonl", encoding="utf-8"):
    f = json.loads(x); gi = f.get("inputs") or {}; pf = gi.get("perf") or {}
    if pf.get("version") != "perf-2" or gi.get("scope", "12") != "12" or not pf.get("hltv_player_ids") or not f.get("match_start"):
        continue
    v = f["model_version"]
    view = "n0" if v in ("23bea2fe37", "6b6a43f2a6") else "n" if v == "6f6e2d39d7" and f["logged_at"] >= "2026-10-13T04:27" else None
    if not view:
        continue
    d = f["match_start"][:10]
    rs = [r for pid in pf["hltv_player_ids"] for r in idx.get((pid, d), [])]
    if len(rs) != 1:
        continue
    r = rs[0]; c = comp(r, view)
    cmp[view].append((pf["history_rounds_weighted"], r[view]["r"], pf["rate"], c["rate"], pf["opp_factor"], c["opp"], pf["lambda_kills"], c["lam"]))
for view, xs in cmp.items():
    rel = lambda i, j: st.median(abs(a[i] - a[j]) / max(abs(a[i]), 1e-9) for a in xs)
    exact = sum(1 for a in xs if abs(a[6] - a[7]) / a[6] < 0.005)
    print(f"{view}: {len(xs)} forecasts | median relative diff: history rounds {rel(0,1):.4f}, rate {rel(2,3):.4f}, opp {rel(4,5):.4f}, "
          f"projection {rel(6,7):.4f} | projection within 0.5%: {exact} ({exact/len(xs):.0%})")
