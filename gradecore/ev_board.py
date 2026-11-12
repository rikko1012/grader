"""Pick'em +EV board legs (SCOUT_PICKEM_EV, competitor spec 4.1). Arithmetic only: the server module workspace_ev.py
collects the quotes and the saved chances; everything priced here goes through gradecore.fair and gradecore.payouts.

Per pick'em leg (one app, one line, one side):
  break-even   the selected entry's per-leg break-even from the VERIFIED tables (gradecore/pickem_payouts.json via
               payouts.pickem_config()). An Underdog pick with a payout modifier other than 1.0 on a standard entry uses
               payouts.break_even(multiplier=...) (MODIFIER_RULE); a modifier on a flex entry, a PrizePicks pick with a
               modifier, and any non-standard line are unavailable (None), never guessed.
  market fair  fair.fair_chance at the app's EXACT line (stale / one-sided / wide quotes dropped first; a book on another
               line, or one that settles the prop differently from the app, is dropped - gradecore.book_rules), over at
               least MIN_BOOKS_DEFAULT independent pricing families (owner 2026-11-12).
  model        the chance the board prints today (calibration layers are off; no blend weight is fitted, so the two are
               shown side by side and never mixed).
  EV           fair.leg_ev(p, be) = p / be - 1 on the chance given no push (a push is a refunded / removed pick).
Rows with quotes but no fair chance are HIDDEN with a reason (hidden_reason), so stale quotes never produce an EV.
"""
from . import fair, payouts

WEIGHT_CHOICES = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0)
MIN_LIQUIDITY_CHOICES = (0.0, 100.0, 500.0, 1000.0, 5000.0)
MAX_WIDTH_CHOICES = (0.05, 0.08, fair.MAX_WIDTH)
MIN_BOOKS_MAX = 4
MIN_BOOKS_DEFAULT = 2          # owner 2026-11-12: "use 2 independent books by default"; counts pricing families, not sites
ONE_BOOK_LABEL = "1 book"      # a row that rests on one family, shown only when the member lowers the filter to 1

HIDDEN_WORDS = {
    "stale": "only old prices",
    "one-sided": "only one side priced",
    "wide": "book margin too wide",
    "spread": "exchange spread too wide",
    "liquidity": "exchange liquidity below your minimum",
    "line": "no book on this exact line",
    "rules": "books that settle a sit-out differently from the app",
    "books": "fewer independent books than the minimum",
    "required": "a required book is missing",
    "weight": "every book set to weight 0",
    "other": "prices could not be read",
}


def pickem_break_even(app, entry, n, multiplier=None, line_type="standard", config=None):
    """Per-leg break-even of one pick on the selected entry, or None (unavailable)."""
    if line_type != "standard":
        return None
    cfg = config if config is not None else payouts.pickem_config()
    m = None if multiplier is None or abs(float(multiplier) - 1.0) < 1e-9 else float(multiplier)
    if m is None:
        return payouts.entry_break_even(app, entry, n, cfg)
    if app != "underdog":
        return None
    return payouts.break_even(app, entry, n, multiplier=m, config=cfg)


def no_push(p_side, p_other):
    """A printed chance (push included) as the chance given no push; None when either side is missing."""
    if not isinstance(p_side, (int, float)) or not isinstance(p_other, (int, float)) or p_side + p_other <= 0:
        return None
    return float(p_side) / (float(p_side) + float(p_other))


def hidden_reason(res):
    """Why a leg that had quotes got no fair chance -> one HIDDEN_WORDS key."""
    why = res.get("reason") or ""
    if why.startswith("required"):
        return "required"
    if "weight 0" in why:
        return "weight"
    reasons = [d["reason"] for d in res.get("dropped") or []]
    if res.get("sources"):
        return "books"
    if reasons and all(r == "stale" for r in reasons):
        return "stale"
    for r in reasons:
        if r == "one-sided":
            return "one-sided"
    for r in reasons:
        if r == "wide":
            return "wide"
        if r.startswith("exchange spread"):
            return "spread"
        if "liquidity" in r:
            return "liquidity"
        if r.startswith("different line"):
            return "line"
        if r == "settlement rules differ":
            return "rules"
        if r == "stale":
            return "stale"
    return "other" if reasons else "books"


def price_leg(quotes, side, line, now, *, be, p_model, p_model_other, reader=None, **fair_opts):
    """One leg. -> {"p_market", "p_model", "be", "american", "ev", "ev_model", "fair": fair.fair_chance result,
    "hidden": HIDDEN_WORDS key or None, "basis": "market" | "model only"}."""
    p_model_np = no_push(p_model, p_model_other)
    out = {"p_model": p_model, "be": be, "american": None if be is None else payouts.american_of(be),
           "ev_model": fair.leg_ev(p_model_np, be) if p_model_np is not None and be is not None else None,
           "p_market": None, "ev": None, "fair": None, "hidden": None, "basis": "model only"}
    if not quotes:
        return out
    res = fair.fair_chance(quotes, side, line, now, reader=reader, **fair_opts)
    out["fair"] = res
    if res["p"] is None:
        out["hidden"] = hidden_reason(res)
        out["basis"] = None
        return out
    out.update(p_market=res["p"], basis="market", ev=fair.leg_ev(res["p"], be) if be is not None else None)
    return out


def hidden_banner(counts):
    """{reason key: plays} -> plain words, largest first: '12 plays hidden: 9 with only old prices, 3 with ...'."""
    total = sum(counts.values())
    if not total:
        return None
    parts = [f"{n} with {HIDDEN_WORDS.get(k, HIDDEN_WORDS['other'])}" for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n]
    return f"{total} {'play' if total == 1 else 'plays'} hidden: " + ", ".join(parts) + "."
