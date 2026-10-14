"""2026-10-14 audit: each grade records what the SELECTED odds actually did (detail.market_effect) - a Thunderpick label
alone is not projection integration: perf-2 never reads the win chance; the market-quality cap is the stake effect."""
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import grader

GI = json.loads((Path(__file__).parent / "fixtures" / "grader_inputs_sample.json").read_text(encoding="utf-8"))["inputs"]


def _g(**k):
    return grader.grade_leg(dict(GI, **k))


def test_perf_grade_never_uses_the_odds_for_projection_or_probability():
    for mq, src in (("HARD", "polymarket"), ("SOFT", "thunderpick")):
        g = _g(market_quality=mq, odds_provenance={"selected": src})
        me = g["detail"]["market_effect"]
        assert g["projection_source"].startswith("perf-2") and me["projection"] is False and me["probability"] is False
        assert me["selected"] == src and me["market_quality"] == mq
    # the projection and chance are identical whatever the price says (perf-2 takes no market input)
    a, b = _g(team_ml=-400, opp_ml=300), _g(team_ml=300, opp_ml=-400)
    assert (a["lambda_final"], a["p_more"]) == (b["lambda_final"], b["p_more"])


def test_stake_effect_only_when_the_market_cap_binds():
    lines = [x / 2 for x in range(40, 80)]
    seen = set()
    for line in lines:
        hard, soft = _g(line=line, market_quality="HARD"), _g(line=line, market_quality="SOFT")
        me = soft["detail"]["market_effect"]
        assert hard["detail"]["market_effect"]["stake"] is False
        binding = max(me["stake_without_market_rule"].values()) > 1.5
        assert me["stake"] is binding and max(soft["units"], soft["units_less"]) <= 1.5
        if me["stake"]:
            assert (hard["units"], hard["units_less"]) == (me["stake_without_market_rule"]["more"], me["stake_without_market_rule"]["less"])
        seen.add(me["stake"])
    assert seen == {True, False}                                # both cases occur on this player's lines


def test_old_pipeline_uses_a_real_price_and_not_a_missing_one():
    old = dict(GI, perf={"error": "player 'x' not found in HLTV"})
    g = grader.grade_leg(dict(old, team_ml=-200, opp_ml=160, market_quality="HARD"))
    assert g["projection_source"].startswith("legacy") and g["detail"]["market_effect"]["projection"] is True
    g = grader.grade_leg(dict(old, team_ml=None, opp_ml=None, p_team_market=None, market_quality="NONE"))
    assert g["detail"]["market_effect"]["projection"] is False                      # 50/50 default, not the odds
