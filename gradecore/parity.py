"""Competitor spec 4.16 board parity fixes: the per-row numbers the board shows (display only, never a grade or a stake).

Every formula here reuses the section-3 math (gradecore.payouts, gradecore.fair, gradecore.slip); nothing is re-derived:
  - break-even: the VERIFIED per-leg break-even of the book's default 2-pick entry (payouts.entry_break_even on
    pickem_payouts.json). Goblin / demon / modified lines and any book without a verified table -> None (shown as "-").
  - EV: fair.leg_ev(p, be) = p / be - 1 = p x per-leg payout - 1, with p = fair.leg_chance(no-push printed chance) (the
    board's own chance; "model only" until a market blend weight is fitted).
  - EV band (spec 4.16.7): Skip < 0 <= Marginal < 3% <= Strong < 6% <= Premium.
  - hit-rate sort key (spec 3 'Hit-rate sorting rule'): fair.hit_rate_edge(hits, n, be) = hit rate - break-even.
  - display sizing: gradecore.slip.stake on the ENTRY the break-even belongs to (the book's default 2-pick, both legs at this
    chance, its real payout tiers), capped at policy.MAX_UNITS, shown as a share of bankroll (never "U", so it can never be
    read as a second grader stake). 1/4 Kelly, 1/2 Kelly or flat 1%. A member DISPLAY preference only; owner decision
    pending before SCOUT_PARITY_PREFS is switched on.

Rating gates (review 2026-11-05): EV, band and sizes are given only when the caller says the row is RATED, i.e. the live
slip check would rate it (CS2 only, letter not held, half-point line, chance band passing the settled-results check:
workspace_rules.leg_edge_ok). Sizes also need a grader stake above 0U. The break-even and the hit-rate key need neither.
The chance is the no-push chance p / (1 - push), the same definition as gradecore.ev_board and the slip header.
"""
from . import fair, payouts, policy, slip

ENTRY = {"prizepicks": ("power", 2, "PrizePicks Power 2-pick"), "underdog": ("standard", 2, "Underdog standard 2-pick")}
BANDS = ((0.06, "premium", "Premium"), (0.03, "strong", "Strong"), (0.0, "marginal", "Marginal"))
SKIP = ("skip", "Skip")
SIZING = {"quarter": ("kelly", 0.25), "half": ("kelly", 0.5), "flat": ("flat", None)}
SIZE_CAP_UNITS = policy.MAX_UNITS


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


def no_push(p_side, p_push=None):
    """The chance given no push (a push refunds the pick): p / (1 - push). A half-point line has push 0 and is unchanged."""
    p = float(p_side)
    if isinstance(p_push, (int, float)) and not isinstance(p_push, bool) and 0 < p_push < 1:
        return p / (1.0 - float(p_push))
    return p


def entry_returns(p, platform):
    """Gross-return outcomes of the book's default 2-pick entry with both legs at chance p (its verified tiers), or None."""
    e = ENTRY.get(platform)
    t = payouts.tiers(platform, e[0], e[1], payouts.pickem_config()) if e else None
    if t is None:
        return None
    return slip.returns_of(slip.slip_ev([p] * e[1], t)["hit_dist"], t)


def sizes(p, platform):
    """Display size by preference as a % of bankroll: {quarter, half, flat}, from the entry's Kelly (not one leg at 1 / be),
    capped at SIZE_CAP_UNITS. None without a verified entry."""
    rets = entry_returns(p, platform)
    if rets is None:
        return None
    out = {}
    for k, (mode, frac) in SIZING.items():
        r = slip.stake(rets, mode=mode, cap_units=SIZE_CAP_UNITS, **({"fraction": frac} if frac else {}))
        out[k] = round(r["units"] * slip.UNIT_SHARE * 100, 2)
    return out


def row(p_side, side, platform, line_type, multiplier, l10=None, *, p_push=None, rated=False, sized=False):
    """The parity fields for one board row, or None when there is nothing to show.
    -> {"be", "be_american", "be_decimal", "entry", "hit": {...} | None}, plus {"ev", "band", "band_label"} only when
    `rated`, plus {"sizes_pct"} only when `rated` and `sized`."""
    if not side or p_side is None:
        return None
    b = leg_be(platform, line_type, multiplier)
    if b is None:
        return None
    out = {"be": round(b["be"], 6), "be_american": round(b["american"]), "be_decimal": round(b["decimal"], 2), "entry": b["entry"],
           "hit": None}
    if l10 and l10.get("n"):
        h = fair.hit_rate_edge(l10.get(side) or 0, l10["n"], b["be"])
        if h is not None:
            out["hit"] = {"hits": h["hits"], "n": h["n"], "key": round(h["key"], 6), "below": h["below_break_even"]}
    if rated:
        p = fair.leg_chance(no_push(p_side, p_push))["p"]
        ev = fair.leg_ev(p, b["be"])
        bd = band(ev)
        out.update(ev=round(ev, 6), band=bd[0], band_label=bd[1])
        if sized:
            sz = sizes(p, platform)
            if sz is not None:
                out["sizes_pct"] = sz
    return out


def held(rec):
    """A row whose letter is held (letter / league containment) or that carries a sports hold: never rated, never sized."""
    s = rec.get("scout") or {}
    return bool(s.get("letter_held") or s.get("hold"))


def of_record(rec, rated=False):
    """row() read from a dr-1 display record (CS2, LoL and NBA/NFL adapters all share this shape). `rated` = the caller's
    settled-results check passed for this row; a held row, a whole-number line or a sport other than CS2 is never rated."""
    s, o = rec.get("scout") or {}, rec.get("offer") or {}
    side = s.get("side")
    mult = (o.get(side) or {}).get("payout") if side in ("over", "under") else None
    try:
        whole = float(o.get("line")).is_integer()
    except (TypeError, ValueError):
        whole = True
    ok = bool(rated) and rec.get("sport") == "cs2" and not held(rec) and not whole
    units = s.get("units")
    sized = ok and isinstance(units, (int, float)) and not isinstance(units, bool) and units > 0
    return row(s.get("p_side"), side, o.get("platform"), (rec.get("market") or {}).get("line_type"), mult,
               (rec.get("history") or {}).get("l10"), p_push=s.get("p_push"), rated=ok, sized=sized)
