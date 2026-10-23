"""vm-1 - map-informed prop distribution (research/map_veto/props/REGISTER.md). SHADOW / RESEARCH ONLY: never changes a
grade, chance or stake; the live grader and veto-1 are untouched.

For one forecast: the veto-1 distribution over legal map sequences x a per-map match-script model (Beta-binomial rounds
under CS2 MR12 + overtime rules, team strength from pre-match Elo and veto-1 map strength) -> the scope's total-rounds
distribution (Maps 1-3 in a BO3 conditioned on the series reaching map 3) -> the player's kill / HS distribution as a
mixture with the LIVE kill rate per round (no per-map player adjustment) -> P(Over | no push) at the offered line.

Pure Python (math only), deterministic, bounded: per call <= 7 per-map score distributions (cached, bounded LRU), <= 210
ordered sequences, one rounds pmf of <= ~150 points. No I/O. The kills / HS arithmetic is rm-1's (cands_shadow)."""
import collections
import hashlib
import json
import math
import os
import sys
import threading


ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import cands_shadow as rm  # noqa: E402

VERSION_BASE = "vm-1"
HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS_PATH = os.path.join(HERE, "vm1_params.json")
MAPS = {"1": 1, "12": 2, "123": 3}
SOURCES = ("perf-2", "perf-2-bo3", "perf-cm2")
CAP = rm.CAP
MAX_OT = 8                 # overtime periods enumerated (mass beyond is ~1e-7 and renormalised away)
SEQ_MASS_MIN = 0.98        # the veto prediction must list sequences covering >= 98% (ask veto_model.predict(top=250))


# ------------------------------------------------------------------ params
def load_params(path=PARAMS_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def params_sha(params):
    core = {k: params[k] for k in sorted(params) if k not in ("meta",)}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:10]


def version(params):
    return f"{VERSION_BASE}-{params_sha(params)}"


_P = {"p": None}


def _params(params):
    if params is not None:
        return params
    if _P["p"] is None:
        _P["p"] = load_params()
    return _P["p"]


# ------------------------------------------------------------------ score enumeration (MR12 + MR3 overtime)
def _lc(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def _score_paths(max_ot=MAX_OT):
    """[(winner rounds, loser rounds, log N, is_ot)]: N = legal round orders that end at that final score."""
    out = [(13, x, _lc(12 + x, x), False) for x in range(12)]
    base = _lc(24, 12)
    for k in range(max_ot):
        for j in range(3):
            out.append((16 + 3 * k, 12 + 3 * k + j, base + k * math.log(20.0) + _lc(3 + j, j), True))
    return out


SCORES = _score_paths()
VALID = {(w, l) for w, l, _, _ in SCORES}


def _lbeta(a, b):
    return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)


def score_logprob(a, b, mu, kappa):
    """log P(final score A a - B b) under pi ~ Beta(mu kappa, (1 - mu) kappa), iid rounds; None if not a legal score."""
    w, l = max(a, b), min(a, b)
    hit = [s for s in SCORES if s[0] == w and s[1] == l]
    if not hit or a == b:
        return None
    al, be = mu * kappa, (1 - mu) * kappa
    return hit[0][2] + _lbeta(al + a, be + b) - _lbeta(al, be)


_MAP_CACHE = collections.OrderedDict()
_MAP_LOCK = threading.Lock()
_MAP_MAX = 4096


def map_joint(mu, kappa, tau=1.0):
    """{total rounds: (P(A wins with that total), P(B wins with that total), is_ot)} for one map. Sums to 1."""
    key = (round(mu, 7), round(kappa, 5), round(tau, 5))
    with _MAP_LOCK:
        hit = _MAP_CACHE.get(key)
        if hit is not None:
            _MAP_CACHE.move_to_end(key)
            return hit
    al, be = mu * kappa, (1 - mu) * kappa
    lb0 = _lbeta(al, be)
    reg, ot = {}, {}
    for w, l, ln, is_ot in SCORES:
        pa = math.exp(ln + _lbeta(al + w, be + l) - lb0)
        pb = math.exp(ln + _lbeta(al + l, be + w) - lb0)
        d = ot if is_ot else reg
        a0, b0 = d.get(w + l, (0.0, 0.0))
        d[w + l] = (a0 + pa, b0 + pb)
    sr = sum(a + b for a, b in reg.values())
    so = sum(a + b for a, b in ot.values())
    q = so / (sr + so)
    q2 = min(0.5, tau * q) if tau != 1.0 else q
    out = {}
    for r, (a, b) in reg.items():
        out[r] = (a / sr * (1 - q2), b / sr * (1 - q2), False)
    for r, (a, b) in ot.items():
        out[r] = (a / so * q2, b / so * q2, True)
    with _MAP_LOCK:
        _MAP_CACHE[key] = out
        while len(_MAP_CACHE) > _MAP_MAX:
            _MAP_CACHE.popitem(last=False)
    return out


# ------------------------------------------------------------------ match context / per-map win model
def match_context(table, team_a, team_b, pool, before, lineup_a=None, lineup_b=None, vparams=None):
    """Pre-match strength inputs for vm-1 from a veto_model.VetoTable cut strictly before `before` (same rule as veto-1):
    {team_a, team_b, elo_a, elo_b, strength_a{map}, strength_b{map}}. Pure (reads the table only)."""
    import veto_model as vmod
    vparams = vparams or vmod.PARAMS
    bday = vmod._day(before)
    if table.cutoff is not None and bday < table.cutoff:
        raise ValueError("table holds matches on/after the prediction date (leakage)")
    pool_k = sorted({vmod.map_key(m) for m in pool})
    la = tuple(sorted(lineup_a)) if lineup_a else (tuple(sorted(table.last_lineup[team_a][1])) if team_a in table.last_lineup else None)
    lb = tuple(sorted(lineup_b)) if lineup_b else (tuple(sorted(table.last_lineup[team_b][1])) if team_b in table.last_lineup else None)
    sa = table.team_stats(team_a, la, bday, pool_k, vparams)
    sb = table.team_stats(team_b, lb, bday, pool_k, vparams)
    return {"team_a": team_a, "team_b": team_b, "elo_a": float(table.elo[team_a]), "elo_b": float(table.elo[team_b]),
            "strength_a": {m: float(v) for m, v in sa["strength"].items()},
            "strength_b": {m: float(v) for m, v in sb["strength"].items()}, "n_a": sa["n"], "n_b": sb["n"]}


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def map_mu(ctx, m, P, use_maps=True):
    """Team A's mean round-win probability on map m."""
    ea = 1.0 / (1.0 + 10 ** ((ctx["elo_b"] - ctx["elo_a"]) / 400.0))
    d = (ctx["strength_a"].get(m, 0.0) - ctx["strength_b"].get(m, 0.0)) if use_maps else 0.0
    z = P["c0"] * _logit(ea) + (P["c1"] * d if use_maps else 0.0)
    return 1.0 / (1.0 + math.exp(-z))


def map_kappa(m, P, use_maps=True):
    return P["kappa_map"].get(m, P["kappa"]) if use_maps else P["kappa"]


def per_map(ctx, maps, P, use_maps=True):
    return {m: map_joint(map_mu(ctx, m, P, use_maps), map_kappa(m, P, use_maps), P.get("tau", 1.0)) for m in maps}


# ------------------------------------------------------------------ series script -> scope rounds pmf
def _marg(j):
    return {r: a + b for r, (a, b, _) in j.items()}


def _conv(x, y):
    out = collections.defaultdict(float)
    for a, pa in x.items():
        for b, pb in y.items():
            out[a + b] += pa * pb
    return out


def _side(j, s, reg_only=False):
    return {r: (v[0] if s == "a" else v[1]) for r, v in j.items() if not (reg_only and v[2])}


def scope_rounds(seqs, fmt, scope, pm):
    """Exact mixture over map sequences: ({rounds: prob}, info). seqs = [(played order tuple, weight)] (weights sum 1).
    BO3 Maps 1-3 = conditioned on the series reaching map 3 (Map 1 / Map 2 split)."""
    m = MAPS[scope]
    info = {}
    if m == 1:
        W = collections.defaultdict(float)
        for s, w in seqs:
            W[s[0]] += w
        out = collections.defaultdict(float)
        ot = 0.0
        for a, w in W.items():
            for r, p in _marg(pm[a]).items():
                out[r] += w * p
            ot += w * sum(v[0] + v[1] for v in pm[a].values() if v[2])
        info["ot_prob"] = ot
        return dict(out), info
    if m == 2 or (m == 3 and fmt == "bo5"):
        W = collections.defaultdict(float)
        for s, w in seqs:
            W[tuple(s[:m])] += w
        out = collections.defaultdict(float)
        noot = 0.0
        for key, w in W.items():
            cur = {0: 1.0}
            nz = 1.0
            for a in key:
                cur = _conv(cur, _marg(pm[a]))
                nz *= sum(v[0] + v[1] for v in pm[a].values() if not v[2])
            for r, p in cur.items():
                out[r] += w * p
            noot += w * nz
        info["ot_prob"] = 1 - noot
        return dict(out), info
    # BO3 Maps 1-3, conditioned on a split after two maps
    W3 = collections.defaultdict(float)
    for s, w in seqs:
        W3[(s[0], s[1], s[2])] += w
    G = collections.defaultdict(lambda: collections.defaultdict(float))
    split_cache, noot_split = {}, {}
    for (a, b, c), w in W3.items():
        if (a, b) not in split_cache:
            sp = collections.defaultdict(float)
            for s1, s2 in (("a", "b"), ("b", "a")):
                for r, p in _conv(_side(pm[a], s1), _side(pm[b], s2)).items():
                    sp[r] += p
            split_cache[(a, b)] = sp
            ra = {x: sum(_side(pm[a], x, True).values()) for x in "ab"}
            rb = {x: sum(_side(pm[b], x, True).values()) for x in "ab"}
            noot_split[(a, b)] = ra["a"] * rb["b"] + ra["b"] * rb["a"]
        for r, p in split_cache[(a, b)].items():
            G[c][r] += w * p
    out = collections.defaultdict(float)
    reach = 0.0
    noot = 0.0
    for c, g in G.items():
        for r, p in _conv(g, _marg(pm[c])).items():
            out[r] += p
    for (a, b, c), w in W3.items():
        reach += w * sum(split_cache[(a, b)].values())
        noot += w * noot_split[(a, b)] * sum(v[0] + v[1] for v in pm[c].values() if not v[2])
    tot = sum(out.values())
    info["p_reach_map3"] = reach
    info["ot_prob"] = 1 - noot / reach if reach > 0 else None
    return {r: p / tot for r, p in out.items()}, info


def _prune(pmf, tol=1e-10):
    pmf = {r: p for r, p in pmf.items() if p > tol}
    t = sum(pmf.values())
    return sorted((r, p / t) for r, p in pmf.items())


# ------------------------------------------------------------------ the distribution of one forecast
def sequences_from_prediction(pred, retro_order=None):
    """[(played order, weight)] from a veto_model.predict output (its 'sequences' list; ask for top=250 so the list is
    complete) or, RETROSPECTIVE arm only, the confirmed played order with weight 1. Returns (seqs, covered mass)."""
    if retro_order is not None:
        return [(tuple(retro_order), 1.0)], 1.0
    seqs = [(tuple(s), float(p)) for s, p in pred.get("sequences") or [] if p > 0]
    mass = sum(p for _, p in seqs)
    if mass <= 0:
        return [], 0.0
    return [(s, p / mass) for s, p in seqs], mass


def _rho(scope, src, P):
    if scope == "1":
        return P["rho"]["1_p2"]
    if scope == "12":
        return P["rho"]["12_cm2" if src == "perf-cm2" else "12_p2"]
    return P["rho"]["123_p2"]


def shadow_entry(forecast_inputs, veto_prediction, match_ctx, params=None, retro_order=None, use_maps=True,
                 live_p_more=None):
    """vm-1 shadow record of one forecast. forecast_inputs = the grader's saved inputs (prop, line, scope, bo, perf{...});
    veto_prediction = veto_model.predict(...) for the match (pre-veto, top=250); match_ctx = match_context(...).
    retro_order = RETROSPECTIVE confirmed played order (never for live); use_maps=False = the no-map control C0.
    Never raises (errors come back as eligible None + error)."""
    try:
        return _entry(forecast_inputs, veto_prediction, match_ctx, _params(params), retro_order, use_maps, live_p_more)
    except Exception as e:  # noqa: BLE001 - a shadow never stops the caller
        P = params or _P["p"] or {}
        return {"version": version(P) if P else VERSION_BASE, "eligible": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}


def _entry(gi, pred, ctx, P, retro_order, use_maps, live_p_more):
    out = {"version": version(P), "params_sha": params_sha(P), "arm": "RETROSPECTIVE" if retro_order is not None else
           ("vm-1" if use_maps else "C0-no-map"), "live_eligible": retro_order is None and use_maps}
    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
    prop, scope = gi.get("prop"), str(gi.get("scope") or "12")
    src = str(pf.get("version") or "")
    out.update(prop=prop, scope=scope, source=src)
    if prop not in ("kills", "hs") or scope not in MAPS:
        return dict(out, eligible=False, reason="not a vm-1 cell")
    if src not in SOURCES or pf.get("lambda") is None or not pf.get("sd"):
        return dict(out, eligible=False, reason="live projection not perf-2 / perf-2-bo3 / perf-cm2")
    try:
        line = float(gi["line"])
    except (KeyError, TypeError, ValueError):
        return dict(out, eligible=False, reason="no line")
    if not math.isfinite(line) or line < 0 or line * 2 != int(line * 2):
        return dict(out, eligible=False, reason="line is not a whole or half number")
    if pred is None or ctx is None:
        return dict(out, eligible=False, reason="no veto-1 prediction for the match")
    fmt = str(pred.get("format") or "").lower()
    if fmt not in ("bo1", "bo3", "bo5"):
        return dict(out, eligible=False, reason="unknown format")
    if MAPS[scope] > {"bo1": 1, "bo3": 3, "bo5": 5}[fmt]:
        return dict(out, eligible=False, reason="scope longer than the format")
    seqs, mass = sequences_from_prediction(pred, retro_order)
    if retro_order is None and mass < SEQ_MASS_MIN:
        return dict(out, eligible=False, reason=f"veto prediction lists only {mass:.3f} of the sequence mass")
    if not seqs:
        return dict(out, eligible=False, reason="no map sequences")
    maps = sorted({m for s, _ in seqs for m in s[:MAPS[scope]]})
    pm = per_map(ctx, maps, P, use_maps)
    rpmf, info = scope_rounds(seqs, fmt, scope, pm)
    sup = _prune(rpmf)
    m = MAPS[scope]
    er = sum(r * w for r, w in sup)
    rho = _rho(scope, src, P)
    if prop == "kills":
        lam = float(pf["lambda"])
        c = float(pf["sd"]) / math.sqrt(max(lam, 1.0))
    else:
        import perf
        lam = float(pf["lambda_kills"])
        c = perf.kills_c(pf, scope)
    rate = lam / (m * P["rbar"])
    comps = []
    for r, w in sup:
        mu = rate * r
        comps.append((w, mu, rho * c * math.sqrt(max(mu, 1.0))))
    live = [(1.0, lam, c * math.sqrt(max(lam, 1.0)))]
    if prop == "kills":
        o, u, pu = rm.kills_probs(line, comps)
        lo, lu, _ = rm.kills_probs(line, live)
        proj, proj_live = rate * er, lam
    else:
        h = float(pf["hs_per_kill"])
        o, u, pu = rm.hs_probs(rm.kills_pmf(comps), h, line)
        lo, lu, _ = rm.hs_probs(rm.kills_pmf(live), h, line)
        proj, proj_live = rate * er * h, lam * h
    pm_raw = o / (o + u) if o + u > 0 else 0.5
    p_over = min(max(pm_raw, 1 - CAP), CAP) if prop == "kills" else pm_raw
    lr = lo / (lo + lu) if lo + lu > 0 else 0.5
    live_replay = min(max(lr, 1 - CAP), CAP) if prop == "kills" else lr
    mu_slot = {mp: round(map_mu(ctx, mp, P, use_maps), 6) for mp in maps}
    res = dict(out, eligible=True, line=line, format=fmt, p_over=round(p_over, 6), p_over_uncapped=round(pm_raw, 6),
               raw={"over": round(o, 6), "under": round(u, 6), "push": round(pu, 6)},
               lam=round(proj, 4), lam_live=round(proj_live, 4), rounds_mean=round(er, 4),
               rounds_live=round(m * P["rbar"], 4), ot_prob=None if info.get("ot_prob") is None else round(info["ot_prob"], 6),
               p_reach_map3=None if info.get("p_reach_map3") is None else round(info["p_reach_map3"], 6),
               live_replay=round(live_replay, 6), seq_mass=round(mass, 6), n_sequences=len(seqs),
               map_round_win_a=mu_slot, veto_version=pred.get("version") if pred else None,
               side="Over" if p_over > 0.5 else ("Under" if p_over < 0.5 else None))
    if live_p_more is not None:
        res["live_p_more"] = live_p_more
        res["delta_p"] = round(p_over - float(live_p_more), 6)
    return res
