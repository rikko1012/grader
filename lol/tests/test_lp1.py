"""lp-1 projection candidates (lol/lp1.py) and their walk-forward harness (lol/lp1_eval.py): no look-ahead, the math the
candidates rely on, and that nothing live imports them."""
import math
from pathlib import Path

import numpy as np
import pytest

from lol import lp1, lp1_eval, model


def _games():
    # two domestic leagues A (strong) and B (weak), 30 days of domestic play, then an international event on days 40-41
    g = []
    for d in range(1, 31):
        g.append((d, 1, "a1", "a2", d % 2, "LA"))
        g.append((d, 1, "b1", "b2", d % 2, "LB"))
    for d in (40, 41):
        g.append((d, 1, "a1", "b1", 1, "World Championship"))
        g.append((d, 2, "a2", "b2", 1, "World Championship"))
    return g


def test_home_league_ignores_international_and_window():
    e = lp1.LeagueElo(_games())
    assert e.home("a1", 40) == "LA" and e.home("b2", 40) == "LB"
    assert e.home("a1", 1) is None                       # nothing strictly before day 1
    assert e.home("a1", 31 + lp1.HOME_WINDOW) is None    # window passed


def test_league_offsets_move_only_on_cross_league_games():
    e = lp1.LeagueElo(_games())
    assert e.offset("LA", 35) == 0.0 and e.offset("LB", 35) == 0.0      # domestic games only so far
    assert e.offset("LA", 42) > 0 > e.offset("LB", 42)                  # strong league won abroad
    assert e.offset("LA", 42) == pytest.approx(-e.offset("LB", 42))


def test_no_look_ahead_in_ratings():
    base = _games()
    later = base + [(41, 3, "b1", "a1", 1, "World Championship"), (50, 1, "b1", "a1", 1, "World Championship")]
    e1, e2 = lp1.LeagueElo(base), lp1.LeagueElo(later)
    for d in (35, 40, 41):                                # games on day >= d never reach day d's numbers
        assert e1.p_win("a1", "b1", d) == pytest.approx(e2.p_win("a1", "b1", d))
        assert e1.d_league("a1", "b1", d) == e2.d_league("a1", "b1", d)
    assert e1.p_win("a1", "b1", 42) != pytest.approx(e2.p_win("a1", "b1", 42))


def test_d_league_sign_and_cross_flag():
    e = lp1.LeagueElo(_games())
    dl, cross = e.d_league("a1", "b1", 42)
    assert cross and dl > 0
    assert e.d_league("a1", "a2", 42) == (0.0, False)
    assert e.d_league("a1", "nobody", 42) == (0.0, False)


def test_fit_gap_recovers_the_true_gap():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.3, 20000)
    lam = rng.uniform(2, 6, 20000)
    y = rng.poisson(lam * np.exp(0.5 * x))
    assert lp1.fit_gap(x, lam, y) == pytest.approx(0.5, abs=0.1)
    assert lp1.fit_gap(x, lam, rng.poisson(lam)) == pytest.approx(0.0, abs=0.1)


def test_day_fits_use_only_earlier_days():
    day = np.array([1] * 300 + [5] * 300)
    x = np.r_[np.full(300, 0.2), np.full(300, -0.2)]
    lam = np.full(600, 3.0)
    y = np.r_[np.full(300, 6.0), np.full(300, 6.0)]
    f = lp1.DayFits(day, np.ones(600, bool), x, lam, y, min_n=100)
    g5 = f.g(5)                                   # fitted on day 1 only
    assert g5 > 0
    y2 = y.copy()
    y2[300:] = 0.0                                # change day-5 outcomes: day 5's own g must not move
    assert lp1.DayFits(day, np.ones(600, bool), x, lam, y2, min_n=100).g(5) == g5
    assert f.g(1) == 0.0                          # nothing before day 1 -> no change


def test_sf_ratio_is_exact_against_model_strength():
    for p_old, w_bar, p_new in ((0.35, 0.6, 0.2), (0.5, 0.5, 0.8), (0.7, 0.45, 0.7)):
        ratio, ew = 2.0117, 0.5
        sf_old = model.strength(p_old, w_bar, ratio, ew)
        sf_new = model.strength(p_new, w_bar, ratio, ew)
        assert lp1.sf_ratio(p_old, sf_old, p_new, ratio, ew) == pytest.approx(sf_new / sf_old)
    assert lp1.sf_ratio(0.4, 0.97, 0.4, 2.0, 0.5) == pytest.approx(1.0)
    assert lp1.sf_ratio(None, 0.97, 0.4, 2.0, 0.5) == 1.0


def test_stomp_ratio_identity_and_direction():
    assert lp1.stomp_ratio(0.6, 0.6, -0.0038, 0.0965) == pytest.approx(1.0)
    assert lp1.stomp_ratio(0.5, 0.9, -0.0038, 0.0965) < 1.0     # negative beta: bigger expected stomp, fewer kills


def test_tempo_and_event_sums_use_earlier_days_only():
    assert lp1.tempo_factor(0, 0, 0, 10) == 1.0
    assert lp1.tempo_factor(10, 20, 5, math.inf) == 1.0
    assert lp1.tempo_factor(8, 10, 5, 0) == pytest.approx(0.8)
    ev = lp1.EventSums(["W|2026", "W|2026", "W|2026", None], [10, 11, 12, 10], [2, 4, 100, 9], [3, 3, 3, 3])
    assert ev.sums("W|2026", 10) == (0.0, 0.0, 0)
    assert ev.sums("W|2026", 12) == (6.0, 6.0, 2)               # day-12 outcome not included
    assert lp1.event_key("World Championship", 739900).startswith("World Championship|")
    assert lp1.event_key("LoL Champions Korea", 739900) is None


def test_shrink_and_fit_shrink():
    assert np.allclose(lp1.shrink([2.0, 8.0], [4.0, 4.0], 1.0), [2.0, 8.0])
    assert np.allclose(lp1.shrink([2.0, 8.0], [4.0, 4.0], 0.0), [4.0, 4.0])
    rng = np.random.default_rng(1)
    true = rng.uniform(2, 6, 20000)
    noisy = true * np.exp(rng.normal(0, 0.35, 20000))           # a noisy projection: the best a is below 1
    y = rng.poisson(true)
    assert lp1.fit_shrink(noisy, np.full(20000, 4.0), y, 50.0) < 1.0


def test_nb_ll_matches_model():
    for k, mu, size in ((0, 2.5, 3.0), (7, 4.2, 5.4), (12, 3.0, 8.1)):
        assert float(lp1.nb_ll([k], [mu], size)[0]) == pytest.approx(model.nb_logpmf(k, mu, size))


def _S(p, hit, over, sid, graded=True):
    return {"p": p, "q": p if over else 1 - p, "over": over, "hit": hit, "y": hit if over else 1 - hit, "sid": sid,
            "graded": graded, "offered": True, "lam": 3.0, "unit": (sid, "p", "kills", "1"), "actual": 3.0,
            "stat": "kills", "scope": "1"}


def test_verdict_rule():
    live = [_S(0.7, h, o, f"s{i // 4}") for i, (h, o) in enumerate([(1, True), (0, True), (1, False), (0, False)] * 50)]
    good = [_S(0.5 + 0.001, h, o, f"s{i // 4}") for i, (h, o) in enumerate([(1, True), (0, True), (1, False), (0, False)] * 50)]
    rl = lp1_eval.summarize(live)
    rg = lp1_eval.summarize(good, live)
    assert rg["dLL_vs_live"][2] < 0
    ok, why = lp1_eval.verdict(rg, rl)
    assert ok, why
    few = [dict(s, graded=i % 2 == 0) for i, s in enumerate(good)]
    ok, why = lp1_eval.verdict(lp1_eval.summarize(few, live), rl)
    assert not ok and any("90%" in w for w in why)


def test_live_forecast_never_imports_lp1():
    root = Path(lp1.__file__).resolve().parent
    for f in list((root / "center").glob("*.py")) + [root / "model.py", root / "scout.py"]:
        src = f.read_text(encoding="utf-8")
        assert "lp1" not in src, f
