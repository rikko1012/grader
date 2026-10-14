"""2026-10-14 harness finding: during an outage the member card's temporary grade never carries its own stake - it shows
the Prop Board's kept grade for the exact line, or a 0-unit Lean (one stake per line; never a stake on a fallback grade)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server

ST = "2026-10-14T18:00:00+00:00"
RES = {"player_block": {"player": {"nick": "ZywOo"}}, "next_match": {"start": ST}}
TEMP = {"side": "More", "p": 0.58, "grade": "B-", "units": 0.5, "state": "PLAY", "reason": "legacy fallback"}


def test_outage_card_shows_the_boards_kept_grade_for_the_exact_line(monkeypatch):
    row = {"book": "underdog", "nick": "ZywOo", "prop": "kills", "scope": "12", "line": 30.5, "start": ST, "side": "Less",
           "p": 0.53, "grade": "LEAN", "units": 0.0, "state": "LEAN", "hltv_retry": "2026-10-14T17:00:00+00:00"}
    monkeypatch.setattr(server, "_bs_rows", lambda: [row])
    v = server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP))
    assert (v["side"], v["grade"], v["units"], v["p"]) == ("Less", "LEAN", 0.0, 0.53) and v["kept_from_board"]


def test_outage_card_without_a_board_line_has_no_stake(monkeypatch):
    monkeypatch.setattr(server, "_bs_rows", lambda: [])
    v = server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP))
    assert v["units"] == 0.0 and v["grade"] == "LEAN" and v["temporary"] and v["side"] == "More"   # side + chance stay
    other = {"book": "underdog", "nick": "ZywOo", "prop": "kills", "scope": "12", "line": 31.5, "start": ST, "side": "Less",
             "p": 0.6, "grade": "B", "units": 0.5}
    monkeypatch.setattr(server, "_bs_rows", lambda: [other])          # another line: never borrowed
    assert server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP))["units"] == 0.0
