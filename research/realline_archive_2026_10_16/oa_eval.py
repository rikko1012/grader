"""Opponent-adjusted kill rate: evaluation (PROTOCOL_OA.md). Development evidence; read-only."""
import math
import os
import pickle
import sys

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RL)
sys.path.insert(0, os.path.join(RL, "acc2"))
import eval20 as E  # noqa: E402
from study import comp, LIVE, logscore, ci  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
S = pickle.load(open(os.path.join(RL, "oa_sums.pkl"), "rb"))["sums"]
R = pickle.load(open(os.path.join(RL, "acc2", "series_m1.pkl"), "rb"))
P = LIVE


def lam_oa(rec, view, variant):
    s = S.get((rec["m"], rec["p"]), {}).get(view)
    if not s:
        return None
    c0 = comp(rec, view)
    rate_all = (s["Ka"] + P["k0"] * P["pop"] * P["pm"]) / (s["R"] + P["k0"])
    rate = (s["Kea"] + P["ke"] * rate_all) / (s["Re"] + P["ke"])
    opp = c0["opp"] if variant == 1 else (s["Dup"] if s["Dup"] is not None else 1.0)
    return rate * opp * P["base_r"] * 2


ev = []
for r in R:
    if r["date"] < "2026-01-01" or r.get("y") is None:
        continue
    b = comp(r, "n0")
    l1, l2 = lam_oa(r, "n0", 1), lam_oa(r, "n0", 2)
    if l1 is None:
        continue
    bn = comp(r, "n")
    ln1 = lam_oa(r, "n", 1)
    ev.append({"m": r["m"], "p": r["p"], "y": r["y"], "y_hs": r.get("y_hs"), "base": b["lam"], "oa1": l1, "oa2": l2, "hsr": b["hsr"],
               "base_n": bn["lam"], "oa1_n": ln1, "hist": r["n0"]["r"], "top": r["n0_target"], "lan": r["lan"], "date": r["date"]})
A_obs = pickle.load(open(os.path.join(RL, "actionable.pkl"), "rb"))
E1 = pickle.load(open(os.path.join(RL, "eval20_rows.pkl"), "rb"))["E1"]
book_players = {o["p_id"] for o in A_obs} | {o["p_id"] for o in E1}
print(f"# Opponent-adjusted kill rate (OA)\n\nHLTV history scored 2026-01-01 .. {max(e['date'] for e in ev)}: {len(ev)} player-series / "
      f"{len({e['m'] for e in ev})} matches; book-listed players (an offered line Oct 2-16): {len(book_players)}")


def hist_table(rows, label, base="base", cands=("oa1", "oa2")):
    if len(rows) < 100:
        print(f"| {label} | {len(rows)} | - |")
        return
    out = [label, f"{len(rows)} / {len({e['m'] for e in rows})}"]
    mb = np.mean([abs(e[base] - e["y"]) for e in rows])
    out.append(f"{mb:.3f}, bias {np.mean([e[base] - e['y'] for e in rows]):+.2f}")
    for c in cands:
        g = lambda e: abs(e[base] - e["y"]) - abs(e[c] - e["y"])
        gl = lambda e: logscore(e["y"], e[c], P["c"]) - logscore(e["y"], e[base], P["c"])
        mg, (lo, hi) = np.mean([g(e) for e in rows]), ci(rows, g, B=1000)
        lg, (llo, lhi) = np.mean([gl(e) for e in rows]), ci(rows, gl, B=1000)
        out.append(f"{mg:+.3f} ({lo:+.3f}, {hi:+.3f}); log {lg:+.4f} ({llo:+.4f}, {lhi:+.4f}); bias {np.mean([e[c] - e['y'] for e in rows]):+.2f}")
    print("| " + " | ".join(out) + " |")


print("\n## 1. HLTV history, Maps 1-2 kills (positive = candidate better; MAE gain in kills, log-score gain per series)")
print("| segment | series / matches | current MAE, bias | OA-1 MAE gain (95%); log-score gain; bias | OA-2 MAE gain (95%); log-score gain; bias |")
print("|---|---|---|---|---|")
segs = [("all", lambda e: True), ("book-listed players", lambda e: e["p"] in book_players),
        ("book-listed, history >= 600 rounds", lambda e: e["p"] in book_players and e["hist"] >= 600),
        ("book-listed, history < 600 rounds", lambda e: e["p"] in book_players and e["hist"] < 600),
        ("thin history (< 600 rounds)", lambda e: e["hist"] < 600), ("established (>= 600 rounds)", lambda e: e["hist"] >= 600),
        ("original top-200 set", lambda e: e["top"]), ("lower tier", lambda e: not e["top"]), ("LAN", lambda e: e["lan"]), ("online", lambda e: not e["lan"])]
for nm, f in segs:
    hist_table([e for e in ev if f(e)], nm)
print("\nfull-history view (n; fh-1 inputs), OA-1 vs current formula on the same view:")
print("| segment | series / matches | current(n) MAE, bias | OA-1(n) MAE gain (95%); log-score gain; bias |")
print("|---|---|---|---|")
for nm, f in segs[:3] + segs[4:6]:
    hist_table([e for e in ev if f(e) and e["oa1_n"] is not None], nm, base="base_n", cands=("oa1_n",))
by_m = {}
for e in ev:
    by_m.setdefault(e["date"][:7], []).append(e)
print("\nOA-1 vs current by month (all / book-listed): MAE gain")
for mth in sorted(by_m):
    g = by_m[mth]
    gb = [e for e in g if e["p"] in book_players]
    print(f"  {mth}: {np.mean([abs(e['base'] - e['y']) - abs(e['oa1'] - e['y']) for e in g]):+.3f} ({len(g)})  | book-listed "
          f"{np.mean([abs(e['base'] - e['y']) - abs(e['oa1'] - e['y']) for e in gb]) if gb else float('nan'):+.3f} ({len(gb)})")

# ------------------------------------------------------------------ 2. real lines: decision-time forecasts at the original line
recmap = {(r["m"], r["p"]): r for r in R}
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def p_over(lam, line, prop):
    c = E.SPREAD(prop, "12")
    sd = c * math.sqrt(max(lam, 1.0))
    if line != int(line):
        po = 1 - Phi((math.floor(line) + 0.5 - lam) / sd)
        pu = 1 - po
    else:
        po, pu = 1 - Phi((line + 0.5 - lam) / sd), Phi((line - 0.5 - lam) / sd)
    p = po / (po + pu)
    return min(max(p, 0.22), 0.78) if prop == "kills" else p


def line_rows(src, tag):
    rows = []
    for o in src:
        if o["scope"] != "12":
            continue
        r = recmap.get((o["m"], o["p_id"]))
        if r is None:
            continue
        b = comp(r, "n0")
        l1, l2 = lam_oa(r, "n0", 1), lam_oa(r, "n0", 2)
        if l1 is None:
            continue
        f = 1.0 if o["prop"] == "kills" else b["hsr"]
        lb, la1, la2 = b["lam"] * f, l1 * f, l2 * f
        rows.append({"m": o["m"], "day": o["day"], "prop": o["prop"], "scope": "12", "line": o["line"], "y": o["y"], "actual": o["actual"],
                     "lam_b": lb, "lam_1": la1, "lam_2": la2, "p_cur": p_over(lb, o["line"], o["prop"]), "p_oa1": p_over(la1, o["line"], o["prop"]),
                     "p_oa2": p_over(la2, o["line"], o["prop"]), "p_live": o.get("p_live"), "flat": 0.5})
    E.add_base(rows)
    return rows


for src, tag in ((A_obs, "decision-time forecasts at their original offered line (timestamped)"), (E1, "offered-line archive (capture time not recorded; separate benchmark)")):
    LR = line_rows(src, tag)
    for prop in ("kills", "hs"):
        g = [r for r in LR if r["prop"] == prop]
        models = ["flat", "p_BASE", "p_cur", "p_oa1", "p_oa2"] + (["p_live"] if g and g[0].get("p_live") is not None else [])
        E.prob_table(g, models, ["p_cur", "p_BASE"], f"{tag} - {prop} Maps 1-2")
        E.selection(g, models[2:], f"{tag} - {prop}")
        bt = E.Boot(g)
        for k in ("lam_b", "lam_1", "lam_2"):
            b_ = bt.slope(np.array([r[k] - r["line"] for r in g]), np.array([r["actual"] - r["line"] for r in g]))
            dm = bt.ratio(np.array([(r[k] - r["actual"]) ** 2 - (r["line"] - r["actual"]) ** 2 for r in g]))
            mae = np.mean([abs(r[k] - r["actual"]) for r in g])
            print(f"  {prop} {k}: slope on (projection - line) {b_[0]:+.3f} ({b_[1]:+.3f}, {b_[2]:+.3f}); MSE - line MSE {dm[0]:+.2f} ({dm[1]:+.2f}, {dm[2]:+.2f}); MAE {mae:.3f}")
pickle.dump(ev, open(os.path.join(RL, "oa_eval_rows.pkl"), "wb"))
