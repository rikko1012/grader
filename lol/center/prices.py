"""
What each LoL market field means - audited 2026-10-17 on the saved responses (lol/center/markets.py archive) and the
platforms' own documentation. Nothing here is a model input or a grade input; it classifies offers so a price is never
misread as a probability.

Underdog (api.underdogfantasy.com/v1/over_under_lines?sport_id=LOL)
  options[].payout_multiplier   INDIVIDUAL-PICK PAYOUT MODIFIER (documented: help.underdogsports.com article 13780101,
                                read 2026-10-17 - "the base multiplier assumes a standard 1.0x multiplier for every
                                selection ... a pick with a 0.7x multiplier [lowers] your total payout"). Never 1/x.
  options[].decimal_price / american_price
                                UNDOCUMENTED. Observed on the 41 archived LoL offers (2026-10-17): present on every
                                side shown; on the 18 two-sided offers the two prices always carry 4.8-5.3% overround
                                and are not a fixed rescale of the payout modifier (ratio 1.84-2.01; equal 1.0x/1.0x
                                modifiers with different prices). They behave like two-sided odds, but Underdog does
                                not document them: classified "undocumented_two_sided_price" - shown for research
                                with proportional margin removal, never used as the model probability or in a grade.
  options[].odds.fantasy.*      UNDOCUMENTED ("precision" type; integer probabilities summing to 100-104 per line).
                                Same treatment as decimal_price.
  one side listed               23 of the 41 offers list Higher only: the other side is NOT OFFERED (not a 50/50).
  line_type "balanced"          the ordinary line; alternates (has_alternates) would be separate offers.
  Full-entry payouts            documented base table (2-pick 3.5x ... 8-pick 120x; flex tables) - an ENTRY property,
                                kept apart from any single pick's hit probability.
PrizePicks (partner-api.prizepicks.com, league 121)
  no prices at all              every PrizePicks LoL offer is PRICE-UNVERIFIED.
  odds_type standard            the ordinary line (both sides offered unless allowed_wager_types says otherwise).
  odds_type goblin / demon      ALTERNATE lines with a lower / higher payout (help center); More only when
                                allowed_wager_types says so. Never an ordinary-line anchor.
  is_promo / flash_sale_line_score   PROMOTIONAL lines: never an anchor.
Thunderpick                      the integration (thunderpick.py) is CS2-only (esport/csgo); LoL coverage NOT verified -
                                no LoL Thunderpick data. Polymarket LoL series prices (lol/odds.py) are match-level
                                prediction-market prices, never prop prices.

Proportional margin removal (documented baseline, only for two-sided decimal prices at the SAME line):
    p_over = (1 / d_over) / (1 / d_over + 1 / d_under)
e.g. 1.55 / 2.11 -> 0.577. It is a market baseline, not the true chance, and it says nothing about another threshold
(no "fair line" is derived from one priced line).
"""

UD_PAYOUT_DOC = "https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts"


def no_vig(d_over, d_under):
    """Proportional margin removal for two-sided decimal prices at the same line -> (p_over, overround) or None."""
    try:
        a, b = 1.0 / float(d_over), 1.0 / float(d_under)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if a <= 0 or b <= 0:
        return None
    return a / (a + b), a + b


def classify(platform, offer):
    """-> {"kind", "anchor_eligible", "sides_offered", "payout_modifiers", "undocumented_no_vig_p_over", "note"}."""
    sides = offer.get("sides") or {}
    ex = offer.get("extra") or {}
    if platform == "underdog":
        listed = [k for k in ("higher", "lower") if k in sides and (sides[k].get("status") in (None, "active"))]
        mods = {k: sides[k].get("payout_multiplier") for k in listed}
        out = {"kind": "ordinary" if offer.get("odds_type") in (None, "balanced") else "alternate",
               "sides_offered": listed, "payout_modifiers": mods, "undocumented_no_vig_p_over": None, "overround": None}
        if len(listed) == 2:
            nv = no_vig(sides["higher"].get("decimal_price"), sides["lower"].get("decimal_price"))
            if nv:
                out["undocumented_no_vig_p_over"], out["overround"] = round(nv[0], 4), round(nv[1], 4)
            out["price_status"] = "undocumented_two_sided_price"
        else:
            out["price_status"] = "one_side_offered"
        out["anchor_eligible"] = out["kind"] == "ordinary" and len(listed) == 2
        out["note"] = "payout_multiplier is a payout modifier (documented), not odds; decimal_price is undocumented"
        return out
    ot = offer.get("odds_type") or "standard"
    promo = bool(ex.get("is_promo") or ex.get("flash_sale_line_score"))
    kind = "promotional" if promo or ot == "promo" else ("alternate" if ot in ("goblin", "demon") else "ordinary")
    listed = [k for k in ("higher", "lower") if (sides.get(k) or {}).get("offered")]
    return {"kind": kind, "sides_offered": listed, "payout_modifiers": None, "price_status": "price_unverified",
            "undocumented_no_vig_p_over": None, "overround": None, "anchor_eligible": kind == "ordinary" and len(listed) == 2,
            "note": "PrizePicks lists no prices"}
