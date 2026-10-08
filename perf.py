"""Performance model (2026-10-08 rebuild) - ONE implementation shared by training, replay and live grading.

projection = player kills per round (time-weighted, half-life HL days, shrunk toward the pro average by K0 rounds,
             tonight's LAN/online maps blended in by their own sample KE)
             x opponent effect (kills per round his opponents conceded per enemy player, shrunk by KO, vs the average)
             x expected rounds (21.74 per map, measured on the training period)
headshots  = projected kills x the player's shrunk headshots-per-kill (KHS).
Map 1 = one map; Maps 1-2 = two maps. Spread = C * sqrt(projection), C measured on training-period residuals.

Tuned on Sep 2025 - Jun 2026 (HLTV player-map history); team strength (Elo) did not improve projections and is not used.
Inputs are plain lists so the same code runs on the HLTV export (training/replay) and the live HLTV database:
  player_maps: [(day_ordinal, kills, rounds, headshots_or_None, is_lan, hs_ok)]  - maps BEFORE the match day only
  opp_maps:    [(day_ordinal, kills_conceded_by_the_5_opponents, rounds)]
Missing data stays missing: no history -> the shrinkage target alone, and the result says so (data_quality)."""
import math

VERSION = "perf-1"
PARAMS = {"hl": 120.0, "k0": 300.0, "ko": 300.0, "ke": 800.0, "base_r": 21.74132969862184, "k_hs": 150.0,
          "pop": 0.66442, "pop_hs": 0.5106}
SPREAD = {("kills", "12"): 1.446, ("kills", "1"): 1.43, ("hs", "12"): 1.262, ("hs", "1"): 1.235}   # TRAIN residuals via this module
MAPS = {"1": 1, "12": 2}


def _wsum(rows, day, hl, f):
    tot_a = tot_b = 0.0
    for r in rows:
        if r[0] >= day:
            continue                                   # strictly earlier days only
        w = math.exp(-(day - r[0]) / hl)
        a, b = f(r)
        tot_a += w * a; tot_b += w * b
    return tot_a, tot_b


def project(player_maps, opp_maps, lan, day, scope="12", prop="kills", params=PARAMS):
    """Projection for one player in one match (scope "1" or "12"). lan: True / False / None (unknown -> no blend)."""
    p = params
    if scope not in MAPS:
        raise ValueError(f"perf.project: scope {scope!r} not modelled")
    k, r = _wsum(player_maps, day, p["hl"], lambda x: (x[1], x[2]))
    rate_all = (k + p["k0"] * p["pop"]) / (r + p["k0"])
    rate = rate_all
    env_rounds = 0.0
    if lan is not None:
        ke_, re_ = _wsum([x for x in player_maps if bool(x[4]) == bool(lan)], day, p["hl"], lambda x: (x[1], x[2]))
        rate = (ke_ + p["ke"] * rate_all) / (re_ + p["ke"])
        env_rounds = re_
    ck, cr = _wsum(opp_maps, day, p["hl"], lambda x: (x[1], x[2]))
    opp = ((ck + p["ko"] * p["pop"] * 5) / (cr * 5 + p["ko"] * 5)) / p["pop"]
    hk, hkk = _wsum([x for x in player_maps if x[3] is not None and x[5]], day, p["hl"], lambda x: (x[3], x[1]))
    hsr = (hk + p["k_hs"] * p["pop_hs"]) / (hkk + p["k_hs"])
    lam_k = rate * opp * p["base_r"] * MAPS[scope]
    lam = lam_k * hsr if prop == "hs" else lam_k
    return {"version": VERSION, "lambda": lam, "lambda_kills": lam_k, "rate": rate, "rate_all_envs": rate_all, "opp_factor": opp,
            "hs_per_kill": hsr, "rounds_per_map": p["base_r"], "maps": MAPS[scope], "sd": SPREAD[(prop, scope)] * math.sqrt(max(lam, 1.0)),
            "spread_c": SPREAD[(prop, scope)], "history_rounds_weighted": r, "env_rounds_weighted": env_rounds, "opp_rounds_weighted": cr,
            "data_quality": "no history - pro average only" if r <= 0 else ("thin history" if r < 150 else "ok")}


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def probs(lam, sd, line):
    """Unconditional (over, under, push) with the line's real cut (x.5: at the line; whole: line +-0.5, push between)."""
    up, dn = (line, line) if abs(line - round(line)) > 0.25 else (line + 0.5, line - 0.5)
    po, pu = 1 - _phi((up - lam) / sd), _phi((dn - lam) / sd)
    return po, pu, max(0.0, 1 - po - pu)


# ---- perf-2 CANDIDATE (frozen 2026-10-08, comparison only until confirmed on matches from Oct 9 on):
# headshots | kills ~ BetaBinomial(kills, mean = his shrunk HS/kill, concentration HS_KAPPA), kills ~ the perf-1 kills
# distribution (discretised normal) - HS probabilities average over every possible kill total; HS <= kills by construction.
# HS_KAPPA chosen on TRAIN only (mean log-likelihood of the actual HS total, 1,500 series): 100.
HS_KAPPA = 100.0


def kills_pmf(lam_kills, scope):
    """Kills as a whole number: the perf-1 normal (mean lam, sd C*sqrt(lam)) discretised to integers and CONDITIONED on
    kills >= 0 (the mass below zero is removed by renormalising, not piled onto 0). Upper tail kept to lam + 8 sd
    (beyond it < 1e-9 remains). Sums to exactly 1."""
    c = SPREAD[("kills", scope)]
    sd = c * math.sqrt(max(lam_kills, 1.0))
    raw = {}
    for k in range(0, int(lam_kills + 8 * sd) + 2):
        pk = _phi((k + 0.5 - lam_kills) / sd) - _phi((k - 0.5 - lam_kills) / sd)
        if pk > 0:
            raw[k] = pk
    tot = sum(raw.values())
    return {k: v / tot for k, v in raw.items()}


def hs_pmf(lam_kills, scope, hs_per_kill, kappa=HS_KAPPA):
    """Headshots: for each kill total k (kills_pmf), HS | k ~ BetaBinomial(k, a, b) with mean hs_per_kill and
    concentration kappa - so HS <= kills always and every kill total is averaged over. Sums to exactly 1."""
    from math import lgamma, exp
    a, b = hs_per_kill * kappa, (1 - hs_per_kill) * kappa
    out = {}
    for k, pk in kills_pmf(lam_kills, scope).items():
        base = lgamma(a + b) - lgamma(a) - lgamma(b) - lgamma(k + a + b)
        terms = [exp(lgamma(k + 1) - lgamma(y + 1) - lgamma(k - y + 1) + lgamma(y + a) + lgamma(k - y + b) + base) for y in range(k + 1)]
        tk = sum(terms)                                  # 1 up to rounding - renormalised so each k contributes exactly pk
        for y, t in enumerate(terms):
            out[y] = out.get(y, 0.0) + pk * t / tk
    return out


def hs_probs(lam_kills, scope, hs_per_kill, line, kappa=HS_KAPPA):
    """(over, under, push) for a headshot line under the perf-2 candidate distribution."""
    pmf = hs_pmf(lam_kills, scope, hs_per_kill, kappa)
    o = sum(v for y, v in pmf.items() if y > line); u = sum(v for y, v in pmf.items() if y < line)
    return o, u, sum(v for y, v in pmf.items() if y == line)            # exactly 0 on x.5 lines (no integer equals it)
