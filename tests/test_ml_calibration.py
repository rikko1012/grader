"""ml_models/calibration.py - market-calibration research model. Synthetic fixtures only (no network, no database)."""
import json

import numpy as np
import pandas as pd
import pytest

from ml_models import calibration as C

DAY = 86400
T0 = 1767225600  # 2026-01-01 00:00 UTC


def synth(n=2400, slope=1.3, seed=1, start=T0, platform="polymarket"):
    rng = np.random.default_rng(seed)
    L = rng.normal(0.3, 1.0, n)
    y = (rng.random(n) < 1 / (1 + np.exp(-slope * L))).astype(int)
    p = 1 / (1 + np.exp(-L))
    cut = start + np.sort(rng.integers(0, 300 * DAY, n))
    return pd.DataFrame({
        "match_id": [f"hltv:{i}" for i in range(n)], "platform": platform, "y": y, "mkt_p": p,
        "cutoff_ts": cut, "start_ts": cut + 1800, "bo": rng.choice([1, 3, 5], n, p=[.1, .85, .05]),
        "bo_source": "market title", "tier_mean": rng.normal(1530, 90, n), "lan": rng.integers(0, 2, n),
        "mkt_npts48": rng.integers(1, 289, n), "mkt_age_min": rng.uniform(4, 10, n), "k_vol48": np.nan,
        "mkt_max48": np.minimum(p + 0.05, 0.98), "mkt_min48": np.maximum(p - 0.05, 0.02),
        "mkt_mv6": rng.normal(0, 0.1, n), "mkt_mv24": np.where(rng.random(n) < .4, rng.normal(0, .15, n), np.nan),
        "k_spread": np.nan})


def test_market_identity_and_slope_recovery():
    d = synth()
    raw, y = C.raw_from_rows(d), d.y.to_numpy().astype(float)
    m_inf = C.fit(raw, y, "c01_slope", 1e9)
    assert np.allclose(C.predict(m_inf, raw), d.mkt_p, atol=1e-6)        # infinite shrinkage = the market
    m = C.fit(raw, y, "c01_slope", 0.3)
    assert 1.15 < m["w"][0] < 1.45                                       # recovers the planted slope 1.3
    json.dumps(m)                                                         # deployable as plain JSON


@pytest.mark.parametrize("cfg", list(C.CONFIGS))
def test_configs_without_intercept_are_odd(cfg):
    d = synth(800)
    raw, y = C.raw_from_rows(d), d.y.to_numpy().astype(float)
    m = C.fit(raw, y, cfg, 1.0)
    fl = d.copy()
    fl["mkt_p"], fl["mkt_mv6"], fl["mkt_mv24"] = 1 - d.mkt_p, -d.mkt_mv6, -d.mkt_mv24
    fl["mkt_max48"], fl["mkt_min48"] = 1 - d.mkt_min48, 1 - d.mkt_max48
    p1, p2 = C.predict(m, raw), C.predict(m, C.raw_from_rows(fl))
    if "icpt" in C.CONFIGS[cfg]:
        assert np.abs(p1 - (1 - p2)).max() < 0.2
    else:
        assert np.abs(p1 - (1 - p2)).max() < 1e-9


def test_forbidden_and_post_hoc_bo_ignored():
    assert not C.FORBIDDEN & set(C.RAW_COLS)
    d = synth(50)
    d.loc[:9, "bo_source"] = "hltv format / maps played"
    raw = C.raw_from_rows(d)
    assert np.isnan(raw["bo1"][:10]).all() and np.isfinite(raw["bo1"][10:]).all()


def test_blocks_gap_and_first_block():
    d = synth(3000)
    blocks = C.make_blocks(d.cutoff_ts.to_numpy(), d.match_id.to_numpy(), min_train=1500, big=600)
    first = blocks[0]
    ntr = (d.cutoff_ts < first[1] - C.GAP_S).sum()
    assert ntr >= 1500
    prev_start = first[1] - 31 * DAY
    assert (d.cutoff_ts < prev_start - C.GAP_S).sum() < 1500 or prev_start < d.cutoff_ts.min()
    for (_, s, e), (_, s2, _) in zip(blocks, blocks[1:]):
        assert e == s2


def test_walk_forward_never_trains_on_block_or_later():
    d = synth(2600, seed=4)
    blocks = C.make_blocks(d.cutoff_ts.to_numpy(), d.match_id.to_numpy(), min_train=1500)
    o, per = C.walk_forward(d, d, blocks[:1], log=lambda s: None)
    s = blocks[0][1]
    assert per[0]["n_train"] == int((d.cutoff_ts < s - C.GAP_S).sum())
    assert (o.cutoff_ts >= s).all()
    assert per[0]["rules"][C.PRIMARY_RULE]["chosen"]


def test_primary_rows_prefers_polymarket_unless_kalshi_tighter():
    a = synth(3, platform="polymarket")
    k = synth(3, platform="kalshi")
    k["k_spread"] = [0.01, 0.005, 0.03]
    k = k.iloc[:2]
    extra = synth(1, platform="kalshi")
    extra["match_id"] = "hltv:k-only"
    pr = C.primary_rows(pd.concat([a, k, extra], ignore_index=True)).set_index("match_id")
    assert pr.loc["hltv:0", "platform"] == "polymarket"      # tie at one tick -> Polymarket
    assert pr.loc["hltv:1", "platform"] == "kalshi"          # strictly tighter Kalshi spread
    assert pr.loc["hltv:2", "platform"] == "polymarket"
    assert pr.loc["hltv:k-only", "platform"] == "kalshi"


def test_decay_weights_and_predict_rows_list_of_dicts():
    w = C.decay_weights(np.array([0, 90 * DAY, 180 * DAY]), 90.0)
    assert np.allclose(w, [0.25, 0.5, 1.0])
    d = synth(400)
    m = C.fit(C.raw_from_rows(d), d.y.to_numpy().astype(float), "c10_full", 3.0)
    rows = d.head(3).to_dict("records")
    assert np.allclose(C.predict_rows(m, rows), C.predict(m, C.raw_from_rows(d.head(3))))
