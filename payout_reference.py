"""Published reference rates, not a quote for a member's specific lineup.

Source explicitly permits contest-specific changes. Owner sign-off 2026-11-05 ("Mark the PrizePicks help-center payout
table as verified so PrizePicks gets EV and break-even.") verified these standard-line rates for break-even and EV
display (gradecore/pickem_payouts.json carries the record). Stakes and settlement still never read them; goblin / demon /
promo / same-game lineups pay unpublished multipliers and stay unavailable.
"""
PP_SOURCE = 'https://www.prizepicks.com/help-center/payouts'
PP_PAGE_DATE = '2026-10-19'
PP_READ_AT = '2026-11-03'
PP_POWER = {2: 3.0, 3: 6.0, 4: 10.0, 5: 20.0, 6: 37.5}
PP_FLEX = {2: {0: 2.0, 1: .5}, 3: {0: 3.0, 1: 1.0},
           4: {0: 6.0, 1: 1.5}, 5: {0: 10.0, 1: 2.0, 2: .4},
           6: {0: 25.0, 1: 2.0, 2: .4}}


def prizepicks(entry, n):
    table = PP_POWER if entry == 'power' else PP_FLEX if entry == 'flex' else {}
    rate = table.get(n)
    if rate is None: return None
    return {'multiple': rate if isinstance(rate, float) else rate[0],
            'tiers': {0: rate} if isinstance(rate, float) else dict(rate),
            'entry_type': entry, 'source': PP_SOURCE, 'page_date': PP_PAGE_DATE,
            'read_at': PP_READ_AT, 'reference_only': True, 'label': 'Published base payout'}
