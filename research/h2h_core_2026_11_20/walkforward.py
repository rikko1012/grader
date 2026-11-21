"""hc-1 roster-core H2H walk-forward test - runs FROZEN_DEFINITIONS.md exactly. Usage: python walkforward.py [data_dir] > out.json"""
import bisect, json, math, sys, pickle
from collections import defaultdict
from datetime import date
import numpy as np
import pandas as pd

D = sys.argv[1] if len(sys.argv) > 1 else "C:/Users/tikic/cs2_scout_ops/data/cs2_full/"
TRAIN_END, VALID_END = date(2026, 4, 1).toordinal(), date(2026, 7, 1).toordinal()   # Amendment 1

base = pickle.load(open(D + "proj_full/baseline.pkl", "rb"))[["matchid", "playerid", "teamid", "oppid", "day", "cm2_lam12"]]
base = base.dropna(subset=["cm2_lam12"])
pm = pd.read_csv(D + "work/player_map.csv.gz", usecols=["matchid", "playerid", "number", "k"])
pm = pm[pm["number"].isin([1, 2])].dropna(subset=["k"])
g = pm.groupby(["matchid", "playerid"]).agg(y=("k", "sum"), n=("number", "nunique")).reset_index()
g = g[g["n"] == 2][["matchid", "playerid", "y"]]
rows = base.merge(g, on=["matchid", "playerid"])
rows = rows[rows["cm2_lam12"] > 0].copy()
rows["r"] = (rows["y"] - rows["cm2_lam12"]) / rows["cm2_lam12"]
print("rows", len(rows), file=sys.stderr)

ctx = pd.read_csv(D + "work/team_match_ctx.csv.gz", usecols=["matchid", "teamid", "date", "lineup"]).dropna(subset=["lineup"])
ctx["day"] = pd.to_datetime(ctx["date"]).map(lambda t: t.toordinal())
lineup = {}
team_hist = defaultdict(list)
for m, t, d, s in zip(ctx["matchid"], ctx["teamid"], ctx["day"], ctx["lineup"]):
    ids = frozenset(int(x) for x in str(s).split(";") if x)
    lineup[(int(m), int(t))] = ids
    team_hist[int(t)].append((d, int(m), ids))
for t in team_hist:
    team_hist[t].sort()
team_days = {t: [x[0] for x in v] for t, v in team_hist.items()}


def roster_before(team, day):
    """the team's five in its previous series (strictly earlier day), else None"""
    v = team_hist.get(team)
    if not v:
        return None
    i = bisect.bisect_left(team_days[team], day) - 1
    while i >= 0:
        if len(v[i][2]) == 5:
            return v[i][2]
        return None            # the newest earlier series is the roster; an incomplete record = no roster (frozen rule)
    return None


by_p = defaultdict(list)
for x in rows.itertuples(index=False):
    by_p[int(x.playerid)].append((int(x.day), int(x.matchid), int(x.oppid), float(max(-1.0, min(1.0, x.r)))))
for p in by_p:
    by_p[p].sort()

feat = {k: [] for k in ("N", "C", "U")}
has = {k: [] for k in ("N", "C", "U")}
c_only = []
for x in rows.itertuples(index=False):
    p, day, opp = int(x.playerid), int(x.day), int(x.oppid)
    roster = roster_before(opp, day)
    hist = by_p[p]
    j = bisect.bisect_left(hist, (day,))
    acc = {k: [0.0, 0.0, 0] for k in ("N", "C", "U")}
    for d0, m0, o0, r0 in hist[:j]:
        age = day - d0
        if age > 365:
            continue
        n_ = o0 == opp
        c_ = bool(roster) and len(roster & lineup.get((m0, o0), frozenset())) >= 3
        w = 0.5 ** (age / 120)
        for k, ok in (("N", n_), ("C", c_), ("U", n_ or c_)):
            if ok:
                acc[k][0] += w * r0; acc[k][1] += w; acc[k][2] += 1
    for k in acc:
        s, w, n = acc[k]
        feat[k].append(s / (w + 2) if n else 0.0)
        has[k].append(n > 0)
    c_only.append(acc["C"][2] > 0 and acc["N"][2] == 0)
for k in feat:
    rows["f_" + k] = feat[k]
    rows["has_" + k] = has[k]
rows["c_only"] = c_only

tr, va, te = rows[rows.day < TRAIN_END], rows[(rows.day >= TRAIN_END) & (rows.day < VALID_END)], rows[rows.day >= VALID_END]


def ols(df, col):
    X = np.c_[np.ones(len(df)), df[col].values]
    b = np.linalg.lstsq(X, df["r"].values, rcond=None)[0]
    return float(b[1])


def grouped_ci(df, diff, n=2000, seed=7):
    rng = np.random.default_rng(seed)
    mids = df["matchid"].values
    u, inv = np.unique(mids, return_inverse=True)
    sums = np.bincount(inv, weights=diff, minlength=len(u))
    cnts = np.bincount(inv, minlength=len(u))
    est = sums.sum() / cnts.sum()
    bs = []
    for _ in range(n):
        idx = rng.integers(0, len(u), len(u))
        bs.append(sums[idx].sum() / cnts[idx].sum())
    return est, float(np.percentile(bs, 2.5)), float(np.percentile(bs, 97.5))


out = {"rows": len(rows), "train": len(tr), "valid": len(va), "test": len(te), "test_matches": int(te.matchid.nunique()),
       "baseline_test_mae": float(np.mean(np.abs(te.y - te.cm2_lam12))), "defs": {}}
for k in ("N", "C", "U"):
    bt, bv = ols(tr, "f_" + k), ols(va, "f_" + k)
    lam2 = te.cm2_lam12 * (1 + bt * te["f_" + k])
    diff = (np.abs(te.y - lam2) - np.abs(te.y - te.cm2_lam12)).values
    est, lo, hi = grouped_ci(te, diff)
    sub = te[te["has_" + k]]
    d2 = (np.abs(sub.y - sub.cm2_lam12 * (1 + bt * sub["f_" + k])) - np.abs(sub.y - sub.cm2_lam12)).values
    e2 = grouped_ci(sub, d2) if len(sub) else (None, None, None)
    out["defs"][k] = {"b_train": bt, "b_valid": bv, "sign_agrees": (bt > 0) == (bv > 0),
                      "test_dmae": est, "ci": [lo, hi], "pass": hi < 0 and (bt > 0) == (bv > 0),
                      "share_with_meeting": {"train": float(tr["has_" + k].mean()), "test": float(te["has_" + k].mean())},
                      "test_rows_with_meeting": int(len(sub)), "dmae_on_rows_with_meeting": e2[0], "ci_on_rows_with_meeting": [e2[1], e2[2]],
                      "mean_r_with_meeting_test": float(sub.r.mean()) if len(sub) else None}
# the rows where the roster rule finds a meeting the org rule misses
co = te[te.c_only]
out["c_only"] = {"test_rows": int(len(co)), "share_test": float(te.c_only.mean()), "train_rows": int(tr.c_only.sum()),
                 "mean_r": float(co.r.mean()) if len(co) else None, "corr_f_r": float(np.corrcoef(co.f_C, co.r)[0, 1]) if len(co) > 2 else None}
if len(co):
    bt = out["defs"]["C"]["b_train"]
    d3 = (np.abs(co.y - co.cm2_lam12 * (1 + bt * co.f_C)) - np.abs(co.y - co.cm2_lam12)).values
    e3 = grouped_ci(co, d3)
    out["c_only"].update(dmae=e3[0], ci=[e3[1], e3[2]])

# real lines
lr = pd.read_csv(D + "proj_full/line_rows.csv.gz")
lr = lr[(lr.stat == "kills") & (lr.scope.astype(str) == "12") & (lr.actual != lr.line)]
j = lr.merge(rows[["matchid", "playerid", "f_N", "f_C", "f_U", "has_N", "has_C", "has_U", "c_only"]], on=["matchid", "playerid"])
out["real_lines"] = {"decided_rows": int(len(lr)), "joined": int(len(j))}
for k in ("N", "C", "U"):
    s = j[j["has_" + k] & (j["f_" + k] != 0)]
    if not len(s):
        out["real_lines"][k] = None
        continue
    hit = ((s["f_" + k] > 0) == (s.actual > s.line)).astype(float).values
    est, lo, hi = grouped_ci(s.rename(columns={}), hit)
    out["real_lines"][k] = {"rows": int(len(s)), "hit": est, "ci": [lo, hi], "overs_pointed": int((s["f_" + k] > 0).sum())}
s = j[j.c_only & (j.f_C != 0)]
if len(s):
    hit = ((s.f_C > 0) == (s.actual > s.line)).astype(float).values
    est, lo, hi = grouped_ci(s, hit)
    out["real_lines"]["c_only"] = {"rows": int(len(s)), "hit": est, "ci": [lo, hi]}
print(json.dumps(out, indent=1))
