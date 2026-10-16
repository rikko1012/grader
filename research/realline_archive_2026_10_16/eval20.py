"""Full real-line archive evaluation (PROTOCOL.md, fixed before this ran). Development evidence; read-only."""
import collections
import json
import math
import os
import pickle
import sys
from statistics import NormalDist

import numpy as np

RL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\eval-pipeline")
import perf  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
ND = NormalDist()
B = 2000
RNG = np.random.default_rng(20261016)
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
clip = lambda p: min(max(p, 1e-4), 1 - 1e-4)
ll = lambda p, y: -math.log(clip(p) if y else 1 - clip(p))
br = lambda p, y: (p - y) ** 2
SPREAD = lambda prop, scope: perf.SPREAD.get((prop, scope), perf.SPREAD[(prop, "12")])


def normal_over(mu, lam, line, prop, scope):
    sd = SPREAD(prop, scope) * math.sqrt(max(lam, 1.0))
    if line != int(line):
        po = 1 - Phi((math.floor(line) + 0.5 - mu) / sd)
        pu = 1 - po
    else:
        po, pu = 1 - Phi((line + 0.5 - mu) / sd), Phi((line - 0.5 - mu) / sd)
    p = po / (po + pu) if po + pu > 0 else 0.5
    return min(max(p, 0.22), 0.78) if prop == "kills" else p


# ------------------------------------------------------------------ match-grouped bootstrap
class Boot:
    def __init__(self, rows):
        self.keys = sorted({r["m"] for r in rows})
        self.ix = {k: i for i, k in enumerate(self.keys)}
        self.g = np.array([self.ix[r["m"]] for r in rows])
        self.draw = RNG.integers(0, len(self.keys), size=(B, len(self.keys)))
        self.cnt = np.bincount(self.g, minlength=len(self.keys)).astype(float)

    def ratio(self, vals, mask=None):
        v = np.asarray(vals, float)
        w = np.ones_like(v) if mask is None else np.asarray(mask, float)
        s = np.bincount(self.g, weights=v * w, minlength=len(self.keys))
        c = np.bincount(self.g, weights=w, minlength=len(self.keys))
        S, C = s[self.draw].sum(1), c[self.draw].sum(1)
        ok = C > 0
        est = s.sum() / c.sum() if c.sum() else float("nan")
        lo, hi = np.percentile(S[ok] / C[ok], [2.5, 97.5]) if ok.any() else (float("nan"),) * 2
        return est, lo, hi

    def slope(self, x, y, mask=None):
        """OLS slope of y on x, match-grouped bootstrap."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        w = np.ones_like(x) if mask is None else np.asarray(mask, float)
        def agg(a):
            return np.bincount(self.g, weights=a * w, minlength=len(self.keys))
        n, sx, sy, sxx, sxy = agg(np.ones_like(x)), agg(x), agg(y), agg(x * x), agg(x * y)
        def sl(N, SX, SY, SXX, SXY):
            return (N * SXY - SX * SY) / (N * SXX - SX * SX)
        est = sl(n.sum(), sx.sum(), sy.sum(), sxx.sum(), sxy.sum())
        d = self.draw
        bs = sl(n[d].sum(1), sx[d].sum(1), sy[d].sum(1), sxx[d].sum(1), sxy[d].sum(1))
        lo, hi = np.percentile(bs, [2.5, 97.5])
        return est, lo, hi


def logit_slope(p, y):
    """Calibration slope: logistic regression of y on logit(p) (Newton)."""
    x = np.log(np.clip(p, 1e-4, 1 - 1e-4) / (1 - np.clip(p, 1e-4, 1 - 1e-4)))
    a = b = 0.0
    for _ in range(50):
        z = a + b * x
        q = 1 / (1 + np.exp(-z))
        w = q * (1 - q)
        g = np.array([np.sum(y - q), np.sum((y - q) * x)])
        H = np.array([[np.sum(w), np.sum(w * x)], [np.sum(w * x), np.sum(w * x * x)]]) + 1e-9 * np.eye(2)
        step = np.linalg.solve(H, g)
        a, b = a + step[0], b + step[1]
        if np.abs(step).max() < 1e-9:
            break
    return b


def auc(p, y):
    p, y = np.asarray(p), np.asarray(y)
    o = np.argsort(p)
    r = np.empty(len(p))
    r[o] = np.arange(1, len(p) + 1)
    for v in np.unique(p):                                 # mid-ranks for ties
        m = p == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    n1, n0 = y.sum(), len(y) - y.sum()
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else float("nan")


def slope_ci(bt, rows, key):
    p = np.array([r[key] for r in rows])
    y = np.array([r["y"] for r in rows], float)
    est = logit_slope(p, y)
    vals = []
    for d in bt.draw[:400]:
        idx = np.concatenate([np.where(bt.g == k)[0] for k in d])
        vals.append(logit_slope(p[idx], y[idx]))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return est, lo, hi


# ------------------------------------------------------------------ chronological fits (earlier days only)
def add_base(rows):
    days = sorted({r["day"] for r in rows})
    acc = collections.defaultdict(lambda: [0, 0])
    for d in days:
        today = [r for r in rows if r["day"] == d]
        for r in today:
            o, n = acc[(r["prop"], r["scope"])]
            r["p_BASE"] = (o + 25) / (n + 50)
        for r in today:
            acc[(r["prop"], r["scope"])][0] += r["y"]
            acc[(r["prop"], r["scope"])][1] += 1


def fit_corr(train, lam_key, ridge):
    W = {}
    for prop in ("kills", "hs"):
        t = [r for r in train if r["prop"] == prop]
        if len(t) < 30:
            W[prop] = (0.0, 0.0)
            continue
        z = np.array([(r[lam_key] - r["line"]) / (SPREAD(prop, r["scope"]) * math.sqrt(max(r[lam_key], 1.0))) for r in t])
        y = np.array([r["y"] for r in t], float)
        a = b = 0.0
        for _ in range(60):
            q = 1 / (1 + np.exp(-(a + b * z)))
            w = q * (1 - q)
            g = np.array([np.sum(y - q) - ridge * a, np.sum((y - q) * z) - ridge * b])
            H = np.array([[np.sum(w) + ridge, np.sum(w * z)], [np.sum(w * z), np.sum(w * z * z) + ridge]])
            st = np.linalg.solve(H, g)
            a, b = a + st[0], b + st[1]
            if np.abs(st).max() < 1e-10:
                break
        W[prop] = (a, b)
    return W


def corr_p(r, W, lam_key):
    a, b = W[r["prop"]]
    z = (r[lam_key] - r["line"]) / (SPREAD(r["prop"], r["scope"]) * math.sqrt(max(r[lam_key], 1.0)))
    return 1 / (1 + math.exp(-(a + b * z)))


def shrink_p(p, w):
    return ND.cdf(w * ND.inv_cdf(min(max(p, 1e-6), 1 - 1e-6)))


def anchor_p(r, w, lam_key):
    m = (r["line"] + r["other_line"]) / 2 if r.get("other_line") is not None else r["line"]
    return normal_over(m + w * (r[lam_key] - m), r[lam_key], r["line"], r["prop"], r["scope"])


GRID = [round(0.05 * i, 2) for i in range(25)]


def fit_grid(train, fn):
    W = {}
    for prop in ("kills", "hs"):
        t = [r for r in train if r["prop"] == prop]
        W[prop] = min(GRID, key=lambda w: (sum(ll(fn(r, w), r["y"]) for r in t), abs(w - 1))) if t else 1.0
    return W


def rolling(rows, prob_key, lam_key, tag):
    """Fit CORR / SHRINK / ANCHOR on earlier days, score each later day (>= 300 earlier observations)."""
    days = sorted({r["day"] for r in rows})
    log = []
    for d in days:
        train = [r for r in rows if r["day"] < d]
        test = [r for r in rows if r["day"] == d]
        if len(train) < 300:
            continue
        tdays = sorted({r["day"] for r in train})
        inner_tr = [r for r in train if r["day"] < tdays[-1]]
        inner_te = [r for r in train if r["day"] == tdays[-1]]
        best = min((0.3, 1, 3, 10, 30, 100), key=lambda lr: sum(ll(corr_p(r, fit_corr(inner_tr, lam_key, lr), lam_key), r["y"]) for r in inner_te)
                   if len(inner_tr) >= 60 else 0)
        Wc = fit_corr(train, lam_key, best)
        Ws = fit_grid(train, lambda r, w: shrink_p(r[prob_key], w))
        Wa = fit_grid(train, lambda r, w: anchor_p(r, w, lam_key))
        for r in test:
            r[f"p_CORR_{tag}"] = corr_p(r, Wc, lam_key)
            r[f"p_SHRINKfit_{tag}"] = shrink_p(r[prob_key], Ws[r["prop"]])
            r[f"p_ANCHORfit_{tag}"] = anchor_p(r, Wa[r["prop"]], lam_key)
            r["scored"] = True
        log.append({"day": d, "n_train": len(train), "ridge": best, "corr": {k: [round(x, 4) for x in v] for k, v in Wc.items()},
                    "shrink_w": Ws, "anchor_w": Wa})
    return log


def frozen(rows, prob_key, lam_key, tag):
    for r in rows:
        r[f"p_LB_{tag}"] = shrink_p(r[prob_key], {"kills": 0.0, "hs": 0.5}[r["prop"]]) if r["scope"] == "12" else r[prob_key]
        r[f"p_PL2_{tag}"] = anchor_p(r, {"kills": 0.0, "hs": 0.25}[r["prop"]], lam_key)


# ------------------------------------------------------------------ reports
def prob_table(rows, models, ref_keys, label):
    bt = Boot(rows)
    y = np.array([r["y"] for r in rows], float)
    print(f"\n### {label}: {len(rows)} observations / {len(bt.keys)} matches, days {min(r['day'] for r in rows)} .. "
          f"{max(r['day'] for r in rows)}, Over rate {y.mean():.3f}")
    head = "| model | log loss | Brier | AUC | calib slope (95%) | mean est - obs (95%) |" + "".join(f" log-loss gain vs {k} (95%) | Brier gain vs {k} (95%) |" for k in ref_keys)
    print(head)
    print("|" + "---|" * (6 + 2 * len(ref_keys)))
    out = {}
    for m in models:
        p = np.array([r[m] for r in rows])
        L_ = np.array([ll(a, b) for a, b in zip(p, y)])
        Br = (p - y) ** 2
        sl = slope_ci(bt, rows, m) if p.std() > 1e-9 else (float("nan"),) * 3
        gap = bt.ratio(p - y)
        cells = [f"{L_.mean():.4f}", f"{Br.mean():.4f}", f"{auc(p, y):.3f}", f"{sl[0]:.2f} ({sl[1]:.2f}, {sl[2]:.2f})",
                 f"{gap[0]:+.3f} ({gap[1]:+.3f}, {gap[2]:+.3f})"]
        out[m] = {"ll": L_.mean(), "brier": Br.mean(), "auc": auc(p, y), "slope": sl, "gap": gap}
        for k in ref_keys:
            q = np.array([r[k] for r in rows])
            gl = bt.ratio(np.array([ll(a, b) for a, b in zip(q, y)]) - L_)
            gb = bt.ratio((q - y) ** 2 - Br)
            cells += [f"{gl[0]:+.4f} ({gl[1]:+.4f}, {gl[2]:+.4f})", f"{gb[0]:+.4f} ({gb[1]:+.4f}, {gb[2]:+.4f})"]
            out[m][f"vs_{k}"] = {"ll": gl, "brier": gb}
        print(f"| {m} | " + " | ".join(cells) + " |")
    return out


def selection(rows, models, label, thr=0.57):
    bt = Boot(rows)
    print(f"\n#### selection rule (favoured side >= {thr:.0%}) - {label}")
    print("| model | picks | matches | mean est | hit (95%) | BASE chance same sides | hit - BASE (95%) | Overs hit | Unders hit |")
    print("|---|---|---|---|---|---|---|---|---|")
    out = {}
    for m in models:
        mask, hit, pp, pb, ov = [], [], [], [], []
        for r in rows:
            p = r[m]
            s = max(p, 1 - p) >= thr
            mask.append(s)
            o = p >= 0.5
            hit.append(r["y"] if o else 1 - r["y"])
            pp.append(max(p, 1 - p))
            pb.append(r["p_BASE"] if o else 1 - r["p_BASE"])
            ov.append(o)
        mask, hit, pp, pb, ov = map(np.array, (mask, hit, pp, pb, ov))
        n = int(mask.sum())
        if not n:
            print(f"| {m} | 0 | - | - | - | - | - | - | - |")
            out[m] = {"picks": 0}
            continue
        h = bt.ratio(hit, mask)
        v = bt.ratio(hit - pb, mask)
        mt = len({r["m"] for r, s in zip(rows, mask) if s})
        ho = hit[mask & ov].mean() if (mask & ov).any() else float("nan")
        hu = hit[mask & ~ov].mean() if (mask & ~ov).any() else float("nan")
        print(f"| {m} | {n} | {mt} | {pp[mask].mean():.3f} | {h[0]:.3f} ({h[1]:.3f}, {h[2]:.3f}) | {pb[mask].mean():.3f} | "
              f"{v[0]:+.3f} ({v[1]:+.3f}, {v[2]:+.3f}) | {ho:.3f} ({int((mask & ov).sum())}) | {hu:.3f} ({int((mask & ~ov).sum())}) |")
        out[m] = {"picks": n, "matches": mt, "hit": h, "beyond_base": v}
    return out


def disagreement(rows, lam_key, label):
    """Why the projection's disagreement with the line fails: totals, not probabilities."""
    print(f"\n#### disagreement with the line ({label}, projection = {lam_key})")
    print("| slice | obs | matches | sd(proj - line) | slope of (actual - line) on (proj - line) (95%) | MSE proj - MSE line (95%) | "
          "proj-only error var (1-b)D | line-only error var bD |")
    print("|---|---|---|---|---|---|---|---|")
    res = {}
    slices = [("all kills Maps 1-2", lambda r: r["prop"] == "kills" and r["scope"] == "12"),
              ("all HS Maps 1-2", lambda r: r["prop"] == "hs" and r["scope"] == "12"),
              ("kills Map 1", lambda r: r["prop"] == "kills" and r["scope"] == "1"),
              ("HS Map 1", lambda r: r["prop"] == "hs" and r["scope"] == "1"),
              ("kills M1-2 Underdog line", lambda r: r["prop"] == "kills" and r["scope"] == "12" and "underdog" in r.get("books", [r.get("book")])),
              ("kills M1-2 PrizePicks line", lambda r: r["prop"] == "kills" and r["scope"] == "12" and "prizepicks" in r.get("books", [r.get("book")])),
              ("kills M1-2 history < 600 rounds", lambda r: r["prop"] == "kills" and r["scope"] == "12" and r["hist_r"] < 600),
              ("kills M1-2 history >= 600 rounds", lambda r: r["prop"] == "kills" and r["scope"] == "12" and r["hist_r"] >= 600),
              ("kills M1-2 original top-200 set", lambda r: r["prop"] == "kills" and r["scope"] == "12" and r["n0_target"]),
              ("kills M1-2 lower tier", lambda r: r["prop"] == "kills" and r["scope"] == "12" and not r["n0_target"]),
              ("kills M1-2 LAN", lambda r: r["prop"] == "kills" and r["scope"] == "12" and r["lan"]),
              ("kills M1-2 online", lambda r: r["prop"] == "kills" and r["scope"] == "12" and not r["lan"]),
              ("kills M1-2 |proj - line| >= 2", lambda r: r["prop"] == "kills" and r["scope"] == "12" and abs(r[lam_key] - r["line"]) >= 2)]
    for nm, f in slices:
        g = [r for r in rows if f(r)]
        if len(g) < 40:
            continue
        bt = Boot(g)
        x = np.array([r[lam_key] - r["line"] for r in g])
        yv = np.array([r["actual"] - r["line"] for r in g])
        b = bt.slope(x, yv)
        D = x.var()
        dm = bt.ratio((np.array([r[lam_key] for r in g]) - np.array([r["actual"] for r in g])) ** 2 - yv ** 2)
        print(f"| {nm} | {len(g)} | {len(bt.keys)} | {math.sqrt(D):.2f} | {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f}) | "
              f"{dm[0]:+.2f} ({dm[1]:+.2f}, {dm[2]:+.2f}) | {(1 - b[0]) * D:.2f} | {b[0] * D:.2f} |")
        res[nm] = {"n": len(g), "matches": len(bt.keys), "sdD": math.sqrt(D), "b": b, "dmse": dm}
    return res


def market_dev(rows, lam_key, label):
    """Does the line's departure from our projection follow pre-match observables, and is it right?"""
    g = [r for r in rows if r["prop"] == "kills" and r["scope"] == "12"]
    bt = Boot(g)
    dev = np.array([r["line"] - r[lam_key] for r in g])
    err = np.array([r["actual"] - r[lam_key] for r in g])
    b = bt.slope(dev, err)
    print(f"\n#### the line's departure from our projection ({label}, kills Maps 1-2, {len(g)} obs / {len(bt.keys)} matches)")
    print(f"  slope of (actual - projection) on (line - projection): {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f}) - 1 = the line is fully right, 0 = no information")
    for nm, xs in (("Elo gap (own - opponent)/100", [(r["elo"][0] - r["elo"][1]) / 100 for r in g]),
                   ("history rounds / 1000", [r["hist_r"] / 1000 for r in g]),
                   ("LAN", [1.0 if r["lan"] else 0.0 for r in g])):
        s = bt.slope(np.array(xs), dev)
        s2 = bt.slope(np.array(xs), err)
        print(f"  {nm}: slope of (line - projection) {s[0]:+.3f} ({s[1]:+.3f}, {s[2]:+.3f}); slope of (actual - projection) {s2[0]:+.3f} ({s2[1]:+.3f}, {s2[2]:+.3f})")
    return b


def cross_platform(rows, label):
    g = [r for r in rows if r.get("other_line") is not None]
    if len(g) < 40:
        print(f"\n#### cross-platform ({label}): {len(g)} observations - too few")
        return None
    bt = Boot(g)
    x = np.array([r["other_line"] - r["line"] for r in g])
    yv = np.array([r["actual"] - r["line"] for r in g])
    b = bt.slope(x, yv)
    side = np.array([(r["y"] if r["other_line"] > r["line"] else 1 - r["y"]) for r in g])
    h = bt.ratio(side)
    print(f"\n#### cross-platform lines differ ({label}): {len(g)} observations / {len(bt.keys)} matches")
    print(f"  slope of (actual - this line) on (other line - this line): {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f}) (0.5 = the mean of the two lines is right)")
    print(f"  taking the side the other platform's line points to: hit {h[0]:.3f} ({h[1]:.3f}, {h[2]:.3f})")
    return {"b": b, "hit": h, "n": len(g), "matches": len(bt.keys)}


def main():
    E1 = pickle.load(open(os.path.join(RL, "archive.pkl"), "rb"))
    E2 = pickle.load(open(os.path.join(RL, "ours.pkl"), "rb"))
    for r in E1:
        r["flat"] = 0.5
    for r in E2:
        r["flat"] = 0.5
        r["other_line"] = None
    # other platform's line for E2 (when the two platforms' saved lines differ)
    u = collections.defaultdict(list)
    for r in E2:
        u[(r["m"], r["p_id"], r["prop"], r["scope"])].append(r)
    for v in u.values():
        if len(v) == 2 and v[0]["line"] != v[1]["line"]:
            v[0]["other_line"], v[1]["other_line"] = v[1]["line"], v[0]["line"]
    add_base(E1)
    add_base(E2)
    res = {}
    # ---------------- E1: offered-line archive, consistent reconstructed models
    frozen(E1, "p_n0", "lam_n0", "proj")
    frozen(E1, "p_n", "lam_n", "full")
    log1 = rolling(E1, "p_n0", "lam_n0", "proj")
    rolling(E1, "p_n", "lam_n", "full")
    sc1 = [r for r in E1 if r.get("scored")]
    models1 = ["flat", "p_BASE", "p_n0", "p_n", "p_LB_proj", "p_PL2_proj", "p_CORR_proj", "p_SHRINKfit_proj", "p_ANCHORfit_proj",
               "p_CORR_full", "p_ANCHORfit_full"]
    res["E1"] = prob_table(sc1, models1, ["p_n0", "p_BASE"], "E1 offered-line archive, scored days (rolling origin)")
    res["E1_sel"] = selection(sc1, models1[2:], "E1")
    res["E1_dis"] = disagreement(E1, "lam_n0", "E1 all days")
    disagreement(E1, "lam_n", "E1 all days, full history")
    res["E1_dev"] = market_dev(E1, "lam_n0", "E1")
    res["E1_x"] = cross_platform(E1, "E1")
    # ---------------- E2: our saved forecasts at their own lines
    p2 = {"6f6e2d39d7", "a310d76b55", "9cfcbd217a", "1088faf157", "23bea2fe37", "6b6a43f2a6", "77d2cdf508", "c80ac64bc8", "0d6b117256",
          "313626ab10", "a64083e3c2", "fe063dc2e9", "af09e740c8", "e94c5e5c1a"}
    for r in E2:
        r["pipeline"] = "perf-2 era tags (Oct 11+)" if r["host"] == "render" else "Railway (older pipelines / early perf-2)"
    frozen(E2, "p_live", "lam_n0", "live")
    log2 = rolling(E2, "p_live", "lam_n0", "live")
    sc2 = [r for r in E2 if r.get("scored")]
    models2 = ["flat", "p_BASE", "p_live", "p_n0", "p_n", "p_LB_live", "p_PL2_live", "p_CORR_live", "p_SHRINKfit_live", "p_ANCHORfit_live"]
    res["E2"] = prob_table(sc2, models2, ["p_live", "p_BASE"], "E2 our saved forecasts, scored days (rolling origin)")
    res["E2_sel"] = selection(sc2, models2[2:], "E2")
    for pl in sorted({r["pipeline"] for r in E2}):
        g = [r for r in E2 if r["pipeline"] == pl]
        prob_table(g, ["p_BASE", "p_live", "p_n0"], ["p_live", "p_BASE"], f"E2 by pipeline: {pl}")
        selection(g, ["p_live", "p_n0"], f"E2 {pl}")
    rr = [r for r in E2 if r.get("p_rr") is not None]
    if rr:
        prob_table(rr, ["p_BASE", "p_live", "p_rr"], ["p_live", "p_BASE"], "E2 rows with a saved rr-1 value (logged from 2026-10-13)")
        selection(rr, ["p_live", "p_rr"], "E2 rr-1 rows")
        fw = [r for r in rr if r["logged"] > "2026-10-13T17:51" and r["start"] > "2026-10-13T17:51"]
        prob_table(fw, ["p_BASE", "p_live", "p_rr"], ["p_live", "p_BASE"], "rr-1 FORWARD (logged and started after its freeze 10-13 17:51)")
        selection(fw, ["p_live", "p_rr"], "rr-1 forward")
    lb = [r for r in E2 if r["logged"] > "2026-10-12T15:49" and r["start"] > "2026-10-12T15:49"]
    prob_table(lb, ["p_BASE", "p_live", "p_LB_live"], ["p_live", "p_BASE"], "lb-001 FORWARD (exact: its frozen transform of the saved live chance)")
    selection(lb, ["p_live", "p_LB_live"], "lb-001 forward")
    res["E2_dis"] = disagreement(E2, "lam_n0", "E2 all days")
    res["E2_x"] = cross_platform(E2, "E2")
    json.dump({"log_E1": log1, "log_E2": log2}, open(os.path.join(RL, "eval20_fits.json"), "w"), indent=1, default=str)
    pickle.dump({"E1": E1, "E2": E2, "res": res}, open(os.path.join(RL, "eval20_rows.pkl"), "wb"))


if __name__ == "__main__":
    main()
