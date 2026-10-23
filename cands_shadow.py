"""rm-1 / th-1 frozen shadows (CHALLENGERS.md; research/cs2-accuracy-2y CANDIDATES.md, REGISTER.md rows rm-1 / th-1).

Shadow only: logged beside the live grade in forecasts.shadow["cands"], never shown, never changes a grade, chance or stake.
Raw outcome chances are stored apart from the recommendation (side only). Pure Python (math only) on the forecast's own saved
performance-model input `perf`; the frozen constants below were fitted on HLTV matches <= 2026-10-21 (params_FINAL.json).

rm-1 (rounds-mixture-1), cells kills Map 1, kills Maps 1-3, HS Map 1: same mean as live; kills | R ~ normal(lam R / E[R],
  RHO c sqrt(max(mu_R, 1))) mixed over R = the sum of m iid per-map rounds draws from PI (league pmf incl. overtime); kills
  chance = the live line cuts on each normal, mixed, then the live kills cap; HS = the live beta-binomial (kappa 100) over the
  mixed integer kills pmf.
th-1 (thin-fallback-1), cell kills Map 1, perf-2 / perf-2-bo3 lines with history_rounds_weighted < 150: lam' = lam e^a,
  c' = c e^s by band D0 (< 50) / D1 (50-150); the live normal line cuts and cap on (lam', c')."""
import hashlib
import json
import math

FROZEN_AT = "2026-10-23T07:45:00+00:00"
CAP = 0.78
KAPPA = 100.0
# ---- frozen constants (params_FINAL.json: pi window 2025-10-22..2026-10-21, 26,156 maps; rho / th by ML on 2025-01-01..2026-10-21)
PI_Q = 0.11354947239639089
PI_REG = {13: 0.007677046493573709, 14: 0.023505563702234108, 15: 0.04136116622099543, 16: 0.056758388682825844, 17: 0.07487276804968515, 18: 0.08901923574570862, 19: 0.10174243077719314, 20: 0.11278357629604072, 21: 0.11627706374536358, 22: 0.12408349866298629, 23: 0.12084878806176141, 24: 0.131070473561632}
PI_OT = {28: 0.14511784511784512, 29: 0.2518518518518518, 30: 0.3047138047138047, 34: 0.040740740740740744, 35: 0.07306397306397307, 36: 0.09831649831649832, 40: 0.011784511784511785, 41: 0.018855218855218854, 42: 0.028956228956228958, 46: 0.003367003367003367, 47: 0.007407407407407408, 48: 0.00808080808080808, 52: 0.00101010101010101, 53: 0.00101010101010101, 54: 0.004377104377104377, 60: 0.00101010101010101, 66: 0.0003367003367003367}
RHO = {'1_p2': 0.8004368718076132, '12_p2': 0.8332019232762377, '12_cm2': 0.7992697484944598, '123_p2': 0.7695311796420888}
TH = {'p2_1_D0': {'a': -0.11977786157633913, 's': 0.185454990044707}, 'p2_1_D1': {'a': -0.02880165543899591, 's': 0.10090171354922624}, 'p2_12_D0': {'a': -0.07784539974645702, 's': 0.22776566505073337}, 'p2_12_D1': {'a': -0.00937968841866812, 's': 0.10757562033406481}, 'cm2_D0': {'a': 0.0068723734519543535, 's': 0.153875606745285}, 'cm2_D1': {'a': 0.010549162955111345, 's': 0.08829967859466914}, 'F': {'a': -0.12281776554372499, 's': 0.25850319718562254}, 'p2_123_D0': {'a': 0.03762907924124197, 's': 0.1356160372023103}, 'p2_123_D1': {'a': 0.03941060867299691, 's': 0.04296416144229534}}
RM1 = {"id": "rm-1", "family": "rounds-mixture-1", "version": "rm-1/2026-10-23", "cells": [["kills", "1"], ["kills", "123"], ["hs", "1"]]}
TH1 = {"id": "th-1", "family": "thin-fallback-1", "version": "th-1/2026-10-23", "cells": [["kills", "1"]]}
PERF_VERSIONS = ("perf-2", "perf-2-bo3")
MAPS = {"1": 1, "12": 2, "123": 3}
CONSTANTS_SHA = hashlib.sha256(json.dumps({"q": PI_Q, "reg": PI_REG, "ot": PI_OT, "rho": RHO, "th": TH}, sort_keys=True).encode()).hexdigest()


def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def pi_map():
    """Per-map rounds pmf: (1 - q) P_reg + q P_ot."""
    out = {}
    for r, p in PI_REG.items():
        out[int(r)] = out.get(int(r), 0.0) + (1 - PI_Q) * p
    for r, p in PI_OT.items():
        out[int(r)] = out.get(int(r), 0.0) + PI_Q * p
    return out


def pi_scope(m, tol=1e-9):
    """The per-map pmf convolved m times, mass < tol dropped and renormalised: [(rounds, weight)]."""
    base = pi_map()
    cur = dict(base)
    for _ in range(m - 1):
        nxt = {}
        for a, pa in cur.items():
            for b, pb in base.items():
                nxt[a + b] = nxt.get(a + b, 0.0) + pa * pb
        cur = nxt
    cur = {r: p for r, p in cur.items() if p > tol}
    t = sum(cur.values())
    return sorted((r, p / t) for r, p in cur.items())


_SCOPE = {}


def _components(lam, c, scope, rho):
    """[(weight, mu, sd)] of the rm-1 mixture for one projection."""
    if scope not in _SCOPE:
        _SCOPE[scope] = pi_scope(MAPS[scope])
    sup = _SCOPE[scope]
    er = sum(r * w for r, w in sup)
    out = []
    for r, w in sup:
        mu = lam * r / er
        out.append((w, mu, rho * c * math.sqrt(max(mu, 1.0))))
    return out


def _cuts(line):
    return (math.floor(line) + 0.5,) * 2 if line != int(line) else (line + 0.5, line - 0.5)   # grader._cuts


def kills_probs(line, comps):
    """(over, under, push) unconditional, mixed over the components (each the live continuous-normal cut rule)."""
    up, dn = _cuts(line)
    po = sum(w * (1 - _phi((up - mu) / sd)) for w, mu, sd in comps)
    pu = sum(w * _phi((dn - mu) / sd) for w, mu, sd in comps)
    return po, pu, max(0.0, 1 - po - pu)


def kills_pmf(comps):
    """Integer kills pmf: each component discretised and conditioned >= 0 (perf.kills_pmf), mixed. Sums to 1."""
    kmax = int(max(mu + 8 * sd for _, mu, sd in comps)) + 2
    out = [0.0] * (kmax + 1)
    for w, mu, sd in comps:
        raw = [_phi((k + 0.5 - mu) / sd) - _phi((k - 0.5 - mu) / sd) for k in range(kmax + 1)]
        t = sum(raw)
        for k, v in enumerate(raw):
            out[k] += w * v / t
    t = sum(out)
    return {k: v / t for k, v in enumerate(out) if v > 0}


def hs_probs(kpmf, hs_per_kill, line):
    """(over, under, push) for an HS line: HS | k ~ BetaBinomial(k, kappa 100, mean hs_per_kill), over the kills pmf
    (the perf.hs_pmf arithmetic on another kills pmf)."""
    h = min(max(hs_per_kill, 1e-6), 1 - 1e-6)
    a, b = h * KAPPA, (1 - h) * KAPPA
    lg = math.lgamma
    pmf = {}
    for k, pk in kpmf.items():
        base = lg(a + b) - lg(a) - lg(b) - lg(k + a + b)
        terms = [math.exp(lg(k + 1) - lg(y + 1) - lg(k - y + 1) + lg(y + a) + lg(k - y + b) + base) for y in range(k + 1)]
        tk = sum(terms)
        for y, t in enumerate(terms):
            pmf[y] = pmf.get(y, 0.0) + pk * t / tk
    o = sum(v for y, v in pmf.items() if y > line)
    u = sum(v for y, v in pmf.items() if y < line)
    return o, u, sum(v for y, v in pmf.items() if y == line)


def _scored(o, u, prop):
    pm = o / (o + u) if o + u > 0 else 0.5
    return pm, (min(max(pm, 1 - CAP), CAP) if prop == "kills" else pm)


def _base(spec, gi, g):
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    prop, scope = gi.get("prop"), str(gi.get("scope") or "12")
    out = {"id": spec["id"], "version": spec["version"], "constants_sha": CONSTANTS_SHA, "prop": prop, "scope": scope,
           "live_source": (g or {}).get("projection_source"), "live": {"p_more": (g or {}).get("p_more")}}
    ver = str(pf.get("version") or "")
    if [prop, scope] not in spec["cells"]:
        return out, None, f"not a {spec['id']} cell ({prop} {scope})"
    if ver not in PERF_VERSIONS or (g or {}).get("projection_source") != ver or pf.get("lambda") is None or not pf.get("sd"):
        return out, None, "live grade not on perf-2 / perf-2-bo3"
    if (g or {}).get("p_more") is None:
        return out, None, "live grade has no chance"
    return out, pf, None


def _entry(out, prop, line, comps, live_comps, extra):
    if prop == "kills":
        o, u, p = kills_probs(line, comps)
        lo, lu, _ = kills_probs(line, live_comps)
    else:
        o, u, p = hs_probs(kills_pmf(comps), extra["hs_per_kill"], line)
        lo, lu, _ = hs_probs(kills_pmf(live_comps), extra["hs_per_kill"], line)
    model, pm = _scored(o, u, prop)
    _, live_replay = _scored(lo, lu, prop)
    return dict(out, eligible=True, raw={"over": round(o, 6), "under": round(u, 6), "push": round(p, 6)},
                p_more_model=round(model, 6), p_more=round(pm, 6), live_replay=round(live_replay, 6),
                rec={"side": "Over" if pm > 0.5 else ("Under" if pm < 0.5 else None)},
                changed=abs(pm - float(out["live"]["p_more"])) > 1e-6, **{k: v for k, v in extra.items() if k != "hs_per_kill"})


def rm1_entry(gi, g):
    out, pf, why = _base(RM1, gi, g)
    if why:
        return dict(out, eligible=False, reason=why)
    prop, scope, line = out["prop"], out["scope"], float(gi["line"])
    if prop == "kills":
        lam = float(pf["lambda"]); c = float(pf["sd"]) / math.sqrt(max(lam, 1.0))
        extra = {"lambda": lam}
    else:
        import perf
        lam = float(pf["lambda_kills"]); c = perf.kills_c(pf, scope)
        extra = {"lambda_kills": lam, "hs_per_kill": float(pf["hs_per_kill"])}
    rho = RHO[f"{scope}_p2"]
    live = [(1.0, lam, c * math.sqrt(max(lam, 1.0)))]
    return _entry(dict(out, rho=rho), prop, line, _components(lam, c, scope, rho), live, extra)


def th1_entry(gi, g):
    out, pf, why = _base(TH1, gi, g)
    if why:
        return dict(out, eligible=False, reason=why)
    r = pf.get("history_rounds_weighted")
    if r is None:
        return dict(out, eligible=False, reason="no saved history depth")
    r = float(r)
    if r >= 150:
        return dict(out, eligible=False, reason="history >= 150 weighted rounds (not in the th-1 cohort)", history_rounds_weighted=r)
    band = "D0" if r < 50 else "D1"
    a, s = TH[f"p2_1_{band}"]["a"], TH[f"p2_1_{band}"]["s"]
    lam = float(pf["lambda"]); c = float(pf["sd"]) / math.sqrt(max(lam, 1.0))
    lam2, c2 = lam * math.exp(a), c * math.exp(s)
    comps = [(1.0, lam2, c2 * math.sqrt(max(lam2, 1.0)))]
    live = [(1.0, lam, c * math.sqrt(max(lam, 1.0)))]
    return _entry(dict(out, band=band, history_rounds_weighted=r, a=a, s=s), "kills", float(gi["line"]), comps, live, {"lambda": lam2})


def entries(gi, g):
    """{"rm1": ..., "th1": ...} for one live grade; an error in one never stops the other (or the pick log)."""
    res = {}
    for k, fn, spec in (("rm1", rm1_entry, RM1), ("th1", th1_entry, TH1)):
        try:
            res[k] = fn(gi, g)
        except Exception as e:     # noqa: BLE001 - a shadow never stops the pick log
            res[k] = {"id": spec["id"], "version": spec["version"], "eligible": None, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    return res
