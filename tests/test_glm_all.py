"""ml_models/glm-all.py - regularized GLM on the market offset. Synthetic fixtures only (no network, no dataset)."""
import importlib.util
import os

import numpy as np
import pandas as pd
import pytest

_P = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml_models", "glm-all.py")
_spec = importlib.util.spec_from_file_location("glm_all", _P)
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

DIFFS = ["relo_last_diff", "form10_adj_diff", "mappool_vs", "lan_gap_x_lan", "standin_diff", "stab30_diff", "mle_e_h2h",
         "lastlu_lt5_diff", "rest_days_diff", "glicko_diff", "form5_diff", "form10_diff", "form5_adj_diff",
         "lan_gap_diff", "stab90_diff", "m7_diff", "home_diff", "mle_a_rating", "mle_b_mappool", "mle_c_form",
         "mle_d_lan_x_rating", "xgap", "mkt_mv6", "mkt_mv24"] + \
        [f"{k}_diff_k{x}" for k in ("elo", "relo") for x in (10, 16, 24, 32, 48)]
AB = ["games_series", "maps60", "series365", "lastlu_overlap", "starters_lt5", "glicko_rd", "n_starters", "elo_known"]


def synth(n=1200, seed=1, signal=0.0):
    """Rows with a market price and features; y drawn from the market logit (+ signal * mappool_vs)."""
    rng = np.random.default_rng(seed)
    d = {"mkt_p": 1 / (1 + np.exp(-rng.normal(0, 1, n)))}
    for c in DIFFS:
        d[c] = rng.normal(0, 1, n)
    for c in AB:
        d[c + "_a"], d[c + "_b"] = rng.integers(0, 50, n).astype(float), rng.integers(0, 50, n).astype(float)
    for k in ("elo", "relo", "glicko"):
        d[f"{k}_p"] = np.clip(d["mkt_p"] + rng.normal(0, 0.1, n), 0.02, 0.98)
    d["mkt_max48"] = np.minimum(d["mkt_p"] + 0.05, 0.98)
    d["mkt_min48"] = np.maximum(d["mkt_p"] - 0.05, 0.02)
    d["mle_e_h2h_n"] = rng.integers(0, 5, n).astype(float)
    d["mkt_npts48"], d["mkt_age_min"] = rng.integers(1, 400, n).astype(float), rng.uniform(0, 30, n)
    d["bo"] = rng.choice([1, 3, 5], n).astype(float)
    d["lan"] = rng.integers(0, 2, n).astype(float)
    d["tier_mean"], d["k_spread"], d["k_vol48"] = rng.normal(1500, 80, n), np.full(n, np.nan), np.full(n, np.nan)
    d["o_npts48"], d["o_spread"] = rng.integers(1, 400, n).astype(float), rng.uniform(0.01, 0.05, n)
    z = np.log(d["mkt_p"] / (1 - d["mkt_p"])) + signal * d["mappool_vs"]
    d["y"] = (rng.uniform(size=n) < 1 / (1 + np.exp(-z))).astype(float)
    d["platform"] = np.array(["polymarket"] * n, dtype=object)
    return d


def swap(d):
    """The same rows with team A and B exchanged."""
    s = dict(d)
    s["mkt_p"] = 1 - d["mkt_p"]
    for k in ("elo", "relo", "glicko"):
        s[f"{k}_p"] = 1 - d[f"{k}_p"]
    s["mkt_max48"], s["mkt_min48"] = 1 - d["mkt_min48"], 1 - d["mkt_max48"]
    for c in DIFFS:
        s[c] = -d[c]
    for c in AB:
        s[c + "_a"], s[c + "_b"] = d[c + "_b"], d[c + "_a"]
    s["y"] = 1 - d["y"]
    return s


@pytest.mark.parametrize("cfg", G.CONFIGS_R1 + G.CONFIGS_R2_NEW, ids=lambda c: c["name"])
def test_antisymmetric_any_config(cfg):
    d = synth(400)
    base, mods = G.raw_arrays(d)
    mdl = G.fit_model(base, mods, d["y"], cfg, G.LAMBDAS[-3])
    p = G.predict_model(mdl, base, mods)
    bs, ms = G.raw_arrays(swap(d))
    assert np.allclose(G.predict_model(mdl, bs, ms), 1 - p, atol=1e-9)


def test_huge_penalty_is_the_market():
    d = synth(500)
    base, mods = G.raw_arrays(d)
    for cfg in G.CONFIGS_R1:
        b = G.fit_model(base, mods, d["y"], cfg, 1e6)["beta"]
        assert np.allclose(b, 0, atol=1e-6)
    assert np.allclose(G.predict_model(None, base, mods), d["mkt_p"], atol=1e-9)


def test_lasso_kkt_and_ridge_gradient():
    d = synth(800, signal=0.4)
    base, mods = G.raw_arrays(d)
    names = G.config_names(G.ALL_CONFIGS["S2_core|offset|lasso"])
    X, _ = G.design(base, mods, names)
    lam = 0.003
    b = G.fit_enet(X, d["y"], base["m"], lam, 1.0)
    g = X.T @ (G._sig(base["m"] + X @ b) - d["y"]) / len(d["y"])
    nz = np.abs(b) > 1e-8
    assert np.allclose(g[nz], -lam * np.sign(b[nz]), atol=2e-5)          # KKT on the active set
    assert np.all(np.abs(g[~nz]) <= lam + 2e-5)                           # and off it
    br = G.fit_enet(X, d["y"], base["m"], lam, 0.0)
    gr = X.T @ (G._sig(base["m"] + X @ br) - d["y"]) / len(d["y"]) + lam * br
    assert np.max(np.abs(gr)) < 1e-8
    assert b[names.index("mappool_vs")] > 0                               # recovers the planted signal


def test_weights_scale_invariant_and_decay():
    d = synth(600)
    base, mods = G.raw_arrays(d)
    X, _ = G.design(base, mods, ["mappool_vs", "xgap"])
    b1 = G.fit_enet(X, d["y"], base["m"], 0.01, 0.0, w=np.ones(600))
    b2 = G.fit_enet(X, d["y"], base["m"], 0.01, 0.0)
    assert np.allclose(b1, b2)
    w = G.decay_weights(np.array([0, 90 * 86400, 180 * 86400.0]), 180 * 86400.0, 180)
    assert np.isclose(w.mean(), 1) and np.isclose(w[0] / w[2], 0.5)
    assert G.decay_weights(np.arange(3.0), 3.0, None) is None


def test_predict_rows_matches_arrays():
    d = synth(50)
    base, mods = G.raw_arrays(d)
    mdl = G.fit_model(base, mods, d["y"], G.ALL_CONFIGS["S3_all|free|ridge"], 0.01)
    rows = [{k: (v[i] if not isinstance(v[i], float) or np.isfinite(v[i]) else None) for k, v in d.items()}
            for i in range(50)]
    assert np.allclose(G.predict_rows(mdl, rows), G.predict_model(mdl, base, mods))
    ens = {"ensemble": [mdl, None]}
    z = (np.log(G.predict_model(mdl, base, mods) / (1 - G.predict_model(mdl, base, mods))) + base["m"]) / 2
    assert np.allclose(G.predict_model(ens, base, mods), 1 / (1 + np.exp(-z)))


def test_standardisation_uses_training_rows_only():
    d = synth(300)
    base, mods = G.raw_arrays(d)
    names = ["xgap", "m*liq"]
    _, st = G.design(G._subset(base, np.arange(200)), G._subset(mods, np.arange(200)), names)
    _, st_all = G.design(base, mods, names)
    assert st["rms"]["xgap"] == pytest.approx(np.sqrt(np.mean(d["xgap"][:200] ** 2)))
    assert st["rms"]["xgap"] != st_all["rms"]["xgap"]
    assert st["mod_mean"]["liq"] == pytest.approx(np.mean(np.log1p(d["mkt_npts48"][:200])))


def test_blocks_split_big_months_and_wait_for_training():
    t0 = pd.Timestamp("2026-01-01", tz="UTC").timestamp()
    day = 86400
    cut = np.concatenate([t0 + np.linspace(0, 150 * day, 1600),                   # Jan..May
                          pd.Timestamp("2026-06-01", tz="UTC").timestamp() + np.linspace(0, 29 * day, 700)])
    b = G.make_blocks(cut, cut)
    labs = [x[0] for x in b]
    assert labs[0] in ("2026-05", "2026-06a") and "2026-06a" in labs and "2026-06b" in labs
    for lab, s, e in b:
        assert (cut < s - day).sum() >= 1500


def test_inner_chunks_keep_gap_and_same_validation_rows():
    d = synth(900)
    base, mods = G.raw_arrays(d)
    cut = np.sort(np.random.default_rng(2).uniform(0, 300 * 86400, 900))
    mid = np.array([f"m{i}" for i in range(900)])
    bounds = G.chunk_bounds(cut, 3, 0.12)
    cfgs = [G.ALL_CONFIGS["P1_blend|offset|ridge"]]
    a = G.inner_select(base, mods, d["y"], cut, np.ones(900, bool), mid, bounds, cfgs, prefix=("own", None))
    b = G.inner_select(base, mods, d["y"], cut, np.ones(900, bool), mid, bounds, cfgs, hl=180, prefix=("own", 180))
    assert (a["mid"] == b["mid"]).all()
    for t_lo, t_hi in bounds:
        assert t_lo < t_hi
    assert len(a["y"]) == sum(((cut >= lo) & (cut < hi)).sum() for lo, hi in bounds)


def test_primary_rule_prefers_polymarket():
    df = pd.DataFrame({"match_id": ["a", "a", "b", "c"], "platform": ["polymarket", "kalshi", "kalshi", "polymarket"],
                       "mkt_npts48": [1, 2, 3, 4], "k_spread": [np.nan, 0.01, 0.02, np.nan],
                       "cutoff_ts": [1, 1, 2, 3]})
    p = G.primary_rows(df)
    assert list(zip(p.match_id, p.platform)) == [("a", "polymarket"), ("b", "kalshi"), ("c", "polymarket")]
    assert p.o_spread.iloc[0] == 0.01 and np.isnan(p.o_spread.iloc[1])
