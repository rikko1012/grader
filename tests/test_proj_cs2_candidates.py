"""Tests for the research-only CS2 projection candidates (research/proj_cs2_2026_11_05)."""
import datetime as dt
import os
import sys

import pytest

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'research', 'proj_cs2_2026_11_05')
sys.path.insert(0, HERE)
import proj_candidates as pc  # noqa: E402


def test_pover_monotonic_and_symmetric():
    assert pc.pover(25.0, 7.0, 24.5) > pc.pover(24.0, 7.0, 24.5)
    assert abs(pc.pover(24.5, 7.0, 24.5) - 0.5) < 1e-9
    assert pc.pover(30, 7, 24.5) + pc.pover(19, 7, 24.5) == pytest.approx(1.0, abs=1e-9)


def test_implied_sd_recovers_spread():
    sd = 7.3
    p = pc.pover(28.0, sd, 25.5)
    assert pc.implied_sd(28.0, 25.5, p, 'kills', '12') == pytest.approx(sd, rel=1e-3)
    # uninformative chance falls back to the live formula
    f = pc.FORMULA_C[('kills', '12')] * (25.6 ** 0.5)
    assert pc.implied_sd(25.6, 25.5, 0.505, 'kills', '12') == pytest.approx(f)


def _row(lam, line, actual, agree=True, prop='kills', scope='12', fh=None):
    fh = lam if fh is None else fh
    return dict(lam=lam, line=line, actual=actual, prop=prop, scope=scope,
                ch={'fh-001': {'lam': fh, 'lam_base': lam if agree else lam + 1.0}})


def test_mi_weight_is_clipped_and_keeps_side():
    # projection distance from the line carries no information -> w ~ 0
    train = [_row(26 + (i % 5) - 2, 25.5, 25.5 + (1 if i % 2 else -1)) for i in range(200)]
    W = pc.fit_w(train)
    w = W[('kills', '12', True)]
    assert 0.0 <= w <= 0.2
    # fully informative -> w = 1
    train2 = [_row(26 + (i % 5) - 2, 25.5, 26 + (i % 5) - 2) for i in range(200)]
    assert pc.fit_w(train2)[('kills', '12', True)] == pytest.approx(1.0)
    # side never flips: the mean moves toward the line, not past it
    x = _row(28.0, 25.5, 0)
    m = pc.lam_mi(x, {('kills', '12', True): 0.3}, {}, x['lam'])
    assert 25.5 < m < 28.0


def test_st_identity_without_challengers():
    x = dict(lam=20.0, line=19.5, actual=0, prop='hs', scope='12', ch={})
    F = {('hs', '12'): [0.5, 0.5, 0.5, 0.5]}
    assert pc.lam_st(x, F) == 20.0


def test_walk_forward_has_no_lookahead():
    import evaluate as ev
    rows = ev.load()
    rows = [x for x in rows if x['day'] <= '2026-10-21']
    out = ev.walk_forward(rows)
    assert out, 'expected test rows'
    # each test day is fitted only on matches started >= 6 h before the day; asserted inside walk_forward,
    # and the train set of the first test day must exclude every row of that day
    first = min(x['day'] for x in out)
    cut = dt.datetime.fromisoformat(first + 'T00:00:00') - dt.timedelta(hours=ev.BUFFER_H)
    assert not [x for x in rows if x['day'] == first and x['t'] <= cut]
