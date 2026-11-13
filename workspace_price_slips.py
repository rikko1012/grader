"""Read-only price-based drafts. Never submits bets or promises future returns."""
import math
import time
from decimal import Decimal, ROUND_DOWN


def positive_pool(legs, doc, horizon=20):
    """Intersect the ordinary eligible pool with exact positive-EV sides.

    Keep published model numbers untouched. Only the search ranking uses the
    book consensus. No model-only, opposite-side or fallback leg fills a batch.
    """
    from workspace_books import _ts
    from gradecore.fair import FRESHNESS_S
    built = _ts(doc.get('as_of'))
    elapsed = time.time()-built if built is not None else float('inf')
    def fresh(r):
        sources = r.get('sources') or []
        return 0 <= elapsed <= 60 and bool(sources) and all(
            isinstance(s.get('age_s'), (int, float)) and 0 <= s['age_s']
            and s['age_s']+elapsed+horizon <= FRESHNESS_S.get(s.get('kind'), 0) for s in sources)
    rows = {(r['key'], r['side']): r for r in doc.get('rows', [])
            if r.get('basis') == 'market' and isinstance(r.get('ev'), (int, float))
            and r['ev'] > 0 and 0 < r.get('p_market', 0) < 1 and fresh(r)}
    out = []
    for leg in legs:
        key = '|'.join((leg['sport'], leg['book'], str(leg['row_id'])))
        r = rows.get((key, leg['side']))
        if r and r['line'] == leg['line'] and leg.get('line_type') == 'standard' and leg.get('multiplier') in (None, 1, 1.0):
            out.append(dict(leg, rank_p=r['p_market']))
    return out


def draft(doc, mode, leg_id, books, budget, now=None, expected=None):
    """Generate an exact current sportsbook single or a separate matched pair.

    Candidate rows must already pass the market engine's event/stat/period,
    pricing-family and settlement-rule checks. Recheck time and rounded stakes.
    """
    from workspace_books import _ts
    now = time.time() if now is None else now
    try:
        total = Decimal(str(budget)).quantize(Decimal('.01'), rounding=ROUND_DOWN)
        if not total.is_finite() or not Decimal('1') <= total <= Decimal('100000'):
            raise ValueError
    except Exception:
        raise ValueError('Choose a total stake from $1 to $100,000.') from None
    if mode not in ('ev', 'arb'):
        raise ValueError('Choose EV or arbitrage.')
    rows = [r for d in doc.get('sports', {}).values() for r in d.get('arbitrage' if mode == 'arb' else 'ev', [])]
    def matches(r):
        target = ','.join(l['book'] for l in r['legs']) if mode == 'arb' else r['book']+':'+r['side']
        return r['leg_id'] == leg_id and books == target
    row = next((r for r in rows if matches(r)), None)
    if row is None:
        raise ValueError('This opportunity is no longer available. Refresh prices.')
    start = _ts(row.get('start_utc'))
    if start is None or start <= now:
        raise ValueError('This game has started or its start time is unavailable.')
    legs = row['legs'] if mode == 'arb' else [row]
    if expected is not None and expected != [[l['book'],l['side'],l['odds']] for l in legs]:
        raise ValueError('The price changed. Refresh prices and generate again.')
    limit = doc.get('method', {}).get('arb_max_age_s' if mode == 'arb' else 'ev_stale_s', 300 if mode == 'arb' else 1800)
    for l in legs:
        seen = _ts(l.get('seen_at'))
        if l.get('kind') != 'sportsbook' or l.get('flags') or seen is None or not 0 <= now-seen <= limit:
            raise ValueError('A price is stale or cannot be used as a standalone sportsbook bet.')
        if not isinstance(l.get('odds'), (int, float)) or not math.isfinite(l['odds']) or l['odds'] <= 1:
            raise ValueError('A valid price is missing.')
    if mode == 'arb':
        if len(legs) != 2 or {l['side'] for l in legs} != {'over', 'under'} or legs[0]['book'] == legs[1]['book']:
            raise ValueError('A matched pair needs opposite sides at two books.')
        a, b = [Decimal(str(l['odds'])) for l in legs]
        ideal = total*b/(a+b)
        lo = ideal.quantize(Decimal('.01'), rounding=ROUND_DOWN)
        split = max((lo, lo+Decimal('.01')), key=lambda s: min(s*a, (total-s)*b))
        stakes = [split, total-split]
        gross = min(stakes[0]*a, stakes[1]*b).quantize(Decimal('.01'), rounding=ROUND_DOWN)
        if min(stakes) <= 0 or gross <= total:
            raise ValueError('No positive margin remains after rounding the stakes.')
    else:
        if not row.get('ev', 0) > 0:
            raise ValueError('This price no longer has positive expected value.')
        stakes, gross = [total], None
    return {'mode': mode, 'checked_at': now, 'total_stake': float(total),
            'minimum_return': float(gross) if gross is not None else None,
            'profit_if_either_wins': float(gross-total) if gross is not None else None,
            'note': ('Separate bets, not a parlay. Amounts assume both quoted prices are accepted. Pushes and voids follow the books\' matching rules.' if mode == 'arb' else 'One straight bet at the quoted price. Expected value is an estimate, not a guaranteed return.'),
            'slips': [dict(book=l['book'], book_label=l['book_label'], side=l['side'], odds=l['odds'],
                          american=l.get('american'), stake=float(s), seen_at=l['seen_at'],
                          **{k: row.get(k) for k in ('sport','event','event_id','player','stat','stat_label','period_label','line','leg_id')})
                      for l,s in zip(legs,stakes)]}
