"""The slip-leg snapshot: every current pick'em offer of one platform + league, joined to the forecast made for that
exact offer revision, with the slip policy applied (sp-1). Read-only on the canonical store.

Policy sp-1 (member): a leg is eligible only when ALL hold - otherwise it is excluded with ONE counted reason, never
substituted:
  - the platform is a pick'em platform (Underdog / PrizePicks; Kalshi is an exchange, not an entry)
  - capabilities.gate(offer) passes (supported full-game single stat, ordinary line, resolved event + player, open)
  - the game has not started (event start, else the platform's listed start; an event status other than scheduled
    locks it) and its start is known
  - the offer was seen by ingestion within STALE_S (server observation age; the source's own time is often unknown)
  - a forecast exists FOR THIS OFFER REVISION (its offer_snapshot_ids name the current record id) on the same line;
    a forecast of an older revision never carries to a moved line
  - the forecast is not research-only, has a chosen side that the offer really lists, a grade and a positive stake
    (the grade policy's own output: a held 0U pick stays out, it is never relabelled)
  - the leg's team is known (the platforms' two-team rule must be checkable)
Policy "research" (admin only, labelled): research-only forecasts with a chosen side are allowed; grade / stake not
required. Never shown to members.

One leg per offer: the forecast's chosen side only (the opposite side of a forecast is never a slip leg).
Exposure keys: a leg's PROP keys are (player, box-score part) for every part of its market (markets.MARKETS), so an
alternate line, another platform's copy or a combo containing the same part all collide in the reuse rules.

Compact representation (slim-1, 2026-10-24 - the snapshot is loaded into the sports service, budget-capped):
  legs     eligible legs only, as Leg records (__slots__, dict-style read access: leg["p_win"], leg.get("team"))
  excluded reason COUNTS
  state    {offer_id: (record_id, revision, line, eligible, reason, leg_index)} for EVERY current offer - a plain
           tuple per offer (ST_* positions); reason is the exclusion text (interned) or None; leg_index points into
           legs (-1 when not eligible). No OfferSnapshot is kept: validate looks the current offer up in the store
           when it needs to name what changed (validate.check offer_lookup).
"""
from __future__ import annotations

import hashlib
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from .. import capabilities, markets, store
from ..contracts import SPORT_OF, parse_utc
from . import POLICY_VERSION, rules

STALE_S = 25 * 60            # ingestion runs every 10 min; two missed cycles + margin = not fresh
FORECAST_LOOKBACK_H = 72
POLICIES = ("member", "research")
REPRESENTATION = "slim-1"
ST_RECORD, ST_REVISION, ST_LINE, ST_ELIGIBLE, ST_REASON, ST_LEG = range(6)

LEG_FIELDS = ("leg_id", "platform", "league", "offer_id", "record_id", "revision", "forecast_id", "model_version",
              "grade_policy_version", "research_only", "event_id", "event_ref", "player_id", "player", "team", "stat",
              "stat_label", "scope", "line", "side", "side_word", "variant", "promotional", "multiplier", "start_utc",
              "observed_at", "last_seen_at", "source_time_unknown", "p_win", "p_loss", "p_push", "grade",
              "stake_units", "weight", "prop_keys")
_LEG_SET = frozenset(LEG_FIELDS)
_INTERN = ("platform", "league", "model_version", "grade_policy_version", "event_id", "event_ref", "team", "stat",
           "stat_label", "scope", "side", "side_word", "variant", "start_utc", "observed_at", "last_seen_at", "grade")


def _leg_from_values(values):
    leg = Leg.__new__(Leg)
    for k, v in zip(LEG_FIELDS, values):
        object.__setattr__(leg, k, v)
    return leg


class Leg:
    """One eligible slip leg. __slots__ record with read-only dict-style access (leg["x"], leg.get("x"), keys()) so
    the builder / validator / API code reads it exactly like the former dict. Pickles as a plain value tuple."""
    __slots__ = LEG_FIELDS

    def __init__(self, **kw):
        for k in LEG_FIELDS:
            v = kw.get(k)
            if k in _INTERN and isinstance(v, str):
                v = sys.intern(v)
            object.__setattr__(self, k, v)

    def __getitem__(self, k):
        if k not in _LEG_SET:
            raise KeyError(k)
        return getattr(self, k)

    def get(self, k, default=None):
        return getattr(self, k) if k in _LEG_SET else default

    def __contains__(self, k):
        return k in _LEG_SET

    def keys(self):
        return LEG_FIELDS

    def to_dict(self):
        return {k: getattr(self, k) for k in LEG_FIELDS}

    def __reduce__(self):
        return _leg_from_values, (tuple(getattr(self, k) for k in LEG_FIELDS),)

    def __repr__(self):
        return f"Leg({self.leg_id})"


def offer_state(pool, offer_id):
    """-> (record_id, revision, line, eligible, reason, leg) for one current offer, or None. leg = the Leg or None."""
    st = pool["state"].get(offer_id)
    if st is None:
        return None
    li = st[ST_LEG]
    return st[:ST_LEG] + ((pool["legs"][li] if li >= 0 else None),)


def _iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def prop_keys(league, player_id, stat):
    spec = markets.market(SPORT_OF.get(league), stat) or {"parts": (stat,)}
    return tuple(f"{player_id}|{p}" for p in spec["parts"])


def _forecasts_by_offer(con, league, now, event_ids=None):
    """Forecasts by the offer record id they were made for. Forecasting is incremental (LINE_CORRECTNESS.md C11): an
    unchanged offer revision keeps its first forecast however old, so forecasts are selected by the EVENTS of the
    current offers, not by a time window (the window is the fallback when no event list is given)."""
    if event_ids is None:
        since = _iso(now - timedelta(hours=FORECAST_LOOKBACK_H))
        rows = con.execute("SELECT payload FROM records WHERE kind='forecast' AND league=? AND known_at >= ?",
                           (league, since)).fetchall()
    else:
        ids, rows = sorted(e for e in event_ids if e), []
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            rows += con.execute(f"SELECT payload FROM records WHERE kind='forecast' AND league=? AND event_id IN "
                                f"({','.join('?' * len(chunk))})", [league, *chunk]).fetchall()
    by = {}
    for r in rows:
        f = store._rec(r)
        for rid in f.offer_snapshot_ids:
            by.setdefault(rid, []).append(f)
    return by


def _pick_forecast(fs, policy):
    cand = [f for f in fs if policy == "research" or not f.research_only]
    if not cand:
        return None, ("research forecast only" if fs else "no forecast for this exact line")
    cand.sort(key=lambda f: (f.forecast_at, f.model_version, f.forecast_id), reverse=True)
    return cand[0], None


def build(con, league, platform, now=None, policy="member"):
    """-> {"snapshot_id", "snapshot_at", "league", "platform", "policy", "legs": [leg], "excluded": {reason: n},
           "offers": n, "state": {offer_id: (record_id, revision, line, eligible, reason, leg_index)},
           "representation": "slim-1"}"""
    if policy not in POLICIES:
        raise ValueError("policy")
    now = now or datetime.now(timezone.utc)
    now_s = _iso(now)
    offers = store.offers(con, league=league, platform=platform, limit=100000)
    seen, revs = {}, {}
    for r in con.execute("SELECT record_id, revision, last_seen_at FROM records WHERE kind='offer' AND league=? AND "
                         "platform=?", (league, platform)).fetchall():
        seen[r["record_id"]] = r["last_seen_at"]
        revs[r["record_id"]] = r["revision"]
    events = {e.event_id: e for _, e in store.events(con, league=league, limit=100000)}
    live = {o.event_id for _, o in offers if o.availability == "open" and o.event_id and
            (events.get(o.event_id) is None or (events[o.event_id].start_utc or "9") > now_s)}
    fby = _forecasts_by_offer(con, league, now, live)
    legs, excluded, rows = [], Counter(), []
    ids = []
    for rid, o in offers:
        reason, leg = _evaluate(rid, o, platform, events, seen, fby, now, now_s, policy)
        if reason:
            reason = sys.intern(reason)
            excluded[reason.split(":")[0]] += 1
        else:
            leg = Leg(revision=revs.get(rid), **leg)
            legs.append(leg)
            ids.append(f"{rid}|{leg['forecast_id']}")
        rows.append((o.offer_id, rid, revs.get(rid), o.line, reason, leg))
    del offers
    legs.sort(key=lambda l: (-l["p_win"], l["leg_id"]))
    index = {id(l): i for i, l in enumerate(legs)}
    state = {oid: (rid, rev, line, reason is None, reason, index[id(leg)] if leg is not None else -1)
             for oid, rid, rev, line, reason, leg in rows}
    sid = "ps-" + hashlib.sha256("|".join(sorted(ids) + [league, platform, policy]).encode()).hexdigest()[:16]
    return {"snapshot_id": sid, "snapshot_at": now_s, "league": league, "platform": platform, "policy": policy,
            "policy_version": POLICY_VERSION, "legs": legs, "excluded": dict(excluded), "offers": len(rows),
            "state": state, "representation": REPRESENTATION}


def _evaluate(rid, o, platform, events, seen, fby, now, now_s, policy):
    if platform not in rules.PLATFORMS:
        return "not a pick'em platform", None
    ok, why = capabilities.gate(o)
    if not ok:
        return f"not gradable: {why[0]}", None
    ev = events.get(o.event_id)
    start = ev.start_utc if ev and ev.start_utc else (o.listed_start_utc if o.listed_time_kind == "start" else None)
    if start is None:
        return "start unknown", None
    if ev is not None and ev.status != "scheduled":
        return f"game {ev.status}", None
    if start <= now_s:
        return "started / locked", None
    last = max(x for x in (seen.get(rid), o.observed_at) if x)
    age = (now - parse_utc(last)).total_seconds()
    if age > STALE_S:
        return f"price not fresh: last seen {int(age // 60)} min ago", None
    f, why = _pick_forecast(fby.get(rid, []), policy)
    if f is None:
        return why, None
    if abs(f.line - o.line) > 1e-9 or f.stat != o.stat or f.player_id != o.player_id:
        return "forecast is for another line", None
    side = f.chosen_side
    if side is None:
        return "no side chosen", None
    q = o.side(side)
    if q is None or not q.offered:
        return "chosen side not offered", None
    if policy == "member":
        if f.grade is None:
            return "no grade", None
        if f.stake_units is None or f.stake_units <= 0:
            return "held stake (0U)", None
    if not o.team:
        return "team unknown: the two-team rule cannot be checked", None
    p_win = f.p_over if side == "over" else f.p_under
    p_loss = f.p_under if side == "over" else f.p_over
    if p_win <= 0:
        return "zero chance", None
    leg = {"leg_id": f"{platform}|{o.offer_id}|{side}", "platform": platform, "league": o.league,
           "offer_id": o.offer_id, "record_id": rid, "forecast_id": f.forecast_id, "model_version": f.model_version,
           "grade_policy_version": f.grade_policy_version, "research_only": f.research_only,
           "event_id": o.event_id, "event_ref": o.event_ref, "player_id": o.player_id, "player": o.player_name,
           "team": o.team, "stat": o.stat, "stat_label": o.source_stat, "scope": o.scope, "line": o.line,
           "side": side, "side_word": rules.SIDE_WORD[platform][side], "variant": o.variant,
           "promotional": o.promotional, "multiplier": q.multiplier, "start_utc": start,
           "observed_at": o.observed_at, "last_seen_at": max(x for x in (seen.get(rid), o.observed_at) if x),
           "source_time_unknown": o.source_time_unknown, "p_win": p_win, "p_loss": p_loss, "p_push": f.p_push,
           "grade": f.grade, "stake_units": f.stake_units, "weight": math.log(p_win),
           "prop_keys": prop_keys(o.league, o.player_id, o.stat)}
    return None, leg
