"""lw-1: line-informed chance for CS2 props (research/esports_accuracy_2026_10_25, REGISTER.md d0a6237, RESULTS.md).

    P(Over | no push) = Phi(a_g + w_g * z),   z = Phi^-1(the performance model's raw no-push Over chance)

g = stat x scope. Equivalent projection for a normal N(lam, sd) at the line's cut L*: mu = L* + sd * a_g + w_g (lam - L*):
the book line is kept and only the share w of the model's distance from it that the settled real lines showed to be real
is added. Kills keep the live 22-78% cap (applied by the grader after this transform).

Frozen fit: every settled P1 unit 2026-10-11..10-25 (3,650 units, HLTV-corrected settlements, data hash eb5822b13949),
maximum likelihood on the decided outcome per group; groups with < 300 units use the stat's pooled fit (registered rule).
Registered walk-forward (refit daily, Oct 14-25, 3,276 units / 188 matches): log loss +0.0067 [+0.0011, +0.0128] vs live,
Brier +0.0032 [+0.0005, +0.0060]; staked 56.3% (423) vs live 51.3% (1,262). Applies only to performance-model grades.
Rollback: LW1_LIVE=0.
"""
import math
import os

VERSION = "lw-1/2026-10-25"
DATA_HASH = "eb5822b13949"
# (a, w) per 'prop|scope'; exact values of research/esports_accuracy_2026_10_25/outputs/lw1_final.json
PARAMS = {
    "kills|12": (0.03377003115241389, 0.04297731215832612),     # group fit, 1,731 units
    "kills|1": (0.016943694951364868, -0.060843352814198344),   # kills pooled (1,901): Map 1 had < 300 units
    "kills|123": (0.016943694951364868, -0.060843352814198344),
    "hs|12": (-0.0037981768616451657, 0.3963116877412208),      # group fit, 1,602 units
    "hs|1": (-0.009393165340405959, 0.271803094493522),         # HS pooled (1,749)
    "hs|123": (-0.009393165340405959, 0.271803094493522),
}


def enabled():
    return os.environ.get("LW1_LIVE", "1") != "0"


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _phi_inv(p):
    """Inverse standard normal CDF (bisection to 1e-12; deterministic on every platform)."""
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if _phi(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def chance(prop, scope, p_raw, params=None):
    """(p, a, w, z) - the line-informed no-push Over chance from the model's raw no-push chance (the forecast's
    probs_raw.p_more_no_push, 4 decimals), or None when this stat / scope has no fit."""
    ab = (params or PARAMS).get(f"{prop}|{scope or '12'}")
    if ab is None or p_raw is None:
        return None
    a, w = ab
    z = _phi_inv(min(max(float(p_raw), 0.001), 0.999))
    return _phi(a + w * z), a, w, z


def projection(line, p, sd, cuts):
    """The normal mean that gives P(Over) / (P(Over) + P(Under)) = p at this line with the model's sd (the line-informed
    projection shown with the chance; bisection). cuts = grader._cuts."""
    up, down = cuts(float(line))

    def pm(mu):
        po, pu = 1 - _phi((up - mu) / sd), _phi((down - mu) / sd)
        return po / (po + pu) if po + pu > 0 else 0.5
    lo, hi = float(line) - 12 * sd, float(line) + 12 * sd
    for _ in range(100):
        mid = (lo + hi) / 2
        if pm(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
