"""Phase 1 pure modules. Synthetic data only; the expected numbers come from sports/payouts.py and the brief."""
import json
import math

import numpy as np
import pytest

from gradecore import blend, calibrate, containment, devig, gates, payouts, policy, scorecard


# ------------------------------------------------------------------------------------------------- devig
PRICE_SETS = [(1.91, 1.91), (1.5, 2.6), (1.2, 4.5), (1.05, 12.0), (2.2, 1.65), (1.8, 2.05)]


@pytest.mark.parametrize("prices", PRICE_SETS)
@pytest.mark.parametrize("name", list(devig.METHODS))
def test_every_method_sums_to_one_and_keeps_the_order(name, prices):
    p = devig.METHODS[name](prices)
    assert p is not None and sum(p) == pytest.approx(1.0) and min(p) > 0
    assert (p[0] > p[1]) == (prices[0] < prices[1])


def test_multiplicative_matches_the_sports_formula():
    a, b = 1 / 1.8, 1 / 2.05
    assert devig.multiplicative((1.8, 2.05))[0] == pytest.approx(a / (a + b))


def test_even_market_is_fifty_fifty_on_every_method():
    for fn in devig.METHODS.values():
        assert fn((1.91, 1.91)) == pytest.approx([0.5, 0.5])


def test_power_and_shin_take_more_off_the_long_shot():
    q = 1 / 12.0
    mult = devig.multiplicative((1.05, 12.0))[1]
    assert devig.power((1.05, 12.0))[1] < mult < q and devig.shin((1.05, 12.0))[1] < mult


def test_shin_matches_the_known_two_way_formula():
    # two outcomes: z = ((S-1)(d^2 - S)) / (S (d^2 - 1)), d = q1 - q2 (Clarke 2017)
    q1, q2 = 1 / 1.5, 1 / 2.6
    s, d = q1 + q2, q1 - q2
    z = ((s - 1) * (d * d - s)) / (s * (d * d - 1))
    want = (math.sqrt(z * z + 4 * (1 - z) * q1 * q1 / s) - z) / (2 * (1 - z))
    assert devig.shin((1.5, 2.6))[0] == pytest.approx(want, abs=1e-9)


def test_worst_case_is_the_lowest_for_the_chosen_side():
    for side in (0, 1):
        p, name = devig.worst_case((1.3, 3.4), side)
        assert p == min(fn((1.3, 3.4))[side] for fn in devig.METHODS.values())
        assert devig.METHODS[name]((1.3, 3.4))[side] == p
    assert devig.worst_case((1.3, 3.4), 0)[1] != devig.worst_case((1.3, 3.4), 1)[1]


def test_bad_prices_give_none_not_a_number():
    for bad in ((1.0, 2.0), (0, 2), (None, 2), ("x", 2), (2.0,), (float("inf"), 2)):
        assert devig.multiplicative(bad) is None and devig.worst_case(bad) is None and devig.width(bad) is None


def test_additive_refuses_a_negative_and_worst_case_skips_it():
    assert devig.additive((1.1, 1.1, 50.0)) is None         # needs 3+ outcomes: two prices over 1 cannot go negative
    assert devig.worst_case((1.1, 1.1, 50.0), 2)[1] != "additive"


def test_market_priced_under_100_has_no_shin_but_keeps_the_others():
    assert devig.shin((2.2, 2.2)) is None
    assert devig.multiplicative((2.2, 2.2)) == pytest.approx([0.5, 0.5])
    assert devig.worst_case((2.2, 2.2), 0)[0] == pytest.approx(0.5)


def test_width_and_american_odds():
    assert devig.width((1.91, 1.91)) == pytest.approx(2 / 1.91 - 1)
    assert devig.american_to_decimal(-110) == pytest.approx(1 + 100 / 110)
    assert devig.american_to_decimal(150) == pytest.approx(2.5)
    with pytest.raises(ValueError):
        devig.american_to_decimal(0)


# ------------------------------------------------------------------------------------------------- blend
def test_blend_ends_and_middle():
    assert blend.blend(0.6, 0.5, 1.0) == pytest.approx(0.6)
    assert blend.blend(0.6, 0.5, 0.0) == pytest.approx(0.5)
    assert 0.5 < blend.blend(0.6, 0.5, 0.5) < 0.6
    assert blend.blend(0.7, 0.3, 0.5) == pytest.approx(0.5)


def test_no_market_is_the_model_at_any_weight():
    assert blend.blend(0.63, None, 0.2) == 0.63
    assert blend.blend(0.63, None, 0.2, width=0.3) == 0.63


def test_blend_refuses_a_weight_outside_0_1():
    with pytest.raises(ValueError):
        blend.blend(0.6, 0.5, 1.2)


def test_extreme_chances_stay_finite():
    assert 0 < blend.blend(0.0, 1.0, 0.5) < 1
    assert 0 < blend.blend(1.0, 1.0, 0.5) <= 1


def test_width_shift_runs_toward_the_market_as_the_brief_says(monkeypatch):
    monkeypatch.setattr(blend, "WIDTH_TOWARD", "market")
    w = [blend.effective_weight(0.8, x) for x in (None, 0.02, 0.05, 0.10, 0.15, 0.40)]
    assert w[0] == w[1] == w[2] == 0.8 and w[3] == pytest.approx(0.4) and w[4] == 0.0 and w[5] == 0.0
    assert blend.blend(0.7, 0.5, 0.8, width=0.2) == pytest.approx(0.5)


def test_width_shift_flips_with_the_one_constant(monkeypatch):
    monkeypatch.setattr(blend, "WIDTH_TOWARD", "model")
    assert blend.effective_weight(0.2, 0.15) == 1.0
    assert blend.effective_weight(0.2, 0.10) == pytest.approx(0.6)
    monkeypatch.setattr(blend, "WIDTH_TOWARD", "sideways")
    with pytest.raises(ValueError):
        blend.effective_weight(0.2, 0.15)


def test_weight_table_defaults_to_the_model_alone():
    assert blend.weight({"nba|points": 0.3}, "nba", "points") == 0.3
    assert blend.weight({"nba|points": 0.3}, "nba", "rebounds") == 1.0
    assert blend.weight(None, "cs2", "kills") == 1.0


def test_fit_weight_finds_the_better_source():
    rng = np.random.default_rng(1)
    truth = rng.uniform(0.3, 0.7, 6000)
    y = (rng.uniform(size=6000) < truth).astype(float)
    noise = np.clip(truth + rng.normal(0, 0.25, 6000), 0.02, 0.98)
    assert blend.fit_weight(truth, noise, y) > 0.8
    assert blend.fit_weight(noise, truth, y) < 0.2


def test_fit_weight_holds_off_under_min_n_and_refuses_pushes():
    assert blend.fit_weight([0.6] * 299, [0.5] * 299, [1] * 299) is None
    with pytest.raises(ValueError):
        blend.fit_weight([0.6] * 300, [0.5] * 300, [0.5] * 300)


# --------------------------------------------------------------------------------------------- calibrate
def _overstated(n, seed=3):
    """Stated chance runs high: the real chance is halfway back to 50%."""
    rng = np.random.default_rng(seed)
    p = rng.uniform(0.5, 0.8, n)
    return p, (rng.uniform(size=n) < 0.5 + (p - 0.5) * 0.5).astype(float)


def test_identity_under_300():
    p, y = _overstated(299)
    a = calibrate.fit(p, y, "cs2|kills|over", "2026-11-01")
    assert a["kind"] == "identity" and a["n"] == 299 and calibrate.apply(a, 0.7) == 0.7


def test_isotonic_with_its_provenance():
    p, y = _overstated(3000)
    a = calibrate.fit(p, y, "cs2|kills|over", "2026-11-01")
    assert a["kind"] == "isotonic" and a["fit_date"] == "2026-11-01" and a["n"] == 3000
    assert len(a["data_hash"]) == 16 and a["data_hash"] != calibrate.data_hash(p[:-1], y[:-1])
    assert calibrate.fit(p, y)["data_hash"] == a["data_hash"]


def test_calibration_pulls_an_overstated_chance_back_and_never_falls():
    p, y = _overstated(6000)
    a = calibrate.fit(p, y)
    out = [calibrate.apply(a, x) for x in np.linspace(0, 1, 201)]
    assert all(b >= c for b, c in zip(out[1:], out[:-1]))
    assert calibrate.apply(a, 0.75) == pytest.approx(0.625, abs=0.05)


def test_pav_is_right_on_a_small_known_case():
    x, y = calibrate.fit_isotonic([0.1, 0.2, 0.3, 0.4], [1, 0, 1, 1], min_block=1)
    assert y == pytest.approx([0.5, 1.0]) and x == pytest.approx([0.15, 0.35])


def test_equal_chances_share_a_block():
    x, y = calibrate.fit_isotonic([0.6, 0.6, 0.6, 0.6], [1, 0, 1, 0], min_block=1)
    assert x == pytest.approx([0.6]) and y == pytest.approx([0.5])


def test_small_steps_merge_so_a_handful_cannot_make_a_100_percent_tier():
    p = np.r_[np.linspace(0.5, 0.7, 2000), [0.9] * 5]
    y = np.r_[(np.arange(2000) % 2).astype(float), [1.0] * 5]
    assert max(calibrate.fit_isotonic(p, y)[1]) < 0.9


@pytest.mark.parametrize("n", [300, 999, 1000, 6000])
def test_flat_data_comes_back_flat(n):
    rng = np.random.default_rng(5)
    p = rng.uniform(0.5, 0.8, n)
    y = (rng.uniform(size=n) < 0.53).astype(float)      # the CS2 complaint: 70% hits no more than 58%
    assert calibrate.apply(calibrate.fit(p, y), 0.72) == pytest.approx(0.53, abs=0.09)


@pytest.mark.parametrize("n,kind", [(300, "platt"), (999, "platt"), (1000, "isotonic")])
def test_platt_below_1000_legs_isotonic_from_there(n, kind):
    assert calibrate.fit(*_overstated(n))["kind"] == kind


def test_too_few_isotonic_steps_fall_back_to_platt():
    p = np.r_[[0.55] * 600, [0.65] * 600]
    y = np.r_[(np.arange(600) % 2).astype(float), (np.arange(600) % 5 > 1).astype(float)]
    assert len(calibrate.fit_isotonic(p, y)[0]) == 2 and calibrate.fit(p, y)["kind"] == "platt"


def test_platt_recovers_a_known_line():
    rng = np.random.default_rng(6)
    p = rng.uniform(0.2, 0.8, 40000)
    lg = np.log(p / (1 - p))
    y = (rng.uniform(size=40000) < 1 / (1 + np.exp(-(0.6 * lg - 0.2)))).astype(float)
    a, b = calibrate.fit_platt(p, y)
    assert a == pytest.approx(0.6, abs=0.04) and b == pytest.approx(-0.2, abs=0.04)


def test_platt_slope_never_goes_negative():
    rng = np.random.default_rng(7)
    p = rng.uniform(0.5, 0.8, 4000)
    y = (rng.uniform(size=4000) < 1.2 - p).astype(float)
    a, b = calibrate.fit_platt(p, y)
    assert a == 0.0 and 1 / (1 + math.exp(-b)) == pytest.approx(y.mean())


def test_one_class_data_is_identity():
    assert calibrate.fit([0.6] * 400, [1.0] * 400)["kind"] == "identity"
    assert calibrate.fit_platt([0.6, 0.7], [1, 1]) is None


def test_output_is_clipped_off_0_and_1():
    p = np.r_[[0.1] * 400, [0.5] * 400, [0.9] * 400]
    y = np.r_[[0.0] * 400, (np.arange(400) % 2).astype(float), [1.0] * 400]
    a = calibrate.fit(p, y)
    assert a["kind"] == "isotonic"
    assert calibrate.apply(a, 0.0) == calibrate.CLIP[0] and calibrate.apply(a, 1.0) == calibrate.CLIP[1]


def test_a_push_cannot_enter_a_fit():
    with pytest.raises(ValueError):
        calibrate.fit([0.6, 0.6], [1.0, 0.5])
    with pytest.raises(ValueError):
        calibrate.fit([1.6], [1.0])
    with pytest.raises(ValueError):
        calibrate.fit([0.6], [1.0, 0.0])


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        calibrate.apply({"kind": "mystery"}, 0.6)


def test_build_splits_market_and_side_drops_pushes_and_round_trips(tmp_path):
    p, y = _overstated(800)
    rows = [{"market": "kills", "side": "over", "p": float(a), "status": "win" if b else "loss"} for a, b in zip(p, y)]
    rows += [{"market": "kills", "side": "under", "p": 0.6, "status": "win"}]
    rows += [{"market": "kills", "side": "over", "p": 0.6, "status": s} for s in ("push", "void", None)]
    doc = calibrate.build(rows, "cs2", "2026-11-01")
    assert doc["dropped"] == 3
    assert doc["tables"]["kills|over"]["n"] == 800 and doc["tables"]["kills|over"]["kind"] != "identity"
    assert calibrate.lookup(doc, "kills", "under")["kind"] == "identity"
    assert calibrate.lookup(doc, "hs", "over")["kind"] == "identity" and calibrate.lookup(None, "hs", "over")
    path = calibrate.artifact_path(tmp_path / "artifacts", "cs2")
    assert path.name == "calibration_cs2.json"
    calibrate.save(doc, path)
    back = calibrate.load(path)
    art = calibrate.lookup(back, "kills", "over")
    assert calibrate.apply(art, 0.7) == calibrate.apply(doc["tables"]["kills|over"], 0.7)


# ----------------------------------------------------------------------------------------------- payouts
def test_break_evens_match_sports_payouts():
    assert payouts.break_even("underdog") == pytest.approx(0.53452, abs=1e-5)
    assert payouts.break_even("underdog") == pytest.approx(3.5 ** -0.5)
    assert payouts.break_even("underdog", "flex", 4) == pytest.approx(0.5559, abs=5e-5)    # sports BE_HIGH
    for n in payouts.UD_STANDARD:
        assert payouts.ud_standard_break_even(n) == payouts.break_even("underdog", "standard", n)
    for n in payouts.UD_FLEX:
        assert payouts.ud_flex_return(n, payouts.ud_flex_break_even(n)) == pytest.approx(1.0, abs=1e-9)


def test_prizepicks_is_unknown_until_a_table_is_supplied(tmp_path):
    # the coded tables carry no PrizePicks payout; the verified file table (owner sign-off 2026-11-05) is what supplies it
    assert payouts.CODED_CONFIG["prizepicks"]["entries"]["power"] is None
    assert payouts.break_even("prizepicks", config=payouts.CODED_CONFIG) is None
    assert payouts.break_even("prizepicks") == pytest.approx(3.0 ** -0.5)                   # CONFIG: the verified table
    f = tmp_path / "pp.json"
    f.write_text(json.dumps({"prizepicks": {"entries": {"power": {"2": 3.0}}, "version": "test"}}), encoding="utf-8")
    cfg = payouts.load_config(f, payouts.CODED_CONFIG)
    assert payouts.break_even("prizepicks", config=cfg) == pytest.approx(3.0 ** -0.5)
    assert payouts.break_even("prizepicks", "power", 3, config=cfg) is None
    assert payouts.break_even("underdog", config=cfg) == pytest.approx(3.5 ** -0.5)


def test_flex_table_arrives_from_json_keys_too():
    cfg = payouts.with_tables({"prizepicks": {"entries": {"flex": {"3": {"0": 3.0, "1": 1.0}}}}})
    assert 0.5 < payouts.break_even("prizepicks", "flex", 3, config=cfg) < 0.6


@pytest.mark.parametrize("variant", ["goblin", "demon", "promo", "anything-new"])
def test_alt_lines_have_no_break_even_without_a_multiplier(variant):
    assert payouts.break_even("underdog", variant=variant) is None
    assert payouts.break_even("underdog", "flex", 4, variant=variant) is None


def test_a_supplied_multiplier_gives_a_break_even():
    assert payouts.break_even("underdog", variant="demon", multiplier=1.0) == pytest.approx(3.5 ** -0.5)
    assert payouts.break_even("underdog", variant="demon", multiplier=1.5) < 3.5 ** -0.5 < \
        payouts.break_even("underdog", variant="goblin", multiplier=0.8)
    assert payouts.break_even("underdog", "flex", 4, variant="demon", multiplier=1.5) is None


def test_unknown_book_entry_or_size_is_none():
    assert payouts.break_even("sleeper") is None
    assert payouts.break_even("underdog", "power") is None
    assert payouts.break_even("underdog", "standard", 9) is None
    assert payouts.break_even(None) is None
    assert payouts.break_even("UNDERDOG") == payouts.break_even("underdog")


def test_entry_payout_and_ev_ported():
    assert payouts.ud_entry_payout(2, [1.0, 1.1])[0] == pytest.approx(3.85)
    assert payouts.ud_entry_payout(3, [1.0], "flex")[0] == 3.25
    assert payouts.ud_entry_payout(2, [None, 1.0])[0] is None
    assert payouts.ud_standard_ev([0.6, 0.6], [1.0, 1.0])[0] == pytest.approx(0.36 * 3.5 - 1)
    assert payouts.ud_standard_ev([0.6, 0.6], [None, 1.0])[0] is None


def test_modifier_rule_matches_sports():
    assert payouts.modifier_break_even(2, 1.0) == pytest.approx(3.5 ** -0.5)
    assert payouts.modifier_break_even(3, 1.0) == pytest.approx(6.5 ** (-1 / 3))


def test_straight_ev_and_fee_ported():
    assert payouts.straight_ev(0.5, 2.1) == pytest.approx(0.05)
    assert payouts.straight_ev(0.4, 2.5, 0.5) == pytest.approx(0.1)
    assert payouts.kalshi_taker_fee(0.5, 100) == 1.75
    assert payouts.kalshi_taker_fee(0.01, 1) == 0.01


def test_table_lists_every_entry_and_marks_prizepicks_unknown():
    t = payouts.break_even_table()
    assert len([r for r in t["rows"] if r["book"] == "underdog"]) == 7 + 6
    pp = [r for r in t["rows"] if r["book"] == "prizepicks"]      # verified by owner sign-off 2026-11-05: Power 2-6, Flex 2-6
    assert len(pp) == 5 + 5 and all(r["break_even"] is not None for r in pp)
    assert t["books"]["prizepicks"]["status"].startswith("VERIFIED by owner sign-off 2026-11-05")
    t0 = payouts.break_even_table(payouts.CODED_CONFIG)
    assert [r["break_even"] for r in t0["rows"] if r["book"] == "prizepicks"] == [None, None]
    assert t0["books"]["prizepicks"]["status"] == payouts.PP_STATUS


# ------------------------------------------------------------------------------------------------- gates
def test_tightest_cap_wins_and_reasons_say_why():
    g = gates.evaluate({gates.RUST, gates.SAMPLE_THIN})
    assert g.cap == 1.0 and g.reasons[0].startswith("long break") and len(g.reasons) == 2
    assert gates.evaluate({gates.STANDIN, gates.RUST}).cap == 0.0


def test_no_flags_no_cap():
    g = gates.evaluate(None)
    assert g.cap is None and g.reasons == () and g.withheld == ()


def test_unknown_flag_does_nothing_and_is_kept():
    g = gates.evaluate({"standin_typo"})
    assert g.cap is None and g.unknown == ("standin_typo",)


def test_withheld_flags_are_not_caps():
    g = gates.evaluate({gates.CONTAINED})
    assert g.withheld and g.cap is None


@pytest.mark.parametrize("kw,want", [
    ({"sample": 0}, {"sample_low"}), ({"sample": 4}, {"sample_low"}), ({"sample": 5}, {"sample_thin"}),
    ({"sample": 19}, {"sample_thin"}), ({"sample": 20}, set()), ({"days_idle": 28}, set()), ({"days_idle": 29}, {"rust"}),
    ({"market_age_s": 1800}, set()), ({"market_age_s": 1801}, {"stale_market"}), ({"market_width": 0.1}, set()),
    ({"market_width": 0.11}, {"wide_market"}), ({"market_books": 1}, {"thin_market"}), ({"market_books": 2}, set()),
    ({}, set())])
def test_facts_thresholds(kw, want):
    assert gates.facts(**kw) == want


def test_same_match_keeps_the_strongest_leg():
    legs = [("a", 0.6), ("b", 0.7), ("a", 0.65), ("a", 0.65)]
    assert gates.same_match_extras(legs) == {0, 3}
    assert gates.same_match_extras(legs, 2) == {0}
    assert gates.same_match_extras([]) == set()


def test_every_cap_is_a_stake_the_board_can_show():
    assert all(c % 0.5 == 0 for c, _ in gates.CAPS.values())


# ------------------------------------------------------------------------------------------------ policy
@pytest.mark.parametrize("p,letter", [
    (0.80, "A+"), (0.72, "A+"), (0.7199, "A"), (0.66, "A"), (0.6599, "A-"), (0.62, "A-"), (0.6199, "B+"), (0.585, "B+"),
    (0.5849, "B"), (0.55, "B"), (0.5499, "Lean"), (0.54, "Lean"), (0.45, "Lean")])
def test_letters_at_the_cut_offs(p, letter):
    assert policy.grade(p, "underdog").letter == letter


def test_cut_offs_sit_in_one_constant_and_drive_every_sports_grade(monkeypatch):
    assert policy.grade(0.60, "underdog").letter == "B+"
    monkeypatch.setattr(policy, "LETTER_CUTS", (("A+", 0.95), ("B", 0.599)))
    assert policy.grade(0.60, "underdog").letter == "B"


def test_ev_is_p_over_break_even_minus_one():
    g = policy.grade(0.60, "underdog")
    assert g.ev_vs_breakeven == pytest.approx(0.60 / 3.5 ** -0.5 - 1) and g.break_even == pytest.approx(3.5 ** -0.5)


def test_a_letter_needs_ev_above_zero():
    # flex 4 breaks even at 55.59%: 55.5% clears the B cut but not the entry
    g = policy.grade(0.555, "underdog", entry="flex", n=4)
    assert g.letter == "Lean" and g.ev_vs_breakeven < 0 and g.stake_units == 0 and "no edge" in g.reasons[0]
    assert policy.grade(0.555, "underdog").letter == "B"


def test_stake_grid_and_a_plus_rule():
    be = 3.5 ** -0.5
    stakes = {p: policy.grade(p, "underdog").stake_units for p in (0.55, 0.585, 0.62, 0.66, 0.70, 0.72, 0.80, 0.99)}
    assert stakes == {0.55: 0.0, 0.585: 0.5, 0.62: 1.0, 0.66: 1.5, 0.70: 1.5, 0.72: 2.0, 0.80: 3.0, 0.99: 3.0}
    assert policy.kelly(0.72, be) * policy.KELLY_FRACTION / policy.UNIT_BANKROLL == pytest.approx(2.49, abs=0.01)
    for p in np.linspace(0.5, 0.99, 200):
        g = policy.grade(float(p), "underdog")
        assert g.stake_units % 0.5 == 0
        assert g.stake_units < 2 or g.letter == "A+"
        assert g.letter != "Lean" or g.stake_units == 0


def test_stake_never_falls_as_the_chance_rises():
    s = [policy.grade(float(p), "underdog").stake_units for p in np.linspace(0.5, 0.99, 500)]
    assert all(b >= a for a, b in zip(s, s[1:]))


def test_kelly_is_the_textbook_figure():
    assert policy.kelly(0.6, 0.5) == pytest.approx(0.2) and policy.kelly(0.4, 0.5) == 0


def test_a_gate_caps_the_stake_and_keeps_the_letter():
    g = policy.grade(0.72, "underdog", flags={gates.RUST})
    assert g.letter == "A+" and g.stake_units == 1.0 and any("long break" in r for r in g.reasons)
    g = policy.grade(0.72, "underdog", flags={gates.STANDIN})
    assert g.letter == "A+" and g.stake_units == 0 and any("stand-in" in r for r in g.reasons)


def test_a_cap_above_the_stake_does_not_raise_it():
    assert policy.grade(0.585, "underdog", flags={gates.THIN_MARKET}).stake_units == 0.5


@pytest.mark.parametrize("flag", [gates.CONTAINED, gates.UNVALIDATED])
def test_withheld_removes_letter_and_stake_keeps_the_band(flag):
    g = policy.grade(0.72, "underdog", flags={flag})
    assert g.letter is None and g.stake_units == 0 and g.band == "A+" and g.ev_vs_breakeven > 0 and g.reasons


@pytest.mark.parametrize("variant", ["goblin", "demon", "promo"])
def test_alt_line_without_a_multiplier_gets_no_letter(variant):
    g = policy.grade(0.80, "underdog", variant)
    assert g.letter is None and g.ev_vs_breakeven is None and g.stake_units == 0 and g.band is None
    assert g.reasons[0] == "Alt line: payout unknown"


def test_alt_line_with_a_multiplier_is_graded():
    g = policy.grade(0.70, "underdog", "goblin", multiplier=0.9)
    assert g.letter == "A" and g.break_even > 3.5 ** -0.5


def test_prizepicks_gets_no_letter_until_its_table_is_supplied():
    g = policy.grade(0.70, "prizepicks", config=payouts.CODED_CONFIG)
    assert g.letter is None and g.stake_units == 0 and "Break-even unknown" in g.reasons[0]
    assert policy.grade(0.70, "prizepicks").letter == "A"           # the verified table (owner sign-off 2026-11-05)
    cfg = payouts.with_tables({"prizepicks": {"entries": {"power": {2: 3.0}}}})
    assert policy.grade(0.70, "prizepicks", config=cfg).letter == "A"


def test_grade_refuses_a_non_chance():
    with pytest.raises(ValueError):
        policy.grade(1.2, "underdog")


def test_offer_takes_the_higher_offered_side():
    o = policy.grade_offer(0.45, 0.55, {"over", "under"}, "underdog")
    assert o["chosen_side"] == "under" and o["label"] == "B Under" and o["sides"]["over"]["p"] == 0.45


def test_offer_never_shows_an_unavailable_side():
    o = policy.grade_offer(0.40, 0.60, {"over"}, "underdog")
    assert o["chosen_side"] == "over" and o["label"] == "Lean Over" and o["sides"]["under"] == {"available": False, "p": None}


def test_offer_with_no_side():
    o = policy.grade_offer(0.5, 0.5, set(), "underdog")
    assert o["chosen_side"] is None and o["grade"] is None and o["label"] == "No side offered"


def test_offer_labels_explain_a_missing_letter():
    assert policy.grade_offer(0.7, 0.3, {"over"}, "underdog", "demon")["label"] == "Alt line: payout unknown"
    assert policy.grade_offer(0.7, 0.3, {"over"}, "underdog", flags={gates.CONTAINED})["label"] == \
        gates.WITHHOLD[gates.CONTAINED]


def test_grade_carries_the_policy_version():
    from gradecore import GRADE_POLICY_VERSION
    assert policy.grade(0.6, "underdog").policy == GRADE_POLICY_VERSION == "gp-3"


# --------------------------------------------------------------------------------------------- scorecard
def _rows(n, hit=0.6, p=0.6, per_match=1, be=None, stat=None):
    """Wins evenly spaced, so the hit rate is exact in any stretch of rows."""
    out = []
    for i in range(n):
        win = int((i + 1) * hit + 1e-9) > int(i * hit + 1e-9)
        r = {"p": p, "status": "win" if win else "loss", "match_key": f"m{i // per_match}",
             "side": "over" if (i // 50) % 2 else "under"}
        if be:
            r["break_even"] = be
        if stat:
            r["stat"] = stat
        out.append(r)
    return out


def test_hit_rate_brier_log_loss_by_hand():
    rows = [{"p": 0.8, "status": "win", "match_key": "a"}, {"p": 0.6, "status": "loss", "match_key": "b"}]
    s = scorecard.score(rows)
    assert s["n"] == 2 and s["hit_rate"] == 0.5 and s["stated"] == pytest.approx(0.7) and s["gap"] == pytest.approx(-0.2)
    assert s["brier"] == pytest.approx((0.04 + 0.36) / 2)
    assert s["log_loss"] == pytest.approx(-(math.log(0.8) + math.log(0.4)) / 2)


def test_pushes_are_apart_and_never_wins():
    base = _rows(200, 0.55)
    pushes = [{"p": 0.9, "status": "push", "match_key": f"m{i}"} for i in range(50)]
    a, b = scorecard.score(base), scorecard.score(base + pushes)
    assert b["pushes"] == 50 and b["wins"] == a["wins"] and b["n"] == a["n"] == 200
    for k in ("hit_rate", "brier", "log_loss", "stated", "hit_ci", "reliability"):
        assert a[k] == b[k]


def test_only_pushes_has_no_rate():
    s = scorecard.score([{"p": 0.6, "status": "push", "match_key": "a"}])
    assert s["n"] == 0 and s["pushes"] == 1 and "hit_rate" not in s


def test_voids_and_pending_are_counted_apart():
    rows = [{"p": 0.6, "status": s, "match_key": "a"} for s in ("void", "pending", None)]
    s = scorecard.score(rows)
    assert (s["voids"], s["pending"], s["n"]) == (1, 2, 0)


def test_unknown_status_raises():
    with pytest.raises(ValueError):
        scorecard.score([{"p": 0.6, "status": "won", "match_key": "a"}])


def test_interval_is_none_from_one_match():
    rows = [{"p": 0.6, "status": "win", "match_key": "a"}, {"p": 0.6, "status": "loss", "match_key": "a"}]
    assert scorecard.score(rows)["hit_ci"] is None


def test_clustering_widens_the_interval():
    rng = np.random.default_rng(4)
    y, g = [], []
    for m in range(120):                # every leg of a match wins or loses together
        hit = float(rng.uniform() < 0.5)
        y += [hit] * 10
        g += [m] * 10
    _, lo, hi = scorecard.cluster_ci(y, g)
    _, lo2, hi2 = scorecard.cluster_ci(y, list(range(len(y))))
    assert (hi - lo) > 2 * (hi2 - lo2)


def test_clustered_interval_covers_the_truth_about_95_percent():
    hits = 0
    for seed in range(200):
        rng = np.random.default_rng(seed)
        y = (rng.uniform(size=300) < 0.55).astype(float)
        _, lo, hi = scorecard.cluster_ci(y, list(range(300)), reps=400)
        hits += lo <= 0.55 <= hi
    assert 0.90 <= hits / 200 <= 0.99


def test_bootstrap_is_seeded():
    y, g = [1, 0, 1, 1, 0, 0, 1], list("abcdefg")
    assert scorecard.cluster_ci(y, g) == scorecard.cluster_ci(y, g)
    assert scorecard.cluster_ci(y, g, seed=8) != scorecard.cluster_ci(y, g)


def test_cluster_ci_empty():
    assert scorecard.cluster_ci([], []) == (None, None, None)


def test_reliability_bins():
    rows = [{"p": 0.62, "status": "win", "match_key": "a"}, {"p": 0.64, "status": "loss", "match_key": "b"},
            {"p": 0.72, "status": "win", "match_key": "c"}, {"p": 1.0, "status": "win", "match_key": "d"}]
    rel = scorecard.score(rows)["reliability"]
    assert [(b["lo"], b["hi"], b["n"]) for b in rel] == [(0.6, 0.65, 2), (0.7, 0.75, 1), (0.95, 1.0, 1)]
    assert rel[0]["stated"] == pytest.approx(0.63) and rel[0]["hit"] == 0.5 and rel[0]["ci"] is not None
    assert rel[1]["ci"] is None


def test_grouping_by_sport_tier_book_side():
    rows = [{**r, "sport": "cs2", "tier": "A"} for r in _rows(20)] + [{**r, "sport": "nba", "tier": "B"} for r in _rows(10)]
    out = scorecard.by(rows, "sport", "tier")
    assert set(out) == {"cs2|A", "nba|B"} and out["cs2|A"]["n"] == 20 and out["nba|B"]["n"] == 10


def test_ev_at_break_even_price():
    rows = [{"status": "win", "match_key": "a", "break_even": 0.5}, {"status": "loss", "match_key": "b", "break_even": 0.5}]
    assert scorecard.ev_ci(rows)[0] == pytest.approx(0.0)
    assert scorecard.ev_ci(rows[:1])[0] == pytest.approx(1.0)


# ------------------------------------------------------------------------------------------- containment
SWITCHES = list(containment.ENV.values())


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    for name in SWITCHES:
        monkeypatch.delenv(name, raising=False)
    p = tmp_path / "containment.json"
    containment.set_path(p)
    yield p
    containment.set_path(None)


def _write(path, scopes):
    path.write_text(json.dumps({"version": containment.VERSION, "scopes": scopes}), encoding="utf-8")
    containment.reset_cache()


@pytest.mark.parametrize("sport", containment.SPORTS)
def test_nothing_known_is_contained(state_file, sport):
    assert containment.contained(sport, sport, "kills") is True
    assert containment.state(sport)["source"] == "default"


def test_unknown_sport_raises(state_file):
    with pytest.raises(ValueError):
        containment.contained("dota", "x", "kills")


def test_file_answers_per_sport_and_stat(state_file):
    _write(state_file, {"nba": {"contained": False, "stats": ["points"]}, "cs2": {"contained": True}})
    assert containment.contained("nba", "nba", "rebounds") is False
    assert containment.contained("nba", "nba", "points") is True
    assert containment.contained("cs2", "blast", "kills") is True
    assert containment.contained("lol", "lck", "kills") is True


def test_league_scope_narrows_within_a_sport(state_file):
    _write(state_file, {"cs2": {"contained": False}, "cs2:blast": {"contained": True}, "lol:lck": {"contained": False}})
    assert containment.contained("cs2", "blast", "kills") is True
    assert containment.contained("cs2", "iem", "kills") is False
    assert containment.contained("lol", "lck", "kills") is False
    assert containment.contained("lol", "lpl", "kills") is True


@pytest.mark.parametrize("sport", containment.SPORTS)
@pytest.mark.parametrize("off", ["0", "off", "false", "no"])
def test_the_owner_switch_never_releases(state_file, monkeypatch, sport, off):
    """Owner absolute rule "Never force letters on": 0 is recorded as asked and ignored; the file / default decides."""
    _write(state_file, {sport: {"contained": True, "stats": ["kills"]}})
    monkeypatch.setenv(containment.ENV[sport], off)
    containment.reset_cache()
    assert containment.contained(sport, sport, "kills") is True
    st = containment.state(sport)
    assert st["source"] == "file" and st["env_requested"] == "release"
    assert containment.env_request(sport) == {"name": containment.ENV[sport], "value": off, "asked": "release", "applied": False}
    containment.set_path(state_file.parent / "missing.json")       # nothing known + a 0: still contained
    monkeypatch.setenv(containment.ENV[sport], off)
    assert containment.contained(sport, sport, "kills") is True and containment.state(sport)["source"] == "default"
    containment.set_path(state_file)
    _write(state_file, {sport: {"contained": False}})               # released by the check: the 0 changes nothing
    assert containment.contained(sport, sport, "kills") is False


@pytest.mark.parametrize("sport", containment.SPORTS)
def test_the_owner_switch_contains_over_the_file(state_file, monkeypatch, sport):
    _write(state_file, {sport: {"contained": False}})
    monkeypatch.setenv(containment.ENV[sport], "1")
    containment.reset_cache()
    assert containment.contained(sport, sport, "kills") is True
    assert containment.state(sport)["source"] == "env"
    monkeypatch.setenv(containment.ENV[sport], "maybe")
    containment.reset_cache()
    assert containment.env_override(sport) is None and containment.contained(sport, sport, "kills") is False


def test_env_names_match_the_brief():
    assert containment.ENV == {"cs2": "SCOUT_CONTAIN_CS2", "lol": "SCOUT_CONTAIN_LOL",
                               "nba": "SPORTS_CONTAIN_NBA", "nfl": "SPORTS_CONTAIN_NFL"}


@pytest.mark.parametrize("bad", ['{"version": "ct-1", "leagues": {}}', "not json", '{"version": "ct-2", "scopes": {"nba": 1}}',
                                 '{"version": "ct-2", "scopes": {"nba": {"contained": "no"}}}'])
def test_a_bad_file_stays_contained(state_file, bad):
    state_file.write_text(bad, encoding="utf-8")
    containment.reset_cache()
    assert containment.contained("nba", "nba", "points") is True


def test_a_decision_reaches_readers_after_the_cache_window(state_file, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(containment.time, "time", lambda: now[0])
    containment.reset_cache()
    _write(state_file, {"nba": {"contained": True}})
    assert containment.contained("nba", "nba", "points") is True
    state_file.write_text(json.dumps({"version": containment.VERSION, "scopes": {"nba": {"contained": False}}}), "utf-8")
    assert containment.contained("nba", "nba", "points") is True
    now[0] += containment.CHECK_S + 1
    assert containment.contained("nba", "nba", "points") is False


def test_verdict_needs_300_settled_legs_and_pushes_do_not_help():
    rows = _rows(299, 0.65, 0.65, be=0.5345) + [{"p": 0.65, "status": "push", "match_key": "z", "break_even": 0.5345}] * 50
    v = containment.verdict(rows)
    assert v["verdict"] == "insufficient" and v["n"] == 299


def test_verdict_releases_an_honest_winning_market():
    v = containment.verdict(_rows(600, 0.64, 0.64, be=0.5345))
    assert v["verdict"] == "release" and v["window"] == 300 and v["ev_ci"][0] > 0


def test_verdict_contains_an_overstated_market():
    v = containment.verdict(_rows(600, 0.54, 0.61, be=0.5345))
    assert v["verdict"] == "contain" and abs(v["gap"]) > 0.025


def test_verdict_contains_an_honest_market_with_no_edge():
    v = containment.verdict(_rows(600, 0.535, 0.535, be=0.5345))
    assert v["verdict"] == "contain" and v["ev_ci"][0] <= 0


def test_verdict_reads_the_latest_legs():
    old = _rows(300, 0.45, 0.60, be=0.5345)
    assert containment.verdict(old + _rows(300, 0.64, 0.64, be=0.5345))["verdict"] == "release"


def test_verdict_clustering_can_keep_a_market_contained():
    # 300 legs from 6 matches: the rate looks fine, the interval does not
    rows = _rows(300, 0.64, 0.64, per_match=50, be=0.5345)
    r = []
    for i, x in enumerate(rows):
        m = i // 50
        r.append({**x, "status": "win" if m < 4 else "loss", "p": 0.66})
    v = containment.verdict(r)
    assert v["verdict"] == "contain" and v["ev_ci"][0] <= 0


def test_verdict_skips_legs_with_no_break_even():
    rows = _rows(400, 0.64, 0.64)
    assert containment.verdict(rows)["verdict"] == "insufficient"


def test_verdict_names_a_failing_stat():
    rows = _rows(300, 0.64, 0.64, be=0.5345, stat="kills") + _rows(100, 0.2, 0.6, be=0.5345, stat="hs")
    v = containment.verdict(rows)
    assert v["verdict"] == "contain:hs"


@pytest.mark.parametrize("v,after", [
    ("release", {"contained": False, "stats": []}), ("contain", {"contained": True, "stats": ["a"]}),
    ("contain:b,c", {"contained": False, "stats": ["a", "b", "c"]}), ("insufficient", {"contained": False, "stats": ["a"]}),
    ("keep", {"contained": False, "stats": ["a"]}), ("", {"contained": False, "stats": ["a"]}), (None, {"contained": False, "stats": ["a"]})])
def test_decide_ported(v, after):
    assert containment.decide({"contained": False, "stats": ["a"]}, v) == after


def test_apply_verdicts_writes_state_log_and_keeps_since(state_file):
    a = containment.apply_verdicts({"cs2": {"verdict": "release", "n": 300, "why": "ok"}}, "2026-11-01", "test")
    assert a["cs2"]["changed"] and a["cs2"]["before"]["contained"] is True
    assert containment.contained("cs2", "blast", "kills") is False
    b = containment.apply_verdicts({"cs2": {"verdict": "insufficient"}, "lol": {"verdict": "contain"}}, "2026-11-02", "test")
    assert not b["cs2"]["changed"] and not b["lol"]["changed"]
    doc = json.loads(state_file.read_text(encoding="utf-8"))
    assert doc["scopes"]["cs2"]["since"] == "2026-11-01" and len(doc["log"]) == 3 and doc["log"][0]["n"] == 300


def test_apply_verdicts_trims_the_log(state_file, monkeypatch):
    monkeypatch.setattr(containment, "LOG_MAX", 3)
    for d in range(5):
        containment.apply_verdicts({"nba": {"verdict": "keep"}}, f"day{d}", "test")
    assert len(json.loads(state_file.read_text(encoding="utf-8"))["log"]) == 3


def test_apply_verdicts_needs_a_path(monkeypatch):
    containment.set_path(None)
    with pytest.raises(ValueError):
        containment.apply_verdicts({}, "d", "s")


def test_snapshot_lists_all_four(state_file):
    s = containment.snapshot()
    assert set(s["sports"]) == set(containment.SPORTS) and set(s["env"]) == set(SWITCHES)


# ----------------------------------------------------------------------------------------- the whole path
def test_one_leg_through_the_core():
    from gradecore.VERSION import GRADE_POLICY_VERSION
    p = blend.blend(0.70, devig.worst_case((1.8, 2.05), 0)[0], 0.8)
    a = calibrate.fit(*_overstated(6000))
    g = policy.grade(calibrate.apply(a, p), "underdog")
    assert 0.5 < g.break_even < 0.6 and g.letter in ("Lean", "B", "B+", "A-") and g.policy == GRADE_POLICY_VERSION


def test_core_uses_only_numpy_and_the_standard_library():
    import ast
    import sys
    from pathlib import Path
    core = Path(blend.__file__).resolve().parent
    for f in core.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for n in names:
                top = n.split(".")[0]
                assert top == "numpy" or top in sys.stdlib_module_names, (f.name, n)
