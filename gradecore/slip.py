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


# ------------------------------------------------------------------------------------------------ competitor spec, section 3
# Pick'em entries by hit count. Each leg's chance is GIVEN NO PUSH (pushes removed, spec "Win (no push)"): a pushed leg
# shrinks the entry instead of losing it, so it is left out of the win chance. Tiers = {hits: gross multiple} from
# payouts.tiers() (verified tables only). Nothing here places, sizes a grader stake or changes a grade.
COPULA_DRAWS = 20_000
Z95 = 1.959963984540054
KELLY_FRACTION = 0.25
UNIT_SHARE = 0.01               # display: 1 unit = 1% of the entered bankroll, unless the member sets a unit size


def _probs(probs):
    out = []
    for p in probs:
        if isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError("leg chances must be finite numbers between 0 and 1")
        out.append(float(p))
    if not out:
        raise ValueError("a slip needs at least one leg")
    return out


def poisson_binomial(probs):
    """P(k hits) for independent legs. DP: P_0 = 1; for each leg i: P_k <- P_k (1 - p_i) + P_{k-1} p_i."""
    dist = [1.0]
    for p in _probs(probs):
        nxt = [0.0] * (len(dist) + 1)
        for k, q in enumerate(dist):
            nxt[k] += q * (1.0 - p)
            nxt[k + 1] += q * p
        dist = nxt
    return dist


def _payout_vector(tiers, n):
    if not tiers:
        raise ValueError("a payout table is required")
    pay = [0.0] * (n + 1)
    for k, m in tiers.items():
        k = int(k)
        if not 0 <= k <= n or isinstance(m, bool) or not isinstance(m, (int, float)) or not math.isfinite(m) or m < 0:
            raise ValueError("payout tiers must map 0..n hits to finite nonnegative multiples")
        pay[k] = float(m)
    return pay


def returns_of(hit_dist, tiers):
    """[(probability, gross multiple)] of the entry, one row per hit count."""
    n = len(hit_dist) - 1
    pay = _payout_vector(tiers, n)
    return [(p, pay[k]) for k, p in enumerate(hit_dist)]


def slip_ev(probs, tiers):
    """Independent legs: exact win chance and EV. EV = sum_k P(k) payout_k - 1.
    -> {"n", "hit_dist", "p_all_hit", "p_paid", "expected_multiple", "ev", "multiple"}."""
    dist = poisson_binomial(probs)
    n = len(dist) - 1
    pay = _payout_vector(tiers, n)
    exp = sum(p * m for p, m in zip(dist, pay))
    return {"n": n, "hit_dist": dist, "p_all_hit": dist[n], "p_paid": sum(p for p, m in zip(dist, pay) if m > 0),
            "expected_multiple": exp, "ev": exp - 1.0, "multiple": pay[n], "basis": "exact; legs independent"}


def entry_ev(probs, book, entry, config=None):
    """slip_ev on a verified entry table, or an 'unavailable' answer when the table is not verified."""
    from . import payouts
    t = payouts.tiers(book, entry, len(probs), config)
    if t is None:
        return {"available": False, "reason": f"{book} {entry} {len(probs)}-pick payout is not verified"}
    out = slip_ev(probs, t)
    out.update(available=True, book=book, entry=entry, tiers=t,
               break_even=payouts.entry_break_even(book, entry, len(probs), config))
    return out


def _corr_matrix(n, corr):
    m = np.eye(n) if corr is None else np.array(corr, dtype=float)
    if m.shape != (n, n) or not np.all(np.isfinite(m)) or not np.allclose(m, m.T) or not np.allclose(np.diag(m), 1):
        raise ValueError("correlation must be a symmetric n x n matrix with ones on the diagonal")
    if np.any(np.abs(m) > 1):
        raise ValueError("correlations must lie between -1 and 1")
    vals, vecs = np.linalg.eigh(m)
    if vals.min() < -1e-8:
        return None
    return vecs @ np.diag(np.sqrt(np.maximum(vals, 0)))


def correlation_matrix(legs, correlation):
    """Build the latent matrix from adapter.correlation(a, b) (chosen-side latent correlation). Legs from different
    match_keys get 0; a same-match pair with no validated estimate (None) -> None (the slip cannot be rated)."""
    n = len(legs)
    m = np.eye(n)
    for i in range(n):
        for j in range(i):
            if legs[i].get("match_key") != legs[j].get("match_key"):
                continue
            rho = correlation(legs[i], legs[j]) if correlation is not None else None
            if rho is None:
                return None
            m[i, j] = m[j, i] = float(rho)
    return m


def simulate(probs, tiers, corr=None, draws=COPULA_DRAWS, seed=SEED, chunk=500_000):
    """Gaussian copula (identity = independent Monte Carlo): leg i hits when z_i < Phi^-1(p_i), z ~ N(0, corr), seeded.
    -> {"ev", "ev_low", "ev_high" (95% interval of the simulation), "p_all_hit" (+ interval), "hit_dist", "draws",
    "seed"} or {"available": False, "reason"} when the correlations do not form a valid joint model."""
    p = _probs(probs)
    n = len(p)
    pay = np.array(_payout_vector(tiers, n))
    factor = _corr_matrix(n, corr)
    if factor is None:
        return {"available": False, "reason": "correlation estimates do not form a valid joint model"}
    nd = NormalDist()
    cut = np.array([-np.inf if x <= 0 else np.inf if x >= 1 else nd.inv_cdf(x) for x in p])
    rng = np.random.default_rng(seed)
    counts = np.zeros(n + 1)
    s1 = s2 = 0.0
    left = int(draws)
    if left < 2:
        raise ValueError("at least 2 draws")
    while left > 0:
        m = min(chunk, left)
        z = rng.standard_normal((m, n)) @ factor.T
        hits = (z < cut).sum(axis=1)
        counts += np.bincount(hits, minlength=n + 1)
        r = pay[hits]
        s1 += r.sum()
        s2 += (r * r).sum()
        left -= m
    mean = s1 / draws
    se = math.sqrt(max(s2 / draws - mean * mean, 0.0) / (draws - 1))
    dist = (counts / draws).tolist()
    pa = dist[n]
    pse = math.sqrt(pa * (1 - pa) / draws)
    return {"available": True, "ev": mean - 1.0, "ev_low": mean - 1.0 - Z95 * se, "ev_high": mean - 1.0 + Z95 * se,
            "p_all_hit": pa, "p_all_hit_low": max(0.0, pa - Z95 * pse), "p_all_hit_high": min(1.0, pa + Z95 * pse),
            "hit_dist": dist, "draws": int(draws), "seed": seed,
            "basis": f"Gaussian copula; {int(draws)} draws; seed {seed}"}


def kelly_fraction(returns):
    """Full-Kelly bankroll fraction on a whole payout distribution [(prob, gross multiple)]: maximises E[log wealth]."""
    return _kelly([(float(p), float(m)) for p, m in returns if p > 0])


def stake(returns, mode="kelly", fraction=KELLY_FRACTION, bankroll=None, unit=None, flat_units=1.0, cap_units=None):
    """A member's DISPLAY stake for a slip (spec: Kelly units; Outlier: % or $ from an entered bankroll). It never changes
    a grader stake. mode 'kelly' = fraction x full Kelly (1/4 default, 1/2 etc. by `fraction`); 'flat' = flat_units.
    Units: 1 unit = `unit` dollars, else UNIT_SHARE of the bankroll; without a bankroll a unit is 1% of an unnamed one.
    cap_units = the gate cap (gates.evaluate(...).cap) - the stake never shows more.
    -> {"full_kelly", "fraction", "units", "amount", "capped"}."""
    full = kelly_fraction(returns)
    if bankroll is not None and not (isinstance(bankroll, (int, float)) and bankroll > 0):
        raise ValueError("bankroll must be a positive amount")
    unit_dollars = float(unit) if unit else float(bankroll) * UNIT_SHARE if bankroll else None
    if mode == "flat":
        units = float(flat_units) if full > 0 else 0.0
        frac = None
    elif mode == "kelly":
        if not 0 < fraction <= 1:
            raise ValueError("Kelly fraction must be in (0, 1]")
        frac = full * fraction
        units = frac * float(bankroll) / unit_dollars if bankroll else frac / UNIT_SHARE
    else:
        raise ValueError("mode must be kelly or flat")
    capped = cap_units is not None and units > cap_units
    if capped:
        units = float(cap_units)
    return {"full_kelly": full, "fraction": frac, "units": units, "capped": capped,
            "amount": None if unit_dollars is None else units * unit_dollars}
