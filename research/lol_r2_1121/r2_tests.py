"""LoL round 2 tests, exactly as PREREG.md (sha256 of the committed copy 28334a23...). Research only; reads the audit CSVs
and the lpf-1 replay (lpf_replay/); writes r2_results.json + prints a summary. No production access."""
import csv, json, math, os, pickle, random, sys, itertools
from collections import defaultdict, Counter
import numpy as np

WT = r"C:\Users\tikic\cs2_scout\.claude\worktrees\claude-lol-r2-1121"
sys.path.insert(0, WT)
from lol import lpf, model as LM

BASE = "C:/Users/tikic/cs2_scout_ops/logs/audit_1121"
OUT = BASE + "/round2/lol"
CACHE = r"C:\Users\tikic\AppData\Local\Temp\claude\aa2edd5017cc8a7b\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\lpf_r2cache"
HOLD_DAYS = {"2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08", "2026-11-14"}
BANDS = [(0.80, "A+"), (0.74, "A"), (0.68, "A-"), (0.63, "B+"), (0.57, "B"), (0.55, "B-")]

ROWS = []
for f in ("train", "holdout"):
    for r in csv.DictReader(open(f"{BASE}/data/lol/{f}.csv", encoding="utf-8")):
        if r["group"] == "p1" and r["grade"] != "":
            ROWS.append(r)
DAYS = sorted({r["day"] for r in ROWS})


def fl(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def grade_of(p):
    for t, g in BANDS:
        if p >= t:
            return g
    return "Lean"


def offered(r, side):
    if r["platform"] == "underdog":
        return r[f"ud_{side}_status"] == "active"
    return r[f"pp_{side}_offered"] == "True"


def outcome(r, side):
    if r["push"] == "1" or r["result"] == "P":
        return "P"
    if r["result"] not in ("W", "L"):
        return None
    oh = r["over_hit"] == "1"
    return "W" if (oh if side == "over" else not oh) else "L"


def decide(po, pu):
    side = "over" if po >= pu else "under"
    p = po if side == "over" else pu
    return side, grade_of(p), p


def records(rows, dec):
    out = []
    for r in rows:
        side, grade = dec[r["unit_key"]][:2]
        o = outcome(r, side)
        off = offered(r, side)
        out.append({"day": r["day"], "sid": r["sid"], "pm": r["pm_key"], "side": side, "res": o, "uk": r["unit_key"],
                    "letter": grade != "Lean", "playable": off, "pc": off and r["one_sided"] == "", "mv": r["model_version"],
                    "stat": r["stat"], "scope": r["scope"], "p": dec[r["unit_key"]][2]})
    return out


def uni(recs, u):
    return {"all_graded": recs, "letters": [x for x in recs if x["letter"]],
            "playable_current": [x for x in recs if x["pc"]],
            "playable_current_letters": [x for x in recs if x["pc"] and x["letter"]]}[u]


def wl(rs):
    return sum(x["res"] == "W" for x in rs), sum(x["res"] == "L" for x in rs), sum(x["res"] == "P" for x in rs)


def hit(rs):
    w, l, _ = wl(rs)
    return w / (w + l) if w + l else None


def summ(rs):
    w, l, p = wl(rs)
    days = defaultdict(list)
    for x in rs:
        days[x["day"]].append(x)
    dh = {d: hit(v) for d, v in sorted(days.items())}
    ov = [x for x in rs if x["res"] in ("W", "L") and x["side"] == "over"]
    un = [x for x in rs if x["res"] in ("W", "L") and x["side"] == "under"]
    return {"W": w, "L": l, "P": p, "hit": None if not w + l else round(100 * w / (w + l), 2), "rows": len(rs),
            "pm": len({x["pm"] for x in rs}), "series": len({x["sid"] for x in rs}), "days": len(dh),
            "bad_days": sum(1 for v in dh.values() if v is not None and v < 0.53),
            "over_share": round(100 * len(ov) / max(1, len(ov) + len(un)), 1),
            "over_hit": None if not ov else round(100 * hit(ov), 1), "under_hit": None if not un else round(100 * hit(un), 1),
            "daily": {d: (None if v is None else round(100 * v, 1)) for d, v in dh.items()}}


def boot(base, cand, key, B=2000, seed=7):
    """Bootstrap CI of the pooled hit delta, resampling clusters (day or sid) with base/cand rows of the same cluster."""
    cl = sorted({x[key] for x in base} | {x[key] for x in cand})
    agg = {}
    for lab, rs in (("b", base), ("c", cand)):
        for x in rs:
            if x["res"] in ("W", "L"):
                a = agg.setdefault(x[key], [0, 0, 0, 0])
                i = 0 if lab == "b" else 2
                a[i] += x["res"] == "W"
                a[i + 1] += 1
    arr = np.array([agg.get(c, [0, 0, 0, 0]) for c in cl], float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(cl), (B, len(cl)))
    s = arr[idx].sum(1)
    d = 100 * (s[:, 2] / np.maximum(s[:, 3], 1) - s[:, 0] / np.maximum(s[:, 1], 1))
    return [round(float(np.percentile(d, 2.5)), 2), round(float(np.percentile(d, 97.5)), 2)]


def signflip(base, cand):
    db, dc = defaultdict(list), defaultdict(list)
    for x in base:
        db[x["day"]].append(x)
    for x in cand:
        dc[x["day"]].append(x)
    diffs = []
    for d in sorted(set(db) & set(dc)):
        hb, hc = hit(db[d]), hit(dc[d])
        if hb is not None and hc is not None:
            diffs.append(hc - hb)
    if not diffs:
        return None, 1.0, 0
    obs = abs(sum(diffs) / len(diffs))
    n = len(diffs)
    if n <= 20:
        ge = tot = 0
        for signs in itertools.product((1, -1), repeat=n):
            tot += 1
            ge += abs(sum(s * v for s, v in zip(signs, diffs)) / n) >= obs - 1e-12
        p = ge / tot
    else:
        rng = random.Random(7)
        ge = sum(abs(sum(rng.choice((1, -1)) * v for v in diffs) / n) >= obs - 1e-12 for _ in range(20000))
        p = (ge + 1) / 20001
    return round(100 * sum(diffs) / n, 2), p, n


def compare(rows, base_dec, cand_dec, primary="playable_current", subset_days=None):
    rs = [r for r in rows if subset_days is None or r["day"] in subset_days]
    rb, rc = records(rs, base_dec), records(rs, cand_dec)
    out = {"changes": dict(Counter(("flip" if base_dec[r["unit_key"]][0] != cand_dec[r["unit_key"]][0] else "same_side") +
                                    ("|grade_changed" if base_dec[r["unit_key"]][1] != cand_dec[r["unit_key"]][1] else "")
                                    for r in rs))}
    for u in ("playable_current", "all_graded", "letters", "playable_current_letters"):
        b, c = uni(rb, u), uni(rc, u)
        md, p, n = signflip(b, c)
        sb, sc = summ(b), summ(c)
        out[u] = {"base": sb, "cand": sc, "pooled_delta": None if sb["hit"] is None or sc["hit"] is None else round(sc["hit"] - sb["hit"], 2),
                  "mean_day_delta": md, "p_signflip": p, "n_days": n, "ci_day": boot(b, c, "day"), "ci_series": boot(b, c, "sid"),
                  "volume_ratio": round((sc["W"] + sc["L"]) / max(1, sb["W"] + sb["L"]), 3)}
    pr = out[primary]
    by_mv = {}
    for mv in sorted({r["model_version"] for r in rs}):
        b = uni([x for x in rb if x["mv"] == mv], primary)
        c = uni([x for x in rc if x["mv"] == mv], primary)
        by_mv[mv] = {"base": summ(b)["hit"], "cand": summ(c)["hit"], "n_base": len(b), "n_cand": len(c)}
    out["by_model_version"] = by_mv
    for lab, dd in (("orig_train_days", set(DAYS) - HOLD_DAYS), ("audit_holdout_days", HOLD_DAYS)):
        b = uni([x for x in rb if x["day"] in dd], primary)
        c = uni([x for x in rc if x["day"] in dd], primary)
        out[lab] = {"base": summ(b)["hit"], "cand": summ(c)["hit"], "n": len(c), "base_W_L": wl(b)[:2], "cand_W_L": wl(c)[:2]}
    return out


def gates(res, p_holm, primary="playable_current"):
    pr = res[primary]
    eff_a = p_holm < 0.05 and (pr["pooled_delta"] or 0) > 0
    eff_b = pr["base"]["bad_days"] - pr["cand"]["bad_days"] >= 2 and (pr["pooled_delta"] or 0) >= 0
    vol = pr["volume_ratio"] >= 0.6
    skew = pr["cand"]["over_share"] >= pr["base"]["over_share"] - 5
    return {"holm_p": round(p_holm, 4), "effect_a_holm": eff_a, "effect_b_bad_days": eff_b, "volume_ok": vol,
            "no_under_skew": skew, "PASS": (eff_a or eff_b) and vol and skew}


# ----------------------------------------------------------------------------------------------- NB helpers
def nb_p(lam, size, line):
    """P(X > line), P(X < line) for NB(mean lam, size) (whole lines: push excluded from both)."""
    mx = lpf.Mix(np.array([[1.0]]), np.array([[lam]]), np.array([[size]]))
    po, push = mx.p_over(np.array([line]))
    po, push = float(po[0]), float(push[0])
    return po, 1 - po - push


def series_to_game(m, bo):
    m = min(max(m, 0.02), 0.98)
    if bo in (1, None):
        return m
    f = (lambda p: p * p * (3 - 2 * p)) if bo == 3 else (lambda p: p ** 3 * (10 - 15 * p + 6 * p * p))
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < m else (lo, mid)
    return min(max((lo + hi) / 2, 0.02), 0.98)


RESULTS = {"prereg_sha256_committed_copy": "28334a23f8a66d5d09acc6bd2652040181d77195f3aea758044f81f58ff9c995"}

# =============================================================================================== Base L (saved)
SAVED = {r["unit_key"]: decide(fl(r["p_over"]), fl(r["p_under"])) for r in ROWS}
# sanity: saved side/grade reproduced
rep = Counter((SAVED[r["unit_key"]][0] == r["side"], SAVED[r["unit_key"]][1] == r["grade"]) for r in ROWS)
RESULTS["saved_reproduction"] = {str(k): v for k, v in rep.items()}
# NB reproduction of the saved chance (decides whether candidates may use NB(lambda, size) directly)
diffs = []
for r in ROWS:
    lam, sz, line = fl(r["lambda"]), fl(r["nb_size"]), fl(r["line"])
    if None in (lam, sz, line):
        continue
    po, pu = nb_p(lam, sz, line)
    diffs.append(abs(po - fl(r["p_over"])))
diffs = np.array(diffs)
RESULTS["nb_reproduction"] = {"n": len(diffs), "within_0.005": round(float((diffs < 0.005).mean()), 4),
                              "p90_absdiff": round(float(np.percentile(diffs, 90)), 4)}


def nb_shift_dec(r, lam_new):
    """Candidate chance for a saved-lambda row: saved chance + (NB(lam_new) - NB(lam)) (keeps any mixture the saved
    chance carried)."""
    lam, sz, line = fl(r["lambda"]), fl(r["nb_size"]), fl(r["line"])
    po0, pu0 = nb_p(lam, sz, line)
    po1, pu1 = nb_p(lam_new, sz, line)
    po = min(max(fl(r["p_over"]) + (po1 - po0), 0.0), 1.0)
    pu = min(max(fl(r["p_under"]) + (pu1 - pu0), 0.0), 1.0)
    return decide(po, pu)


# T2b mkt-sf
R = LM.PRIORS["win_ratio"]
EW = {s: LM.PARAMS[s]["ew"] for s in ("kills", "assists")}
T2B = dict(SAVED)
n2b = 0
for r in ROWS:
    m, pw, sf, lam = fl(r["team_win_market_p"]), fl(r["p_win_elo"]), fl(r["strength_factor"]), fl(r["lambda"])
    if r["model_version"] != "lol-1f" or None in (m, pw, sf, lam) or sf == 1.0 or r["stat"] not in EW:
        continue
    ew, Rr = EW[r["stat"]], R[r["stat"]]
    D = (pw * Rr + 1 - pw) / sf ** (1 / ew)
    pg = series_to_game(m, int(r["best_of"] or 1))
    sf_new = ((pg * Rr + 1 - pg) / D) ** ew
    T2B[r["unit_key"]] = nb_shift_dec(r, lam * sf_new / sf)
    n2b += 1
RESULTS["T2b"] = {"rows_changed_input": n2b, "res": compare(ROWS, SAVED, T2B)}


# T4b scope offset on saved lambda, walk-forward by day
def scope_offsets(rows, mean_of, day):
    cells = defaultdict(lambda: [0.0, 0.0, 0])
    seen = set()
    for r in sorted(rows, key=lambda r: r["unit_key"]):
        if r["day"] >= day or r["pm_key"] in seen or fl(r["actual"]) is None or r["settle_status"] != "graded":
            continue
        mu = mean_of(r)
        if mu is None:
            continue
        seen.add(r["pm_key"])
        c = cells[(r["stat"], r["scope"])]
        c[0] += fl(r["actual"]); c[1] += mu; c[2] += 1
    return {k: (math.log(v[0] / v[1]) if v[2] >= 30 and v[0] > 0 and v[1] > 0 else 0.0, v[2]) for k, v in cells.items()}


T4B = dict(SAVED)
off_log_b = {}
for d in DAYS:
    offs = scope_offsets(ROWS, lambda r: fl(r["lambda"]), d)
    off_log_b[d] = {f"{k[0]}|{k[1]}": [round(v[0], 4), v[1]] for k, v in offs.items()}
    for r in ROWS:
        if r["day"] != d:
            continue
        c = offs.get((r["stat"], r["scope"]), (0.0, 0))[0]
        if c and fl(r["lambda"]) is not None:
            T4B[r["unit_key"]] = nb_shift_dec(r, fl(r["lambda"]) * math.exp(c))
RESULTS["T4b"] = {"offsets_by_day": off_log_b, "res": compare(ROWS, SAVED, T4B)}


# T1b best line per player-market (selection) on a decision set
def best_line(rows, dec):
    rb = records(rows, dec)
    byuk = {x["uk"]: x for x in rb}
    groups = defaultdict(list)
    for r in rows:
        x = byuk[r["unit_key"]]
        if x["pc"]:
            groups[r["pm_key"]].append((-x["p"], 0 if r["odds_type"] == "standard" else 1, r["unit_key"]))
    best = {sorted(v)[0][2] for v in groups.values()}
    allpc = [x for x in rb if x["pc"]]
    bst = [x for x in allpc if x["uk"] in best]
    md, p, n = signflip(allpc, bst)
    return {"all_playable_current": summ(allpc), "best_line": summ(bst), "pooled_delta": round(summ(bst)["hit"] - summ(allpc)["hit"], 2),
            "mean_day_delta": md, "p_signflip": p, "n_days": n, "ci_day": boot(allpc, bst, "day"), "ci_series": boot(allpc, bst, "sid"),
            "volume_ratio": round(len(bst) / max(1, len(allpc)), 3)}


RESULTS["T1b_L"] = best_line(ROWS, SAVED)

# =============================================================================================== Base P (lpf-1 replay)
REP = json.load(open(OUT + "/lpf_replay/lpf_replay_rows.json"))
RAW = pickle.load(open(os.path.join(CACHE, "lpf_raw.pkl"), "rb"))
RESULTS["replay_meta"] = {"n_rows": len(REP["rows"]), "n_settled_in_copy": REP["n_settled"], "secs": REP["secs"]}
PROWS = [r for r in ROWS if r["unit_key"] in REP["rows"]]
RESULTS["replay_coverage"] = {"rows": len(PROWS), "days": sorted({r["day"] for r in PROWS}),
                              "of_rows_on_those_days": sum(1 for r in ROWS if r["day"] in {x["day"] for x in PROWS})}
LPF = {r["unit_key"]: decide(REP["rows"][r["unit_key"]]["po"], REP["rows"][r["unit_key"]]["pu"]) for r in PROWS}
# context: lpf-1 replay vs saved (no gate)
RESULTS["context_lpf1_replay_vs_saved"] = compare(PROWS, SAVED, LPF)

# raw pieces: target index -> (refit, position)
TIDX = {(t["match"], t["pid"]): i for i, t in enumerate(RAW["T"])}
POS = {}
for rf, blk in RAW["raw"].items():
    for j, i in enumerate(blk["idx"]):
        POS[int(i)] = (rf, j)


def p3map(p, coef):
    p = np.asarray(p, float)
    if coef is None:
        return 2 * p * (1 - p)
    x = np.clip(2 * p * (1 - p), 1e-6, 1 - 1e-6)
    z = coef[0] * np.log(x / (1 - x)) + coef[1]
    return 1 / (1 + np.exp(-z))


def comps_one(mus, stat, scope, j, p, p3, bo):
    g = mus[stat]
    W = {n: float(g[(n, 1.0)][j]) for n in (1, 2, 3)}
    L = {n: float(g[(n, 0.0)][j]) for n in (1, 2, 3)}
    q = 1 - p
    if scope == "1":
        return [(p, W[1], "1"), (q, L[1], "1")]
    if scope == "12":
        return [(p * p, W[1] + W[2], "12"), (2 * p * q, (W[1] + L[2] + L[1] + W[2]) / 2, "12"), (q * q, L[1] + L[2], "12")]
    s3, l3 = W[1] + W[2] + W[3], L[1] + L[2] + L[3]
    if bo == 3:
        sw = p * p + q * q
        return [((1 - p3) * p * p / sw, W[1] + W[2], "12"), ((1 - p3) * q * q / sw, L[1] + L[2], "12"),
                (p3 * p, (W[1] + L[2] + W[3] + L[1] + W[2] + W[3]) / 2, "123"),
                (p3 * q, (W[1] + L[2] + L[3] + L[1] + W[2] + L[3]) / 2, "123")]
    return [(p ** 3, s3, "123"), (3 * p * p * q, s3 * 2 / 3 + l3 / 3, "123"), (3 * p * q * q, s3 / 3 + l3 * 2 / 3, "123"), (q ** 3, l3, "123")]


def lpf_chance(r, p_override=None, scale=1.0):
    rr = REP["rows"][r["unit_key"]]
    i = TIDX.get((rr["sid"], rr["pid"]))
    if i is None or i not in POS:
        return None
    rf, j = POS[i]
    blk = RAW["raw"][rf]
    p = float(blk["p"][j]) if p_override is None else p_override
    bo = int(blk["bo"][j])
    p3 = float(blk["p3"][j]) if p_override is None else float(p3map(p, blk["wm_p3coef"]))
    cs = comps_one(blk["mus"], rr["stat"], str(rr["scope"]), j, p, p3, bo)
    role = blk["role"][j]
    sz = [blk["sizes"].get((rr["stat"], c[2], role), blk["sizes"].get((rr["stat"], c[2], None), 5.0)) for c in cs]
    mx = lpf.Mix(np.array([[c[0] for c in cs]]), np.array([[c[1] * scale for c in cs]]), np.array([sz]))
    po, push = mx.p_over(np.array([fl(r["line"])]))
    po, push = float(po[0]), float(push[0])
    return po, 1 - po - push, float(sum(c[0] * c[1] for c in cs) * scale)


# reconstruction check: the rebuilt mixture equals the replay's saved chance
rc = [abs(lpf_chance(r)[0] - REP["rows"][r["unit_key"]]["po"]) for r in PROWS if lpf_chance(r) is not None]
RESULTS["lpf_rebuild_check"] = {"n": len(rc), "max_absdiff": round(float(max(rc)), 6)}

# strength diagnostic incl. lpf-1 replay p_win (series-team; game-1 win chance vs series result, favourite accuracy)
st = {}
for r in ROWS:
    k = (r["sid"], r["tid"])
    e = st.setdefault(k, {"won": fl(r["post_team_won_series"]), "elo": fl(r["p_win_elo"]), "mkt": fl(r["team_win_market_p"]), "lpf": None, "day": r["day"]})
    if r["unit_key"] in REP["rows"] and REP["rows"][r["unit_key"]].get("p_win") is not None:
        e["lpf"] = REP["rows"][r["unit_key"]]["p_win"]
diag = {}
for c in ("elo", "mkt", "lpf"):
    s = [v for v in st.values() if v[c] is not None and v["won"] is not None and v[c] != 0.5]
    diag[c] = {"n": len(s), "fav_acc": round(sum((v[c] > 0.5) == (v["won"] == 1) for v in s) / max(1, len(s)), 3)}
both = [v for v in st.values() if None not in (v["elo"], v["mkt"], v["lpf"], v["won"])]
diag["all_three"] = {"n": len(both), **{c: round(sum((v[c] > 0.5) == (v["won"] == 1) for v in both) / max(1, len(both)), 3) for c in ("elo", "mkt", "lpf")}}
RESULTS["strength_diagnostic"] = diag

# T2a mkt-p on lpf-1
T2A = dict(LPF)
n2a = 0
for r in PROWS:
    m = fl(r["team_win_market_p"])
    if m is None:
        continue
    pg = series_to_game(m, int(r["best_of"] or 1))
    z = lpf_chance(r, p_override=pg)
    if z:
        T2A[r["unit_key"]] = decide(z[0], z[1])
        n2a += 1
RESULTS["T2a"] = {"rows_changed_input": n2a, "res": compare(PROWS, LPF, T2A)}

# T4a scope offset on lpf-1 replay, walk-forward
LPF_MEAN = {r["unit_key"]: REP["rows"][r["unit_key"]]["mean"] for r in PROWS}
T4A = dict(LPF)
off_log_a = {}
for d in sorted({r["day"] for r in PROWS}):
    offs = scope_offsets(PROWS, lambda r: LPF_MEAN.get(r["unit_key"]), d)
    off_log_a[d] = {f"{k[0]}|{k[1]}": [round(v[0], 4), v[1]] for k, v in offs.items()}
    for r in PROWS:
        if r["day"] != d:
            continue
        c = offs.get((r["stat"], r["scope"]), (0.0, 0))[0]
        if c:
            z = lpf_chance(r, scale=math.exp(c))
            if z:
                T4A[r["unit_key"]] = decide(z[0], z[1])
RESULTS["T4a"] = {"offsets_by_day": off_log_a, "res": compare(PROWS, LPF, T4A)}
RESULTS["T1b_P"] = best_line(PROWS, LPF)

# Holm over the 5 registered tests
fam = {"T2a": RESULTS["T2a"]["res"]["playable_current"]["p_signflip"], "T2b": RESULTS["T2b"]["res"]["playable_current"]["p_signflip"],
       "T4a": RESULTS["T4a"]["res"]["playable_current"]["p_signflip"], "T4b": RESULTS["T4b"]["res"]["playable_current"]["p_signflip"],
       "T1b": RESULTS["T1b_L"]["p_signflip"]}
order = sorted(fam, key=lambda k: fam[k])
holm, run = {}, 0.0
for i, k in enumerate(order):
    run = max(run, min(1.0, (len(fam) - i) * fam[k]))
    holm[k] = run
RESULTS["holm"] = {"raw": fam, "adjusted": holm}
for k in ("T2a", "T2b", "T4a", "T4b"):
    RESULTS[k]["gates"] = gates(RESULTS[k]["res"], holm[k])
b = RESULTS["T1b_L"]
RESULTS["T1b_L"]["gates"] = {"holm_p": holm["T1b"], "effect_a_holm": holm["T1b"] < 0.05 and b["pooled_delta"] > 0,
                             "effect_b_bad_days": b["all_playable_current"]["bad_days"] - b["best_line"]["bad_days"] >= 2 and b["pooled_delta"] >= 0,
                             "volume_ok": b["volume_ratio"] >= 0.6,
                             "no_under_skew": b["best_line"]["over_share"] >= b["all_playable_current"]["over_share"] - 5}
g = RESULTS["T1b_L"]["gates"]
g["PASS"] = (g["effect_a_holm"] or g["effect_b_bad_days"]) and g["volume_ok"] and g["no_under_skew"]

# ---------------------------------------------------------------------------- track 3 diagnostic (assists residuals)
t3 = {}
for lab, rows, mean_of in (("saved_lambda", ROWS, lambda r: fl(r["lambda"])), ("lpf1_replay", PROWS, lambda r: LPF_MEAN.get(r["unit_key"]))):
    cells = defaultdict(list)
    seen = set()
    for r in rows:
        if r["stat"] != "assists" or r["pm_key"] in seen or fl(r["actual"]) is None or mean_of(r) is None:
            continue
        seen.add(r["pm_key"])
        cells["team_won" if r["post_team_won_series"] == "1" else "team_lost"].append(fl(r["actual"]) - mean_of(r))
    t3[lab] = {k: {"pm": len(v), "mean_resid": round(float(np.mean(v)), 3)} for k, v in cells.items()}
RESULTS["track3_assists_resid_by_result_DIAGNOSTIC_post_columns"] = t3
RESULTS["track3_playable_current_assists_rows"] = sum(1 for r in ROWS if r["stat"] == "assists" and r["playable_current_rules"] == "1")

# ---------------------------------------------------------------------------- track 1 accounting (a15 = live join)
A15 = [r for f in ("train", "holdout") for r in csv.DictReader(open(f"{BASE}/data/lol/{f}.csv", encoding="utf-8"))
       if r["group"] == "a15" and r["settle_status"] == "graded" and r["side_offered"] == "1"]


def band_rows(rs):
    w = sum(r["win"] == "1" for r in rs)
    return {"rows": len(rs), "W": w, "L": len(rs) - w, "hit": round(100 * w / max(1, len(rs)), 2)}


RESULTS["track1_slip_band_population_a15"] = {
    "before_rp1 (side_offered=1)": band_rows(A15),
    "after_rp1 (also not one-sided)": band_rows([r for r in A15 if r["one_sided"] == ""]),
    "removed_one_sided": band_rows([r for r in A15 if r["one_sided"] != ""]),
    "removed_one_sided_by_stat": {s: band_rows([r for r in A15 if r["one_sided"] != "" and r["stat"] == s]) for s in ("kills", "assists")}}

json.dump(RESULTS, open(OUT + "/r2_results.json", "w"), indent=1, default=str)


def line(k, res, primary="playable_current"):
    pr = res[primary]
    return (f"{k}: base {pr['base']['W']}-{pr['base']['L']}-{pr['base']['P']} {pr['base']['hit']} -> cand {pr['cand']['W']}-{pr['cand']['L']}-{pr['cand']['P']} "
            f"{pr['cand']['hit']} d {pr['pooled_delta']} dayCI {pr['ci_day']} serCI {pr['ci_series']} p {pr['p_signflip']:.4f} days {pr['n_days']} "
            f"bad {pr['base']['bad_days']}->{pr['cand']['bad_days']} over {pr['base']['over_share']}->{pr['cand']['over_share']} vol {pr['volume_ratio']}")


print("saved repro", RESULTS["saved_reproduction"], "nb repro", RESULTS["nb_reproduction"], "lpf rebuild", RESULTS["lpf_rebuild_check"])
print("coverage", RESULTS["replay_coverage"]["rows"], RESULTS["replay_coverage"]["days"])
print("strength", RESULTS["strength_diagnostic"])
print(line("CTX lpf1-replay vs saved", RESULTS["context_lpf1_replay_vs_saved"]))
for k in ("T2a", "T2b", "T4a", "T4b"):
    print(line(k, RESULTS[k]["res"]), "changes", RESULTS[k]["res"]["changes"])
    print("   gates", RESULTS[k]["gates"], "| all_graded d", RESULTS[k]["res"]["all_graded"]["pooled_delta"], RESULTS[k]["res"]["all_graded"]["ci_series"],
          "| by_mv", RESULTS[k]["res"]["by_model_version"], "| split", RESULTS[k]["res"]["orig_train_days"], RESULTS[k]["res"]["audit_holdout_days"])
for k in ("T1b_L", "T1b_P"):
    b = RESULTS[k]
    print(k, "all", b["all_playable_current"]["W"], b["all_playable_current"]["L"], b["all_playable_current"]["hit"], "best", b["best_line"]["W"], b["best_line"]["L"],
          b["best_line"]["hit"], "d", b["pooled_delta"], b["ci_day"], b["ci_series"], "p", b["p_signflip"], "bad", b["all_playable_current"]["bad_days"], "->",
          b["best_line"]["bad_days"], "over", b["all_playable_current"]["over_share"], "->", b["best_line"]["over_share"], "vol", b["volume_ratio"], b.get("gates"))
print("holm", RESULTS["holm"])
print("T3", RESULTS["track3_assists_resid_by_result_DIAGNOSTIC_post_columns"], RESULTS["track3_playable_current_assists_rows"])
print("T1", RESULTS["track1_slip_band_population_a15"])
