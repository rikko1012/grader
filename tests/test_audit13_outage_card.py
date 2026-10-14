"""During an outage (HLTV unreachable, a Thunderpick blip) the member card has no grade of its own (2026-10-14 owner):
it shows the Prop Board's saved grade only when it is for exactly this pick - player, match, platform, stat, maps, line
and market type, with its side still offered - with the time it was graded, marked stale; otherwise a data-unavailable
verdict with no side, chance or stake. Its temporary numbers (chances, projection, ladder, simulation, comparison) are
withheld. (2026-10-14 harness finding before that: a legacy B- 0.5U card next to the board's kept Lean 0U.)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import server

ST = "2026-10-14T18:00:00+00:00"
RES = {"player_block": {"player": {"nick": "ZywOo"}}, "next_match": {"start": ST, "slug": "vitality-vs-mouz"},
       "grade": {"projection_source": "legacy (performance model unavailable: HLTV database unreachable right now)"}}
TEMP = {"side": "More", "p": 0.58, "grade": "B-", "units": 0.5, "state": "PLAY", "reason": "legacy fallback"}
GRADED = "2026-10-14T16:40:00+00:00"


def _row(**kw):
    r = {"book": "underdog", "nick": "ZywOo", "prop": "kills", "scope": "12", "line": 30.5, "graded_line": 30.5, "start": ST,
         "match_slug": "vitality-vs-mouz", "line_type": "standard", "side": "Less", "p": 0.53, "grade": "LEAN", "units": 0.0,
         "state": "LEAN", "graded_at": GRADED, "hltv_retry": "2026-10-14T17:00:00+00:00", "retry_cause": "hltv"}
    r.update(kw)
    return r


def _unavailable(v):
    return (v["side"], v["p"], v["grade"], v["units"], v["unavailable"], v["kept_from_board"]) == (None, None, None, 0.0, True, False)


def test_outage_card_shows_the_boards_saved_grade_for_the_exact_pick_marked_stale(monkeypatch):
    monkeypatch.setattr(server, "_bs_rows", lambda: [_row()])
    v = server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP))
    assert (v["side"], v["grade"], v["units"], v["p"]) == ("Less", "LEAN", 0.0, 0.53)
    assert v["kept_from_board"] and v["stale"] and not v["unavailable"] and v["graded_at"] == GRADED
    assert v["kept_book"] == "underdog" and v["kept_cause"] == "hltv" and GRADED in v["reason"] and "stale" in v["reason"]
    assert "legacy" not in str(v)                                   # nothing of the temporary grade is carried over


def test_outage_card_without_a_saved_grade_is_data_unavailable(monkeypatch):
    monkeypatch.setattr(server, "_bs_rows", lambda: [])
    v = server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP))
    assert _unavailable(v) and v["state"] == "UNAVAILABLE"           # no temporary side, chance (never a 50%) or stake
    assert "HLTV database unreachable" in v["reason"] and "no saved grade of this exact pick" in v["reason"]


@pytest.mark.parametrize("why, kw, args", [
    ("another line", {"line": 31.5, "graded_line": 31.5}, {}),
    ("graded at another line", {"graded_line": 29.5}, {}),
    ("another platform", {"book": "prizepicks"}, {"book": "underdog"}),
    ("another match at the same time", {"match_slug": "faze-vs-navi"}, {}),
    ("another start, no match id", {"start": "2026-10-15T18:00:00+00:00", "match_slug": None}, {}),
    ("another stat", {"prop": "hs"}, {}),
    ("other maps", {"scope": "1"}, {}),
    ("another market type", {"line_type": "goblin"}, {}),
    ("side no longer offered", {"under_available": False}, {}),
    ("another player", {"nick": "ZywOo2"}, {}),
    ("no grade on the row", {"grade": None}, {}),
])
def test_a_saved_grade_of_any_other_pick_is_never_reused(monkeypatch, why, kw, args):
    monkeypatch.setattr(server, "_bs_rows", lambda: [_row(**kw)])
    v = server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP), book=args.get("book", ("underdog", "prizepicks")))
    assert _unavailable(v), why


def test_goblin_card_matches_only_the_goblin_row_and_promo_counts_as_standard(monkeypatch):
    monkeypatch.setattr(server, "_bs_rows", lambda: [_row(book="prizepicks", line_type="standard", side="More"),
                                                    _row(book="prizepicks", line_type="goblin", side="More", p=0.71, grade="A-")])
    v = server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP), book="prizepicks", line_type="goblin")
    assert v["grade"] == "A-" and v["p"] == 0.71
    monkeypatch.setattr(server, "_bs_rows", lambda: [_row(book="prizepicks", line_type=None, odds_type="promo", side="More")])
    assert server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP), book="prizepicks")["kept_from_board"]


def test_no_platform_card_takes_the_first_platform_that_lists_the_exact_pick(monkeypatch):
    monkeypatch.setattr(server, "_bs_rows", lambda: [_row(book="prizepicks", grade="B", units=0.5, p=0.58)])
    v = server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP), book=("underdog", "prizepicks"))
    assert v["kept_book"] == "prizepicks" and v["units"] == 0.5


def test_the_match_id_decides_when_the_platforms_start_differs(monkeypatch):
    # 2026-10-14 post-deploy check: Underdog listed WBT vs ENCE at 14:00, BO3.gg (the graded match) at 12:00 - the same match
    monkeypatch.setattr(server, "_bs_rows", lambda: [_row(start="2026-10-14T20:00:00+00:00")])
    assert server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP))["kept_from_board"]


def test_a_row_without_a_match_id_still_needs_the_same_start(monkeypatch):
    monkeypatch.setattr(server, "_bs_rows", lambda: [_row(match_slug=None)])
    assert server.outage_verdict(RES, "kills", "12", 30.5, dict(TEMP))["kept_from_board"]
    res = dict(RES, next_match={"start": None})
    assert _unavailable(server.outage_verdict(res, "kills", "12", 30.5, dict(TEMP)))


def test_withheld_card_keeps_no_temporary_numbers():
    res = {"grade": {"p_more": 0.5, "p_less": 0.5, "lambda_final": 30.5, "grade": "C", "units": 0.0, "probs_raw": {"push": 0.0},
                     "detail": {"p_win": 0.5}, "dist": [0.1], "line": 30.5, "prop": "kills", "scope": "12", "line_type": "standard",
                     "flags": ["x"], "projection_source": "legacy (performance model unavailable: HLTV database unreachable right now)"},
           "ladder": [{"line": 29.5, "p": 0.55}], "simulation": {"anchored": True}, "comparison": {"ours": {"p": 0.5}}}
    server.withhold_temporary(res)
    g = res["grade"]
    assert g["withheld"] and g["status"] == "DATA UNAVAILABLE" and g["line"] == 30.5 and g["flags"] == ["x"]
    for k in ("p_more", "p_less", "lambda_final", "grade", "units", "probs_raw", "detail", "dist"):
        assert k not in g
    assert res["ladder"] == [] and res["simulation"] is None and res["comparison"].get("error")
