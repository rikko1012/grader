"""Pick'em payout tables and the break-even arithmetic derived from them (owner brief 3 and 7).

UNDERDOG - published base payouts, read 2026-10-23 20:3x UTC from
  https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts ("Updated over a month ago";
  payouts include the returned entry fee; "the base multiplier assumes a standard 1.0x multiplier for every
  selection"; a pick's modifier raises or lowers the payout; correlated projections can also modify it; "Your modifier
  pick must be correct to receive the max payout boost"; flex 3-8 picks; 6+ pick flex allows 2 losses).
  How several modifiers combine is NOT stated: payout = base x product(modifiers) is an ASSUMPTION
  (MODIFIER_RULE), labelled as such everywhere it is used.
PRIZEPICKS - payout tables are in-app only (platform_rules.py): PP_TABLE is None and every PrizePicks payout /
  break-even is UNVERIFIED. Nothing here invents one.

Break-even per pick (equal chance p on every pick, all modifiers 1.0):
  standard n-pick:  p^n * B_n = 1           ->  p* = B_n ** (-1/n)
  flex n-pick:      sum_k C(n,k) p^(n-k) (1-p)^k * F_n[k] = 1   (k = losses paid), solved by bisection
The data branch's 53.45% is the STANDARD 2-PICK value only ((1/3.5) ** 0.5); larger and flex entries differ
(break_even_table()).

Straight bets (no push): E[net] = p * d - 1. With a push refunding the stake: E[net] = p_win * (d - 1) - p_loss.
These are payout mathematics for internal evaluation - never a member-facing EV feature (brief section 7).
"""
from __future__ import annotations

from math import comb

UD_SOURCE = "https://help.underdogsports.com/en/articles/13780101-pick-em-standard-flex-entry-payouts"
UD_READ_AT = "2026-10-23T20:35:00Z"
UD_TABLE_VERSION = "ud-payouts-2026-10-23"
UD_STANDARD = {2: 3.5, 3: 6.5, 4: 12.0, 5: 20.0, 6: 35.0, 7: 65.0, 8: 120.0}
UD_FLEX = {                                         # losses -> payout multiple (entry fee included)
    3: {0: 3.25, 1: 1.09},
    4: {0: 6.0, 1: 1.4},
    5: {0: 10.0, 1: 2.5},
    6: {0: 25.0, 1: 2.6, 2: 0.25},
    7: {0: 40.0, 1: 2.75, 2: 0.5},
    8: {0: 80.0, 1: 3.0, 2: 1.0},
}
MODIFIER_RULE = "assumed: entry payout = base x product of pick modifiers (Underdog does not publish the combination)"
PP_TABLE = None
PP_STATUS = "unverified: PrizePicks payout tables are in-app only"


def ud_standard_break_even(n: int) -> float:
    return UD_STANDARD[n] ** (-1.0 / n)


def ud_flex_return(n: int, p: float) -> float:
    """Expected payout multiple of an n-pick flex entry with chance p on every pick (modifiers 1.0)."""
    tab = UD_FLEX[n]
    return sum(comb(n, k) * p ** (n - k) * (1 - p) ** k * mult for k, mult in tab.items())


def ud_flex_break_even(n: int) -> float:
    lo, hi = 0.3, 0.9
    for _ in range(200):
        mid = (lo + hi) / 2
        if ud_flex_return(n, mid) < 1.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def ud_entry_payout(n: int, modifiers, entry="standard"):
    """Max payout multiple of an entry (all picks correct) under MODIFIER_RULE -> (multiple, basis)."""
    base = UD_STANDARD[n] if entry == "standard" else UD_FLEX[n][0]
    m = base
    for x in modifiers:
        if x is None:
            return None, "a pick's payout modifier is missing"
        m *= float(x)
    return m, MODIFIER_RULE


def ud_standard_ev(probs, modifiers):
    """Expected net return per unit of a standard entry with independent picks (an ESTIMATE: independence is an
    assumption, not a measured fact) -> (ev, basis)."""
    n = len(probs)
    pay, basis = ud_entry_payout(n, modifiers)
    if pay is None:
        return None, basis
    p_all = 1.0
    for p in probs:
        p_all *= p
    return p_all * pay - 1.0, basis + "; picks assumed independent"


def break_even_table():
    """Every Underdog entry type's per-pick break-even (equal p, modifiers 1.0)."""
    rows = [{"entry": f"standard {n}-pick", "payout": UD_STANDARD[n], "break_even": ud_standard_break_even(n)}
            for n in sorted(UD_STANDARD)]
    rows += [{"entry": f"flex {n}-pick", "payout": UD_FLEX[n], "break_even": ud_flex_break_even(n)}
             for n in sorted(UD_FLEX)]
    return {"source": UD_SOURCE, "read_at": UD_READ_AT, "version": UD_TABLE_VERSION, "modifier_rule": MODIFIER_RULE,
            "rows": rows, "prizepicks": PP_STATUS}


def modifier_break_even(n: int, modifier: float, others: float = 1.0) -> float:
    """Per-pick break-even for one pick with `modifier` when the other n-1 picks each have chance p* at modifier
    `others` (standard entry, MODIFIER_RULE). The data branch compared p * modifier with the 2-pick 53.45%; that is
    the same test only for n = 2 with a partner exactly at break-even."""
    base = UD_STANDARD[n]
    p_other = (base * others ** (n - 1)) ** (-1.0 / n) if n > 1 else 1.0
    return 1.0 / (base * modifier * (others * p_other) ** (n - 1))


def straight_ev(p_win: float, decimal_odds: float, p_loss: float | None = None) -> float:
    """Net expectation per unit staked, before fees. p_loss None = no push possible (p_loss = 1 - p_win)."""
    if p_loss is None:
        return p_win * decimal_odds - 1.0
    return p_win * (decimal_odds - 1.0) - p_loss


def kalshi_taker_fee(price: float, contracts: float = 1.0, coefficient: float = 0.07) -> float:
    """Kalshi 'quadratic' trading fee for `contracts` at `price` dollars: coefficient * C * P * (1 - P), rounded UP to
    the cent (series fee_type 'quadratic', fee_multiplier 1 on KXNBAPTS / KXNFLRECYDS, read 2026-10-23). The 0.07
    coefficient is Kalshi's published general taker rate as quoted by third parties; the fee schedule PDF answered 429
    to us - UNVERIFIED. Fees never change a probability; they widen the price band in which no trade is profitable."""
    import math
    raw = coefficient * contracts * price * (1.0 - price)
    return math.ceil(raw * 100 - 1e-9) / 100.0
