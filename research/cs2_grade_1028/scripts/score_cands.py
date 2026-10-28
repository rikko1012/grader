"""Scores the candidates registered in research/cs2_grade_1028/REGISTER.md (commit 38fb79d) on the P1 units joined to
their forecasts: oc-1 (offered-line calibration per cell, walk-forward by day), sd-bo3-1 (spread multiplier on the
BO3.gg route, walk-forward by day), and a forward reading of the parked LW-1 (final frozen weights, units after its data
cut). Live is scored on identical units. All intervals are match-grouped bootstraps."""
import json, math, os, sys, collections
D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)
from ana import boot
import p1join

CAP = 0.78


def phi(z): return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def probit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    lo, hi = -8.0, 8.0
    for _ in range(80):
        m = (lo + hi) / 2
        lo, hi = (m, hi) if phi(m) < p else (lo, m)
    return (lo + hi) / 2


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -math.log(p if y else 1 - p)


def cap_k(p, prop):
    return min(max(p, 1 - CAP), CAP) if prop == "kills" else p


def fit_probit(zs, ys, iters=200):
    """ML fit of P = Phi(a + w z) by Newton steps on (a, w); starts at identity."""
    a, w = 0.0, 1.0
    for _ in range(iters):
        g = [0.0, 0.0]; H = [[0.0, 0.0], [0.0, 0.0]]
        for z, y in zip(zs, ys):
            t = a + w * z
            p = min(max(phi(t), 1e-9), 1 - 1e-9)
            d = math.exp(-t * t / 2) / math.sqrt(2 * math.pi)
            s = (y - p) / (p * (1 - p)) * d                  # d loglik / dt
            c = d * d / (p * (1 - p))                         # expected information (Fisher) per unit
            g[0] += s; g[1] += s * z
            H[0][0] += c; H[0][1] += c * z; H[1][1] += c * z * z
        H[1][0] = H[0][1]
        det = H[0][0] * H[1][1] - H[0][1] * H[1][0]
        if abs(det) < 1e-12: break
        da = (H[1][1] * g[0] - H[0][1] * g[1]) / det
        dw = (-H[1][0] * g[0] + H[0][0] * g[1]) / det
        a += da; w += dw
        if abs(da) + abs(dw) < 1e-8: break
    return a, w


def cell_of(u):
    return (u["prop"], "12" if u["scope"] == "12" else "other")


def summary(rows, name):
    """rows: dicts with match, pl (live p_over), pc (cand p_over), y (over), prop, day, units_live."""
    if not rows: return {"cand": name, "units": 0}
    for r in rows:
        r["dll"] = ll(r["pl"], r["y"]) - ll(r["pc"], r["y"])
        r["dbr"] = (r["pl"] - r["y"]) ** 2 - (r["pc"] - r["y"]) ** 2
    n = len(rows)
    gain = sum(r["dll"] for r in rows) / n
    gci = boot(rows, lambda r: r["dll"], lambda r: 1, lambda r: r["match"], B=2000)
    bg = sum(r["dbr"] for r in rows) / n
    bci = boot(rows, lambda r: r["dbr"], lambda r: 1, lambda r: r["match"], B=2000)

    def staked(key):
        s = [r for r in rows if max(r[key], 1 - r[key]) >= 0.55]
        if not s: return {"n": 0}
        hits = [(r["y"] if r[key] > 0.5 else 1 - r["y"]) for r in s]
        over_share = sum(1 for r in s if r[key] > 0.5) / len(s)
        over_rate = sum(r["y"] for r in s) / len(s)
        hci = boot([dict(r, h=h) for r, h in zip(s, hits)], lambda r: r["h"], lambda r: 1, lambda r: r["match"], B=1000)
        return {"n": len(s), "matches": len({r["match"] for r in s}), "hit": round(100 * sum(hits) / len(s), 1),
                "hit_ci": [round(100 * x, 1) for x in hci], "stated": round(100 * sum(max(r[key], 1 - r[key]) for r in s) / len(s), 1),
                "over_share": round(100 * over_share, 1), "over_rate": round(100 * over_rate, 1),
                "balance_gap": round(abs(over_share - over_rate), 3)}
    sl, sc = staked("pl"), staked("pc")
    # worst upset day for the candidate's staked set: the day with the lowest Over hit rate among its Over-favouring staked picks
    days = collections.defaultdict(list)
    for r in rows:
        if max(r["pc"], 1 - r["pc"]) >= 0.55: days[r["day"]].append(r)
    worst = None
    for d, rs in days.items():
        if len(rs) >= 20:
            osh = sum(1 for r in rs if r["pc"] > 0.5) / len(rs); orr = sum(r["y"] for r in rs) / len(rs)
            if worst is None or abs(osh - orr) > worst[1]: worst = (d, round(abs(osh - orr), 3), len(rs))
    slices = []
    for k, rs in collections.defaultdict(list, {}).items(): pass
    g = collections.defaultdict(list)
    for r in rows: g[(r["prop"], r["scope"])].append(r)
    for k, rs in g.items():
        slices.append({"slice": f"{k[0]} {k[1]}", "units": len(rs), "matches": len({r['match'] for r in rs}), "gain": round(sum(r["dll"] for r in rs) / len(rs), 5)})
    return {"cand": name, "units": n, "matches": len({r["match"] for r in rows}), "days": len({r["day"] for r in rows}),
            "logloss_live": round(sum(ll(r["pl"], r["y"]) for r in rows) / n, 4), "logloss_cand": round(sum(ll(r["pc"], r["y"]) for r in rows) / n, 4),
            "gain": round(gain, 5), "gain_ci": [round(x, 5) for x in gci], "brier_gain": round(bg, 5), "brier_gain_ci": [round(x, 5) for x in bci],
            "staked_live": sl, "staked_cand": sc, "retention": round(sc.get("n", 0) / sl["n"], 3) if sl.get("n") else None,
            "worst_balance_day": worst, "slices": slices}


def oc1(us):
    days = sorted({u["day"] for u in us})
    rows, fits = [], {}
    for d in days:
        train = [u for u in us if u["day"] < d and u["raw"] is not None]
        test = [u for u in us if u["day"] == d and u["raw"] is not None]
        params = {}
        for c in {cell_of(u) for u in test}:
            tr = [u for u in train if cell_of(u) == c]
            if len(tr) >= 300:
                params[c] = fit_probit([probit(u["raw"]) for u in tr], [u["over"] for u in tr])
            else:
                params[c] = (0.0, 1.0)
        fits[d] = {f"{c[0]} {c[1]}": [round(a, 4), round(w, 4)] for c, (a, w) in params.items()}
        for u in test:
            a, w = params[cell_of(u)]
            pc = cap_k(phi(a + w * probit(u["raw"])), u["prop"])
            rows.append({"match": u["match"], "pl": u["p_over"], "pc": pc, "y": u["over"], "prop": u["prop"], "scope": u["scope"], "day": d})
    out = summary(rows, "oc-1")
    out["fits_last_day"] = fits[days[-1]] if days else None
    out["fits_by_day"] = {d: fits[d] for d in days[-4:]}
    # live replay check: the capped identity transform of raw must reproduce the logged chance
    gaps = [abs(cap_k(u["raw"], u["prop"]) - u["p_over"]) for u in us if u["raw"] is not None]
    out["replay_gap_mean"] = round(sum(gaps) / len(gaps), 4) if gaps else None
    return out


def sd_bo3(us, picks_sd):
    coh = [u for u in us if u["source"].startswith("perf-2-bo3") and u["raw"] is not None and u["proj"] is not None]
    # sd from the first-grade factors; cross-check by inverting the normal from (lambda, line, raw chance)
    rows, grid, chosen = [], (1.0, 1.25, 1.5, 1.75, 2.0, 2.5), {}
    def chance(u, m):
        sd = picks_sd.get(u["pick_id"])
        if not sd: return None
        line, lam = u["line"], u["proj"]
        if line != int(line):
            c = math.floor(line) + 0.5; up, dn = c, c
        else:
            up, dn = line + 0.5, line - 0.5
        po = 1 - phi((up - lam) / (sd * m)); pu = phi((dn - lam) / (sd * m))
        p = po / (po + pu) if po + pu > 0 else 0.5
        return cap_k(p, u["prop"])
    days = sorted({u["day"] for u in coh})
    inv_gap = []
    for u in coh:
        p1 = chance(u, 1.0)
        if p1 is not None: inv_gap.append(abs(p1 - u["p_over"]))
    for d in days:
        train = [u for u in coh if u["day"] < d]
        test = [u for u in coh if u["day"] == d]
        m = 1.0
        if len(train) >= 80:
            best = None
            for g in grid:
                s = sum(ll(chance(u, g), u["over"]) for u in train if chance(u, g) is not None)
                if best is None or s < best[0]: best = (s, g)
            m = best[1]
        chosen[d] = m
        for u in test:
            pc = chance(u, m)
            if pc is None: continue
            rows.append({"match": u["match"], "pl": u["p_over"], "pc": pc, "y": u["over"], "prop": u["prop"], "scope": u["scope"], "day": d})
    out = summary(rows, "sd-bo3-1")
    out["m_by_day"] = chosen
    out["cohort_units"] = len(coh); out["sd_inversion_gap_mean"] = round(sum(inv_gap) / len(inv_gap), 4) if inv_gap else None
    out["decidable"] = out.get("matches", 0) >= 30
    return out


LW1 = {("kills", "12"): (0.0338, 0.0430), ("hs", "12"): (-0.0038, 0.3963), ("kills", "other"): (0.0169, -0.0608), ("hs", "other"): (-0.0094, 0.2718)}


def lw1_forward(us):
    cut = "2026-10-25T19:38:29"
    rows = []
    for u in us:
        if u["start"] <= cut or u["raw"] is None: continue
        a, w = LW1[cell_of(u)]
        pc = cap_k(phi(a + w * probit(u["raw"])), u["prop"])
        rows.append({"match": u["match"], "pl": u["p_over"], "pc": pc, "y": u["over"], "prop": u["prop"], "scope": u["scope"], "day": u["day"]})
    return summary(rows, "LW-1 forward (final frozen weights, starts after 2026-10-25 19:38 UTC)")


if __name__ == "__main__":
    us, meta = p1join.units()
    print("units", len(us), meta)
    picks = json.load(open(os.path.join(D, "picks.json")))["rows"]
    picks_sd = {}
    for r in picks:
        try: picks_sd[r["id"]] = (json.loads(r.get("factors") or "{}")).get("sd")
        except Exception: pass
    res = {"oc1": oc1(us), "sd_bo3_1": sd_bo3(us, picks_sd), "lw1_forward": lw1_forward(us)}
    json.dump(res, open(os.path.join(D, "cand_results.json"), "w"), indent=1, default=str)
    for k, v in res.items():
        print("\n==", k); print(json.dumps({kk: vv for kk, vv in v.items() if kk not in ("fits_by_day",)}, default=str, indent=0)[:3000])
