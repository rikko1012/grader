"""Complete-model comparison (PROTOCOL_MODELS.md, written before fitting). Development evidence; read-only."""
import math
import os
import pickle
import sys
import warnings

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import PoissonRegressor

warnings.filterwarnings("ignore")
RL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RL)
sys.path.insert(0, os.path.join(RL, "acc2"))
import eval20 as E  # noqa: E402
from study import comp, LIVE, ci  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
R = pickle.load(open(os.path.join(RL, "acc2", "series_m1.pkl"), "rb"))
S = pickle.load(open(os.path.join(RL, "oa_sums.pkl"), "rb"))["sums"]
P = LIVE
FEATS = ["log_rate_all", "log_rate_env", "log_opp", "log_hist_r", "log_env_r", "log_maps", "lan", "elo_own", "elo_opp", "elo_gap",
         "log_team_rpm", "log_opp_rpm", "log_adj_rate", "log_joint_opp"]
MONO = {"log_rate_all": 1, "log_rate_env": 1, "log_opp": 1, "log_adj_rate": 1, "log_joint_opp": 1}


def feats(r):
    h = r["n"]
    c = comp(r, "n")
    s = (S.get((r["m"], r["p"])) or {}).get("n")
    if not s:
        return None
    adj = (s["Ka"] + P["k0"] * P["pop"] * P["pm"]) / (s["R"] + P["k0"])
    trpm = (h["t1r"] + 21.74 * 10) / (h["t1w"] + 10)
    orpm = (h["t2r"] + 21.74 * 10) / (h["t2w"] + 10)
    return [math.log(c["rate_all"]), math.log(c["rate"]), math.log(c["opp"]), math.log1p(h["r"]), math.log1p(h["re"]), math.log1p(h["maps"]),
            1.0 if r["lan"] else 0.0, r["elo"][0] / 400, r["elo"][1] / 400, (r["elo"][0] - r["elo"][1]) / 400, math.log(max(trpm, 1)),
            math.log(max(orpm, 1)), math.log(adj), math.log(s["Dup"] if s["Dup"] else 1.0)]


data = []
for r in R:
    if r.get("y") is None or r["date"] < "2025-11-01":
        continue
    x = feats(r)
    if x is None:
        continue
    c0, cn = comp(r, "n0"), comp(r, "n")
    data.append({"m": r["m"], "p": r["p"], "date": r["date"], "y": r["y"], "x": x, "cur": c0["lam"], "cur_full": cn["lam"],
                 "hsr0": c0["hsr"], "hsrn": cn["hsr"], "hist": r["n0"]["r"], "top": r["n0_target"], "lan": r["lan"]})
X = np.array([d["x"] for d in data])
Y = np.array([d["y"] for d in data], float)
D = np.array([d["date"] for d in data])
print(f"# Complete-model comparison\n\nrecords with a Maps 1-2 kills target: {len(data)} ({min(D)} .. {max(D)})")


def logscore_v(y, lam, c):
    sd = c * np.sqrt(np.maximum(lam, 1.0))
    pk = np.array([Phi((a + 0.5 - l) / s) - Phi((a - 0.5 - l) / s) for a, l, s in zip(y, lam, sd)])
    z = np.array([1 - Phi((-0.5 - l) / s) for l, s in zip(lam, sd)])
    return np.log(np.maximum(pk / z, 1e-12))


def spread_c(y, lam):
    return float(np.sqrt(np.mean((y - lam) ** 2 / np.maximum(lam, 1.0))))


def fit_glm(Xtr, ytr, alpha):
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    m = PoissonRegressor(alpha=alpha, max_iter=1000).fit((Xtr - mu) / sd, ytr)
    return lambda Xq: m.predict((Xq - mu) / sd)


def fit_gbm(Xtr, ytr, leaves, iters):
    m = HistGradientBoostingRegressor(loss="poisson", learning_rate=0.05, max_leaf_nodes=leaves, max_iter=iters, min_samples_leaf=200,
                                      l2_regularization=1.0, early_stopping=False, monotonic_cst=[MONO.get(f, 0) for f in FEATS],
                                      random_state=7).fit(Xtr, ytr)
    return lambda Xq: m.predict(Xq)


tr = (D >= "2025-11-01") & (D <= "2026-06-30")
va = (D >= "2026-07-01") & (D <= "2026-08-31")
configs = {"GLM": [("alpha", a) for a in (1e-4, 1e-3, 1e-2, 1e-1)], "GBM": [((8, 300)), ((16, 300)), ((8, 600)), ((16, 150))]}
chosen = {}
print("\n## validation (2026-07-01 .. 08-31), mean log score (higher = better); CUR for reference:",
      f"{np.mean(logscore_v(Y[va], np.array([d['cur'] for d, v in zip(data, va) if v]), P['c'])):.5f}")
for fam, cfgs in configs.items():
    best = None
    for cfg in cfgs:
        f = fit_glm(X[tr], Y[tr], cfg[1]) if fam == "GLM" else fit_gbm(X[tr], Y[tr], *cfg)
        lam_tr, lam_va = f(X[tr]), f(X[va])
        c = spread_c(Y[tr], lam_tr)
        ls = float(np.mean(logscore_v(Y[va], lam_va, c)))
        print(f"  {fam} {cfg}: c {c:.3f}, validation log score {ls:.5f}, MAE {np.mean(np.abs(lam_va - Y[va])):.3f}")
        if best is None or ls > best[0]:
            best = (ls, cfg)
    chosen[fam] = best[1]
print("chosen:", chosen)

# test A (fit <= 08-31, predict September) and test B (fit <= 09-30, predict Oct 1-16)
pred, cvals = {}, {}
for tag, fit_end, lo, hi in (("A", "2026-08-31", "2026-09-01", "2026-09-30"), ("B", "2026-09-30", "2026-10-01", "2026-10-16")):
    ft = (D >= "2025-11-01") & (D <= fit_end)
    te = (D >= lo) & (D <= hi)
    for fam, cfg in chosen.items():
        f = fit_glm(X[ft], Y[ft], cfg[1]) if fam == "GLM" else fit_gbm(X[ft], Y[ft], *cfg)
        c = spread_c(Y[ft], f(X[ft]))
        cvals[(fam, tag)] = c
        lam = f(X[te])
        for i, l in zip(np.where(te)[0], lam):
            pred.setdefault(i, {})[fam] = (float(l), c)
    if tag == "B":
        FIT_B = {fam: (fit_glm(X[ft], Y[ft], cfg[1]) if fam == "GLM" else fit_gbm(X[ft], Y[ft], *cfg)) for fam, cfg in chosen.items()}
        C_B = {fam: cvals[(fam, "B")] for fam in chosen}
print("spread c per fit:", {k: round(v, 3) for k, v in cvals.items()})
test = []
for i, pr in pred.items():
    d = dict(data[i])
    d.update({f"lam_{k}": v[0] for k, v in pr.items()})
    d.update({f"c_{k}": v[1] for k, v in pr.items()})
    test.append(d)
A_obs = pickle.load(open(os.path.join(RL, "actionable.pkl"), "rb"))
E1 = pickle.load(open(os.path.join(RL, "eval20_rows.pkl"), "rb"))["E1"]
book = {o["p_id"] for o in A_obs} | {o["p_id"] for o in E1}
print(f"\n## history test (2026-09-01 .. 10-16): {len(test)} player-series / {len({t['m'] for t in test})} matches; positive = better than CUR")
print("| segment | series | CUR MAE / log score | CUR-FULL MAE gain; log gain | GLM MAE gain (95%); log gain (95%) | GBM MAE gain (95%); log gain (95%) |")
print("|---|---|---|---|---|---|")
for t in test:
    t["ls_cur"] = float(logscore_v(np.array([t["y"]]), np.array([t["cur"]]), P["c"])[0])
    t["ls_full"] = float(logscore_v(np.array([t["y"]]), np.array([t["cur_full"]]), P["c"])[0])
    for fam in chosen:
        t[f"ls_{fam}"] = float(logscore_v(np.array([t["y"]]), np.array([t[f"lam_{fam}"]]), t[f"c_{fam}"])[0])
segs = [("all", lambda t: True), ("book-listed players", lambda t: t["p"] in book),
        ("book-listed established (>= 600)", lambda t: t["p"] in book and t["hist"] >= 600),
        ("book-listed thin (< 600)", lambda t: t["p"] in book and t["hist"] < 600),
        ("established", lambda t: t["hist"] >= 600), ("thin", lambda t: t["hist"] < 600),
        ("original top-200 set", lambda t: t["top"]), ("lower tier", lambda t: not t["top"]), ("LAN", lambda t: t["lan"]), ("online", lambda t: not t["lan"])]
HIST = {}
for nm, f in segs:
    g = [t for t in test if f(t)]
    cells = [nm, str(len(g)), f"{np.mean([abs(t['cur'] - t['y']) for t in g]):.3f} / {np.mean([t['ls_cur'] for t in g]):.4f}",
             f"{np.mean([abs(t['cur'] - t['y']) - abs(t['cur_full'] - t['y']) for t in g]):+.3f}; {np.mean([t['ls_full'] - t['ls_cur'] for t in g]):+.4f}"]
    for fam in chosen:
        ga = lambda t, fam=fam: abs(t["cur"] - t["y"]) - abs(t[f"lam_{fam}"] - t["y"])
        gl = lambda t, fam=fam: t[f"ls_{fam}"] - t["ls_cur"]
        a, (alo, ahi) = np.mean([ga(t) for t in g]), ci(g, ga, B=1000)
        l, (llo, lhi) = np.mean([gl(t) for t in g]), ci(g, gl, B=1000)
        cells.append(f"{a:+.3f} ({alo:+.3f}, {ahi:+.3f}); {l:+.4f} ({llo:+.4f}, {lhi:+.4f})")
        HIST[(nm, fam)] = {"mae": (a, alo, ahi), "log": (l, llo, lhi)}
    print("| " + " | ".join(cells) + " |")

# ------------------------------------------------------------------ real lines (decision-time forecasts at the original line)
recmap = {(r["m"], r["p"]): r for r in R}


def p_line(lam, c, line, prop):
    sd = c * math.sqrt(max(lam, 1.0))
    if line != int(line):
        po = 1 - Phi((math.floor(line) + 0.5 - lam) / sd)
        pu = 1 - po
    else:
        po, pu = 1 - Phi((line + 0.5 - lam) / sd), Phi((line - 0.5 - lam) / sd)
    p = po / (po + pu)
    return min(max(p, 0.22), 0.78) if prop == "kills" else p


rows = []
for o in A_obs:
    if o["scope"] != "12" or o["day"] < "2026-10-01":
        continue
    r = recmap.get((o["m"], o["p_id"]))
    if r is None:
        continue
    x = feats(r)
    if x is None:
        continue
    c0, cn = comp(r, "n0"), comp(r, "n")
    hs0, hsn = (1.0, 1.0) if o["prop"] == "kills" else (c0["hsr"], cn["hsr"])
    c_hs = E.SPREAD("hs", "12")
    row = {"m": o["m"], "day": o["day"], "prop": o["prop"], "scope": "12", "line": o["line"], "y": o["y"], "actual": o["actual"], "flat": 0.5,
           "p_live": o["p_live"]}
    row["lam_CUR"] = c0["lam"] * hs0
    row["p_CUR"] = p_line(row["lam_CUR"], P["c"] if o["prop"] == "kills" else c_hs, o["line"], o["prop"])
    for fam in chosen:
        lam = float(FIT_B[fam](np.array([x]))[0]) * hsn
        cc = C_B[fam] if o["prop"] == "kills" else c_hs * C_B[fam] / P["c"]
        row[f"lam_{fam}"] = lam
        row[f"p_{fam}"] = p_line(lam, cc, o["line"], o["prop"])
    rows.append(row)
print(f"\n## real lines: decision-time forecasts at the original, timestamped line, matches from 2026-10-01 (all fits use matches <= 09-30)")
E.add_base(rows)
models = ["flat", "p_BASE", "p_live", "p_CUR"] + [f"p_{fam}" for fam in chosen]
LINE = E.prob_table(rows, models, ["p_CUR", "p_BASE"], "kills + HS Maps 1-2 pooled")
E.selection(rows, models[2:], "kills + HS pooled")
for prop in ("kills", "hs"):
    g = [r for r in rows if r["prop"] == prop]
    E.prob_table(g, models, ["p_CUR", "p_BASE"], f"{prop} Maps 1-2")
    E.selection(g, models[2:], prop)
    bt = E.Boot(g)
    for k in ["lam_CUR"] + [f"lam_{fam}" for fam in chosen]:
        dm = bt.ratio(np.array([(r[k] - r["actual"]) ** 2 - (r["line"] - r["actual"]) ** 2 for r in g]))
        print(f"  {prop} {k}: MSE minus the line's MSE {dm[0]:+.2f} ({dm[1]:+.2f}, {dm[2]:+.2f}); MAE {np.mean([abs(r[k] - r['actual']) for r in g]):.3f}")

# ------------------------------------------------------------------ selection rule (fixed in the protocol)
print("\n## selection (protocol rule)")
for fam in chosen:
    h1 = HIST[("book-listed players", fam)]
    h2 = HIST[("book-listed established (>= 600)", fam)]
    ln = LINE[f"p_{fam}"]
    c1 = h1["mae"][1] > 0 and h1["log"][1] > 0
    c2 = h2["log"][1] > 0
    c3 = ln["vs_p_CUR"]["ll"][1] > 0 and ln["vs_p_BASE"]["ll"][0] >= 0
    print(f"  {fam}: (1) book-listed MAE + log intervals above 0: {c1} | (2) book-listed established log interval above 0: {c2} | "
          f"(3) real-line log-loss gain vs CUR interval above 0 and >= BASE: {c3} (vs CUR {ln['vs_p_CUR']['ll'][0]:+.4f} "
          f"({ln['vs_p_CUR']['ll'][1]:+.4f}, {ln['vs_p_CUR']['ll'][2]:+.4f}), vs BASE {ln['vs_p_BASE']['ll'][0]:+.4f}) -> {'SELECTED' if c1 and c2 and c3 else 'not selected'}")
pickle.dump({"test": test, "rows": rows, "chosen": chosen, "c": cvals}, open(os.path.join(RL, "models_out.pkl"), "wb"))
