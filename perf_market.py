"""perf_market.py - rr-1 (variant rr-ks): the market win probability in the projection, SHADOW ONLY (2026-10-13).

Frozen definition: FACTOR_AUDIT.md "round 6" (frozen 2026-10-13 17:51 UTC). Ported unchanged from the design's reference
implementation (scratchpad design/round-race/rr_live.py); frozen constants in perf_market_tables.json (rr_tables.json).
Nothing here changes a grade, chance, stake, label, board or card: picklog._factors logs shadow_entry() next to every
forecast (shadow key "market_rounds") so the candidate is evaluated forward on the same lines as the live model.

The market enters through the ROUND RACE, not a favourite multiplier: from the series price p and the format, a per-map
chance q (MODEL-DERIVED: BO1 q = p, BO3 p = q^2(3-2q), BO5 p = q^3(10-15q+6q^2), maps independent with equal q); a
calibrated MR12 round race gives the rounds the team expects to win W(q) and lose L(q); kills weigh rounds won a = 1.43
and lost b = 0.59 (relative to the perf-2 per-round rate). De-duplication: the perf-2 rate and opponent factor already
contain both teams' historical strength, so the kill-weighted rounds are divided by H = M(rho_team) M(1-rho_opp) / M(0.5)
(rho = each team's time-weighted historical round-win share) - only the difference between today's market and history
counts. No price, BO2 / unknown format, BO1 beyond Map 1, a BO3.gg-provisional projection or an unreadable round share:
no candidate number (the reason is logged) - never a stand-in."""
import json
import math
from pathlib import Path

VERSION = "rr-1"
VARIANT = "rr-ks"
TABLES = json.loads((Path(__file__).resolve().parent / "perf_market_tables.json").read_text(encoding="utf-8"))
MAPS = {"1": 1, "12": 2, "123": 3}


def q_from_series(p, bo):
    """Per-map chance from the series chance; maps i.i.d. with equal q (model-derived, never a map quote)."""
    if bo == 1:
        return p
    f = (lambda q: q * q * (3 - 2 * q)) if bo == 3 else (lambda q: q ** 3 * (10 - 15 * q + 6 * q * q))
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < p else (lo, mid)
    return (lo + hi) / 2


def _lookup(name, qf, t=TABLES):
    tab = t["tables_unnormalised"][name]
    step = t["q_step"]
    x = (min(max(qf, 0.5), 0.9995) - 0.5) / step
    i = min(int(x), len(tab) - 2)
    f = x - i
    if i == len(tab) - 2 and 0.5 + (i + 1) * step > 0.9995:
        f = (min(qf, 0.9995) - (0.5 + i * step)) / (0.9995 - (0.5 + i * step))
    return (tab[i] * (1 - f) + tab[i + 1] * f) * t["scale"]


def scenario_rounds(q, t=TABLES):
    """Normalised expected rounds for the team with map chance q: R, W (won), L (lost), and W / L given a map win / loss."""
    if q >= 0.5:
        g = lambda n: _lookup(n, q, t)
        return {"R": g("R"), "W": g("W"), "L": g("L"), "W_win": g("W_win"), "L_win": g("L_win"), "W_loss": g("W_loss"),
                "L_loss": g("L_loss")}
    g = lambda n: _lookup(n, 1 - q, t)
    return {"R": g("R"), "W": g("L"), "L": g("W"), "W_win": g("L_loss"), "L_win": g("W_loss"), "W_loss": g("L_win"),
            "L_loss": g("W_win")}


def round_share(team_maps, day, hl=None, k=None, t=TABLES):
    """Time-weighted round-win share before `day` from [(day_ordinal, won, lost)], half-life 120 d, shrunk 400 rounds to 0.5."""
    hl = hl or t["rho_half_life_days"]
    k = k or t["rho_shrink_rounds"]
    a = b = 0.0
    for d, own, opp in team_maps:
        if d >= day:
            continue
        w = math.exp(-(day - d) / hl)
        a += w * own
        b += w * (own + opp)
    return (a + k * 0.5) / (b + k)


def project_rr(pf, p_series, bo, scope, prop="kills", rho_team=None, rho_opp=None, variant=VARIANT, spread=None, t=TABLES):
    """The frozen reference projection (unchanged from rr_live.project_rr)."""
    import perf
    spread = spread or perf.SPREAD
    n = MAPS[scope]
    ro = pf["lambda_kills"] / (pf["rounds_per_map"] * pf["maps"])
    base = {"version": VERSION, "variant": variant, "market_used": False, "q": None, "p_series": p_series, "bo": bo}
    if p_series is None or bo not in (1, 3, 5) or not (0.0 < p_series < 1.0) or (bo == 1 and scope != "1"):
        lam_k = ro * t["base_r"] * n
        base.update(rounds_sum=t["base_r"] * n, H=1.0, reason="no market price / format")
    else:
        q = 0.5 if variant.endswith("-noq") else q_from_series(p_series, bo)
        core = variant[:-4] if variant.endswith("-noq") else variant
        s = scenario_rounds(q, t)
        a, b = t["a_won_round"], t["b_lost_round"]
        M = lambda x: a * x + b * (1 - x)
        rt = 0.5 if rho_team is None else rho_team
        rp = 0.5 if rho_opp is None else rho_opp
        cond = scope == "123" and bo == 3
        if core == "rr":
            per = s["R"]
            tot = (s["W_win"] + s["L_win"] + s["W_loss"] + s["L_loss"] + per) if cond else n * per
            H = 1.0
            kill_rounds = tot
        else:
            Kmap = a * s["W"] + b * s["L"]
            Kwin = a * s["W_win"] + b * s["L_win"]
            Kloss = a * s["W_loss"] + b * s["L_loss"]
            kill_rounds = (Kwin + Kloss + Kmap) if cond else n * Kmap
            H = M(rt) * M(1 - rp) / M(0.5)
            tot = (s["W_win"] + s["L_win"] + s["W_loss"] + s["L_loss"] + s["R"]) if cond else n * s["R"]
        lam_k = ro * kill_rounds / H
        base.update(market_used=True, q=q, rounds_sum=tot, H=H, rho_team=rt, rho_opp=rp)
    lam = lam_k * pf["hs_per_kill"] if prop == "hs" else lam_k
    c = spread[(prop, scope)]
    base.update(lambda_kills=lam_k, **{"lambda": lam}, sd=c * math.sqrt(max(lam, 1.0)), spread_c=c,
                spread_c_kills=spread[("kills", scope)], hs_per_kill=pf["hs_per_kill"], perf_lambda_kills=ro * t["base_r"] * n)
    return base


def _market_p(gi):
    if gi.get("p_team_market") is not None:
        return float(gi["p_team_market"])
    if gi.get("team_ml") is not None and gi.get("opp_ml") is not None:
        import grader
        return grader.win_prob(gi["team_ml"], gi["opp_ml"])[0]
    return None


def shadow_entry(gi, grade):
    """The candidate on THIS forecast's inputs (logged, never shown): its expected rounds, kills / HS projection, spread,
    Over / Under / push, side, grade and stake under the SAME grader and stake rules (grade_leg on the forecast's own
    inputs with the candidate projection in place of perf-2's). None when there is no perf-2 projection to compare."""
    pf = (gi or {}).get("perf")
    published = pf.get("version") if isinstance(pf, dict) else None
    if published == "perf-cm2" and isinstance(pf.get("perf2"), dict):
        pf = dict(pf, **pf["perf2"])     # cm-2 graded this line (2026-10-20): compared with perf-2's own numbers, as fh-1 is
    if not isinstance(pf, dict) or pf.get("version") != "perf-2" or pf.get("lambda_kills") is None:
        return None
    scope, prop = gi.get("scope") or "12", gi.get("prop") or "kills"
    pv = gi.get("odds_provenance") or {}
    out = {"version": VERSION, "variant": VARIANT, "p_series": _market_p(gi), "bo": gi.get("bo"),
           "market_source": pv.get("selected"), "market_retrieved_at": pv.get("retrieved_at"),
           "perf2_lambda": pf.get("lambda"), "published_version": published}
    rs = pf.get("round_share")
    if not isinstance(rs, dict) or rs.get("error") or rs.get("team") is None or rs.get("opp") is None:
        return dict(out, status="round share unavailable", reason=(rs or {}).get("error") if isinstance(rs, dict) else None)
    try:
        r = project_rr(pf, out["p_series"], out["bo"], scope, prop, rs["team"], rs["opp"])
    except Exception as e:          # noqa: BLE001 - shadow only
        return dict(out, status=f"error {type(e).__name__}")
    out.update(rho_team=round(rs["team"], 4), rho_opp=round(rs["opp"], 4), q=None if r["q"] is None else round(r["q"], 4),
               rounds_sum=round(r["rounds_sum"], 3), H=round(r["H"], 5), lambda_kills=round(r["lambda_kills"], 3),
               **{"lambda": round(r["lambda"], 3)}, sd=round(r["sd"], 3), market_used=r["market_used"],
               reason=r.get("reason"))
    if not r["market_used"]:
        return dict(out, status="no market term (" + (r.get("reason") or "") + ") - equals perf-2")
    try:
        import grader
        from pick_verdict import verdict
        cand = dict(pf, **{"lambda": r["lambda"]}, lambda_kills=r["lambda_kills"], sd=r["sd"])
        g = grader.grade_leg(dict(gi, perf=cand), dict(grader.CONFIG, lw1=None))
        v = verdict(g)
        out.update(status="ok", p_more=g.get("p_more"), probs_raw=g.get("probs_raw"), side=v.get("side"),
                   grade=v.get("grade"), units=v.get("units"))
    except Exception as e:          # noqa: BLE001
        out.update(status=f"graded projection only (grader error {type(e).__name__})")
    return out
