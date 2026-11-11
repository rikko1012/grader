"""Scout's chance at another book's line, read from the SAME saved projection distribution (audit 2026-11-05 A2/B4).

A Sleeper / Dabble line can sit one kill away from the PrizePicks / Underdog line Scout graded. The grade saved for that player,
stat and maps carries the exact distribution behind it, so the chance at the other line is a read of that distribution - never a
new model, never a calibration or a shrink, never a letter or a stake:

  - CS2: grader.py's "dist" on the board row (normal lam / sd, or the headshot beta-binomial), through dist_view.pmf - the same
    function the card's distribution chart uses. Read only when that distribution reproduces the board row's own saved outcome
    chances (model_outcomes) AND its printed chance at its own line; otherwise no chance is shown (a display layer such as ec-1, or
    a grade whose distribution was not saved, is never mixed with a raw read). The chance follows the grader's side rule at a line:
    push removed, the kills cap, the side with the higher chance.
  - LoL: the saved negative binomial (lambda, nb_size) of the forecast, the formula lol/model.py probs() uses. Read only when it
    reproduces the saved p_over / p_under at the forecast's own line (a 2-or-3-game mixture or a calibrated chance does not, so it
    gets no chance). The side is the more likely one (lol/center/forecast.py's rule).

Pure functions: no I/O, no lol or sports import. Display only - nothing here is logged, graded or staked."""
import math

CHECK_TOL = 0.002          # the saved distribution must reproduce the saved chances this closely (dist_view.CHECK_TOL)
P_TOL = 0.0016             # and the printed chance (rounded to 3 places) at its own line


def compact_dist(d):
    """The board row's copy of a grade's "dist" (a few numbers), or None."""
    if not isinstance(d, dict):
        return None
    try:
        if d.get("kind") == "normal":
            return {"kind": "normal", "lam": round(float(d["lam"]), 4), "sd": round(float(d["sd"]), 4)}
        if d.get("kind") == "hs_bb":
            out = {"kind": "hs_bb", "lam_kills": round(float(d["lam_kills"]), 4), "hs_per_kill": round(float(d["hs_per_kill"]), 5),
                   "scope": str(d.get("scope") or "12")}
            if d.get("c") is not None:
                out["c"] = float(d["c"])
            return out
    except (KeyError, TypeError, ValueError):
        return None
    return None


def _three(pmf, line):
    over = sum(v for k, v in pmf.items() if k > line)
    under = sum(v for k, v in pmf.items() if k < line)
    push = sum(v for k, v in pmf.items() if k == line)
    return over, under, push


def _cap():
    try:
        import grader
        return float(grader.CONFIG["p_cap_kills"])
    except Exception:                          # noqa: BLE001 - no cap known = no chance shown (never an uncapped kills chance)
        return None


def _cs2_side(over, under, prop, mean, line):
    """(side, p_more) at one line by the grader's rule: push removed, the kills cap, the side with the higher rounded chance."""
    if over + under <= 0:
        return None
    pm = over / (over + under)
    if prop == "kills":
        cap = _cap()
        if cap is None:
            return None
        pm = min(max(pm, 1 - cap), cap)
    pmr, plr = round(pm, 3), round(1 - pm, 3)
    if pmr != plr:
        side = "over" if pmr > plr else "under"
    else:
        raw = over / (over + under)
        side = ("over" if raw > 0.5 else "under") if raw != 0.5 else ("over" if mean > line else "under")
    return side, pm


def cs2_at(x, line):
    """{side, p_side, p_over, p_under, p_push, basis} for this CS2 board row's player / stat / maps at `line`, or None.
    x is the graded board row (dist, model_outcomes, line, p, side, prop)."""
    import dist_view
    try:
        line, base = float(line), float(x.get("graded_line") if x.get("graded_line") is not None else x["line"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (line, base)):
        return None
    if x.get("cal_v") or x.get("p_raw") is not None:
        return None                            # the printed chance is a display layer's: never mixed with a raw read
    if x.get("perf_on") is not True or any("only half counts" in str(f) for f in (x.get("flags") or ())):
        return None                            # old pipeline: its gap-trust shift and margins depend on the line, so the saved
                                               # distribution read at another line is not what the grader would print there
    dist, raw, p = x.get("dist"), x.get("model_outcomes") or {}, x.get("p")
    try:
        raw = {"over": float(raw.get("over")), "under": float(raw.get("under")),
               "push": float(raw.get("push", 0.0))}
        p = float(p)
    except (TypeError, ValueError, AttributeError):
        return None
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in (*raw.values(), p)):
        return None
    try:
        pmf = dist_view.pmf(dist) if dist else None
    except Exception:                          # noqa: BLE001
        pmf = None
    if not pmf or raw.get("over") is None or raw.get("under") is None or p is None:
        return None
    mean = sum(k * v for k, v in pmf.items())
    o, u, q = _three(pmf, base)
    if max(abs(o - raw["over"]), abs(u - raw["under"]), abs(q - (raw.get("push") or 0.0))) > CHECK_TOL:
        return None                            # not the distribution behind this grade
    own = _cs2_side(o, u, x.get("prop"), mean, base)
    want = {"More": "over", "Less": "under"}.get(x.get("side"))
    if own is None or want is None or own[0] != want:
        return None
    if abs((own[1] if want == "over" else 1 - own[1]) - float(p)) > P_TOL:
        return None                            # the printed chance is not this distribution's read at its own line
    o, u, q = _three(pmf, line)
    got = _cs2_side(o, u, x.get("prop"), mean, line)
    if got is None:
        return None
    side, pm = got
    po, pu = round(pm, 3), round(1 - pm, 3)
    return {"side": side, "p_side": po if side == "over" else pu, "p_over": po, "p_under": pu, "p_push": round(q, 4),
            "basis": "saved distribution"}


# ------------------------------------------------------------------------------------------------------------- LoL

def _nb_pmf(k, mu, size):
    """lol/model.py nb_pmf, the same formula (copied: this module imports no lol package)."""
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(math.lgamma(k + size) - math.lgamma(size) - math.lgamma(k + 1)
                    + size * math.log(size / (size + mu)) + k * math.log(mu / (size + mu)))


def nb_probs(lam, size, line):
    """lol/model.py probs(): (over, under, push) for a line under NB(lam, size)."""
    top = int(lam + 12 * math.sqrt(lam + lam * lam / size)) + 10
    over = under = push = 0.0
    for k in range(0, max(top, int(line) + 2)):
        pk = _nb_pmf(k, lam, size)
        if k > line:
            over += pk
        elif k < line:
            under += pk
        else:
            push += pk
    tot = over + under + push
    return over / tot, under / tot, push / tot


def lol_at(lam, size, base_line, p_over, p_under, line):
    """{side, p_side, p_over, p_under, p_push, basis} at `line` from the saved NB, or None when it does not reproduce the saved
    p_over / p_under at the forecast's own line."""
    try:
        lam, size, base_line, line, p_over, p_under = (float(v) for v in (lam, size, base_line, line, p_over, p_under))
    except (TypeError, ValueError):
        return None
    if lam <= 0 or size <= 0 or not all(math.isfinite(v) for v in (lam, size, base_line, line, p_over, p_under)):
        return None
    if not (0 <= p_over <= 1 and 0 <= p_under <= 1):
        return None
    o, u, _ = nb_probs(lam, size, base_line)
    if max(abs(o - p_over), abs(u - p_under)) > CHECK_TOL:
        return None                            # a mixture or a calibrated chance: not this distribution
    o, u, q = nb_probs(lam, size, line)
    side = "over" if o >= u else "under"
    po, pu, pq = round(o, 4), round(u, 4), round(q, 4)
    return {"side": side, "p_side": po if side == "over" else pu, "p_over": po, "p_under": pu, "p_push": pq, "basis": "saved distribution"}
