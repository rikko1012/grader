"""SPORTS_MARKET_FALLBACK (remaining brief step 3, display only): when no book passes the frozen 15-minute rule the
snapshot may carry the books' own readings at the headline line as `mk.fb`, for the Market column alone. Flag off = the
exact row view of before; the board signal (board_view.market_at -> lean / edge / sort) never reads it."""
import math

import pytest

from sports import board_view, market_view
from sports.tests import helpers as h
from sports.tests.test_market_view import EV, NOW, book, devig, items

STALE = "2026-10-23T19:40:00Z"            # 25 min before NOW: fails the frozen 15-minute rule, still a listed book


def logit(p):
    return math.log(p / (1 - p))


def test_fallback_only_with_the_switch_only_without_a_consensus_and_only_at_the_headline_line():
    pp = h.offer()                                                     # PrizePicks 20.5 (target)
    fd = book("fanduel", "fd:1", observed_at=STALE)
    br = book("betrivers", "br:1", over=2.20, under=1.70, observed_at=STALE)
    its = items(pp, fd, br)
    off = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0)
    assert off["c"] is None and "fb" not in off                      # every book stale -> no consensus, no fb
    assert all(not b["in"] for b in off["bk"])
    on = market_view.row_view([(its[0][0], pp)], its, EV, NOW, "nba", 0.0, fallback=True)
    assert {k: v for k, v in on.items() if k != "fb"} == off          # nothing else in the view changes
    fb = on["fb"]
    want = 1 / (1 + math.exp(-(logit(devig(1.80, 2.05)) + logit(devig(2.20, 1.70))) / 2))
    assert fb["po"] == pytest.approx(want, abs=1e-4) and fb["pu"] == pytest.approx(1 - want, abs=1e-4)
    assert fb["n"] == 2 and fb["books"] == ["betrivers", "fanduel"] and fb["l"] == 20.5 and fb["age"] == 1500
    assert "display only" in fb["why"]
    # one fresh book makes a real consensus: no fallback even with the switch
    fresh = items(pp, book("fanduel", "fd:1"), br)
    view = market_view.row_view([(fresh[0][0], pp)], fresh, EV, NOW, "nba", 0.0, fallback=True)
    assert view["c"] is not None and "fb" not in view
    # books priced at another line are never moved to this one (that is the registered rule's job, not a display fallback)
    other = items(pp, book("fanduel", "fd:1", line=21.5, observed_at=STALE))
    assert "fb" not in market_view.row_view([(other[0][0], pp)], other, EV, NOW, "nba", 0.0, fallback=True)
    # a one-sided price carries no chance
    one = items(pp, book("fanduel", "fd:1", under=None, observed_at=STALE))
    assert "fb" not in market_view.row_view([(one[0][0], pp)], one, EV, NOW, "nba", 0.0, fallback=True)
    assert market_view.fallback_at([], 20.5) is None
    assert market_view.fallback_at([{"p": "x", "kind": "devig", "c": 1.0, "l": 20.5}], 20.5) is None


def test_switch_is_read_from_the_environment_and_the_board_signal_ignores_the_fallback(monkeypatch):
    monkeypatch.delenv(market_view.FALLBACK_FLAG, raising=False)
    assert market_view.fallback_on() is False
    monkeypatch.setenv(market_view.FALLBACK_FLAG, "1")
    assert market_view.fallback_on() is True
    assert market_view.fallback_on({market_view.FALLBACK_FLAG: "off"}) is False
    mk = {"l": 20.5, "hp": "prizepicks", "c": None, "at": {}, "bk": [],
          "fb": {"po": .6, "pu": .4, "n": 2, "l": 20.5, "books": ["a", "b"], "age": 1500}}
    assert board_view.market_at(mk, "r1", 20.5) is None              # lean / edge / sort key never read fb
    assert board_view.signal(.62, .38, 22.0, 20.5, board_view.market_at(mk, "r1", 20.5))["ln"] == "over"
    assert "e" not in board_view.signal(.62, .38, 22.0, 20.5, board_view.market_at(mk, "r1", 20.5))
