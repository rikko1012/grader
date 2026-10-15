"""Controlled one-change-at-a-time comparisons vs the current model, identical rows. Anything fitted (Elo-gap slope, league
rounds) uses EARLIER months only (rolling origin); scored months 2026-01 .. 2026-10 (Nov-Dec 2025 = burn-in for fits).
Every candidate tried is listed here. Gains are current minus candidate (+ = candidate better)."""
import math, os, sys, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from study import R, comp, logscore, ci, FOLDS, LIVE

SCORED = [f for f in FOLDS if f >= "2026-01"]
GAP = lambda r: max(-1.0, min(1.0, (r["elo"][0] - r["elo"][1]) / 400.0))


def fit_b(rows, lam_key):
    num = sum(r[lam_key] * GAP(r) * (r["y"] - r[lam_key]) for r in rows); den = sum((r[lam_key] * GAP(r)) ** 2 for r in rows)
    return num / den if den else 0.0


# league as-of mean rounds per map (all earlier maps, 120-day half-life approximated by trailing 120 days)
by_day = defaultdict(list)
for r in R:
    by_day[r["day"]].append(r["rounds"] / 2.0)
days = sorted(by_day)
import bisect
cum = []; s = n = 0.0
for d in days:
    s += sum(by_day[d]); n += len(by_day[d]); cum.append((d, s, n))
def league_rounds(day):
    i = bisect.bisect_left(days, day) - 1; j = bisect.bisect_left(days, day - 120) - 1
    if i < 0: return LIVE["base_r"]
    s1, n1 = cum[i][1], cum[i][2]; s0, n0 = (cum[j][1], cum[j][2]) if j >= 0 else (0.0, 0.0)
    return (s1 - s0) / (n1 - n0) if n1 > n0 else LIVE["base_r"]
def team_rounds(r, view="n0", k=10.0):
    h = r[view]; lg = league_rounds(r["day"])
    a = (h["t1r"] + k * lg) / (h["t1w"] + k); b = (h["t2r"] + k * lg) / (h["t2w"] + k)
    return (a + b) / 2

CANDS = {
    "live (old-history view)": lambda r: comp(r, "n0"),
    "full history (repaired view)": lambda r: comp(r, "n"),
    "no shrinkage (raw rate)": lambda r: comp(r, "n0", k0=0),
    "shrink target 1.00 x pro average": lambda r: comp(r, "n0", pm=1.0),
    "no environment blend": lambda r: comp(r, "n0", env=False),
    "no opponent adjustment": lambda r: comp(r, "n0", opp=False),
    "opponent shrink 75 rounds": lambda r: comp(r, "n0", ko=75.0),
    "opponent shrink 1200 rounds": lambda r: comp(r, "n0", ko=1200.0),
    "rounds = league as-of mean": lambda r: comp(r, "n0", rounds_fn=lambda x: league_rounds(x["day"])),
    "rounds = both teams' as-of map length": lambda r: comp(r, "n0", rounds_fn=lambda x: team_rounds(x)),
    "HS share = league constant": lambda r: comp(r, "n0", hs_const=True),
    "baseline: pro average x 21.74 x 2": lambda r: {"lam": LIVE["pop"] * LIVE["base_r"] * 2, "lam_hs": LIVE["pop"] * LIVE["base_r"] * 2 * LIVE["pop_hs"]},
}
for nm, fn in CANDS.items():
    for r in R:
        c = fn(r); r[nm] = c["lam"]; r[nm + "|hs"] = c["lam_hs"]
# rolling-origin Elo-gap term on top of live (old-history) and of the full-history view
for base in ("live (old-history view)", "full history (repaired view)"):
    nm = base.split(" (")[0] + " + Elo team-strength term (rolling fit)"
    fits = {}
    for f in FOLDS:
        tr = [r for r in R if r["fold"] < f]
        fits[f] = fit_b(tr, base) if len(tr) > 2000 else 0.0
    for r in R:
        b = fits[r["fold"]]; m = 1 + b * GAP(r)
        r[nm] = r[base] * m; r[nm + "|hs"] = r[base + "|hs"] * m
    CANDS[nm] = None
    print(f"{nm}: fitted b by fold", {f: round(v, 3) for f, v in fits.items()})

BASE = "live (old-history view)"
ev = [r for r in R if r["fold"] in SCORED]
print(f"\nscored rows {len(ev)} / matches {len({r['m'] for r in ev})} (months {SCORED[0]}..{SCORED[-1]}); + = candidate better than the current model")
print("| candidate | kills MAE | MAE gain (95%) | months better | kills bias | log-score gain (95%) | HS MAE gain (95%) |\n|---|---:|---|---:|---:|---|---|")
res = {}
for nm in CANDS:
    if nm == BASE:
        m0 = sum(abs(r[BASE] - r["y"]) for r in ev) / len(ev)
        print(f"| {nm} | {m0:.3f} | - | - | {sum(r[BASE]-r['y'] for r in ev)/len(ev):+.2f} | - | - |"); continue
    g = lambda r: abs(r[BASE] - r["y"]) - abs(r[nm] - r["y"])
    gl = lambda r: logscore(r["y"], r[nm], LIVE["c"]) - logscore(r["y"], r[BASE], LIVE["c"])
    hs = [r for r in ev if r["y_hs"] is not None]
    gh = lambda r: abs(r[BASE + "|hs"] - r["y_hs"]) - abs(r[nm + "|hs"] - r["y_hs"])
    months = sum(1 for f in SCORED if sum(g(r) for r in ev if r["fold"] == f) > 0)
    mg = sum(g(r) for r in ev) / len(ev); c1 = ci(ev, g, B=400); c2 = ci(ev, gl, B=400); c3 = ci(hs, gh, B=400)
    res[nm] = {"mae_gain": mg, "ci": c1, "months_better": months}
    print(f"| {nm} | {sum(abs(r[nm]-r['y']) for r in ev)/len(ev):.3f} | {mg:+.3f} ({c1[0]:+.3f} to {c1[1]:+.3f}) | {months}/{len(SCORED)} | "
          f"{sum(r[nm]-r['y'] for r in ev)/len(ev):+.2f} | {sum(gl(r) for r in ev)/len(ev):+.4f} ({c2[0]:+.4f} to {c2[1]:+.4f}) | "
          f"{sum(gh(r) for r in hs)/len(hs):+.3f} ({c3[0]:+.3f} to {c3[1]:+.3f}) |")
import pickle
pickle.dump({r["m"] * 100000 + r["p"]: {k: r[k] for k in CANDS} for r in R}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cand_lams.pkl"), "wb"))
