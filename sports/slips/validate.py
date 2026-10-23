"""Server-side revalidation of a slip on LOAD and before COPY - the same contract as the esports copy check (closeout2
copyval): the page writes the clipboard ONLY after this answer says ok, with the text this answer returns. A rejected
or failed check leaves the clipboard untouched and shows the reason.

check(slip, pool, mode, now) re-checks every leg against the CURRENT snapshot (pool.build):
  offer still listed (platform + provider offer id); same offer REVISION (record id) - else what changed is named
  (line, side offered, variant, payout multiplier, listed start, availability); the side; not started / locked (the
  real clock, not the snapshot's); fresh (observed age); still eligible under the slip policy; the SAME forecast (id)
  and side; a custom line is hypothetical and never copyable.
  Slip level: one platform (mixed platforms refused), the platform's published entry rules, and the payout schedule
  (its key must match what the member saw, else "payout changed").
Any change -> {"ok": False, "action": "rebuild"}: nothing is substituted silently; each changed leg carries its current
version so the member can explicitly reconfirm it (a new check is then required).
copy_text never implies the entry was placed: it says the slip still has to be entered and checked in the app.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from .. import identity
from ..contracts import parse_utc
from . import builder, joint, pool as poolmod, rules

MAX_LEGS = 8
MODES = ("load", "copy")


def payout_key(schedule):
    raw = json.dumps([schedule.get("platform"), schedule.get("entry_type"), schedule.get("picks"),
                      schedule.get("member"), schedule.get("figures_shown")], sort_keys=True, default=str)
    return "pk-" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _et(start_utc):
    if not start_utc:
        return "start unknown"
    t = parse_utc(start_utc)
    off = identity.eastern_offset_hours(t)
    lt = t + timedelta(hours=off)
    return lt.strftime("%a %b %d %I:%M %p").replace(" 0", " ") + (" EDT" if off == -4 else " EST")


def _label(l):
    return f"{l.get('player') or l.get('offer_id')} {l.get('stat_label') or l.get('stat') or ''} {l.get('line')}".strip()


def _changes(leg, o):
    ch = []
    if o.line != leg.get("line"):
        ch.append(f"line moved {leg.get('line')} -> {o.line}")
    if leg.get("variant") and o.variant != leg.get("variant"):
        ch.append(f"line type changed ({leg.get('variant')} -> {o.variant})")
    q = o.side(leg.get("side"))
    if q is None or not q.offered:
        ch.append(f"{leg.get('side')} is no longer offered")
    elif leg.get("multiplier") is not None and q.multiplier != leg.get("multiplier"):
        ch.append(f"payout multiplier changed ({leg.get('multiplier')} -> {q.multiplier})")
    if leg.get("start_utc") and o.listed_start_utc and o.listed_start_utc != leg.get("start_utc") \
            and o.listed_time_kind == "start":
        ch.append(f"start moved to {_et(o.listed_start_utc)}")
    if o.availability != "open":
        ch.append(f"offer {o.availability}")
    return ch or ["the offer changed"]


def check(slip, pool, mode="load", now=None):
    now = now or datetime.now(timezone.utc)
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    if mode not in MODES:
        raise ValueError("mode must be load or copy")
    platform, entry_type = slip.get("platform"), slip.get("entry_type") or rules.DEFAULT_ENTRY.get(slip.get("platform"))
    legs_in = list(slip.get("legs") or [])
    problems, current = [], []
    if not 1 <= len(legs_in) <= MAX_LEGS:
        problems.append({"leg": None, "code": "size", "text": f"A slip holds 1-{MAX_LEGS} picks"})
        legs_in = legs_in[:MAX_LEGS]
    plats = {l.get("platform") for l in legs_in} | {platform}
    if len(plats) > 1:
        problems.append({"leg": None, "code": "platforms", "text": "One platform per slip - these picks are on " +
                         " and ".join(sorted(rules.LABEL.get(p, str(p)) for p in plats if p))})
    for i, leg in enumerate(legs_in):
        lab = _label(leg)
        st = pool["state"].get(leg.get("offer_id")) if leg.get("platform") == pool["platform"] else None
        if leg.get("custom"):
            problems.append({"leg": i, "code": "custom", "text": f"{lab}: custom line - hypothetical, not offered; "
                             "it can't be copied as an entry"})
            current.append(None)
            continue
        if st is None:
            problems.append({"leg": i, "code": "gone", "text": f"{lab}: no longer on the {rules.LABEL.get(platform, platform)} board"})
            current.append(None)
            continue
        cur = st.get("leg")
        if st["record_id"] != leg.get("record_id"):
            for c in _changes(leg, st["offer"]):
                problems.append({"leg": i, "code": "changed", "text": f"{lab}: {c}"})
        start = (cur or leg).get("start_utc")
        if not start or start <= now_s:
            problems.append({"leg": i, "code": "locked", "text": f"{lab}: game started / locked"})
        if cur is not None:
            age = (now - parse_utc(cur["last_seen_at"])).total_seconds()
            if age > poolmod.STALE_S:
                problems.append({"leg": i, "code": "stale", "text": f"{lab}: price not fresh (seen {int(age // 60)} min ago)"})
        if not st["eligible"]:
            if st["reason"] != "started / locked":
                problems.append({"leg": i, "code": "policy", "text": f"{lab}: not a Scout slip pick now ({st['reason']})"})
        elif cur["side"] != leg.get("side"):
            problems.append({"leg": i, "code": "side", "text": f"{lab}: Scout's side is now {cur['side_word']}"})
        elif cur["forecast_id"] != leg.get("forecast_id"):
            problems.append({"leg": i, "code": "forecast", "text": f"{lab}: the forecast was updated - reconfirm"})
        current.append({k: cur.get(k) for k in builder.LEG_FIELDS} if cur else None)
    legs_now = [c for c in current if c is not None]
    entry = rules.entry_check(legs_now if len(legs_now) == len(legs_in) else
                              [dict(l, platform=l.get("platform")) for l in legs_in], platform, entry_type)
    for r in entry["refusals"]:
        problems.append({"leg": None, "code": "entry", "text": r})
    sched = rules.schedule(platform, entry_type, legs_now) if len(legs_now) == len(legs_in) and legs_in else None
    if sched is not None and slip.get("payout_key") and payout_key(sched) != slip.get("payout_key"):
        problems.append({"leg": None, "code": "payout", "text": "The payout for this slip changed - check it below"})
    ok = not problems
    out = {"ok": ok, "action": "copy" if ok else "rebuild", "mode": mode, "checked_at": now_s,
           "snapshot_at": pool["snapshot_at"], "snapshot_id": pool["snapshot_id"], "problems": problems,
           "current": current, "payout": sched, "payout_key": payout_key(sched) if sched else None,
           "joint": joint.joint(legs_now) if sched else None, "entry_check": entry}
    if ok and mode == "copy":
        out["copy_text"] = copy_text(platform, entry_type, legs_now)
    return out


def copy_text(platform, entry_type, legs):
    words = rules.SIDE_WORD[platform]
    lines = [f"{rules.LABEL[platform]} - {len(legs)}-pick {entry_type} - Scout Sports slip (not entered)"]
    for i, l in enumerate(legs, 1):
        lines.append(f"{i}. {l['player']} ({l['team']}) - {l.get('event_ref') or ''} - {_et(l['start_utc'])} - "
                     f"{l.get('stat_label') or l['stat']} {words[l['side']]} {l['line']}")
    lines.append("Enter it yourself in the app and check every line there first: lines move, and a changed line "
                 "needs a new check.")
    return "\n".join(lines)
