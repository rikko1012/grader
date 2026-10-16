"""Reproduction check before rollout: production cm1.py on the research's IDENTICAL inputs (the acc2 dump) must reproduce the
research implementation (study sums, oa_build sums, models.feats, the frozen GLM, the line probability). Read-only."""
import contextlib
import glob
import json
import math
import os
import sys
import time
from datetime import date

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\eval-pipeline")
import cm1  # noqa: E402

sys.path.insert(0, RL)
with contextlib.redirect_stdout(open(os.devnull, "w", encoding="utf-8")):
    import models as M  # noqa: E402  (research pipeline: R, S, feats, p_line)
sys.stdout = sys.__stdout__
sys.stdout.reconfigure(encoding="utf-8")
rows = []
for f in sorted(glob.glob(os.path.join(RL, "acc2", "dump", "20*.json"))):
    d = json.load(open(f))
    c = d["cols"]
    ix = [c.index(k) for k in ("match", "date", "lan", "map_no", "player", "team", "opp", "k", "hs", "own", "opp_score")]
    rows += [tuple(r[i] for i in ix) for r in d["rows"]]
t0 = time.time()
T = cm1.build_tables(rows, built_at="2026-10-17T00:00:00+00:00", through=date(2026, 10, 19))
print(f"tables built from {len(rows)} research rows in {time.time() - t0:.1f}s: {T['meta']}")
frozen = json.load(open(os.path.join(RL, "cm1_frozen.json")))
mu, sd, coef, icpt = (np.array(frozen[k]) for k in ("mean", "std", "coef")) + (frozen["intercept"],) if False else (np.array(frozen["mean"]), np.array(frozen["std"]), np.array(frozen["coef"]), frozen["intercept"])
recs = [r for r in M.R if r["date"] >= "2026-09-01" and r.get("y") is not None]
maxd = {}
nbad, n = 0, 0
times = []
for r in recs:
    s_res = (M.S.get((r["m"], r["p"])) or {}).get("n")
    if not s_res:
        continue
    t1 = time.perf_counter()
    s = cm1.inputs(T, [r["p"]], r["team"], r["opp"], r["day"], r["lan"])
    times.append(time.perf_counter() - t1)
    if s.get("ineligible"):
        nbad += 1
        continue
    n += 1
    h = r["n"]
    pairs = {"k": (s["k"], h["k"]), "r": (s["r"], h["r"]), "ke": (s["ke"], h["ke"]), "re": (s["re"], h["re"]), "hk": (s["hk"], h["hk"]),
             "hkk": (s["hkk"], h["hkk"]), "ck": (s["ck"], h["ck"]), "cr": (s["cr"], h["cr"]), "t1r": (s["t1r"], h["t1r"]), "t1w": (s["t1w"], h["t1w"]),
             "t2r": (s["t2r"], h["t2r"]), "t2w": (s["t2w"], h["t2w"]), "maps": (s["maps"], h["maps"]), "ka": (s["ka"], s_res["Ka"]),
             "dup": (s["dup"] if s["dup"] is not None else 1.0, s_res["Dup"] if s_res["Dup"] is not None else 1.0),
             "elo_own": (s["elo_own"], r["elo"][0]), "elo_opp": (s["elo_opp"], r["elo"][1])}
    xr = M.feats(r)
    xp = cm1.features(s)
    pairs.update({f"x{i}": (a, b) for i, (a, b) in enumerate(zip(xp, xr))})
    lam_r = float(np.exp(icpt + np.dot(coef, (np.array(xr) - mu) / sd)))
    pairs["lambda"] = (cm1.predict(xp), lam_r)
    for k, (a, b) in pairs.items():
        dv = abs(a - b) / max(1.0, abs(b))
        maxd[k] = max(maxd.get(k, 0), dv)
print(f"\nrecords compared: {n} (research test period Sep 1 - Oct 16); ineligible in production: {nbad}")
print("max relative difference per quantity:", {k: f"{v:.2e}" for k, v in sorted(maxd.items(), key=lambda kv: -kv[1])})
print(f"inputs() time per forecast: median {np.median(times) * 1e3:.2f} ms, 99th pct {np.percentile(times, 99) * 1e3:.2f} ms")
# line probability: research p_line (fit B in the study; here the frozen spread) vs cm1.p_over
A_obs = M.A_obs
dp = 0
cnt = 0
for o in A_obs:
    if o["scope"] != "12":
        continue
    r = M.recmap.get((o["m"], o["p_id"]))
    if r is None or M.feats(r) is None:
        continue
    s = cm1.inputs(T, [r["p"]], r["team"], r["opp"], r["day"], r["lan"])
    if s.get("ineligible"):
        continue
    sc = cm1.score(s, o["line"], o["prop"])
    xr = M.feats(r)
    lam_k = float(np.exp(icpt + np.dot(coef, (np.array(xr) - mu) / sd)))
    hsn = M.comp(r, "n")["hsr"]
    lam = lam_k if o["prop"] == "kills" else lam_k * hsn
    cc = frozen["spread_c_kills"] if o["prop"] == "kills" else 1.264 * frozen["spread_c_kills"] / 1.457
    pr = M.p_line(lam, cc, o["line"], o["prop"])
    dp = max(dp, abs(sc["p_more"] - pr))
    cnt += 1
print(f"line probabilities compared on {cnt} decision-time forecasts: max |cm1 - research| = {dp:.2e}")
# leakage probe on the production tables: adding rows on / after the match day must not change any sum
r = recs[len(recs) // 2]
s0 = cm1.inputs(T, [r["p"]], r["team"], r["opp"], r["day"], r["lan"])
d = date.fromordinal(r["day"]).isoformat()
fut = [(999999901, d, r["lan"], 1, r["p"], r["team"], r["opp"], 60, 30, 13, 0)] + [(999999901, d, r["lan"], 1, 10_000_000 + i, r["team"], r["opp"], 1, 0, 13, 0) for i in range(4)] + \
      [(999999901, d, r["lan"], 1, 20_000_000 + i, r["opp"], r["team"], 0, 0, 0, 13) for i in range(5)]
T2 = cm1.build_tables(rows + fut, built_at="2026-10-17T00:00:00+00:00", through=date(2026, 10, 19))
s1 = cm1.inputs(T2, [r["p"]], r["team"], r["opp"], r["day"], r["lan"])
diff = {k: (s0[k], s1[k]) for k in s0 if s0[k] != s1[k] and k not in ("refit",)}
print(f"leakage probe (a 10-player map on the match day itself added): changed sums {diff if diff else 'none'}")
