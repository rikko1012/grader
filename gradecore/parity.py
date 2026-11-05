"""Competitor spec 4.16 board parity fixes: the per-row numbers the board shows (display only, never a grade or a stake).

Every formula here reuses the section-3 math (gradecore.payouts, gradecore.fair, gradecore.slip); nothing is re-derived:
  - break-even: the VERIFIED per-leg break-even of the book's default 2-pick entry (payouts.entry_break_even on
    pickem_payouts.json). Goblin / demon / modified lines and any book without a verified table -> None (shown as "-").
  - EV: fair.leg_ev(p, be) = p / be - 1 = p x per-leg payout - 1, with p = fair.leg_chance(printed chance) (the board's own
    chance; "model only" until a market blend weight is fitted).
  - EV band (spec 4.16.7): Skip < 0 <= Marginal < 3% <= Strong < 6% <= Premium.
  - hit-rate sort key (spec 3 'Hit-rate sorting rule'): fair.hit_rate_edge(hits, n, be) = hit rate - break-even.
  - display sizing: gradecore.slip.stake on the leg priced at its break-even odds (decimal 1 / be); 1/4 Kelly, 1/2 Kelly or
    flat 1U. A member DISPLAY preference only: it never touches the grader's units.
"""
from . import fair, payouts, slip

ENTRY = {"prizepicks": ("power", 2, "PrizePicks Power 2-pick"), "underdog": ("standard", 2, "Underdog standard 2-pick")}
BANDS = ((0.06, "premium", "Premium"), (0.03, "strong", "Strong"), (0.0, "marginal", "Marginal"))
SKIP = ("skip", "Skip")
SIZING = {"quarter": ("kelly", 0.25), "half": ("kelly", 0.5), "flat": ("flat", None)}


def break_even(platform):
    """{be, american, decimal, entry, source, read_at, version} for the book's default 2-pick entry, or None (unverified)."""
    e = ENTRY.get(platform)
    if e is None:
        return None
    cfg = payouts.pickem_config()
    be = payouts.entry_break_even(platform, e[0], e[1], cfg)
    if be is None:
        return None
    b = cfg.get(platform) or {}
    return {"be": be, "american": payouts.american_of(be), "decimal": 1.0 / be, "entry": e[2],
            "source": b.get("source"), "read_at": b.get("read_at"), "version": b.get("version")}


def table():
    """Every book the board can rate, for the column tooltip: {platform: break_even(platform)} (verified books only)."""
    return {p: v for p in ENTRY if (v := break_even(p)) is not None}


def leg_be(platform, line_type, multiplier):
    """The break-even a board row is rated against, or None. Only a standard line at a plain 1.0x pick has a published
    per-pick payout here (Underdog modifier combinations are not published; goblin / demon lines pay differently)."""
    if line_type != "standard":
        return None
    if multiplier is not None and abs(float(multiplier) - 1.0) > 1e-9:
        return None
    return break_even(platform)


def band(ev):
    """(key, label) of the spec's EV band, or None without an EV."""
    if ev is None:
        return None
    for lo, key, label in BANDS:
        if ev >= lo:
            return key, label
    return SKIP


def leg_kelly(p, be):
    """Full-Kelly fraction of one leg bought at its break-even odds (decimal 1 / be): slip.kelly_fraction on [(p, 1/be), (1-p, 0)]."""
    return slip.kelly_fraction([(p, 1.0 / be), (1.0 - p, 0.0)])


def sizes(p, be):
    """Display units by sizing preference (1 unit = slip.UNIT_SHARE of an entered bankroll): {quarter, half, flat}."""
    out = {}
    for k, (mode, frac) in SIZING.items():
        r = slip.stake([(p, 1.0 / be), (1.0 - p, 0.0)], mode=mode, **({"fraction": frac} if frac else {}))
        out[k] = round(r["units"], 2)
    return out


def row(p_side, side, platform, line_type, multiplier, l10=None):
    """The parity fields for one board row (a record's scout / offer / history), or None when there is nothing to show.
    -> {"be", "be_american", "be_decimal", "entry", "ev", "band", "band_label", "sizes", "hit": {...} | None}."""
    if not side or p_side is None:
        return None
    b = leg_be(platform, line_type, multiplier)
    if b is None:
        return None
    p = fair.leg_chance(float(p_side))["p"]
    ev = fair.leg_ev(p, b["be"])
    bd = band(ev)
    hit = None
    if l10 and l10.get("n"):
        h = fair.hit_rate_edge(l10.get(side) or 0, l10["n"], b["be"])
        if h is not None:
            hit = {"hits": h["hits"], "n": h["n"], "key": round(h["key"], 6), "below": h["below_break_even"]}
    return {"be": round(b["be"], 6), "be_american": round(b["american"]), "be_decimal": round(b["decimal"], 2), "entry": b["entry"],
            "ev": round(ev, 6), "band": bd[0], "band_label": bd[1], "sizes": sizes(p, b["be"]), "hit": hit}


def of_record(rec):
    """row() read from a dr-1 display record (CS2, LoL and NBA/NFL adapters all share this shape)."""
    s, o = rec.get("scout") or {}, rec.get("offer") or {}
    side = s.get("side")
    mult = (o.get(side) or {}).get("payout") if side in ("over", "under") else None
    return row(s.get("p_side"), side, o.get("platform"), (rec.get("market") or {}).get("line_type"), mult,
               (rec.get("history") or {}).get("l10"))
