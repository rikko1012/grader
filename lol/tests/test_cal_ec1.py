"""ec-1 on LoL (gradecore/chance_cal.py, owner 2026-11-05): the chance members see is the calibrated one, computed once when
the worker saves a forecast; the record keeps the raw chance beside it; the side is never flipped; Render "0" shows raw."""
import pytest

from gradecore import chance_cal as cc
from lol import cal
from lol.center import forecast


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(cc.FLAG, "1")
    cc.reset_cache()
    yield
    cc.reset_cache()


def _rec(po, pu, pp=0.0, stat="kills", **kw):
    return dict({"stat": stat, "platform": "prizepicks", "line": 3.5, "lambda": 3.0, "nb_size": 5.0, "sides": {},
                 "odds_type": "standard", "scope": "12", "p_over": po, "p_under": pu, "p_push": pp}, **kw)


def test_off_is_the_raw_chance(monkeypatch):
    monkeypatch.delenv(cc.FLAG, raising=False)
    assert cal.ec1(_rec(0.7, 0.3)) is None and cal.adjust(_rec(0.7, 0.3)) is None


@pytest.mark.parametrize("po,pu,pp,stat", [(0.62, 0.38, 0.0, "kills"), (0.30, 0.70, 0.0, "kills"), (0.45, 0.48, 0.07, "assists"),
                                           (0.81, 0.19, 0.0, "deaths"), (0.5, 0.5, 0.0, "kills")])
def test_ec1_keeps_the_side_the_push_and_sums_to_one(on, po, pu, pp, stat):
    side = "over" if po >= pu else "under"
    ca = cal.adjust(_rec(po, pu, pp, stat, model_side=side))
    assert ca["v"] == cc.tag() and ca["src"] == "ec-1" and ca["pp"] == round(pp, 4)
    assert abs(ca["po"] + ca["pu"] + ca["pp"] - 1) < 2e-4
    c = cc.side_chance("lol", stat, side, (po if side == "over" else pu) / (po + pu))
    assert abs((ca["po"] if side == "over" else ca["pu"]) - round((1 - pp) * c, 4)) < 1e-9
    sh = forecast.shown(dict(_rec(po, pu, pp, stat), model_side=side, cal_version=ca["v"], cal_p_over=ca["po"],
                             cal_p_under=ca["pu"], cal_p_push=ca["pp"]))
    assert sh["side"] == side and sh["cal"] == ca["v"] and sh["raw"]["p_over"] == po
    g, units = forecast.grade_and_units(sh["p_side"])
    assert sh["grade"] == g and sh["units_policy"] == units


def test_a_side_at_the_floor_is_never_flipped(on):
    # kills Overs calibrate to the 50% floor: Over 50.0 / Under 50.0 still reads as the model's Over
    sh = forecast.shown(dict(_rec(0.51, 0.49), model_side="over", cal_version=cc.tag(), cal_p_over=0.5, cal_p_under=0.5, cal_p_push=0.0))
    assert sh["side"] == "over" and sh["grade_label"] == "Lean Over"
    sh = forecast.shown(dict(_rec(0.49, 0.51), model_side="under", cal_version=cc.tag(), cal_p_over=0.5, cal_p_under=0.5, cal_p_push=0.0))
    assert sh["side"] == "under"


def test_render_zero_shows_the_raw_chance_of_an_ec1_record(monkeypatch):
    f = dict(_rec(0.30, 0.70), model_side="under", cal_version="ec-1:abc", cal_p_over=0.45, cal_p_under=0.55, cal_p_push=0.0,
             grade="A-", units_policy=1.5)
    monkeypatch.setenv(cc.FLAG, "0")
    sh = forecast.shown(f)
    assert sh["cal"] is None and sh["p_under"] == 0.70
    monkeypatch.setenv(cc.FLAG, "1")
    sh = forecast.shown(f)
    assert sh["cal"] == "ec-1:abc" and sh["p_under"] == 0.55 and sh["grade"] == "B-"


def test_lc1_still_wins_where_its_market_is_on(on):
    obj = cal.validate(dict(cal.json.loads(cal.DEFAULT_PATH.read_text(encoding="utf-8"))))
    params = {k: dict(p, on=(k == "kills:prizepicks")) for k, p in obj["params"].items()}
    w = dict(obj, params=params, sha=cal._sha(params))
    assert cal.adjust(_rec(0.62, 0.38), weights=w)["v"].startswith("lc-1")
    assert cal.adjust(_rec(0.62, 0.38, platform="underdog"), weights=w)["v"].startswith("ec-1")


def test_the_board_reports_the_ec1_state(on):
    st = forecast._ec1_status()
    assert st["enabled"] and st["tag"] == cc.tag() and st["letters"] in ("live", "held")
