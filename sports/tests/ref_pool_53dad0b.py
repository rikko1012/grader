"""REFERENCE ONLY (tests): the slip pool build (sports/slips/pool.py build / _forecasts_by_offer / _pick_forecast /
_evaluate) exactly as on origin/main 53dad0b - it held every current offer of the hot window as an OfferSnapshot and
every forecast row of the live events at once. The streamed build must return the same pool, pickle-byte for pickle-
byte (sports/tests/test_prebuilt_mem_1118.py)."""
import hashlib
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from sports import capabilities, store
from sports.contracts import parse_utc
from sports.slips import POLICY_VERSION, rules
from sports.slips.pool import (FORECAST_LOOKBACK_H, POLICIES, REPRESENTATION, STALE_S, Leg, _iso, _sanity_held,
                               board_view, guard_reason, lean_mismatch, leg_weight, prop_keys)


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
    """member (sp-2): the newest non-research forecast, else the newest research-only one (a labelled research leg).
    research (admin): the newest forecast of any kind."""
    if not fs:
        return None, "no forecast for this exact line"
    cand = list(fs)
    if policy == "member" and any(not f.research_only for f in cand):
        cand = [f for f in cand if not f.research_only]
    cand.sort(key=lambda f: (f.forecast_at, f.model_version, f.forecast_id), reverse=True)
    return cand[0], None


def build(con, league, platform, now=None, policy="member", market_at=None, cal_at=None):
    """-> {"snapshot_id", "snapshot_at", "league", "platform", "policy", "legs": [leg], "excluded": {reason: n},
           "offers": n, "state": {offer_id: (record_id, revision, line, eligible, reason, leg_index)},
           "representation": "slim-1"}"""
    if policy not in POLICIES:
        raise ValueError("policy")
    now = now or datetime.now(timezone.utc)
    now_s = _iso(now)
    offers = store.offers(con, league=league, platform=platform, limit=100000)
    seen, revs = {}, {}
    # Only metadata for the selected revisions is needed. Historical revisions can number in the millions.
    rids = [rid for rid, _ in offers]
    for i in range(0, len(rids), 400):
        part = rids[i:i + 400]
        for r in con.execute("SELECT record_id, revision, last_seen_at FROM records WHERE record_id IN "
                             f"({','.join('?' * len(part))})", part):
            seen[r["record_id"]] = r["last_seen_at"]
            revs[r["record_id"]] = r["revision"]
    events = {e.event_id: e for _, e in store.events(con, league=league, limit=100000)}
    live = {o.event_id for _, o in offers if o.availability == "open" and o.event_id and
            (events.get(o.event_id) is None or (events[o.event_id].start_utc or "9") > now_s)}
    fby = _forecasts_by_offer(con, league, now, live)
    held = _sanity_held(league) if policy == "member" else set()
    legs, excluded, rows = [], Counter(), []
    ids = []
    for rid, o in offers:
        reason, leg = _evaluate(rid, o, platform, events, seen, fby, now, now_s, policy, market_at, held, cal_at)
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


def _evaluate(rid, o, platform, events, seen, fby, now, now_s, policy, market_at=None, held=(), cal_at=None):
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
        return "market prices Grader's side higher", None
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
           "research_grade": board_view.research_grade(p_win)
           if board_view.lean_of(po, pu, f.projection_mean, float(o.line)) == side else None}
    return None, leg
