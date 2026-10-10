"""PrizePicks lines from the HLTV scraper's database (hltv.prizepicks_rows / prizepicks_board) in the card's line
comparison: same strict joins as Underdog, labelled by book, their own staleness, never ranked by payout, grade untouched."""
import copy, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comparison as cmp
import hltv
from tests.test_comparison import NOW, START, _res, _row

T0 = datetime(2026, 10, 9, 15, 0, tzinfo=timezone.utc)


def _rec(lid="p1", player="cairne", maps="1-2", market="kills", line=31.5, ot="standard", team="Fnatic", opp="K27", start=T0):
    return (lid, player, team, opp, start, market, maps, line, ot, 30.5, 1, T0 - timedelta(hours=2), T0 - timedelta(hours=5), 30.5)


def test_rows_from_the_database():
    rows = hltv.prizepicks_rows([_rec(), _rec("p2", market="headshots", maps="1"), _rec("p3", maps="2"),
                                 _rec("p4", ot="goblin", line=27.5), _rec("p5", market="assists")])
    assert [r["id"] for r in rows] == ["pp:p1", "pp:p2", "pp:p4"]          # Map 2 and other markets are not compared
    r = rows[0]
    assert (r["book"], r["prop"], r["scope"], r["line"], r["alt"], r["open_line"], r["other_line"]) == (
        "prizepicks", "kills", "12", 31.5, False, 30.5, 30.5)
    assert rows[1]["prop"] == "hs" and rows[1]["scope"] == "1"
    assert rows[2]["alt"] and rows[2]["odds_type"] == "goblin"
    assert cmp._ts(r["start"]) == T0


def test_panel_shows_both_books_with_strict_joins():
    res = _res(line=30.5)
    before = copy.deepcopy(res["grade"])
    pp = [dict(x, stale=False) for x in hltv.prizepicks_rows([
        _rec("p1", line=31.5), _rec("p4", ot="goblin", line=27.5),
        _rec("p6", team="Vitality"),                                         # namesake on another team
        _rec("p7", start=T0 + timedelta(hours=5)),                           # another match
        _rec("p8", maps="1")])]                                              # other maps
    p = cmp.panel(res, [_row()] + pp, board_at=NOW.timestamp() - 60, now=NOW)
    assert res["grade"] == before                                            # context only: the grade is untouched
    got = sorted((o["source"], o["line"], o["alt"]) for o in p["offers"])
    assert got == [("prizepicks", 27.5, True), ("prizepicks", 31.5, False), ("underdog", 30.5, False)]
    assert len(p["rejected"]) == 3
    assert p["best_line"] == {}                                              # PrizePicks has no per-line payout: never ranked
    o = next(o for o in p["offers"] if o["id"] == "pp:p1")
    assert (o["open_line"], o["moves"], o["odds_type"]) == (30.5, 1, "standard")
    assert abs(o["p_over"] + o["p_under"] + o["p_push"] - 1) < 1e-6


def test_prizepicks_staleness_is_its_own_feed_age():
    res = _res(line=30.5)
    fresh = [dict(x, stale=False) for x in hltv.prizepicks_rows([_rec()])]
    old = [dict(x, stale=True) for x in hltv.prizepicks_rows([_rec()])]
    # Underdog's board copy 40 min old: its lines are stale, PrizePicks' follow their own feed
    p = cmp.panel(res, [_row()] + fresh, board_at=NOW.timestamp() - 40 * 60, now=NOW)
    assert {o["source"]: o["stale"] for o in p["offers"]} == {"underdog": True, "prizepicks": False}
    p = cmp.panel(res, [_row()] + old, board_at=NOW.timestamp() - 60, now=NOW)
    assert {o["source"]: o["stale"] for o in p["offers"]} == {"underdog": False, "prizepicks": True}


def test_board_without_database_is_harmless(monkeypatch):
    monkeypatch.setattr(hltv, "dsn", lambda: "")
    b = hltv.prizepicks_board()
    assert b["rows"] == [] and "not configured" in b["error"]
    assert next(s for s in cmp.SOURCES if s["id"] == "prizepicks")["status"] == "connected"
