"""ml_models/ratings.py - ratings-stack research models. Synthetic fixtures only (no network, no database)."""
import math
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import ml_features as F
from ml_models import ratings as R


def rows(n=600, seed=1, edge=0.0):
    """Synthetic dataset rows: a market price, ratings correlated with the truth, outcome drawn from the truth."""
    rng = np.random.default_rng(seed)
    z = rng.normal(0, 1.0, n)
    rating = z + rng.normal(0, 1.0, n)
    mkt = z * (1 - edge) + rng.normal(0, 0.3, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-z))).astype(float)
    t0 = 1_760_000_000
    return pd.DataFrame({
        "mkt_p": 1 / (1 + np.exp(-mkt)), "bo": rng.choice([1, 3, 3, 5], n),
        "elo_p": 1 / (1 + np.exp(-rating)), "relo_p": np.where(rng.random(n) < 0.05, np.nan, 1 / (1 + np.exp(-rating))),
        "relo_last_diff": rating * 100, "glicko_p": 1 / (1 + np.exp(-rating)), "lan": rng.integers(0, 2, n),
        "tier_mean": rng.normal(1550, 60, n), "games_series_a": rng.integers(0, 80, n),
        "games_series_b": rng.integers(0, 80, n), "mkt_age_min": rng.integers(0, 300, n),
        "cutoff_ts": t0 + np.arange(n) * 3600 * 8, "y": y, "match_id": [f"hltv:{i}" for i in range(n)],
        **{f"bt_diff_hl{h}": rating * 0.5 for h in R.BT_HALF_LIVES},
        **{f"elo_diff_k{k}": rating * 150 for k in (10, 16, 24, 32, 48)},
    })


def test_p_series_matches_ml_features():
    for p in (0.1, 0.35, 0.5, 0.62, 0.9):
        for bo in (1, 3, 5):
            assert R.p_series(p, bo) == pytest.approx(F.p_series(p, bo), abs=1e-12)


def test_stack_with_huge_penalty_is_the_market():
    d = rows()
    m = R.fit(d, d.y, R.CONFIGS_R1["stack_ER_int"], 1e9)
    assert np.allclose(R.predict(m, d), d.mkt_p, atol=1e-6)


def test_market_and_blend_zero_weight_are_the_market():
    d = rows()
    for name in ("market", "blend_p_ER", "blend_logit_ER"):
        m = R.fit(d, d.y, R.CONFIGS_R1[name], 0.0)
        assert np.allclose(R.predict(m, d), d.mkt_p, atol=1e-9)


def test_stack_finds_rating_signal_when_market_is_weak():
    d = rows(n=3000, edge=0.6, seed=4)
    m = R.fit(d, d.y, R.CONFIGS_R1["stack_E"], 1.0)
    w = dict(zip(m["names"], m["w"]))
    assert w["elo"] > 0.1


def test_inner_folds_train_strictly_before_validation_with_gap():
    d = rows(n=800)
    cut = d.cutoff_ts.to_numpy(float)
    folds = R.inner_folds(cut)
    assert len(folds) == R.INNER_FOLDS
    for tr, val in folds:
        assert cut[tr].max() < cut[val].min() - 86400
        assert not set(tr) & set(val)


def test_fit_window_only_uses_training_rows():
    d = rows(n=900)
    win = R.fit_window(d.iloc[:600], d.y.iloc[:600], d.cutoff_ts.iloc[:600], R.config_space(("r1", "r2")))
    d2 = d.copy()
    d2.loc[600:, "y"] = 1 - d2.loc[600:, "y"]          # changing later outcomes cannot change the window
    win2 = R.fit_window(d2.iloc[:600], d2.y.iloc[:600], d2.cutoff_ts.iloc[:600], R.config_space(("r1", "r2")))
    p1, p2 = R.predict_window(win, d.iloc[600:]), R.predict_window(win2, d.iloc[600:])
    for k in p1:
        assert np.allclose(p1[k], p2[k])


def test_bet_returns_arithmetic():
    r = R.bet_returns([0.70, 0.30, 0.52], [0.60, 0.45, 0.50], [1, 1, 0], 0.03)
    # row 0: back A at 0.61 and win -> 0.39/0.61; row 1: back B at 0.56 and lose -> -1; row 2: no bet
    assert len(r) == 2
    assert r[0] == pytest.approx(0.39 / 0.61)
    assert r[1] == -1.0


def test_bt_uses_only_games_before_day_and_orders_teams():
    games = []
    rng = np.random.default_rng(0)
    strength = {1: 1.0, 2: 0.0, 3: -1.0}
    d0 = date(2026, 1, 1).toordinal()
    for i in range(600):
        a, b = rng.choice([1, 2, 3], 2, replace=False)
        p = 1 / (1 + math.exp(-(strength[a] - strength[b])))
        games.append((d0 + i // 5, int(a), int(b), float(rng.random() < p)))
    day = date.fromordinal(d0 + 100)
    bt = R.BTRatings(games, 90)
    th = bt.asof(day)
    assert th[1] > th[2] > th[3]
    # a game ON the day (or later) never changes the rating as of that day
    extra = games + [(day.toordinal(), 3, 1, 1.0)] * 50
    extra.sort(key=lambda g: g[0])
    th2 = R.BTRatings(extra, 90).asof(day)
    assert all(th[t] == pytest.approx(th2[t], abs=1e-12) for t in th)


def test_primary_view_one_row_per_match_polymarket_first():
    df = pd.DataFrame({"platform": ["polymarket", "kalshi", "kalshi", "polymarket"],
                       "match_id": ["hltv:1", "hltv:1", "hltv:2", "hltv:3"]})
    v = R.primary_view(df)
    assert sorted(v.match_id) == ["hltv:1", "hltv:2", "hltv:3"]
    assert v.set_index("match_id").loc["hltv:1", "platform"] == "polymarket"


def test_blocks_split_big_months_and_wait_for_min_train(monkeypatch):
    monkeypatch.setattr(R, "MIN_TRAIN", 50)
    monkeypatch.setattr(R, "BLOCK_SPLIT_N", 40)
    base = pd.Timestamp("2026-01-01", tz="UTC")
    ts = [base + pd.Timedelta(hours=12 * i) for i in range(200)]       # 2 per day -> ~60 per month
    d = pd.DataFrame({"cutoff_ts": [t.timestamp() for t in ts], "match_id": [f"m{i}" for i in range(200)]})
    b = R.make_blocks(d)
    assert b[0][0] == "2026-02a"            # January (60 rows) gives >= 50 training matches only from February on
    assert [x[0] for x in b][:2] == ["2026-02a", "2026-02b"]
