"""Sports Slip Studio on the VERIFIED PrizePicks payout table (owner 2026-11-06: "update the Sports Slip Studio so it
uses the verified PrizePicks payout table. Make sure settlement still works correctly after the change.").

The table is gradecore/pickem_payouts.json (owner sign-off 2026-11-05), read by sports.payouts as data. Every case
below is compared with the main site's gradecore.payouts for the same inputs: after the P-DNP tier drops (written out
independently here), the settled multiple must equal gradecore.payouts.tiers("prizepicks", entry, picks)[correct]
(0 for a missing tier). Pure functions only - no store, no production data. Every name is a TEST FIXTURE."""
import json
import math

import pytest

from gradecore import payouts as gp
from gradecore import slip as gslip
from sports import payouts
from sports.slips import builder, joint, rules, settle


def leg(i, team=None, p=0.6, push=0.0, variant="standard", promotional=False):
    return {"leg_id": f"prizepicks|o{i}|over", "platform": "prizepicks", "league": "nba", "offer_id": f"o{i}",
            "record_id": f"r{i}", "event_id": f"e{i}", "player_id": f"p{i}", "player": f"P{i}",
            "team": team or f"T{i}", "side": "over", "line": 20.5, "variant": variant, "promotional": promotional,
            "p_win": p, "p_loss": 1 - p - push, "p_push": push, "multiplier": None, "weight": math.log(p),
            "prop_keys": (f"p{i}|pts",)}


def legs(n, **kw):
    return [leg(i, **kw) for i in range(1, n + 1)]


# P-DNP (help center, last updated October 19, 2026), written out here independently of rules.PP_REVERT
DROP = {("power", n): ("power", n - 1) for n in range(3, 7)}
DROP.update({("flex", 6): ("flex", 5), ("flex", 5): ("flex", 4), ("flex", 4): ("flex", 3), ("flex", 3): ("power", 2)})


def reference(entry, results):
    """The main site's payout for the same inputs: gradecore.payouts.tiers at the reverted entry (None = unknown)."""
    at = (entry, len(results))
    for _ in range(sum(1 for r in results if r in ("void", "push"))):
        at = DROP.get(at)
        if at is None:
            return None
    t = gp.tiers("prizepicks", at[0], at[1], gp.pickem_config())
    return None if t is None else t.get(sum(1 for r in results if r == "win"), 0.0)


# ------------------------------------------------------------------------------------------- one table, two readers
def test_sports_reads_the_same_verified_table_as_the_main_site():
    raw = json.loads(payouts.PP_FILE.read_text(encoding="utf-8"))["prizepicks"]
    assert raw["verified"] is True and payouts.PP_VERSION == raw["version"] == "pp-payouts-2026-10-19"
    assert payouts.PP_FILE.resolve() == gp.PICKEM_FILE.resolve()
    cfg = gp.pickem_config()
    for n in range(2, 7):
        assert payouts.pp_tiers("power", n) == gp.tiers("prizepicks", "power", n, cfg)
        assert payouts.pp_tiers("flex", n) == gp.tiers("prizepicks", "flex", n, cfg)
        for e in ("power", "flex"):
            assert payouts.pp_in_app(e, n) == gp.in_app("prizepicks", e, n)
    assert payouts.PP_POWER == {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}
    assert rules.PP_STANDARD_POWER == payouts.PP_POWER
    assert rules.PP_STANDARD_FLEX == {n: gp.tiers("prizepicks", "flex", n, cfg) for n in range(2, 7)}
    assert payouts.pp_tiers("power", 7) is None and payouts.pp_tiers("goblin", 2) is None


def test_power_2_6_and_flex_3_6_offered_flex_2_hidden():
    assert rules.size_range("prizepicks", "power") == (2, 6) and rules.size_range("prizepicks", "flex") == (3, 6)
    assert not payouts.pp_in_app("flex", 2) and payouts.pp_in_app("flex", 3) and payouts.pp_in_app("power", 2)
    with pytest.raises(builder.SettingsError):
        builder.normalize({"sizes": {2: 1}, "entry_type": "flex"}, "prizepicks")
    assert builder.normalize({"sizes": {3: 1}, "entry_type": "flex"}, "prizepicks")["entry_type"] == "flex"
    two = rules.entry_check(legs(2), "prizepicks", "flex")
    assert two["eligible"] is False and "3-6 picks" in two["refusals"][0]
    assert rules.schedule("prizepicks", "flex", legs(2))["figures_shown"] is False


@pytest.mark.parametrize("n", range(2, 7))
def test_power_schedule_shows_the_verified_figure(n):
    s = rules.schedule("prizepicks", "power", legs(n))
    assert s["figures_shown"] and s["member"][0] == {"outcome": f"All {n} correct", "multiple": payouts.PP_POWER[n]}
    assert "not published" not in json.dumps(s["member"]) and "in the app" not in json.dumps(s["member"])


@pytest.mark.parametrize("n", range(3, 7))
def test_flex_schedule_lists_every_tier(n):
    s = rules.schedule("prizepicks", "flex", legs(n))
    shown = {int(r["outcome"].split(" of ")[0]): r["multiple"] for r in s["member"] if " of " in r["outcome"]}
    assert s["figures_shown"] and shown == gp.tiers("prizepicks", "flex", n, gp.pickem_config())
    assert any(r["outcome"] == "A pick is DNP or ties" for r in s["member"])
    if n == 3:
        assert any("2-pick Power" in (r.get("text") or "") for r in s["member"])


@pytest.mark.parametrize("kw", [{"variant": "goblin"}, {"variant": "demon"}, {"promotional": True}])
def test_goblin_demon_promo_stay_unavailable(kw):
    ls = legs(3)
    ls[1] = leg(2, **kw)
    s = rules.schedule("prizepicks", "power", ls)
    assert not s["figures_shown"] and all(r["multiple"] is None for r in s["member"])
    assert joint.published_return("prizepicks", "power", ls)["value"] is None
    r = settle.entry_result("prizepicks", "power", ls, ["win", "win", "win"])
    assert r["status"] == "graded_payout_unknown" and r["payout_multiple"] is None and not r["payout_known"]
    lost = settle.entry_result("prizepicks", "power", ls, ["win", "loss", "win"])
    assert (lost["status"], lost["payout_multiple"], lost["payout_known"]) == ("lost", 0.0, True)


# ------------------------------------------------------------------------------------------------------ settlement
CASES = [
    # all hit
    *[("power", ["win"] * n, "won") for n in range(2, 7)],
    *[("flex", ["win"] * n, "won") for n in range(3, 7)],
    # a miss on power
    ("power", ["win", "loss"], "lost"), ("power", ["win"] * 5 + ["loss"], "lost"),
    # Flex partial tiers and beyond the table
    ("flex", ["win", "win", "loss"], "partial"),
    ("flex", ["win", "win", "win", "loss"], "partial"),
    ("flex", ["win"] * 4 + ["loss"], "partial"), ("flex", ["win"] * 3 + ["loss"] * 2, "partial"),
    ("flex", ["win"] * 5 + ["loss"], "partial"), ("flex", ["win"] * 4 + ["loss"] * 2, "partial"),
    ("flex", ["win", "loss", "loss"], "lost"), ("flex", ["win", "win", "loss", "loss"], "lost"),
    ("flex", ["win"] * 3 + ["loss"] * 3, "lost"),
    # DNPs (void) drop one tier each
    ("power", ["win", "win", "win", "void"], "won"),                    # 4 Power -> 3 Power 6x
    ("power", ["win"] * 4 + ["void", "void"], "won"),                   # 6 Power -> 4 Power 10x
    ("power", ["win", "loss", "void"], "lost"),
    ("flex", ["win", "win", "loss", "win", "void"], "partial"),         # 5 Flex -> 4 Flex, 3 of 4 = 1.5x
    ("flex", ["win"] * 4 + ["void", "void"], "won"),                    # 6 Flex -> 4 Flex 6x
    ("flex", ["win", "win", "void"], "won"),                            # 3 Flex -> 2 POWER 3x (not Flex 2's 2x)
    ("flex", ["win", "loss", "void"], "lost"),                          # 3 Flex -> 2 Power: a miss pays 0 (not 0.5x)
    # ties (push) drop one tier like a DNP
    ("power", ["win", "win", "push"], "won"),                           # 3 Power -> 2 Power 3x
    ("flex", ["win", "win", "loss", "push"], "partial"),                # 4 Flex -> 3 Flex, 2 of 3 = 1x
    ("flex", ["win", "push", "win"], "won"),                            # 3 Flex -> 2 Power 3x
    # a DNP and a tie together
    ("flex", ["win"] * 4 + ["void", "push"], "won"),                    # 6 Flex -> 4 Flex 6x
    ("flex", ["win", "win", "loss", "win", "void", "push"], "partial"),  # 6 Flex -> 4 Flex, 3 of 4 = 1.5x
]


@pytest.mark.parametrize("entry,results,status", CASES)
def test_settlement_matches_the_main_site_table(entry, results, status):
    r = settle.entry_result("prizepicks", entry, legs(len(results)), results)
    ref = reference(entry, results)
    assert ref is not None
    assert r["status"] == status and r["payout_known"] is True
    assert r["payout_multiple"] == pytest.approx(ref), (r, ref)
    assert "member_display" not in r


def test_flex_three_with_a_dnp_reverts_to_two_pick_power():
    r = settle.entry_result("prizepicks", "flex", legs(3), ["win", "win", "void"])
    assert r["reverted_to"] == {"entry_type": "power", "picks": 2} and r["payout_multiple"] == 3.0


@pytest.mark.parametrize("entry,results", [
    ("power", ["win", "void"]), ("power", ["loss", "void"]),           # 2-Pick Power -> refund
    ("flex", ["win", "void", "void"]),                                 # DNPs leave one pick
    ("power", ["win", "void", "void", "void"]),
])
def test_dnps_below_two_picks_refund(entry, results):
    r = settle.entry_result("prizepicks", entry, legs(len(results)), results)
    assert (r["status"], r["payout_multiple"], r["payout_known"]) == ("refunded", 1.0, True)


def test_dnps_to_one_team_refund_but_ties_do_not():
    ls = [leg(1, "NYK"), leg(2, "NYK"), leg(3, "BOS")]
    assert settle.entry_result("prizepicks", "power", ls, ["win", "win", "void"])["status"] == "refunded"
    tie = settle.entry_result("prizepicks", "power", ls, ["win", "win", "push"])
    assert (tie["status"], tie["payout_multiple"]) == ("won", reference("power", ["win", "win", "push"])) == ("won", 3.0)


@pytest.mark.parametrize("results,status,mult,known", [
    (["win", "push"], "graded_payout_unknown", None, False),           # ties leave 1 pick: no published payout
    (["push", "push", "win"], "graded_payout_unknown", None, False),
    (["push", "push", "loss"], "lost", 0.0, True),
    (["push", "push"], "graded_payout_unknown", None, False),
])
def test_ties_never_refund_and_one_pick_left_has_no_payout(results, status, mult, known):
    r = settle.entry_result("prizepicks", "power", legs(len(results)), results)
    assert (r["status"], r["payout_multiple"], r["payout_known"]) == (status, mult, known)
    assert gp.tiers("prizepicks", "power", 1, gp.pickem_config()) is None      # the main site has no 1-pick payout either


def test_pending_leg_keeps_the_slip_pending():
    r = settle.entry_result("prizepicks", "flex", legs(3), ["win", None, "win"])
    assert r["status"] == "pending" and r["payout_multiple"] is None


def test_settle_slip_with_no_leg_settled_is_pending():
    ls = legs(3)
    by = {("prizepicks", l["offer_id"], "over"): None for l in ls}
    r = settle.settle_slip({"platform": "prizepicks", "entry_type": "power", "legs": ls}, by)
    assert r["status"] == "pending"                                     # no leg settled yet


# --------------------------------------------------------------------- Slip Studio expected return = main site's EV
@pytest.mark.parametrize("entry,ps", [
    ("power", [0.6, 0.55]), ("power", [0.58, 0.6, 0.62]), ("power", [0.55] * 6),
    ("flex", [0.6, 0.55, 0.58]), ("flex", [0.6] * 4), ("flex", [0.57, 0.6, 0.62, 0.55, 0.59]), ("flex", [0.6] * 6),
])
def test_published_return_equals_gradecore_entry_ev(entry, ps):
    ls = [leg(i, p=p) for i, p in enumerate(ps, 1)]
    r = joint.published_return("prizepicks", entry, ls)
    ref = gslip.entry_ev(ps, "prizepicks", entry, gp.pickem_config())
    assert r["shown"] and ref["available"] and r["value"] == pytest.approx(ref["expected_multiple"], abs=1e-12)


def test_published_return_with_pushes_uses_the_tier_drops():
    ls = [leg(1, p=0.5, push=0.1), leg(2, p=0.6), leg(3, p=0.55)]
    r = joint.published_return("prizepicks", "power", ls)
    want = 0.5 * 0.6 * 0.55 * 6.0 + 0.1 * 0.6 * 0.55 * 3.0                  # all hit 6x; leg 1 ties -> 2-pick Power 3x
    assert r["shown"] and r["value"] == pytest.approx(want, abs=1e-12)


def test_flex_two_is_not_offered_by_the_studio():
    r = joint.published_return("prizepicks", "flex", legs(2))
    assert r["value"] is None and "no 2-pick flex" in r["why"]


def test_ranking_note_has_no_unpublished_wording():
    note = builder.RANKING["prizepicks"]["note"].lower()
    assert builder.RANKING["prizepicks"]["by"] == "expected_return"
    assert "not published" not in note and "in the app" not in note
