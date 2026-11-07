"""Fair chance at a pick'em line from many books (competitor spec section 3, "Fair chance at the same line").

Pure arithmetic; no live caller until SCOUT_PICKEM_EV (spec 4.1) is switched on. Imports no sport code: the line-shift
shape for a book on a different line is handed in by the caller as `reader` (sports passes sports.fair.at_line, CS2 /
LoL pass their own distribution through reader_from_distribution), so the measured shapes are reused, never copied.

Per quote, in this order (every dropped quote keeps its reason, so a banner can say how many and why):
  1. freshness  - older than its kind's limit (FRESHNESS_S) or stamped in the future: dropped, never priced.
  2. one-sided  - a sportsbook quote without both sides cannot be devigged: dropped.
  3. exchange   - bid/ask midpoint, only when ask - bid <= MAX_SPREAD and liquidity >= the minimum.
  4. width      - a sportsbook market whose overround is above max_width: dropped.
  5. devig      - worst-case by default: the lowest chance multiplicative, power and Shin give the chosen side.
  6. line       - a quote on another line is read at the pick'em line through `reader`; no reader = dropped.
Then copy-books (same cluster) count once, Required / Min books are checked, and the weighted average is the fair chance.

Chances here are of the chosen side GIVEN NO PUSH (a refunded push is not a loss); `push` is reported beside it.
"""
import math
from dataclasses import dataclass

from . import blend, devig, gates, market_tools

KINDS = ("sportsbook", "exchange")
FRESHNESS_S = {"sportsbook": float(market_tools.EV_STALE_S), "exchange": 5 * 60.0}
MAX_SPREAD = 0.05                      # exchange: ask - bid in probability units (5 cents on a $1 contract)
MIN_LIQUIDITY = 0.0                    # exchange: dollars resting at the quoted price; the member filter raises it
MAX_WIDTH = gates.WIDE_MARKET_WIDTH    # sportsbook overround above this is dropped
METHODS = ("worst", "multiplicative", "power", "shin")
WORST_CASE_METHODS = ("multiplicative", "power", "shin")     # spec: worst case = min of these three

# Weight presets: book -> weight. A book missing from a preset weighs DEFAULT_BOOK_WEIGHT. Members can save their own.
DEFAULT_BOOK_WEIGHT = 1.0
PRESETS = {
    "equal": {},
    "sharp": {"pinnacle": 3.0, "circa": 2.0, "bookmaker": 2.0, "novig": 1.5, "prophetx": 1.5,
              "kalshi": 1.5, "polymarket": 1.5},
}


@dataclass(frozen=True)
class Quote:
    """One book's price on one market. Sportsbooks: decimal over / under. Exchanges: bid / ask of the Over (YES)
    contract in probability units, plus liquidity. `cluster` = the copy group (books that copy one price count once);
    None = the book is its own cluster."""
    book: str
    line: float
    observed_at: float
    kind: str = "sportsbook"
    over: float | None = None
    under: float | None = None
    bid: float | None = None
    ask: float | None = None
    liquidity: float | None = None
    cluster: str | None = None

    def group(self):
        return (self.cluster or self.book).lower()


def _finite(x):
    return x is not None and not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def exchange_mid(bid, ask, liquidity=None, min_liquidity=MIN_LIQUIDITY, max_spread=MAX_SPREAD):
    """-> (midpoint chance of YES, None) or (None, reason). Used only when the spread <= max_spread and liquidity >= the
    minimum (unknown liquidity passes only when the minimum is 0)."""
    if not (_finite(bid) and _finite(ask)) or not 0 <= bid <= ask <= 1:
        return None, "exchange quote has no valid bid and ask"
    if ask - bid > max_spread + 1e-12:
        return None, f"exchange spread {ask - bid:.2f} wider than {max_spread:.2f}"
    if min_liquidity > 0 and not (_finite(liquidity) and liquidity >= min_liquidity):
        return None, "exchange liquidity below the minimum"
    mid = (bid + ask) / 2.0
    if not 0 < mid < 1:
        return None, "exchange midpoint is 0 or 1"
    return mid, None


def devig_side(over, under, side="over", method="worst"):
    """-> (chance of `side` given no push, method used) or None. 'worst' = the lowest of multiplicative, power, Shin."""
    i = 0 if side == "over" else 1
    prices = [over, under]
    if method == "worst":
        best = None
        for name in WORST_CASE_METHODS:
            p = devig.METHODS[name](prices)
            if p is not None and (best is None or p[i] < best[0]):
                best = (p[i], name)
        return best
    if method not in devig.METHODS:
        raise ValueError(f"unknown devig method {method!r}")
    p = devig.METHODS[method](prices)
    return None if p is None else (p[i], method)


def _split(r):
    """A reader's answer -> (over, under, push)."""
    if isinstance(r, dict):
        return float(r["over"]), float(r["under"]), float(r.get("push", 0.0) or 0.0)
    if isinstance(r, (tuple, list)):
        o, u = float(r[0]), float(r[1])
        return o, u, float(r[2]) if len(r) > 2 else max(0.0, 1.0 - o - u)
    o = float(r)
    return o, 1.0 - o, 0.0


def reader_from_distribution(p_at, lo=1e-3, hi=None, iters=200):
    """Turn a sport's own distribution into a line-shift reader without copying it.

    p_at(mean, line) -> chance of Over (given no push) or (over, under, push). The reader solves the mean whose
    P(Over sharp_line | no push) equals the market's chance (bisection: the chance rises with the mean) and reads the
    same distribution at the pick'em line. -> reader(sharp_line, p_over_sharp, line) -> {over, under, push, mean}."""
    def no_push(mean, line):
        o, u, _ = _split(p_at(mean, line))
        return o / (o + u) if o + u > 0 else 0.5

    def reader(sharp_line, p_over_sharp, line):
        target = min(max(float(p_over_sharp), 1e-4), 1 - 1e-4)
        a, b = lo, hi if hi is not None else max(5.0, abs(float(sharp_line)) * 4 + 10)
        for _ in range(iters):
            mid = (a + b) / 2
            if no_push(mid, float(sharp_line)) < target:
                a = mid
            else:
                b = mid
            if b - a < 1e-7:
                break
        mean = (a + b) / 2
        o, u, p = _split(p_at(mean, float(line)))
        return {"over": o, "under": u, "push": p, "mean": mean}
    return reader


def at_line(p_over_quote, quote_line, line, reader=None):
    """The chance of Over / Under / push at the pick'em `line` from a no-vig chance of Over at `quote_line`.
    Same half-point line -> as it is. Anything else needs the sport's reader; None when there is none."""
    whole = abs(float(line) - round(float(line))) < 1e-9
    if abs(float(line) - float(quote_line)) < 1e-9 and not whole:
        return {"over": p_over_quote, "under": 1.0 - p_over_quote, "push": 0.0, "shifted": False}
    if abs(float(line) - float(quote_line)) < 1e-9 and reader is None:
        # A whole line on both sides: the devigged chance is already "given no push"; the push size is unknown.
        return {"over": p_over_quote, "under": 1.0 - p_over_quote, "push": None, "shifted": False}
    if reader is None:
        return None
    got = reader(float(quote_line), float(p_over_quote), float(line))
    o, u, p = _split(got)
    return {"over": o, "under": u, "push": p, "shifted": abs(float(line) - float(quote_line)) > 1e-9,
            "far": bool(got.get("far")) if isinstance(got, dict) else False}


def _no_push(r, side):
    o, u = r["over"], r["under"]
    if o + u <= 0:
        return None
    return (o if side == "over" else u) / (o + u)


def fair_chance(quotes, side, line, now, *, weights=None, preset="equal", method="worst", min_books=1,
                required_books=(), min_liquidity=MIN_LIQUIDITY, max_spread=MAX_SPREAD, max_width=MAX_WIDTH,
                freshness=None, reader=None):
    """The weighted fair chance of `side` ('over' / 'under') at the pick'em `line`.

    -> {"p": chance given no push or None, "push", "books": clusters used, "sources": [...], "dropped": [...],
        "reason": why there is no p (None when there is one)}. Stale quotes are dropped before anything is priced."""
    if side not in ("over", "under"):
        raise ValueError("side must be over or under")
    if method not in METHODS:
        raise ValueError(f"unknown devig method {method!r}")
    limits = dict(FRESHNESS_S, **(freshness or {}))
    w_table = dict(PRESETS.get(preset) or {}, **{k.lower(): float(v) for k, v in (weights or {}).items()})
    sources, dropped = [], []

    def drop(q, why):
        dropped.append({"book": q.book, "line": q.line, "kind": q.kind, "reason": why,
                        "age_s": None if not _finite(q.observed_at) else now - q.observed_at})

    for q in quotes:
        if q.kind not in KINDS:
            drop(q, f"unknown quote kind {q.kind!r}")
            continue
        if not _finite(q.observed_at) or q.observed_at > now:
            drop(q, "no valid quote time")
            continue
        age = now - q.observed_at
        if age > limits[q.kind]:
            drop(q, "stale")
            continue
        if q.kind == "exchange":
            mid, why = exchange_mid(q.bid, q.ask, q.liquidity, min_liquidity, max_spread)
            if mid is None:
                drop(q, why)
                continue
            p_over_q, used, width = mid, "exchange midpoint", q.ask - q.bid
        else:
            if not (_finite(q.over) and _finite(q.under)):
                drop(q, "one-sided")
                continue
            width = devig.width([q.over, q.under])
            if width is None:
                drop(q, "unusable prices")
                continue
            if width > max_width + 1e-12:
                drop(q, "wide")
                continue
            d = devig_side(q.over, q.under, "over", method if method != "worst" else "multiplicative")
            if d is None:
                drop(q, "unusable prices")
                continue
            p_over_q, used = d
        r = at_line(p_over_q, q.line, line, reader)
        if r is None:
            drop(q, "different line and no line-shift shape")
            continue
        if r.get("far"):
            # beyond line_shift.GAP_WARN the shape is read far from the price: usually a different market, never an edge
            drop(q, "different line too far")
            continue
        if q.kind == "sportsbook" and method == "worst":
            # Worst case for the CHOSEN side at the pick'em line: read every method through the shift, keep the lowest.
            best = None
            for name in WORST_CASE_METHODS:
                d = devig_side(q.over, q.under, "over", name)
                if d is None:
                    continue
                rr = at_line(d[0], q.line, line, reader)
                pv = None if rr is None else _no_push(rr, side)
                if pv is not None and (best is None or pv < best[0]):
                    best = (pv, name, rr)
            if best is None:
                drop(q, "unusable prices")
                continue
            p, used, r = best
        else:
            p = _no_push(r, side)
            if p is None:
                drop(q, "unusable prices")
                continue
        sources.append({"book": q.book, "group": q.group(), "kind": q.kind, "line": q.line, "age_s": age,
                        "width": width, "liquidity": q.liquidity, "method": used, "p": p, "push": r.get("push"),
                        "shifted": bool(r.get("shifted")),
                        "weight": w_table.get(q.book.lower(), DEFAULT_BOOK_WEIGHT)})

    clusters = {}
    for s in sources:
        clusters.setdefault(s["group"], []).append(s)
    out = {"p": None, "push": None, "books": sorted(clusters), "sources": sources, "dropped": dropped, "reason": None}
    missing = [b for b in required_books if b.lower() not in {s["book"].lower() for s in sources}]
    if missing:
        out["reason"] = "required book missing: " + ", ".join(missing)
        return out
    if len(clusters) < max(1, int(min_books)):
        out["reason"] = f"{len(clusters)} usable book(s), {int(min_books)} needed"
        return out
    num = den = 0.0
    pushes = []
    for members in clusters.values():
        # A copy group counts once: its members' average chance, at the heaviest member's weight.
        w = max(m["weight"] for m in members)
        if w <= 0:
            continue
        num += w * sum(m["p"] for m in members) / len(members)
        den += w
        pushes += [m["push"] for m in members if m["push"] is not None]
    if den <= 0:
        out["reason"] = "every usable book has weight 0"
        return out
    out["p"] = num / den
    out["push"] = sum(pushes) / len(pushes) if pushes else None
    return out


# ----------------------------------------------------------------------------------------------- the leg
def leg_chance(p_printed, p_market=None, w=blend.DEFAULT_WEIGHT, width=None):
    """The chance a leg is priced on. p_printed = the chance the board prints today (model chance); the spec's p_cal.
    logit blend with the market only through blend.blend (w = the model's share; DEFAULT_WEIGHT 1 = the printed chance
    unchanged until a weight is fitted). No market = "model only"."""
    if p_market is None:
        return {"p": float(p_printed), "basis": "model only", "p_model": float(p_printed), "p_market": None}
    return {"p": blend.blend(p_printed, p_market, w, width), "basis": "model + market", "p_model": float(p_printed),
            "p_market": float(p_market)}


def leg_ev(p, be):
    """Pick'em leg EV against the entry's per-leg break-even: p / be - 1 (= p x per-leg payout - 1)."""
    if p is None or be is None:
        return None
    if not 0 < be < 1:
        raise ValueError("break-even must be between 0 and 1")
    return float(p) / float(be) - 1.0


def discrepancy_ev(offers):
    """Spec 'Discrepancy EV': each app's chance at ITS line x that app's multiplier, ranked by EV, never by line gap.

    offers: [{"app", "line", "side", "p" (chance at that app's line, given no push), and "multiplier" (per-pick payout
    multiple) or "be" (per-leg break-even)}]. EV = p x multiplier - 1, or p / be - 1. Unknown payout -> ev None, last."""
    rows = []
    for o in offers:
        ev = None
        if o.get("p") is not None:
            if o.get("multiplier") is not None:
                ev = float(o["p"]) * float(o["multiplier"]) - 1.0
            elif o.get("be") is not None:
                ev = leg_ev(o["p"], o["be"])
        rows.append(dict(o, ev=ev))
    return sorted(rows, key=lambda r: (r["ev"] is None, -(r["ev"] or 0.0)))


def hit_rate_edge(hits, n, be):
    """Spec 'Hit-rate sorting rule': sort by hit rate - break-even; a leg whose break-even is above its hit rate is
    flagged (Outlier's 'Under 0.5 at -5000, 100%' junk). -> {"hit_rate", "n", "key", "below_break_even"} or None."""
    if not n or be is None:
        return None
    rate = hits / n
    return {"hit_rate": rate, "n": n, "hits": hits, "key": rate - float(be), "below_break_even": float(be) > rate}
