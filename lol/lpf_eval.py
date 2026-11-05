"""lpf-1 walk-forward evaluation (research only; reads a read-only local copy of production's LoL tables, writes one report;
never touches live data, never deploys).

  python -m lol.lpf_eval --db C:\\Users\\tikic\\cs2_scout_ops\\data\\lol_full\\lol_full.db --cache <dir> --out lol/eval/<report>.json

STAGE A - every historical outcome (no book lines): for every (series, player) of the history and every scope it can be
settled for (Map 1; Maps 1-2 in best-of-3 / best-of-5; Maps 1-3 in best-of-5 and, as the A2 mixture, best-of-3), the
predicted distribution is scored at the realised total: log score (log pmf), PIT histogram, P(X > t) calibration at every
half-integer threshold within 2 of the predicted mean (5-pt bins), MAE of the mean. Baseline = the live model re-run on
the same history (lpf_data.LiveBaseline; reconstruction accuracy reported). Candidates are refit at the first of every
month on series of EARLIER days only (features of every row from days before its own match day); the NB sizes are fitted on
out-of-sample predictions of the 60 days before the refit. CIs resample whole series (2,000 bootstrap draws).
  DEV     refits 2026-03-01 .. 2026-09-01 (test series 2026-03-01 .. 2026-09-30): all iteration happened here.
  HOLDOUT refits 2026-10-01 and 2026-11-01 (test series 2026-10-01 .. 2026-11-04): untouched until the final run, reported
          separately.
STAGE B - the settled graded props at the offered lines (2026-10-17 .. 2026-11-05; the latest pre-match saved forecast per
line with its latest settlement, as gradecore.chance_cal.LOL_ROWS_SQL): the candidate's chance at the same line from its
holdout refit; calibration per 5-pt bin, Over / Under separately; log loss vs live; graded (side chance >= 55%) count and hit
rate vs live; Over share.
PASS (fixed by the task before any number): HOLDOUT all-outcome log score better with the clustered CI excluding 0, AND at the
offered lines |stated - hit| <= 2.5 pts overall / Over / Under, AND graded count >= 90% of live, AND graded hit not worse.
"""
import argparse
import datetime as dt
import json
import math
import os
import pickle
import sys
import time
from collections import defaultdict

import numpy as np

from . import lpf, lpf_data as D

BOOT = 2000
REFITS_DEV = ["2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]
REFITS_HOLD = ["2026-10-01", "2026-11-01"]
END = "2026-11-06"
SIZE_WINDOW = 60
P3_WINDOW = 365         # the decider model is refit on the best-of-3s of the year before the refit
SCOPES = ("1", "12", "123")


def iso(d):
    return dt.date.fromordinal(int(d)).isoformat()


def cluster_ci(vals, clusters, B=BOOT, seed=1):
    vals = np.asarray(vals, dtype=float)
    keys, inv = np.unique(np.asarray(clusters), return_inverse=True)
    s = np.bincount(inv, weights=vals, minlength=len(keys))
    n = np.bincount(inv, minlength=len(keys)).astype(float)
    rng = np.random.default_rng(seed)
    b = rng.integers(0, len(keys), (B, len(keys)))
    est = s[b].sum(1) / n[b].sum(1)
    return [round(float(vals.mean()), 5), round(float(np.percentile(est, 2.5)), 5), round(float(np.percentile(est, 97.5)), 5)]


# ============================================================================================ build (cached)
def build(db, cache, log):
    path = os.path.join(cache, "lpf_build.pkl") if cache else None
    if path and os.path.exists(path):
        log(f"loading cached build {path}")
        with open(path, "rb") as fh:
            return pickle.load(fh)
    t0 = time.time()
    st = D.Store(db)
    T = D.targets(st)
    B = D.LiveBaseline(st, log)
    for t in T:
        t["live"], t["p_live"] = B.project(t)
    log(f"baseline: {len(T)} targets {time.time() - t0:.0f}s")
    Fz = lpf.Features(st, log=log)
    feats = []
    for i, t in enumerate(T):
        feats.append(Fz.target(t))
        if i % 10000 == 0:
            log(f"features {i}/{len(T)} {time.time() - t0:.0f}s")
    X, names = lpf.matrix(feats)
    sizes = {f"{s}|{sc}": B.size(s, sc) for s in D.STATS for sc in SCOPES}
    for t in T:
        t.pop("tournament_obj", None)
    out = {"T": T, "X": X, "names": names, "live_sizes": sizes, "db_rows": len(st.rows)}
    # the settled record (Stage B) - read here so the harness needs the DB once
    sql = ("SELECT f.*, s.status AS s_status, s.over_hit AS s_over_hit, s.push AS s_push, s.actual AS s_actual "
           "FROM forecasts f JOIN (SELECT platform, market_key, line, MAX(id) AS id FROM forecasts WHERE forecast_at < series_start "
           "GROUP BY platform, market_key, line) z ON z.id = f.id "
           "JOIN (SELECT unit_key, MAX(version) AS v FROM settlements GROUP BY unit_key) lv ON "
           "lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line "
           "JOIN settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v")
    out["settled"] = [dict(r) for r in st.con.execute(sql)]
    out["live_forecasts_check"] = reconstruction_check(st, T)
    if path:
        os.makedirs(cache, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(out, fh)
    return out


def reconstruction_check(st, T):
    """How close the offline baseline is to the live model: the saved pre-match lol-1f forecasts vs the reconstruction for the
    same (series, player): per-game lambda, the factor multiplier and the Elo p_win."""
    idx = {(t["match"], t["pid"]): t for t in T}
    res = {}
    for stat in ("kills", "assists"):
        dl, dp, n = [], [], 0
        for r in st.con.execute("SELECT sid, pid, inputs FROM forecasts WHERE model_version = 'lol-1f' AND stat = ?", (stat,)):
            t = idx.get((r["sid"], r["pid"]))
            if not t:
                continue
            inp = json.loads(r["inputs"] or "{}")
            if not inp.get("lambda_game"):
                continue
            n += 1
            dl.append(t["live"][stat][0] / inp["lambda_game"] - 1)
            dp.append(t["p_live"] - inp["p_win"])
        dl, dp = np.abs(dl), np.abs(dp)
        res[stat] = {"saved_forecasts": n, "lambda_rel_diff_median": round(float(np.median(dl)), 5),
                     "lambda_rel_diff_p90": round(float(np.percentile(dl, 90)), 5), "within_1pct": round(float((dl < 0.01).mean()), 4),
                     "within_3pct": round(float((dl < 0.03).mean()), 4), "p_win_abs_diff_median": round(float(np.median(dp)), 5)}
    return res


# ============================================================================================ rows
class Frame:
    def __init__(self, b):
        self.T, self.X, self.names = b["T"], b["X"], list(b["names"])
        self.col = {n: i for i, n in enumerate(self.names)}
        self.day = np.array([t["day"] for t in self.T])
        self.match = np.array([t["match"] for t in self.T])
        self.role = np.array([t["role"] for t in self.T])
        self.bo = np.array([t["best_of"] for t in self.T])
        self.live_sizes = b["live_sizes"]
        # per-game rows: (target index, game number, win of that game)
        gi, gn = [], []
        for i, t in enumerate(self.T):
            for n in (1, 2, 3):
                if n in t["games"]:
                    gi.append(i)
                    gn.append(n)
        self.gi, self.gn = np.array(gi), np.array(gn, dtype=float)
        self.gw = np.full(len(gi), np.nan)          # filled by wins_per_game (W variant training rows only)
        self.gy = {s: np.array([self.T[i]["games"][n][D.J[s]] for i, n in zip(gi, gn)], dtype=float) for s in D.STATS}

    def F(self, idx):
        return {k: self.X[idx, self.col[k]] for k in lpf.WinModel.COLS}


# ============================================================================================ candidate
class Candidate:
    """Walk-forward refits of one variant: 'M' (per-game mean) or 'W' (win/loss conditional)."""

    def __init__(self, fr, variant="M", drop=(), rounds=lpf.N_ROUNDS, params=None, log=print, offset=False):
        self.fr, self.variant, self.rounds, self.params, self.log = fr, variant, rounds, params, log
        self.offset = offset
        # offset: the booster starts from the live projection (log of the reconstructed live per-game lambda) and learns
        # only the correction; the live lambda is itself a function of earlier games only
        self.off = {s: np.log(np.maximum([t["live"][s][0] for t in fr.T], 1e-3)) for s in D.STATS}
        self.feat = [n for n in fr.names if n not in set(drop)]
        self.fi = [fr.col[n] for n in self.feat]

    def _gX(self, rows, gn, win=None):
        X = self.fr.X[rows][:, self.fi]
        cols = [X, gn[:, None], ((gn == 3) & (self.fr.bo[rows] == 3)).astype(float)[:, None]]
        if self.variant == "W":
            cols.append(win[:, None])
        return np.hstack(cols)

    def names(self):
        return self.feat + ["gn", "decider"] + (["win"] if self.variant == "W" else [])

    def train(self, d_end, d_start=None):
        """Boosters per stat on per-game rows of targets with day < d_end."""
        fr = self.fr
        m = fr.day[fr.gi] < d_end
        if d_start is not None:
            m &= fr.day[fr.gi] >= d_start
        if self.variant == "W":
            m &= np.isfinite(fr.gw)
        rows = fr.gi[m]
        X = self._gX(rows, fr.gn[m], fr.gw[m] if self.variant == "W" else None)
        return {s: lpf.fit_booster(X, fr.gy[s][m], self.names(), self.rounds, self.params,
                                   init=self.off[s][rows] if self.offset else None) for s in D.STATS}

    def _pred(self, b, X, s, idx):
        if not self.offset:
            return b.predict(X)
        return np.exp(b.predict(X, raw_score=True) + self.off[s][idx])

    def game_means(self, boosters, idx):
        """{stat: {(n, win): mu array}} for targets idx, games 1-3 (win None for M)."""
        out = {}
        for s, b in boosters.items():
            o = {}
            for n in (1, 2, 3):
                gn = np.full(len(idx), float(n))
                if self.variant == "W":
                    for w in (1.0, 0.0):
                        o[(n, w)] = self._pred(b, self._gX(idx, gn, np.full(len(idx), w)), s, idx)
                else:
                    o[(n, None)] = self._pred(b, self._gX(idx, gn), s, idx)
            out[s] = o
        return out

    def comps(self, mus, stat, scope, idx, p, p3=None):
        """Mixture components per target for a scope: list of (weight array, mean array, size key). p3 = P(a best-of-3
        reaches game 3) from the walk-forward decider model (None = the independence value 2 p (1 - p), as live A2)."""
        g = mus[stat]
        bo = self.fr.bo[idx]
        if p3 is None:
            p3 = 2 * p * (1 - p)
        if self.variant == "M":
            m1, m2, m3 = g[(1, None)], g[(2, None)], g[(3, None)]
            if scope == "1":
                return [(np.ones(len(idx)), m1, "1")]
            if scope == "12":
                return [(np.ones(len(idx)), m1 + m2, "12")]
            p3 = np.where(bo == 3, p3, 1.0)
            return [(p3, m1 + m2 + m3, "123"), (1 - p3, m1 + m2, "12")]
        W = {n: g[(n, 1.0)] for n in (1, 2, 3)}
        L = {n: g[(n, 0.0)] for n in (1, 2, 3)}
        q = 1 - p
        if scope == "1":
            return [(p, W[1], "1"), (q, L[1], "1")]
        if scope == "12":
            return [(p * p, W[1] + W[2], "12"), (2 * p * q, (W[1] + L[2] + L[1] + W[2]) / 2, "12"), (q * q, L[1] + L[2], "12")]
        s3 = W[1] + W[2] + W[3]
        l3 = L[1] + L[2] + L[3]
        bo5 = [(p ** 3, s3, "123"), (3 * p * p * q, s3 * 2 / 3 + l3 / 3, "123"), (3 * p * q * q, s3 / 3 + l3 * 2 / 3, "123"),
               (q ** 3, l3, "123")]
        sw = p * p + q * q
        bo3 = [((1 - p3) * p * p / sw, W[1] + W[2], "12"), ((1 - p3) * q * q / sw, L[1] + L[2], "12"),
               (p3 * p, (W[1] + L[2] + W[3] + L[1] + W[2] + W[3]) / 2, "123"),
               (p3 * q, (W[1] + L[2] + L[3] + L[1] + W[2] + L[3]) / 2, "123")]
        is3 = (bo == 3)
        out = []
        for (w5, m5, _), (w3, m3_, k3) in zip(bo5, bo3):
            out.append((np.where(is3, w3, w5), np.where(is3, m3_, m5), np.where(is3, k3, "123")))
        return out

    def mix(self, comps, stat, role, sizes):
        Wt = np.column_stack([c[0] for c in comps])
        M = np.column_stack([c[1] for c in comps])
        S = np.column_stack([np.array([sizes.get((stat, (c[2] if isinstance(c[2], str) else c[2][i]), r), sizes.get((stat, (c[2] if isinstance(c[2], str) else c[2][i]), None), 5.0))
                                       for i, r in enumerate(role)]) for c in comps])
        return lpf.Mix(Wt, M, S)

    def fit_sizes(self, d_end, win_model):
        """NB size per (stat, scope-part, role) by ML on out-of-sample totals of the SIZE_WINDOW days before d_end, from a
        booster trained on targets before that window."""
        fr = self.fr
        lo = d_end - SIZE_WINDOW
        b = self.train(lo)
        idx = np.where((fr.day >= lo) & (fr.day < d_end))[0]
        mus = self.game_means(b, idx)
        p = win_model.p(fr.F(idx))
        p3 = win_model.p3(p)
        grid = np.exp(np.linspace(math.log(0.7), math.log(300), 60))
        sizes = {}
        for s in D.STATS:
            for sc in ("1", "12", "123"):
                y = np.array([D.scope_total(fr.T[i], s, sc) if (sc == "1" or fr.bo[i] in (3, 5)) else None for i in idx], dtype=object)
                ok = np.array([v is not None for v in y])
                if sc == "123":
                    ok &= fr.bo[idx] == 5          # the 3-game size from best-of-5s (the BO3 mixture reuses the "12" size)
                comps = self.comps(mus, s, sc, idx, p, p3)
                for role in list(lpf.ROLES) + [None]:
                    m = ok & ((fr.role[idx] == role) if role else True)
                    if m.sum() < 60:
                        continue
                    yy = y[m].astype(float)
                    best, arg = -math.inf, None
                    for g in grid:
                        mx = lpf.Mix(np.column_stack([c[0][m] for c in comps]), np.column_stack([c[1][m] for c in comps]),
                                     np.full((int(m.sum()), len(comps)), g))
                        v = float(mx.logpmf(yy).sum())
                        if v > best:
                            best, arg = v, g
                    sizes[(s, sc, role)] = float(arg)
        return sizes


def win_model_fit(fr, d_end):
    """Game-1 results of targets before d_end, one per (series, team)."""
    seen, idx, y = set(), [], []
    for i in np.where(fr.day < d_end)[0]:
        t = fr.T[i]
        if t.get("win1") is None or (t["match"], t["team"]) in seen:
            continue
        seen.add((t["match"], t["team"]))
        idx.append(i)
        y.append(int(t["win1"]))
    idx = np.array(idx)
    wm = lpf.WinModel().fit(fr.F(idx), np.array(y))
    # decider: did a best-of-3 reach game 3 (each series once), on the same earlier series
    i3 = np.array([i for i in idx if fr.bo[i] == 3 and fr.day[i] >= d_end - P3_WINDOW])
    if len(i3) >= 200:
        wm.fit_p3(wm.p(fr.F(i3)), np.array([int(fr.T[i]["n_series"] >= 3) for i in i3]))
    return wm


# ============================================================================================ scoring
def thresholds_calibration(P, Y):
    """P(X > t) predicted vs realised over (row, threshold) pairs: 5-pt bins, mean |gap| weighted, max |gap| (bins n>=200)."""
    P, Y = np.asarray(P), np.asarray(Y, dtype=float)
    bins = []
    for lo in np.arange(0.0, 1.0, 0.05):
        m = (P >= lo) & (P < lo + 0.05 if lo < 0.95 else P <= 1.0)
        if m.sum():
            bins.append({"bin": f"{lo:.2f}", "n": int(m.sum()), "pred": round(float(P[m].mean()), 4), "obs": round(float(Y[m].mean()), 4)})
    w = np.array([b["n"] for b in bins], dtype=float)
    gaps = np.array([abs(b["pred"] - b["obs"]) for b in bins])
    big = [abs(b["pred"] - b["obs"]) for b in bins if b["n"] >= 200]
    return {"weighted_mean_abs_gap": round(float((w * gaps).sum() / w.sum()), 4), "max_abs_gap_n200": round(max(big), 4) if big else None,
            "brier": round(float(((P - Y) ** 2).mean()), 5), "bins": bins}


def score_block(dist, y, clusters, rng):
    """Per-row log score, PIT, mean, and threshold pairs for one distribution."""
    ls = dist.logpmf(y)
    mu = dist.mean()
    pit = dist.pit(y, rng)
    ths, P, Yb, cl = [], [], [], []
    base = np.floor(mu)
    for off in (-2, -1, 0, 1):
        t = np.maximum(base + 0.5 + off, 0.5)
        po, _ = dist.p_over(t)
        P.append(po)
        Yb.append((y > t).astype(float))
        cl.append(clusters)
    return {"ls": ls, "mu": mu, "pit": pit, "P": np.concatenate(P), "Yb": np.concatenate(Yb), "pcl": np.concatenate(cl)}


def compare(base, cand, y, clusters):
    d = cand["ls"] - base["ls"]
    out = {"n": int(len(y)), "series": int(len(set(clusters))),
           "logscore_base": round(float(base["ls"].mean()), 5), "logscore_cand": round(float(cand["ls"].mean()), 5),
           "dlogscore_cand_minus_base": cluster_ci(d, clusters),
           "mae_base": round(float(np.abs(base["mu"] - y).mean()), 4), "mae_cand": round(float(np.abs(cand["mu"] - y).mean()), 4),
           "dmae": cluster_ci(np.abs(cand["mu"] - y) - np.abs(base["mu"] - y), clusters),
           "bias_base": round(float((y - base["mu"]).mean()), 4), "bias_cand": round(float((y - cand["mu"]).mean()), 4)}
    for k, r in (("base", base), ("cand", cand)):
        h = np.histogram(r["pit"], bins=10, range=(0, 1))[0] / len(y)
        out[f"pit_deciles_{k}"] = [round(float(x), 4) for x in h]
        out[f"pit_maxdev_{k}"] = round(float(np.abs(h - 0.1).max()), 4)
        out[f"threshold_cal_{k}"] = thresholds_calibration(r["P"], r["Yb"])
    db = (cand["P"] - cand["Yb"]) ** 2 - (base["P"] - base["Yb"]) ** 2
    out["dbrier_thresholds"] = cluster_ci(db, cand["pcl"])
    return out


def scope_rows(fr, idx, stat, scope):
    """(index subset, realised totals, group label) for a scope."""
    keep, ys = [], []
    for i in idx:
        if scope != "1" and fr.bo[i] not in (3, 5):
            continue
        v = D.scope_total(fr.T[i], stat, scope)
        if v is None:
            continue
        keep.append(i)
        ys.append(v)
    return np.array(keep, dtype=int), np.array(ys, dtype=float)


def baseline_mix(fr, idx, stat, scope):
    m = __import__("lol.model", fromlist=["R"])
    lam = np.array([fr.T[i]["live"][stat][0] for i in idx])
    p = np.array([fr.T[i]["p_live"] for i in idx])
    bo = fr.bo[idx]
    if scope == "123":
        p3 = np.where(bo == 3, 2 * p * (1 - p), 1.0)
        return lpf.Mix(np.column_stack([p3, 1 - p3]), np.column_stack([3 * lam, 2 * lam]),
                       np.column_stack([np.full(len(idx), m.R[(stat, "123")]), np.full(len(idx), m.R[(stat, "12")])]))
    return lpf.Mix.single(lam * lpf.SCOPE_N[scope], m.R[(stat, scope)])


def walk_forward(fr, cand, refits, end, log):
    """Per refit date: train on earlier days, predict the month. Returns {target index: {stat: {scope: Mix-params}}}."""
    preds = {}
    ds = [D.day_of(r) for r in refits] + [D.day_of(end)]
    for a, b_ in zip(ds[:-1], ds[1:]):
        t0 = time.time()
        wm = win_model_fit(fr, a)
        sizes = cand.fit_sizes(a, wm)
        boosters = cand.train(a)
        idx = np.where((fr.day >= a) & (fr.day < b_))[0]
        if not len(idx):
            continue
        mus = cand.game_means(boosters, idx)
        p = wm.p(fr.F(idx))
        p3 = wm.p3(p)
        for s in D.STATS:
            for sc in SCOPES:
                comps = cand.comps(mus, s, sc, idx, p, p3)
                mx = cand.mix(comps, s, fr.role[idx], sizes)
                for j, i in enumerate(idx):
                    preds.setdefault(int(i), {}).setdefault(s, {})[sc] = (mx.W[j].copy(), mx.M[j].copy(), mx.S[j].copy())
        for i, pp in zip(idx, p):
            preds[int(i)]["p_win"] = float(pp)
        preds.setdefault("_meta", {})[iso(a)] = {"win_coef": [round(float(c), 4) for c in wm.coef],
                                                 "p3_coef": None if wm.p3coef is None else [round(float(c), 4) for c in wm.p3coef],
                                                 "sizes": {f"{k[0]}|{k[1]}|{k[2]}": round(v, 3) for k, v in sizes.items()}}
        log(f"refit {iso(a)}: {len(idx)} targets, {time.time() - t0:.0f}s")
    return preds


def pred_mix(preds, idx, stat, scope):
    W = np.array([preds[int(i)][stat][scope][0] for i in idx])
    M = np.array([preds[int(i)][stat][scope][1] for i in idx])
    S = np.array([preds[int(i)][stat][scope][2] for i in idx])
    return lpf.Mix(W, M, S)


def stage_a(fr, preds, d_from, d_to, log, label):
    rng = np.random.default_rng(11)
    idx_all = np.array([i for i in range(len(fr.T)) if d_from <= fr.day[i] < d_to and int(i) in preds])
    out = {}
    pooled_d, pooled_cl = [], []
    pseudo_acc = {"base": [], "cand": []}
    for s in D.STATS:
        for sc in SCOPES:
            idx, y = scope_rows(fr, idx_all, s, sc)
            if not len(idx):
                continue
            cl = fr.match[idx]
            groups = {"all": np.ones(len(idx), bool)}
            if sc == "123":
                groups = {"best-of-5": fr.bo[idx] == 5, "best-of-3 (A2 mixture)": fr.bo[idx] == 3}
            for g, m in groups.items():
                if not m.sum():
                    continue
                bm = baseline_mix(fr, idx[m], s, sc)
                cm = pred_mix(preds, idx[m], s, sc)
                b = score_block(bm, y[m], cl[m], rng)
                c = score_block(cm, y[m], cl[m], rng)
                res = compare(b, c, y[m], cl[m])
                out[f"{s}|{sc}|{g}"] = res
                pooled_d.append(c["ls"] - b["ls"])
                pooled_cl.append(cl[m])
                if sc in ("1", "12") or g == "best-of-5":
                    res["book_style_lines"] = pseudo_lines(fr, idx[m], y[m], s, sc, bm, cm)
                    if s != "deaths":
                        for k in ("base", "cand"):
                            pseudo_acc[k].append(res["book_style_lines"]["_rows"][k])
                    res["book_style_lines"].pop("_rows")
                log(f"[{label}] {s:7s} {sc:3s} {g:24s} n {res['n']:6d} dLS {res['dlogscore_cand_minus_base']} MAE {res['mae_base']}->{res['mae_cand']} "
                    f"thrgap {res['threshold_cal_base']['weighted_mean_abs_gap']}->{res['threshold_cal_cand']['weighted_mean_abs_gap']} "
                    f"PITdev {res['pit_maxdev_base']}->{res['pit_maxdev_cand']}")
    out["pooled_all_rows"] = {"dlogscore": cluster_ci(np.concatenate(pooled_d), np.concatenate(pooled_cl))}
    pl = {}
    for k in ("base", "cand"):
        rows = {kk: np.concatenate([r[kk] for r in pseudo_acc[k]]) for kk in pseudo_acc[k][0]}
        pl[k] = line_table(rows)
    out["book_style_lines_kills_assists"] = pl
    log(f"[{label}] book-style lines kills+assists: base {json.dumps(pl['base']['summary'])}")
    log(f"[{label}] book-style lines kills+assists: cand {json.dumps(pl['cand']['summary'])}")
    return out


def pseudo_lines(fr, idx, y, stat, scope, bm, cm):
    """A dev-period stand-in for offered lines (no book lines exist before 2026-10-17): the nearest half-integer to the
    player's last-5 per-game average x games (the 'book-style line' of lol/EVALUATION.md A10). Each model's side = the side
    its chance favours; graded = side chance >= 55% (pushes impossible at half lines)."""
    n = lpf.SCOPE_N[scope]
    col = fr.col[{"kills": "klast5", "deaths": "dlast5", "assists": "alast5"}[stat]]
    raw = fr.X[idx, col] * n
    ok = np.isfinite(raw)
    line = np.floor(np.where(ok, raw, 0)) + 0.5
    rows = {}
    for k, mx in (("base", bm), ("cand", cm)):
        po, _ = mx.p_over(line)
        over = po >= 0.5
        p = np.where(over, po, 1 - po)
        hit = np.where(over, y > line, y < line).astype(float)
        rows[k] = {"p": p[ok], "over": over[ok], "hit": hit[ok], "cl": fr.match[idx][ok]}
    return {"n": int(ok.sum()), "base": line_table(rows["base"])["summary"], "cand": line_table(rows["cand"])["summary"], "_rows": rows}


def line_table(r):
    P, O, H, cl = r["p"], r["over"].astype(bool), r["hit"], r["cl"]
    G = P >= 0.55
    out = {"summary": {}}
    for lab, m in (("all", np.ones(len(P), bool)), ("over", O), ("under", ~O), ("graded", G), ("graded_over", G & O),
                   ("graded_under", G & ~O)):
        if m.sum():
            out["summary"][lab] = {"n": int(m.sum()), "stated": round(float(P[m].mean()), 4), "hit": round(float(H[m].mean()), 4),
                                   "gap": round(float(P[m].mean() - H[m].mean()), 4)}
    bins = []
    for lo in np.arange(0.5, 0.95, 0.05):
        for sd, ms in (("over", O), ("under", ~O)):
            m = ms & (P >= lo) & (P < lo + 0.05)
            if m.sum():
                bins.append({"bin": f"{lo:.2f}", "side": sd, "n": int(m.sum()), "stated": round(float(P[m].mean()), 4),
                             "hit": round(float(H[m].mean()), 4)})
    out["bins"] = bins
    return out


# ============================================================================================ stage B
def stage_b(fr, preds, settled, log):
    from . import model
    from .center.forecast import side_offered
    key = {(t["match"], t["pid"]): i for i, t in enumerate(fr.T)}
    rows = []
    for r in settled:
        if r.get("s_status") != "graded" or r.get("s_push") or r.get("s_over_hit") not in (0, 1):
            continue
        i = key.get((r["sid"], r["pid"]))
        sides = json.loads(r["sides"] or "{}") if isinstance(r.get("sides"), str) else (r.get("sides") or {})
        rows.append(dict(r, i=i, sides_d=sides))
    n_all = len(rows)
    res = {"settled_graded_rows": n_all}
    out = {"live": [], "cand": [], "base": []}
    missing = 0
    for r in rows:
        i = r["i"]
        if i is None or i not in preds or r["stat"] not in preds[i] or r["scope"] not in preds[i][r["stat"]]:
            missing += 1
            continue
        po_l, pu_l = float(r["p_over"]), float(r["p_under"])
        W, M, S = preds[i][r["stat"]][r["scope"]]
        mx = lpf.Mix(W[None], M[None], S[None])
        po_c, push_c = mx.p_over(np.array([r["line"]]))
        po_c, push_c = float(po_c[0]), float(push_c[0])
        pu_c = 1 - po_c - push_c
        bm = baseline_mix(fr, np.array([i]), r["stat"], r["scope"])
        po_b, push_b = bm.p_over(np.array([r["line"]]))
        po_b, push_b = float(po_b[0]), float(push_b[0])
        for lab, po, pu in (("live", po_l, pu_l), ("cand", po_c, pu_c), ("base", po_b, 1 - po_b - push_b)):
            over = po >= pu
            q = po / (po + pu)
            p = q if over else 1 - q
            out[lab].append({"p": p, "q": q, "over": over, "hit": r["s_over_hit"] if over else 1 - r["s_over_hit"], "y": r["s_over_hit"],
                             "graded": (po if over else pu) >= 0.55, "sid": r["sid"], "stat": r["stat"], "scope": r["scope"],
                             "offered": bool(side_offered(r["platform"], r["sides_d"], "over" if over else "under")),
                             "odds_type": r.get("odds_type")})
    res["scored_rows"] = len(out["live"])
    res["no_history_target"] = missing
    for lab in ("live", "base", "cand"):
        res[lab] = summarize(out[lab], out["live"] if lab != "live" else None)
    return res


def summarize(S, live=None):
    P = np.array([o["p"] for o in S]); Hh = np.array([o["hit"] for o in S], dtype=float)
    Ov = np.array([o["over"] for o in S]); Q = np.array([o["q"] for o in S]); Y = np.array([o["y"] for o in S], dtype=float)
    cl = np.array([o["sid"] for o in S])
    ll = -(Y * np.log(np.clip(Q, 1e-9, 1)) + (1 - Y) * np.log(np.clip(1 - Q, 1e-9, 1)))

    def gap(m):
        if not m.sum():
            return None
        g = cluster_ci((P - Hh)[m], cl[m])
        return {"n": int(m.sum()), "stated": round(float(P[m].mean()), 4), "hit": round(float(Hh[m].mean()), 4), "gap": g[0], "gap_ci": g[1:]}

    res = {"n": len(S), "series": len(set(cl)), "log_loss": round(float(ll.mean()), 4), "over_share": round(float(Ov.mean()), 4),
           "data_over_rate": round(float(Y.mean()), 4), "all": gap(np.ones(len(S), bool)), "over": gap(Ov), "under": gap(~Ov)}
    for s in ("kills", "assists"):
        st = np.array([o["stat"] == s for o in S])
        res[s] = {"all": gap(st), "over": gap(st & Ov), "under": gap(st & ~Ov)}
    for sc in SCOPES:
        res[f"scope {sc}"] = gap(np.array([o["scope"] == sc for o in S]))
    bins = []
    for lo in np.arange(0.50, 0.95, 0.05):
        for sd, msd in (("over", Ov), ("under", ~Ov)):
            m = msd & (P >= lo) & (P < lo + 0.05)
            if m.sum():
                bins.append({"bin": f"{lo:.2f}-{lo + 0.05:.2f}", "side": sd, "n": int(m.sum()), "stated": round(float(P[m].mean()), 4),
                             "hit": round(float(Hh[m].mean()), 4)})
    res["bins"] = bins
    G = np.array([o["graded"] for o in S]); OF = np.array([o["offered"] for o in S])
    sel = {}
    for lab, m in (("graded", G), ("graded_offered", G & OF), ("graded_over", G & Ov), ("graded_under", G & ~Ov)):
        if m.sum():
            g = cluster_ci(Hh[m], cl[m])
            sel[lab] = {"n": int(m.sum()), "stated": round(float(P[m].mean()), 4), "hit": g[0], "hit_ci": g[1:],
                        "gap": round(float(P[m].mean()) - g[0], 4)}
    res["selection"] = sel
    if live is not None:
        Ql = np.array([o["q"] for o in live])
        lll = -(Y * np.log(np.clip(Ql, 1e-9, 1)) + (1 - Y) * np.log(np.clip(1 - Ql, 1e-9, 1)))
        res["dLL_vs_live"] = cluster_ci(ll - lll, cl)
        Hl = np.array([o["hit"] for o in live], dtype=float)
        Gl = np.array([o["graded"] for o in live])
        # graded hit difference vs live (all-row paired: each model's graded set; unpaired difference by series bootstrap)
        res["graded_hit_diff_vs_live"] = _diff_ci(Hh, G, Hl, Gl, cl)
    return res


def _diff_ci(h1, g1, h0, g0, cl, B=BOOT, seed=3):
    keys, inv = np.unique(cl, return_inverse=True)
    s1 = np.bincount(inv, weights=h1 * g1, minlength=len(keys)); n1 = np.bincount(inv, weights=g1.astype(float), minlength=len(keys))
    s0 = np.bincount(inv, weights=h0 * g0, minlength=len(keys)); n0 = np.bincount(inv, weights=g0.astype(float), minlength=len(keys))
    rng = np.random.default_rng(seed)
    b = rng.integers(0, len(keys), (B, len(keys)))
    est = s1[b].sum(1) / np.maximum(n1[b].sum(1), 1) - s0[b].sum(1) / np.maximum(n0[b].sum(1), 1)
    pt = s1.sum() / max(n1.sum(), 1) - s0.sum() / max(n0.sum(), 1)
    return [round(float(pt), 4), round(float(np.percentile(est, 2.5)), 4), round(float(np.percentile(est, 97.5)), 4)]


def verdict(hold, b):
    why, ok = [], True
    d = hold["pooled_all_rows"]["dlogscore"]
    if not d[1] > 0:
        ok = False
        why.append(f"holdout all-outcome log score gain {d} CI does not exclude 0 on the positive side")
    c, l = b["cand"], b["live"]
    for k in ("all", "over", "under"):
        g = c[k]
        if g is None or abs(g["gap"]) > 0.025:
            ok = False
            why.append(f"line-level {k}: stated - hit {None if g is None else round(g['gap'] * 100, 1)} pts beyond 2.5")
    gl, gc = l["selection"]["graded"]["n"], c["selection"].get("graded", {"n": 0})["n"]
    if gc < 0.9 * gl:
        ok = False
        why.append(f"graded count {gc} < 90% of live {gl}")
    hl_, hc = l["selection"]["graded"]["hit"], c["selection"].get("graded", {"hit": 0})["hit"]
    if hc < hl_:
        ok = False
        why.append(f"graded hit {hc} below live {hl_}")
    return ok, why


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", default="M")
    ap.add_argument("--stage", default="dev", choices=("dev", "final"))
    ap.add_argument("--rounds", type=int, default=lpf.N_ROUNDS)
    ap.add_argument("--drop", default="")
    ap.add_argument("--offset", action="store_true", help="start the booster from the live projection (learn the correction)")
    a = ap.parse_args(argv)
    log = lambda *x: print(*x, file=sys.stderr, flush=True)  # noqa: E731
    b = build(a.db, a.cache, log)
    fr = Frame(b)
    fr.gw = wins_per_game(fr, a.db, a.cache, log)
    cand = Candidate(fr, a.variant, drop=[x for x in a.drop.split(",") if x], rounds=a.rounds, log=log, offset=a.offset)
    report = {"version": lpf.VERSION, "variant": a.variant, "offset_live": a.offset, "rounds": a.rounds, "written_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "data": {"player_games": b["db_rows"], "targets": len(fr.T), "first_day": iso(fr.day.min()), "last_day": iso(fr.day.max()),
                       "features": len(cand.feat)},
              "baseline_reconstruction": b["live_forecasts_check"], "features": cand.feat}
    refits = REFITS_DEV + (REFITS_HOLD if a.stage == "final" else [])
    end = END if a.stage == "final" else REFITS_HOLD[0]
    preds = walk_forward(fr, cand, refits, end, log)
    report["refit_meta"] = preds.pop("_meta")
    report["dev"] = stage_a(fr, preds, D.day_of(REFITS_DEV[0]), D.day_of(REFITS_HOLD[0]), log, "DEV")
    if a.stage == "final":
        report["holdout"] = stage_a(fr, preds, D.day_of(REFITS_HOLD[0]), D.day_of(END), log, "HOLDOUT")
        report["lines"] = stage_b(fr, preds, b["settled"], log)
        ok, why = verdict(report["holdout"], report["lines"])
        report["verdict"] = {"pass": ok, "why": why}
        for lab in ("live", "base", "cand"):
            x = report["lines"][lab]
            log(f"LINES {lab:5s} n {x['n']} LL {x['log_loss']} {x.get('dLL_vs_live')} all {x['all']['stated']}/{x['all']['hit']} "
                f"over {x['over']} under {x['under']} graded {x['selection'].get('graded')} overshare {x['over_share']}")
        log(f"VERDICT {ok} {why}")
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    return report


def wins_per_game(fr, db, cache, log):
    """The player's team result in each per-game row (W variant training only; never a forecast input)."""
    path = os.path.join(cache, "lpf_wins.pkl") if cache else None
    if path and os.path.exists(path):
        with open(path, "rb") as fh:
            w = pickle.load(fh)
    else:
        con = D.connect(db)
        w = {}
        for r in con.execute("SELECT match_key, player_id, game_number, win FROM hist_player_games"):
            if r["win"] is not None:
                w[(r["match_key"], r["player_id"], r["game_number"])] = float(r["win"])
        if path:
            with open(path, "wb") as fh:
                pickle.dump(w, fh)
    return np.array([w.get((fr.T[i]["match"], fr.T[i]["pid"], int(n)), np.nan) for i, n in zip(fr.gi, fr.gn)])


if __name__ == "__main__":
    main()
