"""Regression tests for the 2026-10-21 audit, group A (card / grader): audit nos 1+3, 2, 24, 26, 27, 28, 29."""
import argparse
import copy
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import pytest

import cs2_scout
import grade_display
import grader
import pick_verdict

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "grader_inputs_sample.json")


# ---------------------------------------------------------------- 1 / 3. h2h(): an HLTV-only meeting with unread headshots
def _hltv_meeting(hs):
    return [{"match_id": "hltv-1", "number": n, "date": date.today().isoformat(), "team": "A", "opponent": "Foe",
             "map": "de_x", "kills": 20, "headshots": hs[n - 1], "won": True, "regional": False} for n in (1, 2)]


def test_h2h_skips_a_series_whose_stat_is_unread():
    rows = _hltv_meeting([9, None])
    out = cs2_scout.h2h(rows, "Foe", 9.5, "headshots")          # raised TypeError before the fix
    assert out["series"] == [] and out["maps"] == 0 and "hit_rate" not in out
    k = cs2_scout.h2h(rows, "Foe", 30.5, "kills")                # kills are recorded: the meeting still counts
    assert k["hit_rate"] == 1.0 and k["series"][0]["total"] == 40
    full = cs2_scout.h2h(_hltv_meeting([9, 8]), "Foe", 15.5, "headshots")
    assert full["series"][0]["total"] == 17 and full["hit_rate"] == 1.0


def test_hltv_only_meeting_with_unread_headshots_reaches_h2h_safely(monkeypatch):
    import hltv
    monkeypatch.setattr(cs2_scout, "_hltv_series", lambda pb, back=None: _hltv_meeting([None, None]))
    monkeypatch.setattr(hltv, "same_team", lambda a, b: a == b)
    rows = cs2_scout._hltv_h2h({"player": {"nick": "p"}}, [], "Foe")
    assert len(rows) == 2 and cs2_scout.h2h(rows, "Foe", 9.5, "headshots")["series"] == []


# ---------------------------------------------------------------- shared: cs2_scout.run up to the chosen match
class _Stop(BaseException):
    pass


def _run_select(monkeypatch, pb, pages, upcoming, member_opp=None, start=None, odds=None, panda=None):
    monkeypatch.setattr(cs2_scout, "run_player", lambda b, name, args: pb)
    monkeypatch.setattr(cs2_scout, "upcoming_matches", lambda b, tid: list(upcoming.get(tid, [])))
    monkeypatch.setattr(cs2_scout, "_hltv_schedule", lambda names, opp=None: [])
    monkeypatch.setattr(cs2_scout, "match_odds", odds or (lambda *a, **k: {"found": False}))

    def stop(*a, **k):
        raise _Stop()
    monkeypatch.setattr(cs2_scout, "_event_type", stop)
    monkeypatch.setattr(cs2_scout, "event_pool", stop)
    monkeypatch.setattr(cs2_scout, "h2h_rows", stop)
    import pandascore
    monkeypatch.setattr(pandascore, "upcoming", panda or (lambda *a, **k: []))
    monkeypatch.setattr(cs2_scout, "opponent_defense", lambda *a, **k: None)

    class B:
        background = False

        def team(self, slug):
            return {"players": pages.get(slug, [])}

        def team_recent(self, *a, **k):
            return {"results": []}

        def game_players(self, gid):
            return []
    args = argparse.Namespace(cmd="full", name=pb["player"]["nick"], member_opponent=member_opp, member_start=start,
                              opponent=member_opp, match=None, maps=None, line=20.5, prop="kills", scope="12")
    res = {}
    try:
        cs2_scout.run(args, res, B(), 12)
    except _Stop:
        pass
    return res, pb


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 2. a benched (status 2) listing is not "on his roster"
PID = 7
A_PAGE = [{"id": PID, "nickname": "X", "status": 2}] + [{"id": 100 + i, "nickname": f"a{i}", "status": 1} for i in range(5)]


def test_benched_listing_is_not_an_active_listing():
    assert cs2_scout._lists_active(A_PAGE, PID) is False
    assert cs2_scout._lists_active([dict(A_PAGE[0], status=1)], PID) is True
    assert cs2_scout._lists_active(None, PID) is False


def test_typed_card_never_grades_the_team_that_benches_him(monkeypatch):
    now = datetime.now(timezone.utc)
    up = {1: [{"start": _iso(now + timedelta(hours=2)), "opponent": "Team C", "slug": "a-vs-c", "event": "E", "maps": [],
               "team_slug": "a"}],
          2: [{"start": _iso(now + timedelta(hours=6)), "opponent": "Team D", "slug": "b-vs-d", "event": "E", "maps": [],
               "team_slug": "b"}]}
    rm = [{"match_id": 50, "number": n, "date": date.today().isoformat(), "team_id": 2, "team": "Team B", "team_slug": "b",
           "lineup": ["x", "b1", "b2", "b3", "b4"]} for n in (1, 2)]
    pb = {"player": {"nick": "X", "id": PID}, "current_team": "Team A", "team_slug": "a", "team_id": 1,
          "on_profile_roster": cs2_scout._lists_active(A_PAGE, PID), "roster_confirmed": False, "is_standin": False,
          "_matches": [], "recent_maps": rm}
    res, pb = _run_select(monkeypatch, pb, {"a": A_PAGE, "b": []}, up)
    assert res["next_match"]["slug"] == "b-vs-d" and pb["playing_for"] == "Team B"     # was a-vs-c (benched there)
    pb2 = dict(pb, current_team="Team A", team_id=1, playing_for=None, standin_for=None, team_note=None)
    res, pb2 = _run_select(monkeypatch, pb2, {"a": A_PAGE, "b": []}, up, member_opp="Team D")
    assert res["next_match"]["slug"] == "b-vs-d" and not pb2.get("standin_for")         # a move, not a 1U stand-in


# ---------------------------------------------------------------- 24. AWP role for a stand-in: the player's own team's row
def test_standin_awp_role_falls_back_to_his_own_team(monkeypatch):
    import roles
    monkeypatch.setattr(roles, "refresh_hltv", lambda *a, **k: None)
    calls = []

    def awp_role(nick, team):
        calls.append(team)
        return (True, "HLTV weapon stats: 37% of 900 kills") if team == "Team A" else (None, None)
    monkeypatch.setattr(roles, "awp_role", awp_role)
    for pf in (None, "Team B"):
        pb = {"player": {"nick": "sniper1"}, "current_team": "Team A", "profile_team": "Team A", "playing_for": pf}
        d = {"sniping": None}
        cs2_scout.hltv_first_gates(pb, d)
        assert d["sniping"] == 100 and "HLTV" in d["gate_sources"]["awp"]             # stand-in: was None / 'unknown'
    calls.clear()
    pb = {"player": {"nick": "z"}, "current_team": "Team C", "playing_for": "Team C"}
    d = {"sniping": 0}
    cs2_scout.hltv_first_gates(pb, d)
    assert calls == ["Team C"] and d["sniping"] == 0                                      # no other team: no retry


# ---------------------------------------------------------------- 26. opponent-only fallbacks use the book's start
def _pb_mine():
    return {"player": {"nick": "p", "id": 9}, "current_team": "Mine", "team_slug": "mine", "team_id": 11,
            "on_profile_roster": True, "roster_confirmed": True, "_matches": [],
            "recent_maps": [{"date": date.today().isoformat(), "team_id": 11, "team": "Mine", "team_slug": "mine"}]}


def test_polymarket_opponent_only_lookup_uses_the_book_start(monkeypatch):
    now = datetime.now(timezone.utc)
    early, book = _iso(now + timedelta(hours=2)), _iso(now + timedelta(hours=8))
    seen = []

    def odds(team, opp=None, start=None, **k):
        seen.append((team, start))
        if team is not None:
            return {"found": False}
        if start is None:
            return {"found": True, "team": "Other Team", "opponent": "Foe", "start": early, "title": "Other Team vs Foe"}
        return {"found": True, "team": "M1NE", "opponent": "Foe", "start": book, "title": "M1NE vs Foe"}
    res, pb = _run_select(monkeypatch, _pb_mine(), {}, {}, member_opp="Foe", start=book, odds=odds)
    assert res["next_match"]["start"] == book and pb["playing_for"] == "M1NE"           # was Other Team at +2h
    assert (None, None) not in seen                                                      # never the start-less guess


def test_pandascore_opponent_only_lookup_uses_the_book_start(monkeypatch):
    now = datetime.now(timezone.utc)
    early, book = _iso(now + timedelta(hours=2)), _iso(now + timedelta(hours=8))

    def panda(name):
        if name != "Foe":
            return []
        return [{"team": "Foe", "opponent": "Other Team", "start": early, "bo": 3, "event": "E"},
                {"team": "Foe", "opponent": "M1NE", "start": book, "bo": 3, "event": "E"}]
    res, pb = _run_select(monkeypatch, _pb_mine(), {}, {}, member_opp="Foe", start=book, panda=panda)
    assert res["next_match"]["start"] == book and pb["playing_for"] == "M1NE"
    res, pb = _run_select(monkeypatch, _pb_mine(), {}, {}, member_opp="Foe", start=_iso(now + timedelta(hours=20)),
                          panda=panda)
    assert "next_match" not in res and pb["scout"] is True                               # no match at that start: no guess


# ---------------------------------------------------------------- 27. a background refresh never drops a PandaScore match
def test_refresh_keeps_a_pandascore_match_it_cannot_see(monkeypatch):
    class T:
        def __init__(self, target=None, **k):
            self.t = target

        def start(self):
            self.t()
    monkeypatch.setattr(cs2_scout.threading, "Thread", T)

    class B:
        def __init__(self, **k):
            pass

        def close(self):
            pass
    monkeypatch.setattr(cs2_scout, "BO3", B)
    found = {"v": None}
    monkeypatch.setattr(cs2_scout, "run", lambda a, res, b, ttl: res.update(found["v"] or {}))
    key = ("audit27", time.time())
    cs2_scout._CONTEXT[key] = (time.time() - 700, {"next_match": {"source": "pandascore", "start": "x"}})
    cs2_scout._refresh_context(key, argparse.Namespace(), 12)
    assert cs2_scout._CONTEXT.get(key)[1]["next_match"]["source"] == "pandascore"     # was replaced by a SCOUT context
    found["v"] = {"next_match": {"source": "bo3.gg", "start": "y"}}
    cs2_scout._refresh_context(key, argparse.Namespace(), 12)
    assert cs2_scout._CONTEXT.get(key)[1]["next_match"]["source"] == "bo3.gg"          # a real rebuild is stored
    key2 = ("audit27b", time.time())
    cs2_scout._CONTEXT[key2] = (time.time() - 700, {"next_match": {"source": "bo3.gg"}})
    found["v"] = None
    cs2_scout._refresh_context(key2, argparse.Namespace(), 12)
    assert not cs2_scout._CONTEXT.get(key2)[1].get("next_match")                        # other sources: unchanged rule


# ---------------------------------------------------------------- 28. a hot-form cut past 100% never blocks a perf grade
def test_hot_form_past_100_percent_still_grades_on_perf():
    d0 = json.load(open(FIX, encoding="utf-8"))["inputs"]
    base = grader.grade_leg(copy.deepcopy(dict(d0, form_trend=1.5)))
    hot = grader.grade_leg(copy.deepcopy(dict(d0, form_trend=2.0)))                     # ValueError before the fix
    assert hot["p_more"] == base["p_more"] and hot["grade"] == base["grade"] and hot["units"] == base["units"]
    assert hot["lambda_final"] == base["lambda_final"]
    legacy = copy.deepcopy(dict(d0, form_trend=2.0))
    legacy.pop("perf", None)
    with pytest.raises(ValueError):                                                      # old-pipeline lines: unchanged
        grader.grade_leg(legacy)


# ---------------------------------------------------------------- 29. a nearby-line chip shows the card's letter
def _g(pm):
    return {"p_more": round(pm, 3), "p_less": round(1 - pm, 3), "grade": grader.grade_from_p(pm),
            "grade_less": grader.grade_from_p(1 - pm), "units": grader.units_for_p(pm), "units_less": grader.units_for_p(1 - pm),
            "line": 29.5, "line_type": "standard", "lambda_final": 30.2, "less_available": True, "prop": "kills"}


def test_chip_uses_the_unrounded_letter_when_it_has_it():
    g = _g(0.5497)
    v = pick_verdict.verdict(g)
    card = grade_display.annotate(g, v)["model_grade"]
    chip = {"line": 29.5, "side": v["side"], "p": v["p"], "grade": v["grade"], "units": v["units"], "state": v["state"],
            "exact_grade": grade_display.model_grade(g, v)}
    assert card == "LEAN" and grade_display.annotate_item(chip)["model_grade"] == "LEAN"   # was 'B-'
    old = dict(chip)
    old.pop("exact_grade")
    assert grade_display.annotate_item(old)["model_grade"] == "B-"                          # saved chips: as before


def test_ladder_chips_carry_the_card_letter():
    gi = {"prop": "kills", "line": 20.5, "line_type": "standard", "maps_played": 30, "kpr": 0.80, "team_ml": -150,
          "opp_ml": 130, "market_quality": "HARD", "roster_confirmed": True, "on_listed_team": True}
    res = {"grader_inputs": gi, "grade": grader.grade_leg(dict(gi))}
    lad = cs2_scout._ladder(res, {}, argparse.Namespace(line=20.5))
    step0 = next(x for x in lad if x["line"] == 20.5)
    g = res["grade"]
    assert step0["exact_grade"] == grade_display.annotate(g, pick_verdict.verdict(g))["model_grade"]
