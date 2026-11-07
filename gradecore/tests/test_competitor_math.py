"""Competitor spec section 3: every formula, against closed forms, brute force and Monte Carlo."""
import itertools
import json
import math
import random

import pytest

from gradecore import devig, fair, market_tools, payouts, slip, steam
from gradecore.tests.pickem_ref import REAL_PICKEM_CONFIG, reference_tables  # noqa: F401  (fixture)

PP_POWER = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}


@pytest.fixture(autouse=True)
def _formula_tables(reference_tables):
    """The formulas below run on the PrizePicks REFERENCE numbers (unverified, so never live): see pickem_ref."""
    return reference_tables


# ----------------------------------------------------------------------------------------------- payout tables
def test_tables_carry_source_and_read_date_and_prizepicks_is_verified_by_owner():
    raw = json.loads(payouts.PICKEM_FILE.read_text(encoding="utf-8"))
    for book in ("prizepicks", "underdog"):
        assert raw[book]["source"].startswith("https://")
        assert raw[book]["read_at"] and raw[book]["version"]
    assert raw["prizepicks"]["source"] == "https://www.prizepicks.com/help-center/payouts"
    # owner sign-off 2026-11-05 ("Mark the PrizePicks help-center payout table as verified so PrizePicks gets EV and
    # break-even."), superseding the 2026-11-04 rule for this table; the status records the date, quote, source and page date
    assert raw["underdog"]["verified"] is True and raw["prizepicks"]["verified"] is True
    st = raw["prizepicks"]["status"]
    assert "2026-11-05" in st and "Mark the PrizePicks help-center payout table as verified" in st
    assert "https://www.prizepicks.com/help-center/payouts" in st and "2026-10-19" in st and raw["prizepicks"]["page_date"] == "2026-10-19"
    live = REAL_PICKEM_CONFIG()
    assert live["prizepicks"]["entries"]["power"] == PP_POWER
    assert payouts.entry_break_even("prizepicks", "power", 2, live) == pytest.approx(3.0 ** -0.5)
    assert payouts.entry_break_even("prizepicks", "flex", 4, live) is not None
    assert {e["book"] for e in payouts.entry_types(live)} == {"prizepicks", "underdog"}
    # the default CONFIG (letter containment, policy, break_even()) carries the same verified table
    assert payouts.CONFIG["prizepicks"]["entries"]["power"] == PP_POWER
    assert payouts.break_even("prizepicks", None, 2) == pytest.approx(3.0 ** -0.5)
    # goblin / demon / promo lines still pay an unpublished multiplier
    assert payouts.break_even("prizepicks", None, 2, variant="goblin") is None
    assert payouts.break_even("prizepicks", None, 2, variant="demon") is None


def test_prizepicks_tables_match_the_recorded_help_center_reference():
    import payout_reference as ref
    cfg = payouts.pickem_config()
    assert cfg["prizepicks"]["entries"]["power"] == ref.PP_POWER == PP_POWER
    assert cfg["prizepicks"]["entries"]["flex"] == ref.PP_FLEX
    # Spec section 3 (in-app, Nov 5): Flex all-hit tier 3x / 6x / 10x / 25x for 3-6 picks.
    assert [cfg["prizepicks"]["entries"]["flex"][n][0] for n in (3, 4, 5, 6)] == [3.0, 6.0, 10.0, 25.0]


def test_underdog_tables_match_the_recorded_table():
    cfg = payouts.pickem_config()
    assert cfg["underdog"]["entries"]["standard"] == payouts.UD_STANDARD
    assert cfg["underdog"]["entries"]["flex"] == payouts.UD_FLEX


def test_live_config_carries_the_verified_prizepicks_table():
    """Owner sign-off 2026-11-05: live callers of CONFIG (letter_containment, policy) see the verified PrizePicks table too."""
    assert payouts.CONFIG["prizepicks"]["entries"]["power"] == PP_POWER
    assert payouts.break_even("prizepicks") == pytest.approx(3.0 ** -0.5)
    assert payouts.break_even("prizepicks", variant="goblin") is None
    assert not payouts.in_app("prizepicks", "flex", 2) and payouts.in_app("prizepicks", "flex", 3)


def test_unverified_book_or_size_is_unavailable(tmp_path, monkeypatch):
    assert payouts.tiers("prizepicks", "power", 7) is None
    assert payouts.entry_break_even("sleeper", "power", 2) is None
    assert slip.entry_ev([0.6, 0.6, 0.6, 0.6, 0.6, 0.6, 0.6], "prizepicks", "power")["available"] is False
    raw = json.loads(payouts.PICKEM_FILE.read_text(encoding="utf-8"))
    raw["underdog"]["verified"] = False                  # an unverified file entry adds nothing over the coded tables
    raw["prizepicks"]["verified"] = False
    f = tmp_path / "p.json"
    f.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(payouts, "PICKEM_FILE", f)
    payouts._pickem_raw.cache_clear()
    REAL_PICKEM_CONFIG.cache_clear()
    try:
        live = REAL_PICKEM_CONFIG()
        assert payouts.entry_break_even("prizepicks", "power", 2, live) is None
        assert payouts.entry_break_even("underdog", "standard", 2, live) == pytest.approx(3.5 ** -0.5)   # CONFIG base table
    finally:
        payouts._pickem_raw.cache_clear()
        REAL_PICKEM_CONFIG.cache_clear()


# ----------------------------------------------------------------------------------------------- break-even
@pytest.mark.parametrize("n,m", PP_POWER.items())
def test_power_break_even_closed_form(n, m):
    be = payouts.entry_break_even("prizepicks", "power", n)
    assert be == pytest.approx((1 / m) ** (1 / n), abs=1e-15)
    assert be ** n * m == pytest.approx(1.0)


def test_power_break_even_known_values():
    assert payouts.power_break_even(2, 3) == pytest.approx(0.57735, abs=1e-5)
    assert payouts.power_break_even(6, 37.5) == pytest.approx(0.54659, abs=1e-5)
    with pytest.raises(ValueError):
        payouts.power_break_even(2, 1.0)


@pytest.mark.parametrize("book,n", [("prizepicks", n) for n in range(2, 7)] + [("underdog", n) for n in range(3, 9)])
def test_flex_break_even_solves_the_table(book, n):
    t = payouts.tiers(book, "flex", n)
    be = payouts.entry_break_even(book, "flex", n)
    assert payouts.tier_return(t, n, be) == pytest.approx(1.0, abs=1e-9)
    # brute force over every hit pattern agrees with the binomial sum
    brute = sum(be ** sum(o) * (1 - be) ** (n - sum(o)) * t.get(sum(o), 0.0) for o in itertools.product((0, 1), repeat=n))
    assert brute == pytest.approx(1.0, abs=1e-9)


def test_flex_break_even_known_values():
    assert payouts.entry_break_even("prizepicks", "flex", 2) == pytest.approx((math.sqrt(5) - 1) / 2, abs=1e-9)  # p^2 + p = 1
    assert payouts.entry_break_even("underdog", "flex", 4) == pytest.approx(payouts.ud_flex_break_even(4), abs=1e-9)
    assert payouts.tier_break_even({2: 0.9}, 2) is None


def test_flex_tiers_are_by_hits():
    assert payouts.tiers("prizepicks", "flex", 6) == {6: 25.0, 5: 2.0, 4: 0.4}
    assert payouts.tiers("prizepicks", "power", 3) == {3: 6.0}


@pytest.mark.parametrize("be,odds", [(0.5, -100.0), (0.6, -150.0), (0.25, 300.0), (0.4, 150.0), (0.542, -118.34)])
def test_american_of_break_even(be, odds):
    assert payouts.american_of(be) == pytest.approx(odds, abs=0.01)


def test_american_round_trip():
    for be in (0.3, 0.45, 0.5, 0.5425, 0.7):
        a = payouts.american_of(be)
        d = devig.american_to_decimal(a)
        assert 1 / d == pytest.approx(be, abs=1e-12)


def test_entry_types_lists_every_verified_entry():
    rows = payouts.entry_types()
    keys = {(r["book"], r["entry"], r["n"]) for r in rows}
    assert {("prizepicks", "power", n) for n in range(2, 7)} <= keys
    assert {("prizepicks", "flex", n) for n in range(2, 7)} <= keys
    assert all(r["source"] and r["break_even"] for r in rows)


# ----------------------------------------------------------------------------------------------- slip win + EV
def test_spec_check_two_leg_power():
    r = slip.slip_ev([0.60, 0.60], {2: 3.0})
    assert r["p_all_hit"] == pytest.approx(0.36)
    assert r["ev"] == pytest.approx(0.08)
    e = slip.entry_ev([0.60, 0.60], "prizepicks", "power")
    assert e["available"] and round(e["p_all_hit"] * 100, 1) == 36.0 and round(e["ev"] * 100, 1) == 8.0


def test_poisson_binomial_matches_brute_force():
    rng = random.Random(3)
    for n in range(1, 9):
        p = [rng.uniform(0.3, 0.8) for _ in range(n)]
        d = slip.poisson_binomial(p)
        brute = [0.0] * (n + 1)
        for o in itertools.product((0, 1), repeat=n):
            brute[sum(o)] += math.prod(pi if x else 1 - pi for pi, x in zip(p, o))
        assert d == pytest.approx(brute, abs=1e-12)
        assert sum(d) == pytest.approx(1.0)


def test_flex_slip_ev_matches_brute_force():
    p = [0.55, 0.6, 0.52, 0.58, 0.61, 0.57]
    t = payouts.tiers("prizepicks", "flex", 6)
    brute = sum(math.prod(pi if x else 1 - pi for pi, x in zip(p, o)) * t.get(sum(o), 0.0)
                for o in itertools.product((0, 1), repeat=6)) - 1
    assert slip.slip_ev(p, t)["ev"] == pytest.approx(brute, abs=1e-12)


def test_slip_at_break_even_has_zero_ev():
    for n in range(2, 7):
        for entry in ("power", "flex"):
            be = payouts.entry_break_even("prizepicks", entry, n)
            assert slip.entry_ev([be] * n, "prizepicks", entry)["ev"] == pytest.approx(0.0, abs=1e-9)


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        slip.slip_ev([1.2, 0.5], {2: 3})
    with pytest.raises(ValueError):
        slip.slip_ev([0.5, 0.5], {3: 3})
    with pytest.raises(ValueError):
        slip.poisson_binomial([])


# ----------------------------------------------------------------------------------------------- Monte Carlo + copula
MC_CASES = [("prizepicks", e, n) for e in ("power", "flex") for n in range(2, 7)] + \
           [("underdog", "standard", n) for n in range(2, 7)] + [("underdog", "flex", n) for n in range(3, 7)]


@pytest.mark.parametrize("book,entry,n", MC_CASES)
def test_exact_ev_matches_monte_carlo_within_0_3_points(book, entry, n):
    """Spec 4.2 done-when: the EV shown matches Monte Carlo within 0.3 pts. Seeded independent-copula draws, enough of
    them that the simulation's own standard error is <= 0.09 pts (so 0.3 pts is a 3+ SE check, not luck)."""
    rng = random.Random(n * 7 + len(entry))
    p = [rng.uniform(0.52, 0.64) for _ in range(n)]
    t = payouts.tiers(book, entry, n)
    exact = slip.slip_ev(p, t)
    var = sum(q * m * m for q, m in slip.returns_of(exact["hit_dist"], t)) - exact["expected_multiple"] ** 2
    draws = max(1_000_000, math.ceil(var / 0.0009 ** 2))
    mc = slip.simulate(p, t, draws=draws, seed=11)
    assert abs(mc["ev"] - exact["ev"]) * 100 < 0.3
    assert mc["ev_low"] - 0.002 <= exact["ev"] <= mc["ev_high"] + 0.002
    assert mc["hit_dist"] == pytest.approx(exact["hit_dist"], abs=1e-3)


def test_copula_defaults_seeded_20k_with_interval():
    a = slip.simulate([0.6, 0.6], {2: 3.0})
    b = slip.simulate([0.6, 0.6], {2: 3.0})
    assert a == b and a["draws"] == 20_000 and slip.COPULA_DRAWS == 20_000
    assert a["ev_low"] < a["ev"] < a["ev_high"]
    se = (a["ev_high"] - a["ev_low"]) / (2 * slip.Z95)
    assert abs(a["ev"] - 0.08) < 3 * se and 0.005 < se < 0.015          # sd of a 3x / 0 payout at 36% over 20k draws ~ 0.0102
    assert abs(a["p_all_hit"] - 0.36) < 3 * (0.36 * 0.64 / 20_000) ** 0.5


def test_positive_correlation_raises_all_hit_chance():
    rho = [[1, 0.4], [0.4, 1]]
    c = slip.simulate([0.6, 0.6], {2: 3.0}, corr=rho, draws=400_000)
    # exact bivariate-normal orthant at rho 0.4 is about 0.4189; independence is 0.36
    assert c["p_all_hit"] > 0.40 and c["ev"] > 0.08
    neg = slip.simulate([0.6, 0.6], {2: 3.0}, corr=[[1, -0.4], [-0.4, 1]], draws=400_000)
    assert neg["p_all_hit"] < 0.33


def test_invalid_correlation_is_unavailable():
    bad = [[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1]]
    assert slip.simulate([0.6] * 3, {3: 6.0}, corr=bad)["available"] is False
    with pytest.raises(ValueError):
        slip.simulate([0.6] * 2, {2: 3.0}, corr=[[1, 0.2], [0.3, 1]])


def test_correlation_matrix_from_adapter():
    legs = [{"match_key": "m1"}, {"match_key": "m1"}, {"match_key": "m2"}]
    m = slip.correlation_matrix(legs, lambda a, b: 0.25)
    assert m[0][1] == 0.25 and m[0][2] == 0 and m[1][2] == 0
    assert slip.correlation_matrix(legs, lambda a, b: None) is None
    assert slip.correlation_matrix(legs, None) is None
    assert slip.correlation_matrix(legs[1:], None).tolist() == [[1, 0], [0, 1]]


# ----------------------------------------------------------------------------------------------- Kelly
def test_kelly_on_a_binary_payout_is_the_closed_form():
    p, m = 0.36, 3.0
    full = slip.kelly_fraction([(p, m), (1 - p, 0.0)])
    assert full == pytest.approx((p * m - 1) / (m - 1), abs=1e-9)


def test_kelly_on_a_flex_distribution_maximises_log_growth():
    p = [0.58] * 6
    t = payouts.tiers("prizepicks", "flex", 6)
    ret = slip.returns_of(slip.poisson_binomial(p), t)
    f = slip.kelly_fraction(ret)
    g = lambda x: sum(q * math.log(1 + x * (m - 1)) for q, m in ret if q > 0)
    assert 0 < f < 1 and g(f) >= g(f * 0.9) and g(f) >= g(min(f * 1.1, 0.999))


def test_stake_modes_display_only():
    ret = [(0.36, 3.0), (0.64, 0.0)]
    full = 0.04
    q = slip.stake(ret)
    assert q["fraction"] == pytest.approx(full / 4) and q["units"] == pytest.approx(full / 4 / 0.01)
    h = slip.stake(ret, fraction=0.5, bankroll=1000)
    assert h["amount"] == pytest.approx(1000 * full / 2) and h["units"] == pytest.approx(2.0)
    u = slip.stake(ret, bankroll=1000, unit=5)
    assert u["amount"] == pytest.approx(1000 * full / 4) and u["units"] == pytest.approx(2.0)
    assert slip.stake(ret, mode="flat")["units"] == 1.0
    capped = slip.stake(ret, fraction=1.0, cap_units=1.5)
    assert capped["units"] == 1.5 and capped["capped"]
    assert slip.stake([(0.3, 3.0), (0.7, 0.0)])["units"] == 0.0
    assert slip.stake([(0.3, 3.0), (0.7, 0.0)], mode="flat")["units"] == 0.0


# ----------------------------------------------------------------------------------------------- devig + exchange + fair
def test_worst_case_is_the_lowest_of_three_methods():
    for over, under in [(1.8, 2.05), (1.5, 2.6), (2.4, 1.6), (1.91, 1.91)]:
        for side, i in (("over", 0), ("under", 1)):
            p, name = fair.devig_side(over, under, side)
            assert p == pytest.approx(min(devig.METHODS[m]([over, under])[i] for m in ("multiplicative", "power", "shin")))
            assert name in fair.WORST_CASE_METHODS


def test_exchange_midpoint_rules():
    assert fair.exchange_mid(0.52, 0.56)[0] == pytest.approx(0.54)
    assert fair.exchange_mid(0.50, 0.55)[0] == pytest.approx(0.525)          # exactly 5 cents is allowed
    assert fair.exchange_mid(0.50, 0.56)[0] is None
    assert fair.exchange_mid(0.52, 0.56, liquidity=40, min_liquidity=100)[0] is None
    assert fair.exchange_mid(0.52, 0.56, liquidity=150, min_liquidity=100)[0] == pytest.approx(0.54)
    assert fair.exchange_mid(0.56, 0.52)[0] is None


NOW = 1_000_000.0


def q(book, over=1.9, under=1.9, line=24.5, age=60, **k):
    return fair.Quote(book=book, line=line, observed_at=NOW - age, over=over, under=under, **k)


def test_fair_drops_stale_one_sided_wide_and_future_quotes():
    quotes = [q("pinnacle", 1.8, 2.1), q("old", 1.5, 2.8, age=fair.FRESHNESS_S["sportsbook"] + 1),
              q("half", 1.8, None), q("wide", 1.6, 1.9), q("future", 1.5, 2.8, age=-10),
              fair.Quote("kalshi", 24.5, NOW - 400, kind="exchange", bid=0.6, ask=0.62)]
    r = fair.fair_chance(quotes, "over", 24.5, NOW)
    reasons = {d["book"]: d["reason"] for d in r["dropped"]}
    assert reasons == {"old": "stale", "half": "one-sided", "wide": "wide", "future": "no valid quote time", "kalshi": "stale"}
    assert r["p"] == pytest.approx(fair.devig_side(1.8, 2.1, "over")[0])
    assert r["books"] == ["pinnacle"]


def test_stale_quotes_never_produce_a_fair_price():
    r = fair.fair_chance([q("a", age=10_000), q("b", age=10_000)], "over", 24.5, NOW)
    assert r["p"] is None and len(r["dropped"]) == 2


def test_weighted_average_and_copy_books_count_once():
    a, b, c = q("pinnacle", 1.8, 2.1), q("fanduel", 2.0, 1.85), q("fd_copy", 1.6, 2.5, cluster="fanduel")
    pa = fair.devig_side(1.8, 2.1, "over")[0]
    pb = fair.devig_side(2.0, 1.85, "over")[0]
    pc = fair.devig_side(1.6, 2.5, "over")[0]
    r = fair.fair_chance([a, b, c], "over", 24.5, NOW, preset="sharp")
    assert r["books"] == ["fanduel", "pinnacle"]
    assert r["p"] == pytest.approx((3 * pa + 1 * (pb + pc) / 2) / 4)
    r2 = fair.fair_chance([a, b], "over", 24.5, NOW, weights={"fanduel": 0})
    assert r2["p"] == pytest.approx(pa)


def test_min_and_required_books():
    quotes = [q("pinnacle", 1.8, 2.1), q("fanduel", 2.0, 1.85)]
    assert fair.fair_chance(quotes, "over", 24.5, NOW, min_books=3)["p"] is None
    assert fair.fair_chance(quotes, "over", 24.5, NOW, min_books=2)["p"] is not None
    r = fair.fair_chance(quotes, "over", 24.5, NOW, required_books=["circa"])
    assert r["p"] is None and "circa" in r["reason"]
    assert fair.fair_chance(quotes, "over", 24.5, NOW, required_books=["Pinnacle"])["p"] is not None


def test_under_side_and_method_choice():
    quotes = [q("pinnacle", 1.8, 2.1)]
    o = fair.fair_chance(quotes, "over", 24.5, NOW, method="multiplicative")["p"]
    u = fair.fair_chance(quotes, "under", 24.5, NOW, method="multiplicative")["p"]
    assert o + u == pytest.approx(1.0)
    w_o = fair.fair_chance(quotes, "over", 24.5, NOW)["p"]
    w_u = fair.fair_chance(quotes, "under", 24.5, NOW)["p"]
    assert w_o + w_u <= 1.0 + 1e-12            # worst case never flatters either side


def test_exchange_quote_in_the_average():
    ex = fair.Quote("kalshi", 24.5, NOW - 60, kind="exchange", bid=0.55, ask=0.59, liquidity=500)
    r = fair.fair_chance([ex], "over", 24.5, NOW, min_liquidity=100)
    assert r["p"] == pytest.approx(0.57)
    assert fair.fair_chance([ex], "over", 24.5, NOW, min_liquidity=1000)["p"] is None


def test_different_line_needs_a_shape():
    r = fair.fair_chance([q("pinnacle", 1.8, 2.1, line=25.5)], "over", 24.5, NOW)
    assert r["p"] is None and r["dropped"][0]["reason"] == "different line and no line-shift shape"


def _poisson_p_at(mean, line):
    """A count distribution handed in by the caller (stand-in for a sport's own shape)."""
    from sports import fair as sfair
    return sfair.dist_probs("count", 1.0, 0.0, mean, line)


def test_line_shift_reuses_the_sports_shape():
    """reader_from_distribution around the sport's own dist_probs gives the same answer as sports.fair.at_line."""
    from sports import fair as sfair
    reader = fair.reader_from_distribution(_poisson_p_at)
    for sharp_line, p, line in [(24.5, 0.55, 23.5), (6.5, 0.48, 7.0), (2.5, 0.6, 3.5)]:
        mine = reader(sharp_line, p, line)
        theirs = sfair.at_line("no-family", "no-market", "count", sharp_line, p, line)
        for k in ("over", "under", "push"):
            assert mine[k] == pytest.approx(theirs[k], abs=2e-5)
    direct = lambda sl, p, l: sfair.at_line("no-family", "no-market", "count", sl, p, l)
    r = fair.fair_chance([q("pinnacle", 1.8, 2.1, line=25.5)], "over", 24.5, NOW, reader=direct)
    assert r["p"] is not None and r["sources"][0]["shifted"]
    same = fair.devig_side(1.8, 2.1, "over")[0]
    assert r["p"] > same                       # an easier Over line (24.5 < 25.5) has a higher chance


def test_line_shift_reuses_the_cs2_and_lol_shapes():
    """CS2: cm1.p_over (the live kills distribution); LoL: lol.model.nb_pmf. Wrapped, never copied."""
    import cm1
    from lol import model as lol_model
    cs2 = fair.reader_from_distribution(lambda m, l: cm1.p_over(m, l, "kills"))
    r = cs2(30.5, 0.55, 28.5)
    assert r["over"] > 0.55 and r["over"] + r["under"] == pytest.approx(1.0)
    assert cm1.p_over(r["mean"], 30.5, "kills") == pytest.approx(0.55, abs=1e-4)

    def lol_p_at(mean, line, size=12.0):
        pmf = [lol_model.nb_pmf(k, mean, size) for k in range(0, 200)]
        below = sum(pmf[:math.floor(line) + (0 if line == int(line) else 1)])
        push = pmf[int(line)] if line == int(line) else 0.0
        return 1 - below - push, below, push
    lol = fair.reader_from_distribution(lol_p_at)
    r = lol(4.5, 0.5, 5.0)
    assert r["push"] > 0 and r["over"] < 0.5
    o, u, _ = lol_p_at(r["mean"], 4.5)
    assert o / (o + u) == pytest.approx(0.5, abs=1e-4)


def test_whole_line_reports_push():
    reader = fair.reader_from_distribution(_poisson_p_at)
    r = fair.fair_chance([q("pinnacle", 1.9, 1.9, line=4.5)], "over", 5.0, NOW, reader=reader, method="multiplicative")
    assert r["push"] > 0 and 0 < r["p"] < 1


# ----------------------------------------------------------------------------------------------- leg, discrepancy, hit rate
def test_leg_chance_and_ev():
    assert fair.leg_chance(0.58) == {"p": 0.58, "basis": "model only", "p_model": 0.58, "p_market": None}
    assert fair.leg_chance(0.58, 0.52)["p"] == pytest.approx(0.58)       # default weight 1 = the printed chance unchanged
    assert fair.leg_chance(0.58, 0.52, w=0.5)["p"] == pytest.approx(
        1 / (1 + math.exp(-(0.5 * math.log(0.58 / 0.42) + 0.5 * math.log(0.52 / 0.48)))))
    be = payouts.entry_break_even("prizepicks", "power", 2)
    assert fair.leg_ev(0.6, be) == pytest.approx(0.6 * math.sqrt(3) - 1)
    assert fair.leg_ev(None, be) is None and fair.leg_ev(0.6, None) is None


def test_discrepancy_ranks_by_ev_not_line_gap():
    rows = fair.discrepancy_ev([
        {"app": "sleeper", "line": 0.5, "side": "over", "p": 0.70, "multiplier": 1.3},     # EV -9%, the 'easy' line
        {"app": "prizepicks", "line": 1.5, "side": "over", "p": 0.45, "be": 0.5774},
        {"app": "boom", "line": 1.5, "side": "over", "p": 0.45, "multiplier": 2.4},         # EV +8%
        {"app": "betr", "line": 1.5, "side": "over", "p": 0.45},
    ])
    assert [r["app"] for r in rows] == ["boom", "sleeper", "prizepicks", "betr"]
    assert rows[0]["ev"] == pytest.approx(0.08) and rows[-1]["ev"] is None


def test_hit_rate_sort_key_flags_junk():
    junk = fair.hit_rate_edge(10, 10, 5000 / 5100)          # Under 0.5 at -5000, 10/10
    good = fair.hit_rate_edge(7, 10, 0.5774)
    assert good["key"] > junk["key"] and good["key"] == pytest.approx(0.1226)
    assert not junk["below_break_even"] and junk["key"] == pytest.approx(1 - 5000 / 5100)
    assert fair.hit_rate_edge(5, 10, 0.5774)["below_break_even"]
    assert fair.hit_rate_edge(0, 0, 0.5) is None


# ----------------------------------------------------------------------------------------------- middles + arbitrage
def _poisson_pmf(mean, upto=80):
    out = {0: math.exp(-mean)}
    for k in range(1, upto + 1):
        out[k] = out[k - 1] * mean / k
    tail = 1 - sum(out.values())
    out[upto] += tail
    return out


def test_middle_table_bands_probabilities_and_ev():
    pmf = _poisson_pmf(27.0)
    legs = [{"side": "over", "line": 26.5, "stake": 50, "decimal": 1.9},
            {"side": "under", "line": 28, "stake": 50, "decimal": 1.9}]
    t = market_tools.middle_table(legs, pmf)
    assert t["probability_total"] == pytest.approx(1.0, abs=1e-12)
    by = {b["label"]: b for b in t["bands"]}
    assert set(by) == {"26 or fewer", "27", "28", "29 or more"}
    assert by["27"]["profit"] == pytest.approx(90.0)                  # both win
    assert by["28"]["profit"] == pytest.approx(45.0)                  # Over wins, Under pushes
    assert by["26 or fewer"]["profit"] == pytest.approx(-5.0)
    assert by["29 or more"]["profit"] == pytest.approx(-5.0)
    assert by["27"]["probability"] == pytest.approx(pmf[27])
    assert t["ev"] == pytest.approx(sum(b["probability"] * b["profit"] for b in t["bands"]))
    with pytest.raises(ValueError):
        market_tools.middle_table(legs, {1: 0.5})


def test_arbitrage_shares_margin_and_ages():
    legs = [{"book": "a", "side": "over", "decimal": 2.1, "observed_at": NOW - 30},
            {"book": "b", "side": "under", "decimal": 2.05, "observed_at": NOW - 90}]
    r = market_tools.arbitrage(legs, NOW)
    total = 1 / 2.1 + 1 / 2.05
    assert r["eligible"] and r["arbitrage"]
    assert r["margin"] == pytest.approx(1 - total)
    assert r["stake_share"] == pytest.approx([(1 / 2.1) / total, (1 / 2.05) / total])
    assert r["stakes"][0] * 2.1 == pytest.approx(r["stakes"][1] * 2.05)
    assert r["ages_s"] == [30, 90] and all("age_s" in leg for leg in r["legs"])
    legs[1]["observed_at"] = NOW - market_tools.ARB_MAX_AGE_S - 1
    stale = market_tools.arbitrage(legs, NOW)
    assert not stale["eligible"] and stale["ages_s"][1] > market_tools.ARB_MAX_AGE_S


# ----------------------------------------------------------------------------------------------- steam
def _series(start, end, t_move, now=NOW):
    return [(now - 20 * 60, start), (now - t_move, start), (now - 60, end)]


def test_steam_fires_on_a_confirmed_move_with_the_app_line_still():
    src = {"pinnacle": _series(0.52, 0.56, 10 * 60), "circa": _series(0.53, 0.565, 8 * 60)}
    r = steam.detect(src, [(NOW - 3600, 24.5)], NOW)
    assert r["fire"] and r["direction"] == 1 and r["confirmed_by"] == ["circa", "pinnacle"]


def test_steam_needs_two_sources_three_points_the_window_and_a_still_line():
    one = {"pinnacle": _series(0.52, 0.56, 10 * 60), "circa": _series(0.53, 0.54, 10 * 60)}
    assert not steam.detect(one, [(NOW - 3600, 24.5)], NOW)["fire"]
    small = {"pinnacle": _series(0.52, 0.545, 10 * 60), "circa": _series(0.53, 0.555, 10 * 60)}
    assert not steam.detect(small, [(NOW - 3600, 24.5)], NOW)["fire"]
    slow = {"pinnacle": [(NOW - 40 * 60, 0.50), (NOW - 60, 0.56)], "circa": [(NOW - 40 * 60, 0.5), (NOW - 60, 0.56)]}
    assert not steam.detect(slow, [(NOW - 3600, 24.5)], NOW)["fire"]            # no observation inside 5-15 min
    src = {"pinnacle": _series(0.52, 0.56, 10 * 60), "circa": _series(0.53, 0.565, 8 * 60)}
    moved = steam.detect(src, [(NOW - 3600, 24.5), (NOW - 120, 25.5)], NOW)
    assert not moved["fire"] and "moved" in moved["reason"]
    split = {"a": _series(0.5, 0.54, 600), "b": _series(0.5, 0.54, 600), "c": _series(0.5, 0.46, 600), "d": _series(0.5, 0.46, 600)}
    assert steam.detect(split, [(NOW - 3600, 24.5)], NOW)["reason"] == "sources disagree"


def test_steam_downward_move():
    src = {"pinnacle": _series(0.56, 0.52, 10 * 60), "circa": _series(0.57, 0.53, 6 * 60)}
    r = steam.detect(src, [(NOW - 3600, 24.5)], NOW)
    assert r["fire"] and r["direction"] == -1
