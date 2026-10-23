"""Corrections 2026-10-23, item 5: power of rm-1's registered forward rule on real P1 lines.

Registered rule (CANDIDATES.md s4 / learning.CANDS_RULE on shadow/cs2-cands): at a look of 300 / 600 / 1,000 distinct forward
matches with >= 1 eligible logged unit, the paired log-loss gain (live log loss - rm-1 log loss, + = rm-1 better) must have a
95% match-grouped interval entirely above 0 (plus Brier point >= 0, side calibration not worse, coverage identical - NOT
modelled here, so every probability below is an UPPER bound on the chance of passing).

Per-match variance comes from the development real lines (cands/p1_cands.csv.gz: logged live p_over vs p_rm1, F6 fit, 2,692
units / 155 matches, start < 2026-10-22). Two populations:
  ALL   every P1 unit (development point +0.0004) - the figure the owner quoted; hypothetical, since rm-1 is only logged for
        its passing cells;
  CELLS the registered eligible cells only (kills Map 1, kills Maps 1-3, HS Map 1; 297 units / 21 matches; point +0.0029).
Estimator = ratio mean sum_m S_m / sum_m n_m. Linearised: SE(M) = sd_m(S_m - theta n_m) / (nbar sqrt(M)).
Power(delta, M) = Phi(delta / SE(M) - z), z = 1.96 (registered 95%); 2.394 (98.33%, the 3-look Bonferroni of the general
REVIEW_RULE) shown as a sensitivity. A Monte Carlo check (matches resampled from development with the effect shifted to delta;
percentile bootstrap B = 500 per simulated look; 400 simulations) validates the normal approximation.

usage: python power_rm1.py <scratchpad>/cs2acc -> results/power_rm1.json, results/power_rm1.md
"""
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 20261023
LOOKS = (300, 600, 1000)
EFFECTS = (-0.002, 0.0, 0.0004, 0.001, 0.0029, 0.005, 0.01, 0.02)
CELLS = {("kills", "1"), ("kills", "123"), ("hs", "1")}
EPS = 1e-12


def ll(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def per_match(d):
    g = d.groupby("match").agg(S=("gain", "sum"), n=("gain", "size"))
    return g.S.values, g.n.values.astype(float)


def boot_ci(S, n, rng, B=2000):
    k = len(S)
    W = rng.multinomial(k, np.full(k, 1.0 / k), size=B).astype(float)
    bs = (W @ S) / (W @ n)
    return [float(x) for x in np.percentile(bs, [2.5, 97.5])]


def sigma_m(S, n):
    th = S.sum() / n.sum()
    return float(np.std(S - th * n, ddof=1) / n.mean())


def power(delta, sig, M, z):
    return float(norm.cdf(delta / (sig / np.sqrt(M)) - z))


def mc(S, n, delta, M, rng, sims=400, B=500):
    """simulated pass rate of 'percentile 95% lower bound > 0' at M matches, true effect delta."""
    th = S.sum() / n.sum()
    S2 = S + (delta - th) * n                      # shift every unit's gain so the population ratio mean = delta
    k, hits = len(S), 0
    for _ in range(sims):
        idx = rng.integers(0, k, M)
        s, m = S2[idx], n[idx]
        W = rng.multinomial(M, np.full(M, 1.0 / M), size=B).astype(float)
        lo = np.percentile((W @ s) / (W @ m), 2.5)
        hits += lo > 0
    return hits / sims


def main(S_dir):
    d = pd.read_csv(os.path.join(S_dir, "cands", "p1_cands.csv.gz"), usecols=["match", "prop", "scope", "p_over", "p_rm1", "over"])
    d["scope"] = d.scope.astype(str)
    d["gain"] = ll(d.p_over.values, d.over.values) - ll(d.p_rm1.values, d.over.values)
    rng = np.random.default_rng(SEED)
    res = {"seed": SEED, "looks": LOOKS, "effects": EFFECTS, "pops": {}}
    for name, sel in (("ALL", np.ones(len(d), bool)), ("CELLS", np.array([(a, b) in CELLS for a, b in zip(d.prop, d.scope)]))):
        x = d[sel]
        S, n = per_match(x)
        th = float(S.sum() / n.sum())
        sig = sigma_m(S, n)
        # uncertainty of sigma itself (matches resampled)
        sigs = []
        for _ in range(1000):
            i = rng.integers(0, len(S), len(S))
            sigs.append(sigma_m(S[i], n[i]))
        r = {"units": int(len(x)), "matches": int(len(S)), "units_per_match": round(float(n.mean()), 2),
             "point": round(th, 5), "ci95": [round(v, 5) for v in boot_ci(S, n, rng)],
             "sigma_match": round(sig, 5), "sigma_match_90": [round(float(v), 5) for v in np.percentile(sigs, [5, 95])],
             "se_at_look": {M: round(sig / np.sqrt(M), 5) for M in LOOKS},
             "mde80_95": {M: round((1.96 + 0.8416) * sig / np.sqrt(M), 5) for M in LOOKS},
             "matches_for_80pct_at_point": (int(np.ceil(((1.96 + 0.8416) * sig / th) ** 2)) if th > 0 else None),
             "power_95": {str(e): {M: round(power(e, sig, M, 1.96), 3) for M in LOOKS} for e in EFFECTS},
             "power_9833": {str(e): {M: round(power(e, sig, M, 2.394), 3) for M in LOOKS} for e in EFFECTS},
             "power_95_sigma_range_at_point": {M: [round(power(th, s_, M, 1.96), 3) for s_ in np.percentile(sigs, [95, 5])] for M in LOOKS},
             "mc_check_95": {}}
        for e in (th, 0.0029, 0.005):
            r["mc_check_95"][str(round(e, 5))] = {M: {"mc": round(mc(S, n, e, M, rng), 3), "normal": round(power(e, sig, M, 1.96), 3)} for M in LOOKS}
        res["pops"][name] = r
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    json.dump(res, open(os.path.join(HERE, "results", "power_rm1.json"), "w"), indent=1)

    L = ["# rm-1 forward-rule power (Corrections 2026-10-23, item 5)", "",
         "Generated by `corrections/power_rm1.py` from `cands/p1_cands.csv.gz` (development real lines, F6 fit). Gain = live log "
         "loss - rm-1 log loss per unit (+ = rm-1 better). P(pass) = probability the registered primary test (95% match-grouped "
         "interval > 0) passes at the look; Brier / calibration guards are not modelled, so these are UPPER bounds.", ""]
    for name, r in res["pops"].items():
        L += [f"## {name}: {r['units']} units / {r['matches']} matches ({r['units_per_match']} units per match)", "",
              f"- development gain {r['point']:+.4f} [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] (match-grouped bootstrap 95%, B = 2,000)",
              f"- per-match sd of the linearised gain (sigma_m): {r['sigma_match']:.4f} (90% range {r['sigma_match_90'][0]:.4f} - {r['sigma_match_90'][1]:.4f})",
              "- standard error at the looks: " + ", ".join(f"{M} m {r['se_at_look'][M]:.4f}" for M in LOOKS),
              "- smallest true effect with 80% power (95% rule): " + ", ".join(f"{M} m {r['mde80_95'][M]:+.4f}" for M in LOOKS),
              f"- matches needed for 80% power if the true effect equals the development point: {r['matches_for_80pct_at_point']}", "",
              "| true effect | P(pass) 300 m | 600 m | 1,000 m | (98.33% rule: 300 / 600 / 1,000) |", "|---|---|---|---|---|"]
        for e in EFFECTS:
            p, q = r["power_95"][str(e)], r["power_9833"][str(e)]
            tag = " (dev point, ALL)" if e == 0.0004 else (" (dev point, CELLS)" if e == 0.0029 else "")
            L.append(f"| {e:+.4f}{tag} | {p[300]:.3f} | {p[600]:.3f} | {p[1000]:.3f} | {q[300]:.3f} / {q[600]:.3f} / {q[1000]:.3f} |")
        L += ["", "At the development point with sigma_m at its 90% range: " +
              ", ".join(f"{M} m {v[0]:.3f}-{v[1]:.3f}" for M, v in r["power_95_sigma_range_at_point"].items()),
              "", "Monte Carlo check (400 simulated looks, bootstrap B = 500): " +
              "; ".join(f"effect {e}: " + ", ".join(f"{M} m mc {v[M]['mc']:.2f} vs normal {v[M]['normal']:.2f}" for M in LOOKS)
                        for e, v in r["mc_check_95"].items()), ""]
    open(os.path.join(HERE, "results", "power_rm1.md"), "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main(sys.argv[1])
