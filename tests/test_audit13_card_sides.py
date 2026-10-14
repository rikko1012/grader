"""2026-10-14: a card for a board line grades with the SAME feed sides as the board (one stake per line): a PrizePicks
line the feed limits to Over gets no Under stake on its card either."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import prizepicks, server, underdog

ST = "2026-10-14T18:00:00+00:00"


def _pp(**k):
    r = {"player": "ZywOo", "prop": "kills", "scope": "12", "line": 30.5, "odds_type": "standard", "start": ST,
         "over_available": True, "under_available": False, "book": "prizepicks"}
    r.update(k)
    return r


def test_card_takes_the_feeds_sides_for_the_exact_line(monkeypatch):
    monkeypatch.setattr(prizepicks, "board", lambda *a, **k: {"rows": [_pp()]})
    monkeypatch.setattr(underdog, "board", lambda *a, **k: {"rows": []})
    assert server.feed_less_available("zywoo", "kills", "12", 30.5, "standard", "prizepicks", ST) is False
    assert server.feed_less_available("ZywOo", "kills", "12", 30.5, "standard", None, ST) is False     # typed card: PP lists it
    assert server.feed_less_available("ZywOo", "kills", "12", 31.5, "standard", "prizepicks", ST) is None   # another line
    assert server.feed_less_available("ZywOo", "hs", "12", 30.5, "standard", "prizepicks", ST) is None
    assert server.feed_less_available("ZywOo", "kills", "12", 30.5, "standard", "prizepicks", "2026-10-15T18:00:00+00:00") is None


def test_underdog_first_for_a_card_without_platform_and_unknown_stays_unknown(monkeypatch):
    monkeypatch.setattr(prizepicks, "board", lambda *a, **k: {"rows": [_pp()]})
    monkeypatch.setattr(underdog, "board", lambda *a, **k: {"rows": [{"player": "ZywOo", "prop": "kills", "scope": "12",
                                                                      "line": 30.5, "start": ST}]})
    assert server.feed_less_available("ZywOo", "kills", "12", 30.5, "standard", None, ST) is None       # Underdog: the rule
    monkeypatch.setattr(prizepicks, "board", lambda *a, **k: {"rows": [_pp(), _pp(under_available=True)]})
    assert server.feed_less_available("ZywOo", "kills", "12", 30.5, "standard", "prizepicks", ST) is None   # conflicting rows
