"""tw-1: walk-forward (rolling-origin) fine-tuning from past games - REGISTER.md amendment A7.

Worker / workstation only (numpy). For every origin t (NFL game-week, NBA game-day) every fitted parameter is refit on
the rows known before t and the origin's games are forecast; calibration layers read only out-of-sample forecasts of
earlier origins. Honest out-of-sample evidence for the PROCEDURE; weaker than a never-seen period (A7 honesty note).

Arms (A7 3): live (sm-1 frozen config refit at t, cal-1), rc (recency-weighted recalibration of a distribution:
mean factor c = sum w y / sum w E[X], spread s by weighted CRPS), v2 (sm-nfl-2 refit + rc), sn (continuous snap share
opportunity + rc), dr (live x level factor of the previous k game-weeks).

  python -m sports.model.tune run <history.db> <sport> [--markets a,b]   walk-forward + decision -> results JSON
  python -m sports.model.tune freeze <sport>                               tw1_<sport>.json + MANIFEST_TW1.json
  python -m sports.model.tune bookline [--learning learning.db --inputs forecasts.db]   bl-1 + book-line criteria
  python -m sports.model.tune status

Forward use (A7 8): load(sport) returns the verified artifact (or None -> sm-1 unchanged); layer(art, market, pmf, lo)
applies a switched market's frozen layer to sm-1's PMF. Off switch SPORTS_TUNE_TW1=0.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from pathlib import Path

import numpy as np

from . import dists, evaluate, grade_policy, model, study

VERSION = "tw-1"
ART_DIR = Path(__file__).resolve().parent / "artifacts"
MANIFEST = ART_DIR / "MANIFEST_TW1.json"
RESULTS = study.RESULTS
NBA_MK = ("points", "rebounds", "assists", "threes")
NFL_MK = ("pass_yds", "rush_yds", "rec_yds", "rush_att", "rec")
ARMS = {"pass_yds": ("rc", "v2", "dr"), "rush_yds": ("rc", "sn"), "rec_yds": ("rc", "v2"),
        "rush_att": ("rc", "sn", "dr"), "rec": ("rc", "v2"),
        "points": ("rc",), "rebounds": ("rc",), "assists": ("rc",), "threes": ("rc",)}
INF = float("inf")
H_GRID = {"nfl": (14.0, 28.0, 56.0, 112.0, INF), "nba": (7.0, 14.0, 28.0, 56.0, INF)}   # days (NFL 2/4/8/16 weeks)
K_GRID = (2, 3, 4, 6)
MIN_RC, MIN_DR = 200, 100
KNOWN_LAG_H = {"nba": 4.0, "nfl": 6.0}
SPAN = {"nba": (30, 60)}                     # warm-up / scored game-days
SEL_SHARE = 0.25
LABEL = "walk-forward (procedure out-of-sample; windows previously inspected) - REGISTER.md A7"
_ON = {"1", "true", "yes", "on"}


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=float).encode("utf-8")).hexdigest()


def enabled():
    return os.environ.get("SPORTS_TUNE_TW1", "1").strip().lower() in _ON


def _ts(s):
    return np.array([np.datetime64(str(x).replace("Z", "")) for x in s], dtype="datetime64[s]")


# ================================================================================================== layers (pure)
def fast_transform(pmf, lo, ks, mu, c, s):
    """dists.transform with bincount (same mass split; float summation order may differ in the last bit)."""
    if c == 1.0 and s == 1.0:
        return pmf
    n, K = pmf.shape
    new = c * mu[:, None] + s * (ks[None, :] - mu[:, None])
    pos = np.clip(new - lo, 0, K - 1)
    f = np.floor(pos).astype(int)
    w = pos - f
    c2 = np.minimum(f + 1, K - 1)
    base = (np.arange(n) * K)[:, None]
    out = np.bincount((base + f).ravel(), (pmf * (1 - w)).ravel(), n * K)
    out += np.bincount((base + c2).ravel(), (pmf * w).ravel(), n * K)
    return out.reshape(n, K)


def _crps_rows(pmf, lo, y):
    return dists.crps(pmf, lo, y)


def fit_rc(pmf, lo, ks, y, w):
    """rc rule (A7 3): c = sum w y / sum w E[X] (clipped), s = argmin weighted CRPS on the cal-1 S grid with c fixed."""
    m = dists.mean_of(pmf, ks)
    c = float(np.clip(np.sum(w * y) / max(np.sum(w * m), 1e-9), study.C_GRID.min(), study.C_GRID.max()))
    ws = w / w.sum()
    best = None
    for s in study.S_GRID:
        v = float(np.sum(ws * _crps_rows(fast_transform(pmf, lo, ks, m, c, float(s)), lo, y)))
        if best is None or v < best[1] - 1e-12:
            best = (float(s), v)
    return c, best[0]


def fit_dr(pmf, ks, y):
    m = dists.mean_of(pmf, ks)
    return float(np.clip(np.sum(y) / max(np.sum(m), 1e-9), study.C_GRID.min(), study.C_GRID.max()))


def weights(age_days, h):
    return np.ones(len(age_days)) if not math.isfinite(h) else 0.5 ** (np.asarray(age_days, float) / h)


# ================================================================================================== sn model (A7 3)
SN_COLS = ("ew_offense_pct", "v2_lag_snap", "v2_ew_partial")
SN_POS = ("RB", "WR", "TE", "QB")


def sn_augment(df, hl):
    """Continuous snap columns IN PLACE (prior games only): per-snap EW opportunity rate (opp / snap) for carries /
    targets. Needs nflv2.augment first (v2_lag_snap, v2_ew_partial)."""
    from .frame import ew_prior, group_starts
    gs = group_starts(df["player_id"])
    h = hl[0] if isinstance(hl, (tuple, list)) else hl
    snap = np.asarray(df["offense_pct"], float)
    up = np.asarray(df["is_up"], float) == 1 if "is_up" in df else np.zeros(len(df), bool)
    known = np.isfinite(snap) & (snap > 0) & ~up
    ms, _ = ew_prior(np.where(known, snap, np.nan), gs, h)
    for c in ("carries", "targets"):
        mo, _ = ew_prior(np.where(known, np.asarray(df[c], float), np.nan), gs, h)
        with np.errstate(all="ignore"):
            df[f"sn_rate_{c}"] = mo / ms
    return df


def _sn_design(df, stats=None):
    fit = stats is None
    stats = stats or {"z": {}, "pos": []}
    cols = []
    for c in SN_COLS:
        x = np.asarray(df[c], float)
        if fit:
            ok = np.isfinite(x)
            stats["z"][c] = {"mean": float(np.mean(x[ok])) if ok.any() else 0.0}
        cols.append(np.where(np.isfinite(x), x, stats["z"][c]["mean"]))
    pos = np.asarray(df["position"])
    if fit:
        stats["pos"] = [p for p in SN_POS if 0 < np.mean(pos == p) < 1]
    for p in stats["pos"]:
        cols.append((pos == p).astype(float))
    return np.column_stack([np.ones(len(df))] + cols), stats


class SN:
    """sn arm for one market: E[opp] = per-snap rate x predicted snap share; sm-1 efficiency / GLM / family."""

    def __init__(self, market, hl, feats, family):
        self.market, self.hl, self.feats, self.family = market, tuple(hl), list(feats), family

    def e_opp(self, df):
        o = model.SPEC[self.market]["opp"]
        X, _ = _sn_design(df, self.sstats)
        S = np.clip(X @ self.sbeta, 0.05, 1.0)
        r = np.asarray(df[f"sn_rate_{o}"], float)
        e = r * S
        return np.where(np.isfinite(e), e, np.asarray(df[f"ew_{o}"], float))

    def mu(self, df):
        _, _, eff = model.base_inputs(df, self.market, self.prior, self.k)
        e = self.e_opp(df)
        cols = study._feature_cols(df, "nfl", self.market, self.feats)
        X, _ = model.design(df, cols, self.stats)
        X = np.column_stack([np.ones(len(df)), X])
        mu = e * eff * np.exp(np.clip(X @ self.beta, -3, 3))
        return np.maximum(np.nan_to_num(mu, nan=0.05), 0.05), e, eff

    def fit(self, tr):
        mk = self.market
        y = np.asarray(tr[f"y_{mk}"], float)
        snap = np.asarray(tr["offense_pct"], float)
        ok = np.isfinite(snap)
        X, self.sstats = _sn_design(tr)
        self.sbeta = np.linalg.lstsq(X[ok], snap[ok], rcond=None)[0]
        self.prior = model.fit_mix_priors(tr, mk, model.fit_priors(tr, mk))
        self.k = model.fit_k(tr, mk, self.prior)
        _, _, eff = model.base_inputs(tr, mk, self.prior, self.k)
        mu0 = self.e_opp(tr) * eff
        cols = study._feature_cols(tr, "nfl", mk, self.feats)
        Xg, self.stats = model.design(tr, cols)
        Xg = np.column_stack([np.ones(len(tr)), Xg])
        self.beta = model.fit_glm(Xg, y, np.log(np.maximum(np.nan_to_num(mu0, nan=0.05), 1e-3)))
        mu, e, eff = self.mu(tr)
        self.par = model.fit_family_params(tr, mk, mu, model.mix_context(tr, mk, mu, e, eff, self.prior))
        return self

    def raw_pmf(self, df):
        lo, ks = dists.grid(self.market)
        mu, e, eff = self.mu(df)
        ctx = model.mix_context(df, self.market, mu, e, eff, self.prior)
        return model.family_pmf(self.family, self.market, mu, ctx, self.par, lo, ks)


# ================================================================================================== walk-forward
def _frame(sport, con, hl):
    if sport == "nba":
        return study._frame("nba", con, hl)
    from . import nflv2
    df = nflv2.study_frame(con, hl)
    sn_augment(df, hl)
    return df


def origins(sport, d):
    """-> (origin key per row, ordered [(key, role)] with role warm | sel | eval)."""
    s = np.asarray(d["start_utc"])
    if sport == "nfl":
        se, wk = np.asarray(d["season"]).astype(int), np.asarray(d["week"]).astype(int)
        key = np.array([f"{a}-{b:02d}" for a, b in zip(se, wk)], dtype=object)
        warm = sorted({k for k, a, b in zip(key, se, wk) if a == 2025 and b <= 9})
        scored = sorted({k for k, a, b in zip(key, se, wk) if (a == 2025 and b >= 10) or (a == 2026 and b <= 7)})
    else:
        t = _ts(s) - np.timedelta64(10, "h")
        key = np.array([str(x)[:10] for x in t.astype("datetime64[D]")], dtype=object)
        days = sorted(set(key))
        nw, ns = SPAN["nba"]
        scored = days[-ns:]
        warm = days[-(ns + nw):-ns]
    nsel = int(round(SEL_SHARE * len(scored)))
    order = [(k, "warm") for k in warm] + [(k, "sel" if i < nsel else "eval") for i, k in enumerate(scored)]
    return key, order


def _base_fit(sport, mk, cfg, tr):
    f = study.Fitted(mk, tuple(cfg["half_lives"]))
    study.mean_model(f, tr, tr, mk, sport, list(cfg["features"]))
    study.fit_family_params(f, tr, mk, sport)
    f.family, f.cal = cfg["family"], tuple(cfg["cal"])
    return f


def walk(sport, mk, df, cfg, v2cfg=None, log=print):
    """Rolling-origin forecasts for one market -> dict of per-row arrays (OOS only)."""
    from . import nflv2
    el = model.eligible(df, mk) & np.isfinite(df.f(f"y_{mk}"))
    d_all = df[el]
    start = _ts(d_all["start_utc"])
    known = start + np.timedelta64(int(KNOWN_LAG_H[sport] * 3600), "s")
    key, order = origins(sport, d_all)
    lo, ks = dists.grid(mk)
    arms = [a for a in ARMS[mk] if a in ("v2", "sn")]
    out = {k: [] for k in ("idx", "origin", "role", "t", "pmf_live", "y", "game", "start", "block", "lref", "stype")}
    for a in arms:
        out["pmf_" + a] = []
    lref_all = None
    with np.errstate(all="ignore"):
        lref_all = np.floor(np.nanmean(d_all.attrs["lags"][mk][:, :10], axis=1)) + 0.5
    t0 = time.time()
    for oi, (k, role) in enumerate(order):
        te_m = key == k
        if not te_m.any():
            continue
        t = start[te_m].min() - np.timedelta64(3600, "s")
        tr_m = known <= t
        tr, te = d_all[tr_m], d_all[te_m]
        f = _base_fit(sport, mk, cfg, tr)
        p_live, _ = study.pmf_for(f, te, mk, sport)
        out["pmf_live"].append(p_live)
        if "v2" in arms:
            c2 = v2cfg[mk]
            v = nflv2.V2(mk, tuple(c2["half_lives"]), c2["features"], c2["family"]).fit(tr)
            out["pmf_v2"].append(v.raw_pmf(te)[0])
        if "sn" in arms:
            out["pmf_sn"].append(SN(mk, tuple(cfg["half_lives"]), cfg["features"], cfg["family"]).fit(tr).raw_pmf(te))
        n = int(te_m.sum())
        out["idx"].append(np.flatnonzero(te_m))
        out["origin"].append(np.full(n, oi))
        out["role"].append(np.full(n, role, dtype=object))
        out["t"].append(np.full(n, t))
        out["y"].append(np.asarray(te[f"y_{mk}"], float))
        out["game"].append(np.asarray(te["game_id"]))
        out["start"].append(start[te_m])
        out["lref"].append(lref_all[te_m])
        out["stype"].append(np.asarray(te["season_type"], dtype=object))
        if sport == "nfl":
            out["block"].append(np.array([k] * n, dtype=object))
        else:
            out["block"].append(np.array(study.featurize.iso_week(te["ts"]), dtype=object))
        if oi % 10 == 0:
            log(f"[{sport}:{mk}] origin {oi + 1}/{len(order)} {k} ({role}) fit {int(tr_m.sum())} test {n} "
                f"({time.time() - t0:.0f}s)")
    res = {kk: np.concatenate(v) for kk, v in out.items() if v}
    res["order"] = order
    return res


def _layer_series(w, base, lo, ks, kind, param):
    """Apply a calibration layer origin by origin, fitted only on earlier OOS rows known at t. -> (pmf, (c, s) per row)."""
    y, org, start, t = w["y"], w["origin"], w["start"], w["t"]
    known = start + np.timedelta64(int(w["lag_h"] * 3600), "s")
    P = base.copy()
    cs = np.ones((len(y), 2))
    for oi in np.unique(org):
        m = org == oi
        tt = t[m][0]
        prior = (org < oi) & (known <= tt)
        if kind == "rc":
            if prior.sum() < MIN_RC:
                continue
            age = (tt - start[prior]).astype("timedelta64[s]").astype(float) / 86400.0
            wt = weights(age, param)
            if wt.sum() <= 0:
                continue
            c, s = fit_rc(base[prior], lo, ks, y[prior], wt)
        else:                                               # dr: previous k game-weeks only
            prior &= org >= oi - param
            if prior.sum() < MIN_DR:
                continue
            c, s = fit_dr(base[prior], ks, y[prior]), 1.0
        P[m] = fast_transform(base[m], lo, ks, dists.mean_of(base[m], ks), c, s)
        cs[m] = (c, s)
    return P, cs


def _metrics(p, lo, ks, y, lref):
    return evaluate.scores(p, lo, y, dists.mean_of(p, ks), lref)


def _bands(sc, games):
    po = sc["p_over_ref"]
    hit = sc["hit_ref"]
    pch = np.maximum(po, 1 - po)
    win = np.where(po >= 0.5, hit, 1 - hit)
    out = {}
    for b in ("A", "B", "C", "Lean"):
        m = np.array([grade_policy.band(x) == b for x in pch])
        if not m.any():
            out[b] = {"n": 0}
            continue
        mm, a, bb = evaluate.boot_mean(win[m] - pch[m], games[m])
        out[b] = {"n": int(m.sum()), "games": int(len(np.unique(games[m]))), "mean_p": float(pch[m].mean()),
                  "hit_rate": float(win[m].mean()), "hit_minus_p": mm, "ci_game": [a, bb]}
    return out


def _summ(sc, games):
    cal = evaluate.reliability(sc["p_over_ref"], sc["hit_ref"])
    po = sc["p_over_ref"]
    return {"crps": float(np.mean(sc["crps"])), "bias": float(np.mean(sc["err"])), "mae": float(np.mean(sc["ae"])),
            "logloss_ref": float(np.mean(sc["logloss_ref"])), "brier_ref": float(np.mean(sc["brier_ref"])),
            "ece_ref": cal["ece"], "in_the_large_ref": cal["in_the_large"], "cov80_pit": float(np.mean(sc["cov80"])),
            "mean_p_over_ref": float(np.mean(po)), "hit_rate_ref": float(np.mean(sc["hit_ref"])),
            "share_over_favoured_ref": float(np.mean(po > 0.5)),
            "mean_p_chosen_ref": float(np.mean(np.maximum(po, 1 - po)))}


def evaluate_market(sport, mk, w, log=print):
    """Selection of h / k on the selection block, then the A7 7 criteria on the evaluation block."""
    lo, ks = dists.grid(mk)
    w["lag_h"] = KNOWN_LAG_H[sport]
    role, y, games, blocks, lref = w["role"], w["y"], w["game"], w["block"], w["lref"]
    sel, ev = role == "sel", role == "eval"
    cands = {}
    for arm in ARMS[mk]:
        base = w["pmf_live"] if arm in ("rc", "dr") else w["pmf_" + arm]
        grid = K_GRID if arm == "dr" else H_GRID[sport]
        tried = {}
        for prm in grid:
            P, cs = _layer_series(w, base, lo, ks, "dr" if arm == "dr" else "rc", prm)
            tried[prm] = (float(_crps_rows(P[sel], lo, y[sel]).mean()), P, cs)
        best = min(tried, key=lambda h: (round(tried[h][0], 10), -h))           # ties -> the longer h / k
        cands[arm] = {"param": best, "selection_crps": {str(k): v[0] for k, v in tried.items()},
                      "P": tried[best][1], "cs": tried[best][2]}
    live = _metrics(w["pmf_live"][ev], lo, ks, y[ev], lref[ev])
    w["dump"] = {"live": {"crps": live["crps"], "p_over_ref": live["p_over_ref"], "err": live["err"]}}
    rep = {"rows": {"warm": int((role == "warm").sum()), "sel": int(sel.sum()), "eval": int(ev.sum())},
           "games_eval": int(len(np.unique(games[ev]))),
           "origins": {r: [k for k, rr in w["order"] if rr == r] for r in ("warm", "sel", "eval")},
           "live": _summ(live, games[ev]), "live_bands_ref": _bands(live, games[ev]), "candidates": {}}
    rep["live_selection_crps"] = float(_crps_rows(w["pmf_live"][sel], lo, y[sel]).mean())
    for arm, c in cands.items():
        sc = _metrics(c["P"][ev], lo, ks, y[ev], lref[ev])
        su = _summ(sc, games[ev])
        crps = evaluate.paired(sc, live, games[ev], blocks[ev], "crps")
        ll = evaluate.paired(sc, live, games[ev], blocks[ev], "logloss_ref")
        bands = _bands(sc, games[ev])
        crit = {"a_crps_ci_below_0": crps["ci_game"][1] is not None and crps["ci_game"][1] < 0,
                "b_calibration_ref": abs(su["in_the_large_ref"]) <= 0.02 and su["ece_ref"] <= 0.03,
                "c_cov80": 0.76 <= su["cov80_pit"] <= 0.84,
                "e_bands_ref": all(abs(v["hit_rate"] - v["mean_p"]) <= 0.03 for v in bands.values() if v["n"] >= 100)}
        csv = c["cs"][ev]
        w.setdefault("dump", {})[arm] = {"crps": sc["crps"], "p_over_ref": sc["p_over_ref"], "err": sc["err"]}
        rep["candidates"][arm] = {
            "param": c["param"] if math.isfinite(c["param"]) else "inf",
            "param_unit": "game-weeks" if arm == "dr" else "days (half-life)",
            "selection_crps": c["selection_crps"], "summary": su, "bands_ref": bands,
            "crps_minus_live": crps, "logloss_ref_minus_live": ll,
            "brier_ref_minus_live": evaluate.paired(sc, live, games[ev], blocks[ev], "brier_ref"),
            "mean_p_chosen_change": su["mean_p_chosen_ref"] - rep["live"]["mean_p_chosen_ref"],
            "layer_c": {"mean": float(csv[:, 0].mean()), "min": float(csv[:, 0].min()), "max": float(csv[:, 0].max())},
            "layer_s": {"mean": float(csv[:, 1].mean()), "min": float(csv[:, 1].min()), "max": float(csv[:, 1].max())},
            "criteria_walk_forward": {k: bool(v) for k, v in crit.items()},
            "walk_forward_pass": bool(all(crit.values()))}
        log(f"[{sport}:{mk}:{arm}] param {c['param']} CRPS diff {crps['diff']:+.4f} {crps['ci_game']} "
            f"itl {su['in_the_large_ref']:+.4f} ece {su['ece_ref']:.4f} cov {su['cov80_pit']:.3f} -> "
            f"{'PASS' if all(crit.values()) else 'fail'} {crit}")
    passed = [a for a, v in rep["candidates"].items() if v["walk_forward_pass"]]
    rep["walk_forward_passed"] = passed
    rep["chosen"] = (min(passed, key=lambda a: rep["candidates"][a]["summary"]["crps"]) if passed else None)
    return rep


def final_params(sport, mk, w, arm, param, as_of):
    """The layer's parameters for forward use: fitted on ALL OOS rows (warm-up + span) known at as_of (A7 8)."""
    lo, ks = dists.grid(mk)
    base = w["pmf_live"] if arm in ("rc", "dr") else w["pmf_" + arm]
    t = np.datetime64(as_of.replace("Z", ""))
    known = w["start"] + np.timedelta64(int(KNOWN_LAG_H[sport] * 3600), "s") <= t
    if arm == "dr":
        last = np.unique(w["origin"])[-int(param):]
        m = known & np.isin(w["origin"], last)
        return {"c": fit_dr(base[m], ks, w["y"][m]), "s": 1.0, "rows": int(m.sum())}
    age = (t - w["start"][known]).astype("timedelta64[s]").astype(float) / 86400.0
    c, s = fit_rc(base[known], lo, ks, w["y"][known], weights(age, param))
    return {"c": c, "s": s, "rows": int(known.sum())}


def _dump(path, w):
    """Per-row evaluation-block arrays (diagnostics only; scratch, never committed)."""
    ev = w["role"] == "eval"
    arr = {"y": w["y"][ev], "game": w["game"][ev].astype(str), "start": w["start"][ev].astype(str),
           "stype": w["stype"][ev].astype(str), "lref": w["lref"][ev]}
    for arm, d in w["dump"].items():
        for k, v in d.items():
            arr[f"{arm}_{k}"] = np.asarray(v)
    np.savez_compressed(path, **arr)


def run(sport, con, markets=None, log=print, as_of=None, dump=None):
    from ..contracts import now_utc
    t0 = time.time()
    as_of = as_of or now_utc()
    cfg = json.loads((ART_DIR / f"model_config_{sport}.json").read_text(encoding="utf-8"))["markets"]
    v2cfg = (json.loads((ART_DIR / "model_config_nflv2.json").read_text(encoding="utf-8"))["markets"]
             if sport == "nfl" else None)
    mks = [m for m in (markets or (NBA_MK if sport == "nba" else NFL_MK))]
    out = {"version": VERSION, "sport": sport, "register": "sports/research/REGISTER.md A7", "label": LABEL,
           "generated_at": now_utc(), "as_of": as_of, "multiple_testing": "16 candidate x market tests at one-sided "
           "2.5% each (~0.4 expected false passes)", "markets": {}}
    frames = {}
    for mk in sorted(mks, key=lambda m: tuple(cfg[m]["half_lives"])):
        hl = tuple(cfg[mk]["half_lives"])
        if hl not in frames:
            frames.clear()
            frames[hl] = _frame(sport, con, hl)
            log(f"[{sport}] frame {hl} built ({time.time() - t0:.0f}s)")
        w = walk(sport, mk, frames[hl], cfg[mk], v2cfg, log)
        rep = evaluate_market(sport, mk, w, log)
        if dump:
            _dump(Path(dump) / f"{sport}_{mk}.npz", w)
        if rep["chosen"]:
            arm = rep["chosen"]
            prm = rep["candidates"][arm]["param"]
            rep["freeze"] = {"arm": arm, "param": prm, **final_params(sport, mk, w, arm, INF if prm == "inf" else prm,
                                                                        as_of)}
        out["markets"][mk] = rep
        log(f"[{sport}:{mk}] chosen {rep['chosen']} ({time.time() - t0:.0f}s)")
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f"model_tw1_{sport}.json"
    if markets and p.exists():                                   # partial re-run of named markets keeps the others
        prev = json.loads(p.read_text(encoding="utf-8"))
        prev["markets"].update(out["markets"])
        out = dict(prev, generated_at=out["generated_at"])
    p.write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    return out


# ================================================================================================== freeze / load
def art_path(sport):
    return ART_DIR / f"tw1_{sport}.json"


def freeze(sport):
    """Switched markets (A7 7 decision; walk-forward part) -> tw1_<sport>.json + MANIFEST_TW1.json entry."""
    from ..contracts import now_utc
    from .fwd import _file_sha
    res = json.loads((RESULTS / f"model_tw1_{sport}.json").read_text(encoding="utf-8"))
    sm1 = json.loads((ART_DIR / f"sm1_{sport}.json").read_text(encoding="utf-8"))
    mk = {}
    for m, r in res["markets"].items():
        fr = r.get("freeze")
        if not fr or fr["arm"] not in ("rc", "dr"):
            continue                    # v2 / sn winners need their own forward model (not wired in tw-1)
        mk[m] = {"arm": fr["arm"], "param": fr["param"], "c": fr["c"], "s": fr["s"], "fit_rows": fr["rows"],
                 "book_line": "pending (A7 7d: not evaluable before >= 100 settled props / 20 games)"}
    art = {"version": VERSION, "sport": sport, "applies_to_sm1": sm1["sha"], "frozen_at": now_utc(),
           "data_as_of": res["as_of"], "results_generated_at": res["generated_at"], "markets": mk,
           "register": "REGISTER.md A7"}
    art["sha"] = _sha(art["markets"])[:16]
    p = art_path(sport)
    p.write_text(json.dumps(art, indent=1, sort_keys=True), encoding="utf-8")
    man = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"version": "tw1-pkg-1",
                                                                                     "files": {}, "artifacts": {}}
    man["files"][p.name] = {"sha256": _file_sha(p), "bytes": p.stat().st_size}
    man["artifacts"][sport] = {"sha": art["sha"], "applies_to_sm1": art["applies_to_sm1"], "frozen_at": art["frozen_at"]}
    MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True), encoding="utf-8")
    return art


def load(sport, sm1_sha=None, art_dir=None):
    """The verified tw-1 artifact for sport, or None (missing / hash mismatch / different sm-1 / switched off)."""
    if not enabled():
        return None
    from .fwd import _file_sha
    d = Path(art_dir) if art_dir else ART_DIR
    p, mp = d / f"tw1_{sport}.json", d / MANIFEST.name
    if not p.exists() or not mp.exists():
        return None
    try:
        man = json.loads(mp.read_text(encoding="utf-8"))
        a = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (man.get("files", {}).get(p.name, {}).get("sha256") != _file_sha(p) or a.get("sha") != _sha(a["markets"])[:16]
            or (man.get("artifacts", {}).get(sport) or {}).get("sha") != a.get("sha")):
        return None
    if sm1_sha is not None and a.get("applies_to_sm1") != sm1_sha:
        return None
    off = {x.strip() for x in os.environ.get("SPORTS_TUNE_TW1_OFF", "").split(",") if x.strip()}
    a["markets"] = {m: v for m, v in a["markets"].items() if m not in off}
    return a


def layer(art, market, pmf, lo):
    """Apply a switched market's frozen layer to ONE sm-1 PMF row (dists.transform - the replay path). -> (pmf, meta)
    or (pmf, None) when the market is not switched."""
    m = (art or {}).get("markets", {}).get(market)
    if not m:
        return pmf, None
    ks = np.arange(lo, lo + len(pmf), dtype=float)
    p = np.asarray(pmf, float)[None, :]
    mu = dists.mean_of(p, ks)
    out = dists.transform(p, lo, ks, mu, float(m["c"]), float(m["s"]))[0]
    return out, {"version": VERSION, "sha": art["sha"], "arm": m["arm"], "c": m["c"], "s": m["s"],
                 "calibration_version": f"{VERSION}:{m['arm']}:c={m['c']:.6g},s={m['s']:g}"}


# ================================================================================================== bl-1 book lines
def _logit(p):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def _sig(x):
    return 1 / (1 + np.exp(-x))


def bl_probs(p_over, p_under, p_push, a):
    """bl-1 (A7 6): F'(k) = sigmoid(logit F(k) + a) on the whole CDF; at line L: P'(Under) = F'(L-), P'(Over) =
    1 - F'(L). -> P'(Over | no push)."""
    p_over, p_under = np.asarray(p_over, float), np.asarray(p_under, float)
    f_le = 1 - p_over                                     # F(L) (includes the push atom)
    fu = _sig(_logit(p_under) + a)
    fl = _sig(_logit(f_le) + a)
    po, pu = 1 - fl, fu
    return np.clip(po / np.maximum(po + pu, 1e-12), 1e-6, 1 - 1e-6)


def fit_bl(p_over, p_under, p_push, hit):
    """a by maximum likelihood (golden section on [-2, 2])."""
    def nll(a):
        q = bl_probs(p_over, p_under, p_push, a)
        return -float(np.mean(hit * np.log(q) + (1 - hit) * np.log(1 - q)))
    lo, hi = -2.0, 2.0
    for _ in range(60):
        m1, m2 = lo + (hi - lo) * 0.382, lo + (hi - lo) * 0.618
        if nll(m1) < nll(m2):
            hi = m2
        else:
            lo = m1
    return (lo + hi) / 2


def bookline_rows(lcon, icon=None, scon=None, model_version="sm-1"):
    """Settled target-book props (A7 6): pushes / voids / unresolved excluded; one row per (game, player, stat, line) =
    the last pre-start forecast (player from the store when scon is given, else one row per forecast). Reads the
    learning.db scorecard (+ forecasts.db inputs for the PMF when icon is given)."""
    q = ("SELECT forecast_id, league, stat, event_id, platform, line, p_over, p_under, p_push, status, value, "
         "forecast_at, settled_at FROM scorecard WHERE superseded = 0 AND model_version = ? AND platform IN "
         "('prizepicks', 'underdog')")
    names = ("fid", "league", "stat", "event", "platform", "line", "po", "pu", "pp", "status", "value", "forecast_at",
             "settled_at")
    last = {}
    for r in lcon.execute(q, (model_version,)):
        r = dict(zip(names, r))
        if r["value"] is None or r["status"] not in ("win", "loss") or float(r["value"]) == float(r["line"]):
            continue
        player = r["fid"]
        if scon is not None:
            from .. import store
            fc = store.get(scon, r["fid"])
            player = getattr(fc, "player_id", None) or r["fid"]
        k = (r["event"], player, r["stat"], float(r["line"]))
        if k not in last or r["forecast_at"] > last[k]["forecast_at"]:
            last[k] = r
    res = list(last.values())
    if icon is not None:
        for r in res:
            x = icon.execute("SELECT pmf_lo, pmf_json FROM inputs WHERE forecast_id = ?", (r["fid"],)).fetchone()
            r["pmf"] = (x[0], json.loads(x[1])) if x and x[1] else None
    return res


def bl_rolling(rs, min_n=100, min_games=20):
    """bl-1 per forecast (rs sorted by forecast_at): a fitted only on props SETTLED before that forecast_at (A7 6).
    -> (P'(Over | no push) per row, a per row; a = 0 and the live probability below the minimum sample)."""
    hit = np.array([float(r["value"]) > float(r["line"]) for r in rs], float)
    po = np.array([r["po"] for r in rs], float)
    pu = np.array([r["pu"] for r in rs], float)
    pp = np.array([r["pp"] or 0.0 for r in rs], float)
    games = np.array([r["event"] for r in rs], dtype=object)
    settled = np.array([r["settled_at"] or "" for r in rs], dtype=object)
    bl = np.clip(po / np.maximum(po + pu, 1e-12), 1e-6, 1 - 1e-6)
    a_used = np.zeros(len(rs))
    cache = {}
    for i, r in enumerate(rs):
        prior = settled < r["forecast_at"]
        key = int(prior.sum())
        if key >= min_n and len(set(games[prior])) >= min_games:
            if key not in cache:
                cache[key] = fit_bl(po[prior], pu[prior], pp[prior], hit[prior])
            a_used[i] = cache[key]
            bl[i] = bl_probs(po[i:i + 1], pu[i:i + 1], pp[i:i + 1], cache[key])[0]
    return bl, a_used


def bookline(rows, tw1=None, min_n=100, min_games=20):
    """bl-1 rolling (fit on props settled before each forecast) + the A7 7(d)/(e) book-line criteria for live and for
    each switched tw-1 market. rows: bookline_rows() output. -> {league: {stat: report}}."""
    out = {}
    by = {}
    for r in rows:
        by.setdefault((r["league"], r["stat"]), []).append(r)
    for (lg, st), rs in sorted(by.items()):
        rs.sort(key=lambda r: r["forecast_at"])
        hit = np.array([float(r["value"]) > float(r["line"]) for r in rs], float)
        po = np.array([r["po"] for r in rs], float)
        pu = np.array([r["pu"] for r in rs], float)
        pp = np.array([r["pp"] or 0.0 for r in rs], float)
        games = np.array([r["event"] for r in rs], dtype=object)
        live = np.clip(po / np.maximum(po + pu, 1e-12), 1e-6, 1 - 1e-6)
        bl, a_used = bl_rolling(rs, min_n, min_games)
        rep = {"props": len(rs), "games": int(len(set(games))), "over_rate": float(hit.mean()),
               "evaluable": len(rs) >= min_n and len(set(games)) >= min_games, "live": _bl_summary(live, hit, games)}
        scored = a_used != 0
        if scored.any():
            rep["bl1"] = _bl_summary(bl[scored], hit[scored], games[scored], ref=live[scored])
        mk = ((tw1 or {}).get("markets") or {}).get(st)
        if mk and all(r.get("pmf") for r in rs):
            q = np.array([_tw1_p(r, mk) for r in rs])
            rep["tw1"] = _bl_summary(q, hit, games, ref=live)
        out.setdefault(lg, {})[st] = rep
    return out


def _tw1_p(r, mk):
    lo, p = r["pmf"]
    p2, _ = layer({"markets": {r["stat"]: mk}, "sha": ""}, r["stat"], np.asarray(p, float), lo)
    po, pu, _ = dists.line_probs(p2, lo, float(r["line"]))
    return min(max(po / max(po + pu, 1e-12), 1e-6), 1 - 1e-6)


def _bl_summary(p, hit, games, ref=None):
    ll = -(hit * np.log(p) + (1 - hit) * np.log(1 - p))
    s = float(np.mean(p > 0.5))
    o = float(hit.mean())
    out = {"n": int(len(p)), "games": int(len(set(games))), "logloss": float(ll.mean()),
           "brier": float(np.mean((p - hit) ** 2)), "in_the_large": float(p.mean() - o),
           "share_over_favoured": s, "over_rate": o, "not_one_sided": abs(s - o) <= 0.15}
    pch = np.maximum(p, 1 - p)
    win = np.where(p >= 0.5, hit, 1 - hit)
    bands = {}
    for b in ("A", "B", "C", "Lean"):
        m = np.array([grade_policy.band(x) == b for x in pch])
        if m.sum():
            mm, lo, hi = evaluate.boot_mean(win[m] - pch[m], games[m])
            bands[b] = {"n": int(m.sum()), "mean_p": float(pch[m].mean()), "hit_rate": float(win[m].mean()),
                        "ci_game": [lo, hi], "ok": bool(m.sum() < 30 or abs(mm) <= 0.05 or (lo is not None and lo <= 0
                                                                                          <= hi))}
    out["bands"] = bands
    if ref is not None:
        llr = -(hit * np.log(ref) + (1 - hit) * np.log(1 - ref))
        m, lo, hi = evaluate.boot_mean(ll - llr, games)
        out["logloss_minus_live"] = {"mean": m, "ci_game": [lo, hi]}
        out["criterion_d"] = bool(m is not None and m <= 0 and out["not_one_sided"])
    return out


def status():
    from .fwd import _file_sha  # noqa: F401
    out = {"version": VERSION, "enabled": enabled(), "artifacts": {}}
    for sp in ("nba", "nfl"):
        a = load(sp)
        out["artifacts"][sp] = None if a is None else {"sha": a["sha"], "markets": {m: {k: v[k] for k in
                                                       ("arm", "c", "s")} for m, v in a["markets"].items()}}
    return out


def main(argv=None):
    import argparse
    import warnings
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    p = argparse.ArgumentParser(prog="python -m sports.model.tune")
    p.add_argument("cmd", choices=["run", "freeze", "bookline", "status"])
    p.add_argument("history", nargs="?")
    p.add_argument("sport", nargs="?")
    p.add_argument("--markets")
    p.add_argument("--as-of", dest="as_of")
    p.add_argument("--learning")
    p.add_argument("--inputs")
    p.add_argument("--dump")
    a = p.parse_args(argv)
    log = lambda x: print(x, flush=True)                                          # noqa: E731
    if a.cmd == "status":
        print(json.dumps(status(), indent=1))
    elif a.cmd == "run":
        from . import datasets
        con = datasets.connect(Path(a.history), read_only=True)
        run(a.sport, con, a.markets.split(",") if a.markets else None, log, a.as_of, a.dump)
    elif a.cmd == "freeze":
        print(json.dumps(freeze(a.history), indent=1))
    else:
        import sqlite3
        from . import learning
        lcon = sqlite3.connect(a.learning) if a.learning else learning.db()
        icon = sqlite3.connect(a.inputs) if a.inputs else None
        rows = bookline_rows(lcon, icon)
        res = {"label": "bl-1 + A7 7(d)/(e) at real target-book lines (settled forward props)", "leagues": {}}
        for lg in ("nba", "nfl"):
            sub = [r for r in rows if r["league"] == lg]
            if sub:
                res["leagues"][lg] = bookline(sub, load(lg))[lg]
        print(json.dumps(res, indent=1, default=float))

if __name__ == "__main__":
    main()
