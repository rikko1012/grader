"""Post-hoc ATTRIBUTION of OA-2 (recorded as an extra configuration, development only): OA-3 = the CURRENT unadjusted
player rate x the joint fit's upcoming-opponent factor. OA-2 - OA-3 = the historical adjustment given the joint opponent
factor; OA-3 - current = the opponent factor's own effect."""
import os
import pickle
import sys

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RL)
sys.path.insert(0, os.path.join(RL, "acc2"))
from study import comp, LIVE, logscore, ci  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
S = pickle.load(open(os.path.join(RL, "oa_sums.pkl"), "rb"))["sums"]
R = {(r["m"], r["p"]): r for r in pickle.load(open(os.path.join(RL, "acc2", "series_m1.pkl"), "rb"))}
ev = pickle.load(open(os.path.join(RL, "oa_eval_rows.pkl"), "rb"))
for e in ev:
    r = R[(e["m"], e["p"])]
    s = S[(e["m"], e["p"])]["n0"]
    c0 = comp(r, "n0")
    e["oa3"] = c0["rate"] * (s["Dup"] if s["Dup"] is not None else 1.0) * LIVE["base_r"] * 2
A_obs = pickle.load(open(os.path.join(RL, "actionable.pkl"), "rb"))
E1 = pickle.load(open(os.path.join(RL, "eval20_rows.pkl"), "rb"))["E1"]
book = {o["p_id"] for o in A_obs} | {o["p_id"] for o in E1}
print("| segment | series | OA-3 vs current: MAE gain (95%); log-score gain (95%) | OA-2 vs OA-3: MAE gain (95%) |")
print("|---|---|---|---|")
for nm, f in (("all", lambda e: True), ("book-listed", lambda e: e["p"] in book), ("book-listed >= 600", lambda e: e["p"] in book and e["hist"] >= 600),
              ("established", lambda e: e["hist"] >= 600), ("thin", lambda e: e["hist"] < 600), ("top-200 set", lambda e: e["top"]), ("lower tier", lambda e: not e["top"])):
    g = [e for e in ev if f(e)]
    a = lambda e: abs(e["base"] - e["y"]) - abs(e["oa3"] - e["y"])
    l = lambda e: logscore(e["y"], e["oa3"], LIVE["c"]) - logscore(e["y"], e["base"], LIVE["c"])
    b = lambda e: abs(e["oa3"] - e["y"]) - abs(e["oa2"] - e["y"])
    print(f"| {nm} | {len(g)} | {np.mean([a(e) for e in g]):+.3f} {tuple(round(x, 3) for x in ci(g, a, B=1000))}; "
          f"{np.mean([l(e) for e in g]):+.4f} {tuple(round(x, 4) for x in ci(g, l, B=1000))} | {np.mean([b(e) for e in g]):+.3f} {tuple(round(x, 3) for x in ci(g, b, B=1000))} |")
