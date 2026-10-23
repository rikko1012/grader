"""Model property checks (sports/model_checks.py) run on today's distribution code (fair.py shapes used by sharp-1 and
sharp-2). Model agents: add your model's dist / produce callables to MODELS / PRODUCERS - nothing else to write."""
import pytest

from sports import fair, model_checks as mc, sharp2
from sports.tests import helpers as h


def _fair(shape, family, market, mean):
    a, b = fair.shape_params(family, market, shape)
    return lambda line: fair.dist_probs(shape, a, b, mean, line)


MODELS = {
    "nba points normal mean 22.3": (_fair("normal", "basketball", "points", 22.3), mc.standard_lines(22.0)),
    "nba rebounds negbin mean 7.4": (_fair("count", "basketball", "rebounds", 7.4), mc.standard_lines(7.0, n=14)),
    "nba threes negbin mean 1.2": (_fair("count", "basketball", "threes", 1.2), mc.standard_lines(1.0, n=2)),
    "nfl receptions negbin mean 4.6": (_fair("count", "football", "rec", 4.6), mc.standard_lines(4.5, n=9)),
    "nfl rec yards normal mean 54": (_fair("normal", "football", "rec_yds", 54.0), mc.standard_lines(54.0, 2.5, 20)),
    "nfl rush yards near zero": (_fair("normal", "football", "rush_yds", 3.0), [-0.5, 0.5, 1.0, 2.5, 5.5, 10.0]),
}


@pytest.mark.parametrize("name", sorted(MODELS))
def test_bounds_sum_push_monotonic(name):
    dist, lines = MODELS[name]
    assert mc.check_bounds_and_sum(dist, lines) == []
    assert mc.check_push(dist, lines) == []
    assert mc.check_monotonic(dist, lines) == []


def test_whole_line_can_push_and_half_cannot():
    dist, _ = MODELS["nba rebounds negbin mean 7.4"]
    assert dist(7.0)[2] > 0 and dist(7.5)[2] == 0


def _sharp2_forecast():
    ev = h.event()
    o = h.offer(line=21.0)
    rungs = [("r1", h.kalshi_rung(19.5, 0.58, 0.60, oid="kalshi:a", observed_at="2026-10-23T22:50:00Z"), None),
             ("r2", h.kalshi_rung(24.5, 0.30, 0.32, oid="kalshi:b", observed_at="2026-10-23T22:50:00Z"), None)]
    fc, _ = sharp2.forecast(o, "o1", rungs, ev, "2026-10-23T22:55:00Z")
    return fc


def test_sharp2_forecast_contract_and_replay():
    fc = _sharp2_forecast()
    assert mc.check_forecast(fc) == []
    assert mc.check_replay(_sharp2_forecast) == []
    assert "between_rungs" in fc.reason_codes and fc.grade is None and fc.stake_units is None and fc.research_only
    assert fc.p_push > 0 and abs(fc.p_over_given_no_push - fc.p_over / (1 - fc.p_push)) < 1e-12


def test_sharp2_alternate_lines_monotonic_from_one_ladder():
    ev = h.event()
    rungs = [("r1", h.kalshi_rung(19.5, 0.58, 0.60, oid="kalshi:a", observed_at="2026-10-23T22:50:00Z"), None),
             ("r2", h.kalshi_rung(24.5, 0.30, 0.32, oid="kalshi:b", observed_at="2026-10-23T22:50:00Z"), None)]
    ps = []
    for ln in (18.5, 19.5, 20.5, 21.5, 22.5, 23.5, 24.5, 25.5):
        fc, _ = sharp2.forecast(h.offer(line=ln, oid=f"pp:{ln}"), "o", rungs, ev, "2026-10-23T22:55:00Z")
        ps.append(fc.p_over)
    assert all(a >= b - 1e-9 for a, b in zip(ps, ps[1:])), ps
