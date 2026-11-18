"""Slip Studio HTTP handlers (sports service process; route lines in sports/web.py ROUTES).

  GET       /api/sports/slips/rules        published platform rules used (sources, page dates, verified flags)
  GET       /api/sports/slips/pool         ?league=&platform=  eligible legs (policy sp-2) + exclusion counts +
                                           research_legs (research-only forecasts, stake 0, labelled)
  GET|POST  /api/sports/slips/build        league, platform + builder settings (JSON body or ?settings=<json>)
  POST      /api/sports/slips/validate     {"mode": "load"|"copy", "slip": {...}} - copy text ONLY on ok
  POST      /api/sports/slips/settle       {"slip": {...}} - current settlement of a saved slip (read-only)
  GET|POST  /api/sports/admin/slips/build  admin: also policy=research and the INTERNAL payout expectation
  GET       /api/sports/admin/slips/log    admin: spool counters

Cheap by construction: the leg snapshot is shared per (league, platform) through web.cached (single flight, POOL_TTL),
builds are cached per (snapshot, settings) and at most BUILD_CONCURRENCY run at once (else 503 sports_busy);
validation and settlement read the shared snapshot / a bounded read-only connection. Handlers never write the store:
records go to the bounded slip spool (sports/slips/log.py), loaded by the worker.
"""
from __future__ import annotations

import json
import threading

from . import store, web
from .slips import builder, joint, log, pool as poolmod, rules, settle, validate

LEAGUES = ("nba", "nfl")
POOL_TTL = 60
BUILD_TTL = 60
BUILD_CONCURRENCY = 2
_BUILD_SLOTS = threading.BoundedSemaphore(BUILD_CONCURRENCY)
MEMBER_LEG_FIELDS = tuple(f for f in builder.LEG_FIELDS)
LEGS_MAX_IN = 4 * validate.MAX_LEGS     # a posted slip larger than this is refused unread (validate names 9-32 legs)


def _bad_request(fn):
    """GET and POST alike: a malformed request is a 400 with the reason (never a 503)."""
    def wrap(req):
        try:
            return fn(req)
        except (ValueError, KeyError, TypeError) as e:
            return 400, {"error": "bad_request", "detail": str(e)[:160]}
    wrap.__name__ = fn.__name__
    wrap.__doc__ = fn.__doc__
    return wrap


def _args(req):
    q = dict(req.query or {})
    if req.method == "POST":
        body = req.json()
        if not isinstance(body, dict):
            raise ValueError("JSON object expected")
        q.update(body)
    return q


def _ctx(q):
    league, platform = q.get("league"), q.get("platform")
    if league not in LEAGUES:
        raise ValueError("league must be nba or nfl")
    if platform not in rules.PLATFORMS:
        raise ValueError("platform must be underdog or prizepicks")
    return league, platform


def _live_current(platform, offer_id):
    """One indexed read per leg on COPY only (<= 8 legs): the store's current offer revision right now."""
    with web.store_ro(budget_s=2) as con:
        return store.current_record_id(con, "offer", platform, offer_id)


_QUOTE_KEYS = frozenset(("r", "p", "l", "v", "o", "t", "g"))      # snapshots.compact_quote (always present)
_ROW_KEYS = frozenset(("k", "e", "pid", "st", "q", "mk"))          # snapshots.compact_row
_MK_KEYS = frozenset(("l", "c", "at", "bk"))                       # market_view.row_view
_FC_KEYS = frozenset(("m", "mu", "po", "pu", "at", "ro", "rv", "ln"))   # snapshots.compact_forecasts entry


def _slim(pairs):
    """json object_pairs_hook for a board file read only for market_by_record / cal_by_record: each object is cut to
    the fields those two read AS IT IS PARSED, so the whole board (every quote's ~15 fields, every market's book list,
    the forecast table) is never held at once (prebuilt-mem 2026-11-18: the NBA board parse was the job's largest
    phase and its survivors pinned the freed heap under the pool builds). Recognised only by the full key set each
    writer always emits; any other object is returned unchanged. Same mk / ca values as the full parse."""
    d = dict(pairs)
    ks = d.keys()
    if _QUOTE_KEYS <= ks:                                    # a quote (q / kn): its record, line and cal chance
        return {k: d[k] for k in ("r", "l", "ca") if k in d}
    if _ROW_KEYS <= ks:                                      # a market row: its quotes and market view
        return {"q": d["q"], "mk": d["mk"]}
    if _MK_KEYS <= ks:                                       # a market view: headline line, consensus, per-quote at
        return {"l": d["l"], "c": d["c"], "at": d["at"]}
    if _FC_KEYS <= ks:                                       # the board's forecast table: not read here
        return None
    return d


def _board_markets(league, cached=True):
    """Market chance (N4 lean rule) and cal-mi-1 chance per offer record from the precomputed board snapshots; no
    snapshot -> {}. One board parsed at a time, slimmed while parsed (_slim); cached=False (worker prebuild) skips
    the service file cache."""
    from . import api_board
    mk, ca = {}, {}
    for name in ("board.json", "board_more.json"):
        p = api_board._root() / league / name
        try:
            b = json.loads(api_board._read(p) if cached else p.read_bytes(), object_pairs_hook=_slim)
        except (api_board.NotReady, ValueError, OSError):
            continue
        mk.update(poolmod.market_by_record([b]))
        ca.update(poolmod.cal_by_record([b]))
        del b
    return mk, ca


def _pool(league, platform, policy="member"):
    def load():
        mk, ca = _board_markets(league)
        with web.store_ro(budget_s=15) as con:
            return poolmod.build(con, league, platform, policy=policy, market_at=mk, cal_at=ca)
    return web.cached(("slips_pool", league, platform, policy), load, ttl=POOL_TTL)


def public_schedule(s):
    return None if s is None else {k: v for k, v in s.items() if k != "internal"}


def public_slip(s):
    out = dict(s, payout=public_schedule(s["payout"]))
    er = s.get("expected_return")
    if er and not er.get("shown"):                         # an unpublished combined payout: rank only, no figure
        out["expected_return"] = {"value": None, "shown": False, "basis": None,
                                  "why": er.get("why") or ("Underdog adjusts this payout for the picks' multipliers; "
                                                           "the app shows the exact figure")}
    out["payout_key"] = validate.payout_key(s["payout"])
    return out


def public_batch(b):
    return dict({k: v for k, v in b.items() if k not in ("slips",)}, slips=[public_slip(s) for s in b["slips"]])


def _internal(b):
    """The internal expectation per slip (logged; admin only)."""
    out = []
    for s in b["slips"]:
        e = joint.expectation(s["platform"], s["entry_type"], s["legs"])
        out.append({"slip_id": s["slip_id"], "payout_internal": s["payout"].get("internal"), "expectation": e})
    return out


def _build(q, policy):
    league, platform = _ctx(q)
    settings = q.get("settings") if "settings" in q else {k: q[k] for k in q if k not in ("league", "platform")}
    if isinstance(settings, str):
        settings = json.loads(settings)
    st = builder.normalize(settings, platform)            # SettingsError (ValueError) -> 400
    pool = _pool(league, platform, policy)
    key = ("slips_build", pool["snapshot_id"], policy, json.dumps(st, sort_keys=True, default=str))

    def load():
        if not _BUILD_SLOTS.acquire(timeout=2.0):
            raise web.Busy("slip builds saturated")
        try:
            b = builder.build(pool, st)
        finally:
            _BUILD_SLOTS.release()
        b["internal"] = _internal(b)
        log.spool("batch", {k: v for k, v in b.items()})
        return b
    return web.cached(key, load, ttl=BUILD_TTL)


@_bad_request
def rules_view(req):
    def load():
        out = {"rules_version": rules.RULES_VERSION, "read_at": rules.READ_AT, "platforms": {}}
        for p in rules.PLATFORMS:
            rs = {}
            for name, r in rules.RULES[p].items():
                src = rules.SOURCES.get(r.get("src")) if r.get("src") else None
                rs[name] = {"verified": r["verified"], "quote": r.get("quote"), "note": r.get("note"),
                            "source": src[0] if src else None, "page_date": src[1] if src else None}
            out["platforms"][p] = {"label": rules.LABEL[p], "entry_types": {
                et: rules.size_range(p, et) for et in rules.ENTRY_TYPES[p]}, "side_words": rules.SIDE_WORD[p],
                "rules": rs}
        return out
    return 200, web.cached(("slips_rules",), load, ttl=600)


@_bad_request
def pool_view(req):
    q = _args(req)
    league, platform = _ctx(q)
    p = _pool(league, platform)
    legs = [{k: l.get(k) for k in MEMBER_LEG_FIELDS} for l in p["legs"]]
    return 200, {"league": league, "platform": platform, "snapshot_at": p["snapshot_at"], "snapshot_id": p["snapshot_id"],
                 "policy_version": p["policy_version"], "offers": p["offers"], "eligible": len(legs), "legs": legs,
                 "research_legs": sum(1 for l in legs if l.get("research_only")),
                 "excluded": p["excluded"], "stale_after_s": poolmod.STALE_S}


@_bad_request
def build(req):
    b = _build(_args(req), "member")
    return 200, public_batch({k: v for k, v in b.items() if k != "internal"})


@_bad_request
def admin_build(req):
    q = _args(req)
    policy = q.pop("policy", "member")
    if policy not in poolmod.POLICIES:
        raise ValueError("policy must be member or research")
    b = _build(q, policy)
    return 200, b


def _slip_in(q):
    slip = q.get("slip")
    if isinstance(slip, str):
        slip = json.loads(slip)
    if not isinstance(slip, dict):
        raise ValueError("slip object expected")
    if slip.get("league") not in LEAGUES or slip.get("platform") not in rules.PLATFORMS:
        raise ValueError("slip league / platform invalid")
    if not isinstance(slip.get("legs"), list):
        raise ValueError("slip legs expected")
    if len(slip["legs"]) > LEGS_MAX_IN:
        raise ValueError(f"at most {LEGS_MAX_IN} legs")
    if not all(isinstance(l, dict) for l in slip["legs"]):
        raise ValueError("each leg is an object")       # was: AttributeError deep in the check -> 503
    return slip


def _offer_lookup(platform, offer_id):
    """The current offer from the store (bounded read-only pool, request budget) - only for a leg whose offer revision
    changed, so the answer can name what changed. Any store trouble -> None (the slim message is used instead)."""
    import sqlite3

    from . import store
    try:
        with web.store_ro() as con:
            return store.latest(con, "offer", platform, offer_id)
    except (web.Busy, FileNotFoundError, OSError, sqlite3.Error):
        return None


@_bad_request
def validate_view(req):
    q = _args(req)
    slip = _slip_in(q)
    mode = q.get("mode") or "load"
    p = _pool(slip["league"], slip["platform"])
    res = validate.check(slip, p, mode=mode, offer_lookup=_offer_lookup,
                         live_current=_live_current if mode == "copy" else None)
    log.spool("copied" if mode == "copy" else "loaded",
              {"member": log.member_hash(req.member), "league": slip["league"], "ok": res["ok"],
               "problems": [x["code"] for x in res["problems"]], "snapshot_id": p["snapshot_id"],
               "builder_version": slip.get("builder_version"), "batch_id": slip.get("batch_id"),
               "slip": {"slip_id": slip.get("slip_id") or builder._slip_id(slip["platform"],
                                                                          slip.get("entry_type") or "", [
                            {"leg_id": f"{l.get('platform')}|{l.get('offer_id')}|{l.get('side')}",
                             "record_id": str(l.get("record_id"))} for l in slip["legs"]]),
                        "platform": slip["platform"], "entry_type": slip.get("entry_type"),
                        "legs": [c for c in res["current"] if c] if res["ok"] else slip["legs"]},
               "payout": public_schedule(res.get("payout"))})
    res = dict(res, payout=public_schedule(res.get("payout")))
    return 200, res


@_bad_request
def settle_view(req):
    q = _args(req)
    slip = _slip_in(q)
    legs = slip["legs"]
    if not 1 <= len(legs) <= validate.MAX_LEGS:         # one store read per leg: bounded like a copy check
        raise ValueError(f"a slip holds 1-{validate.MAX_LEGS} picks")
    for l in legs:
        if not all(isinstance(l.get(k), str) and l.get(k) for k in ("platform", "offer_id", "side")):
            raise ValueError("each leg needs platform, offer_id and side")
    with web.store_ro() as con:
        by = settle.current_settlements(con, legs)
    res = settle.settle_slip({"platform": slip["platform"], "entry_type": slip.get("entry_type") or
                              rules.DEFAULT_ENTRY[slip["platform"]], "legs": legs}, by)
    from . import line_moves                    # SCOUT_LINE_MOVE_NOTES: a settled leg says when its line was bumped (wording only)
    if line_moves.enabled():
        views = [{"result": {"status": lr.get("status"), "value": lr.get("value")}, "line": l.get("line")}
                 for l, lr in zip(legs, res.get("leg_results") or [])]
        line_moves.annotate(views, [(l["platform"], l["offer_id"]) for l in legs], web.store_ro)
        for lr, v in zip(res.get("leg_results") or [], views):
            if v.get("line_move"):
                lr["line_move"] = v["line_move"]
    return 200, res


@_bad_request
def admin_log(req):
    return 200, log.stats()
