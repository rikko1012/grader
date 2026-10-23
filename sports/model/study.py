"""The registered study (REGISTER.md): train -> tune (half-lives, features, family, cal-1) -> LOCKED test, per market.

phase "tune": everything up to and including calibration (never touches test rows).
phase "test": the frozen tune choices scored on the LOCKED test (+ NFL secondary locked test) - run ONCE.
Results: sports/research/results/model_study_<sport>_<phase>.json (+ the frozen config model_config_<sport>.json).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .. import fair, features, markets
from . import dists, evaluate, featurize, model

RESULTS = Path(__file__).resolve().parents[1] / "research" / "results"
CONFIG_DIR = Path(__file__).resolve().parent / "artifacts"
HALF_LIVES = {"nba": [(hm, hr) for hm in (3, 5, 10) for hr in (10, 20, 40)], "nfl": [(h, h) for h in (2, 4, 8)]}
NBA_MK = list(featurize.NBA_MARKETS)
NFL_MK = list(featurize.NFL_MARKETS)
C_GRID = np.round(np.arange(0.90, 1.1001, 0.01), 2)
S_GRID = np.round(np.arange(0.80, 1.3001, 0.02), 2)


def _frame(sport, con, hl):
    if sport == "nba":
        return featurize.nba_frame(con, h_min=hl[0], h_rate=hl[1])
    return featurize.nfl_frame(con, h_opp=hl[0])


def sub(df, mask):
    """Row subset (Frame keeps the per-row lag matrices aligned)."""
    return df[np.asarray(mask)]


def _nfl_fill(df):
    for c in ("ew_target_share", "ew_air_yards_share"):
        if c in df:
            df[c] = np.nan_to_num(df[c])
    return df


# ------------------------------------------------------------------------------------------------------- baselines
def baseline_pmfs(df, sport, market, lo, ks):
    lags = df.attrs["lags"][market]
    fam = "basketball" if sport == "nba" else "football"
    # B0: last <= 10 prior played values
    l10 = lags[:, :10]
    n10 = np.isfinite(l10).sum(axis=1)
    b0_mean = np.nanmean(l10, axis=1)
    K = len(ks)
    b0 = np.zeros((len(df), K))
    for j in range(10):
        v = l10[:, j]
        ok = np.isfinite(v)
        idx = np.clip(np.rint(v[ok]).astype(int) - lo, 0, K - 1)
        np.add.at(b0, (np.where(ok)[0], idx), 1.0)
    b0 /= np.maximum(b0.sum(axis=1, keepdims=True), 1)
    # B1: recency-weighted mean (half-life 10 NBA / 5 NFL, last <= 40 / 20) + fair.py shape
    hl, cap = (10, 40) if sport == "nba" else (5, 20)
    L = lags[:, :cap]
    w = 0.5 ** (np.arange(L.shape[1]) / hl)
    W = np.where(np.isfinite(L), w[None, :], 0.0)
    b1_mean = np.nansum(np.nan_to_num(L) * W, axis=1) / np.maximum(W.sum(axis=1), 1e-9)
    shape = markets.market(fam, market)["shape"]
    a, b = fair.shape_params(fam, market, shape)
    m = np.maximum(b1_mean, 0.05)
    b1 = dists.negbin(m, b, lo, ks) if shape == "count" else dists.normal(m, a * m + b * m * m, lo, ks)
    return {"b0": (b0, b0_mean, n10), "b1": (b1, b1_mean)}


# ---------------------------------------------------------------------------------------------------------- fitting
class Fitted:
    """Everything sm-1 needs for one market (all JSON-serialisable via .config())."""

    def __init__(self, market, hl):
        self.market, self.hl = market, hl
        self.feats, self.stats, self.beta = [], None, np.zeros(0)
        self.family, self.par, self.cal = None, {}, (1.0, 1.0)

    def config(self):
        def clean(o):
            if isinstance(o, dict):
                return {k: clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [clean(v) for v in o]
            if isinstance(o, (np.floating, np.integer)):
                return o.item()
            if isinstance(o, np.ndarray):
                return clean(o.tolist())
            return o
        mi = getattr(self, "mi", None)
        return clean({"mi_hist": mi.config() if mi is not None else None, "market": self.market, "half_lives": list(self.hl), "prior": self.prior, "k": self.k,
                      "features": self.feats, "design": self.stats, "beta": list(self.beta), "family": self.family,
                      "family_params": self.par, "cal": list(self.cal)})


def _feature_cols(df, sport, market, ids):
    reg = {r["id"]: r for r in features.REGISTRY}
    for fid in ids:
        c = "F_" + fid
        if c not in df:
            df[c] = features.compute(df, sport, market, reg[fid]["column"])
    return ["F_" + f for f in ids]


def mean_model(f, df_tr, df, market, sport, feats):
    """Fit base priors + k + GLM on train; return mu for df rows and the fit pieces."""
    prior = model.fit_priors(df_tr, market)
    prior = model.fit_mix_priors(df_tr, market, prior)
    k = model.fit_k(df_tr, market, prior)
    mu0_tr, _, _ = model.base_inputs(df_tr, market, prior, k)
    cols = _feature_cols(df_tr, sport, market, feats)
    X_tr, stats = model.design(df_tr, cols)
    X_tr = np.column_stack([np.ones(len(df_tr)), X_tr])             # intercept: part of the base (amendment A2)
    y_tr = np.asarray(df_tr[f"y_{market}"], float)
    beta = model.fit_glm(X_tr, y_tr, np.log(np.maximum(mu0_tr, 1e-3)))
    f.prior, f.k, f.feats, f.stats, f.beta = prior, k, feats, stats, beta
    return predict_mean(f, df, market, sport)


def predict_mean(f, df, market, sport):
    mu0, e_opp, eff = model.base_inputs(df, market, f.prior, f.k)
    cols = _feature_cols(df, sport, market, f.feats)
    X, _ = model.design(df, cols, f.stats)
    X = np.column_stack([np.ones(len(df)), X])
    mu = mu0 * np.exp(np.clip(X @ f.beta, -3, 3))
    mu = np.maximum(np.nan_to_num(mu, nan=0.05), 0.05)
    return mu, e_opp, eff


def ctx_for(f, df, market, mu, e_opp, eff):
    return model.mix_context(df, market, mu, e_opp, eff, f.prior)


def pmf_for(f, df, market, sport, family=None, cal=None):
    lo, ks = dists.grid(market)
    mu, e_opp, eff = predict_mean(f, df, market, sport)
    ctx = ctx_for(f, df, market, mu, e_opp, eff)
    p = model.family_pmf(family or f.family, market, mu, ctx, f.par, lo, ks)
    c, s = cal or f.cal
    return dists.transform(p, lo, ks, mu, c, s), mu


def _crps(f, df, market, sport, family, cal=(1.0, 1.0)):
    lo, _ = dists.grid(market)
    p, mu = pmf_for(f, df, market, sport, family, cal)
    return dists.crps(p, lo, np.asarray(df[f"y_{market}"], float)), p, mu


def default_family(market):
    return "negbin" if model.SPEC[market]["kind"] == "count" else "normal"


def fit_family_params(f, df_tr, market, sport):
    mu_tr, e_opp, eff = predict_mean(f, df_tr, market, sport)
    ctx_tr = ctx_for(f, df_tr, market, mu_tr, e_opp, eff)
    f.par = model.fit_family_params(df_tr, market, mu_tr, ctx_tr)


def run_market(sport, market, frames, log=print):
    """Tune protocol for one market -> (Fitted, report)."""
    rep = {"market": market, "procedure": ["half-lives (base mean, default family)", "feature selection",
                                           "family", "cal-1"]}
    # 1. half-lives
    hl_scores = {}
    for hl, df in frames.items():
        el = model.eligible(df, market)
        part = model.partition(df, sport)
        tr, tu = df[el & (part == "train")].copy(), df[el & (part == "tune")].copy()
        f = Fitted(market, hl)
        mean_model(f, tr, tr, market, sport, [])
        fit_family_params(f, tr, market, sport)
        c, _, _ = _crps(f, tu, market, sport, default_family(market))
        hl_scores[hl] = (float(c.mean()), len(tr), len(tu))
    best_hl = min(hl_scores, key=lambda h: hl_scores[h][0])
    rep["half_life_tune_crps"] = {str(k): v[0] for k, v in hl_scores.items()}
    rep["half_lives"] = list(best_hl)
    df = frames[best_hl]
    el = model.eligible(df, market)
    part = model.partition(df, sport)
    tr, tu = df[el & (part == "train")].copy(), df[el & (part == "tune")].copy()
    rep["rows"] = {"train": len(tr), "tune": len(tu), "train_games": len(np.unique(tr["game_id"])),
                   "tune_games": len(np.unique(tu["game_id"]))}
    log(f"[{sport}:{market}] half-lives {best_hl} rows {rep['rows']}")
    # 2. features, one at a time on the base, then jointly
    base = Fitted(market, best_hl)
    mean_model(base, tr, tr, market, sport, [])
    fit_family_params(base, tr, market, sport)
    fam0 = default_family(market)
    base_crps, _, _ = _crps(base, tu, market, sport, fam0)
    tests, kept = {}, []
    games = np.asarray(tu["game_id"])
    for track in ("statistical", "history"):
        for r in features.candidates(sport, market, track):
            col = _feature_cols(tu, sport, market, [r["id"]])[0]
            v = np.asarray(tu[col], float)
            if np.nanstd(v) == 0 or np.isfinite(v).sum() < 30:       # cannot be evaluated on tune (amendment A2)
                tests[r["id"]] = {"track": track, "not_evaluable_on_tune": "constant or missing on tune rows",
                                  "passed": False, "kept": False}
                continue
            f = Fitted(market, best_hl)
            mean_model(f, tr, tr, market, sport, [r["id"]])
            f.par = base.par
            c, _, _ = _crps(f, tu, market, sport, fam0)
            d = evaluate.boot_mean(c - base_crps, games)
            ok = d[2] is not None and d[2] < 0
            coef = float(f.beta[1]) if len(f.beta) > 1 else None
            tests[r["id"]] = {"track": track, "tune_crps_diff": d[0], "ci": [d[1], d[2]], "beta_std": coef,
                              "passed": bool(ok), "kept": bool(ok and track == "statistical")}
            if ok and track == "statistical":
                kept.append(r["id"])
    rep["feature_tests"] = tests
    final = Fitted(market, best_hl)
    if kept:
        mean_model(final, tr, tr, market, sport, kept)
        fit_family_params(final, tr, market, sport)
        cj, _, _ = _crps(final, tu, market, sport, fam0)
        dj = evaluate.boot_mean(cj - base_crps, games)
        rep["joint"] = {"features": kept, "tune_crps_diff": dj[0], "ci": [dj[1], dj[2]],
                        "beta_std": dict(zip(kept, [float(b) for b in final.beta[1:len(kept) + 1]]))}
        if not (dj[2] is not None and dj[2] < 0):
            final = base
            rep["joint"]["used"] = False
        else:
            rep["joint"]["used"] = True
    else:
        final = base
    log(f"[{sport}:{market}] kept {final.feats}")
    # 3. family
    fam_scores = {}
    fams = [x for x in model.FAMILIES[model.SPEC[market]["kind"]] if x != "mixture" or model.SPEC[market]["mix"]]
    for fam in fams:
        c, _, _ = _crps(final, tu, market, sport, fam)
        fam_scores[fam] = float(c.mean())
    final.family = min(fam_scores, key=fam_scores.get)
    rep["family_tune_crps"] = fam_scores
    rep["family"] = final.family
    # 4. cal-1 (coordinate search on the registered grids)
    lo, ks = dists.grid(market)
    p_raw, mu = pmf_for(final, tu, market, sport, final.family, (1.0, 1.0))
    y = np.asarray(tu[f"y_{market}"], float)

    def cr(c, s):
        return float(dists.crps(dists.transform(p_raw, lo, ks, mu, c, s), lo, y).mean())
    c_best = min(C_GRID, key=lambda c: cr(c, 1.0))
    s_best = min(S_GRID, key=lambda s: cr(c_best, s))
    c_best = min(C_GRID, key=lambda c: cr(c, s_best))
    final.cal = (float(c_best), float(s_best))
    rep["cal"] = {"c": final.cal[0], "s": final.cal[1], "tune_crps_raw": cr(1.0, 1.0),
                  "tune_crps_cal": cr(*final.cal)}
    log(f"[{sport}:{market}] family {final.family} cal {final.cal}")
    final.mi = market_track(final, tr, tu, market, sport, rep, cr_fn=None)
    return final, rep


def _calibrate(f, tu, market, sport):
    lo, ks = dists.grid(market)
    p_raw, mu = pmf_for(f, tu, market, sport, f.family, (1.0, 1.0))
    y = np.asarray(tu[f"y_{market}"], float)

    def cr(c, s):
        return float(dists.crps(dists.transform(p_raw, lo, ks, mu, c, s), lo, y).mean())
    c_best = min(C_GRID, key=lambda c: cr(c, 1.0))
    s_best = min(S_GRID, key=lambda s: cr(c_best, s))
    c_best = min(C_GRID, key=lambda c: cr(c, s_best))
    f.cal = (float(c_best), float(s_best))


def market_track(final, tr, tu, market, sport, rep, cr_fn=None):
    """mi-hist (REGISTER.md 5): sm-1 + game spread / total terms, same protocol, separate claim. -> Fitted | None."""
    cands = features.candidates(sport, market, "market")
    out = {"label": "market-informed (closing game lines; available about kickoff)", "tests": {}}
    rep["market_track"] = out
    cov = {}
    for r in cands:
        col = _feature_cols(tr, sport, market, [r["id"]])[0]
        _feature_cols(tu, sport, market, [r["id"]])
        cov[r["id"]] = float(np.isfinite(np.asarray(tr[col], float)).mean())
    out["train_coverage"] = cov
    usable = [r for r in cands if cov[r["id"]] >= 0.5]
    if not usable:
        out["run"] = False
        out["why"] = "game lines cover < 50% of eligible train rows (amendment A1c)"
        return None
    fam = final.family
    ref_crps, _, _ = _crps(final, tu, market, sport, fam, final.cal)
    games = np.asarray(tu["game_id"])
    kept = []
    for r in usable:
        f = Fitted(market, final.hl)
        mean_model(f, tr, tr, market, sport, final.feats + [r["id"]])
        fit_family_params(f, tr, market, sport)
        f.family = fam
        _calibrate(f, tu, market, sport)
        c, _, _ = _crps(f, tu, market, sport, fam, f.cal)
        d = evaluate.boot_mean(c - ref_crps, games)
        ok = d[2] is not None and d[2] < 0
        out["tests"][r["id"]] = {"tune_crps_diff_vs_sm1": d[0], "ci": [d[1], d[2]],
                                 "beta_std": float(f.beta[-1]), "passed": bool(ok)}
        if ok:
            kept.append(r["id"])
    out["run"] = True
    if not kept:
        out["kept"] = []
        return None
    f = Fitted(market, final.hl)
    mean_model(f, tr, tr, market, sport, final.feats + kept)
    fit_family_params(f, tr, market, sport)
    f.family = fam
    _calibrate(f, tu, market, sport)
    out["kept"] = kept
    out["cal"] = list(f.cal)
    return f


def score_partition(final, df, market, sport, mask, label):
    """B0 / B1 / sm-1 on the rows of one locked partition -> report (all paired differences)."""
    d = sub(df, mask)
    lo, ks = dists.grid(market)
    y = np.asarray(d[f"y_{market}"], float)
    games = np.asarray(d["game_id"])
    blocks = (np.array(featurize.iso_week(d["ts"]), dtype=object) if sport == "nba" else
              np.array([f"{int(a)}-{int(b)}" for a, b in zip(d["season"], d["week"])], dtype=object))
    bl = baseline_pmfs(d, sport, market, lo, ks)
    b0_pmf, b0_mean, n10 = bl["b0"]
    l_ref = np.floor(b0_mean) + 0.5
    l_whole = np.rint(b0_mean)
    p_sm, mu = pmf_for(final, d, market, sport)
    sc = {"sm1": evaluate.scores(p_sm, lo, y, dists.mean_of(p_sm, ks), l_ref),
          "b1": evaluate.scores(bl["b1"][0], lo, y, bl["b1"][1], l_ref)}
    b0 = evaluate.scores(b0_pmf, lo, y, b0_mean, l_ref)
    hits = ((d.attrs["lags"][market][:, :10] > l_ref[:, None]) & np.isfinite(d.attrs["lags"][market][:, :10])).sum(1)
    b0.update(evaluate.binary_scores((hits + 0.5) / (n10 + 1), y, l_ref))
    b0["nll"] = np.full(len(y), np.nan)
    sc["b0"] = b0
    rep = {"label": label, "rows": int(len(d)), "games": int(len(np.unique(games))),
           "candidates": {k: evaluate.summarize(v, games) for k, v in sc.items()}}
    rep["paired_vs_b1"] = {m: evaluate.paired(sc["sm1"], sc["b1"], games, blocks, m)
                           for m in ("crps", "nll", "logloss_ref", "brier_ref", "ae")}
    rep["paired_vs_b0"] = {m: evaluate.paired(sc["sm1"], sc["b0"], games, blocks, m)
                           for m in ("crps", "logloss_ref", "brier_ref", "ae")}
    # push behaviour at the whole line (one distribution for every line)
    pp = dists.push_prob(p_sm, lo, l_whole)
    rep["push_check_whole_line"] = {"mean_p_push": float(pp.mean()), "observed_push": float((y == l_whole).mean())}
    # slices (declared)
    masks = evaluate.slice_masks(d, sport, sc["sm1"]["p_over_ref"])
    sl = {}
    for name, m in masks.items():
        if m.sum() < 50:
            continue
        dd = sc["sm1"]["crps"][m] - sc["b1"]["crps"][m]
        mm, a, b = evaluate.boot_mean(dd, games[m])
        sl[name] = {"n": int(m.sum()), "crps_diff_vs_b1": mm, "ci": [a, b],
                    "sm1_hit_vs_p": [float(sc["sm1"]["p_over_ref"][m].mean()), float(sc["sm1"]["hit_ref"][m].mean())]}
    rep["slices"] = sl
    return rep


def gate(rep_test, rep_test2=None):
    """REGISTER.md 7 history gate H for one market."""
    sm = rep_test["candidates"]["sm1"]
    c1 = rep_test["paired_vs_b1"]["crps"]["ci_game"][1] < 0 and rep_test["paired_vs_b0"]["crps"]["ci_game"][1] < 0
    c2 = rep_test["paired_vs_b1"]["logloss_ref"]["diff"] <= 0
    cal = sm["calibration"]
    c3 = abs(cal["in_the_large"]) <= 0.02 and cal["ece"] <= 0.03
    c4 = 0.76 <= sm["cov80"]["mean"] <= 0.84
    c5 = not any(v["n"] >= 300 and v["ci"][0] is not None and v["ci"][0] > 0 for v in rep_test["slices"].values())
    c6 = True if rep_test2 is None else rep_test2["paired_vs_b1"]["crps"]["diff"] < 0
    crit = {"1_crps_beats_b1_and_b0": c1, "2_ref_logloss_not_worse_than_b1": c2, "3_calibration": c3,
            "4_cov80_in_0.76_0.84": c4, "5_no_slice_significantly_worse": c5, "6_nfl_secondary_test": c6}
    return {"criteria": {k: bool(v) for k, v in crit.items()}, "passed": bool(all(crit.values()))}


def run(sport, con, phase="tune", log=print, markets_=None):
    t0 = time.time()
    frames = {hl: (_nfl_fill(_frame(sport, con, hl)) if sport == "nfl" else _frame(sport, con, hl))
              for hl in HALF_LIVES[sport]}
    log(f"[{sport}] frames built in {time.time() - t0:.0f}s")
    out = {"sport": sport, "phase": phase, "register": "sports/research/REGISTER.md (+A1)", "markets": {},
           "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    configs = {}
    any_df = next(iter(frames.values()))
    out["dnp_diagnostic"] = any_df.attrs.get("dnp")
    cfg_path = CONFIG_DIR / f"model_config_{sport}.json"
    for market in markets_ or (NBA_MK if sport == "nba" else NFL_MK):
        if phase == "tune":
            final, rep = run_market(sport, market, frames, log)
            configs[market] = final.config()
            out["markets"][market] = rep
        else:
            cfgs = json.loads(cfg_path.read_text(encoding="utf-8"))
            final = from_config(cfgs["markets"][market])
            df = frames[tuple(final.hl)]
            el, part = model.eligible(df, market), model.partition(df, sport)
            rep = {"test": score_partition(final, df, market, sport, el & (part == "test"), "LOCKED test")}
            if sport == "nfl":
                rep["test2"] = score_partition(final, df, market, sport, el & (part == "test2"),
                                               "secondary locked test (2026 weeks 1-7)")
            rep["gate_H"] = gate(rep["test"], rep.get("test2"))
            mc = cfgs["markets"][market].get("mi_hist")
            if mc:
                mi = from_config(mc)
                rep["market_track_test"] = score_market_track(final, mi, df, market, sport, el & (part == "test"))
            out["markets"][market] = rep
            log(f"[{sport}:{market}] gate {rep['gate_H']}")
    if phase == "tune":
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        cfg = {"sport": sport, "model_version": "sm-1", "frozen_at": out["generated_at"], "markets": configs}
        cfg_path.write_text(json.dumps(cfg, indent=1, sort_keys=True), encoding="utf-8")
        out["config_sha"] = model.artifact_hash(cfg)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"model_study_{sport}_{phase}.json").write_text(json.dumps(out, indent=1, default=float),
                                                               encoding="utf-8")
    log(f"[{sport}] {phase} done in {time.time() - t0:.0f}s")
    return out


def from_config(c):
    f = Fitted(c["market"], tuple(c["half_lives"]))
    f.prior, f.k, f.feats, f.stats = c["prior"], c["k"], c["features"], c["design"]
    f.beta = np.asarray(c["beta"], float)
    f.family, f.par, f.cal = c["family"], c["family_params"], tuple(c["cal"])
    return f


def score_market_track(sm, mi, df, market, sport, mask):
    d = sub(df, mask)
    lo, ks = dists.grid(market)
    y = np.asarray(d[f"y_{market}"], float)
    games = np.asarray(d["game_id"])
    ok = np.ones(len(d), bool)
    for fid in mi.feats:
        if fid not in sm.feats:
            ok &= np.isfinite(np.asarray(d[_feature_cols(d, sport, market, [fid])[0]], float))
    d, y, games = sub(d, ok), y[ok], games[ok]
    p_sm, _ = pmf_for(sm, d, market, sport)
    p_mi, _ = pmf_for(mi, d, market, sport)
    b0_mean = np.nanmean(d.attrs["lags"][market][:, :10], axis=1) if len(d) else np.zeros(0)
    l_ref = np.floor(b0_mean) + 0.5
    a = evaluate.scores(p_mi, lo, y, dists.mean_of(p_mi, ks), l_ref)
    b = evaluate.scores(p_sm, lo, y, dists.mean_of(p_sm, ks), l_ref)
    return {"label": "mi-hist vs sm-1 on rows with game lines (LOCKED test)", "rows": int(len(d)),
            "mi_hist": evaluate.summarize(a, games),
            "paired_vs_sm1": {m: evaluate.paired(a, b, games, None, m) for m in ("crps", "logloss_ref", "ae")}}
