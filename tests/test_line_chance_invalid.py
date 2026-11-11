"""A corrupt saved probability must never pass the distribution agreement gate."""
import pytest

from line_chance import cs2_at, lol_at, nb_probs


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), -0.1, 1.1, "invalid", None])
@pytest.mark.parametrize("field", ["over", "under"])
def test_lol_rejects_invalid_saved_chance(bad, field):
    over, under, _ = nb_probs(4.2, 9, 3.5)
    saved = {"over": over, "under": under}
    saved[field] = bad
    assert lol_at(4.2, 9, 3.5, saved["over"], saved["under"], 4.5) is None


@pytest.mark.parametrize("field", ["p", "over", "under", "push"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.1, 1.1, "invalid", None])
def test_cs2_rejects_invalid_saved_chance(field, bad):
    import dist_view
    dist = {"kind": "normal", "lam": 41, "sd": 6}
    pmf = dist_view.pmf(dist)
    over = sum(v for k, v in pmf.items() if k > 38.5)
    x = {"line": 38.5, "dist": dist, "model_outcomes": {"over": over, "under": 1-over, "push": 0},
         "p": round(over, 3), "side": "More", "prop": "headshots", "perf_on": True}
    assert cs2_at(x, 39.5) is not None
    if field == "p":
        x[field] = bad
    else:
        x["model_outcomes"][field] = bad
    assert cs2_at(x, 39.5) is None
