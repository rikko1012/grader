"""Pure shared slip arithmetic; no live callers until the shadow gate passes.

Inputs are chosen-side UNCONDITIONAL probabilities. Independent games use an
exact outcome sum. Same-game legs require a supplied, validated latent-normal
correlation for the chosen sides and use a reproducible 10,000-draw copula.
Payout rules are supplied by the caller as a function of the complete outcome
tuple. No default table, push reduction, modifier or DNP probability is invented.
"""
from itertools import product
import math
from statistics import NormalDist

import numpy as np

DRAWS = 10_000
SEED = 7
OUTCOMES = ("win", "loss", "push")


def _validated(legs):
    legs = tuple(legs)
    if not 2 <= len(legs) <= 6:
        raise ValueError("a slip requires 2–6 legs")
    if len({l["book"] for l in legs}) != 1:
        raise ValueError("one platform per slip")
    players = [(l["sport"], l["player_id"]) for l in legs]
    if len(set(players)) != len(players):
        raise ValueError("a player may appear only once")
    for l in legs:
        if not all(l.get(k) for k in ("sport", "book", "player_id", "match_key", "offer_revision")):
            raise ValueError("leg identity and offer revision are required")
        p = [l.get(k) for k in ("p_win", "p_loss", "p_push")]
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
               or not 0 <= x <= 1 for x in p) or abs(sum(p) - 1) > 1e-6:
            raise ValueError("unconditional win/loss/push probabilities must be finite and sum to one")
    return legs


def distribution(legs, correlation=None, seed=SEED):
    """Return (outcome distribution, basis), or (None, refusal).

    correlation(a,b) must describe the latent chosen-side variables, not the raw
    stat correlation (an Under changes the sign). None is not treated as zero.
    Event keys are namespaced by sport so unrelated provider IDs cannot collide.
    """
    legs = _validated(legs)
    n = len(legs)
    matrix = np.eye(n)
    same_game = False
    for i in range(n):
        for j in range(i):
            a, b = legs[i], legs[j]
            if (a["sport"], a["match_key"]) != (b["sport"], b["match_key"]):
                continue
            same_game = True
            rho = correlation(a, b) if correlation is not None else None
            if rho is None:
                return None, "same-game correlation has not been validated"
            if isinstance(rho, bool) or not isinstance(rho, (int, float)) or not math.isfinite(rho) or abs(rho) > 1:
                raise ValueError("correlation must be finite and between -1 and 1")
            matrix[i, j] = matrix[j, i] = rho
    if not same_game:
        options = [[(outcome, l[key]) for outcome, key in zip(OUTCOMES, ("p_win", "p_loss", "p_push"))
                    if l[key] > 0] for l in legs]
        return {tuple(o for o, _ in combo): math.prod(p for _, p in combo)
                for combo in product(*options)}, "exact sum; separate games assumed independent"

    eigenvalues, vectors = np.linalg.eigh(matrix)
    if eigenvalues.min() < -1e-8:
        return None, "correlation estimates do not form a valid joint model"
    # Clip floating-point noise only; never repair a substantively invalid model.
    factor = vectors @ np.diag(np.sqrt(np.maximum(eigenvalues, 0)))
    samples = np.random.default_rng(seed).standard_normal((DRAWS, n)) @ factor.T
    normal = NormalDist()

    def threshold(p):
        return -math.inf if p <= 0 else math.inf if p >= 1 else normal.inv_cdf(p)

    encoded = np.zeros((DRAWS, n), dtype=np.int8)
    for i, leg in enumerate(legs):
        # Low latent values win; middle values push; high values lose.
        win = threshold(leg["p_win"])
        no_loss = threshold(leg["p_win"] + leg["p_push"])
        encoded[:, i] = np.where(samples[:, i] < win, 0, np.where(samples[:, i] < no_loss, 2, 1))
    outcomes, counts = np.unique(encoded, axis=0, return_counts=True)
    return {tuple(OUTCOMES[int(x)] for x in row): int(count) / DRAWS
            for row, count in zip(outcomes, counts)}, f"Gaussian copula estimate; {DRAWS} draws; seed {seed}"


def _kelly(returns):
    """Maximize expected log bankroll for a full outcome payout distribution."""
    net = [(p, multiple - 1) for p, multiple in returns]
    if sum(p * r for p, r in net) <= 0:
        return 0.0
    lo, hi = 0.0, 1.0 - 1e-12
    for _ in range(80):
        mid = (lo + hi) / 2
        derivative = sum(p * r / (1 + mid * r) for p, r in net)
        if derivative > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def evaluate(legs, payout, correlation=None, seed=SEED):
    """payout(tuple[win/loss/push]) -> verified gross multiple, or None.

    Kelly is a bankroll fraction, not units. The caller still applies containment,
    staking caps and platform eligibility. This calculation cannot place a bet.
    """
    legs = tuple(legs)
    dist, basis = distribution(legs, correlation=correlation, seed=seed)
    out = {"available": dist is not None, "basis": basis, "p_all_win": None,
           "p_no_loss": None, "expected_multiple": None, "net_ev": None,
           "kelly_fraction": None, "quarter_kelly_fraction": None,
           "note": "DNP/void probabilities are not modeled"}
    if dist is None:
        return out
    out["p_all_win"] = sum(p for results, p in dist.items() if all(x == "win" for x in results))
    out["p_no_loss"] = sum(p for results, p in dist.items() if "loss" not in results)
    # Even a rare outcome missed by 10k draws needs a published payout rule.
    # Conservatively require all marginally possible combinations, including
    # some a perfectly correlated model could make unreachable.
    schedules = {}
    options = [[o for o, key in zip(OUTCOMES, ("p_win", "p_loss", "p_push")) if l[key] > 0] for l in legs]
    for results in product(*options):
        multiple = payout(results)
        if multiple is None:
            out["payout_reason"] = "a reachable outcome has no verified payout"
            return out
        if isinstance(multiple, bool) or not isinstance(multiple, (int, float)) or not math.isfinite(multiple) or multiple < 0:
            raise ValueError("payout must be a finite nonnegative gross multiple")
        schedules[results] = multiple
    returns = [(p, schedules[results]) for results, p in dist.items()]
    out["expected_multiple"] = sum(p * multiple for p, multiple in returns)
    out["net_ev"] = out["expected_multiple"] - 1
    out["kelly_fraction"] = _kelly(returns)
    out["quarter_kelly_fraction"] = out["kelly_fraction"] / 4
    return out
