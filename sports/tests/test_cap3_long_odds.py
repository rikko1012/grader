"""cap-3 (owner 2026-11-08, hub NBA board sorted by Grader chance full of "Under 98.4% A" Polymarket ladder rungs: "fix this
these are only A's cause they're probbaly -1000 odd props"). On a line whose payout follows the price (books and exchanges /
prediction markets) a Grader side + letter are given only when the market's no-vig chance of Grader's side is <= 0.65; pick'em
lines (PrizePicks / Underdog, fixed payout) are never affected. Held lines keep their projection and chances; no side, no
letter, no edge, stake 0, text "Grader grades lines priced near even." One rule in board_view.quote_x, every surface."""
import copy

import pytest

from sports import board_view, containment, sheets, snapshots
from sports.model import chance_cal
from sports.slips import pool

SHIPPED = dict(containment.OWNER_RELEASE)
L10 = [5, 5, 0, 10]
NOW = "2026-11-08T21:00:00Z"
TEXT = "Grader grades lines priced near even."


@pytest.fixture(autouse=True)
def released(monkeypatch):
    """Production configuration (ct-1b: NBA + NFL letters released), so a graded row really carries its letter."""
    monkeypatch.setattr(containment, "OWNER_RELEASE", dict(SHIPPED))
    monkeypatch.delenv("SPORTS_CHANCE_CAL", raising=False)
    containment.reset_cache()
    chance_cal.reset_cache()
    yield
    containment.reset_cache()
    chance_cal.reset_cache()


def qx(platform, po, pu, mu, line, mkc=None, own=None):
    return board_view.quote_x("nba", "points", po, pu, mu, line, mkc, L10, platform=platform, own_po=own)


# ------------------------------------------------------------------------------------------------ the rule itself
def test_constants():
    assert board_view.MAX_SIDE_MARKET == 0.65 and board_view.CAP_TEXT == TEXT and board_view.CAP_VERSION == "cap-3"
    assert board_view.is_pickem("prizepicks") and board_view.is_pickem("underdog")
    for p in ("polymarket", "kalshi", "fanduel", "betrivers", "oddsapi:draftkings", "draftkings"):
        assert not board_view.is_pickem(p)


def test_polymarket_ladder_long_odds_is_held():
    """Julius Randle-like ladder rung: 24.5 with market Under .798 and Grader Under .81 -> was an A, now held."""
    x, code = qx("polymarket", 0.19, 0.81, 19.2, 24.5, {"po": 0.202, "pu": 0.798, "n": 3})
    assert code is None
    assert x == {"b": "cap", "t": TEXT, "v": "cap-3"}
    # before cap-3 (no platform named) the same quote was an A
    old, _ = board_view.quote_x("nba", "points", 0.19, 0.81, 19.2, 24.5, {"po": 0.202, "pu": 0.798, "n": 3}, L10)
    assert old["ln"] == "under" and old["gr"] == "A"


def test_polymarket_near_even_rung_is_graded():
    x, code = qx("polymarket", 0.36, 0.64, 19.2, 19.5, {"po": 0.424, "pu": 0.576, "n": 3})
    assert code is None and "b" not in x and x["ln"] == "under" and x["gr"] == "A"


def test_book_long_odds_prices_held_and_even_prices_graded():
    q = {"p": "fanduel", "d": [1.10, 7.0], "o": [1, 1]}
    own = board_view.own_chance(q)
    assert own == pytest.approx((1 / 1.1) / (1 / 1.1 + 1 / 7.0)) and own > 0.85
    x, _ = qx("fanduel", 0.92, 0.08, 30.0, 20.5, None, own)
    assert x["b"] == "cap" and x["t"] == TEXT and "gr" not in x and "ln" not in x
    q2 = {"p": "betrivers", "d": [1.84, 1.89], "o": [1, 1]}
    own2 = board_view.own_chance(q2)
    assert 0.49 < own2 < 0.52
    x2, _ = qx("betrivers", 0.62, 0.38, 25.0, 20.5, None, own2)
    assert "b" not in x2 and x2["ln"] == "over" and x2["gr"] == "A"


def test_own_prices_win_over_the_market_chance():
    """The offer's own prices first: even prices on the book grade even if the cross-book market chance is long."""
    x, _ = qx("fanduel", 0.70, 0.30, 25.0, 20.5, {"po": 0.68, "pu": 0.32, "n": 2}, 0.52)
    assert "b" not in x and x["gr"] == "A"
    x, _ = qx("fanduel", 0.70, 0.30, 25.0, 20.5, {"po": 0.52, "pu": 0.48, "n": 2}, 0.80)
    assert x["b"] == "cap"


def test_boundary_065_is_graded():
    x, _ = qx("kalshi", 0.30, 0.70, 15.0, 20.5, {"po": 0.35, "pu": 0.65, "n": 2})
    assert "b" not in x and x["ln"] == "under"
    x, _ = qx("kalshi", 0.30, 0.70, 15.0, 20.5, {"po": 0.349, "pu": 0.651, "n": 2})
    assert x["b"] == "cap"


def test_non_pickem_without_price_or_market_is_held():
    x, _ = qx("polymarket", 0.19, 0.81, 19.2, 24.5, None, None)
    assert x["b"] == "cap" and x["t"] == TEXT


@pytest.mark.parametrize("platform", ["prizepicks", "underdog"])
def test_pickem_standard_with_extreme_market_still_graded(platform):
    x, code = qx(platform, 0.08, 0.92, 15.0, 24.5, {"po": 0.12, "pu": 0.88, "n": 3}, None)
    assert code is None and "b" not in x and x["ln"] == "under" and x["gr"] == "A"
    x2, _ = qx(platform, 0.62, 0.38, 25.0, 20.5, None, None)       # no market at all: graded as before
    assert "b" not in x2 and x2["gr"] == "A"


def test_no_lean_is_not_held():
    """A quote with no side (chance under 50% both ways / mean on the other side) has nothing to cap."""
    x, _ = qx("polymarket", 0.55, 0.45, 18.0, 24.5, None, None)
    assert "b" not in x and x["ln"] is None


def test_sanity_and_gate_holds_still_win():
    x, code = board_view.quote_x("nba", "points", 0.19, 0.81, 2.0, 24.5, None, L10, platform="polymarket")
    assert code == "ratio_low" and x["b"] == "ratio_low"


def test_own_chance_reads_every_quote_form():
    full_ex = {"sides": [{"side": "over", "offered": True, "price_format": "exchange_yes_no", "bid": 0.18, "ask": 0.22},
                         {"side": "under", "offered": True, "price_format": "exchange_yes_no", "bid": 0.78, "ask": 0.82}]}
    assert board_view.own_chance(full_ex) == pytest.approx(0.20)
    full_bk = {"sides": [{"side": "over", "offered": True, "price_format": "decimal", "price": 1.84},
                         {"side": "under", "offered": True, "price_format": "decimal", "price": 1.89}]}
    assert board_view.own_chance(full_bk) == pytest.approx((1 / 1.84) / (1 / 1.84 + 1 / 1.89))
    one = {"sides": [{"side": "over", "offered": True, "price_format": "decimal", "price": 1.84},
                     {"side": "under", "offered": False, "price_format": "decimal", "price": None}]}
    assert board_view.own_chance(one) is None
    assert board_view.own_chance({"k": [0.18, 0.22]}) == pytest.approx(0.20)
    assert board_view.own_chance({"k": [None, 0.22]}) is None
    assert board_view.own_chance({"d": [1.84, None], "o": [1, 0]}) is None
    pp = {"sides": [{"side": "over", "offered": True, "price_format": "none"}, {"side": "under", "offered": True}]}
    assert board_view.own_chance(pp) is None


def test_exchange_spread_limit_matches_the_market_rule():
    from sports.model import market
    assert board_view.EXCHANGE_MAX_SPREAD == market.MAX_SPREAD == 0.05


def test_wide_exchange_rung_has_no_own_price():
    """Review 2026-11-08: an empty Polymarket / Kalshi rung (bid 0.01 / ask 0.97, live Gamma read: Giannis Points O/U 14.5)
    reads ~49% from its midpoint. A spread over 0.05 is not an own price (the registered market rule): no own chance."""
    for bid, ask in ((0.01, 0.97), (0.001, 0.999), (0.01, 0.99), (0.40, 0.46)):
        assert board_view.own_chance({"k": [bid, ask]}) is None, (bid, ask)
        assert board_view.own_chance({"sides": _ex(bid, ask)}) is None, (bid, ask)
    for bid, ask in ((0.40, 0.45), (0.18, 0.22), (0.47, 0.47)):          # spread <= 0.05 (inclusive): the midpoint
        assert board_view.own_chance({"k": [bid, ask]}) == pytest.approx((bid + ask) / 2)
        assert board_view.own_chance({"sides": _ex(bid, ask)}) == pytest.approx((bid + ask) / 2)


@pytest.mark.parametrize("platform", ["polymarket", "kalshi"])
def test_wide_exchange_rung_falls_back_to_the_market_chance(platform):
    """The reviewed case: Grader Over .985 at 14.5, market chance Over .96, rung 0.01 / 0.97 -> held (was an A)."""
    mkc = {"po": 0.96, "pu": 0.04, "n": 3}
    own = board_view.own_chance({"k": [0.01, 0.97]})
    x, code = qx(platform, 0.985, 0.015, 22.0, 14.5, mkc, own)
    assert code is None and x == {"b": "cap", "t": TEXT, "v": "cap-3"}
    # no market chance either: nothing to judge the price by -> held
    x, _ = qx(platform, 0.985, 0.015, 22.0, 14.5, None, own)
    assert x["b"] == "cap"
    # a near-even market chance still grades the side
    x, _ = qx(platform, 0.62, 0.38, 22.0, 20.5, {"po": 0.55, "pu": 0.45, "n": 3}, own)
    assert "b" not in x and x["ln"] == "over" and x["gr"] == "A"


def test_wide_rung_in_snapshot_and_compact_board_is_held():
    """guard_row (snapshot sides) and the compact board quote (k) give the same answer for a wide rung."""
    row, forecasts, view = _world()
    forecasts["fw"] = _fc("fw", 14.5, 0.985, 0.015, mu=22.0)
    row["quotes"].append(_q("pmw", "polymarket", 14.5, "fw", _ex(0.01, 0.97)))
    row["market"]["at"]["pmw"] = {"po": 0.96, "pu": 0.04, "n": 3}
    forecasts["fe"] = _fc("fe", 20.5, 0.62, 0.38, mu=22.0)            # wide rung, near-even market chance: graded
    row["quotes"].append(_q("pme", "polymarket", 20.5, "fe", _ex(0.01, 0.97)))
    row["market"]["at"]["pme"] = {"po": 0.55, "pu": 0.45, "n": 3}
    view["lines"]["14.5"] = view["lines"]["20.5"] = L10 + L10
    snapshots.guard_row("nba", row, forecasts, view, NOW)
    q = next(q for q in row["quotes"] if q["record_id"] == "pmw")
    assert q["x"] == {"b": "cap", "t": TEXT, "v": "cap-3"}
    c = snapshots.compact_quote(q)
    assert c["x"] == q["x"] and board_view.own_chance(c) is None
    e = next(q for q in row["quotes"] if q["record_id"] == "pme")
    assert "b" not in e["x"] and e["x"]["ln"] == "over" and e["x"]["gr"] == "A", e["x"]


# Golden cases shared with the hub's extra-book mirror (workspace_books.cap_held; tests/test_sports_cap3_hub.py holds the
# SAME table - this package never imports a hub module): (over price, under price, side, market (po, pu) or None, held)
GOLDEN = [(1.10, 7.0, "over", None, True), (1.10, 7.0, "under", None, False), (7.0, 1.10, "under", None, True),
          (1.87, 1.95, "over", None, False), (1.87, 1.95, "under", None, False), (1.50, 2.60, "over", None, False),
          (1.45, 2.80, "over", None, True), (None, 1.10, "under", (0.2, 0.8), True), (None, 1.80, "under", (0.45, 0.55), False),
          (None, 1.80, "under", (0.35, 0.65), False), (None, None, "over", None, True), (4.6, 1.18, "under", (0.45, 0.55), True),
          (2.0, 1.83, "under", (0.2, 0.8), False)]


def test_golden_cases_for_the_hub_extra_book_mirror():
    for o, u, side, mc, held in GOLDEN:
        own = board_view.own_chance({"d": [o, u], "o": [o is not None, u is not None]})
        got = board_view.price_cap("fanduel", side, own, {"po": mc[0], "pu": mc[1]} if mc else None) is not None
        assert got == held, (o, u, side, mc)


# ------------------------------------------------------------------------------------ snapshot rows (guard_row)
def _q(rid, platform, line, fid, sides):
    return {"record_id": rid, "platform": platform, "line": line, "variant": "ladder_rung" if platform == "polymarket" else
            "standard", "promotional": False, "availability": "open", "sides": sides, "last_seen_at": NOW,
            "source_updated_at": None, "gradable": True, "forecast_id": fid}


def _ex(bid, ask):
    return [{"side": "over", "offered": True, "price_format": "exchange_yes_no", "bid": bid, "ask": ask},
            {"side": "under", "offered": True, "price_format": "exchange_yes_no", "bid": round(1 - ask, 6),
             "ask": round(1 - bid, 6)}]


def _bk(o, u):
    return [{"side": "over", "offered": True, "price_format": "decimal", "price": o},
            {"side": "under", "offered": True, "price_format": "decimal", "price": u}]


PICK = [{"side": "over", "offered": True}, {"side": "under", "offered": True}]


def _fc(fid, line, po, pu, mu=19.2):
    return {"forecast_id": fid, "kind": "scout", "model_version": "sm-1", "line": line, "p_over": po, "p_under": pu,
            "p_push": 0.0, "projection_mean": mu, "forecast_at": NOW, "detail": {}}


def _world():
    forecasts = {"f245": _fc("f245", 24.5, 0.19, 0.81), "f195": _fc("f195", 19.5, 0.36, 0.64),
                 "fbr": _fc("fbr", 24.5, 0.19, 0.81), "fpp": _fc("fpp", 24.5, 0.19, 0.81),
                 "fud": _fc("fud", 24.5, 0.19, 0.81)}
    row = {"key": "nba:e1|p1|points|full_game", "event_id": "nba:e1", "player_id": "p1", "player": "Julius Randle",
           "stat": "points", "quotes": [_q("pm245", "polymarket", 24.5, "f245", _ex(0.19, 0.21)),
                                        _q("pm195", "polymarket", 19.5, "f195", _ex(0.41, 0.44)),
                                        _q("br245", "betrivers", 24.5, "fbr", _bk(4.6, 1.18)),
                                        _q("pp245", "prizepicks", 24.5, "fpp", PICK),
                                        _q("ud245", "underdog", 24.5, "fud", PICK)],
           "kalshi": [], "alternates": [],
           "market": {"at": {"pp245": {"po": 0.2, "pu": 0.8, "n": 3}, "ud245": {"po": 0.2, "pu": 0.8, "n": 3}}}}
    view = {"lines": {"24.5": L10 + L10, "19.5": L10 + L10}, "played": 40, "to10": "2026-11-06T00:00:00Z"}
    return row, forecasts, view


def test_snapshot_rows_hold_long_odds_and_keep_chances():
    row, forecasts, view = _world()
    before = copy.deepcopy(forecasts)
    log = snapshots.guard_row("nba", row, forecasts, view, NOW)
    x = {q["record_id"]: q["x"] for q in row["quotes"]}
    assert x["pm245"] == {"b": "cap", "t": TEXT, "v": "cap-3"}
    assert x["br245"]["b"] == "cap"                                   # 4.6 / 1.18: Under ~.80
    assert "b" not in x["pm195"] and x["pm195"]["gr"] == "A"          # mid .425: Under .575, near even
    assert x["pp245"]["gr"] == "A" and x["ud245"]["gr"] == "A"        # pick'em: unchanged
    assert log == []                                                  # cap-3 is not a sanity hold
    assert forecasts == before                                        # projections / chances untouched
    for q in row["quotes"]:
        c = snapshots.compact_quote(q)
        assert c["x"] == q["x"] and "ca" not in c                    # no chance rewritten


def test_compact_board_row_carries_the_hold():
    """The board / player-file quote the pages read (compact_quote) carries the hold (the hub end to end:
    tests/test_sports_cap3_hub.py)."""
    row, forecasts, view = _world()
    snapshots.guard_row("nba", row, forecasts, view, NOW)
    cq = {c["r"]: c for c in (snapshots.compact_quote(q) for q in row["quotes"])}
    assert cq["pm245"]["x"] == {"b": "cap", "t": TEXT, "v": "cap-3"} and cq["pm245"]["k"] == [0.19, 0.21]
    assert cq["br245"]["x"]["b"] == "cap" and cq["br245"]["d"] == [4.6, 1.18]
    assert cq["pm195"]["x"]["gr"] == "A" and cq["pp245"]["x"]["gr"] == "A" and cq["ud245"]["x"]["gr"] == "A"


# ------------------------------------------------------------------------------------------------ sheets + slips
def test_sheet_quote_for_a_book_line_is_held_and_pickem_is_graded():
    hist = [{"participation": "played", "values": {"points": v}, "season": 2026, "start_utc": f"2026-11-0{i}T00:00:00Z"}
            for i, v in enumerate((14, 18, 26, 22, 19, 25, 21, 23, 17, 27), 1)]
    for platform, sides, want in (("betrivers", _bk(4.6, 1.18), "cap"), ("prizepicks", PICK, None)):
        q = _q("r1", platform, 24.5, "fa", sides)
        f = dict(_fc("fa", 24.5, 0.19, 0.81), research_only=True, for_revision=None)
        pf = {"forecasts": {"fa": f}, "forecast_detail": {},
              "markets": [{"key": "nba:e1|p1|points|full_game", "event_id": "nba:e1", "player_id": "p1", "player": "K",
                           "position": "C", "team": "NY", "stat": "points", "stat_label": "Points", "primary": True,
                           "capability": "supported", "quotes": [q], "alternates": [], "market": None}]}
        m, _, _ = sheets.sheet_markets(pf, hist, platform, {"nba:e1": {"start_utc": "2099-01-01T00:00:00Z"}}, NOW)
        x = m[0]["q"][0]["x"]
        if want:
            assert x == {"b": "cap", "t": TEXT, "v": "cap-3"}, x
        else:
            assert "b" not in x and x["gr"] == "A", x


def test_slip_pool_never_takes_a_book_or_exchange_leg():
    for p in ("polymarket", "kalshi", "fanduel", "betrivers"):
        reason, leg = pool._evaluate("r", None, p, {}, {}, {}, None, NOW, "member")
        assert reason == "not a pick'em platform" and leg is None
