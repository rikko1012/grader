"""Exact priced-bet EV and compatible two-sided quote comparisons.

These functions describe quoted arithmetic, never guaranteed execution/profit.
The caller must supply validated probabilities and independently verified prices.
"""
from dataclasses import dataclass
import math


def number(x, low, high=math.inf):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or not low <= x <= high:
        raise ValueError('invalid numeric input')
    return float(x)


def expected_value(p_win, p_loss, decimal_odds, commission=0.0):
    win, loss = number(p_win, 0, 1), number(p_loss, 0, 1)
    if win + loss > 1 + 1e-9:
        raise ValueError('outcomes exceed one')
    odds, fee = number(decimal_odds, 1.000000001), number(commission, 0, 1)
    return win * (odds - 1) * (1 - fee) - loss


@dataclass(frozen=True)
class Quote:
    sport: str
    event_id: str
    player_id: str
    stat: str
    period: str
    line: float
    settlement_rule: str
    book: str
    jurisdiction: str
    side: str
    decimal_odds: float
    source: str
    observed_at: float
    source_at: float
    available: bool = True
    liquid: bool = True
    commission: float = 0.0

    def __post_init__(self):
        for field in ('sport', 'event_id', 'player_id', 'stat', 'period', 'settlement_rule', 'book', 'jurisdiction', 'source'):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError('quote identity and provenance required')
        number(self.line, -math.inf)
        number(self.decimal_odds, 1.000000001)
        number(self.commission, 0, 1)
        number(self.observed_at, 0)
        number(self.source_at, 0)
        if self.side not in ('over', 'under') or self.source_at > self.observed_at:
            raise ValueError('invalid quote side or timestamp')

    def identity(self):
        return (self.sport, self.event_id, self.player_id, self.stat, self.period,
                self.line, self.settlement_rule, self.jurisdiction)


def compare_pair(a, b, now, max_age=120):
    number(now, 0)
    number(max_age, 0)
    if a.identity() != b.identity() or a.side == b.side or a.book == b.book:
        return {'eligible': False, 'reason': 'Quotes do not cover the same compatible two-sided market'}
    for q in (a, b):
        if not q.available or not q.liquid:
            return {'eligible': False, 'reason': 'Unavailable or insufficiently liquid quote'}
        if q.observed_at > now or q.source_at > now or now - q.source_at > max_age or now - q.observed_at > max_age:
            return {'eligible': False, 'reason': 'Stale or future quote'}
    odds = [1 + (q.decimal_odds - 1) * (1 - q.commission) for q in (a, b)]
    implied = [1 / x for x in odds]
    total = sum(implied)
    return {'eligible': True, 'opportunity': total < 1, 'stake_shares': [x / total for x in implied],
            'quoted_return': 1 / total - 1, 'push_return': 0.0,
            'execution_guaranteed': False, 'sources': [a.source, b.source]}


# ------------------------------------------------------------------------------------------------ Phase 4 EV / arb sheet
# The Tools workspace (workspace_markets.py) prices every row through these functions and nothing else.
WIDE_OVERROUND = 0.08          # a two-sided market whose prices add to more than 108% is "wide": less trust in its fair price
EV_STALE_S = 30 * 60           # a quote older than this is never priced
ARB_MAX_AGE_S = 5 * 60         # an arbitrage leg older than this is flagged and left out


def implied(decimal_odds):
    """1 / decimal price: the break-even chance a price implies (margin included)."""
    return 1.0 / number(decimal_odds, 1.000000001)


def ev_push_adjusted(p_cal, decimal_odds, p_push=0.0):
    """EV per 1 unit staked = p_cal x decimal_odds - 1, push-adjusted: a push hands the stake back, so it adds p_push.
    p_cal is the UNCONDITIONAL win chance. Never a probability-point gap."""
    p, d, pp = number(p_cal, 0, 1), number(decimal_odds, 1.000000001), number(p_push, 0, 1)
    if p + pp > 1 + 1e-9:
        raise ValueError('outcomes exceed one')
    return p * d + pp - 1.0


def _american(d):
    return (d - 1) * 100 if d >= 2 else -100 / (d - 1)


def market_width(over_decimal, under_decimal, wide=WIDE_OVERROUND):
    """Market-width check of one book's two-sided price: overround (sum of implied - 1), the American-odds width in cents
    (-110/-110 = 20 cents, -125/+120 = 5 cents) and wide = overround above `wide`."""
    a, b = number(over_decimal, 1.000000001), number(under_decimal, 1.000000001)
    xa, xb = _american(a), _american(b)
    if xa < 0 and xb < 0:
        cents = -xa - xb - 200
    elif xa >= 0 and xb >= 0:
        cents = 200 - xa - xb
    else:
        cents = abs(min(xa, xb)) - max(xa, xb)
    over = 1 / a + 1 / b - 1
    return {'overround': over, 'width_cents': round(cents, 1), 'wide': over > wide}


def arb_two_way(decimal_a, decimal_b):
    """Two opposite sides on two books. impA = 1/oddsA, impB = 1/oddsB.
    stake share A = impA / (impA + impB); margin % = (1 / (impA + impB) - 1) x 100. arbitrage = impA + impB < 1."""
    ia, ib = implied(decimal_a), implied(decimal_b)
    total = ia + ib
    return {'implied': [ia, ib], 'total_implied': total, 'stake_share': [ia / total, ib / total],
            'margin_pct': (1.0 / total - 1.0) * 100.0, 'arbitrage': total < 1.0}
