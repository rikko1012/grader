#!/usr/bin/env python3
"""
market_odds.py - THE one place the grader's market win probability comes from (owner, 2026-10-13: "Thunderpick must also
be the primary odds input to our projections").

select(team, opponent, start_iso) -> an odds dict in the polymarket.match_odds shape, always with "provenance":
  1. Thunderpick first (policy tp-first-1): its proportional margin-adjusted SERIES probability, when thunderpick.policy()
     is active (timezone and series-winner meaning established from evidence), the match links exactly (canonical id +
     orientation), and the quote is current, pre-match, two-way with valid odds.
  2. Otherwise the existing qualified source, unchanged: polymarket.match_odds (the caller then keeps its existing
     OddsPapi fallback). The reason Thunderpick was not used is recorded (provenance.fallback_reason).
  3. Never a 50% stand-in: "found": False is passed on as before (the grader's own NO MARKET handling applies).

Scope: the series price feeds only the series uses (p_win, stomp, market quality); per-map chances come from the SAME
source's map prices when that source supplies them, otherwise they are derived from the series price - Polymarket map
prices are never mixed with a Thunderpick series price. Counted once: the selected source replaces the market input.
Market quality for Thunderpick follows the grader's existing rule for bookmaker prices (book != pinnacle / betfair-ex ->
SOFT); no exchange-style liquidity is invented.
"""
import polymarket

POLICY = "odds-source-1 (Thunderpick first, then the existing Polymarket rule)"


def select(team, opponent, start_iso=None, now=None, tp=None, pm=None):
    """The projection's market input + provenance. tp / pm: injectable sources (tests)."""
    tp_fn = tp
    if tp_fn is None:
        try:
            import thunderpick
            tp_fn = thunderpick.projection_odds
        except Exception as e:          # noqa: BLE001 - a broken Thunderpick module never blocks grading
            tp_fn = lambda *a, **k: {"found": False, "reason": f"Thunderpick module unavailable ({type(e).__name__})"}
    try:
        t = tp_fn(team, opponent, start_iso, now)
    except Exception as e:              # noqa: BLE001
        t = {"found": False, "reason": f"Thunderpick error ({type(e).__name__})"}
    if t.get("found"):
        t["provenance"] = dict(t.get("provenance") or {}, selection=POLICY, selected="thunderpick", fallback_reason=None)
        return t
    o = (pm or polymarket.match_odds)(team, opponent)
    o = dict(o)
    o["provenance"] = {"selection": POLICY, "selected": "polymarket" if o.get("found") else None,
                       "source": "polymarket" if o.get("found") else None, "fallback_reason": t.get("reason") or "not found",
                       "market": "series winner (moneyline)" if o.get("found") else None,
                       "p_team": o.get("team_prob"), "match_url": o.get("url")}
    return o


def source_label(odds):
    """'Thunderpick' / 'Polymarket' / 'OddsPapi (<book>)' / None - what the card shows as the projection odds source."""
    if not odds or not odds.get("found"):
        return None
    pv = odds.get("provenance") or {}
    if pv.get("selected") == "thunderpick":
        return "Thunderpick"
    if odds.get("book") and odds.get("book") != "thunderpick":
        return f"OddsPapi ({odds['book']})"
    return "Polymarket"


def compare(rows, tp_lookup=None):
    """Old source vs Thunderpick source on IDENTICAL saved inputs (reproducible): for each row {"inputs": grader inputs
    as saved with the forecast, "team", "opponent", "start", "forecast_id"} the grader is re-run with the market input
    swapped to Thunderpick's series probability (map prices cleared - derived from the series price, as select() does)
    and its market quality set by the existing bookmaker rule. Returns per-row old/new projection, probability, grade and
    stake plus the Thunderpick provenance; rows without a usable Thunderpick price are counted with the reason."""
    import grader
    from pick_verdict import verdict
    from polymarket import prob_to_american
    tp_lookup = tp_lookup or (lambda t, o, s: __import__("thunderpick").projection_odds(t, o, s))
    out, skipped = [], {}
    for r in rows:
        gi = dict(r.get("inputs") or {})
        if not gi:
            skipped["no saved inputs"] = skipped.get("no saved inputs", 0) + 1
            continue
        t = tp_lookup(r.get("team"), r.get("opponent"), r.get("start"))
        if not t.get("found"):
            k = t.get("reason") or "not found"
            skipped[k] = skipped.get(k, 0) + 1
            continue
        old_g = grader.grade_leg(dict(gi))
        new_in = dict(gi, team_ml=prob_to_american(t["team_prob"]), opp_ml=prob_to_american(1 - t["team_prob"]),
                      map_win_probs=None, market_quality="SOFT", odds_provenance=t.get("provenance"))
        new_g = grader.grade_leg(new_in)
        ov, nv = verdict(old_g), verdict(new_g)
        out.append({"forecast_id": r.get("forecast_id"), "player": r.get("player"), "prop": gi.get("prop"), "line": gi.get("line"),
                    "old": {"p_win": (old_g.get("detail") or {}).get("p_win"), "lambda": old_g.get("lambda_final"),
                            "p_more": old_g.get("p_more"), "side": ov.get("side"), "grade": ov.get("grade"), "units": ov.get("units"),
                            "source": (gi.get("odds_provenance") or {}).get("selected") or ("polymarket" if gi.get("team_ml") is not None else None)},
                    "new": {"p_win": (new_g.get("detail") or {}).get("p_win"), "lambda": new_g.get("lambda_final"),
                            "p_more": new_g.get("p_more"), "side": nv.get("side"), "grade": nv.get("grade"), "units": nv.get("units"),
                            "source": "thunderpick"},
                    "thunderpick": t.get("provenance")})
    changed = [x for x in out if (x["old"]["grade"], x["old"]["units"], x["old"]["side"]) != (x["new"]["grade"], x["new"]["units"], x["new"]["side"])]
    return {"rows": out, "compared": len(out), "skipped": skipped, "changed_grade_stake_or_side": len(changed),
            "mean_abs_dlambda": round(sum(abs((x["new"]["lambda"] or 0) - (x["old"]["lambda"] or 0)) for x in out) / len(out), 3) if out else None,
            "mean_abs_dp": round(sum(abs((x["new"]["p_more"] or 0) - (x["old"]["p_more"] or 0)) for x in out) / len(out), 4) if out else None,
            "note": "identical saved inputs; only the market input differs"}
