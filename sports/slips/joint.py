"""Joint probability of a slip, and the INTERNAL payout expectation (never a member-facing EV feature, brief 7 / 10).

joint(legs)
  Distinct games: P(all picks win) = product of each leg's UNCONDITIONAL win probability, labelled an ESTIMATE that
  assumes the games are independent (not measured - zero correlation is never asserted). Same-game legs: no validated
  dependence model exists, so the joint probability is REFUSED (available False) rather than multiplied.
  A leg's probabilities are the forecast's own (p_win / p_loss / p_push for the chosen side, summing to 1); the chance
  that a player does not play (a void) is not modelled by the forecasts and is said so.

expectation(platform, entry_type, legs)
  E[payout multiple] over every win / loss / push combination of the legs (3^n, n <= 8) under the same independence
  assumption, settled through settle.entry_result - so void / tie reduction and flex tables are exactly the
  settlement rules. Net expectation = E[multiple] - 1. Unknown payouts in a reachable outcome -> None. For a single
  straight bet this is p * d - 1 without pushes and p_win * (d - 1) - p_loss with pushes refunded (payouts.straight_ev;
  tested). Used only in the internal slip log.
"""
from __future__ import annotations

from itertools import product

from . import settle

INDEPENDENCE = "estimate: multiplies each pick's chance, assuming the games are independent (not measured)"
VOID_NOTE = "the chance a player does not play is not in the forecasts"


def joint(legs):
    evs = [l.get("event_id") for l in legs]
    if any(e is None for e in evs) or len(set(evs)) < len(evs):
        return {"available": False, "p_all_win": None, "p_no_loss": None,
                "reason": "two picks from one game: no validated model of how they move together, so no joint "
                          "chance is given"}
    if any(l.get("p_win") is None for l in legs):
        return {"available": False, "p_all_win": None, "p_no_loss": None, "reason": "a pick has no forecast chance"}
    p_all, p_no_loss = 1.0, 1.0
    for l in legs:
        p_all *= l["p_win"]
        p_no_loss *= l["p_win"] + (l.get("p_push") or 0.0)
    return {"available": True, "p_all_win": p_all, "p_no_loss": p_no_loss, "label": INDEPENDENCE, "note": VOID_NOTE}


def outcome_distribution(legs):
    """[(probability, [win|loss|push per leg])] under independence."""
    opts = []
    for l in legs:
        o = [("win", l["p_win"]), ("loss", l["p_loss"]), ("push", l.get("p_push") or 0.0)]
        opts.append([x for x in o if x[1] > 0])
    out = []
    for combo in product(*opts):
        p = 1.0
        for _, q in combo:
            p *= q
        out.append((p, [r for r, _ in combo]))
    return out


def expectation(platform, entry_type, legs, payout=None):
    """-> {"expected_multiple", "net", "basis"} or None fields when a reachable payout is unknown.
    payout(results) -> multiple|None overrides the platform rules (tests / straight bets)."""
    if len(legs) > 8:
        raise ValueError("at most 8 legs")
    j = joint(legs)
    if not j["available"]:
        return {"expected_multiple": None, "net": None, "basis": j["reason"]}
    e = 0.0
    for p, res in outcome_distribution(legs):
        m = payout(res) if payout else settle.entry_result(platform, entry_type, legs, res)["payout_multiple"]
        if m is None:
            return {"expected_multiple": None, "net": None, "basis": "a reachable outcome has no published payout"}
        e += p * m
    return {"expected_multiple": e, "net": e - 1.0, "basis": INDEPENDENCE + "; " + VOID_NOTE + "; internal only"}
