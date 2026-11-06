"""Payout tables and break-even arithmetic (AUDIT.md section 1)."""
import pytest

from sports import edges, payouts


def test_underdog_published_tables():
    assert payouts.UD_STANDARD == {2: 3.5, 3: 6.5, 4: 12.0, 5: 20.0, 6: 35.0, 7: 65.0, 8: 120.0}
    assert payouts.UD_FLEX[3] == {0: 3.25, 1: 1.09} and payouts.UD_FLEX[8] == {0: 80.0, 1: 3.0, 2: 1.0}
    # owner 2026-11-06: the verified PrizePicks table (gradecore/pickem_payouts.json) - test_pp_payouts_sports.py
    assert payouts.PP_POWER == {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5} and "verified" in payouts.PP_STATUS


def test_the_data_branch_5345_is_the_standard_two_pick_only():
    assert payouts.ud_standard_break_even(2) == pytest.approx(edges.UD_REF, abs=1e-12) == pytest.approx(0.534522, 1e-5)
    be = {n: payouts.ud_standard_break_even(n) for n in payouts.UD_STANDARD}
    assert be[3] == pytest.approx(0.53583, abs=1e-4) and be[5] == pytest.approx(0.54928, abs=1e-4)
    assert be[6] == pytest.approx(0.55291, abs=1e-4) and min(be.values()) == be[2]


def test_flex_break_even_solves_the_expected_return():
    for n in payouts.UD_FLEX:
        p = payouts.ud_flex_break_even(n)
        assert payouts.ud_flex_return(n, p) == pytest.approx(1.0, abs=1e-9)
    assert payouts.ud_flex_break_even(3) == pytest.approx(0.55394, abs=1e-4)


def test_modifier_break_even_matches_the_data_branch_rule_for_two_picks():
    # sharp-1 compared p * modifier with 53.45%: identical to the product rule for a 2-pick with a partner at break-even
    for m in (0.8, 0.9, 1.0, 1.15):
        assert payouts.modifier_break_even(2, m) == pytest.approx(edges.UD_REF / m, rel=1e-12)


def test_straight_bet_expectations_with_and_without_push():
    assert payouts.straight_ev(0.55, 1.91) == pytest.approx(0.55 * 1.91 - 1)
    assert payouts.straight_ev(0.50, 2.0, p_loss=0.45) == pytest.approx(0.5 * 1.0 - 0.45)


def test_entry_ev_is_labelled_as_an_estimate():
    ev, basis = payouts.ud_standard_ev([0.56, 0.57], [1.0, 1.05])
    assert ev == pytest.approx(0.56 * 0.57 * 3.5 * 1.05 - 1)
    assert "assumed" in basis and "independent" in basis


def test_kalshi_fee_rounds_up_to_the_cent():
    assert payouts.kalshi_taker_fee(0.50, 1) == 0.02            # 0.07 * 0.25 = 0.0175 -> 0.02
    assert payouts.kalshi_taker_fee(0.50, 100) == 1.75
