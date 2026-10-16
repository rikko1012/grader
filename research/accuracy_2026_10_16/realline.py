"""Real-line accuracy evaluation (PROTOCOL.md, fixed before this ran). Development evidence only."""
import collections, io, json, math, os, random, sys
from datetime import datetime, timedelta, timezone
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import perf
import learning as L

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
D = os.environ.get("RL_DATA", "data") + "/"     # picks.json + forecasts.json from the paged admin export
P = json.load(open(D + "picks.json"))
F = json.load(open(D + "forecasts.json"))
ts = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc) if s else None
byp = collections.defaultdict(list)
for f in F:
    byp[f["pick_id"]].append(f)


def latest_before(p, lead_min=30):
    st = ts(p["match_start"])
    fs = [f for f in byp.get(p["id"], []) if f["logged_at"] and ts(f["logged_at"]) <= st - timedelta(minutes=lead_min)]
    return max(fs, key=lambda f: (ts(f["logged_at"]), f["id"])) if fs else None


def rows_for(kind):
    out = []
    for p in P:
        if p.get("status") != "settled" or p.get("actual") is None or not p.get("match_start") or p.get("line") is None:
            continue
        board = p.get("book") is not None and p.get("on_board") in (1, 2)
        lt = p.get("line_type") or "standard"
        if kind == "board" and not (board and lt == "standard"):
            continue
        if kind == "member" and not (p.get("book") is None and lt == "standard"):
            continue
        if kind == "adjusted" and lt == "standard":
            continue
        f = latest_before(p)
        if f is None or f.get("p_more") is None or f.get("lambda") is None:
            continue
        line, act = float(p["line"]), float(p["actual"])
        if act == line:
            continue
        sc = p.get("scope") or "12"
        prop = p["prop"]
        lam = float(f["lambda"])
        c = perf.SPREAD.get((prop, sc), perf.SPREAD[(prop, "12")])
        sd = c * math.sqrt(max(lam, 1.0))
        try:
            q = json.loads(f["quote"]) if f.get("quote") else None
        except ValueError:
            q = None
        rh = f.get("rec_hit") or p.get("rec_hit")
        try:
            ro, rn = (int(x) for x in str(rh).split("/"))
        except (TypeError, ValueError):
            ro = rn = None
        out.append({"pick": p["id"], "f": f["id"], "unit": (p.get("player_id") or p.get("slug"), p.get("match_slug"), prop, sc),
                    "match": p.get("match_slug"), "day": ts(p["match_start"]).date().isoformat(), "start": p["match_start"],
                    "logged": f["logged_at"], "book": p.get("book"), "prop": prop, "scope": sc, "line": line, "actual": act,
                    "over": int(act > line), "p_live": float(f["p_more"]), "lam": lam, "sd": sd, "z": (lam - line) / sd,
                    "sample_maps": f.get("sample_maps") or p.get("sample_maps"), "rec": (ro / rn) if rn else None,
                    "quote": q, "version": f.get("model_version"), "units": f.get("units"), "grade": f.get("grade")})
    return out


def dedupe(rs):
    best = {}
    for r in rs:
        k = r["unit"]
        if k not in best or (r["logged"], r["f"]) > (best[k]["logged"], best[k]["f"]):
            best[k] = r
    return list(best.values())


ll = lambda p, y: -(y * math.log(max(p, 1e-9)) + (1 - y) * math.log(max(1 - p, 1e-9)))
br = lambda p, y: (p - y) ** 2
logit = lambda p: math.log(max(p, 1e-6) / max(1 - p, 1e-6))
sig = lambda x: 1 / (1 + math.exp(-x))


def fit_logit(X, y, lam_r):
    X, y = np.asarray(X, float), np.asarray(y, float)
    w = np.zeros(X.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + 2 * lam_r * w
        H = (X * (p * (1 - p))[:, None]).T @ X + 2 * lam_r * np.eye(X.shape[1])
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-9:
            break
    return w


FEATS = {
    "CORR": lambda r: [1.0, r["z"]],
    "CORR+thin": lambda r: [1.0, r["z"], r["z"] * float((r["sample_maps"] or 0) < 30)],
    "CORR+form": lambda r: [1.0, r["z"], (r["rec"] - 0.5) if r["rec"] is not None else 0.0],
    "CORR+book": lambda r: [1.0, r["z"], float(r["book"] == "prizepicks")],
    "CORR-logit": lambda r: [1.0, logit(r["p_live"])],
}
GRID = (0.3, 1, 3, 10, 30, 100)


def fit_by_stat(train, feat, lam_r):
    W = {}
    for s in ("kills", "hs"):
        tr = [r for r in train if r["prop"] == s]
        W[s] = fit_logit([FEATS[feat](r) for r in tr], [r["over"] for r in tr], lam_r) if len(tr) >= 20 else None
    return W


def pred(W, feat, r):
    w = W.get(r["prop"])
    return 0.5 if w is None else sig(float(np.dot(w, FEATS[feat](r))))


def choose_lambda(train, feat):
    days = sorted({r["day"] for r in train})
    if len(days) < 2:
        return 10.0
    inner_tr = [r for r in train if r["day"] < days[-1]]
    inner_te = [r for r in train if r["day"] == days[-1]]
    best = min(GRID, key=lambda lr: sum(ll(pred(fit_by_stat(inner_tr, feat, lr), feat, r), r["over"]) for r in inner_te))
    return best


def evaluate(rs, label):
    days = sorted({r["day"] for r in rs})
    test = [r for r in rs if r["day"] > days[0]]
    log = {"days": days, "tried": []}
    for r in test:
        tr = [x for x in rs if x["day"] < r["day"]]
        r["_tr_n"] = len(tr)
    for d in days[1:]:
        tr = [x for x in rs if x["day"] < d]
        te = [x for x in rs if x["day"] == d]
        for s in ("kills", "hs"):
            t = [x for x in tr if x["prop"] == s]
            base = (sum(x["over"] for x in t) + 25) / (len(t) + 50)
            for x in te:
                if x["prop"] == s:
                    x["p_base"] = base
        for feat in FEATS:
            lr = choose_lambda(tr, feat)
            W = fit_by_stat(tr, feat, lr)
            log["tried"].append({"test_day": d, "feat": feat, "lambda_r": lr, "n_train": len(tr),
                                 "w": {k: (None if v is None else [round(float(a), 4) for a in v]) for k, v in W.items()}})
            for x in te:
                x["p_" + feat] = pred(W, feat, x)
    for x in test:
        x["p_flat"] = 0.5
        x["p_lb"] = L.line_blend_p(x["p_live"], x["prop"], x["scope"])
        pc, _, _ = L.prop_line_2_value({"source": "board", "line_type": "standard", "book": x["book"], "prop": x["prop"],
                                        "line": x["line"], "logged_at": x["logged"], "lambda": x["lam"]},
                                       {"perf": {"lambda": x["lam"], "sd": x["sd"]}}, x["quote"])
        x["p_pl2"] = pc
    return test, log


def groups(rs, fn):
    g = collections.defaultdict(lambda: [0.0, 0])
    for r in rs:
        v = fn(r)
        if v is None:
            continue
        g[r["match"]][0] += v
        g[r["match"]][1] += 1
    return list(g.values())


def ci(rs, fn, B=1000, seed=7):
    g = groups(rs, fn)
    if not g:
        return None
    rnd = random.Random(seed)
    vals = []
    for _ in range(B):
        s = n = 0.0
        for _ in range(len(g)):
            a, b = g[rnd.randrange(len(g))]
            s += a; n += b
        vals.append(s / n if n else 0)
    vals.sort()
    return round(vals[int(0.025 * B)], 4), round(vals[int(0.975 * B) - 1], 4)


def auc(ps, ys):
    pos = [p for p, y in zip(ps, ys) if y]
    neg = [p for p, y in zip(ps, ys) if not y]
    if not pos or not neg:
        return None
    s = 0.0
    allv = sorted([(p, 1) for p in pos] + [(p, 0) for p in neg])
    # rank-based (ties averaged)
    ranks, i = {}, 0
    vals = [v for v, _ in allv]
    rk = [0.0] * len(allv)
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and vals[j + 1] == vals[i]:
            j += 1
        for k in range(i, j + 1):
            rk[k] = (i + j) / 2 + 1
        i = j + 1
    rpos = sum(r for r, (_, y) in zip(rk, allv) if y)
    return (rpos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def calib_slope(ps, ys):
    X = [[1.0, logit(p)] for p in ps]
    w = fit_logit(X, ys, 1e-6)
    return round(float(w[1]), 3), round(float(w[0]), 3)


def report(test, label, models):
    print(f"\n######## {label}: {len(test)} rows, {len({r['match'] for r in test})} matches, test days {sorted({r['day'] for r in test})}")
    print("| model | log loss | Brier | AUC | calib slope | mean p(Over) | Over rate | Δ log loss vs LIVE (95%) | Δ vs BASE (95%) |")
    print("|---|---:|---:|---:|---:|---:|---:|---|---|")
    for m in models:
        rs = [r for r in test if r.get("p_" + m) is not None]
        if not rs:
            continue
        ps, ys = [r["p_" + m] for r in rs], [r["over"] for r in rs]
        dl = lambda r: ll(r["p_live"], r["over"]) - ll(r["p_" + m], r["over"])
        db = lambda r: ll(r["p_base"], r["over"]) - ll(r["p_" + m], r["over"])
        slope = calib_slope(ps, ys) if len(set(round(p, 6) for p in ps)) > 2 else ("-", "-")
        a = auc(ps, ys)
        n_note = f" (n={len(rs)})" if len(rs) != len(test) else ""
        print(f"| {m}{n_note} | {sum(ll(p, y) for p, y in zip(ps, ys)) / len(ps):.4f} | {sum(br(p, y) for p, y in zip(ps, ys)) / len(ps):.4f} | "
              f"{'-' if a is None else f'{a:.3f}'} | {slope[0]} | {sum(ps) / len(ps):.3f} | {sum(ys) / len(ys):.3f} | "
              f"{sum(map(dl, rs)) / len(rs):+.4f} {ci(rs, dl)} | {sum(map(db, rs)) / len(rs):+.4f} {ci(rs, db)} |")


def recs(test, label, models, thr=0.57):
    print(f"\n-- recommendation rule (favoured side chance >= {thr}) - {label}")
    print("| model | picks | matches | mean printed | hit rate (95% match-grouped) | Overs / Unders | hit Overs | hit Unders |")
    print("|---|---:|---:|---:|---|---|---:|---:|")
    for m in models:
        sel = []
        for r in test:
            p = r.get("p_" + m)
            if p is None:
                continue
            if p >= thr:
                sel.append(dict(r, side="Over", pp=p, hitv=r["over"]))
            elif 1 - p >= thr:
                sel.append(dict(r, side="Under", pp=1 - p, hitv=1 - r["over"]))
        if not sel:
            print(f"| {m} | 0 | 0 | - | - | - | - | - |")
            continue
        o = [s for s in sel if s["side"] == "Over"]; u = [s for s in sel if s["side"] == "Under"]
        hr = sum(s["hitv"] for s in sel) / len(sel)
        print(f"| {m} | {len(sel)} | {len({s['match'] for s in sel})} | {sum(s['pp'] for s in sel) / len(sel):.3f} | "
              f"{hr:.3f} {ci(sel, lambda s: s['hitv'])} | {len(o)} / {len(u)} | "
              f"{(sum(s['hitv'] for s in o) / len(o)) if o else float('nan'):.3f} | {(sum(s['hitv'] for s in u) / len(u)) if u else float('nan'):.3f} |")


def proj(test, label):
    print(f"\n-- projection error ({label}): point forecasts vs the actual total")
    for s in ("kills", "hs"):
        rs = [r for r in test if r["prop"] == s]
        if not rs:
            continue
        for nm, fn in (("live projection (lambda)", lambda r: r["lam"]), ("offered line", lambda r: r["line"])):
            e = [fn(r) - r["actual"] for r in rs]
            print(f"   {s:5s} {nm:26s} n={len(rs)} MAE {sum(map(abs, e)) / len(e):.2f}  RMSE {math.sqrt(sum(x * x for x in e) / len(e)):.2f}  bias {sum(e) / len(e):+.2f}")
        g = lambda r: abs(r["line"] - r["actual"]) - abs(r["lam"] - r["actual"])
        print(f"   {s:5s} MAE gain of the projection over the line: {sum(map(g, rs)) / len(rs):+.3f} {ci(rs, g)}")


if __name__ == "__main__":
    MODELS = ["live", "base", "flat", "lb", "pl2", "CORR", "CORR+thin", "CORR+form", "CORR+book", "CORR-logit"]
    allb = rows_for("board")
    units = dedupe(allb)
    by_day = collections.Counter(r["day"] for r in units)
    print("board units by match day:", dict(sorted(by_day.items())))
    test, log = evaluate(units, "units")
    report(test, "P1 units (one per player x match x stat x scope)", MODELS)
    recs(test, "P1 units", MODELS)
    proj(test, "P1 units, test days")
    json.dump(log, open(D + "realline_configs.json", "w"), indent=1)
    # sensitivity: every offered line (both platforms), and the separate cohorts
    lines = [r for r in allb]
    t2, _ = evaluate(lines, "lines")
    report(t2, "every offered line (both platforms, not deduped)", ["live", "base", "lb", "pl2", "CORR"])
    recs(t2, "every offered line", ["live", "base", "CORR"])
    for kind in ("member", "adjusted"):
        rs = rows_for(kind)
        if rs:
            print(f"\n{kind} lines (kept separate): {len(rs)} rows / {len({r['match'] for r in rs})} matches; live log loss "
                  f"{sum(ll(r['p_live'], r['over']) for r in rs) / len(rs):.4f}, Over rate {sum(r['over'] for r in rs) / len(rs):.3f}, "
                  f"mean p(Over) {sum(r['p_live'] for r in rs) / len(rs):.3f}")
    json.dump([{k: v for k, v in r.items() if k not in ("quote",)} for r in test], open(D + "realline_test_rows.json", "w"), default=str)
