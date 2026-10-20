"""Assists level (lr-1, lol/EVALUATION.md A9): a rolling, league-level correction of the projection from recent residuals.

Residual units are earlier targets' game-1 totals (day, league, actual y, projection mu). For a target on day d in league L:
  c_G = sum y / sum mu over every residual unit with day in [d - W, d)          (1 when there is none)
  c_L = (sum y_L + K * mbar_L * c_G) / (sum mu_L + K * mbar_L), mbar_L = mean mu_L  (c_G when L has none)
and the projection is multiplied by c_L. Pure; the same code serves the study and live forecasts.
"""
import bisect
from collections import defaultdict


class Level:
    def __init__(self, units, W, K):
        """units: iterable of (day ordinal, league, y, mu)."""
        self.W, self.K = int(W), float(K)
        self._g = self._prefix(units)
        by = defaultdict(list)
        for u in units:
            by[u[1]].append(u)
        self._l = {lg: self._prefix(us) for lg, us in by.items()}

    @staticmethod
    def _prefix(units):
        us = sorted(units, key=lambda u: u[0])
        days, sy, sm, n = [], [0.0], [0.0], [0]
        for d, _, y, mu in us:
            days.append(d)
            sy.append(sy[-1] + y)
            sm.append(sm[-1] + mu)
            n.append(n[-1] + 1)
        return days, sy, sm, n

    def _window(self, pre, day):
        days, sy, sm, n = pre
        i, j = bisect.bisect_left(days, day - self.W), bisect.bisect_left(days, day)
        return sy[j] - sy[i], sm[j] - sm[i], n[j] - n[i]

    def factor(self, league, day):
        """(c_L, units in the league window, c_G)."""
        y, m, n = self._window(self._g, day)
        cg = y / m if n and m > 0 else 1.0
        pre = self._l.get(league)
        if pre is None:
            return cg, 0, cg
        yl, ml, nl = self._window(pre, day)
        if not nl or ml <= 0:
            return cg, 0, cg
        mbar = ml / nl
        return (yl + self.K * mbar * cg) / (ml + self.K * mbar), nl, cg
