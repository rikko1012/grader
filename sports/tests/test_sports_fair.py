"""No-vig, the measured shapes and reading a sharp price at another line."""
import json

import pytest

from sports import config, fair


def test_american_and_no_vig():
    assert fair.american_to_decimal(-110) == pytest.approx(1.9090909)
    assert fair.american_to_decimal(150) == pytest.approx(2.5)
    p, over = fair.no_vig(1.909, 1.909)
    assert p == pytest.approx(0.5) and over == pytest.approx(2 / 1.909)
    assert fair.no_vig(1.55, 2.11)[0] == pytest.approx(0.5765, abs=1e-4)   # lol/center/prices.py's worked example
    assert fair.no_vig(0, 2.0) is None and fair.no_vig("x", 2.0) is None


@pytest.mark.parametrize("shape,a,b,mean,line", [("count", 1, 0.05, 4.2, 4.5), ("count", 1, 0, 1.3, 2.0),
                                                  ("normal", 3.1, 0, 24.0, 24.5), ("normal", 20.2, 0.02, 61, 60.0)])
def test_distribution_sums_to_one(shape, a, b, mean, line):
    o, u, p = fair.dist_probs(shape, a, b, mean, line)
    assert o + u + p == pytest.approx(1.0, abs=1e-6)
    assert (p > 0) == (line == int(line))


def test_solve_mean_recovers_the_chance():
    for shape, a, b, line, p in (("count", 1, 0.05, 4.5, 0.42), ("normal", 3.1, 0, 24.5, 0.55),
                                 ("count", 1, 0.0, 1.0, 0.6)):
        m = fair.solve_mean(shape, a, b, line, p)
        o, u, _ = fair.dist_probs(shape, a, b, m, line)
        assert o / (o + u) == pytest.approx(p, abs=1e-4)


def test_same_half_line_is_the_sharp_chance_itself():
    r = fair.at_line("basketball", "points", "normal", 24.5, 0.58, 24.5)
    assert r["over"] == 0.58 and r["under"] == pytest.approx(0.42) and r["push"] == 0 and r["gap"] == 0
    assert r["method"] == "same line" and not r["far"]


def test_moving_the_line_moves_the_chance_the_right_way():
    base = fair.at_line("basketball", "points", "normal", 24.5, 0.5, 24.5)["over"]
    lower = fair.at_line("basketball", "points", "normal", 24.5, 0.5, 22.5)
    higher = fair.at_line("basketball", "points", "normal", 24.5, 0.5, 26.5)
    assert lower["over"] > base > higher["over"]
    sd = (3.1024 * 24.5) ** 0.5                       # dispersion.json points: var = 3.10 * mean
    assert lower["over"] == pytest.approx(0.5 + 0.5 * __import__("math").erf(2 / sd / 2 ** 0.5), abs=0.01)
    whole = fair.at_line("basketball", "rebounds", "count", 9.5, 0.5, 10.0)
    assert whole["push"] > 0 and whole["over"] + whole["under"] + whole["push"] == pytest.approx(1)
    assert fair.at_line("basketball", "rebounds", "count", 9.5, 0.5, 12.5)["far"]


def test_dispersion_file_is_measured_and_complete():
    d = json.loads(config.DISPERSION_FILE.read_text())
    from sports import markets
    for fam in ("basketball", "football"):
        assert set(d[fam]) == set(markets.MARKETS[fam]), fam
        for k, v in d[fam].items():
            assert v["shape"] == markets.MARKETS[fam][k]["shape"] and v["players"] >= 30 and v["games"] >= 500
    assert d["football"]["pass_td"]["b"] == 0.0         # touchdowns: Poisson fits (dkori's TD check found the same)


def test_fit_matches_a_known_variance_function():
    from sports import fit_dispersion
    groups = [(m, 3.0 * m + 0.01 * m * m, 40) for m in (8, 12, 18, 25, 30)]
    a, b = fit_dispersion.fit(groups, "normal")
    assert a == pytest.approx(3.0, abs=1e-6) and b == pytest.approx(0.01, abs=1e-6)
    groups = [(m, m + 0.06 * m * m, 40) for m in (1, 2, 4, 6, 9)]
    assert fit_dispersion.fit(groups, "count") == (1.0, pytest.approx(0.06))
