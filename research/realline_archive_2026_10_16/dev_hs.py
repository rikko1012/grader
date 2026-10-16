"""Headshot model comparison (PROTOCOL_HS.md). Development on the decision-time cohort; hs-1 forward from production's P1
export. Read-only."""
import json
import math
import os
import pickle
import re
import sys
import urllib.request

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(RL)
sys.path.insert(0, RL)
sys.path.insert(0, os.path.join(RL, "acc2"))
import eval20 as E  # noqa: E402
from study import comp  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
C_HS = E.SPREAD("hs", "12")
HS1 = {"a": -0.15249, "b": 1.01202, "frozen_at": "2026-10-16T20:50:42+00:00"}
logit = lambda p: math.log(min(max(p, 1e-4), 1 - 1e-4) / (1 - min(max(p, 1e-4), 1 - 1e-4)))
sig = lambda x: 1 / (1 + math.exp(-x))

A_obs = pickle.load(open(os.path.join(RL, "actionable.pkl"), "rb"))
R = pickle.load(open(os.path.join(RL, "acc2", "series_m1.pkl"), "rb"))
rec = {(r["m"], r["p"]): r for r in R}
rows = []
for o in A_obs:
    if o["prop"] != "hs" or o["scope"] != "12" or o["lam"] is None:
        continue
    r = rec.get((o["m"], o["p_id"]))
    if r is None:
        continue
    lamF = comp(r, "n")["lam_hs"]
    rows.append(dict(o, lam_full=lamF, x_live=logit(o["p_live"]),
                     z_rec=(o["lam"] - o["line"]) / (C_HS * math.sqrt(max(o["lam"], 1.0))),
                     z_full=(lamF - o["line"]) / (C_HS * math.sqrt(max(lamF, 1.0)))))
for r in rows:
    r.pop("p_BASE", None)
    r.pop("scored", None)
E.add_base(rows)


def fit(train, xk, ridge, prior_b):
    """MLE of logit p = a + b x with ridge toward (0, prior_b)."""
    x = np.array([r[xk] for r in train])
    y = np.array([r["y"] for r in train], float)
    a, b = 0.0, prior_b
    for _ in range(80):
        q = 1 / (1 + np.exp(-(a + b * x)))
        w = q * (1 - q)
        g = np.array([np.sum(y - q) - ridge * a, np.sum((y - q) * x) - ridge * (b - prior_b)])
        H = np.array([[np.sum(w) + ridge, np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x) + ridge]])
        st = np.linalg.solve(H, g)
        a, b = a + st[0], b + st[1]
        if np.abs(st).max() < 1e-10:
            break
    return a, b


MODELS = {"RECAL-LIVE": ("x_live", 1.0), "Z-REC": ("z_rec", 0.0), "Z-FULL": ("z_full", 0.0)}
days = sorted({r["day"] for r in rows})
fits = []
for d in days:
    train = [r for r in rows if r["day"] < d]
    test = [r for r in rows if r["day"] == d]
    if len(train) < 300:
        continue
    td = sorted({r["day"] for r in train})
    itr, ite = [r for r in train if r["day"] < td[-1]], [r for r in train if r["day"] == td[-1]]
    for nm, (xk, pb) in MODELS.items():
        lr = min((0.3, 1, 3, 10, 30, 100), key=lambda lr: sum(E.ll(sig(fit(itr, xk, lr, pb)[0] + fit(itr, xk, lr, pb)[1] * r[xk]), r["y"]) for r in ite))
        a, b = fit(train, xk, lr, pb)
        fits.append((d, nm, lr, round(a, 4), round(b, 4)))
        for r in test:
            r[f"p_{nm}"] = sig(a + b * r[xk])
    for r in test:
        r["scored"] = True
sc = [r for r in rows if r.get("scored")]
print(f"# Headshot model comparison\n\nHS Maps 1-2 decision-time forecasts: {len(rows)} obs / {len({r['m'] for r in rows})} matches; "
      f"scored (>= 300 earlier observations): {len(sc)} obs / {len({r['m'] for r in sc})} matches, days {sc[0]['day']} .. {sc[-1]['day']}")
print("fits per scored day (day, model, ridge, a, b):")
for f in fits:
    print("  ", f)
models = ["flat", "p_BASE", "p_live", "p_RECAL-LIVE", "p_Z-REC", "p_Z-FULL"]
for r in rows:
    r["flat"] = 0.5
res = E.prob_table(sc, models, ["p_live", "p_BASE"], "HS Maps 1-2, decision-time forecasts at the original line (development)")
sel = E.selection(sc, models[2:], "HS Maps 1-2 development")
for era, f in (("Railway era", lambda r: r["host"] == "railway"), ("Render era", lambda r: r["host"] == "render")):
    g = [r for r in sc if f(r)]
    if len(g) >= 60:
        E.prob_table(g, ["p_BASE", "p_live", "p_RECAL-LIVE", "p_Z-FULL"], ["p_live", "p_BASE"], f"{era} (development)")
        E.selection(g, ["p_live", "p_RECAL-LIVE", "p_Z-FULL"], f"{era} (development)")

# ------------------------------------------------------------------ hs-1 forward (production's P1 export; fh-1's saved projection)
T = re.search(r'T\s*=\s*"([^"]+)"', open(os.path.join(SP, "day_recap.py")).read()).group(1)
out, after = [], 0
while after is not None:
    req = urllib.request.Request(f"https://cs2-scout.onrender.com/admin/learning/p1_units.json?after={after}&limit=2000", headers={"X-Admin-Token": T})
    with urllib.request.urlopen(req, timeout=180) as resp:
        pg = json.load(resp)
    out += pg["rows"]
    after = pg["next_after"]
fh = next((c["id"] for c in pg["challengers"] if c["family"] == "full-history-1"), None)
fw = []
for u in out:
    if u["prop"] != "hs" or u["scope"] != "12":
        continue
    v = (u.get("challengers") or {}).get(fh) or {}
    if v.get("lam") is None or not (u["logged"] > HS1["frozen_at"] and u["start"] > HS1["frozen_at"]):
        continue
    lam = float(v["lam"])
    z = (lam - u["line"]) / (C_HS * math.sqrt(max(lam, 1.0)))
    fw.append(dict(u, p_hs1=sig(HS1["a"] + HS1["b"] * z), p_live=u["p"], y=u["over"]))
print(f"\n## hs-1 FORWARD (export built {pg['at']}; P1 HS Maps 1-2 units logged and started after its freeze {HS1['frozen_at']} "
      f"with fh-1's saved projection): {len(fw)} units / {len({u['match'] for u in fw})} matches")
if len(fw) >= 30:
    for u in fw:
        u["m"] = u["match"]
    E.add_base(fw)
    E.prob_table(fw, ["p_BASE", "p_live", "p_hs1"], ["p_live", "p_BASE"], "hs-1 forward")
    E.selection(fw, ["p_live", "p_hs1"], "hs-1 forward")
pickle.dump({"rows": rows, "fits": fits, "forward": fw}, open(os.path.join(RL, "dev_hs.pkl"), "wb"))
