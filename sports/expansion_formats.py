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
