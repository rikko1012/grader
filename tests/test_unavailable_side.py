"""2026-10-15 owner: unavailable selections must not look actionable. A row whose side the platform feed no longer offers
(a retained PrizePicks Under on an Over-only line) says "Unavailable-side no longer offered", keeps its grade for
evaluation, and is excluded from recommendation lists, the same-match cap, copying and slips."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server

ST = "2026-10-14T18:00:00+00:00"


def test_the_label_names_a_side_the_feed_does_not_offer():
    kept = {"side": "Less", "under_available": False, "hltv_retry": "2026-10-14T17:00:00+00:00"}
    assert server.side_unavailable(kept) == server.SIDE_GONE == "Unavailable\u2014side no longer offered"
    assert server.side_unavailable(dict(kept, hltv_retry=None)) == server.SIDE_NOT_OFFERED
    # graded while the Under was offered, dropped since (retained or not): no longer offered
    assert server.side_unavailable(dict(kept, hltv_retry=None, graded_sides={"under": True, "over": True})) == server.SIDE_GONE
    assert server.side_unavailable(dict(kept, graded_sides={"under": False, "over": True})) == server.SIDE_NOT_OFFERED
    assert server.side_unavailable({"side": "More", "over_available": False}) == server.SIDE_NOT_OFFERED
    for ok in ({"side": "Less", "under_available": True}, {"side": "Less"}, {"side": "More", "under_available": False}, {}):
        assert server.side_unavailable(ok) is None


def test_an_unavailable_side_never_counts_in_the_same_match_cap():
    mk = lambda i, **kw: dict({"matchup": "A vs B", "start": ST, "team": "A", "side": "Less", "units": 1.0, "p": 0.6 + i / 100,
                               "nick": f"p{i}", "grade": "B+"}, **kw)
    rows = [mk(0), mk(1), mk(2, unavailable=server.SIDE_GONE, units=1.5)]
    server._cap_same_result(rows)
    assert [r["units"] for r in rows] == [1.0, 1.0, 1.5] and not any(r.get("cap_n") for r in rows)   # 2U of current bets


def test_an_outage_card_says_the_side_is_gone(monkeypatch):
    row = {"book": "prizepicks", "nick": "Crisp", "prop": "kills", "scope": "12", "line": 29.5, "graded_line": 29.5, "start": ST,
           "match_slug": "m", "line_type": "standard", "side": "Less", "grade": "LEAN", "units": 0.0, "p": 0.52,
           "under_available": False, "over_available": True, "hltv_retry": ST, "graded_at": ST}
    monkeypatch.setattr(server, "_bs_rows", lambda: [row])
    res = {"player_block": {"player": {"nick": "Crisp"}}, "next_match": {"start": ST, "slug": "m"},
           "grade": {"odds_transient": True}}
    v = server.outage_verdict(res, "kills", "12", 29.5, {"side": "More"}, book="prizepicks")
    assert v["unavailable"] and v["side"] is None and v["side_unavailable"] == server.SIDE_GONE
    assert v["reason"].startswith(server.SIDE_GONE) and "no saved grade" not in v["reason"]


def test_slip_paths_exclude_an_unavailable_side():
    import slip_batches, slip_builder, slips
    now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
    r = {"id": "r1", "book": "prizepicks", "nick": "p1", "player": "p1", "prop": "kills", "scope": "12", "line": 30.5,
         "graded_line": 30.5, "graded_model": "M", "side": "Less", "p": 0.62, "grade": "B+", "units": 1.0, "state": "GRADE",
         "start": (now + timedelta(minutes=90)).isoformat(), "graded_at": (now - timedelta(minutes=5)).isoformat(),
         "awp": False, "awp_conf": "confirmed", "igl": False, "higher": 1.0, "lower": 1.0, "match_slug": "m1",
         "under_available": True, "unavailable": server.SIDE_GONE}
    st = slip_builder.normalize_settings({"book": "prizepicks"})
    assert slip_builder.why_excluded(r, st, "M", now) == server.SIDE_GONE
    ok, why = slip_batches.eligible([r], now, "M")
    assert ok == [] and why.get("side not offered by the feed") == 1
    assert slips._legs([dict(r, matchup="m1")], now.isoformat()) == []
