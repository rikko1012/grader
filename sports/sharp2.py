"""sharp-2: the corrected MARKET-ONLY research baseline (sports/AUDIT.md). Research records only - no grade, no stake,
never shown to members as a pick. sharp-1 (record.py, MODEL_TAG "sharp-1") stays unchanged as a versioned record.

What changed from sharp-1 (each one an audit finding)
  1. probabilities are UNCONDITIONAL (p_over + p_under + p_push = 1); the conditional-on-no-push values are kept in
     the named fields p_*_given_no_push. sharp-1 logged only the conditional chance as "chance".
  2. Kalshi quotes must be observed BEFORE the ESPN start: Kalshi markets stay active in play (2026-10-23: 227 of 248 CHI @ MIL
     markets still open with a bid 75+ min after tip-off), and Kalshi's own times are expected expirations, not starts.
  3. quote freshness is the OBSERVED age (forecast_at - observed_at of the quote's latest sighting) <= QUOTE_MAX_AGE_S;
     Kalshi gives no quote time (source_time_unknown) and that is recorded as a reason code, never hidden.
  4. liquidity: a rung with no trades and no open interest is not an anchor (84 of 526 NBA points markets on
     2026-10-23 had two-sided quotes but zero volume and zero open interest).
  5. events / players are canonical (identity.py): exact team-alias + date + both teams; roster-exact names.
  6. no break-even, edge or "value" is attached: payouts.py holds the per-entry Underdog tables for evaluation.
Unchanged on purpose: the line-shift transformation (fair.at_line + dispersion.json). The audit found it consistent
with Kalshi's own ladders between rungs (mean |error| 0.7-2.0 points of probability, within quote noise) and biased
for one-sided extrapolation of NFL yards (normal shape, no skew) - those keep sharp-1's far-gap flag as a reason code.
"""
from __future__ import annotations

import hashlib

from . import capabilities, config, fair, markets, store
from .contracts import Forecast, parse_utc

MODEL_VERSION = "sharp-2"
MAX_SPREAD = 0.05
QUOTE_MAX_AGE_S = 15 * 60
FAMILY = {"nba": "basketball", "nfl": "football", "wnba": "basketball", "cbb": "basketball", "cfb": "football"}


def dispersion_hash():
    try:
        return hashlib.sha256(config.DISPERSION_FILE.read_bytes()).hexdigest()[:10]
    except OSError:
        return None


def eligible_rung(q, event, forecast_at, last_seen=None):
    """-> (ok, reason) for one Kalshi OfferSnapshot as an anchor."""
    s = q.side("over")
    if q.availability != "open":
        return False, f"quote {q.availability}"
    if s is None or s.bid is None or s.ask is None:
        return False, "one-sided quote"
    if round(s.ask - s.bid, 4) > MAX_SPREAD:
        return False, "spread > 0.05"
    liq = q.liquidity_dict()
    if not (liq.get("volume") or 0) and not (liq.get("open_interest") or 0):
        return False, "no trades and no open interest"
    if event is None or not event.start_utc:
        return False, "no canonical start"
    seen = max(x for x in (q.observed_at, last_seen) if x)
    if seen >= event.start_utc:
        return False, "quote observed after the start (in play)"
    if seen > forecast_at:
        return False, "quote observed after forecast time"
    age = (parse_utc(forecast_at) - parse_utc(seen)).total_seconds()
    if age > QUOTE_MAX_AGE_S:
        return False, f"quote observed {age:.0f}s before forecast (> {QUOTE_MAX_AGE_S}s)"
    return True, None


def forecast(offer, offer_rid, rungs, event, forecast_at):
    """offer: a gradable pick'em OfferSnapshot; rungs: [(record_id, kalshi OfferSnapshot, last_seen_at)] for the same
    canonical event / player / stat. -> (Forecast | None, reasons)."""
    ok, why = capabilities.gate(offer)
    if not ok:
        return None, why
    fam = FAMILY[offer.league]
    spec = markets.market(fam, offer.stat)
    good, reasons = [], []
    for rid, q, seen in rungs:
        c_ok, c_why = capabilities.compatible(offer, q)
        if not c_ok:
            reasons.append(f"{rid}: {c_why}")
            continue
        e_ok, e_why = eligible_rung(q, event, forecast_at, seen)
        if e_ok:
            s = q.side("over")
            good.append((rid, q.line, (s.bid + s.ask) / 2))
        else:
            reasons.append(f"rung {q.line:g}: {e_why}")
    if not good:
        return None, reasons or ["no Kalshi rung for this player and stat"]
    good.sort(key=lambda g: (abs(g[1] - offer.line), g[1]))
    best = good[0]
    use = [best]
    if best[1] != offer.line:
        other = [g for g in good if (g[1] - offer.line) * (best[1] - offer.line) < 0]
        if other:
            use.append(min(other, key=lambda g: abs(g[1] - offer.line)))
    fs = [fair.at_line(fam, offer.stat, spec["shape"], ln, p, offer.line) for _, ln, p in use]
    if len(fs) == 1:
        f = fs[0]
    else:
        w = [1 / abs(x["gap"]) for x in fs]
        f = {k: sum(x[k] * y for x, y in zip(fs, w)) / sum(w) for k in ("over", "under", "push", "mean")}
        f["far"] = all(x["far"] for x in fs)
        f["gap"] = min((x["gap"] for x in fs), key=abs)
    tot = f["over"] + f["under"] + f["push"]
    po, pu, pp = f["over"] / tot, f["under"] / tot, f["push"] / tot
    if abs(offer.line - round(offer.line)) > 1e-9:
        po, pu, pp = po / (po + pu), pu / (po + pu), 0.0
    dec = 1 - pp
    sides = offer.offered_sides()
    chosen = max(sides, key=lambda s: po if s == "over" else pu) if sides else None
    codes = ["market_only_baseline", "kalshi_quote_time_unknown", "research_only_no_grade"]
    if f.get("far"):
        codes.append("line_gap_beyond_fitted_range")
    codes.append("between_rungs" if len(use) == 2 else ("same_line" if best[1] == offer.line else "one_sided_shift"))
    a, b = fair.shape_params(fam, offer.stat, spec["shape"])
    fc = Forecast.build(
        offers={offer_rid: offer},
        forecast_at=forecast_at, league=offer.league, event_id=offer.event_id, player_id=offer.player_id,
        player_key=offer.player_key, stat=offer.stat, scope=offer.scope, line=offer.line,
        offer_snapshot_ids=(offer_rid,), input_snapshot_ids=tuple(g[0] for g in use), model_version=MODEL_VERSION,
        artifact_version=f"dispersion:{dispersion_hash()}", data_version="kalshi-midpoint", calibration_version=None,
        grade_policy_version=None, projection_mean=round(f["mean"], 4), projection_median=None,
        distribution=(("family", spec["shape"]), ("mean", round(f["mean"], 6)), ("var_a", a), ("var_b", b)),
        p_over=po, p_under=pu, p_push=pp, p_over_given_no_push=po / dec if dec > 0 else None,
        p_under_given_no_push=pu / dec if dec > 0 else None, sides_available=sides, chosen_side=chosen, grade=None,
        stake_units=None, stake_policy_version=None, reason_codes=tuple(codes), seed=None, research_only=True,
        observed_at=forecast_at, known_at=forecast_at)
    return fc, reasons


def record_league(con, league, forecast_at):
    """Forecast every gradable pick'em offer of `league` from the stored Kalshi rungs. -> counts."""
    events = {e.event_id: e for _, e in store.events(con, league)}
    rungs = {}
    for rid, q in store.offers(con, league=league, platform="kalshi", limit=500000):
        meta = store.row_meta(con, rid)
        rungs.setdefault((q.event_id, q.player_id, q.stat), []).append((rid, q, meta.get("last_seen_at")))
    c = {"offers": 0, "forecasts": 0, "duplicate": 0, "no_anchor": 0, "not_gradable": 0}
    for rid, o in store.offers(con, league=league, limit=500000):
        if o.platform not in ("prizepicks", "underdog"):
            continue
        c["offers"] += 1
        ev = events.get(o.event_id)
        if ev and ev.start_utc and ev.start_utc <= forecast_at:
            continue
        ok, _ = capabilities.gate(o)
        if not ok:
            c["not_gradable"] += 1
            continue
        fc, _ = forecast(o, rid, rungs.get((o.event_id, o.player_id, o.stat), []), ev, forecast_at)
        if fc is None:
            c["no_anchor"] += 1
            continue
        res = store.put(con, fc)
        c["forecasts" if res["status"] == "inserted" else "duplicate"] += 1
    return c
