"""2026-10-15 owner: card / board / match table / slip use the same capped stake for the same selection. The event is
matched by its match id, then platform, player, stat, maps, line and side - never by identical provider start times.
Regression: Underdog listed WBT vs ENCE at 14:00, BO3.gg (the graded match) starts 12:00 - the card missed the board's
same-match cap. A rematch (another match id) is never matched."""
import json
import time
from datetime import datetime, timedelta, timezone

import slip_builder
import picklog
import server
import underdog
import prizepicks
import cs2_scout
from tests.test_audit13_site import site, card, get, _grade_stub, _mc_setup, FakeBO3, iso   # noqa: F401 (fixtures)

BO3_START = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=5)
UD_START = iso(BO3_START + timedelta(hours=2))          # the platform's listed start, 2 h later
SLUG = "wbt-vs-ence-15-10-2026"
WBT = ["Schwarz", "teme", "Smash", "Kursy", "alex"]


def _board(site, slug=SLUG):
    feed = []
    for i, n in enumerate(WBT[:4]):                     # 4 x 1U Unders on WBT: the same-match cap cuts them to 0.75U
        rid = f"ud:{i}"
        feed.append({"id": rid, "book": "underdog", "scope": "12", "player": n, "team": "WBT", "opponent": "ENCE",
                     "matchup": "WBT vs ENCE", "start": UD_START, "prop": "kills", "line": 30.5, "alt": False})
        server.BOARD[rid] = {"at": time.time(), "graded_line": 30.5, "graded_model": picklog.MODEL_VERSION, "grade": "B+",
                             "side": "Less", "p": 0.64 - i / 100, "units": 1.0, "proj": 28.0, "state": "GRADE", "reason": "r",
                             "nick": n, "team_name": "WBT", "opp_name": "ENCE", "dstat": ["ok", ""], "line_type": "standard",
                             "match_slug": slug, "book": "underdog", "graded_at": iso(datetime.now(timezone.utc))}
    _mc_setup(site, feed)
    site.setattr(server, "_both_boards", lambda: server.board_payload("underdog")["rows"])
    return feed


def _card_res(nick, slug=SLUG):
    r = _grade_stub(line=30.5)
    r["player_block"]["player"].update(nick=nick, slug=nick.lower())
    r["player_block"]["playing_for"] = "WBT"
    r["next_match"] = {"start": iso(BO3_START), "opponent": "ENCE", "slug": slug, "team_slug": "wbt", "opponent_slug": "ence"}
    return r


def test_card_board_match_table_and_slip_share_the_capped_stake_cold_and_warm(card, site):
    set_grade, _ = card
    _board(site)
    rows = server.board_payload("underdog")["rows"]
    br = next(r for r in rows if r["nick"] == "Schwarz")
    assert (br["units"], br["units_full"], br["cap_n"]) == (0.75, 1.0, 4)                 # the board's capped stake
    set_grade(_card_res("Schwarz"))
    q = f"/api/grade?player=Schwarz&line=30.5&prop=kills&maps=12&book=underdog&team=WBT&opponent=ENCE&start={UD_START}"
    cold, warm = get(q).body()["verdict"], get(q).body()["verdict"]                       # first build, then the shared answer
    server._RESULTS.clear()
    rebuilt = get(q).body()["verdict"]                                                    # rebuilt again (cold path)
    for v in (cold, warm, rebuilt):
        assert (v["side"], v["units"], v.get("cap_n")) == ("Less", 0.75, 4), v
    FakeBO3.ROSTERS = {"wbt": [{"nickname": n, "status": 1} for n in WBT], "ence": [{"nickname": f"e{i}", "status": 1} for i in range(5)]}
    mc = server.match_compare("wbt", "ence", "WBT", "ENCE", iso(BO3_START), "12", "underdog", SLUG)
    cell = next(e for t in mc["teams"] for e in t["players"] if e["nick"] == "Schwarz")["kills"]
    assert (cell["side"], cell["units"], cell["line"]) == ("Less", 0.75, 30.5)            # was missing: start 2 h apart
    st = slip_builder.normalize_settings({"book": "underdog"})
    assert slip_builder.why_excluded(dict(br, under_available=True, lower=1.0, higher=1.0), st, picklog.MODEL_VERSION,
                                     datetime.now(timezone.utc)) in (None, "role not confirmed") and br["units"] == 0.75


def test_a_rematch_never_borrows_the_boards_stake(card, site):
    set_grade, _ = card
    _board(site, slug="wbt-vs-ence-16-10-2026")                   # the board's lines are another meeting of the teams
    set_grade(_card_res("Schwarz"))
    q = f"/api/grade?player=Schwarz&line=30.5&prop=kills&maps=12&book=underdog&team=WBT&opponent=ENCE&start={UD_START}"
    v = get(q).body()["verdict"]
    assert v["units"] == 1.0 and not v.get("cap_n")                # its own stake: never another match's cap
    mc = server.match_compare("wbt", "ence", "WBT", "ENCE", iso(BO3_START), "12", "underdog", SLUG)
    assert not any((e.get("kills") or {}).get("units") for t in mc["teams"] for e in t["players"])


def test_without_a_match_id_the_start_window_still_decides():
    st = BO3_START
    assert server.same_match({"start": iso(st + timedelta(minutes=30))}, None, st)
    assert not server.same_match({"start": iso(st + timedelta(hours=2))}, None, st)
    assert server.same_match({"start": iso(st + timedelta(hours=2)), "match_slug": SLUG}, SLUG, st)
    assert not server.same_match({"start": iso(st), "match_slug": "other"}, SLUG, st)
