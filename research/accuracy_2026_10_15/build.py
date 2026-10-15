"""Accuracy study dataset (2026-10-15). One record per player x match (Maps 1-2, both maps with 10 players), with every
pre-match input of the live perf-2 projection computed from STRICTLY EARLIER days within the live 420-day window:
  - history view N0 ~ the live input (matches of the original top-200 export through 2026-10-08, plus later arrivals) and
  - history view N  = every match in the database (the 2026-10-13 backfill included).
Inputs per view: player kills / rounds (all, tonight's environment), HS / kills (hs_ok maps), opponent kills conceded / rounds,
both teams' recent map rounds; as-of Elo. Targets: Maps 1-2 kills, headshots, rounds."""
import collections, glob, json, math, os, pickle
from datetime import date
import numpy as np

ACC = os.path.dirname(os.path.abspath(__file__)); SP = os.path.dirname(ACC)
HL, WIN = 120.0, 420
orig = {s["match"] for s in pickle.load(open(os.path.join(SP, "perf_series.pkl"), "rb"))["series"]}
rows = []
for f in sorted(glob.glob(os.path.join(ACC, "dump", "20*.json"))):
    d = json.load(open(f)); c = d["cols"]; rows += [dict(zip(c, r)) for r in d["rows"]]
dn = lambda s: date.fromisoformat(s).toordinal()
maps = collections.defaultdict(list)
for r in rows:
    maps[(r["match"], r["map_no"])].append(r)
full = {k for k, v in maps.items() if len(v) == 10}
mdate = {r["match"]: r["date"] for r in rows}
N0_FROM = "2026-10-09"
in_n0 = lambda m: m in orig or mdate[m] >= N0_FROM
# per map: player history rows and team conceded / rounds
PH = collections.defaultdict(list)        # player -> (day, k, rounds, hs, lan, hs_ok, n0)
TC = collections.defaultdict(list)        # team -> (day, kills conceded by its opponents, rounds, n0)
TR = collections.defaultdict(list)        # team -> (day, rounds, n0)
conc = collections.defaultdict(lambda: [0, 0, 0, 0, None])
for (m, mn), v in maps.items():
    if (m, mn) not in full:
        continue
    day = dn(v[0]["date"]); n0 = in_n0(m)
    for r in v:
        rd = (r["own"] or 0) + (r["opp_score"] or 0)
        hs = r["hs"]; ok = hs is not None and not (hs == 0 and (r["k"] or 0) >= 12)
        PH[r["player"]].append((day, r["k"] or 0, rd, hs if hs is not None else 0, 1.0 if r["lan"] else 0.0, 1.0 if ok else 0.0, 1.0 if n0 else 0.0))
        c = conc[(m, mn, r["opp"])]; c[0] += r["k"] or 0; c[1] = rd; c[2] = day; c[3] = n0
    teams = {r["team"] for r in v}
    for t in teams:
        TR[t].append((day, v[0]["own"] + v[0]["opp_score"], 1.0 if n0 else 0.0))
for (m, mn, t), (k, rd, _, n0, _) in conc.items():
    TC[t].append((conc[(m, mn, t)][2], k, rd, 1.0 if n0 else 0.0))
A = lambda h: np.array(sorted(h), float) if h else np.zeros((0, 7))
PHA = {p: A(h) for p, h in PH.items()}; TCA = {t: A(h) for t, h in TC.items()}; TRA = {t: A(h) for t, h in TR.items()}


def wsum(a, day, cols, cond=None):
    """sum over rows with day-420 <= d < day of exp(-(day-d)/HL) * cols (cond: boolean column mask)."""
    if a is None or not len(a):
        return np.zeros(len(cols))
    i = np.searchsorted(a[:, 0], day, "left"); j = np.searchsorted(a[:, 0], day - WIN, "left")
    b = a[j:i]
    if cond is not None:
        b = b[cond(b)]
    if not len(b):
        return np.zeros(len(cols))
    w = np.exp(-(day - b[:, 0]) / HL)
    return (w[:, None] * b[:, cols]).sum(0)


# as-of Elo (start of day), all matches
series_res = {}
for (m, mn), v in maps.items():
    for r in v:
        series_res.setdefault((m, r["team"]), [r["date"], r["opp"], 0, 0])
for (m, mn), v in maps.items():
    t0 = v[0]["team"]
    for t in {r["team"] for r in v}:
        r0 = next(r for r in v if r["team"] == t)
        series_res[(m, t)][2] += int(r0["own"] > r0["opp_score"]); series_res[(m, t)][3] += 1
elo = collections.defaultdict(lambda: 1500.0); pre = {}
by_day = collections.defaultdict(dict)
for (m, t), (d, o, w, n) in series_res.items():
    by_day[d][(m, t)] = (o, w, n)
for d in sorted(by_day):
    for (m, t), (o, w, n) in by_day[d].items():
        pre[(m, t)] = (elo[t], elo[o])
    upd = {}
    for (m, t), (o, w, n) in by_day[d].items():
        if (m, o) in by_day[d] and t < o:
            ea = 1 / (1 + 10 ** ((elo[o] - elo[t]) / 400)); res = 1.0 if w > n - w else 0.0 if w < n - w else 0.5
            upd[t] = upd.get(t, 0) + 24 * (res - ea); upd[o] = upd.get(o, 0) - 24 * (res - ea)
    for t, x in upd.items():
        elo[t] += x
# targets
ser = collections.defaultdict(dict)
for (m, mn), v in maps.items():
    for r in v:
        s = ser[(m, r["player"])]
        s.setdefault("maps", {})[mn] = r
out = []
for (m, p), s in ser.items():
    mm = s["maps"]
    if 1 not in mm or 2 not in mm or (m, 1) not in full or (m, 2) not in full:
        continue
    r1, r2 = mm[1], mm[2]
    d = r1["date"]
    if d < "2025-11-01":
        continue
    day = dn(d); lan = bool(r1["lan"]); team, opp = r1["team"], r1["opp"]
    hs_ok = r1["hs"] is not None and r2["hs"] is not None and not ((r1["hs"] == 0 and r1["k"] >= 12) or (r2["hs"] == 0 and r2["k"] >= 12))
    rec = {"m": m, "p": p, "team": team, "opp": opp, "date": d, "day": day, "lan": lan, "n0_target": in_n0(m), "event": r1["event"],
           "y": r1["k"] + r2["k"], "y_hs": (r1["hs"] + r2["hs"]) if hs_ok else None, "y1": r1["k"],
           "y1_hs": r1["hs"] if (r1["hs"] is not None and not (r1["hs"] == 0 and r1["k"] >= 12)) else None,
           "rounds": r1["own"] + r1["opp_score"] + r2["own"] + r2["opp_score"], "won": int(r1["own"] > r1["opp_score"]) + int(r2["own"] > r2["opp_score"]),
           "elo": pre.get((m, team), (1500.0, 1500.0))}
    P_, O_, T1, T2 = PHA.get(p), TCA.get(opp), TRA.get(team), TRA.get(opp)
    for view, cond in (("n0", lambda b: b[:, 6] > 0.5), ("n", None)):
        c_env = (lambda b, lan=lan, cond=cond: (b[:, 4] == (1.0 if lan else 0.0)) & (cond(b) if cond else True))
        k, r = wsum(P_, day, [1, 2], cond)
        ke, re_ = wsum(P_, day, [1, 2], c_env)
        hk, hkk = wsum(P_, day, [3, 1], (lambda b, cond=cond: (b[:, 5] > 0.5) & (cond(b) if cond else True)))
        ck, cr = wsum(O_, day, [1, 2], (lambda b, cond=cond: (b[:, 3] > 0.5) if cond else np.ones(len(b), bool)))
        t1r, t1n = wsum(T1, day, [1, 0], (lambda b, cond=cond: (b[:, 2] > 0.5) if cond else np.ones(len(b), bool)))
        t2r, t2n = wsum(T2, day, [1, 0], (lambda b, cond=cond: (b[:, 2] > 0.5) if cond else np.ones(len(b), bool)))
        # t*n: weighted map count (sum of weights * day) is wrong - recompute plain weighted counts
        def wcount(a, cnd):
            if a is None or not len(a): return 0.0
            i = np.searchsorted(a[:, 0], day, "left"); j = np.searchsorted(a[:, 0], day - WIN, "left"); b = a[j:i]
            if cnd is not None: b = b[cnd(b)]
            return float(np.exp(-(day - b[:, 0]) / HL).sum()) if len(b) else 0.0
        c3 = (lambda b: b[:, 2] > 0.5) if cond else None
        rec[view] = {"k": k, "r": r, "ke": ke, "re": re_, "hk": hk, "hkk": hkk, "ck": ck, "cr": cr,
                     "t1r": t1r, "t1w": wcount(T1, c3), "t2r": t2r, "t2w": wcount(T2, c3),
                     "maps": int(np.sum((P_[:, 0] < day) & (P_[:, 0] >= day - WIN) & ((P_[:, 6] > 0.5) if cond else True))) if P_ is not None and len(P_) else 0}
    out.append(rec)
out.sort(key=lambda x: (x["date"], x["m"]))
pickle.dump(out, open(os.path.join(ACC, "series.pkl"), "wb"))
print("player-series", len(out), "matches", len({x['m'] for x in out}), "dates", out[0]["date"], out[-1]["date"],
      "| target in N0 set", sum(x["n0_target"] for x in out), "| with HS", sum(x["y_hs"] is not None for x in out))
