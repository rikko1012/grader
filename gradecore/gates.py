"""Shared caps. A gate never removes the side or the letter: it caps the stake and says why. Only a withheld flag
(contained, unvalidated) takes the letter away.

Sports hand in facts as flags (sport-specific ones like awper_hs are listed here too, so every cap sits in one table).
facts() turns plain numbers into the shared flags. The CS2 values copy grader.py; the market values are starting values.
"""
from dataclasses import dataclass

CONTAINED, UNVALIDATED = "contained", "unvalidated"
WITHHOLD = {CONTAINED: "Letters paused: accuracy check not passed",
            UNVALIDATED: "Projection only - model not yet validated"}

SAMPLE_LOW, SAMPLE_THIN = "sample_low", "sample_thin"
RUST, STALE_MARKET, WIDE_MARKET, THIN_MARKET, NO_MARKET = "rust", "stale_market", "wide_market", "thin_market", "no_market"
TRANSFER, STANDIN, STANDIN_OTHER_TEAM, AWPER_HS = "transfer", "standin", "standin_other_team", "awper_hs"
SAME_MATCH = "same_match"

CAPS = {                        # flag -> (max units, reason)
    TRANSFER: (0.0, "not on the listed team"),
    STANDIN: (0.0, "stand-in"),
    STANDIN_OTHER_TEAM: (1.0, "stand-in for another team"),
    AWPER_HS: (0.0, "AWPer on a headshot prop"),
    SAMPLE_LOW: (0.0, "small sample"),
    SAMPLE_THIN: (1.5, "thin data"),
    RUST: (1.0, "long break"),
    STALE_MARKET: (1.5, "market price is stale"),
    WIDE_MARKET: (1.5, "wide market"),
    THIN_MARKET: (1.5, "thin market"),
    NO_MARKET: (1.5, "no betting market"),
    SAME_MATCH: (0.0, "another pick from this match"),
}
SAMPLE_LOW_N, SAMPLE_THIN_N = 5, 20     # recent games / Maps 1-2
RUST_DAYS = 29
STALE_MARKET_S = 1800.0
WIDE_MARKET_WIDTH = 0.10                # overround
MIN_MARKET_BOOKS = 2
MAX_PER_MATCH = 1


@dataclass(frozen=True)
class Gate:
    cap: float | None           # max units, None = no cap
    withheld: tuple = ()        # why no letter may show
    reasons: tuple = ()         # why the stake is capped, tightest first
    unknown: tuple = ()         # flags no table knows: no effect, kept so a typo can be seen


def facts(sample=None, days_idle=None, market_age_s=None, market_width=None, market_books=None):
    """Numbers -> the shared flags. A number that is None adds nothing."""
    f = set()
    if sample is not None:
        if sample < SAMPLE_LOW_N:
            f.add(SAMPLE_LOW)
        elif sample < SAMPLE_THIN_N:
            f.add(SAMPLE_THIN)
    if days_idle is not None and days_idle >= RUST_DAYS:
        f.add(RUST)
    if market_age_s is not None and market_age_s > STALE_MARKET_S:
        f.add(STALE_MARKET)
    if market_width is not None and market_width > WIDE_MARKET_WIDTH:
        f.add(WIDE_MARKET)
    if market_books is not None and market_books < MIN_MARKET_BOOKS:
        f.add(THIN_MARKET)
    return f


def evaluate(flags=()):
    flags = set(flags or ())
    hit = sorted((CAPS[x] for x in flags if x in CAPS))
    return Gate(cap=hit[0][0] if hit else None, withheld=tuple(WITHHOLD[x] for x in sorted(flags) if x in WITHHOLD),
                reasons=tuple(f"{r}: max {c:g}U" for c, r in hit),
                unknown=tuple(sorted(x for x in flags if x not in CAPS and x not in WITHHOLD)))


def same_match_extras(legs, max_per_match=None):
    """legs: [(match_key, p), ...]. -> indices beyond the per-match limit, keeping the highest chances (earliest on a tie).
    These legs take the SAME_MATCH flag."""
    limit = MAX_PER_MATCH if max_per_match is None else max_per_match
    by = {}
    for i, (key, p) in enumerate(legs):
        by.setdefault(key, []).append((-p, i))
    return {i for rows in by.values() for _, i in sorted(rows)[limit:]}
