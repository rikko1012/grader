"""Projection diagnostics at an immutable offered line; not bet settlement.

No payout, letter, probability or win-rate is inferred from a point estimate.
Book participation/retirement rules belong to the settlement adapter.
Finite negative values are valid (for example, NFL rushing yards).
"""
import math


def review(projection, actual, line):
    values = (projection, actual) if line is None else (projection, actual, line)
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or
           not math.isfinite(v) for v in values):
        raise ValueError('finite projection, observation and line required')
    error = projection - actual
    side = None if line is None else 'over' if projection > line else 'under' if projection < line else None
    actual_side = None if line is None else 'over' if actual > line else 'under' if actual < line else 'equal'
    return {'error': error, 'absolute_error': abs(error), 'squared_error': error * error,
            'projected_side': side, 'observed_side': actual_side,
            'crossed_correctly': (side == actual_side) if side and actual_side != 'equal' else None,
            'settlement_status': None}
