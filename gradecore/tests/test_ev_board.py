"""SCOUT_PICKEM_EV (spec 4.1) arithmetic: the copied line shift equals the sports service's own, the per-leg break-even comes
from the verified tables only, EV = p / be - 1, stale / one-sided quotes never produce an EV, and hidden legs are counted."""
import math

import pytest

from gradecore import ev_board, fair, line_shift, payouts
from gradecore.tests.pickem_ref import REAL_PICKEM_CONFIG, reference_tables  # noqa: F401  (fixture)

NOW = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _formula_tables(reference_tables):
    """The formulas below run on the PrizePicks REFERENCE numbers (unverified, so never live): see pickem_ref."""
    return reference_tables


def test_prizepicks_break_even_is_unavailable_on_the_live_tables():
    live = REAL_PICKEM_CONFIG()
    assert ev_board.pickem_break_even("prizepicks", "power", 2, config=live) is None
    assert ev_board.pickem_break_even("prizepicks", "flex", 2, config=live) is None
    assert ev_board.pickem_break_even("underdog", "standard", 2, config=live) == pytest.approx(3.5 ** -0.5)


def test_a_book_line_too_far_from_the_pickem_line_gives_no_chance():
    """Review 2026-11-05: an NBA points quote at 18.5 read at 30.5 (a 12-point shift, GAP_WARN 3) is a different market."""
    rd = line_shift.reader("nba", "points")
    assert rd is not None
    q = [sb("fanduel", 1.87, 1.95, line=18.5)]
    for line in (22.5, 26.5, 30.5):
        r = fair.fair_chance(q, "under", line, NOW, reader=rd)
        assert r["p"] is None and r["dropped"][0]["reason"] == "different line too far"
        assert ev_board.hidden_reason(r) == "line"
    near = fair.fair_chance(q, "under", 20.5, NOW, reader=rd)          # a 2-point shift stays inside GAP_WARN
    assert near["p"] is not None and near["sources"][0]["shifted"]
    res = ev_board.price_leg(q, "under", 30.5, NOW, be=0.57735, p_model=None, p_model_other=None, reader=rd)
    assert res["ev"] is None and res["hidden"] == "line"


def sb(book, over, under, line=20.5, age=60):
    return fair.Quote(book=book, line=line, observed_at=NOW - age, over=over, under=under)


def ex(book, bid, ask, line=20.5, age=60, liquidity=None):
    return fair.Quote(book=book, line=line, observed_at=NOW - age, kind="exchange", bid=bid, ask=ask, liquidity=liquidity)


# ------------------------------------------------------------------------------------------------ line shift (copied)

@pytest.mark.parametrize("family,market", [("basketball", "points"), ("basketball", "rebounds"), ("basketball", "assists"),
                                           ("basketball", "threes"), ("football", "rec_yds"), ("football", "rec")])
@pytest.mark.parametrize("sharp_line,line,p", [(20.5, 21.5, 0.55), (5.5, 4.5, 0.48), (6.5, 6.0, 0.6), (24.5, 24.5, 0.51), (1.5, 2.0, 0.3)])
def test_line_shift_equals_the_sports_services_shape(family, market, sharp_line, line, p):
    from sports import fair as sfair
    shape = sfair._dispersion()[family][market]["shape"]
    theirs = sfair.at_line(family, market, shape, sharp_line, p, line)
    mine = line_shift.at_line(family, market, sharp_line, p, line)
    for k in ("over", "under", "push"):
        assert mine[k] == pytest.approx(theirs[k], abs=1e-6), k
    assert mine["far"] == theirs["far"]


def test_line_shift_has_no_shape_for_an_unknown_market():
    assert line_shift.reader("nba", "no-such-stat") is None and line_shift.reader("cs2", "kills") is None
    assert line_shift.at_line("basketball", "no-such-stat", 20.5, 0.5, 21.5) is None


def test_fair_reads_another_line_through_the_reader():
    r = line_shift.reader("nba", "points")
    res = fair.fair_chance([sb("fanduel", 1.9, 1.9, line=19.5)], "over", 20.5, NOW, reader=r, method="multiplicative")
    from sports import fair as sfair
    a = sfair.at_line("basketball", "points", "normal", 19.5, 0.5, 20.5)
    assert res["p"] == pytest.approx(a["over"] / (a["over"] + a["under"]), abs=1e-6) and res["sources"][0]["shifted"]


# ------------------------------------------------------------------------------------------------ break-even

def test_break_even_from_the_verified_tables():
    assert ev_board.pickem_break_even("prizepicks", "power", 2) == pytest.approx((1 / 3.0) ** 0.5)
    assert ev_board.pickem_break_even("prizepicks", "power", 6) == pytest.approx((1 / 37.5) ** (1 / 6))
    assert ev_board.pickem_break_even("underdog", "standard", 3) == pytest.approx(6.5 ** (-1 / 3))
    assert ev_board.pickem_break_even("prizepicks", "flex", 5) == pytest.approx(payouts.entry_break_even("prizepicks", "flex", 5))
    assert payouts.american_of(ev_board.pickem_break_even("prizepicks", "power", 2)) == pytest.approx(-136.6, abs=0.1)


def test_modifiers_and_non_standard_lines():
    # Underdog standard with a 1.04x pick: the documented modifier rule (payouts.break_even with a multiplier)
    be = ev_board.pickem_break_even("underdog", "standard", 2, 1.04)
    assert be == pytest.approx(payouts.modifier_break_even(2, 1.04, base=3.5))
    assert ev_board.pickem_break_even("underdog", "standard", 2, 1.0) == pytest.approx(3.5 ** -0.5)     # 1.0 = no modifier
    assert ev_board.pickem_break_even("underdog", "flex", 3, 1.04) is None                            # flex published at 1.0x only
    assert ev_board.pickem_break_even("prizepicks", "power", 2, 0.9) is None                          # PrizePicks modifier: unpublished
    assert ev_board.pickem_break_even("prizepicks", "power", 2, line_type="goblin") is None
    assert ev_board.pickem_break_even("prizepicks", "power", 9) is None                               # no such entry


def test_unverified_table_is_unavailable_never_guessed():
    cfg = payouts.with_tables({}, base={"prizepicks": {"default_entry": "power", "entries": {"power": None}}})
    assert ev_board.pickem_break_even("prizepicks", "power", 2, config=cfg) is None


# ------------------------------------------------------------------------------------------------ one leg

def test_leg_ev_is_p_over_break_even_minus_one():
    q = [sb("fanduel", 1.80, 2.05), sb("betrivers", 1.83, 2.00)]
    be = ev_board.pickem_break_even("prizepicks", "power", 2)
    r = ev_board.price_leg(q, "over", 20.5, NOW, be=be, p_model=0.61, p_model_other=0.39)
    p = (fair.devig_side(1.80, 2.05)[0] + fair.devig_side(1.83, 2.00)[0]) / 2
    assert r["basis"] == "market" and r["p_market"] == pytest.approx(p)
    assert r["ev"] == pytest.approx(p / be - 1) and r["ev_model"] == pytest.approx(0.61 / be - 1)
    assert r["american"] == pytest.approx(payouts.american_of(be))


def test_worst_case_by_hand():
    """Worst case = the lowest of multiplicative, power and Shin for the chosen side (hand-written formulas)."""
    o, u = 1.75, 2.15
    a, b = 1 / o, 1 / u
    mult = a / (a + b)
    lo, hi = 0.5, 5.0
    for _ in range(200):
        k = (lo + hi) / 2
        lo, hi = (k, hi) if a ** k + b ** k > 1 else (lo, k)
    power = a ** k
    B = a + b
    zl, zh = 0.0, 0.4
    shin = lambda z, pi: (math.sqrt(z * z + 4 * (1 - z) * pi * pi / B) - z) / (2 * (1 - z))
    for _ in range(200):
        z = (zl + zh) / 2
        zl, zh = (z, zh) if shin(z, a) + shin(z, b) > 1 else (zl, z)
    r = fair.fair_chance([sb("fanduel", o, u)], "over", 20.5, NOW)
    assert r["p"] == pytest.approx(min(mult, power, shin(z, a)), abs=1e-6)
    ru = fair.fair_chance([sb("fanduel", o, u)], "under", 20.5, NOW)
    assert ru["p"] == pytest.approx(min(1 - mult, b ** k, shin(z, b)), abs=1e-6)


def test_stale_quotes_never_produce_an_ev():
    old = fair.FRESHNESS_S["sportsbook"] + 1
    q = [sb("fanduel", 1.5, 2.8, age=old), sb("betrivers", 1.5, 2.8, age=old), ex("kalshi", 0.70, 0.72, age=fair.FRESHNESS_S["exchange"] + 1)]
    r = ev_board.price_leg(q, "over", 20.5, NOW, be=0.55, p_model=0.6, p_model_other=0.4)
    assert r["ev"] is None and r["p_market"] is None and r["hidden"] == "stale" and r["basis"] is None
    assert all(d["reason"] == "stale" for d in r["fair"]["dropped"])


def test_a_stale_book_beside_a_fresh_one_is_left_out_of_the_fair():
    q = [sb("fanduel", 1.9, 1.9), sb("betrivers", 1.2, 4.5, age=fair.FRESHNESS_S["sportsbook"] + 5)]
    r = ev_board.price_leg(q, "over", 20.5, NOW, be=0.55, p_model=None, p_model_other=None)
    assert r["p_market"] == pytest.approx(0.5) and [s["book"] for s in r["fair"]["sources"]] == ["fanduel"]
    assert r["fair"]["dropped"][0]["reason"] == "stale" and r["ev_model"] is None


def test_hidden_reasons():
    one = ev_board.price_leg([sb("fanduel", 1.9, None)], "over", 20.5, NOW, be=0.55, p_model=0.5, p_model_other=0.5)
    assert one["hidden"] == "one-sided"
    wide = ev_board.price_leg([sb("fanduel", 1.5, 1.5)], "over", 20.5, NOW, be=0.55, p_model=0.5, p_model_other=0.5)
    assert wide["hidden"] == "wide"
    spread = ev_board.price_leg([ex("polymarket", 0.40, 0.50)], "over", 20.5, NOW, be=0.55, p_model=0.5, p_model_other=0.5)
    assert spread["hidden"] == "spread"
    few = ev_board.price_leg([sb("fanduel", 1.9, 1.9)], "over", 20.5, NOW, be=0.55, p_model=0.5, p_model_other=0.5, min_books=2)
    assert few["hidden"] == "books"
    req = ev_board.price_leg([sb("fanduel", 1.9, 1.9)], "over", 20.5, NOW, be=0.55, p_model=0.5, p_model_other=0.5, required_books=("pinnacle",))
    assert req["hidden"] == "required"
    liq = ev_board.price_leg([ex("kalshi", 0.50, 0.52, liquidity=50)], "over", 20.5, NOW, be=0.55, p_model=0.5, p_model_other=0.5, min_liquidity=100)
    assert liq["hidden"] == "liquidity"
    shift = ev_board.price_leg([sb("fanduel", 1.9, 1.9, line=19.5)], "over", 20.5, NOW, be=0.55, p_model=0.5, p_model_other=0.5)
    assert shift["hidden"] == "line"


def test_model_only_when_no_book_prices_the_market():
    r = ev_board.price_leg([], "under", 30.5, NOW, be=0.5774, p_model=0.62, p_model_other=0.38)
    assert r["basis"] == "model only" and r["p_market"] is None and r["ev"] is None
    assert r["ev_model"] == pytest.approx(0.62 / 0.5774 - 1)


def test_model_chance_is_taken_given_no_push():
    assert ev_board.no_push(0.45, 0.45) == pytest.approx(0.5) and ev_board.no_push(None, 0.4) is None
    r = ev_board.price_leg([], "over", 21.0, NOW, be=0.5, p_model=0.45, p_model_other=0.45)
    assert r["ev_model"] == pytest.approx(0.0)


def test_hidden_banner_words():
    assert ev_board.hidden_banner({}) is None
    assert ev_board.hidden_banner({"stale": 9, "one-sided": 3}) == "12 plays hidden: 9 with only old prices, 3 with only one side priced."
    assert ev_board.hidden_banner({"wide": 1}) == "1 play hidden: 1 with book margin too wide."
