"""Factor round 2: the registered sm-2 tune protocol (sports/research/REGISTER.md amendment A5). Worker / workstation
only. Never touches a round-1 locked-test row for scoring and never changes sm-1.

  python -m sports.model.study2 tune <history.db> [nba nfl]       one-at-a-time + joint tests, freeze sm-2 configs
  python -m sports.model.study2 fit <history.db> [nba nfl]        sm-2 forward challenger artifacts (passed markets only)

Per market (A5):
  reference  = sm-1's frozen configuration (half-lives, kept fr-1 features, family) refit on the split's fit rows;
  candidate  = reference + ONE round-2 factor (same family, family parameters refit on the fit rows);
  test       = tune-window CRPS(candidate) - CRPS(reference), game-clustered bootstrap 95% interval (B = 2,000, seed
               20261023); a factor PASSES iff the interval is entirely below 0;
  joint      = reference + every passed factor; sm-2 for the market exists iff the joint interval is entirely below 0;
  lines      = game-line factors (status "available (game-line context)") on top of the stage-A set: NFL on the
               round-1 split; NBA on split R2 (fit rows start < 2026-02-01, evaluation 2026-02-01 .. 2026-07-01) because
               NBA train rows have no game lines; evaluable only with >= 50% line coverage on the evaluation rows;
  cal-1      = re-searched for sm-2 on the evaluation window it was selected on (same grids as sm-1).
Results: sports/research/results/model_study2_<sport>_tune.json; configs sports/model/artifacts/model_config2_<sport>.json
(only markets whose joint test passed).
"""
from __future__ import annotations

import argparse
import json
import time
import warnings
from pathlib import Path

import numpy as np

from .. import features
from . import dists, evaluate, featurize2, model, study

RESULTS = study.RESULTS
CONFIG_DIR = study.CONFIG_DIR
VERSION = "sm-2"
R2_FIT_END, R2_EVAL_END = "2026-02-01", "2026-07-01"
MIN_LINE_COVERAGE = 0.5


def split(df, sport, kind):
    """-> (fit mask, eval mask). kind 'r1' = round-1 train / tune; 'r2' (NBA game lines) = A5 split R2."""
    if kind == "r1":
        part = model.partition(df, sport)
        return part == "train", part == "tune"
    s = np.asarray(df["start_utc"])
    return s < R2_FIT_END, (s >= R2_FIT_END) & (s < R2_EVAL_END)


def _fit(market, hl, feats, family, tr, sport):
    f = study.Fitted(market, hl)
    study.mean_model(f, tr, tr, market, sport, list(feats))
    study.fit_family_params(f, tr, market, sport)
    f.family = family
    return f


def _crps(f, ev, market, sport):
    lo, _ = dists.grid(market)
    p, mu = study.pmf_for(f, ev, market, sport, f.family, (1.0, 1.0))
    return dists.crps(p, lo, np.asarray(ev[f"y_{market}"], float)), p


def _ref_line(ev, market):
    """Pre-game reference line floor(mean of the last <= 10 prior values) + 0.5, from the frame's own lag columns."""
    lags = ev.attrs.get("lags", {}).get(market)
    if lags is None:
        return None
    with np.errstate(all="ignore"):
        return np.floor(np.nanmean(lags[:, :10], axis=1)) + 0.5


def _diag(p, ev, market):
    """Secondary tune metrics at the pre-game reference line: log-loss, ECE / in-the-large, 80% PIT coverage."""
    lo, ks = dists.grid(market)
    y = np.asarray(ev[f"y_{market}"], float)
    lref = _ref_line(ev, market)
    if lref is None:
        return None
    sc = evaluate.scores(p, lo, y, dists.mean_of(p, ks), lref)
    cal = evaluate.reliability(sc["p_over_ref"], sc["hit_ref"])
    return {"logloss_ref": float(np.nanmean(sc["logloss_ref"])), "ece": cal["ece"], "in_the_large": cal["in_the_large"],
            "cov80": float(np.nanmean(sc["cov80"]))}


def _test_set(market, sport, hl, family, base_feats, cands, tr, ev, kind, log):
    """One-at-a-time tests of `cands` on top of base_feats, then the joint test. -> (kept, report)."""
    games = np.asarray(ev["game_id"])
    ref = _fit(market, hl, base_feats, family, tr, sport)
    c_ref, p_ref = _crps(ref, ev, market, sport)
    rep = {"split": kind, "reference_features": list(base_feats), "fit_rows": int(len(tr)), "eval_rows": int(len(ev)),
           "eval_games": int(len(np.unique(games))), "reference_crps": float(c_ref.mean()), "tests": {}}
    kept = []
    for r in cands:
        col = study._feature_cols(ev, sport, market, [r["id"]])[0]
        study._feature_cols(tr, sport, market, [r["id"]])
        v_ev, v_tr = np.asarray(ev[col], float), np.asarray(tr[col], float)
        cov_ev, cov_tr = float(np.isfinite(v_ev).mean()), float(np.isfinite(v_tr).mean())
        t = {"coverage_fit": cov_tr, "coverage_eval": cov_ev}
        if np.nanstd(v_ev) == 0 or np.isfinite(v_ev).sum() < 30 or np.isfinite(v_tr).sum() < 30:
            t.update(not_evaluable="constant or < 30 known values on the fit / evaluation rows", passed=False)
        elif r["status"] == features.G and cov_ev < MIN_LINE_COVERAGE:
            t.update(not_evaluable=f"game lines cover {cov_ev:.0%} < 50% of evaluation rows", passed=False)
        else:
            f = _fit(market, hl, list(base_feats) + [r["id"]], family, tr, sport)
            c, p = _crps(f, ev, market, sport)
            d = evaluate.boot_mean(c - c_ref, games)
            ok = d[2] is not None and d[2] < 0
            t.update(crps_diff=d[0], ci=[d[1], d[2]], beta_std=beta_of(f, r["id"]), passed=bool(ok))
            if ok:
                t["diag"] = _diag(p, ev, market)
                kept.append(r["id"])
        rep["tests"][r["id"]] = t
        log(f"   {r['id']}: " + (f"{t.get('crps_diff'):+.5f} [{t['ci'][0]:+.5f}, {t['ci'][1]:+.5f}]"
                                  if "crps_diff" in t else t.get("not_evaluable", "")) + (" PASS" if t["passed"] else ""))
    rep["passed"] = list(kept)
    if kept:
        fj = _fit(market, hl, list(base_feats) + kept, family, tr, sport)
        cj, pj = _crps(fj, ev, market, sport)
        dj = evaluate.boot_mean(cj - c_ref, games)
        used = dj[2] is not None and dj[2] < 0
        rep["joint"] = {"features": kept, "crps_diff": dj[0], "ci": [dj[1], dj[2]], "used": bool(used),
                        "beta_std": {k: beta_of(fj, k) for k in kept},
                        "diag": _diag(pj, ev, market), "reference_diag": _diag(p_ref, ev, market)}
        log(f"   joint {kept}: {dj[0]:+.5f} [{dj[1]:+.5f}, {dj[2]:+.5f}] {'USED' if used else 'not used'}")
        return (kept if used else []), rep
    return [], rep


def beta_of(f, fid):
    """Standardised GLM coefficient of one feature (design: intercept, then each feature's z column and, when it has
    one, its missing indicator, in feature order)."""
    i = 1
    for k, st in (f.stats or {}).items():
        if k == "F_" + fid:
            return float(f.beta[i])
        i += 1 + int(bool(st.get("ind")))
    return None


def _calibrate(f, ev, market, sport):
    lo, ks = dists.grid(market)
    p_raw, mu = study.pmf_for(f, ev, market, sport, f.family, (1.0, 1.0))
    y = np.asarray(ev[f"y_{market}"], float)

    def cr(c, s):
        return float(dists.crps(dists.transform(p_raw, lo, ks, mu, c, s), lo, y).mean())
    c_best = min(study.C_GRID, key=lambda c: cr(c, 1.0))
    s_best = min(study.S_GRID, key=lambda s: cr(c_best, s))
    c_best = min(study.C_GRID, key=lambda c: cr(c, s_best))
    f.cal = (float(c_best), float(s_best))
    return {"c": f.cal[0], "s": f.cal[1], "crps_raw": cr(1.0, 1.0), "crps_cal": cr(*f.cal)}


def frame(sport, con, hl):
    df = study._frame(sport, con, hl)
    if sport == "nfl":
        study._nfl_fill(df)
    featurize2.augment(df, sport, con, hl)
    return df


def run_tune(sport, con, markets_=None, log=print):
    t0 = time.time()
    cfg1 = json.loads((CONFIG_DIR / f"model_config_{sport}.json").read_text(encoding="utf-8"))
    out = {"sport": sport, "phase": "tune", "model_version": VERSION, "register": "sports/research/REGISTER.md A5",
           "registry": features.REGISTRY_VERSION, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "markets": {}}
    configs = {}
    frames = {}
    for market in markets_ or (study.NBA_MK if sport == "nba" else study.NFL_MK):
        c1 = cfg1["markets"][market]
        hl, fam, base = tuple(c1["half_lives"]), c1["family"], list(c1["features"])
        if hl not in frames:
            frames.clear()
            frames[hl] = frame(sport, con, hl)
        df = frames[hl]
        el = model.eligible(df, market)
        log(f"[{sport}:{market}] sm-1 base {base} family {fam} hl {hl}")
        fit_m, ev_m = split(df, sport, "r1")
        tr, ev = df[el & fit_m].copy(), df[el & ev_m].copy()
        kept_a, rep_a = _test_set(market, sport, hl, fam, base, features.candidates(sport, market, "round2"), tr, ev,
                                  "r1 (round-1 train / tune)", log)
        lk = "r2" if sport == "nba" else "r1"
        fit_m, ev_m = split(df, sport, lk)
        trl, evl = df[el & fit_m].copy(), df[el & ev_m].copy()
        kept_b, rep_b = _test_set(market, sport, hl, fam, base + kept_a, features.candidates(sport, market,
                                                                                             "round2_lines"),
                                  trl, evl, "r2 (fit < 2026-02-01, eval 2026-02-01..07-01)" if lk == "r2"
                                  else "r1 (round-1 train / tune)", log)
        rep = {"sm1": {"half_lives": list(hl), "family": fam, "features": base}, "stage_a": rep_a, "stage_b_lines": rep_b,
               "sm2_features_added": kept_a + kept_b}
        if kept_a or kept_b:
            ftr, fev = (trl, evl) if kept_b else (tr, ev)
            f = _fit(market, hl, base + kept_a + kept_b, fam, ftr, sport)
            rep["cal"] = _calibrate(f, fev, market, sport)
            rep["cal_window"] = "r2 evaluation window" if (kept_b and lk == "r2") else "round-1 tune"
            conf = f.config()
            conf.pop("mi_hist", None)
            configs[market] = conf
            rep["challenger"] = True
        else:
            rep["challenger"] = False
            rep["why_no_challenger"] = "no round-2 factor passed (or the joint test failed): sm-2 = sm-1"
        out["markets"][market] = rep
        log(f"[{sport}:{market}] sm-2 adds {kept_a + kept_b} ({time.time() - t0:.0f}s)")
    frames.clear()
    if configs:
        cfg = {"sport": sport, "model_version": VERSION, "frozen_at": out["generated_at"], "markets": configs,
               "base": f"model_config_{sport}.json (sm-1)"}
        (CONFIG_DIR / f"model_config2_{sport}.json").write_text(json.dumps(cfg, indent=1, sort_keys=True),
                                                                encoding="utf-8")
        out["config_sha"] = model.artifact_hash(cfg)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"model_study2_{sport}_tune.json").write_text(json.dumps(out, indent=1, default=float), encoding="utf-8")
    log(f"[{sport}] study2 tune done in {time.time() - t0:.0f}s")
    return out


def main(argv=None):
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    p = argparse.ArgumentParser(prog="python -m sports.model.study2")
    p.add_argument("phase", choices=["tune", "fit"])
    p.add_argument("history")
    p.add_argument("sports", nargs="*")
    p.add_argument("--markets", nargs="*")
    a = p.parse_args(argv)
    from . import datasets
    con = datasets.connect(Path(a.history))
    for sport in a.sports or ["nba", "nfl"]:
        if a.phase == "tune":
            run_tune(sport, con, a.markets, log=lambda x: print(x, flush=True))
        else:
            from . import shadow
            print(json.dumps(shadow.fit(sport, con), indent=1, default=str)[:2000], flush=True)


if __name__ == "__main__":
    main()
