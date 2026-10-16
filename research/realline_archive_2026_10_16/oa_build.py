"""Opponent-adjusted kill rate (PROTOCOL_OA.md). Builds, per study record, the opponent-adjusted history sums and the joint
fit's upcoming-opponent factor, for views n0 and n. Read-only on the dumps; output oa_sums.pkl."""
import collections
import glob
import json
import math
import os
import pickle
import sys
from datetime import date, timedelta

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
SP = os.path.dirname(RL)
ACC = os.path.join(RL, "acc2")
sys.stdout.reconfigure(encoding="utf-8")
HL, WIN = 120.0, 420
K0, KO = 300.0, 300.0
dn = lambda s: date.fromisoformat(s).toordinal()

orig = {s["match"] for s in pickle.load(open(os.path.join(SP, "perf_series.pkl"), "rb"))["series"]}
rows = []
for f in sorted(glob.glob(os.path.join(ACC, "dump", "20*.json"))):
    d = json.load(open(f))
    c = d["cols"]
    rows += [dict(zip(c, r)) for r in d["rows"]]
maps = collections.defaultdict(list)
for r in rows:
    maps[(r["match"], r["map_no"])].append(r)
full = {k for k, v in maps.items() if len(v) == 10}
mdate = {r["match"]: r["date"] for r in rows}
in_n0 = lambda m: m in orig or mdate[m] >= "2026-10-09"

# map rows used by both the history sums and the joint fit (the study's PH rows + the opponent faced)
PH = collections.defaultdict(list)          # player -> (day, k, rounds, lan, n0, opp)
fit_rows = []                               # (day, player, opp, k, rounds, n0)
for (m, mn), v in maps.items():
    if (m, mn) not in full:
        continue
    day = dn(v[0]["date"])
    n0 = 1.0 if in_n0(m) else 0.0
    for r in v:
        rd = (r["own"] or 0) + (r["opp_score"] or 0)
        PH[r["player"]].append((day, r["k"] or 0, rd, 1.0 if r["lan"] else 0.0, n0, r["opp"]))
        fit_rows.append((day, r["player"], r["opp"], r["k"] or 0, rd, n0))
PHA = {p: np.array(sorted(h), float) for p, h in PH.items()}
F = np.array(fit_rows, float)
players = np.unique(F[:, 1]).astype(int)
teams = np.unique(np.concatenate([F[:, 2], np.array([t for p in PH.values() for *_, t in p], float)])).astype(int)
pidx = {p: i for i, p in enumerate(players)}
tidx = {t: i for i, t in enumerate(teams)}
fp = np.array([pidx[int(x)] for x in F[:, 1]])
ft = np.array([tidx[int(x)] for x in F[:, 2]])

# weekly refits (Mondays) of the joint model, strictly earlier maps
first = date(2025, 10, 27)
refits = [first + timedelta(days=7 * i) for i in range(0, 60) if first + timedelta(days=7 * i) <= date(2026, 10, 19)]
Dmat = {v: np.ones((len(refits), len(teams))) for v in ("n0", "n")}
for ri, R in enumerate(refits):
    Rd = R.toordinal()
    for v in ("n0", "n"):
        msk = (F[:, 0] < Rd) & (F[:, 0] >= Rd - WIN) & ((F[:, 5] > 0.5) if v == "n0" else True)
        if msk.sum() < 1000:
            continue
        w = np.exp(-(Rd - F[msk, 0]) / HL)
        k, rd, P_, T_ = F[msk, 3], F[msk, 4], fp[msk], ft[msk]
        mu = (w * k).sum() / (w * rd).sum()
        A = np.ones(len(players))
        Dd = np.ones(len(teams))
        sa, sd = K0 * mu, 5 * KO * mu
        for _ in range(12):
            num = np.bincount(P_, weights=w * k, minlength=len(players))
            den = np.bincount(P_, weights=w * rd * mu * Dd[T_], minlength=len(players))
            A = (num + sa) / (den + sa)
            num_t = np.bincount(T_, weights=w * k, minlength=len(teams))
            den_t = np.bincount(T_, weights=w * rd * mu * A[P_], minlength=len(teams))
            Dd = (num_t + sd) / (den_t + sd)
            expo = np.bincount(T_, weights=w * rd * A[P_], minlength=len(teams))
            Dd = Dd / ((Dd * expo).sum() / expo.sum())          # exposure-weighted mean 1
        Dmat[v][ri] = Dd
    if ri % 10 == 0:
        print("refit", R, "rows", int(msk.sum()), flush=True)
ref_ord = np.array([R.toordinal() for R in refits])

R = pickle.load(open(os.path.join(ACC, "series_m1.pkl"), "rb"))
out = {}
chk = []
for rec in R:
    day = rec["day"]
    ri = int(np.searchsorted(ref_ord, day, "right") - 1)        # latest refit on or before the match day
    a = PHA.get(rec["p"])
    res = {}
    for v in ("n0", "n"):
        if a is None or not len(a) or ri < 0:
            res[v] = None
            continue
        i = np.searchsorted(a[:, 0], day, "left")
        j = np.searchsorted(a[:, 0], day - WIN, "left")
        b = a[j:i]
        if v == "n0":
            b = b[b[:, 4] > 0.5]
        if not len(b):
            res[v] = {"K": 0.0, "R": 0.0, "Ka": 0.0, "Ke": 0.0, "Re": 0.0, "Kea": 0.0, "Dup": None}
            continue
        w = np.exp(-(day - b[:, 0]) / HL)
        Dh = Dmat[v][ri][[tidx[int(t)] for t in b[:, 5]]]
        env = b[:, 3] == (1.0 if rec["lan"] else 0.0)
        res[v] = {"K": float((w * b[:, 1]).sum()), "R": float((w * b[:, 2]).sum()), "Ka": float((w * b[:, 1] / Dh).sum()),
                  "Ke": float((w * b[:, 1])[env].sum()), "Re": float((w * b[:, 2])[env].sum()), "Kea": float((w * b[:, 1] / Dh)[env].sum()),
                  "Dup": float(Dmat[v][ri][tidx[rec["opp"]]]) if rec["opp"] in tidx else None}
        if len(chk) < 2000 and v == "n0":
            chk.append((res[v]["K"] - rec["n0"]["k"], res[v]["R"] - rec["n0"]["r"], res[v]["Ke"] - rec["n0"]["ke"], res[v]["Re"] - rec["n0"]["re"]))
    out[(rec["m"], rec["p"])] = res
c = np.abs(np.array(chk))
print("builder check vs the study's sums (max abs diff K, R, Ke, Re):", c.max(0))
pickle.dump({"sums": out, "refits": [str(x) for x in refits]}, open(os.path.join(RL, "oa_sums.pkl"), "wb"))
d = Dmat["n0"][-1]
print("latest refit opponent factors (n0): spread", np.percentile(d[d != 1], [5, 25, 50, 75, 95]).round(3))
