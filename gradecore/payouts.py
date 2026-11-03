"""THE break-even table: pick'em payouts and the arithmetic derived from them. Copied from sports/payouts.py (same
numbers, same maths); the tables now sit in CONFIG so a book or entry type is data, not code.

UNDERDOG - published base payouts, read 2026-10-23 from UD_SOURCE; payouts include the returned entry fee. How several
  modifiers combine is NOT stated: payout = base x product(modifiers) is an ASSUMPTION (MODIFIER_RULE).
PRIZEPICKS - published standard reference rates live in payout_reference.py. The actual lineup payout can differ.
  CONFIG entries remain None until a verified applicable payout is supplied (load_config); a reference alone does not
  authorize an EV, stake or settlement calculation for an individual contest.

Break-even per pick (equal chance p on every pick, all modifiers 1.0):
  standard n-pick:  p^n * B_n = 1           ->  p* = B_n ** (-1/n)
  flex n-pick:      sum_k C(n,k) p^(n-k) (1-p)^k * F_n[k] = 1   (k = losses paid), solved by bisection

Goblin, demon and promo lines pay an unpublished multiplier: break_even() is None unless the multiplier is supplied.
"""
import copy
import json
import math
from functools import lru_cache
from math import comb
from pathlib import Path

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
PP_STATUS = "Published base rates available; actual applicable lineup payout must be verified"

CONFIG = {
    "underdog": {"default_entry": "standard", "entries": {"standard": UD_STANDARD, "flex": UD_FLEX},
                 "source": UD_SOURCE, "read_at": UD_READ_AT, "version": UD_TABLE_VERSION},
    "prizepicks": {"default_entry": "power", "entries": {"power": None, "flex": None}, "status": PP_STATUS},
}
STANDARD_VARIANT = "standard"


def _int_keys(t):
    if isinstance(t, dict):
        return {int(k): _int_keys(v) for k, v in t.items()}
    return t


def with_tables(extra, base=None):
    """base (default CONFIG) with `extra` laid over it, book by book and entry by entry. JSON's string keys become ints."""
    out = copy.deepcopy(base or CONFIG)
    for book, c in (extra or {}).items():
        b = out.setdefault(book, {"default_entry": None, "entries": {}})
        b.update({k: v for k, v in c.items() if k != "entries"})
        for entry, table in (c.get("entries") or {}).items():
            b["entries"][entry] = _int_keys(table)
    return out


def load_config(path, base=None):
    """A payout file laid over CONFIG: how a verified PrizePicks table arrives, e.g.
    {"prizepicks": {"entries": {"power": {"2": 3.0}}, "version": "pp-..."}}."""
    return with_tables(json.loads(Path(path).read_text(encoding="utf-8")), base)


def flex_return(row, n, p):
    """Expected payout multiple of an n-pick flex entry with chance p on every pick (modifiers 1.0)."""
    return sum(comb(n, k) * p ** (n - k) * (1 - p) ** k * mult for k, mult in row.items())


@lru_cache(maxsize=256)
def _flex_be(items, n):
    row = dict(items)
    lo, hi = 0.3, 0.9
    for _ in range(200):
        mid = (lo + hi) / 2
        if flex_return(row, n, mid) < 1.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def flex_break_even(row, n):
    return _flex_be(tuple(sorted(row.items())), n)


def modifier_break_even(n, modifier, others=1.0, base=None):
    """Per-pick break-even for one pick with `modifier` when the other n-1 picks each have chance p* at modifier `others`
    (standard entry, MODIFIER_RULE)."""
    base = UD_STANDARD[n] if base is None else base
    p_other = (base * others ** (n - 1)) ** (-1.0 / n) if n > 1 else 1.0
    return 1.0 / (base * modifier * (others * p_other) ** (n - 1))


def _row(book, entry, n, config):
    b = (config or CONFIG).get(str(book or "").lower())
    if b is None:
        return None
    table = (b.get("entries") or {}).get(entry or b.get("default_entry"))
    return None if table is None else table.get(n)


def break_even(book, entry=None, n=2, variant=STANDARD_VARIANT, multiplier=None, config=None):
    """The per-pick break-even, or None when it is not known: no verified table, an entry size the book does not offer, or
    a goblin / demon / promo line with no multiplier. A supplied multiplier applies to fixed entries only: the flex
    tables are published for modifier 1.0, so a multiplier on a flex entry is None."""
    row = _row(book, entry, n, config)
    if row is None:
        return None
    flex = isinstance(row, dict)
    if multiplier is not None:
        return None if flex else modifier_break_even(n, float(multiplier), base=float(row))
    if variant != STANDARD_VARIANT:
        return None
    return flex_break_even(row, n) if flex else float(row) ** (-1.0 / n)


def ud_standard_break_even(n):
    return UD_STANDARD[n] ** (-1.0 / n)


def ud_flex_return(n, p):
    return flex_return(UD_FLEX[n], n, p)


def ud_flex_break_even(n):
    return flex_break_even(UD_FLEX[n], n)


def ud_entry_payout(n, modifiers, entry="standard"):
    """Max payout multiple of an entry (all picks correct) under MODIFIER_RULE -> (multiple, basis)."""
    m = UD_STANDARD[n] if entry == "standard" else UD_FLEX[n][0]
    for x in modifiers:
        if x is None:
            return None, "a pick's payout modifier is missing"
        m *= float(x)
    return m, MODIFIER_RULE


def ud_standard_ev(probs, modifiers):
    """Expected net return per unit of a standard entry with independent picks (an estimate: independence is an
    assumption) -> (ev, basis)."""
    pay, basis = ud_entry_payout(len(probs), modifiers)
    if pay is None:
        return None, basis
    return math.prod(probs) * pay - 1.0, basis + "; picks assumed independent"


def break_even_table(config=None):
    """Every entry type's per-pick break-even (equal p, modifiers 1.0), with None where the book's table is unverified."""
    cfg = config or CONFIG
    rows = []
    for book, b in sorted(cfg.items()):
        for entry, table in b["entries"].items():
            if table is None:
                rows.append({"book": book, "entry": entry, "payout": None, "break_even": None})
            for n in sorted(table or ()):
                rows.append({"book": book, "entry": f"{entry} {n}-pick", "payout": table[n],
                             "break_even": break_even(book, entry, n, config=cfg)})
    return {"modifier_rule": MODIFIER_RULE, "rows": rows,
            "books": {k: {x: b.get(x) for x in ("source", "read_at", "version", "status")} for k, b in sorted(cfg.items())}}


def straight_ev(p_win, decimal_odds, p_loss=None):
    """Net expectation per unit staked, before fees. p_loss None = no push possible (p_loss = 1 - p_win)."""
    if p_loss is None:
        return p_win * decimal_odds - 1.0
    return p_win * (decimal_odds - 1.0) - p_loss


def kalshi_taker_fee(price, contracts=1.0, coefficient=0.07):
    """Kalshi 'quadratic' trading fee: coefficient * C * P * (1 - P), rounded UP to the cent (sports/payouts.py: the 0.07
    is unverified). Fees never change a probability."""
    raw = coefficient * contracts * price * (1.0 - price)
    return math.ceil(raw * 100 - 1e-9) / 100.0
