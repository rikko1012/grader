"""Verified singles formats for explicitly identified feeder competitions.

Never infer men's Grand Slam format from an ATP endpoint or completed set count.
"""
SOURCES = {
    'atp_challenger':'https://www.atptour.com/-/media/files/rulebook/2026/2026-rulebook-chapter-1_atp-circuit-regulations19dec25.pdf',
    'wta125':'https://photoresources.wtatennis.com/wta/document/2025/12/24/b300b2a4-8d71-4346-969f-1f6b9399661f/2026-WTA-Rulebook-12-22-2025-.pdf',
}
READ_AT = '2026-11-17T22:45:00Z'


def context(competition, match):
    out = {k:match[k] for k in ('surface','best_of','surface_source','surface_known_at') if match.get(k) is not None}
    if competition in SOURCES and not out.get('best_of'):
        out.update(best_of=3, format_source=SOURCES[competition], format_known_at=READ_AT)
    return out


# sg-scope (2026-11-20): ATP / WTA tour singles formats from ESPN's Grand Slam flag (event.major), for full-match
# lines and their history only (context() above is unchanged, so set-scope lines keep their bytes).
# - Women's singles are best of three at every level, Grand Slams included (2026 WTA Rulebook, SOURCES['wta125']).
# - ATP non-major singles, the ATP Finals included, are best of three (2026 ATP Rulebook, SOURCES['atp_challenger']).
# - Men's Grand Slam singles are NOT inferred (main draw best of five, qualifying varies by event): no rule.
# - Next Gen ATP Finals use short sets: no rule (its scores also fail the ordinary-set check in the parser).
TOUR_READ_AT = '2026-11-20T07:30:00Z'


def tour_rule(competition, match):
    """-> {'best_of': 3, 'basis': ..., 'source': ..., 'known_at': ...} or None."""
    match = match or {}
    major = match.get('major')
    if not isinstance(major, bool) or 'next gen' in str(match.get('name') or '').lower():
        return None
    if competition == 'wta':
        return {'best_of': 3, 'basis': 'wta_singles_best_of_three', 'source': SOURCES['wta125'],
                'known_at': TOUR_READ_AT}
    if competition == 'atp' and major is False:
        return {'best_of': 3, 'basis': 'atp_non_major_best_of_three', 'source': SOURCES['atp_challenger'],
                'known_at': TOUR_READ_AT}
    return None
