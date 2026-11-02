"""Read-only threshold comparison, independent of grade and stake policy.

Adapters must provide canonical IDs and CURRENT offer revisions, including
withdrawals. This does not claim equal payouts, EV, or permission to substitute
a leg. A new offer needs its own forecast and tracking validation.
"""
from dataclasses import dataclass
from datetime import datetime
import math


@dataclass(frozen=True)
class QuoteEvidence:
    sport: str
    league: str
    event_id: str
    player_id: str
    stat: str
    period: str
    book: str
    offer_id: str
    revision: str
    line: float
    observed_at: datetime
    start_at: datetime
    over_available: bool
    under_available: bool
    active: bool = True
    feed_state: str = "ok"
    variant: str = "standard"

    @property
    def identity(self):
        return (self.sport, self.league, self.event_id, self.player_id,
                self.stat, self.period)


@dataclass(frozen=True)
class LineAdvantage:
    book: str
    offer_id: str
    revision: str
    side: str
    original_line: float
    alternative_line: float
    threshold_improvement: float
    # Deliberately no transferred probability, grade, stake, or EV.
    requires_new_forecast: bool = True
    payout_comparison: str = "not_evaluated"


def _aware(value):
    return isinstance(value, datetime) and value.utcoffset() is not None


def _valid(q, side, now, max_age_seconds):
    if not all(isinstance(v, str) and v.strip() for v in
               (*q.identity, q.book, q.offer_id, q.revision)):
        return False
    if not _aware(q.observed_at) or not _aware(q.start_at):
        return False
    if isinstance(q.line, bool) or not isinstance(q.line, (int, float)):
        return False
    if not math.isfinite(q.line) or q.line < 0:
        return False
    if q.active is not True or q.feed_state != "ok" or q.variant != "standard":
        return False
    if (q.over_available if side == "over" else q.under_available) is not True:
        return False
    return (q.start_at > now and
            0 <= (now - q.observed_at).total_seconds() <= max_age_seconds)


def find_line_advantages(target, alternatives, *, side, now, max_age_seconds=600):
    """Find easier thresholds on the SAME side without reselecting the bet.

Fail closed on stale/future quotes, unknown identity, moved target revisions,
and contradictory copies of an offer. Two books can have different economics;
these are threshold advantages only. No automatic switching or grading.
"""
    if side not in ("over", "under"):
        raise ValueError("side must be over or under")
    if not _aware(now):
        raise ValueError("now must include a timezone")
    if (isinstance(max_age_seconds, bool) or
            not isinstance(max_age_seconds, (int, float)) or
            not math.isfinite(max_age_seconds) or max_age_seconds < 0):
        raise ValueError("max age must be finite and nonnegative")
    alternatives = list(alternatives)
    if not _valid(target, side, now, max_age_seconds):
        return ()
    by_offer = {}
    for q in [target, *alternatives]:
        key = (q.book, q.offer_id)
        by_offer.setdefault(key, []).append(q)
    ambiguous = {key for key, copies in by_offer.items()
                 if any(q != copies[0] for q in copies[1:])}
    if (target.book, target.offer_id) in ambiguous:
        return ()
    found = []
    for key, copies in by_offer.items():
        q = copies[0]
        if key in ambiguous or q.book == target.book:
            continue
        if q.identity != target.identity or q.start_at != target.start_at:
            continue
        if not _valid(q, side, now, max_age_seconds):
            continue
        delta = target.line - q.line if side == "over" else q.line - target.line
        if delta > 0:
            found.append(LineAdvantage(q.book, q.offer_id, q.revision, side,
                                       target.line, q.line, delta))
    return tuple(sorted(found, key=lambda x: (-x.threshold_improvement, x.book,
                                             x.offer_id, x.revision)))
