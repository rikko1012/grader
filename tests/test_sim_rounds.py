"""Round simulation (owner 2026-10-20, display only): sim_rounds replays sim.simulate_grade's runs draw for draw, its rows
partition the runs, and the card payload carries it. Every input is a TEST FIXTURE (tests/fixtures/grader_inputs_sample.json)."""
import numpy as np
import pytest

import grader
import sim
import sim_rounds
from tests.test_audit13_grader import _base


@pytest.mark.parametrize("scope,line", [("1", 15.5), ("12", 30.5), ("12", 30.0), ("123", 44.5)])
def test_the_round_view_is_the_match_simulations_own_runs(scope, line):
    g = grader.grade_leg(_base(scope=scope, line=line))
    ms, rv = sim.simulate_grade(g), sim_rounds.round_view(g)
    total, rounds, n = sim_rounds._runs(g)
    up, down = sim.cuts(line)
    assert round(float((total > up).sum() / max(((total > up) | (total < down)).sum(), 1)), 3) == ms["p_more"]
    assert round(float(total.mean()), 1) == ms["mean"] and n == {"1": 1, "12": 2, "123": 3}[scope]
    ot = next(x for x in ms["scenarios"] if x["label"].startswith("any overtime"))
    row = next(x for x in rv["rows"] if x["key"] == "overtime")
    assert row["chance"] == ot["chance"] and row["avg"] == ot["avg"] and row["p_over"] == ot["p_over"]
    assert abs(sum(x["chance"] for x in rv["rows"]) - 1) < 0.003 and rv["weight"] == 0
    assert rv["rounds_p10"] <= rv["rounds_mean"] <= rv["rounds_p90"] and np.all(rounds >= 13)


def test_no_distribution_no_round_view():
    assert sim_rounds.round_view({}) is None and sim_rounds.round_view({"dist": None}) is None


def test_the_member_card_carries_the_round_view():
    import server
    g = grader.grade_leg(_base())
    res = {"grade": g, "simulation": sim.simulate_grade(g)}
    server.protect_recipe(res)
    rv = res["simulation"]["rounds"]
    assert rv == sim_rounds.round_view(g) and [x["key"] for x in rv["rows"]][-1] == "overtime"


def test_series_hs_fills_only_series_whose_maps_reproduce_the_table(monkeypatch):
    """Per-series HS% (owner 2026-10-20, display only): HLTV rows first, then BO3.gg maps; a series is filled only when its
    maps give the table's own per-map values, and never from a map with a missing headshot count."""
    import cs2_scout
    import server
    hl = [dict(match_id="hltv-1", source="hltv", date="2026-10-01", opponent="Team A", number=n, kills=k, headshots=h)
          for n, k, h in ((1, 20, 10), (2, 15, 6), (3, 18, 9))]
    hl += [dict(match_id="hltv-2", source="hltv", date="2026-09-20", opponent="Team B", number=n, kills=k, headshots=h)
           for n, k, h in ((1, 14, 0), (2, 10, 5))]          # 0 HS with 14 kills = a missing count
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back_days=None: hl)
    bo3 = [dict(match_id=9, date="2026-09-10", opponent="team-c", number=n, kills=k, headshots=h) for n, k, h in ((1, 22, 11), (2, 18, 9))]
    pb = {"recent_maps": bo3, "recent_form": {"series": [
        {"date": "2026-10-01", "opponent": "Team A", "values": [20, 15]},
        {"date": "2026-09-20", "opponent": "Team B", "values": [14, 10]},
        {"date": "2026-09-10", "opponent": "Team C", "values": [22, 18]},
        {"date": "2026-09-01", "opponent": "Team D", "values": [20, 20]}]}}
    server.series_hs(pb, "kills")
    a, b, c, d = pb["recent_form"]["series"]
    assert (a["hs"], a["hs_kills"], a["hs_pct"]) == (16, 35, 45.7)
    assert "hs_pct" not in b and "hs_pct" not in d
    assert c["hs_pct"] == 50.0
    pb["recent_form"]["series"][0] = {"date": "2026-10-01", "opponent": "Team A", "values": [21, 15]}   # not the table's maps
    server.series_hs(pb, "kills")
    assert "hs_pct" not in pb["recent_form"]["series"][0]
    pb2 = {"recent_form": {"series": [{"date": "2026-10-01", "opponent": "Team A", "values": [10, 6]}]}}  # an HS line
    server.series_hs(pb2, "hs")
    assert pb2["recent_form"]["series"][0]["hs_pct"] == 45.7
