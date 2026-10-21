"""sh-1 assists shape mixture (lol/sh1.py, EVALUATION.md A13)."""
import math

import pytest

from lol import model, sh1


@pytest.mark.parametrize("mu,s,w", [(7.2, 3.4, 0.3), (14.5, 6.8, 0.6), (21.0, 8.1, 0.1), (0.4, 3.0, 0.5)])
def test_pmf_sums_to_one(mu, s, w):
    assert sum(sh1.pmf(k, mu, s, w) for k in range(0, 600)) == pytest.approx(1.0, abs=1e-9)
    for line in (0.5, 4.5, 7.0, 12.5):
        o, u, p = sh1.probs(mu, s, w, line)
        assert o + u + p == pytest.approx(1.0, abs=1e-12)
        assert min(o, u, p) >= 0


def test_normal_part_renormalised_over_nonnegative_totals():
    assert sum(sh1.normal_pmf(k, 2.0, 3.0) for k in range(0, 200)) == pytest.approx(1.0, abs=1e-12)
    assert sh1.normal_pmf(-1, 2.0, 3.0) == 0.0


@pytest.mark.parametrize("mu,s", [(30.0, 60.0), (45.0, 50.0)])
def test_mean_equals_mu_when_the_normal_tail_is_negligible(mu, s):
    for w in (0.0, 0.3, 0.6):
        assert sh1.mean(mu, s, w) == pytest.approx(mu, rel=1e-5)


@pytest.mark.parametrize("mu,s", [(7.2, 3.4), (14.5, 6.8)])
def test_nb_part_mean_is_mu_and_shift_is_only_the_cut_tail(mu, s):
    assert sh1.mean(mu, s, 0.0) == pytest.approx(mu, abs=1e-6)
    # the mixture mean moves by w x (mean of the renormalised normal - mu) and no more
    sd = math.sqrt(mu + mu * mu / s)
    shift_n = sum(k * sh1.normal_pmf(k, mu, s) for k in range(0, int(mu + 20 * sd))) - mu
    for w in (0.2, 0.5):
        assert sh1.mean(mu, s, w) - mu == pytest.approx(w * shift_n, abs=1e-6)
    assert 0 <= shift_n < 0.15 * sd


@pytest.mark.parametrize("mu,s", [(7.2, 3.395), (13.9, 6.814), (20.4, 8.111), (3.1, 3.023)])
def test_w0_reproduces_model(mu, s):
    for line in (2.5, 5.0, 7.5, 13.5, 20.0):
        assert sh1.probs(mu, s, 0.0, line) == model.probs(mu, s, line)
    assert sh1.interval(mu, s, 0.0) == model.interval(mu, s)
    for k in range(0, 40):
        assert sh1.pmf(k, mu, s, 0.0) == model.nb_pmf(k, mu, s)


def test_mixture_moves_under_mass_toward_less_skew():
    # less right skew: at a line just below the mean the Under chance falls
    mu, s = 7.2, 3.4
    line = math.floor(mu) - 0.5
    assert sh1.probs(mu, s, 0.5, line)[1] < sh1.probs(mu, s, 0.0, line)[1]
