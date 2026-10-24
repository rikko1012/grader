"""Canonical games: every provider's game is mapped to ESPN's event id, the id the rest of Sports already uses (slate,
box scores, settlement). Books name teams differently ("NY Knicks @ BOS Celtics", "New York Knicks @ Boston Celtics",
"PIT Steelers vs NO Saints"), but each prints the team nickname ESPN lists ("Knicks", "Trail Blazers", "76ers").

A provider game matches an ESPN game when BOTH nicknames appear as whole words in the provider's team names and the
starts are within START_TOLERANCE_H (FanDuel lists tip-off + 10 min; Kambi 15 min early). Two ESPN games matching = no
match. An unmatched game keeps event_key "unmatched:<provider>:<id>" and is never compared across books.
"""
import re
from datetime import timedelta

from .. import espn
from .store import dt

START_TOLERANCE_H = 4


def _has(text, nick):
    return bool(nick) and re.search(rf"(^|\W){re.escape(nick.lower())}(\W|$)", (text or "").lower()) is not None


def match(espn_events, provider, event):
    """-> canonical event_key for one provider Event."""
    t = dt(event.start)
    hits = []
    for e in espn_events:
        h, a = e.get("home") or {}, e.get("away") or {}
        if not e.get("start") or not t or abs((dt(e["start"]) - t).total_seconds()) > START_TOLERANCE_H * 3600:
            continue
        names = f"{event.home} | {event.away} | {event.name}"
        if _has(names, h.get("nick")) and _has(names, a.get("nick")):
            hits.append(e)
    return f"espn:{hits[0]['id']}" if len(hits) == 1 else f"unmatched:{provider}:{event.provider_event_id}"


HORIZON_DAYS = 8               # games further out are mapped on a later list refresh, once they come into range
CHUNK_DAYS = 3                 # ESPN refuses long date ranges (400 for a two-month span, 2026-10-23)


def espn_window(lg, events):
    """ESPN's games covering the providers' listed starts, asked in CHUNK_DAYS ranges (cached)."""
    starts = sorted(dt(e.start) for e in events if e.start)
    if not starts:
        return []
    day, last, out = (starts[0] - timedelta(days=1)).date(), (starts[-1] + timedelta(days=1)).date(), []
    while day <= last:
        end = min(last, day + timedelta(days=CHUNK_DAYS - 1))
        out += espn.scoreboard(lg, day, end)
        day = end + timedelta(days=1)
    return out


def keys_for(lg, provider, events, now=None):
    """{provider_event_id: event_key} for a provider's game list (games inside HORIZON_DAYS; the rest unmatched
    until a later refresh)."""
    from datetime import datetime, timezone
    now = now or datetime.now(timezone.utc)
    near = [e for e in events if e.start and now - timedelta(hours=6) <= dt(e.start) <= now + timedelta(days=HORIZON_DAYS)]
    try:
        evs = espn_window(lg, near)
    except Exception:                                  # ESPN down: games stay unmatched (never wrongly merged)
        evs = []
    keys = {e.provider_event_id: f"unmatched:{provider}:{e.provider_event_id}" for e in events}
    keys.update({e.provider_event_id: match(evs, provider, e) for e in near})
    return keys
