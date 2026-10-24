"""Market candidates (REGISTER.md 10, frozen 2026-10-23 before any evaluation; forward only - no historical quotes):

  mc-1          market-only CONSENSUS of every eligible verified source
  mkt-<source>  market-only, one source alone (kalshi, polymarket, oddsapi:<book>, and the direct book feeds fanduel /
                betrivers - amendment A4) - each book evaluated on its own
  mi-1          market-informed: location solved from the same eligible quotes, shape = sm-1's calibrated PMF

Eligibility per quote (same canonical event, player, stat, full-game scope, ordinary main / ladder line):
  kalshi        sharp2.eligible_rung (two-sided, spread <= 0.05, traded or open interest, observed before the start,
                observed <= 15 min before the forecast)
  polymarket    two-sided YES quote, spread <= 0.05, liquidity > 0, observed before the start and <= 15 min old.
                Polymarket resolves a player who does not play as NO, so its YES midpoint is UNCONDITIONAL on playing:
                p_play_cond = mid / (1 - d), d = DNP_SHARE[league] (frozen: NBA measured on ds-1 train rows, NFL 0 with
                reason code polymarket_dnp_unmeasured_nfl)
  oddsapi:<bk>  both sides at the same line, decimal odds > 1; de-vig multiplicative per book on that exhaustive
                pair only; age from the book's own last_update when given (else observed age) <= 15 min, before start
  oddspapi: excluded until its format is verified (sports/books)
A two-way book price at a WHOLE line refunds a push, so every source's number is P(Over | no push) at its own line.
Line translation (market-only): the foundation's fair.at_line shape (dispersion.json) - the same one sharp-2 uses, so
market-only never borrows sm-1. Per source, the quote nearest the target line (ties: the lower line) is used.
Combination: equal-weight mean of log-odds across SOURCES (one vote per source; weights fixed in advance, nothing
estimated - there is no development partition of quotes yet). >= 2 sources = "consensus"; 1 = labelled single source.
Target leakage: the target offer's own platform is never a source for that target (PrizePicks / Underdog never are).
Unconditional output: p_over = p_cond x (1 - p_push(L)), p_push from the same shape at a whole target line; half
lines push 0.
"""
from __future__ import annotations

import math

import numpy as np

from .. import capabilities, fair, markets, sharp2
from ..contracts import parse_utc
from . import dists

VERSION_CONSENSUS = "mc-1"
VERSION_MI = "mi-1"
MAX_SPREAD = 0.05
MAX_AGE_S = 15 * 60
DNP_SHARE = {"nba": None, "nfl": 0.0}            # NBA value filled from the frozen market config (artifacts)
FAMILY = {"nba": "basketball", "nfl": "football"}


def _logit(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _inv(x):
    return 1 / (1 + math.exp(-x))


# the books' own public feeds (sports/books/bridge.py): judged by the SAME per-book rule as The Odds API's books, but
# only as single-source candidates mkt-<book> - REGISTER.md section 10 amendment A4; mc-1 / mi-1 keep the registered
# source set (in_consensus)
DIRECT_BOOKS = ("fanduel", "betrivers")


def source_of(platform):
    return platform if not platform.startswith("oddsapi:") else platform


def in_consensus(source):
    """The sources mc-1 and mi-1 combine, exactly as registered (section 10): Kalshi, Polymarket, each Odds API book."""
    return source in ("kalshi", "polymarket") or source.startswith("oddsapi:")


def consensus_sources(per_source):
    return {s: v for s, v in per_source.items() if in_consensus(s)}


def quote_prob(q, event, forecast_at, last_seen=None, dnp_share=0.0, last_source=None):
    """-> (p_over_given_no_push at q.line, reason) or (None, why) for one source quote. last_seen / last_source = the
    revision's newest sighting and newest SOURCE time (store columns): an unchanged quote re-confirmed by the source
    is as fresh as its latest source time, not its first one (LINE_CORRECTNESS.md R2)."""
    seen = max(x for x in (q.observed_at, last_seen) if x)
    if event is None or not event.start_utc:
        return None, "no canonical start"
    if seen >= event.start_utc:
        return None, "quote observed after the start"
    if seen > forecast_at:
        return None, "quote observed after forecast time"
    if q.platform == "kalshi":
        ok, why = sharp2.eligible_rung(q, event, forecast_at, last_seen)
        if not ok:
            return None, why
        s = q.side("over")
        return (s.bid + s.ask) / 2, None
    srcs = [x for x in (q.source_updated_at, last_source) if x and x <= forecast_at]
    age_from = max(srcs) if srcs else (q.source_updated_at or seen)
    if (parse_utc(forecast_at) - parse_utc(age_from)).total_seconds() > MAX_AGE_S:
        return None, "older than 15 min"
    if q.availability != "open":
        return None, f"quote {q.availability}"
    if q.platform == "polymarket":
        s = q.side("over")
        if s is None or s.bid is None or s.ask is None:
            return None, "one-sided"
        if s.ask - s.bid > MAX_SPREAD + 1e-9:
            return None, "spread > 0.05"
        if not (q.liquidity_dict().get("liquidity") or 0) > 0:
            return None, "no liquidity"
        mid = (s.bid + s.ask) / 2
        return min(0.99, mid / (1 - dnp_share)), None
    if q.platform.startswith("oddsapi:") or q.platform in DIRECT_BOOKS:
        o, u = q.side("over"), q.side("under")
        if not (o and u and o.offered and u.offered and o.price and u.price and o.price > 1 and u.price > 1):
            return None, "not a two-sided priced pair"
        io, iu = 1 / o.price, 1 / u.price
        return io / (io + iu), None
    return None, f"source {q.platform} not in mc-1"


def _shape(league, stat):
    fam = FAMILY[league]
    spec = markets.market(fam, stat)
    return fam, spec["shape"]


def at_target(league, stat, src_line, p_cond, target_line):
    """Move a P(Over | no push) at src_line to the target line with the dispersion.json shape. -> (cond, push)."""
    fam, shape = _shape(league, stat)
    f = fair.at_line(fam, stat, shape, src_line, p_cond, target_line)
    tot = f["over"] + f["under"]
    return (f["over"] / tot if tot > 0 else 0.5), f["push"] / (f["over"] + f["under"] + f["push"])


def eligible_quotes(target, quotes, events, forecast_at, dnp_share):
    """quotes: [(record_id, OfferSnapshot, last_seen[, last_source_updated_at])] for the target's event / player / stat.
    -> per source the quote nearest the target line: {source: (rid, line, p_cond)} + reasons."""
    best, reasons = {}, []
    ev = events.get(target.event_id)
    for item in quotes:
        rid, q, seen = item[:3]
        last_src = item[3] if len(item) > 3 else None
        if q.platform == target.platform:
            continue                                         # target's own price never anchors it
        ok, why = capabilities.compatible(target, q)
        if not ok:
            reasons.append(f"{q.platform}: {why}")
            continue
        p, why = quote_prob(q, ev, forecast_at, seen, dnp_share, last_src)
        if p is None:
            reasons.append(f"{q.platform} {q.line:g}: {why}")
            continue
        src = source_of(q.platform)
        cand = (abs(q.line - target.line), q.line, rid, p)
        if src not in best or cand[:2] < best[src][:2]:
            best[src] = cand
    return {s: (v[2], v[1], v[3]) for s, v in best.items()}, reasons


def consensus(target, per_source, sources=None):
    """-> (p_over, p_under, p_push, used_sources) unconditional at the target line, or None."""
    use = {s: v for s, v in per_source.items() if sources is None or s in sources}
    if not use:
        return None
    logits, pushes = [], []
    for s, (_, line, p) in sorted(use.items()):
        c, push = at_target(target.league, target.stat, line, p, target.line)
        logits.append(_logit(c))
        pushes.append(push)
    c = _inv(sum(logits) / len(logits))
    half = abs(target.line - round(target.line)) > 1e-9
    push = 0.0 if half else float(np.mean(pushes))
    return c * (1 - push), (1 - c) * (1 - push), push, sorted(use)


def market_informed(pmf, lo, mu, per_source):
    """mi-1: shift sm-1's calibrated PMF by a mean factor c so its P(Over | no push) at each source's line matches the
    source (least squares on log-odds over sources). -> (pmf_c, c) or None."""
    if not per_source:
        return None
    ks = np.arange(lo, lo + len(pmf))
    targets = [(line, _logit(p)) for _, line, p in per_source.values()]

    def loss(c):
        q = dists.transform(pmf[None, :], lo, ks, np.array([mu]), c, 1.0)[0]
        e = 0.0
        for line, t in targets:
            o, u, _ = dists.line_probs(q, lo, line)
            e += (_logit(o / max(o + u, 1e-12)) - t) ** 2
        return e
    lo_c, hi_c = 0.3, 3.0
    for _ in range(60):                                       # golden-section on c (loss is unimodal in c)
        a = lo_c + (hi_c - lo_c) * 0.382
        b = lo_c + (hi_c - lo_c) * 0.618
        if loss(a) < loss(b):
            hi_c = b
        else:
            lo_c = a
    c = (lo_c + hi_c) / 2
    return dists.transform(pmf[None, :], lo, ks, np.array([mu]), c, 1.0)[0], c
