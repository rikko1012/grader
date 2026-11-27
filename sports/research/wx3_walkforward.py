"""wx-3: NFL weather round 3 (REGISTER.md A14) - expanding walk-forward by season, frozen before any scoring.

usage: SPORTS_DATA_DIR=<dir with model/history.db and model/files/*> python -m sports.research.wx3_walkforward

Each test: the market's live sm-1 structure (features, family, half-lives from sm1_nfl.json) refitted on every row that
started before the test season, versus the same plus one weather feature. Paired CRPS per row, pooled over the four
test seasons, game-clustered bootstrap. Writes sports/research/results/wx3_nfl.json. Research only: nothing here is
imported by the live service and nothing changes a grade.
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import re
import sys
from pathlib import Path

import numpy as np

from .. import config
from ..model import datasets, dists, evaluate, featurize, forward, model, study
from ..model.shadow import _ref_lines

RESULTS = Path(__file__).resolve().parent / "results" / "wx3_nfl.json"
SEASONS = (2023, 2024, 2025, 2026)
B, SEED = 2000, 1126
WET = re.compile(r"rain|shower|drizzle|snow|sleet|flurr|wintry|thunder", re.I)
WIND = re.compile(r"Wind:\s*(?:[A-Za-z]{0,3}\s*)?(\d+)\s*mph", re.I)
CALM = re.compile(r"Wind:\s*Calm", re.I)
TEMP = re.compile(r"Temp:\s*(-?\d+)\s*°?\s*F", re.I)
COVERED = {"dome", "closed"}
TESTS = [("wx.wind_hi", m, -1) for m in ("pass_yds", "rec_yds", "rec")] + \
        [("wx.cold_hi", m, -1) for m in ("pass_yds", "rec_yds", "rec")] + \
        [("wx.wet", m, -1) for m in ("pass_yds", "rec_yds", "rec")] + \
        [("wx.wind_hi", "rush_att", +1), ("wx.wet", "rush_att", +1),
         ("wx.wind_hi", "rush_yds", 0), ("wx.wet", "rush_yds", 0)]


def parse_text(w):
    """pbp `weather` text -> (wind mph | None, temp F | None, wet 0/1)."""
    w = w or ""
    m = WIND.search(w)
    wind = float(m.group(1)) if m else (0.0 if CALM.search(w) else None)
    t = TEMP.search(w)
    return wind, (float(t.group(1)) if t else None), int(bool(WET.search(w)))


def game_weather(hcon, files_dir):
    """{game_id: {"outdoor", "wind", "temp", "wet", "src"}} by the A14 1 rule."""
    text = {}
    for p in sorted(Path(files_dir).glob("play_by_play_*.csv.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                g = r.get("game_id")
                if g and g not in text and (r.get("weather") or "").strip():
                    text[g] = r["weather"]
    out = {}
    for g, roof, temp, wind in hcon.execute("SELECT game_id, roof, temp, wind FROM games WHERE league = 'nfl'"):
        outdoor = (roof or "").lower() not in COVERED and roof not in (None, "")
        pw, pt, wet = parse_text(text.get(g))
        out[g] = {"outdoor": outdoor,
                  "wind": float(wind) if wind is not None else pw,
                  "temp": float(temp) if temp is not None else pt,
                  "wet": wet if text.get(g) else None,
                  "src": "schedule" if wind is not None else ("pbp" if pw is not None else "none")}
    return out


def add_features(df, wx):
    gid = np.asarray(df["game_id"])
    out = {"wx.wind_hi": [], "wx.cold_hi": [], "wx.wet": []}
    for g in gid:
        w = wx.get(g)
        if w is None or not w["outdoor"]:
            # indoor -> 0; a game missing from the schedule (should not happen) -> missing
            v = 0.0 if w is not None else np.nan
            for k in out:
                out[k].append(v)
            continue
        out["wx.wind_hi"].append(max(0.0, w["wind"] - 12.0) if w["wind"] is not None else np.nan)
        out["wx.cold_hi"].append(max(0.0, 32.0 - w["temp"]) if w["temp"] is not None else np.nan)
        out["wx.wet"].append(float(w["wet"]) if w["wet"] is not None else np.nan)
    for k, v in out.items():
        df["F_" + k] = np.asarray(v, float)


def fit_arm(market, hl, family, feats, tr, te):
    f = study.Fitted(market, hl)
    study.mean_model(f, tr, tr, market, "nfl", feats)
    study.fit_family_params(f, tr, market, "nfl")
    f.family = family
    p, mu = study.pmf_for(f, te, market, "nfl", family, (1.0, 1.0))
    return f, p, mu


def cluster_boot(d, games):
    """mean, 95% CI, one-sided p(mean >= 0) - resampling whole games."""
    d, games = np.asarray(d, float), np.asarray(games)
    ok = np.isfinite(d)
    d, games = d[ok], games[ok]
    _, inv = np.unique(games, return_inverse=True)
    G = inv.max() + 1
    s = np.bincount(inv, weights=d, minlength=G)
    n = np.bincount(inv, minlength=G).astype(float)
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, G, size=(B, G))
    bs = s[idx].sum(axis=1) / n[idx].sum(axis=1)
    lo, hi = np.quantile(bs, [0.025, 0.975])
    return float(d.mean()), [float(lo), float(hi)], float((np.sum(bs >= 0) + 1) / (B + 1))


def holm(ps):
    order = np.argsort(ps)
    adj, run = np.empty(len(ps)), 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        adj[i] = run
    return adj


def main():
    hcon = datasets.connect(read_only=True)
    sm1 = forward.load_artifact("nfl")
    wx = game_weather(hcon, config.DATA_DIR / "model" / "files")
    cov = {}
    for g, w in wx.items():
        if w["outdoor"]:
            k = g[:4]
            c = cov.setdefault(k, {"outdoor": 0, "wind_schedule": 0, "wind_pbp": 0, "wind_none": 0, "wet": 0})
            c["outdoor"] += 1
            c["wind_" + w["src"]] += 1
            c["wet"] += int(w["wet"] or 0)
    frames, res = {}, {"register": "REGISTER.md A14 (wx-3)", "sm1": sm1["sha"], "coverage_outdoor_games": cov,
                       "tests": {}}
    for feat, mk, sign in TESTS:
        a1 = sm1["markets"][mk]
        hl = tuple(a1["half_lives"])
        if hl not in frames:
            df = study._nfl_fill(featurize.nfl_frame(hcon, h_opp=hl[0]))
            add_features(df, wx)
            frames[hl] = df
        df = frames[hl]
        base_feats = list(a1["features"])
        el = model.eligible(df, mk) & np.isfinite(df.f(f"y_{mk}"))
        start = np.asarray(df["start_utc"]).astype(str)
        season = np.asarray(df["season"]).astype(int)
        lo, ks = dists.grid(mk)
        rows_d, rows_ll, rows_g, rows_nz, folds = [], [], [], [], {}
        for S in SEASONS:
            te_m = el & (season == S)
            if not te_m.any():
                continue
            first = min(start[te_m])
            tr_m = el & (start < first)
            tr, te = study.sub(df, tr_m), study.sub(df, te_m)
            _, pb, mub = fit_arm(mk, hl, a1["family"], base_feats, tr, te)
            fc, pc, muc = fit_arm(mk, hl, a1["family"], base_feats + [feat], tr, te)
            y = np.asarray(te[f"y_{mk}"], float)
            lref = _ref_lines(df, mk, np.flatnonzero(te_m))
            sb, sc = evaluate.scores(pb, lo, y, mub, lref), evaluate.scores(pc, lo, y, muc, lref)
            d = sc["crps"] - sb["crps"]
            ll = sc["logloss_ref"] - sb["logloss_ref"]
            games = np.asarray(te["game_id"])
            x = np.asarray(te["F_" + feat], float)
            nz = np.isfinite(x) & (x != 0)
            # coefficient of the weather column = the first design column after the base features
            ci = 1 + sum(1 + int(fc.stats[c]["ind"]) for c in ["F_" + b for b in base_feats])
            folds[str(S)] = {"train_rows": int(tr_m.sum()), "test_rows": int(te_m.sum()),
                             "test_games": int(len(np.unique(games))), "rows_feature_nonzero": int(nz.sum()),
                             "crps_diff": float(np.nanmean(d)), "logloss_ref_diff": float(np.nanmean(ll)),
                             "beta_std": float(fc.beta[ci]),
                             "crps_diff_nonzero_rows": float(np.nanmean(d[nz])) if nz.any() else None}
            rows_d.append(d), rows_ll.append(ll), rows_g.append(games.astype(str)), rows_nz.append(nz)
            print(f"{feat} {mk} {S}: {folds[str(S)]}", flush=True)
        D, LL, G, NZ = map(np.concatenate, (rows_d, rows_ll, rows_g, rows_nz))
        m, ci95, p_lo = cluster_boot(D, G)
        mnz, cinz, _ = cluster_boot(D[NZ], G[NZ]) if NZ.any() else (None, None, None)
        betas = [f["beta_std"] for f in folds.values()]
        sign_ok = all(b * sign > 0 for b in betas) if sign else (all(b > 0 for b in betas) or all(b < 0 for b in betas))
        res["tests"][f"{feat}|{mk}"] = {
            "feature": feat, "market": mk, "hypothesised_sign": sign, "folds": folds,
            "pooled_crps_diff": m, "ci95_game": ci95, "p_one_sided": p_lo,
            "pooled_logloss_ref_diff": float(np.nanmean(LL)),
            "folds_crps_better": int(sum(f["crps_diff"] < 0 for f in folds.values())),
            "sign_ok_every_fold": bool(sign_ok),
            "nonzero_rows": int(NZ.sum()), "crps_diff_nonzero_rows": mnz, "ci95_nonzero_rows": cinz}
    keys = list(res["tests"])
    adj = holm(np.array([res["tests"][k]["p_one_sided"] for k in keys]))
    for k, a in zip(keys, adj):
        t = res["tests"][k]
        t["p_holm"] = float(a)
        crit = {"a_holm_p_below_0_05": a < 0.05, "b_logloss_not_worse": t["pooled_logloss_ref_diff"] <= 0,
                "c_better_in_3_of_4_folds": t["folds_crps_better"] >= 3, "d_sign_every_fold": t["sign_ok_every_fold"]}
        t["criteria"], t["passed"] = crit, bool(all(crit.values()))
    RESULTS.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    for k in keys:
        t = res["tests"][k]
        print(f"{k:24s} crps {t['pooled_crps_diff']:+.4f} {t['ci95_game']} p_holm {t['p_holm']:.3f} "
              f"ll {t['pooled_logloss_ref_diff']:+.5f} folds {t['folds_crps_better']}/4 sign {t['sign_ok_every_fold']} "
              f"-> {'PASS' if t['passed'] else 'fail'}")
    print(json.dumps(cov))


if __name__ == "__main__":
    sys.exit(main())
