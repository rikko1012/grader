"""pnf-1 walk-forward: NFL projection DISTRIBUTIONS on every historical player-game, then at offered book lines.

    python -m sports.research.proj_nfl_full --history <hist_pnf.db> --export <gradecore_export_nfl.json>
        [--out sports/research/results/proj_nfl_full_<date>.json] [--stage dev|final|all]

Read-only: reads a local history db (proj_nfl_full_data.py) and a saved export; writes one local JSON report.

Baseline = the live sm-1 configuration (model_config_nfl.json: half-lives, features, family, cal-1 frozen) REFIT at
every cutoff on rows known before it, exactly as sports.model.forward.fit does - the repo's own code (featurize,
study.mean_model, study.fit_family_params, study.pmf_for). On the settled book-line props the baseline is the exact
saved live chance (and the committed artifact cc56aeb8 is re-run to show how close the offline reconstruction is).

Protocol (owner hard rules 2026-11-05):
  * chronological walk-forward: development window = monthly refits 2023-09 .. 2026-03 (seasons 2023-2025, train
    from 2022); FINAL untouched window = 2026 season, refit at every Tuesday 12:00 UTC and, for the book-line rows, at
    the forecast day (training rows known - start + 6 h - before the cutoff); features strictly from prior games.
  * all-outcome scores per market on every eligible player-game: log score of the PMF at the realised value, CRPS,
    MAE of the mean, randomised-PIT histogram, P(X > t) calibration at half-integer thresholds around the player's
    own last-6 average (the typical line), Over and Under judged separately; 95% CIs resample whole games.
  * book-line stage: calibration per 5-pt bin, Over / Under separately, graded (C or better = 53.45%) count and hit
    vs live, log loss vs live; CIs resample whole games (match_key).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from sports.model import dists, featurize, model, study
from sports.model import proj_nfl_full as pnf

ART = Path(__file__).resolve().parents[1] / "model" / "artifacts"
LIVE_MK = ("rec", "rec_yds", "rush_att", "rush_yds", "pass_yds")
STEP = {"rec": 1.0, "rec_yds": 10.0, "rush_att": 2.0, "rush_yds": 10.0, "pass_yds": 20.0, "pass_cmp": 2.0}
GRADE_C, GRADE_B, GRADE_A = 0.5345, 0.5559, 0.60
BIN_W = {"rec_yds": 5, "rush_yds": 5, "pass_yds": 10}
B = 1000
DEV = ("2023-09-01", "2026-03-01")
FINAL_START = "2026-09-01"
KNOWN_H = 6


def _ep(s):
    return featurize.epoch([s])[0]


def _iso(e):
    return datetime.fromtimestamp(e, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------------------ data
def load(history):
    con = sqlite3.connect("file:" + str(Path(history)).replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    frames = {}
    for hl in (2, 4, 8):
        frames[hl] = study._nfl_fill(featurize.nfl_frame(con, h_opp=hl, with_lags=False))
    keys = frames[4]["game_id"].astype(str) + "|" + frames[4]["player_id"].astype(str)
    for hl in (2, 8):
        k2 = frames[hl]["game_id"].astype(str) + "|" + frames[hl]["player_id"].astype(str)
        assert np.array_equal(k2, keys), "frames are not row-aligned"
    cf = pnf.augment(frames[4].copy(), con)
    gs = featurize.group_starts(cf["player_id"])
    for mk in pnf.MARKETS:
        cf[f"l6_{pnf.STAT[mk]}"] = featurize.roll_prior(cf.f(pnf.STAT[mk]), gs, 6, 1)
    meta = {"espn_ids": {}, "games": {}}
    for r in con.execute("SELECT gsis_id, espn_id FROM nfl_ids WHERE espn_id IS NOT NULL"):
        try:
            meta["espn_ids"][str(int(float(r[1])))] = r[0]
        except ValueError:
            continue
    meta["games"] = {r[0]: r[1] for r in con.execute("SELECT espn_id, game_id FROM games WHERE league = 'nfl' AND "
                                                     "espn_id IS NOT NULL")}
    try:
        meta["build"] = {r[0]: r[1] for r in con.execute("SELECT k, v FROM pnf_build")}
    except sqlite3.Error:
        meta["build"] = {}
    con.close()
    return frames, cf, meta


def population(df, market):
    """All-outcome evaluation population: the player had >= 3 prior games and a role in the market's volume."""
    pos = np.asarray(df["position"], dtype=object)
    pn = df.f("prior_n")
    y = df.f(pnf.STAT[market])
    with np.errstate(invalid="ignore"):
        if market in ("pass_yds", "pass_cmp"):
            m = (pos == "QB") & (np.nan_to_num(df.f("l6_pass_att")) >= 10)
        elif market in ("rush_att", "rush_yds"):
            m = np.isin(pos, ("RB", "QB", "WR")) & (np.nan_to_num(df.f("l6_carries")) >= 2)
        else:
            m = np.isin(pos, ("WR", "TE", "RB")) & (np.nan_to_num(df.f("l6_targets")) >= 1)
    return m & (pn >= 3) & np.isfinite(y)


def cutoffs(stage):
    out = []
    if stage in ("dev", "all"):
        d = datetime(2023, 9, 1, tzinfo=timezone.utc)
        end = datetime(2026, 3, 1, tzinfo=timezone.utc)
        while d < end:
            nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            if d.month in (9, 10, 11, 12, 1, 2):
                out.append(("dev", d, nxt))
            d = nxt
    if stage in ("final", "all"):
        d = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
        while d.weekday() != 1:
            d += timedelta(days=1)
        while d < datetime(2026, 11, 5, tzinfo=timezone.utc):
            out.append(("final", d, d + timedelta(days=7)))
            d += timedelta(days=7)
    return out


# ------------------------------------------------------------------------------------------------------- baseline
def fit_baseline(frames, known):
    cfg = json.loads((ART / "model_config_nfl.json").read_text(encoding="utf-8"))["markets"]
    fits = {}
    for mk in LIVE_MK:
        c = cfg[mk]
        df = frames[c["half_lives"][0]]
        el = model.eligible(df, mk) & known & np.isfinite(df.f(f"y_{mk}"))
        tr = df[el]
        f = study.Fitted(mk, tuple(c["half_lives"]))
        study.mean_model(f, tr, tr, mk, "nfl", list(c["features"]))
        study.fit_family_params(f, tr, mk, "nfl")
        f.family, f.cal = c["family"], tuple(c["cal"])
        fits[mk] = f
    return fits


def baseline_pmf(frames, f, mk, idx):
    df = frames[f.hl[0]][idx]
    p, mu = study.pmf_for(f, df, mk, "nfl")
    return p


def b1_pmf(cf, mk, idx):
    """Naive reference for markets without a live model (completions): recency-weighted mean, NB(alpha 0.05)."""
    lo, ks = pnf.grid(mk)
    m = np.maximum(np.nan_to_num(cf.f(f"ew_{pnf.STAT[mk]}")[idx], nan=0.5), 0.1) if f"ew_{pnf.STAT[mk]}" in cf else \
        np.maximum(np.nan_to_num(cf.f(f"e2_{pnf.STAT[mk]}")[idx], nan=0.5), 0.1)
    return dists.negbin(m, 0.05, lo, ks)


# --------------------------------------------------------------------------------------------------------- scoring
def row_scores(p, lo, y, thresholds, mk=None):
    ks = np.arange(lo, lo + p.shape[1]).astype(float)
    yy = np.clip(np.rint(y), lo, lo + p.shape[1] - 1)
    idx = (yy - lo).astype(int)
    pm = p[np.arange(len(y)), idx]
    F = np.cumsum(p, axis=1)
    f_hi = F[np.arange(len(y)), idx]
    f_lo = f_hi - pm
    mean = p @ ks
    bw = BIN_W.get(mk, 1)
    if bw > 1:                       # binned log score: mass of the bw-yard bin holding y (robust to spiky PMFs)
        b = np.floor((ks - lo) / bw).astype(int)
        nb = b.max() + 1
        M = np.zeros((p.shape[1], nb))
        M[np.arange(p.shape[1]), b] = 1.0
        pb = (p @ M)[np.arange(len(y)), b[idx]]
        lsb = np.log(np.maximum(pb, 1e-9))
    else:
        lsb = np.log(np.maximum(pm, 1e-9))
    po = np.stack([(p * (ks[None, :] > t[:, None])).sum(axis=1) for t in thresholds.T], axis=1)
    return {"ls": np.log(np.maximum(pm, 1e-9)), "lsb": lsb, "crps": dists.crps(p, lo, y), "ae": np.abs(mean - y), "mean": mean,
            "f_lo": f_lo, "f_hi": f_hi, "p_over": po}


def thresholds_for(cf, mk, idx):
    """Five half-integer thresholds around the player's own last-6 average (a model-free typical line)."""
    l6 = np.nan_to_num(cf.f(f"l6_{pnf.STAT[mk]}")[idx])
    c = np.floor(l6) + 0.5
    floor = -0.5 if mk in ("rec_yds", "rush_yds", "pass_yds") else 0.5
    return np.stack([np.maximum(c + k * STEP[mk], floor) for k in (-2, -1, 0, 1, 2)], axis=1)


def boot(vals, groups, reps=B, seed=11, stat=None):
    vals = np.asarray(vals, float)
    keys, inv = np.unique(np.asarray(groups), return_inverse=True)
    if vals.ndim == 1:
        vals = vals[:, None]
    sums = np.zeros((len(keys), vals.shape[1]))
    np.add.at(sums, inv, vals)
    cnt = np.bincount(inv, minlength=len(keys)).astype(float)
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(reps):
        w = np.bincount(rng.integers(0, len(keys), len(keys)), minlength=len(keys)).astype(float)
        s = (sums * w[:, None]).sum(axis=0)
        c = (cnt * w).sum()
        out.append(s / max(c, 1) if stat is None else stat(s, c))
    out = np.sort(np.array(out), axis=0)
    return out[int(0.025 * reps)], out[int(0.975 * reps) - 1]


def _r(x, d=4):
    return None if x is None else round(float(x), d)


def threshold_cal(po, y, thr, games):
    """Chosen side per (row, threshold): Over when P(X > t) >= 0.5. Stated vs hit overall / Over / Under, 5-pt bins,
    graded (C or better) count + hit. Pushes cannot occur at half-integer thresholds."""
    yb = (y[:, None] > thr).astype(float)
    side = (po >= 0.5).astype(float)
    pc = np.where(side == 1, po, 1 - po)
    hit = (side == yb).astype(float)
    G = np.repeat(np.asarray(games)[:, None], thr.shape[1], axis=1).ravel()
    pc, hit, side, yb = pc.ravel(), hit.ravel(), side.ravel(), yb.ravel()
    out = {"n": int(len(pc)), "over_share": _r(side.mean()), "over_rate": _r(yb.mean())}
    for name, m in (("all", np.ones(len(pc), bool)), ("over", side == 1), ("under", side == 0)):
        if m.sum():
            lo, hi = boot(pc[m] - hit[m], G[m])
            out[name] = {"n": int(m.sum()), "stated": _r(pc[m].mean()), "hit": _r(hit[m].mean()),
                         "gap": _r((pc[m] - hit[m]).mean()), "gap_ci": [_r(lo[0]), _r(hi[0])]}
    g = pc >= GRADE_C
    out["graded"] = {"n": int(g.sum()), "stated": _r(pc[g].mean()) if g.any() else None,
                     "hit": _r(hit[g].mean()) if g.any() else None,
                     "over": [int((g & (side == 1)).sum()), _r(pc[g & (side == 1)].mean()) if (g & (side == 1)).any()
                              else None, _r(hit[g & (side == 1)].mean()) if (g & (side == 1)).any() else None],
                     "under": [int((g & (side == 0)).sum()), _r(pc[g & (side == 0)].mean()) if (g & (side == 0)).any()
                               else None, _r(hit[g & (side == 0)].mean()) if (g & (side == 0)).any() else None]}
    bins = []
    for lo_ in range(50, 100, 5):
        m = (pc * 100 >= lo_) & (pc * 100 < lo_ + 5) if lo_ < 95 else pc * 100 >= lo_
        if m.sum():
            bins.append({"bin": f"{lo_}-{lo_ + 5}", "n": int(m.sum()), "stated": _r(pc[m].mean()),
                         "hit": _r(hit[m].mean()), "over": [int((m & (side == 1)).sum()),
                                                            _r(hit[m & (side == 1)].mean()) if (m & (side == 1)).any()
                                                            else None],
                         "under": [int((m & (side == 0)).sum()), _r(hit[m & (side == 0)].mean())
                                   if (m & (side == 0)).any() else None]})
    out["bins"] = bins
    return out


def pit_hist(f_lo, f_hi, bins=10):
    """Expected randomised-PIT histogram (exact: mass of U(F(y-), F(y)) in each decile)."""
    edges = np.linspace(0, 1, bins + 1)
    w = np.maximum(f_hi - f_lo, 1e-12)
    h = [float(np.mean(np.clip(np.minimum(f_hi, edges[i + 1]) - np.maximum(f_lo, edges[i]), 0, None) / w))
         for i in range(bins)]
    return [round(x, 4) for x in h]


def summarize(rows, label, mk=""):
    """rows: dict of arrays for one market x window (both arms)."""
    g = rows["game"]
    out = {"label": label, "rows": int(len(g)), "games": int(len(np.unique(g))),
           "players": int(len(np.unique(rows["player"])))}
    d_ls = rows["cand_ls"] - rows["base_ls"]
    lo, hi = boot(d_ls, g)
    out["log_score"] = {"base": _r(rows["base_ls"].mean()), "cand": _r(rows["cand_ls"].mean()),
                        "diff": _r(d_ls.mean()), "diff_ci": [_r(lo[0]), _r(hi[0])]}
    d_lb = rows["cand_lsb"] - rows["base_lsb"]
    lo, hi = boot(d_lb, g)
    out["log_score_binned"] = {"base": _r(rows["base_lsb"].mean()), "cand": _r(rows["cand_lsb"].mean()),
                               "diff": _r(d_lb.mean()), "diff_ci": [_r(lo[0]), _r(hi[0])],
                               "bin_width": BIN_W.get(mk, 1)}
    for k in ("crps", "ae"):
        d = rows[f"cand_{k}"] - rows[f"base_{k}"]
        lo, hi = boot(d, g)
        out[k] = {"base": _r(rows[f"base_{k}"].mean()), "cand": _r(rows[f"cand_{k}"].mean()), "diff": _r(d.mean()),
                  "diff_ci": [_r(lo[0]), _r(hi[0])]}
    out["bias"] = {"base": _r((rows["base_mean"] - rows["y"]).mean()), "cand": _r((rows["cand_mean"] - rows["y"]).mean())}
    out["pit"] = {"base": pit_hist(rows["base_flo"], rows["base_fhi"]), "cand": pit_hist(rows["cand_flo"],
                                                                                         rows["cand_fhi"])}
    out["thresholds"] = {"base": threshold_cal(rows["base_po"], rows["y"], rows["thr"], g),
                         "cand": threshold_cal(rows["cand_po"], rows["y"], rows["thr"], g)}
    return out


# ------------------------------------------------------------------------------------------------- walk-forward
def walk(frames, cf, stage, log=print, max_windows=None):
    ts = cf.f("ts")
    res = {mk: [] for mk in pnf.MARKETS}
    pops = {mk: population(cf, mk) for mk in pnf.MARKETS}
    windows = []
    for tag, cut, nxt in cutoffs(stage)[:max_windows]:
        c0, c1 = cut.timestamp(), nxt.timestamp()
        test_any = any(((ts >= c0) & (ts < c1) & pops[mk]).any() for mk in pnf.MARKETS)
        if not test_any:
            continue
        t0 = time.time()
        known = ts + KNOWN_H * 3600 <= c0
        base = fit_baseline(frames, known)
        cand = pnf.PNF().fit(cf[known])
        wrec = {"window": tag, "cutoff": _iso(c0), "until": _iso(c1), "train_rows": int(known.sum()), "test": {}}
        for mk in pnf.MARKETS:
            idx = np.flatnonzero(pops[mk] & (ts >= c0) & (ts < c1))
            if not len(idx):
                continue
            y = cf.f(pnf.STAT[mk])[idx]
            thr = thresholds_for(cf, mk, idx)
            lo, _ = pnf.grid(mk)
            pb = baseline_pmf(frames, base[mk], mk, idx) if mk in base else b1_pmf(cf, mk, idx)
            pc, _, _ = cand.pmf(cf[idx], mk)
            sb, sc = row_scores(pb, lo, y, thr, mk), row_scores(pc, lo, y, thr, mk)
            res[mk].append({"window": np.array([tag] * len(idx), dtype=object), "idx": idx, "y": y, "thr": thr,
                            **{f"base_{k}": v for k, v in sb.items()}, **{f"cand_{k}": v for k, v in sc.items()}})
            wrec["test"][mk] = [int(len(idx)), _r((sc["ls"] - sb["ls"]).mean())]
        windows.append(wrec)
        log(f"{tag} {_iso(c0)[:10]} train={known.sum()} {wrec['test']} {time.time() - t0:.1f}s")
    return res, windows


def _cat(parts, cf):
    keys = parts[0].keys()
    out = {k: np.concatenate([p[k] for p in parts]) for k in keys}
    out["game"] = np.asarray(cf["game_id"])[out["idx"]]
    out["player"] = np.asarray(cf["player_id"])[out["idx"]]
    out["base_flo"], out["base_fhi"] = out["base_f_lo"], out["base_f_hi"]
    out["cand_flo"], out["cand_fhi"] = out["cand_f_lo"], out["cand_f_hi"]
    out["base_po"], out["cand_po"] = out["base_p_over"], out["cand_p_over"]
    return out


def _subset(d, m):
    return {k: (v[m] if isinstance(v, np.ndarray) and len(v) == len(m) else v) for k, v in d.items()}


def all_outcome_report(res, cf):
    rep = {}
    for mk, parts in res.items():
        if not parts:
            continue
        d = _cat(parts, cf)
        rep[mk] = {}
        for w in ("dev", "final"):
            m = d["window"] == w
            if m.sum():
                rep[mk][w] = summarize(_subset(d, m), f"{mk} {w}", mk)
                if mk in LIVE_MK:
                    el = model.eligible(cf[d["idx"][m]], mk)
                    if el.sum() > 50:
                        rep[mk][w + "_live_eligible"] = summarize(_subset(_subset(d, m), el), f"{mk} {w} live-eligible", mk)
                pos = np.asarray(cf["position"], dtype=object)[d["idx"][m]]
                rep[mk][w + "_by_pos"] = {}
                for p in np.unique(pos):
                    mm = pos == p
                    if mm.sum() > 100:
                        dd = _subset(_subset(d, m), mm)
                        dl = dd["cand_ls"] - dd["base_ls"]
                        lo, hi = boot(dl, dd["game"], reps=400)
                        rep[mk][w + "_by_pos"][str(p)] = {"rows": int(mm.sum()), "ls_diff": _r(dl.mean()),
                                                         "ci": [_r(lo[0]), _r(hi[0])],
                                                         "mae": [_r(dd["base_ae"].mean()), _r(dd["cand_ae"].mean())]}
    # pooled over the live markets (each market's mean log-score difference, games resampled jointly)
    for w in ("dev", "final"):
        ds, db, gs = [], [], []
        for mk in LIVE_MK:
            if not res.get(mk):
                continue
            d = _cat(res[mk], cf)
            m = d["window"] == w
            ds.append(d["cand_ls"][m] - d["base_ls"][m])
            db.append(d["cand_lsb"][m] - d["base_lsb"][m])
            gs.append(d["game"][m])
        if ds and sum(len(x) for x in ds):
            x, xb, g = np.concatenate(ds), np.concatenate(db), np.concatenate(gs)
            lo, hi = boot(np.column_stack([x, xb]), g)
            rep.setdefault("pooled_live_markets", {})[w] = {
                "rows": int(len(x)), "games": int(len(np.unique(g))), "ls_diff": _r(x.mean()),
                "ci": [_r(lo[0]), _r(hi[0])], "ls_binned_diff": _r(xb.mean()), "binned_ci": [_r(lo[1]), _r(hi[1])]}
    return rep


# --------------------------------------------------------------------------------------------- book-line stage
def book_rows(export_path, cf, meta):
    exp = json.loads(Path(export_path).read_text(encoding="utf-8"))
    pos = {(g, p): i for i, (g, p) in enumerate(zip(cf["game_id"], cf["player_id"]))}
    rows, miss = [], {}
    for r in exp["rows"]:
        if r["status"] not in ("win", "loss", "push"):
            continue
        gid = meta["games"].get(r["match_key"].split(":")[-1])
        pid = meta["espn_ids"].get(r["player_id"].split(":")[-1], "espn:" + r["player_id"].split(":")[-1])
        i = pos.get((gid, pid))
        if i is None:
            miss["no_history_row"] = miss.get("no_history_row", 0) + 1
            continue
        rr = dict(r)
        rr["i"] = i
        po = r["p"] if r["side"] == "over" else 1 - r["p"]
        rr["live_po"] = float(po)
        rr["one_sided_over"] = r["side"] == "over" and r["p"] < 0.5
        rows.append(rr)
    return exp, rows, miss


def book_stage(frames, cf, meta, export_path, log=print):
    exp, rows, miss = book_rows(export_path, cf, meta)
    ts = cf.f("ts")
    art = json.loads((ART / "sm1_nfl.json").read_text(encoding="utf-8"))
    # refit per forecast day: training rows known before 00:00 UTC of the forecast day
    days = sorted({r["forecast_at"][:10] for r in rows})
    fits = {}
    for d in days:
        c0 = _ep(d + "T00:00:00Z")
        known = ts + KNOWN_H * 3600 <= c0
        t0 = time.time()
        fits[d] = pnf.PNF().fit(cf[known])
        log(f"book refit {d} train={int(known.sum())} {time.time() - t0:.1f}s")
    for r in rows:
        r["cand_po"] = None
    for d in days:
        for mk in LIVE_MK:
            sel = [r for r in rows if r["forecast_at"][:10] == d and r["market"] == mk]
            if not sel:
                continue
            idx = np.array([r["i"] for r in sel])
            p, lo, mean = fits[d].pmf(cf[idx], mk)
            f = study.from_config(art["markets"][mk])
            pa, mua = study.pmf_for(f, frames[f.hl[0]][idx], mk, "nfl")
            for j, r in enumerate(sel):
                o, u, pu = dists.line_probs(p[j], lo, r["line"])
                r["cand_po"] = o / max(o + u, 1e-12) if pu > 0 else o
                r["cand_mean"] = float(mean[j])
                r["cand_push"] = pu
                ao, au, apu = dists.line_probs(pa[j], dists.grid(mk)[0], r["line"])
                r["recon_side_p"] = ao if r["side"] == "over" else au
                r["recon_mean"] = float(mua[j])
    # previous game of the player known at forecast time? (feature freshness check)
    stale = 0
    for r in rows:
        i = r["i"]
        if i > 0 and cf["player_id"][i - 1] == cf["player_id"][i]:
            if ts[i - 1] + KNOWN_H * 3600 > _ep(r["forecast_at"]):
                stale += 1
    return exp, rows, miss, stale


def _ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def side_report(p_over, rows, y, games):
    one = np.array([r["one_sided_over"] for r in rows])
    side = np.where(one, 1, (p_over >= 0.5).astype(int))
    pc = np.where(side == 1, p_over, 1 - p_over)
    hit = (side == y).astype(float)
    out = {"n": int(len(y)), "over_share": _r(side.mean()), "over_rate": _r(y.mean())}
    for name, m in (("all", np.ones(len(y), bool)), ("over", side == 1), ("under", side == 0)):
        if m.sum() == 0:
            out[name] = {"n": 0}
            continue
        lo, hi = boot(pc[m] - hit[m], np.asarray(games)[m], reps=2000)
        out[name] = {"n": int(m.sum()), "stated": _r(pc[m].mean()), "hit": _r(hit[m].mean()),
                     "gap": _r((pc[m] - hit[m]).mean()), "gap_ci": [_r(lo[0]), _r(hi[0])]}
    bins = []
    for lo_ in range(0, 100, 5):
        m = (pc * 100 >= lo_) & (pc * 100 < lo_ + 5) if lo_ < 95 else (pc * 100 >= lo_)
        if m.sum():
            bins.append({"bin": f"{lo_}-{lo_ + 5}", "n": int(m.sum()), "stated": _r(pc[m].mean()),
                         "hit": _r(hit[m].mean()), "over_n": int((m & (side == 1)).sum()),
                         "over_hit": _r(hit[m & (side == 1)].mean()) if (m & (side == 1)).any() else None,
                         "under_n": int((m & (side == 0)).sum()),
                         "under_hit": _r(hit[m & (side == 0)].mean()) if (m & (side == 0)).any() else None})
    out["bins"] = bins
    g = pc >= GRADE_C
    out["graded"] = {"n": int(g.sum()), "stated": _r(pc[g].mean()) if g.any() else None,
                     "hit": _r(hit[g].mean()) if g.any() else None,
                     "over_n": int((g & (side == 1)).sum()), "under_n": int((g & (side == 0)).sum()),
                     "over_stated_hit": [_r(pc[g & (side == 1)].mean()), _r(hit[g & (side == 1)].mean())]
                     if (g & (side == 1)).any() else None,
                     "under_stated_hit": [_r(pc[g & (side == 0)].mean()), _r(hit[g & (side == 0)].mean())]
                     if (g & (side == 0)).any() else None,
                     "letters": {k: int(v) for k, v in zip(("A", "B", "C"), (
                         (pc >= GRADE_A).sum(), ((pc >= GRADE_B) & (pc < GRADE_A)).sum(),
                         ((pc >= GRADE_C) & (pc < GRADE_B)).sum()))}}
    if g.any():
        lo, hi = boot(hit[g], np.asarray(games)[g], reps=2000)
        out["graded"]["hit_ci"] = [_r(lo[0]), _r(hi[0])]
    return out, side, pc, hit, g


def graded_hit_diff(rows, p_live, p_cand, y, games, reps=2000, seed=7):
    arms = {}
    for a, p in (("live", p_live), ("cand", p_cand)):
        _, side, pc, hit, g = side_report(p, rows, y, games)
        arms[a] = (g.astype(float), (hit * g))
    keys, inv = np.unique(np.asarray(games), return_inverse=True)
    S = {a: (np.bincount(inv, weights=v[0], minlength=len(keys)), np.bincount(inv, weights=v[1], minlength=len(keys)))
         for a, v in arms.items()}

    def rate(a, w):
        return (S[a][1] * w).sum() / max((S[a][0] * w).sum(), 1)
    one = np.ones(len(keys))
    point = rate("cand", one) - rate("live", one)
    rng = np.random.default_rng(seed)
    sims = []
    for _ in range(reps):
        w = np.bincount(rng.integers(0, len(keys), len(keys)), minlength=len(keys)).astype(float)
        sims.append(rate("cand", w) - rate("live", w))
    sims = np.sort(sims)
    return {"diff": _r(point), "ci": [_r(sims[int(0.025 * reps)]), _r(sims[int(0.975 * reps) - 1])]}


def book_report(rows):
    dec = [r for r in rows if r["status"] in ("win", "loss") and r.get("cand_po") is not None]
    y = np.array([1.0 if (r["status"] == "win") == (r["side"] == "over") else 0.0 for r in dec])
    games = [r["match_key"] for r in dec]
    pl = np.array([r["live_po"] for r in dec])
    pcand = np.clip(np.array([r["cand_po"] for r in dec]), 1e-4, 1 - 1e-4)
    d_ll = _ll(pcand, y) - _ll(pl, y)
    lo, hi = boot(d_ll, games, reps=2000)
    live, *_ = side_report(pl, dec, y, games)
    cand, *_ = side_report(pcand, dec, y, games)
    rep = {"n": len(dec), "games": len(set(games)), "players": len({r["player_id"] for r in dec}),
           "logloss": {"live": _r(_ll(pl, y).mean()), "cand": _r(_ll(pcand, y).mean()), "diff": _r(d_ll.mean()),
                       "diff_ci": [_r(lo[0]), _r(hi[0])]},
           "live": live, "cand": cand, "graded_hit_diff": graded_hit_diff(dec, pl, pcand, y, games), "by_market": {}}
    for mk in LIVE_MK:
        m = np.array([r["market"] == mk for r in dec])
        if m.sum():
            sub = [dec[i] for i in np.flatnonzero(m)]
            gm = [games[i] for i in np.flatnonzero(m)]
            lo, hi = boot(d_ll[m], gm, reps=1000)
            lv, *_ = side_report(pl[m], sub, y[m], gm)
            cd, *_ = side_report(pcand[m], sub, y[m], gm)
            rep["by_market"][mk] = {"n": int(m.sum()), "ll_diff": _r(d_ll[m].mean()), "ll_ci": [_r(lo[0]), _r(hi[0])],
                                    "live": {k: lv[k] for k in ("all", "over", "under", "graded")},
                                    "cand": {k: cd[k] for k in ("all", "over", "under", "graded")}}
    recon = np.array([abs(r["recon_side_p"] - r["p"]) for r in rows if r.get("recon_side_p") is not None])
    rep["reconstruction"] = {"rows": int(len(recon)), "median_abs_diff": _r(np.median(recon)),
                             "mean_abs_diff": _r(recon.mean()), "share_within_0.01": _r((recon < 0.01).mean()),
                             "share_within_0.03": _r((recon < 0.03).mean())}
    rep["mean_vs_line"] = {"cand_mean_minus_line": _r(np.mean([r["cand_mean"] - r["line"] for r in dec])),
                           "live_recon_mean_minus_line": _r(np.mean([r["recon_mean"] - r["line"] for r in dec]))}
    return rep


def gate(all_rep, book):
    c, lv = book["cand"], book["live"]
    pooled = all_rep.get("pooled_live_markets", {})
    checks = {
        "all_outcome_logscore_better_ci_excl_0_dev": pooled.get("dev", {}).get("ci", [0])[0] > 0,
        "all_outcome_logscore_better_ci_excl_0_final": pooled.get("final", {}).get("ci", [0])[0] > 0,
        "all_outcome_binned_logscore_better_ci_excl_0_final": pooled.get("final", {}).get("binned_ci", [0])[0] > 0,
        "line_calib_all_2.5": abs(c["all"]["gap"]) <= 0.025,
        "line_calib_over_2.5": c["over"].get("n", 0) > 0 and abs(c["over"]["gap"]) <= 0.025,
        "line_calib_under_2.5": c["under"].get("n", 0) > 0 and abs(c["under"]["gap"]) <= 0.025,
        "graded_count_90pct_of_live": c["graded"]["n"] >= 0.9 * lv["graded"]["n"],
        "graded_hit_not_worse": (c["graded"]["hit"] or 0) >= (lv["graded"]["hit"] or 0),
    }
    checks["pass"] = all(checks.values())
    return checks


def run(history, export_path, stage="all", log=print):
    t0 = time.time()
    frames, cf, meta = load(history)
    log(f"frames {len(cf)} rows in {time.time() - t0:.1f}s")
    res, windows = walk(frames, cf, stage, log)
    rep = {"version": pnf.VERSION, "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "history": {"rows": int(len(cf)), "first": _iso(cf.f("ts").min()), "last": _iso(cf.f("ts").max()),
                       "build": meta.get("build")},
           "windows": windows, "all_outcomes": all_outcome_report(res, cf)}
    if stage in ("final", "all"):
        exp, rows, miss, stale = book_stage(frames, cf, meta, export_path, log)
        rep["book_lines"] = book_report(rows)
        rep["book_lines"]["export"] = {"schema": exp.get("schema"), "built_at": exp.get("built_at"),
                                       "rows": exp.get("rows") if isinstance(exp.get("rows"), int) else len(exp["rows"]),
                                       "matched": len(rows), "unmatched": miss,
                                       "prev_game_not_known_at_forecast": stale}
        rep["gate"] = gate(rep["all_outcomes"], rep["book_lines"])
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--history", required=True)
    ap.add_argument("--export", required=True)
    ap.add_argument("--stage", default="all", choices=("dev", "final", "all"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    rep = run(a.history, a.export, a.stage)
    txt = json.dumps(rep, indent=1, default=float)
    if a.out:
        Path(a.out).write_text(txt, encoding="utf-8")
    ao = rep["all_outcomes"]
    for mk in pnf.MARKETS:
        for w in ("dev", "final"):
            s = ao.get(mk, {}).get(w)
            if s:
                t = s["thresholds"]
                print(f"{mk:9s} {w:5s} n={s['rows']:6d} LS {s['log_score']['base']:.4f}->{s['log_score']['cand']:.4f} "
                      f"d {s['log_score']['diff']:+.4f} {s['log_score']['diff_ci']} MAE {s['ae']['base']:.3f}->"
                      f"{s['ae']['cand']:.3f} | thr base O {t['base']['over']['gap']:+.3f} U {t['base']['under']['gap']:+.3f}"
                      f" cand O {t['cand']['over']['gap']:+.3f} U {t['cand']['under']['gap']:+.3f}")
    print("pooled", ao.get("pooled_live_markets"))
    if "book_lines" in rep:
        b = rep["book_lines"]
        for arm in ("live", "cand"):
            x = b[arm]
            print(f"BOOK {arm}: all {x['all']['stated']}/{x['all']['hit']} gap {x['all']['gap']} O {x['over'].get('gap')}"
                  f" U {x['under'].get('gap')} graded {x['graded']['n']} hit {x['graded']['hit']} overshare "
                  f"{x['over_share']}")
        print("BOOK logloss", b["logloss"], "graded hit diff", b["graded_hit_diff"], "recon", b["reconstruction"])
        print("GATE", rep["gate"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
