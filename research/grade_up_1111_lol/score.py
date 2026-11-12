"""gu-lol-1 scoring (FROZEN.md sections 3 and 5): every projection candidate against lpf-1 at the real offered lines
(forward record), the history check at book-style lines, and the deaths evidence D1. Research only; reads local copies.

  python research/grade_up_1111_lol/score.py <db> <lpf cache dir> <out dir>
"""
import json
import os
import pickle
import sqlite3
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from lol import lpf, lpf_data as D, lpf_eval as E  # noqa: E402

B = 2000
ORDER = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "ALL", "P10", "P11"] + [f"NOISE{k}" for k in range(1, 6)]
NAME = {"base": "lpf-1 (baseline)", "P1": "P1 opp-sim", "P2": "P2 role-alloc", "P3": "P3 form-win", "P4": "P4 tenure-role",
        "P5": "P5 intl-exp", "P6": "P6 stage", "P7": "P7 champ", "P8": "P8 pace-x", "ALL": "P9 ALL (P1-P8)", "P10": "P10 mkt-win",
        "P11": "P11 side-oracle (upper bound)"}
NAME.update({f"NOISE{k}": f"placebo: 5 noise columns (seed {k})" for k in range(1, 6)})


# ------------------------------------------------------------------------------------------------ forward record
def forward_rows(db):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = ("SELECT f.*, s.status AS st, s.actual, s.over_hit FROM forecasts f JOIN (SELECT platform, market_key, line, MAX(id) AS id "
         "FROM forecasts WHERE forecast_at < series_start GROUP BY platform, market_key, line) lf ON lf.id = f.id "
         "JOIN (SELECT unit_key, MAX(version) AS v FROM settlements GROUP BY unit_key) lv ON lv.unit_key = f.platform || '|' || f.market_key || '|' || f.line "
         "JOIN settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v")
    rows, seen = [], {}
    for r in con.execute(q):
        r = dict(r)
        if r["st"] not in ("graded", "push"):
            continue
        r["inp"] = json.loads(r["inputs"] or "{}")
        r["sides_d"] = json.loads(r["sides"] or "{}")
        r["day"] = r["series_start"][:10]
        k = (r["sid"], r["pid"], r["stat"], r["scope"], r["platform"], r["odds_type"], r["line"])
        if k in seen and seen[k]["id"] > r["id"]:
            continue
        seen[k] = r
    rows = sorted(seen.values(), key=lambda r: r["id"])
    return rows


def game_p(ps, bo):
    """game-win chance implied by a series-win chance (independent games)."""
    if bo == 1:
        return ps
    f = (lambda p: p * p * (3 - 2 * p)) if bo == 3 else (lambda p: p ** 3 * (10 - 15 * p + 6 * p * p))
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < ps else (lo, mid)
    return (lo + hi) / 2


def reweight(W, scope, bo, p, p3coef):
    q = 1 - p
    if scope == "1":
        w = [p, q]
    elif scope == "12":
        w = [p * p, 2 * p * q, q * q]
    elif bo == 3:
        wm = lpf.WinModel()
        wm.p3coef = None if p3coef is None else np.array(p3coef)
        p3 = float(wm.p3(np.array([p]))[0])
        sw = p * p + q * q
        w = [(1 - p3) * p * p / sw, (1 - p3) * q * q / sw, p3 * p, p3 * q]
    else:
        w = [p ** 3, 3 * p * p * q, 3 * p * q * q, q ** 3]
    assert len(w) == len(W)
    return np.array(w)


def arm(rows, P, mkt=False):
    """per forward row: (po, pu, mean) from a preds file (None when the target has no prediction)."""
    preds, keys, meta = P["preds"], P["keys"], P["meta"]
    out = []
    for r in rows:
        i = keys.get((r["sid"], r["pid"]))
        if i is None or i not in preds:
            out.append(None)
            continue
        W, M, S = preds[i][r["stat"]][r["scope"]]
        if mkt:
            m = (r["inp"].get("team_win") or {}).get("market") or {}
            if m.get("found") and m.get("p") is not None and 0.01 < float(m["p"]) < 0.99:
                bo = int(r["best_of"] or 1)
                W = reweight(W, r["scope"], bo, game_p(float(m["p"]), bo), meta["2026-11-01" if r["day"] >= "2026-11-01" else "2026-10-01"]["p3_coef"])
        mx = lpf.Mix(np.asarray(W)[None], np.asarray(M)[None], np.asarray(S)[None])
        po, push = mx.p_over(np.array([r["line"]]))
        po, push = float(po[0]), float(push[0])
        out.append((po, 1 - po - push, float(mx.mean()[0])))
    return out


def boot_idx(sids, rng):
    keys, inv = np.unique(np.asarray(sids), return_inverse=True)
    return keys, inv, rng.integers(0, len(keys), (B, len(keys)))


def ratio_ci(num, den, inv, nk, bi):
    """bootstrap CI of sum(num) / sum(den) over resampled series (percent)."""
    a = np.bincount(inv, weights=num, minlength=nk)
    b = np.bincount(inv, weights=den, minlength=nk)
    d = b[bi].sum(1)
    v = 100 * a[bi].sum(1)[d > 0] / d[d > 0]
    return [round(float(np.percentile(v, 2.5)), 1), round(float(np.percentile(v, 97.5)), 1)] if len(v) else [None, None]


def offered_cut(R, side, win, inv, nk, bi):
    """hit on the lines whose chosen side is offered by the app; and of those, the ones not Underdog one-sided (shown graded)."""
    def off(r, sd):
        s = r["sides_d"].get("higher" if sd == 1 else "lower")
        if r["platform"] == "underdog":
            return bool(s) and (s.get("status") in (None, "active"))
        return bool(s and s.get("offered"))
    o = np.array([sd != 0 and off(r, sd) for r, sd in zip(R, side)])
    one = np.array([r["platform"] == "underdog" and len([k for k in ("higher", "lower") if r["sides_d"].get(k)]) == 1 for r in R])
    out = {}
    for lab, m in (("side offered", o), ("side offered, two-sided line (what members see graded)", o & ~one)):
        out[lab] = {"n": int(m.sum()), "hit": round(100 * win[m].mean(), 1) if m.any() else None, "ci": ratio_ci(win * m, m.astype(float), inv, nk, bi),
                    "over_n": int((m & (side == 1)).sum()), "over_hit": round(100 * win[m & (side == 1)].mean(), 1) if (m & (side == 1)).any() else None,
                    "under_hit": round(100 * win[m & (side == -1)].mean(), 1) if (m & (side == -1)).any() else None}
    return out


def summarize(rows, A, base=None, mask=None):
    """rows + arm values -> the owner-rule table row. Pushes are excluded from hit rates (same lines for every arm)."""
    idx = [j for j, r in enumerate(rows) if r["st"] == "graded" and A[j] is not None and (base is None or base[j] is not None)
           and (mask is None or mask[j])]
    R = [rows[j] for j in idx]
    y = np.array([r["over_hit"] for r in R], dtype=float)
    sid = [r["sid"] for r in R]
    rng = np.random.default_rng(7)
    keys, inv, bi = boot_idx(sid, rng)
    nk = len(keys)

    def parts(X):
        po = np.array([X[j][0] for j in idx])
        pu = np.array([X[j][1] for j in idx])
        side = np.where(np.abs(po - pu) < 1e-9, 0, np.where(po > pu, 1, -1))
        win = np.where(side == 1, y, np.where(side == -1, 1 - y, 0.0))
        ps = np.where(side == 1, po, pu)
        return side, win, ps

    side, win, ps = parts(A)
    g, O, U = side != 0, side == 1, side == -1
    res = {"lines": len(R), "series": nk, "graded": int(g.sum()), "letters": int((g & (ps >= 0.55)).sum()),
           "hit": round(100 * win[g].mean(), 1), "hit_ci": ratio_ci(win * g, g.astype(float), inv, nk, bi),
           "over_n": int(O.sum()), "over_hit": round(100 * win[O].mean(), 1) if O.any() else None,
           "over_ci": ratio_ci(win * O, O.astype(float), inv, nk, bi),
           "under_n": int(U.sum()), "under_hit": round(100 * win[U].mean(), 1) if U.any() else None,
           "under_ci": ratio_ci(win * U, U.astype(float), inv, nk, bi),
           "over_share": round(100 * O.sum() / g.sum(), 1), "data_over_rate": round(100 * y.mean(), 1),
           "stated": round(100 * float((ps / (np.array([A[j][0] + A[j][1] for j in idx])))[g].mean()), 1),
           "letter_hit": round(100 * win[g & (ps >= 0.55)].mean(), 1)}
    # added after the freeze (descriptive, not part of the bar): lines where the arm's side can actually be played
    res["offered"] = offered_cut(R, side, win, inv, nk, bi)
    # projection quality: one unit per player-market (latest row)
    pm = {}
    for n, r in enumerate(R):
        pm[(r["sid"], r["pid"], r["stat"], r["scope"])] = n
    u = np.array(sorted(pm.values()))
    err = np.array([A[idx[n]][2] - R[n]["actual"] for n in u])
    res["mae"], res["bias"] = round(float(np.abs(err).mean()), 3), round(float(err.mean()), 3)
    if base is not None:
        bs, bw, bps = parts(base)
        bg = bs != 0
        a = np.bincount(inv, weights=win * g, minlength=nk)
        an = np.bincount(inv, weights=g.astype(float), minlength=nk)
        b = np.bincount(inv, weights=bw * bg, minlength=nk)
        bn = np.bincount(inv, weights=bg.astype(float), minlength=nk)
        d = 100 * (a[bi].sum(1) / an[bi].sum(1) - b[bi].sum(1) / bn[bi].sum(1))
        res["d_hit"] = round(100 * (win[g].mean() - bw[bg].mean()), 2)
        res["d_hit_ci"] = [round(float(np.percentile(d, 2.5)), 1), round(float(np.percentile(d, 97.5)), 1)]
        res["d_sd"] = round(float(d.std()), 2)
        res["flips"] = int(((side != bs) & g & bg).sum())
        res["base_hit"] = round(100 * bw[bg].mean(), 1)
        bO, bU = bs == 1, bs == -1
        res["base_over_hit"], res["base_under_hit"] = round(100 * bw[bO].mean(), 1), round(100 * bw[bU].mean(), 1)
        res["base_over_share"] = round(100 * bO.sum() / bg.sum(), 1)
        res["base_graded"], res["base_letters"] = int(bg.sum()), int((bg & (bps >= 0.55)).sum())
        res["changed_rows_hit"] = None
        ch = (side != bs) & g & bg
        if ch.any():
            res["changed_rows_hit"] = [round(100 * bw[ch].mean(), 1), round(100 * win[ch].mean(), 1)]
        c1 = res["d_hit"] > 0
        c2 = res["over_hit"] >= res["base_over_hit"] and res["under_hit"] >= res["base_under_hit"]
        c3 = res["graded"] >= res["base_graded"] and res["letters"] >= 0.95 * res["base_letters"]
        c4 = abs(res["over_share"] - res["data_over_rate"]) <= abs(res["base_over_share"] - res["data_over_rate"]) + 1e-9
        res["clauses"] = {"1 hit up": bool(c1), "2 Over and Under not lower": bool(c2), "3 count": bool(c3), "4 split": bool(c4)}
        ok = c1 and c2 and c3 and c4
        res["verdict"] = ("PASS" if ok and res["d_hit_ci"][0] > 0 else "NEAR-MISS" if ok and res["d_hit"] >= 1.0 else "FAIL")
        # series needed for the CI of this observed gain to exclude 0 (CI half-width ~ 1.96 sd, sd ~ 1 / sqrt(series))
        if res["d_hit"] > 0:
            res["series_needed"] = int(np.ceil(nk * (1.96 * res["d_sd"] / res["d_hit"]) ** 2))
    return res


# ------------------------------------------------------------------------------------------------ history check
def history(fr, P, base, d0, d1, stats=("kills", "assists")):
    """book-style lines on targets [d0, d1): hit of the candidate vs lpf-1, paired by series."""
    idx_all = np.array([i for i in range(len(fr.T)) if d0 <= fr.day[i] < d1 and i in P["preds"] and i in base["preds"]])
    hc, hb, oc, ob, cl = [], [], [], [], []
    for s in stats:
        col = fr.col[{"kills": "klast5", "deaths": "dlast5", "assists": "alast5"}[s]]
        for sc in E.SCOPES:
            idx, y = E.scope_rows(fr, idx_all, s, sc)
            if sc == "123":
                m = fr.bo[idx] == 5
                idx, y = idx[m], y[m]
            if not len(idx):
                continue
            raw = fr.X[idx, col] * lpf.SCOPE_N[sc]
            ok = np.isfinite(raw)
            idx, y, line = idx[ok], y[ok], np.floor(raw[ok]) + 0.5
            for X, H, O in ((P, hc, oc), (base, hb, ob)):
                po, _ = E.pred_mix(X["preds"], idx, s, sc).p_over(line)
                over = po >= 0.5
                H.append(np.where(over, y > line, y < line).astype(float))
                O.append(over)
            cl.append(fr.match[idx])
    hc, hb, oc, ob, cl = (np.concatenate(v) for v in (hc, hb, oc, ob, cl))
    d = E.cluster_ci(hc - hb, cl, B)
    return {"n": int(len(hc)), "series": int(len(set(cl))), "base_hit": round(100 * hb.mean(), 2), "hit": round(100 * hc.mean(), 2),
            "d_hit": round(100 * d[0], 2), "d_ci": [round(100 * d[1], 2), round(100 * d[2], 2)],
            "over_hit": [round(100 * hb[ob].mean(), 2), round(100 * hc[oc].mean(), 2)],
            "under_hit": [round(100 * hb[~ob].mean(), 2), round(100 * hc[~oc].mean(), 2)],
            "over_share": [round(100 * ob.mean(), 1), round(100 * oc.mean(), 1)]}


def deaths(fr, arms, d0, d1):
    """D1: lol-1 (live deaths) vs lpf-1 deaths (and +ALL) on every historical outcome + book-style lines."""
    rng = np.random.default_rng(11)
    first = next(iter(arms.values()))
    idx_all = np.array([i for i in range(len(fr.T)) if d0 <= fr.day[i] < d1 and all(i in a["preds"] for a in arms.values())])
    acc = {k: {"ls": [], "ae": [], "hit": [], "over": []} for k in ["lol-1"] + list(arms)}
    cl = []
    col = fr.col["dlast5"]
    for sc in E.SCOPES:
        idx, y = E.scope_rows(fr, idx_all, "deaths", sc)
        if sc == "123":
            m = fr.bo[idx] == 5
            idx, y = idx[m], y[m]
        raw = fr.X[idx, col] * lpf.SCOPE_N[sc]
        ok = np.isfinite(raw)
        idx, y, line = idx[ok], y[ok], np.floor(raw[ok]) + 0.5
        cl.append(fr.match[idx])
        for k in acc:
            mx = E.baseline_mix(fr, idx, "deaths", sc) if k == "lol-1" else E.pred_mix(arms[k]["preds"], idx, "deaths", sc)
            po, _ = mx.p_over(line)
            over = po >= 0.5
            acc[k]["ls"].append(mx.logpmf(y))
            acc[k]["ae"].append(np.abs(mx.mean() - y))
            acc[k]["hit"].append(np.where(over, y > line, y < line).astype(float))
            acc[k]["over"].append(over)
    cl = np.concatenate(cl)
    A = {k: {m: np.concatenate(v[m]) for m in v} for k, v in acc.items()}
    out = {"n": int(len(cl)), "series": int(len(set(cl)))}
    for k in A:
        o = A[k]["over"]
        out[k] = {"logscore": round(float(A[k]["ls"].mean()), 4), "mae": round(float(A[k]["ae"].mean()), 3),
                  "hit": round(100 * float(A[k]["hit"].mean()), 2), "over_hit": round(100 * float(A[k]["hit"][o].mean()), 2),
                  "under_hit": round(100 * float(A[k]["hit"][~o].mean()), 2), "over_share": round(100 * float(o.mean()), 1)}
        if k != "lol-1":
            d = E.cluster_ci(A[k]["hit"] - A["lol-1"]["hit"], cl, B)
            out[k]["d_hit_vs_lol1"] = [round(100 * v, 2) for v in d]
            out[k]["d_logscore_vs_lol1"] = E.cluster_ci(A[k]["ls"] - A["lol-1"]["ls"], cl, B)
            out[k]["d_mae_vs_lol1"] = E.cluster_ci(A[k]["ae"] - A["lol-1"]["ae"], cl, B)
    return out


def main():
    db, cache, out = sys.argv[1:4]
    rows = forward_rows(db)
    load = lambda g: pickle.load(open(os.path.join(out, f"preds_{g}.pkl"), "rb"))
    base = load("base")
    A0 = arm(rows, base)
    res = {"rows": len(rows), "graded": sum(r["st"] == "graded" for r in rows), "push": sum(r["st"] == "push" for r in rows),
           "series": len({r["sid"] for r in rows}), "forward": {}, "history": {}, "by": {}}
    # the baseline reproduces the Nov 9 lpf-1 rows?
    try:
        old = pickle.load(open(os.path.join(os.path.dirname(out), "lpf1109", "lpf_rows_nooe.pkl"), "rb"))
        d = [abs(a[0] - old[(r["platform"], r["market_key"], r["line"])][0]) for r, a in zip(rows, A0)
             if a and (r["platform"], r["market_key"], r["line"]) in old]
        res["baseline_vs_nov9_lpf_rows"] = {"n": len(d), "max_abs_dP": round(float(max(d)), 5), "median": round(float(np.median(d)), 6)}
    except Exception as e:  # noqa
        res["baseline_vs_nov9_lpf_rows"] = str(e)
    res["forward"]["base"] = summarize(rows, A0)
    cuts = {"kills": [r["stat"] == "kills" for r in rows], "assists": [r["stat"] == "assists" for r in rows],
            "from 2026-10-26": [r["day"] >= "2026-10-26" for r in rows],
            "shown (not Underdog one-sided)": [not (r["platform"] == "underdog" and len([k for k in ("higher", "lower") if r["sides_d"].get(k)]) == 1) for r in rows],
            "market odds saved": [bool(((r["inp"].get("team_win") or {}).get("market") or {}).get("found")) for r in rows]}
    for c, m in cuts.items():
        res["by"].setdefault(c, {})["base"] = summarize(rows, A0, mask=m)
    b = E.build(db, cache, lambda *a: None)
    fr = E.Frame(b)
    d0, d1 = D.day_of("2026-07-01"), D.day_of("2026-10-01")
    arms = {}
    for g in ORDER:
        if g == "P10":
            A = arm(rows, base, mkt=True)
        else:
            if not os.path.exists(os.path.join(out, f"preds_{g}.pkl")):
                continue
            P = load(g)
            A = arm(rows, P)
            res["history"][g] = history(fr, P, base, d0, d1)
            if g == "ALL":
                arms["lpf-1 + ALL"] = P
        res["forward"][g] = summarize(rows, A, A0)
        for c, m in cuts.items():
            res["by"][c][g] = summarize(rows, A, A0, mask=m)
        f = res["forward"][g]
        print(f"{NAME[g]:30s} n {f['graded']} hit {f['base_hit']} -> {f['hit']} d {f['d_hit']:+.2f} {f['d_hit_ci']} O {f['base_over_hit']}->{f['over_hit']} "
              f"U {f['base_under_hit']}->{f['under_hit']} share {f['base_over_share']}->{f['over_share']} letters {f['base_letters']}->{f['letters']} "
              f"flips {f['flips']} {f['verdict']} off {f['offered']['side offered']['n']}/{f['offered']['side offered']['hit']} | hist {res['history'].get(g, {}).get('d_hit')} {res['history'].get(g, {}).get('d_ci')}", flush=True)
    arms = dict({"lpf-1": base}, **arms)
    res["deaths"] = {"2026-07-01..2026-11-09": deaths(fr, arms, d0, D.day_of("2026-11-10")),
                     "holdout 2026-10-01..2026-11-09": deaths(fr, arms, d1, D.day_of("2026-11-10"))}
    print(json.dumps(res["deaths"], indent=1))
    print(json.dumps(res["forward"]["base"]))
    json.dump(res, open(os.path.join(HERE, "results_projection.json"), "w"), indent=1)
    pickle.dump({"rows": rows, "base": A0}, open(os.path.join(out, "rows_base.pkl"), "wb"))


if __name__ == "__main__":
    main()
