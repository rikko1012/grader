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
import math
import os
import re
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

from .. import identity
from ..names import team_abbr
from ..contracts import parse_utc
from . import builder, joint, pool as poolmod, rules

MAX_LEGS = 8
MODES = ("load", "copy")

# PP_OPEN_LINK (2026-10-28, owner switch, default OFF): an ok COPY answer also carries PrizePicks' own lineup link
# (?projections=<projectionId>-<o|u>-<line_score>,...; the member's PrizePicks lineup builder opens with these picks and
# the member enters it) or, for Underdog (no pre-fill link exists), its pick'em home. Built only from the checked current
# legs; never an invite_code / wager_id. Same rules as the esports pplink.py (sports does not import CS2 modules).
PP_BOARD = "https://app.prizepicks.com/board"
UD_HOME = "https://app.underdogsports.com/pick-em/higher-lower/all/home"
_PP_ID = re.compile(r"^pp:([0-9]{1,15})$")


def open_link_on(env=None):
    return (os.environ if env is None else env).get("PP_OPEN_LINK", "").strip() == "1"


def pp_link(legs):
    """Checked current legs -> the PrizePicks lineup URL, or None: 2-6 legs, every leg platform prizepicks with offer_id
    'pp:<digits>', side exactly over / under, a goblin / demon only over, a finite line >= 0, no projection twice."""
    if not isinstance(legs, list) or not 2 <= len(legs) <= 6:
        return None
    toks, seen = [], set()
    for l in legs:
        m = _PP_ID.match(str((l or {}).get("offer_id") or ""))
        if not m or l.get("platform") != "prizepicks" or m.group(1) in seen:
            return None
        seen.add(m.group(1))
        sd = {"over": "o", "under": "u"}.get(l.get("side"))
        if sd is None or (str(l.get("variant") or "standard").lower() in ("goblin", "demon") and sd != "o"):
            return None
        try:
            x = float(l.get("line"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(x) or x < 0:
            return None
        toks.append(f"{m.group(1)}-{sd}-{int(x) if x.is_integer() else repr(x)}")
    return PP_BOARD + "?projections=" + quote(",".join(toks), safe="-,.")


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


def _changes_slim(leg, line):
    """What changed when only the slim snapshot is at hand (no store lookup): the line, else a generic change."""
    if line != leg.get("line"):
        return [f"line moved {leg.get('line')} -> {line}"]
    return ["the offer changed"]


def check(slip, pool, mode="load", now=None, offer_lookup=None, live_current=None):
    """offer_lookup(platform, offer_id) -> the current OfferSnapshot (or None): used only for a leg whose offer
    revision changed, to name exactly what changed (the slim snapshot keeps no offer objects).
    live_current(platform, offer_id) -> the store's CURRENT record id right now (optional): the pool is a cached
    snapshot (prebuilt <= 90 s), so a copy re-checks each leg's revision live - a line that moved since the snapshot
    is "changed", never copied (LINE_CORRECTNESS.md C5)."""
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
        st = poolmod.offer_state(pool, leg.get("offer_id")) if leg.get("platform") == pool["platform"] else None
        if leg.get("custom"):
            problems.append({"leg": i, "code": "custom", "text": f"{lab}: custom line - hypothetical, not offered; "
                             "it can't be copied as an entry"})
            current.append(None)
            continue
        if st is None:
            problems.append({"leg": i, "code": "gone", "text": f"{lab}: no longer on the {rules.LABEL.get(platform, platform)} board"})
            current.append(None)
            continue
        rid, _rev, line, eligible, reason, cur = st
        if rid != leg.get("record_id"):
            o = offer_lookup(leg.get("platform"), leg.get("offer_id")) if offer_lookup else None
            for c in (_changes(leg, o) if o is not None else _changes_slim(leg, line)):
                problems.append({"leg": i, "code": "changed", "text": f"{lab}: {c}"})
        elif live_current is not None and live_current(leg.get("platform"), leg.get("offer_id")) != rid:
            problems.append({"leg": i, "code": "changed", "text": f"{lab}: the line changed moments ago - refresh"})
        start = (cur or leg).get("start_utc")
        pool_locked = not eligible and reason == "started / locked"   # the pool's verdict counts when it holds no
        if not isinstance(start, str) or not start or start <= now_s or pool_locked:   # leg (client time not trusted)
            problems.append({"leg": i, "code": "locked", "text": f"{lab}: game started / locked"})
        if cur is not None:
            age = (now - parse_utc(cur["last_seen_at"])).total_seconds()
            if age > poolmod.STALE_S:
                problems.append({"leg": i, "code": "stale", "text": f"{lab}: price not fresh (seen {int(age // 60)} min ago)"})
        if not eligible:
            if reason != "started / locked":
                problems.append({"leg": i, "code": "policy", "text": f"{lab}: not a Scout slip pick now ({reason})"})
        elif cur["side"] != leg.get("side"):
            problems.append({"leg": i, "code": "side", "text": f"{lab}: Scout's side is now {cur['side_word']}"})
        elif cur["forecast_id"] != leg.get("forecast_id"):
            problems.append({"leg": i, "code": "forecast", "text": f"{lab}: the forecast was updated - reconfirm"})
        current.append(builder.leg_view(cur) if cur else None)
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
        if open_link_on():
            if platform == "prizepicks":
                link = pp_link(legs_now)
                if link:
                    out["pp_link"] = link
            elif platform == "underdog":
                out["open_url"] = UD_HOME
    if ok and platform == "underdog" and open_link_on():
        # each checked leg's own Underdog line link (opens that line's market screen; the member taps Higher / Lower)
        from . import udlink
        links = [udlink.link_for(l.get("league"), l.get("offer_id")) for l in legs_now]
        if any(links):
            out["legs_open"] = links
    return out


def _stat_words(l):
    """#12: the canonical market label (markets.py), the book's raw stat name only when the key is unknown."""
    from .. import markets
    from ..contracts import SPORT_OF
    spec = markets.market(SPORT_OF.get(l.get("league")), l.get("stat")) if l.get("stat") else None
    if spec and spec.get("label"):
        return spec["label"]
    raw = l.get("stat_label") or l.get("stat") or ""
    return raw.replace("_", " ").capitalize() if "_" in raw else raw


def copy_text(platform, entry_type, legs):
    words = rules.SIDE_WORD[platform]
    lines = [f"{rules.LABEL[platform]} - {len(legs)}-pick {entry_type} - Scout Sports slip (not entered)"]
    for i, l in enumerate(legs, 1):
        lines.append(f"{i}. {l['player']} ({team_abbr(l.get('league'), l['team']) or l['team']}) - {l.get('event_ref') or ''} - {_et(l['start_utc'])} - "
                     f"{_stat_words(l)} {words[l['side']]} {l['line']}")
    if any(l.get("research_only") for l in legs):
        lines.append("Research estimates; stakes 0.")
    lines.append("Enter it yourself in the app and check every line there first: lines move, and a changed line "
                 "needs a new check.")
    return "\n".join(lines)
