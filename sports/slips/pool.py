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
  - the forecast has a chosen side that the offer really lists; a NON-research forecast also needs a grade and a
    positive stake (the grade policy's own output: a held 0U pick stays out, it is never relabelled)
  - sp-2 (owner choice 2026-10-24): a RESEARCH-ONLY forecast is eligible as a labelled research leg - the leg carries
    research_only=True and stake_units=0.0, Slip Studio shows the warning "Research estimates; stakes 0" and
    tags each such leg. A non-research forecast for the same offer is preferred when one exists.
  - the leg's team is known (the platforms' two-team rule must be checkable)
  - N4 (owner re-audit 2026-10-24, the board's lean rule): when the board snapshot has a market chance AT THIS
    offer's line (market_at, same rule as SportsRows.marketAt), Scout's chance of the chosen side must be ABOVE the
    market's chance of that side - a side the market prices higher is no lean and never a slip leg
  - N1/N2 guards (sports/board_view.py, member policy): a Scout (sm-1 / mi-1) forecast on a market whose model failed
    its registered gate (vg-1: NFL) is projection-only and never a leg - unless the owner released the league
    (ct-1b, sports/containment.py OWNER_RELEASE: NBA + NFL since 2026-11-08); a forecast held by the sg-1 sanity guard
    (projection under 25% / over 4x the line, or the last-10 results contradicting its side - the board's
    sanity.json holds) is excluded too
Policy "research" (admin only, labelled): any forecast (newest first, research or not) with a chosen side; grade /
stake not required.

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

from .. import availability, board_view, capabilities, containment, grade_rules, markets, store
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
              "stake_units", "weight", "prop_keys", "research_grade", "featured")
_LEG_SET = frozenset(LEG_FIELDS)
_INTERN = ("platform", "league", "model_version", "grade_policy_version", "event_id", "event_ref", "team", "stat",
           "stat_label", "scope", "side", "side_word", "variant", "start_utc", "observed_at", "last_seen_at", "grade",
           "research_grade")


def _leg_from_values(values):
    leg = Leg.__new__(Leg)
    values = tuple(values) + (None,) * (len(LEG_FIELDS) - len(values))     # a pool pickled before "featured" (bp-1)
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


def _forecasts_by_offer(con, league, now, event_ids=None, want=None):
    """Forecasts by the offer record id they were made for. Forecasting is incremental (LINE_CORRECTNESS.md C11): an
    unchanged offer revision keeps its first forecast however old, so forecasts are selected by the EVENTS of the
    current offers, not by a time window (the window is the fallback when no event list is given).
    Rows are decoded one at a time (prebuilt-mem 2026-11-18: every payload of the live events was held at once);
    want = the only offer record ids the caller will look up (build: the offers that reach the forecast step) - the
    other forecasts are dropped as they are read. Same lists, same order, for every wanted id."""
    if event_ids is None:
        since = _iso(now - timedelta(hours=FORECAST_LOOKBACK_H))
        queries = [("SELECT payload FROM records WHERE kind='forecast' AND league=? AND known_at >= ?",
                    (league, since))]
    else:
        ids = sorted(e for e in event_ids if e)
        queries = [(f"SELECT payload FROM records WHERE kind='forecast' AND league=? AND event_id IN "
                    f"({','.join('?' * len(ids[i:i + 400]))})", [league, *ids[i:i + 400]])
                   for i in range(0, len(ids), 400)]
    by = {}
    for sql, args in queries:
        for r in con.execute(sql, args):
            f = store._rec(r)
            for rid in f.offer_snapshot_ids:
                if want is None or rid in want:
                    by.setdefault(rid, []).append(f)
    return by


def _pick_forecast(fs, policy):
    """member (sp-2): the newest non-research forecast, else the newest research-only one (a labelled research leg).
    research (admin): the newest forecast of any kind."""
    if not fs:
        return None, "no forecast for this exact line"
    cand = list(fs)
    if policy == "member" and any(not f.research_only for f in cand):
        cand = [f for f in cand if not f.research_only]
    cand.sort(key=lambda f: (f.forecast_at, f.model_version, f.forecast_id), reverse=True)
    return cand[0], None


def market_by_record(boards):
    """{offer record id: (p_over, p_under)} - the market chance at each quote's own line from compact board snapshot
    objects (snapshots.compact_row "mk": {l, c, at}); mirrors SportsRows.marketAt (web/sports/table.js): at[rid] when
    present (None = no consensus), else the headline c when the quote is at the headline line; n > 0 only.
    The fields read here are the ones api_slips._slim keeps when it parses a board file: change both together."""
    out = {}
    for b in boards or ():
        for m in (b or {}).get("markets") or ():
            mk = m.get("mk") or {}
            at = mk.get("at") or {}
            for q in m.get("q") or ():
                rid = q.get("r")
                c = at[rid] if rid in at else (mk.get("c") if mk.get("l") is not None and mk.get("l") == q.get("l")
                                                else None)
                if c and (c.get("n") or 0) > 0 and c.get("po") is not None and c.get("pu") is not None:
                    out[rid] = (float(c["po"]), float(c["pu"]))
    return out


def cal_by_record(boards):
    """{offer record id: cal-mi-1 chance dict (po, pu, pp, f, v, mode)} from compact board snapshot objects (the
    quote's `ca`, REGISTER A8) - a slip leg uses the SAME calibrated chance the board shows for that forecast.
    The fields read here are the ones api_slips._slim keeps when it parses a board file: change both together."""
    out = {}
    for b in boards or ():
        for m in (b or {}).get("markets") or ():
            for q in m.get("q") or ():
                ca = q.get("ca")
                if q.get("r") and ca and ca.get("po") is not None and ca.get("pu") is not None:
                    out[q["r"]] = ca
    return out


def build(con, league, platform, now=None, policy="member", market_at=None, cal_at=None):
    """-> {"snapshot_id", "snapshot_at", "league", "platform", "policy", "legs": [leg], "excluded": {reason: n},
           "offers": n, "state": {offer_id: (record_id, revision, line, eligible, reason, leg_index)},
           "representation": "slim-1"}"""
    if policy not in POLICIES:
        raise ValueError("policy")
    now = now or datetime.now(timezone.utc)
    now_s = _iso(now)
    events = {e.event_id: e for _, e in store.events(con, league=league, limit=100000)}
    # STREAMED (prebuilt-mem 2026-11-18: the job was killed over its cap holding every current offer of the hot window -
    # ~60k OfferSnapshots for NBA PrizePicks with an archive backlog - plus every forecast payload of the live events).
    # Offers are read in the listing's order, 400 at a time with their metadata; each one gets the forecast-free checks
    # (_early) right away and only its state row is kept. An offer that passes them (open, gradable, not started, fresh:
    # the current slate, a few thousand) is kept whole and evaluated in full once the forecasts are read. The forecast
    # step is the first that reads fby, so the result is the same pool as reading everything first.
    rows, keep, live, buf = [], [], set(), []
    seen = {}

    def chunk():
        # Only metadata for the selected revisions is needed. Historical revisions can number in the millions.
        meta = {}
        for r in con.execute("SELECT record_id, revision, last_seen_at FROM records WHERE record_id IN "
                             f"({','.join('?' * len(buf))})", [rid for rid, _ in buf]):
            meta[r["record_id"]] = (r["revision"], r["last_seen_at"])
        for rid, o in buf:
            if o.availability == "open" and o.event_id and (events.get(o.event_id) is None or
                                                             (events[o.event_id].start_utc or "9") > now_s):
                live.add(o.event_id)
            rev, last = meta[rid] if rid in meta else (None, None)
            reason, _ = _early(rid, o, platform, events, {rid: last} if rid in meta else {}, now, now_s)
            if reason:
                rows.append((o.offer_id, rid, rev, o.line, reason, None))
            else:
                if rid in meta:
                    seen[rid] = last
                keep.append((len(rows), rid, o, rev))
                rows.append(None)
        buf.clear()

    for rid, o in store.iter_offers(con, league=league, platform=platform, limit=100000):
        buf.append((rid, o))
        if len(buf) == 400:
            chunk()
    if buf:
        chunk()
    fby = _forecasts_by_offer(con, league, now, live, want={rid for _, rid, _, _ in keep})
    held = _sanity_held(league) if policy == "member" else set()
    for i, rid, o, rev in keep:
        reason, leg = _evaluate(rid, o, platform, events, seen, fby, now, now_s, policy, market_at, held, cal_at)
        rows[i] = (o.offer_id, rid, rev, o.line, reason, leg)
    keep.clear()                                        # the kept offers and forecasts are garbage now
    fby = None
    legs, excluded, ids = [], Counter(), []
    for n, (oid, rid, rev, line, reason, leg) in enumerate(rows):
        if reason:
            reason = sys.intern(reason)
            excluded[reason.split(":")[0]] += 1
        else:
            leg = Leg(revision=rev, **leg)
            legs.append(leg)
            ids.append(f"{rid}|{leg['forecast_id']}")
        rows[n] = (oid, rid, rev, line, reason, leg)
    legs.sort(key=lambda l: (-l["p_win"], l["leg_id"]))
    index = {id(l): i for i, l in enumerate(legs)}
    state = {oid: (rid, rev, line, reason is None, reason, index[id(leg)] if leg is not None else -1)
             for oid, rid, rev, line, reason, leg in rows}
    sid = "ps-" + hashlib.sha256("|".join(sorted(ids) + [league, platform, policy]).encode()).hexdigest()[:16]
    return {"snapshot_id": sid, "snapshot_at": now_s, "league": league, "platform": platform, "policy": policy,
            "policy_version": POLICY_VERSION, "legs": legs, "excluded": dict(excluded), "offers": len(rows),
            "state": state, "representation": REPRESENTATION}


def leg_weight(platform, p_win, multiplier):
    """bs-2 (N8): log(p_win x payout multiplier) on Underdog (sum over a slip = log of the all-hit expected payout at
    the published base payout), log(p_win) on PrizePicks (no per-pick multiplier) or a missing multiplier."""
    m = float(multiplier) if platform == "underdog" and multiplier is not None and float(multiplier) > 0 else 1.0
    return math.log(p_win * m)


def _sanity_held(league):
    """{(record_id, forecast_id): reason code} the board's last snapshot held with sg-2 (the history rules need the
    history the board reads; the ratio / floor / certain rules are re-checked here directly). Reads the full "keys"
    list (never truncated), else the logged entries of an older file."""
    try:
        import json
        from ..snapshots import snap_root
        d = json.loads((snap_root() / league / "sanity.json").read_text(encoding="utf-8"))
        if isinstance(d.get("keys"), list):
            out = {(k[0], k[1]): k[2] for k in d["keys"] if isinstance(k, list) and len(k) == 3}
        else:
            out = {(e.get("record_id"), e.get("forecast_id")): e.get("reason") for e in d.get("entries") or ()}
        for (r, _f), c in list(out.items()):            # av-1 holds the LINE, whichever forecast a leg would use
            if c in availability.CODES:
                out[(r, None)] = c
        return out
    except Exception:                                   # noqa: BLE001 - no snapshot yet: the ratio rules still apply
        return {}


def lean_mismatch(po, pu, mean, line, side):
    """THE board lean rule applied to a slip leg (A9 audit 2026-10-28): a leg is the side the board leans to
    (board_view.lean_of - chance > 50% AND the projection mean on that side of the line). A forecast whose chosen side
    has the higher chance while its mean sits on the other side of the line is "No lean" on the board, so it is never
    a slip leg either. -> exclusion text or None (a forecast without a mean keeps the chance-only rule)."""
    if mean is None or side not in ("over", "under"):
        return None
    lean = board_view.lean_of(po, pu, mean, float(line))
    if lean == side:
        return None
    return ("no lean: the projection mean is on the other side of the line" if lean is None and
            (po if side == "over" else pu) > 0.5 else "no lean: the board does not lean to this side")


def guard_reason(f, league, rid=None, held=(), chances=()):
    """vg-1 / sg-2 for one forecast as a slip leg -> exclusion text or None (member policy). held = the board's
    {(record_id, forecast_id): reason} (or a set of pairs); chances = the leg's shown chances (cal-mi) beside the raw."""
    if isinstance(held, dict):                          # av-1: the board held the player's line (ruled out / inactive)
        av = held.get((rid, f.forecast_id)) or held.get((rid, None))
        if av in availability.CODES:
            return f"not playing: {availability.text(av)}"
    if f.model_version not in board_view.SCOUT_MODELS:
        return None
    if board_view.contain_reason(league, f.stat) or (board_view.gate_reason(league, f.stat)
                                                     and not containment.released(league)):
        return "model not validated for this market: projection only"     # contained (A9a 4) or failed gate
    # ct-1b (owner 2026-11-08): an owner-released league's failed-gate markets are legs like any other (stakes stay 0)
    side = f.chosen_side
    code = board_view.sanity_check(f.projection_mean, f.line, side if side in ("over", "under") else None,
                                   chances=(getattr(f, "p_over", None), getattr(f, "p_under", None)) + tuple(chances))
    if code is None and (rid, f.forecast_id) in held:
        code = (held.get((rid, f.forecast_id)) if isinstance(held, dict) else None) or "l10_contra"
        if code not in board_view.SANITY_TEXT:
            code = "l10_contra"
    return f"held by the sanity guard: {board_view.SANITY_TEXT[code][6:]}" if code else None


def _early(rid, o, platform, events, seen, now, now_s):
    """The checks of _evaluate that need no forecast (platform, gate, start, lock, freshness), in _evaluate's order.
    -> (exclusion text or None, start). build runs only these on every current offer while streaming; an offer
    that passes them is evaluated in full (_evaluate) once the forecasts are read."""
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
    return None, start


def _evaluate(rid, o, platform, events, seen, fby, now, now_s, policy, market_at=None, held=(), cal_at=None):
    early, start = _early(rid, o, platform, events, seen, now, now_s)
    if early:
        return early, None
    f, why = _pick_forecast(fby.get(rid, []), policy)
    if f is None:
        return why, None
    if abs(f.line - o.line) > 1e-9 or f.stat != o.stat or f.player_id != o.player_id:
        return "forecast is for another line", None
    side = f.chosen_side
    po, pu, pp = f.p_over, f.p_under, f.p_push
    ca = (cal_at or {}).get(rid)
    if ca and ca.get("f") == f.forecast_id and side is not None:   # cal-mi-1 (REGISTER A8): the board's chance
        po, pu, pp = ca["po"], ca["pu"], ca.get("pp", f.p_push)
        offered = [s_ for s_ in ("over", "under") if (o.side(s_) is not None and o.side(s_).offered)]
        side = max(offered, key=lambda s_: po if s_ == "over" else pu) if offered else side
    if side is None:
        return "no side chosen", None
    why = lean_mismatch(po, pu, f.projection_mean, o.line, side)
    if why:
        return why, None
    if policy == "member":
        g = guard_reason(f, o.league, rid, held, (po, pu))
        if g:
            return g, None
    q = o.side(side)
    if q is None or not q.offered:
        return "chosen side not offered", None
    if policy == "member" and not f.research_only:
        if f.grade is None:
            return "no grade", None
        if f.stake_units is None or f.stake_units <= 0:
            return "held stake (0U)", None
    if not o.team:
        return "team unknown: the two-team rule cannot be checked", None
    p_win = po if side == "over" else pu
    p_loss = pu if side == "over" else po
    if p_win <= 0:
        return "zero chance", None
    mk = (market_at or {}).get(rid)
    if mk is not None and p_win - (mk[0] if side == "over" else mk[1]) <= 1e-9:
        return "market prices Scout's side higher", None
    leg = {"leg_id": f"{platform}|{o.offer_id}|{side}", "platform": platform, "league": o.league,
           "offer_id": o.offer_id, "record_id": rid, "forecast_id": f.forecast_id, "model_version": f.model_version,
           "grade_policy_version": f.grade_policy_version, "research_only": f.research_only,
           "event_id": o.event_id, "event_ref": o.event_ref, "player_id": o.player_id, "player": o.player_name,
           "team": o.team, "stat": o.stat, "stat_label": o.source_stat, "scope": o.scope, "line": o.line,
           "side": side, "side_word": rules.SIDE_WORD[platform][side], "variant": o.variant,
           "promotional": o.promotional, "multiplier": q.multiplier, "start_utc": start,
           "observed_at": o.observed_at, "last_seen_at": max(x for x in (seen.get(rid), o.observed_at) if x),
           "source_time_unknown": o.source_time_unknown, "p_win": p_win, "p_loss": p_loss, "p_push": pp,
           "grade": None if f.research_only else f.grade,
           "stake_units": 0.0 if f.research_only else f.stake_units, "weight": leg_weight(platform, p_win, q.multiplier),
           "prop_keys": prop_keys(o.league, o.player_id, o.stat),
           # rg-1: the board's research letter for this forecast / line (same unrounded chance, same lean rule)
           # mvlean-1 / os-2 (grade_rules): the board's own letter on this quote; bp-1: the board's featured best prop
           "research_grade": grade_rules.letter_for(o.league, rid, side, board_view.research_grade(p_win, o.league, o.stat))
           if board_view.lean_of(po, pu, f.projection_mean, float(o.line)) == side else None,
           "featured": grade_rules.featured(o.league, rid, side)}
    return None, leg
