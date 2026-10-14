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
Thunderpick's reason for not being used is always recorded: provenance.fallback_reason is the reason in words WITH its
availability state ("<reason> [availability: stale]"), provenance.tp_availability the state alone (machine-readable).
A source that FAILED (raised) rather than answered is recorded too - provenance.tp_error / pm_error - because a failure is
not a feed state: a re-check treats it as "unknown", never as "not current" (see revalidate).

The returned object is a DEEP COPY that nobody mutates afterwards (cs2_scout keeps res["odds"] as returned; grader inputs
take copies). Its provenance always has: selection (policy text), odds_policy ("odds-source-2|<Thunderpick policy
version>"), selected, source, fallback_reason, tp_availability, mapping_rev (mapping.revision() at selection), sig
(odds_signature), selected_at, query {team, opponent, start, bo}, network, oddspapi_mode.

odds_signature(o): sha1 over WHAT the market input is - selected source, match (canonical id or url), source event /
market id, market, orientation, rounded odds, the exact team probability (6 dp). No times, no snapshot hashes: an
unchanged quote seen by a newer poll is the same input (no new forecast); a moved price or another source is not.

revalidate(cached, now=None, network=False): the same selection re-run for the cached query (cs2_scout.finish_line, at
EVERY regrade - a shared context lives up to 30 min, a Thunderpick quote is current for 180 s). Returns the cached object
itself when its sig, Thunderpick availability and market-quality class (the stake-cap input: HARD / SOFT / NONE) are
unchanged, else the new selection. recheck() is the same with a record of the check itself ({checked_at, result, what the
re-check found, Thunderpick's latest quote age ...}) - kept NEXT TO the odds (grader_inputs.odds_check, saved with the
forecast), never written into them: a re-confirmed quote keeps its own retrieval time (never refreshed).
Re-checks never wait on a network request: Polymarket by its rule on the event list already in memory (none in memory:
Polymarket is "not readable", never downloaded on a member's clock), OddsPapi cache-only. Failures (2026-10-13 review):
  - a Thunderpick READER failure (an exception - e.g. sqlite 'database is locked' - not a feed state) keeps a cached
    Thunderpick input while that quote is still inside its own window (retrieval + QUOTE_MAX_AGE_S, before the start);
    past it the selection falls to the next source (never a non-current quote);
  - Polymarket not readable keeps a cached Polymarket / OddsPapi / no-market input (its answer is unknown), but never a
    cached Thunderpick quote that is no longer current (eligibility: Thunderpick ONLY when current) - that one falls to
    OddsPapi's cache or no market;
  - any other failure while re-checking keeps the cached input.
Identical re-checks within REVAL_TTL_S share one evaluation (700 members at once) - a failed evaluation too (waiters never
re-run it one after another) - never past the selected quote's own expiry or the match start.

Thunderpick blips (owner decision E1, 2026-10-13: "a Thunderpick odds blip is treated like an HLTV blip"): when
Thunderpick HAS an event for this exact match (linked + matched) but no current quote, the selection is the usual
fallback (Polymarket / OddsPapi / no market - a member card can still show a temporary grade) marked as temporary:
top-level transient: True, provenance.blip: True, provenance.fallback_reason = BLIP_TEXT + ": <reason> [availability]".
provenance.selected / source stay the fallback source (nothing calls it Thunderpick or current); the not-current
Thunderpick quote is recorded beside it with its OWN retrieval time and age - provenance.tp_last_quote {retrieved_at,
age_s, source_event_id, match_id, availability, used: False} - never as the input.
Blip states (the event still held for the match): availability "stale" (the latest successful answer, which lists the
event, is older than QUOTE_MAX_AGE_S - 429 cooldowns between polls, timeouts, 5xx) and "endpoint_empty" (an empty []
answer between full answers) while the answer that last listed the event is at most thunderpick.BLIP_MAX_S old and
nothing but the feed blocks the quote (the reader says so: "blip" in its answer; a reader without the key:
TP_BLIP_STATES), and a reader failure (exception) while the current run of failures is shorter than BLIP_MAX_S.
NOT blips - a normal fallback with no mark, exactly as before: no event / unlinked / ambiguous, policy_inactive,
scope_unconfirmed, format_excluded, draw_listed, invalid_odds, started, not_in_latest_answer (absent from a newer full
answer), the module unavailable, and an event no answer listed for longer than BLIP_MAX_S (or polling / authentication
stopped) - treated as cleared: a permanently dead or emptied Thunderpick is never "transient forever". Consumers: the
grade copies the flag
(odds_transient) - the board keeps the line's last grade or hides it, the card shows a notice, the grade is never
logged as a forecast. The odds signature is unchanged (it says what the temporary input is); revalidate never returns
a transient object as the confirmed "same" input (no freezing: every re-check re-decides it) and records a blip as
"transient: ..." - never "changed" (not a new forecast input).

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
# E1 (owner, 2026-10-13): a Thunderpick odds blip is treated like an HLTV blip - see the module doc
TP_BLIP_STATES = ("stale", "endpoint_empty")   # a reader that does not say (no "blip" key): these states are blips
BLIP_TEXT = "Thunderpick quote not current (feed blip) - treated like an outage"
_TP_FAIL = {"since": None}     # the current run of Thunderpick reader failures (a blip only while it is shorter than
                               # thunderpick.BLIP_MAX_S - a reader that keeps failing is not "transient forever")


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


@functools.lru_cache(maxsize=256)
def _accepts_start(fn):
    """The fallback reader takes the graded match's start (start=) - injected test readers may not."""
    try:
        ps = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "start" in ps or any(p.kind == p.VAR_KEYWORD for p in ps.values())


def _fb_call(fn, start_iso, *a, **k):
    """A fallback reader (Polymarket / OddsPapi) with the graded match's start when it takes one (2026-10-13 audit: the
    graded match, never a rematch of the same teams); a wrapper that only looks like it does gets the plain call."""
    try:
        takes = bool(start_iso) and _accepts_start(fn)
    except TypeError:               # unhashable callable
        takes = False
    if takes:
        try:
            return fn(*a, start=start_iso, **k)
        except TypeError as e:
            if "'start'" not in str(e):
                raise
    return fn(*a, **k)


def _tp_default():
    try:
        import thunderpick
        return thunderpick.projection_odds
    except Exception as e:         # noqa: BLE001 - a broken Thunderpick module never blocks grading
        why = f"Thunderpick module unavailable ({type(e).__name__})"
        return lambda *a, **k: {"found": False, "reason": why, "availability": None}


def _op_default(team, opponent, cache_only=False, max_age=None, start=None):
    import oddspapi
    return oddspapi.match_odds(team, opponent, cache_only=cache_only, max_age=max_age, start=start)


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


def _finish(o, pv, selected, fallback_reason, tp_av, q, network, op_mode, mrev, now, tp_err=None, pm_err=None,
            blip=False, tp_last=None):
    pv = dict(pv, selection=POLICY, odds_policy=odds_policy(), selected=selected, fallback_reason=fallback_reason,
              tp_availability=tp_av, mapping_rev=mrev, query=dict(q), network=bool(network), oddspapi_mode=op_mode,
              selected_at=_iso(now), tp_error=tp_err, pm_error=pm_err, blip=bool(blip), tp_last_quote=tp_last)
    if selected:
        pv["source"] = pv.get("source") or selected
    o["provenance"] = pv
    o.pop("transient", None)
    if blip:                        # E1: a temporary fallback - consumers keep the line's last grade / never log it
        o["transient"] = True
    pv["sig"] = odds_signature(o)
    return copy.deepcopy(o)


def _blip_max():
    try:
        import thunderpick
        return float(getattr(thunderpick, "BLIP_MAX_S", 1800))
    except Exception:              # noqa: BLE001
        return 1800.0


def _reader_failed():
    """A Thunderpick reader failure (an exception - unknown, not a feed state) is a blip while the current run of
    failures is shorter than BLIP_MAX_S; a reader that keeps failing is a normal fallback."""
    now = time.time()
    since = _TP_FAIL["since"] = _TP_FAIL["since"] or now
    return now - since <= _blip_max()


def _fallback_text(reason, state):
    """Why Thunderpick was not used, in words WITH its availability state (spec: fallback_reason includes the state) -
    e.g. "event not in Thunderpick's latest answer [availability: not_in_latest_answer]". No state (reader error, older
    reader): the reason alone."""
    reason = reason or "not found"
    if state and str(state) not in reason:
        return f"{reason} [availability: {state}]"
    return reason


class PolymarketNotInMemory(Exception):
    """A re-check found no Polymarket event list in memory: it never downloads one (a request that can hang 20 s would sit
    on a member's grade, 700 at once) - Polymarket's answer is unknown on this re-check (provenance.pm_error)."""


def select(team, opponent, start_iso=None, now=None, tp=None, pm=None, bo=None, network=True, op=None, recheck=False):
    """The projection's market input + provenance (module doc). tp / pm / op: injectable sources (tests).
    recheck=False (a context build): a Polymarket failure propagates exactly as before 2026-10-13 - no grade is built on a
    transient failure. recheck=True (revalidate): the failure is recorded (provenance.pm_error) and the selection goes on
    to OddsPapi's cache / no market, so revalidate can decide from what the failure means for the CACHED input (it never
    keeps a Thunderpick quote that is no longer current). A Thunderpick reader failure falls through in both modes, with
    the reason and provenance.tp_error (revalidate treats it as unknown, not as a feed state).
    E1 (owner 2026-10-13): when Thunderpick HAS an event for this match but no current quote (a blip - module doc), the
    usual fallback selection comes back marked transient (top-level transient: True, provenance.blip: True,
    fallback_reason starting with BLIP_TEXT); otherwise no mark."""
    q = {"team": team, "opponent": opponent, "start": start_iso, "bo": bo}
    mrev = _mapping_rev()
    tp_fn = tp or _tp_default()
    tp_err = pm_err = None
    try:
        t = _tp_call(tp_fn, team, opponent, start_iso, now, bo) or {}
        _TP_FAIL["since"] = None
    except Exception as e:          # noqa: BLE001 - a Thunderpick failure falls through to the next source, with the reason
        tp_err = type(e).__name__
        t = {"found": False, "reason": f"Thunderpick error ({tp_err})", "availability": None, "blip": _reader_failed()}
    if t.get("found"):
        out = copy.deepcopy(dict(t))
        pv = dict(out.get("provenance") or {})
        if pv.get("p_team") is None:
            pv["p_team"] = out.get("team_prob")
        return _finish(out, pv, "thunderpick", None, pv.get("availability") or "current", q, network, None, mrev, now)
    tp_av = t.get("availability")
    blip = bool(t["blip"]) if "blip" in t else tp_av in TP_BLIP_STATES
    tp_reason = _fallback_text(t.get("reason"), tp_av)
    if blip:
        tp_reason = f"{BLIP_TEXT}: {tp_reason}"
    # Thunderpick's last quote for this match when the reader names it (a stale one): recorded with its OWN retrieval time
    # and age - never the selected input, never labelled current
    tp_last = ({"retrieved_at": t.get("quote_retrieved_at"), "age_s": t.get("quote_age_s"),
                "source_event_id": t.get("source_event_id"), "match_id": t.get("match_id"), "availability": tp_av,
                "used": False, "note": "Thunderpick's last quote for this match - not current, not used"}
               if t.get("quote_retrieved_at") else None)
    try:
        o = copy.deepcopy(dict(_fb_call(pm or polymarket.match_odds, start_iso, team, opponent)))
    except Exception as e:          # noqa: BLE001
        if not recheck:
            raise                   # a context build: as before (no grade from a transient failure)
        pm_err = str(e) if isinstance(e, PolymarketNotInMemory) else f"Polymarket error ({type(e).__name__})"
        o = {"found": False, "note": pm_err}
    if o.get("found"):
        return _finish(o, _pm_provenance(o), "polymarket", tp_reason, tp_av, q, network, None, mrev, now, tp_err,
                       blip=blip, tp_last=tp_last)
    pm_note, op_mode, op_note = o.get("note"), None, None
    if opponent:                    # bookmaker price (Pinnacle first) - as before, only with a known opponent
        op_mode = "live" if network else "cache-only"
        try:
            a = _fb_call(op or _op_default, start_iso, team, opponent, cache_only=not network,
                         max_age=None if network else OP_CACHE_MAX_AGE_S)
        except Exception as e:      # noqa: BLE001
            a = {"found": False, "note": f"OddsPapi error ({type(e).__name__})"}
        a = copy.deepcopy(dict(a or {}))
        if a.get("found"):
            a["polymarket_note"] = pm_note
            return _finish(a, _op_provenance(a, op_mode, pm_note), "oddspapi", tp_reason, tp_av, q, network, op_mode,
                           mrev, now, tp_err, pm_err, blip=blip, tp_last=tp_last)
        op_note = a.get("note")
    pv = {"source": None, "market": None, "p_team": None, "match_url": None, "polymarket_note": pm_note,
          "oddspapi_note": op_note}
    return _finish(o, pv, None, tp_reason, tp_av, q, network, op_mode, mrev, now, tp_err, pm_err, blip=blip,
                   tp_last=tp_last)


def _pm_in_memory(team, opponent, start=None):
    """Polymarket by its rule on the event list already in memory - never a request on a re-check (the list is refreshed
    by every context build and the board loop; the live path keeps the last list through a Polymarket outage). Nothing in
    memory (no download has succeeded in this process): PolymarketNotInMemory - Polymarket is unknown on this re-check
    (2026-10-13 review: a download here held every waiting member's grade, one 20 s attempt after another)."""
    data, at = polymarket.cached_events()
    if data is None:
        raise PolymarketNotInMemory("Polymarket not readable on the re-check (no event list in memory yet; a re-check "
                                    "never downloads one)")
    return polymarket.match_odds(team, opponent, events=data, events_at=at, start=start)


def _key_lock(key):
    with _REVAL_LOCK:
        lk = _REVAL_LOCKS.get(key)
        if lk is None:
            if len(_REVAL_LOCKS) > 5000:
                _REVAL_LOCKS.clear()
            lk = _REVAL_LOCKS[key] = threading.Lock()
        return lk


def _tp_max_age():
    try:
        import thunderpick
        return float(getattr(thunderpick, "QUOTE_MAX_AGE_S", 180))
    except Exception:              # noqa: BLE001
        return 180.0


def _memo_until(new):
    """A shared re-check result is reused for REVAL_TTL_S, never past the selected quote's own eligibility (Thunderpick:
    retrieval + QUOTE_MAX_AGE_S) or the match start."""
    until = time.time() + REVAL_TTL_S
    pv = new.get("provenance") or {}
    if pv.get("selected") == "thunderpick":
        ra = _ts(pv.get("retrieved_at"))
        if ra is not None:
            until = min(until, ra + _tp_max_age())
    st = _ts(pv.get("canonical_start") or new.get("start"))
    if st is not None:
        until = min(until, st)
    return until


def _tp_quote_in_window(o, now_ts):
    """The cached Thunderpick quote is still inside its OWN eligibility window: its retrieval time + QUOTE_MAX_AGE_S (the
    same clock WS1's 'current' uses - never extended) and before the match start. Unreadable times: not in window."""
    pv = o.get("provenance") or {}
    ra = _ts(pv.get("retrieved_at"))
    if ra is None or now_ts >= ra + _tp_max_age():
        return False
    st = _ts(pv.get("canonical_start") or o.get("start"))
    return st is None or now_ts < st


def _quality_fn(fn):
    """The grader's stake-cap class of an odds object (cs2_scout.market_quality: HARD / SOFT / NONE) - a liquidity or
    price-source change of the same price moves the 1.5U cap, so the re-check compares it too."""
    if fn is not None:
        return fn
    try:
        from cs2_scout import market_quality
        return market_quality
    except Exception:              # noqa: BLE001
        return None


def _qclass(fn, o):
    try:
        return fn(o) if fn else None
    except Exception:              # noqa: BLE001
        return None


def _decide(cached, new, now_ts, quality):
    """(odds to grade with, result words) for one re-check - the module doc's rules, in order."""
    pv, npv = cached.get("provenance") or {}, new.get("provenance") or {}
    same_input = npv.get("sig") == pv.get("sig") and _qclass(quality, new) == _qclass(quality, cached)
    # E1: a transient (blip) selection is never confirmed as the cached input - it must not freeze past the blip
    if (same_input and npv.get("tp_availability") == pv.get("tp_availability")
            and not cached.get("transient") and not new.get("transient")):
        return cached, "confirmed"
    if npv.get("tp_error"):         # the reader failed: Thunderpick's state is unknown, not "not current"
        if pv.get("selected") == "thunderpick":
            if _tp_quote_in_window(cached, now_ts):
                return cached, (f"kept: Thunderpick reader error ({npv['tp_error']}); the cached quote is still inside "
                                f"its own {int(_tp_max_age())} s window")
            # past its window: falls to the next source below - never a quote that is no longer current
        elif same_input and not cached.get("transient"):
            return cached, f"kept: Thunderpick reader error ({npv['tp_error']}); same market input"
    if npv.get("pm_error") and npv.get("selected") != "thunderpick" and pv.get("selected") != "thunderpick":
        return cached, f"kept: {npv['pm_error']}"
    if new.get("transient"):        # E1: a temporary fallback during a Thunderpick blip - not a new forecast input
        return copy.deepcopy(new), f"transient: {npv.get('fallback_reason')}"
    return copy.deepcopy(new), "changed"


def _check_record(new, result, shared, used):
    """What the re-check itself found and when (its sources' own times - nothing refreshed)."""
    npv = (new or {}).get("provenance") or {}
    return {"checked_at": npv.get("selected_at") or _iso(None), "result": result, "shared_evaluation": bool(shared),
            "found_selected": npv.get("selected"), "found_sig": npv.get("sig"),
            "tp_availability": npv.get("tp_availability"), "fallback_reason": npv.get("fallback_reason"),
            "tp_error": npv.get("tp_error"), "pm_error": npv.get("pm_error"), "blip": bool(npv.get("blip")),
            "transient": bool((used or {}).get("transient")), "tp_last_quote": npv.get("tp_last_quote"),
            # the selected source's latest answer as the re-check read it (Thunderpick: the quote's age at checked_at)
            "retrieved_at": npv.get("retrieved_at"), "quote_age_s": npv.get("quote_age_s"),
            "event_last_observed": npv.get("event_last_observed"),
            "endpoint_last_success_at": npv.get("endpoint_last_success_at"),
            "used_selected": ((used or {}).get("provenance") or {}).get("selected"),
            "used_sig": ((used or {}).get("provenance") or {}).get("sig")}


def recheck(cached, now=None, network=False, tp=None, pm=None, op=None, quality=None):
    """The cached selection re-checked now (module doc) -> (odds, check). odds: the cached object when nothing it depends
    on changed (or a failure is to be ignored), else the new selection (a new object; the cached one is never mutated).
    check: the record of this re-check (None for objects not made by select() - returned as is). quality: the stake-cap
    class function (default cs2_scout.market_quality)."""
    pv = (cached or {}).get("provenance") or {}
    q = pv.get("query")
    if not cached or not isinstance(q, dict):
        return cached, None
    shared = tp is None and pm is None and op is None and now is None
    key = (q.get("team"), q.get("opponent"), q.get("start"), str(q.get("bo")), bool(network), _mapping_rev())

    def run():
        return select(q.get("team"), q.get("opponent"), q.get("start"), now=now, tp=tp, pm=pm or _pm_in_memory,
                      bo=q.get("bo"), network=network, op=op, recheck=True)
    hit_used = False
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
                    new, hit_used = hit[2], True
                    if new is None:               # the shared evaluation FAILED: every waiter keeps its cached input
                        raise RuntimeError(hit[3])
                else:
                    at = time.time()
                    try:
                        new = run()
                    except Exception as e:        # noqa: BLE001 - remembered too (REVAL_TTL_S): waiters never re-run a
                        with _REVAL_LOCK:         # failing evaluation one after another
                            _REVAL[key] = (time.time() + REVAL_TTL_S, at, None, type(e).__name__)
                        raise
                    with _REVAL_LOCK:
                        if len(_REVAL) > 5000:
                            _REVAL.clear()
                        _REVAL[key] = (_memo_until(new), at, new, None)
    except Exception as e:         # noqa: BLE001 - a failure while re-checking never changes the grade's market input
        why = str(e) if hit_used else type(e).__name__
        return cached, {"checked_at": _iso(None), "result": f"failed ({why}): the context's odds kept",
                        "shared_evaluation": hit_used, "used_selected": pv.get("selected"), "used_sig": pv.get("sig")}
    now_ts = now if now is not None else time.time()
    out, result = _decide(cached, new, now_ts, _quality_fn(quality))
    return out, _check_record(new, result, hit_used, out)


def revalidate(cached, now=None, network=False, tp=None, pm=None, op=None, quality=None):
    """recheck() without the record: the odds to grade with (module doc)."""
    return recheck(cached, now=now, network=network, tp=tp, pm=pm, op=op, quality=quality)[0]


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
