"""Why live's recommended hit differs between P1 (443 / 69, 50.1%) and the E2 P1-like subset (429 / 61, 54.1%): which P1
units are missing from E2 and how they did. Read-only."""
import collections
import json
import os
import pickle
import sys

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(RL)
sys.path.insert(0, RL)
import eval20 as E  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
P1 = json.load(open(os.path.join(SP, "audit1016", "p1_units_1937.json")))["rows"]
F = json.load(open(os.path.join(SP, "audit1016", "forecasts.json")))
f2p = {f["id"]: f["pick_id"] for f in F}
D = pickle.load(open(os.path.join(RL, "eval20_rows.pkl"), "rb"))
E2 = D["E2"]
ep = {r["pick_id"]: r for r in E2 if r["host"] == "render"}
ours_dropped = pickle.load(open(os.path.join(RL, "ours.pkl"), "rb"))
in_e2, out_e2 = [], []
for u in P1:
    pid = f2p.get(u["forecast_id"])
    (in_e2 if pid in ep else out_e2).append(dict(u, pick=pid))
sel = lambda rs: [r for r in rs if max(r["p"], 1 - r["p"]) >= 0.57]
hit = lambda r: r["over"] if r["p"] >= 0.5 else 1 - r["over"]
for nm, rs in (("P1 units whose pick is in E2", in_e2), ("P1 units NOT in E2", out_e2)):
    s = sel(rs)
    print(f"{nm}: {len(rs)} units / {len({r['match'] for r in rs})} matches; recommended {len(s)} / {len({r['match'] for r in s})} matches, "
          f"hit {np.mean([hit(r) for r in s]) if s else float('nan'):.3f}")
# why are they not in E2?  (the pick has no HLTV ids, a later forecast of the same unit + platform replaced it, a copy, ...)
picks = {p["id"]: p for p in json.load(open(os.path.join(SP, "audit1016", "picks.json")))}
why = collections.Counter()
by_day = collections.Counter()
for u in out_e2:
    p = picks.get(u["pick"])
    by_day[u["start"][:10]] += 1
    if p is None:
        why["pick not found"] += 1
        continue
    m = None
    why["other"] += 0
print("not-in-E2 by match day:", dict(sorted(by_day.items())))
s_out = sel(out_e2)
print("not-in-E2 recommended by match day:", dict(sorted(collections.Counter(r["start"][:10] for r in s_out).items())))
# E2's own settlement vs P1's for the overlap
diff = sum(1 for u in in_e2 if ep[u["pick"]]["y"] != u["over"] and abs(ep[u["pick"]]["line"] - u["line"]) < 1e-9)
print("overlap with the same line where E2's HLTV outcome differs from P1's settlement:", diff)
same_line = sum(1 for u in in_e2 if abs(ep[u["pick"]]["line"] - u["line"]) < 1e-9)
print("overlap rows at the same line:", same_line, "of", len(in_e2))

# same pick: compare line, chance and outcome
cmp = collections.Counter()
ex = []
for u in in_e2:
    e = ep[u["pick"]]
    same_line = abs(e["line"] - u["line"]) < 1e-9
    same_p = abs(e["p_live"] - u["p"]) < 1e-6
    cmp[("same line" if same_line else "different line", "same chance" if same_p else "different chance", "same outcome" if e["y"] == u["over"] else "different outcome")] += 1
    if not same_p and len(ex) < 5:
        ex.append((u["forecast_id"], u["logged"], u["p"], u["line"], e["logged"], e["p_live"], e["line"]))
for k, v in cmp.most_common():
    print(v, k)
for x in ex:
    print("  P1 forecast", x[0], x[1], "p", x[2], "line", x[3], "| E2 logged", x[4], "p", x[5], "line", x[6])
