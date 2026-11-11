"""av-cs2-1 (owner 2026-11-09: every projection accounts for whether a player is playing): a player his own team's BO3.gg
page lists as benched (status 2) - not a stand-in, graded for that same team - shows no side, letter or stake on the board
or the card and is never logged; players who play are untouched; a failed team-page read never holds anyone.
Every player and team here is a TEST FIXTURE."""
import json
import os
import sys
import time
import types
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import cs2_scout
import picklog
import prizepicks
import server
import underdog
from tests.test_audit13_site import START, _grade_stub, card, get, iso, site  # noqa: F401 (fixtures)


def _pb(benched=("benchy",), nick="benchy", **kw):
    pb = {"player": {"nick": nick, "slug": nick, "id": 7}, "benched": None if benched is None else list(benched), "profile_team": "Team A",
          "current_team": "Team A", "playing_for": "Team A", "is_standin": False}
    pb.update(kw)
    return pb


# ------------------------------------------------------------------ the rule
def test_benched_on_his_own_team_is_held():
    assert server.benched_hold(_pb())
    assert server.benched_hold(_pb(playing_for=None))                       # no other team named: his own team


def test_not_held_when_he_plays():
    assert not server.benched_hold(_pb(benched=()))                         # active on the page
    assert not server.benched_hold(_pb(benched=("someone",)))               # another player is benched
    assert not server.benched_hold(_pb(standin_for="Team B", playing_for="Team B"))   # stand-in for another team
    assert not server.benched_hold(_pb(is_standin=True))
    assert not server.benched_hold(_pb(playing_for="Team B"))               # graded for another team (move / loan)
    assert not server.benched_hold({})


def test_failed_team_page_read_never_holds():
    # cs2_scout builds pb["benched"] from the page it read; an unreadable page = empty roster = empty benched list
    assert not server.benched_hold(_pb(benched=[]))
    assert not server.benched_hold(_pb(benched=None))


# ------------------------------------------------------------------ board: held row is hidden and never logged
def test_board_holds_a_benched_line_and_logs_nothing(site):
    fut = iso(datetime.now(timezone.utc) + timedelta(hours=3))
    row = {"id": "ud-benchy", "scope": "12", "player": "benchy", "team": "Team A", "opponent": "Team B", "matchup": "A vs B",
           "start": fut, "prop": "kills", "line": 30.5, "alt": False, "under_available": True, "over_available": True}
    site.setattr(underdog, "board", lambda wait=False: {"at": time.time(), "rows": [dict(row)], "error": None})
    site.setattr(prizepicks, "board", lambda wait=False, now=None: {"state": "ok", "at": time.time(), "rows": [], "error": None})
    site.setattr(server, "_board_load", lambda: None)
    site.setattr(server, "_board_save", lambda: None)
    for name in ("maybe_batch", "settle_all"):
        site.setattr(server.slip_batches, name, lambda *a, **k: None)
    site.setattr(server.slips, "log_recommended", lambda *a, **k: None)
    site.setattr(server.roles, "refresh_hltv", lambda *a, **k: None)
    site.setattr(server.roles, "refresh", lambda *a, **k: None)
    res = _grade_stub(line=30.5)
    res["player_block"] = _pb()
    site.setattr(cs2_scout, "grade", lambda *a, **k: json.loads(json.dumps(res)))
    logged = []
    site.setattr(picklog, "log_async", lambda *a, **k: logged.append(a))
    # an earlier graded side on the board for this line (he was active then)
    server.BOARD["ud-benchy"] = {"at": 0, "side": "Less", "grade": "B+", "p": 0.64, "units": 1.0, "graded_line": 30.5,
                                 "graded_model": picklog.MODEL_VERSION, "nick": "benchy"}

    class Stop(BaseException):
        pass
    n = [0]

    def fake_sleep(s):
        n[0] += 1
        if n[0] > 1:
            raise Stop()
    site.setattr(server, "time", types.SimpleNamespace(time=time.time, sleep=fake_sleep))
    with pytest.raises(Stop):
        server.board_forever()
    b = server.BOARD["ud-benchy"]
    assert b["state"] == "HELD" and b["reason"] == "Not in the team's current lineup" and b["held"] == "av-cs2-1"
    assert not b.get("side") and b.get("grade") is None and b.get("units") is None and b["graded_line"] == 30.5
    assert server.board_payload()["rows"] == []                              # no side, letter or stake anywhere
    assert logged == []                                                      # nothing written to the pick log
    for w in ("unconfirmed", "may ", "might", "possibly"):
        assert w not in b["reason"].lower()


# ------------------------------------------------------------------ card
def test_card_of_a_benched_player_has_no_side_letter_or_stake(card):
    set_grade, logged = card
    res = _grade_stub()
    res["player_block"] = dict(_pb(nick="s0und", benched=["s0und"]), profile_team="T", current_team="T", playing_for="T")
    set_grade(res)
    h = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=underdog")
    v, g = h.body()["verdict"], h.body()["grade"]
    assert h.code == 200 and v["side"] is None and v["grade"] is None and v["units"] == 0.0 and v["p"] is None
    assert v["reason"] == "Not in the team's current lineup" and g["reason"] == v["reason"] and "p_more" not in g
    assert "lambda_final" not in g and h.body()["ladder"] == [] and logged == []


def test_card_of_a_playing_player_is_unchanged(card):
    set_grade, logged = card
    set_grade(_grade_stub())                                                 # no benched list: the stub's normal pick
    h = get("/api/grade?player=s0und&line=36.5&prop=kills&maps=12&book=underdog")
    v = h.body()["verdict"]
    assert v["side"] == "Less" and v["grade"] and v["units"] > 0 and len(logged) == 1


# ------------------------------------------------------------------ matchroom: fresh re-read of a suspect team page
def test_team_page_suspect():
    five = [{"nickname": f"p{i}", "status": 1} for i in range(5)]
    assert not server.team_page_suspect({"players": five})
    assert server.team_page_suspect({"players": five + [{"nickname": "b", "status": 2}]})
    assert server.team_page_suspect({"players": five[:4]})
    assert not server.team_page_suspect({"players": five + [{"nickname": "c", "is_coach": True, "status": 2}]})


class _B:
    def __init__(self, fresh=None, err=None):
        self.fresh, self.err, self.ttls = fresh, err, []
        self.cs2 = types.SimpleNamespace(get_team_data=lambda slug: slug)

    def _run(self, coro, ttl=None):
        self.ttls.append(ttl)
        if self.err:
            raise self.err
        return self.fresh


def test_team_fresh_reads_with_a_short_ttl_and_keeps_the_page_on_failure():
    old = {"players": [{"nickname": "b", "status": 2}]}
    new = {"players": [{"nickname": "b", "status": 1}]}
    b = _B(fresh=new)
    assert server.team_fresh(b, "team-a", old) is new and b.ttls == [server.TEAM_FRESH_H]
    assert server.team_fresh(_B(err=RuntimeError("bo3.gg 503")), "team-a", old) is old   # transient: the page read stands
    assert server.team_fresh(_B(fresh={"error": "x"}), "team-a", old) is old


# ------------------------------------------------------------------ LoL link_mismatch on member records (workspace_lol)
def test_lol_link_mismatch_reads_as_void_on_member_records():
    import workspace_lol
    assert workspace_lol.result_of({"status": "link_mismatch", "actual": None, "over_hit": None}, "over") == ("void", None)
    assert workspace_lol.result_of({"status": "dnp", "actual": None, "over_hit": None}, "over") == ("void", None)
