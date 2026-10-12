"""ml_models/gbm.py: protocol plumbing on synthetic rows (no dataset, no network)."""
import json

import numpy as np
import pandas as pd
import pytest

from ml_models import gbm as G


def _rows(n=900, seed=1):
    rng = np.random.default_rng(seed)
    t0 = 1767225600                                   # 2026-01-01
    elo = rng.normal(0, 90, n)
    true = 0.9 * elo / 100 + rng.normal(0, 0.4, n)
    mkt = true + rng.normal(0, 0.3, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-true))).astype(int)
    d = pd.DataFrame({
        "match_id": [f"hltv:{i}" for i in range(n)], "platform": "polymarket", "y": y,
        "cutoff_ts": t0 + np.sort(rng.integers(0, 200 * G.DAY, n)),
        "mkt_p": 1 / (1 + np.exp(-mkt)), "mkt_age_min": 5.0, "mkt_npts48": 100, "bo": 3, "lan": rng.integers(0, 2, n),
        "mkt_max48": 0.99, "mkt_min48": 0.01, "mkt_mv6": rng.normal(0, .1, n), "elo_diff": elo,
        "elo_p": 1 / (1 + np.exp(-elo / 100)), "glicko_rd_a": rng.normal(100, 10, n), "glicko_rd_b": 80.0,
        "games_series_a": 10, "games_series_b": 40, "k_spread": np.nan})
    return d


def test_flip_is_an_involution_and_swaps_pairs():
    X = G.derive(_rows(50))
    F = G.flip(X)
    pd.testing.assert_frame_equal(G.flip(F), X)
    assert np.allclose(F.mkt_logit, -X.mkt_logit)
    assert np.allclose(F.glicko_rd_a, X.glicko_rd_b) and np.allclose(F.hi48, X.lo48)
    assert set(G.FLIP) == set(G.MKT + G.RAT + G.CTX)


def test_symmetrised_prediction_is_team_order_invariant_and_export_matches():
    pytest.importorskip("lightgbm")
    d = _rows()
    X = G.derive(d)
    cfg = dict(name="t", fset="mkt_rat", reg="r4", aug=True, extra={})
    m = G.fit(X.iloc[:600].reset_index(drop=True), d.y.to_numpy()[:600], cfg, 80)
    Xt = X.iloc[600:].reset_index(drop=True)
    p, pf = G.predict(m, Xt), G.predict(m, G.flip(Xt))
    assert np.allclose(p, 1 - pf, atol=1e-12)
    exp = json.loads(json.dumps(G.export_model(m)))
    assert np.allclose(G.predict_exported(exp, Xt), p, atol=1e-12)
    by_it = G.correction(m, Xt, by_iter=True)
    assert by_it.shape[1] == 81 and np.allclose(by_it[:, 0], 0) and np.allclose(by_it[:, -1], G.correction(m, Xt))


def test_monotone_in_elo_difference():
    pytest.importorskip("lightgbm")
    d = _rows()
    X = G.derive(d)
    m = G.fit(X, d.y.to_numpy(), dict(name="t", fset="rat", reg="r8", aug=False, extra={}), 150)
    row = X.iloc[[0] * 41].reset_index(drop=True)
    row["elo_diff"] = np.linspace(-300, 300, 41)
    assert np.all(np.diff(G.correction(m, row)) >= -1e-12)


def test_inner_segments_respect_the_embargo_and_time_order():
    cut = np.sort(np.random.default_rng(3).integers(0, 300 * G.DAY, 2000))
    segs = G.inner_segments(cut)
    assert len(segs) == G.INNER_FOLDS
    for tr, va in segs:
        assert cut[tr].max() < cut[va].min() - G.EMBARGO_S + 1


def test_primary_rule_and_blocks():
    a = _rows(30)
    k = a.iloc[:10].copy()
    k["platform"], k["k_spread"] = "kalshi", [0.01] * 5 + [0.02] * 5
    p = G.primary_rows(pd.concat([a, k]))
    assert p.match_id.is_unique and (p.platform == "polymarket").all()   # Kalshi can only tie the 1c tick
    d = _rows(4000)
    bl = G.make_blocks(d)
    assert bl and bl[0]["n_train"] >= G.MIN_TRAIN_MATCHES
    for b in bl:                                       # >600 rows in a month -> halves
        n = ((d.cutoff_ts >= b["start"]) & (d.cutoff_ts < b["end"])).sum()
        assert n <= G.SPLIT_MONTH_ABOVE or b["block"][-1] in "ab"


def test_threshold_is_chosen_inside_the_window_only():
    y = np.array([1, 0] * 20)
    price = np.full(40, 0.5)
    good = np.where(y == 1, 0.7, 0.3)
    assert G.choose_threshold(good, price, y) is not None
    assert G.choose_threshold(1 - good, price, y) is None              # losing inner bets -> no betting
