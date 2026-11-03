"""Evidence required before a shared grading candidate can replace live grades."""
import math
from datetime import timedelta
from .evaluation import timestamp


def readiness(historical, prospective, now):
    reasons = []
    if not historical.get('historical_gate_pass'):
        reasons.append('Held-out log-loss improvement has not passed')
    bins = historical.get('candidate', {}).get('reliability', [])
    if not bins or any(not isinstance(b.get('gap'), (int, float)) or
                       not math.isfinite(b['gap']) or abs(b['gap']) > .03 for b in bins):
        reasons.append('Held-out reliability bins are not all within three points')
    try:
        elapsed = timestamp(now) - timestamp(prospective['started_at'])
        if elapsed < timedelta(days=7):
            reasons.append('Seven prospective days have not elapsed')
    except (KeyError, TypeError, ValueError):
        reasons.append('Prospective start is not recorded')
    if prospective.get('unique_forecasts', 0) < 500:
        reasons.append('Fewer than 500 settled prospective forecasts')
    ci = prospective.get('delta_ci') or []
    if len(ci) != 2 or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in ci) or ci[1] >= 0:
        reasons.append('Prospective improvement is not established')
    if prospective.get('side_balance_pass') is not True:
        reasons.append('Over and Under balance has not passed')
    return {'eligible': not reasons, 'reasons': reasons, 'minimum_days': 7,
            'minimum_forecasts': 500, 'checked_at': now}
