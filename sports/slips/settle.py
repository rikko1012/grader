"""Slip (entry) settlement per platform rule, from the canonical leg settlements (sports/settlement.py records).

entry_result(platform, entry_type, legs, results) is pure: results[i] in win / loss / push / void / unresolved / None.
  Underdog  ties are voids (U-TIE2). Void legs drop out and the entry pays for the active picks (U-TIE: "as if the
            entry were a pick 2 instead of 3"); fewer than 2 active picks or active picks from one team -> refunded.
            Flex reverts down one entry per void along U-FLEX's chain (3-pick flex -> 2-pick standard). The printed
            4-pick-flex revert (3x) conflicts with the 3-pick flex payout (3.25x): that all-correct payout is unknown.
            A leg multiplier other than 1.0 -> payout unknown (combination unpublished; the shift survives voids).
  PrizePicks  DNP (void) and ties drop out, one tier each (P-DNP). DNPs that leave fewer than 2 picks or one team ->
            refunded; ties never make the lineup ineligible (a lineup left with one pick by ties has no published
            payout). Payout = the standard Minimum Guarantee rate (P-OUT), internal only: the lineup's own payout
            can differ and a leaderboard win can pay more (or pay a lineup with a miss).
  Both      a lost Higher/More pick may have been a Rescue / Reboot (left in the first half, did not return) - not
            visible in box scores, flagged in notes.

settle_slip(slip, settlements_by_key) maps a logged slip's legs to their CURRENT leg settlement revisions. A corrected
leg result (a new settlement revision) changes the slip result; log.record_settlement stores that as a new slip
settlement revision that names the one it supersedes.
"""
from __future__ import annotations

from .. import payouts
from . import rules

PENDING = ("unresolved", None)
SETTLE_VERSION = "ss-1"


def _teams(legs):
    t = [l.get("team") for l in legs]
    return None if any(not x for x in t) else len(set(t))


def _result(status, mult=None, known=True, **kw):
    out = {"status": status, "payout_multiple": mult, "payout_known": known, "notes": [], "settle_version": SETTLE_VERSION}
    out.update(kw)
    return out


def _rescue_notes(platform, legs, results):
    lost_over = [l for l, r in zip(legs, results) if r == "loss" and l.get("side") == "over"]
    if not lost_over:
        return []
    word = "Rescue" if platform == "underdog" else "Reboot"
    return [f"{word} not checked: a lost {rules.SIDE_WORD[platform]['over']} pick on a player who left in the first "
            "half and did not return can change this result (not visible in box scores)"]


def entry_result(platform, entry_type, legs, results):
    if len(legs) != len(results):
        raise ValueError("one result per leg")
    if any(r in PENDING for r in results):
        return _result("pending", None, False, active=None, removed=None, losses=None)
    if platform == "underdog":
        out = _underdog(entry_type, legs, results)
    elif platform == "prizepicks":
        out = _prizepicks(entry_type, legs, results)
    else:
        return _result("unsupported", None, False, reason=f"{platform} is not a pick'em platform")
    out["notes"] += _rescue_notes(platform, legs, results)
    return out


def _underdog(entry_type, legs, results):
    active = [(l, r) for l, r in zip(legs, results) if r in ("win", "loss")]
    removed = len(legs) - len(active)
    losses = sum(1 for _, r in active if r == "loss")
    base = dict(active=len(active), removed=removed, losses=losses, rule="U-TIE/U-TIE2/U-FLEX")
    nt = _teams([l for l, _ in active])
    if len(active) < 2 or nt == 1:
        return _result("refunded", 1.0, True, reason="fewer than 2 active picks or active picks from one team",
                       **base)
    if nt is None:
        return _result("graded_rule_unchecked", None, False, reason="a pick has no team: two-team rule not checked",
                       **base)
    mults = [l.get("multiplier") for l in legs]
    plain = all(m is not None and abs(float(m) - 1.0) < 1e-9 for m in mults)
    etype, picks, conflict = entry_type, len(legs), False
    if entry_type == "flex":
        for _ in range(removed):
            tgt = rules.UD_FLEX_REVERT.get(picks) if etype == "flex" else None
            if tgt is None:
                break
            if picks == 4:
                conflict = True                  # printed 3x vs the 3-pick flex table's 3.25x
            etype, picks = tgt[0], tgt[1]
    else:
        picks = len(active)
    base["reverted_to"] = {"entry_type": etype, "picks": picks}
    if etype == "standard":
        if losses:
            return _result("lost", 0.0, True, **base)
        mult = payouts.UD_STANDARD.get(picks)
        status = "won"
    else:
        mult = payouts.UD_FLEX.get(picks, {}).get(losses, 0.0)
        status = "won" if losses == 0 else ("partial" if mult > 0 else "lost")
        if mult == 0.0:
            return _result("lost", 0.0, True, **base)
        if conflict and losses == 0:
            return _result(status, None, False, reason="Underdog prints 3x and 3.25x for this reverted entry", **base)
    if not plain:
        return _result(status, None, False, reason="a pick multiplier is not 1.0: the combined payout is not "
                                                   "published (" + payouts.MODIFIER_RULE + ")", **base)
    return _result(status, mult, True, basis=f"Underdog published payouts {payouts.UD_TABLE_VERSION}", **base)


def _prizepicks(entry_type, legs, results):
    dnp = [l for l, r in zip(legs, results) if r == "void"]
    ties = [l for l, r in zip(legs, results) if r == "push"]
    remaining = [l for l, r in zip(legs, results) if r != "void"]
    active = [(l, r) for l, r in zip(legs, results) if r in ("win", "loss")]
    correct = sum(1 for _, r in active if r == "win")
    base = dict(active=len(active), removed=len(dnp) + len(ties), losses=len(active) - correct, rule="P-DNP/P-OUT",
                member_display=False)
    if dnp:
        nt = _teams(remaining)
        if len(remaining) < 2 or nt == 1:
            return _result("refunded", 1.0, True, reason="DNPs left fewer than 2 picks or one team", **base)
    m = len(active)
    base["reverted_to"] = {"entry_type": entry_type, "picks": m}
    if m < 2:
        if m == 1 and correct == 0:
            return _result("lost", 0.0, False, reason="no Minimum Guarantee; a leaderboard payout is not knowable",
                           **base)
        return _result("graded_payout_unknown", None, False,
                       reason="ties left fewer than 2 picks: PrizePicks publishes no payout for that", **base)
    basis = "PrizePicks standard Minimum Guarantee rate (P-OUT); the lineup's own rate can differ and a leaderboard " \
            "win pays the higher amount"
    if entry_type == "power":
        if correct < m:
            return _result("lost", 0.0, False, basis=basis + " (a leaderboard win can still pay)", **base)
        return _result("won", rules.PP_STANDARD_POWER.get(m), False, basis=basis, **base)
    mult = rules.PP_STANDARD_FLEX.get(m, {}).get(correct, 0.0)
    status = "won" if correct == m else ("partial" if mult > 0 else "lost")
    return _result(status, mult, False, basis=basis, **base)


def leg_status(settlement):
    """A Settlement record -> win / loss / push / void / unresolved."""
    return None if settlement is None else settlement.status


def current_settlements(con, legs):
    """{(platform, offer_id, side): latest Settlement} from the canonical store (read-only)."""
    from .. import store
    out = {}
    for l in legs:
        k = (l["platform"], l["offer_id"], l["side"])
        if k in out:
            continue
        out[k] = store.latest(con, "settlement", l["platform"], f"{l['offer_id']}|{l['side']}")
    return out


def settle_slip(slip, by_key):
    legs = slip["legs"]
    from ..settlement import at_line
    sts = [by_key.get((l["platform"], l["offer_id"], l["side"])) for l in legs]
    status = [at_line(s, l.get("line"), l["side"])[0] for l, s in zip(legs, sts)]   # each leg at ITS OWN line
    res = entry_result(slip["platform"], slip["entry_type"], legs, status)
    res["leg_results"] = [{"offer_id": l["offer_id"], "side": l["side"], "status": st,
                           "value": getattr(s, "source_value", None), "settlement_id": getattr(s, "settlement_id", None),
                           "revision": getattr(s, "revision", None)} for l, s, st in zip(legs, sts, status)]
    return res
