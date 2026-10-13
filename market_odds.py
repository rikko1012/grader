#!/usr/bin/env python3
"""
market_odds.py - THE one place the grader's market win probability comes from (owner, 2026-10-13: "Thunderpick must also
be the primary odds input to our projections").

select(team, opponent, start_iso=None, now=None, tp=None, pm=None, bo=None, network=True) - ONE policy (odds-source-2) for
the board, background refreshes and members alike, returning an odds dict in the polymarket.match_odds shape, always with
"provenance":
  1. Thunderpick when thunderpick.projection_odds(...) finds an eligible quote (availability "current": the event is in its
     endpoint's latest successful nonempty answer, at most QUOTE_MAX_AGE_S old, enabled market type and format, two valid
     odds, no draw price, policy active, canonical start in the future). bo (the match format) is passed through.
  2. Otherwise Polymarket by its existing rule (polymarket.match_odds: the teams' pre-match moneyline).
  3. Otherwise OddsPapi (bookmaker price, Pinnacle first; only with a known opponent): a LIVE request only when
     network=True (a member's own context build); network=False (board, background refreshes, every re-check) reads
     only prices already fetched - provenance.network / oddspapi_mode say which, so a board selection and a member
     selection of the same line can be told apart and compared.
  4. Otherwise "found": False - the grader's own NO MARKET handling. Never a 50% stand-in.
Thunderpick's reason for not being used (and its availability state, provenance.tp_availability) is always recorded.

The returned object is a DEEP COPY that nobody mutates afterwards (cs2_scout keeps res["odds"] as returned; grader inputs
take copies). Its provenance always has: selection (policy text), odds_policy ("odds-source-2|<Thunderpick policy
version>"), selected, source, fallback_reason, tp_availability, mapping_rev (mapping.revision() at selection), sig
(odds_signature), selected_at, query {team, opponent, start, bo}, network, oddspapi_mode.

odds_signature(o): sha1 over WHAT the market input is - selected source, match (canonical id or url), source event /
market id, market, orientation, rounded odds, the exact team probability (6 dp). No times, no snapshot hashes: an
unchanged quote seen by a newer poll is the same input (no new forecast); a moved price or another source is not.

revalidate(cached, now=None, network=False): the same selection re-run for the cached query (cs2_scout.finish_line, at
EVERY regrade - a shared context lives up to 30 min, a Thunderpick quote is current for 180 s). Returns the cached object
itself when its sig and Thunderpick availability are unchanged, else the new selection. Re-checks never wait on a
network request: Polymarket by its rule on the event list already in memory, OddsPapi cache-only; a failure while
re-checking keeps the cached input (a transient failure never changes a grade). Identical re-checks within REVAL_TTL_S
share one evaluation (700 members at once), never past the selected quote's own expiry or the match start.

Scope: the series price feeds only the series uses (p_win, stomp, market quality); per-map chances come from the SAME
source's map prices when it supplies them, otherwise they are derived from the series price - Polymarket map prices are
never mixed with a Thunderpick series price. Counted once: the selected source replaces the market input. Market quality
for bookmaker prices follows the grader's existing rule (book != pinnacle / betfair-ex -> SOFT); no exchange-style
liquidity is invented.
"""
import copy
import functools
import hashlib
import inspect
import json
import threading
import time
from datetime import datetime, timezone

import polymarket

SELECTION_VERSION = "odds-source-2"
POLICY = ("odds-source-2 (Thunderpick first, then the existing Polymarket rule, then OddsPapi - live for a member's own "
          "context build, cache-only otherwise; never a 50% stand-in)")
OP_CACHE_MAX_AGE_S = 30 * 60   # cache-only OddsPapi: prices a member's grade fetched within one shared context's life
REVAL_TTL_S = 2.0              # identical re-checks inside this window share one evaluation (load: 700 members at once)
_REVAL, _REVAL_LOCKS, _REVAL_LOCK = {}, {}, threading.Lock()


def odds_policy():
    """'odds-source-2|<thunderpick.POLICY_VERSION>' - the selection rule AND the Thunderpick eligibility policy it applied."""
    try:
        import thunderpick
        tpv = getattr(thunderpick, "POLICY_VERSION", None) or "tp-unknown"
    except Exception:              # noqa: BLE001 - a broken Thunderpick module never blocks grading
        tpv = "tp-unavailable"
    return f"{SELECTION_VERSION}|{tpv}"


def _mapping_rev():
    try:
        import mapping
        return mapping.revision()
    except Exception:              # noqa: BLE001
        return None


def _iso(ts):
    return datetime.fromtimestamp(time.time() if ts is None else ts, timezone.utc).isoformat(timespec="milliseconds")


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _r(x, n=4):
    try:
        return None if x is None else round(float(x), n)
    except (TypeError, ValueError):
        return None


@functools.lru_cache(maxsize=64)
def _accepts_bo(fn):
    """The Thunderpick reader takes the match format (bo=) - older readers / test stubs do not."""
    try:
        ps = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "bo" in ps or any(p.kind == p.VAR_KEYWORD for p in ps.values())


def _tp_call(fn, team, opponent, start_iso, now, bo):
    """The Thunderpick reader with the match format when it takes one (a wrapper that only looks like it does falls back
    to the plain call - never a 'Thunderpick error' for a signature)."""
    if _accepts_bo(fn):
        try:
            return fn(team, opponent, start_iso, now, bo=bo)
        except TypeError as e:
            if "'bo'" not in str(e):
                raise
    return fn(team, opponent, start_iso, now)


def _tp_default():
    try:
        import thunderpick
        return thunderpick.projection_odds
    except Exception as e:         # noqa: BLE001 - a broken Thunderpick module never blocks grading
        why = f"Thunderpick module unavailable ({type(e).__name__})"
        return lambda *a, **k: {"found": False, "reason": why, "availability": None}


def _op_default(team, opponent, cache_only=False, max_age=None):
    import oddspapi
    return oddspapi.match_odds(team, opponent, cache_only=cache_only, max_age=max_age)


def p_team(o):
    """The exact market probability of the query team the selection produced (None when no market - never 50%):
    Thunderpick's proportional margin-adjusted probability (6 dp), OddsPapi's de-vigged decimal prices (6 dp),
    Polymarket's price as its rule produces it."""
    if not o or not o.get("found"):
        return None
    pv = o.get("provenance") or {}
    for v in (pv.get("p_team"), o.get("team_prob")):
        if v is not None:
            try:
                return round(float(v), 6)
            except (TypeError, ValueError):
                continue
    return None


def odds_signature(o):
    """sha1 over what the market input IS (see the module doc) - never times or snapshot hashes."""
    o = o or {}
    pv = o.get("provenance") or {}
    if not o.get("found"):
        parts = ["no market"]
    else:
        sel = pv.get("selected") or ("oddspapi" if o.get("book") and o.get("book") != "thunderpick" else
                                     "thunderpick" if o.get("book") == "thunderpick" else "polymarket")
        odds = ([_r(pv.get("odds_team")), _r(pv.get("odds_opp"))] if pv.get("odds_team") is not None
                else [_r(o.get("team_prob")), _r(o.get("opp_prob"))])
        parts = [sel, pv.get("match_id") or pv.get("match_url") or o.get("url"),
                 pv.get("source_event_id") or pv.get("fixture_id") or pv.get("condition_id") or pv.get("market_id"),
                 pv.get("market"), [o.get("team"), o.get("opponent"), pv.get("team_is_a"), pv.get("flipped")],
                 odds, p_team(o)]
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _pm_provenance(o):
    """Polymarket: what match_odds returned - ids, raw prices, price source, liquidity; None when not listed."""
    url = o.get("url")
    slug = o.get("event_slug") or (url.rsplit("/event/", 1)[1] if isinstance(url, str) and "/event/" in url else None)
    return {"source": "polymarket", "market": "series winner (moneyline)", "p_team": o.get("team_prob"),
            "match_url": url, "event_url": url, "event_slug": slug, "title": o.get("title"), "start": o.get("start"),
            "market_id": o.get("market_id"), "condition_id": o.get("condition_id"), "outcomes": o.get("outcomes"),
            "outcome_prices": o.get("outcome_prices"), "best_bid": o.get("best_bid"), "best_ask": o.get("best_ask"),
            "price_source": o.get("source"), "liquidity": o.get("liquidity"), "volume": o.get("volume"),
            "team_outcome": o.get("team"), "opp_outcome": o.get("opponent"),
            "retrieved_at": o.get("retrieved_at"), "retrieved_note": "our retrieval time of Polymarket's event list",
            "source_updated_at": None, "source_updated_note": "the event list carries no per-price update time"}


def _op_provenance(a, mode, pm_note):
    """OddsPapi: the bookmaker's two decimal prices, de-vigged proportionally (exact), the fixture, our retrieval time."""
    d = list(a.get("decimal") or [None, None]) + [None, None]
    dt, do = d[0], d[1]
    p = None
    try:
        if dt and do and float(dt) > 1 and float(do) > 1:
            it, io = 1 / float(dt), 1 / float(do)
            p = round(it / (it + io), 6)
    except (TypeError, ValueError):
        p = None
    return {"source": "oddspapi", "book": a.get("book"), "market": "Match Winner (OddsPapi market 171)",
            "p_team": p if p is not None else a.get("team_prob"), "odds_team": dt, "odds_opp": do,
            "method": "proportional de-vig of the two decimal prices", "fixture_id": a.get("fixture_id"),
            "title": a.get("title"), "start": a.get("start"), "match_url": None, "oddspapi_mode": mode,
            "retrieved_at": a.get("retrieved_at"), "source_updated_at": None,
            "source_updated_note": "the feed's price update time is not read", "polymarket_note": pm_note}


def _finish(o, pv, selected, fallback_reason, tp_av, q, network, op_mode, mrev, now):
    pv = dict(pv, selection=POLICY, odds_policy=odds_policy(), selected=selected, fallback_reason=fallback_reason,
              tp_availability=tp_av, mapping_rev=mrev, query=dict(q), network=bool(network), oddspapi_mode=op_mode,
              selected_at=_iso(now))
    if selected:
        pv["source"] = pv.get("source") or selected
    o["provenance"] = pv
    pv["sig"] = odds_signature(o)
    return copy.deepcopy(o)


def select(team, opponent, start_iso=None, now=None, tp=None, pm=None, bo=None, network=True, op=None):
    """The projection's market input + provenance (module doc). tp / pm / op: injectable sources (tests)."""
    q = {"team": team, "opponent": opponent, "start": start_iso, "bo": bo}
    mrev = _mapping_rev()
    tp_fn = tp or _tp_default()
    try:
        t = _tp_call(tp_fn, team, opponent, start_iso, now, bo) or {}
    except Exception as e:          # noqa: BLE001 - a Thunderpick failure falls through to the next source, with the reason
        t = {"found": False, "reason": f"Thunderpick error ({type(e).__name__})", "availability": None}
    if t.get("found"):
        out = copy.deepcopy(dict(t))
        pv = dict(out.get("provenance") or {})
        if pv.get("p_team") is None:
            pv["p_team"] = out.get("team_prob")
        return _finish(out, pv, "thunderpick", None, pv.get("availability") or "current", q, network, None, mrev, now)
    tp_reason, tp_av = t.get("reason") or "not found", t.get("availability")
    o = copy.deepcopy(dict((pm or polymarket.match_odds)(team, opponent)))
    if o.get("found"):
        return _finish(o, _pm_provenance(o), "polymarket", tp_reason, tp_av, q, network, None, mrev, now)
    pm_note, op_mode, op_note = o.get("note"), None, None
    if opponent:                    # bookmaker price (Pinnacle first) - as before, only with a known opponent
        op_mode = "live" if network else "cache-only"
        try:
            a = (op or _op_default)(team, opponent, cache_only=not network,
                                    max_age=None if network else OP_CACHE_MAX_AGE_S)
        except Exception as e:      # noqa: BLE001
            a = {"found": False, "note": f"OddsPapi error ({type(e).__name__})"}
        a = copy.deepcopy(dict(a or {}))
        if a.get("found"):
            a["polymarket_note"] = pm_note
            return _finish(a, _op_provenance(a, op_mode, pm_note), "oddspapi", tp_reason, tp_av, q, network, op_mode,
                           mrev, now)
        op_note = a.get("note")
    pv = {"source": None, "market": None, "p_team": None, "match_url": None, "polymarket_note": pm_note,
          "oddspapi_note": op_note}
    return _finish(o, pv, None, tp_reason, tp_av, q, network, op_mode, mrev, now)


def _pm_in_memory(team, opponent):
    """Polymarket by its rule on the event list already in memory - no request on a re-check (the list is refreshed by
    every context build; the live path keeps the same list through a Polymarket outage). Nothing in memory yet: the
    normal call (one shared download, at most once per process)."""
    data, at = polymarket.cached_events()
    if data is None:
        return polymarket.match_odds(team, opponent)
    return polymarket.match_odds(team, opponent, events=data, events_at=at)


def _key_lock(key):
    with _REVAL_LOCK:
        lk = _REVAL_LOCKS.get(key)
        if lk is None:
            if len(_REVAL_LOCKS) > 5000:
                _REVAL_LOCKS.clear()
            lk = _REVAL_LOCKS[key] = threading.Lock()
        return lk


def _memo_until(new):
    """A shared re-check result is reused for REVAL_TTL_S, never past the selected quote's own eligibility (Thunderpick:
    retrieval + QUOTE_MAX_AGE_S) or the match start."""
    until = time.time() + REVAL_TTL_S
    pv = new.get("provenance") or {}
    if pv.get("selected") == "thunderpick":
        try:
            import thunderpick
            max_age = float(getattr(thunderpick, "QUOTE_MAX_AGE_S", 180))
        except Exception:          # noqa: BLE001
            max_age = 180.0
        ra = _ts(pv.get("retrieved_at"))
        if ra is not None:
            until = min(until, ra + max_age)
    st = _ts(pv.get("canonical_start") or new.get("start"))
    if st is not None:
        until = min(until, st)
    return until


def revalidate(cached, now=None, network=False, tp=None, pm=None, op=None):
    """The cached selection re-checked now (module doc): the cached object when nothing it depends on changed (same sig,
    same Thunderpick availability), else the new selection. Objects not made by select() (no query) are returned as is."""
    pv = (cached or {}).get("provenance") or {}
    q = pv.get("query")
    if not cached or not isinstance(q, dict):
        return cached
    shared = tp is None and pm is None and op is None and now is None
    key = (q.get("team"), q.get("opponent"), q.get("start"), str(q.get("bo")), bool(network), _mapping_rev())

    def run():
        return select(q.get("team"), q.get("opponent"), q.get("start"), now=now, tp=tp, pm=pm or _pm_in_memory,
                      bo=q.get("bo"), network=network, op=op)
    try:
        if not shared:
            new = run()
        else:
            made = _ts(pv.get("selected_at")) or 0.0
            with _key_lock(key):                  # one evaluation per query at a time; the rest read it
                hit = _REVAL.get(key)
                # a shared result counts only while fresh AND evaluated after the cached selection was made (a check
                # from before a member's context build fetched a price must not undo that selection)
                if hit and time.time() < hit[0] and hit[1] >= made:
                    new = hit[2]
                else:
                    at = time.time()
                    new = run()
                    with _REVAL_LOCK:
                        if len(_REVAL) > 5000:
                            _REVAL.clear()
                        _REVAL[key] = (_memo_until(new), at, new)
    except Exception:              # noqa: BLE001 - a failure while re-checking never changes the grade's market input
        return cached
    npv = new.get("provenance") or {}
    if npv.get("sig") == pv.get("sig") and npv.get("tp_availability") == pv.get("tp_availability"):
        return cached
    return copy.deepcopy(new)


def source_label(odds):
    """'Thunderpick' / 'Polymarket' / 'OddsPapi (<book>)' / None - what the card shows as the projection odds source."""
    if not odds or not odds.get("found"):
        return None
    pv = odds.get("provenance") or {}
    if pv.get("selected") == "thunderpick":
        return "Thunderpick"
    if pv.get("selected") == "manual":
        return "Manual odds (CLI)"
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
    tp_lookup = tp_lookup or (lambda t, o, s, bo=None: _tp_call(_tp_default(), t, o, s, None, bo))
    out, skipped = [], {}
    for r in rows:
        gi = dict(r.get("inputs") or {})
        if not gi:
            skipped["no saved inputs"] = skipped.get("no saved inputs", 0) + 1
            continue
        # the saved match format goes with the lookup when the reader takes it (Thunderpick eligibility is per format)
        t = (tp_lookup(r.get("team"), r.get("opponent"), r.get("start"), bo=gi.get("bo")) if _accepts_bo(tp_lookup)
             else tp_lookup(r.get("team"), r.get("opponent"), r.get("start")))
        if not t.get("found"):
            k = t.get("reason") or "not found"
            skipped[k] = skipped.get(k, 0) + 1
            continue
        old_g = grader.grade_leg(dict(gi))
        # the whole market input is swapped (p_team_market too - a grader that prefers it must not keep the old source's)
        new_in = dict(gi, team_ml=prob_to_american(t["team_prob"]), opp_ml=prob_to_american(1 - t["team_prob"]),
                      map_win_probs=None, market_quality="SOFT", odds_provenance=t.get("provenance"),
                      p_team_market=p_team(t))
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
