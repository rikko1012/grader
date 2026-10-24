"""How often each game's props are fetched, and when a stored quote counts as stale - one table for both, so the
board's "stale" label always matches what the worker promises.

A ladder is [(hours to start, minutes between fetches), ...]: a game 90 minutes away is fetched every 10 minutes on
the "full" ladder; a game further out than the last rung is not fetched at all. Started games are never fetched
(pre-game prices only). Direct book feeds (no credits) use "full". The Odds API uses the densest ladder whose
estimated credits fit SPORTS_ODDS_API_MONTHLY_CREDITS (plan.py works it out from the games really listed).

Stale = not seen within 2 x the current rung's interval (at least STALE_MIN_S). A quote is shown with its own
observation time; it is never labelled current because it sits in storage.
"""
from datetime import timedelta

from .store import dt

LADDERS = {
    "full": [(2, 10), (8, 30), (24, 60), (72, 180), (168, 720)],
    "standard": [(2, 20), (8, 60), (24, 180), (72, 720)],
    "lean": [(3, 60), (12, 180), (36, 720)],
    "minimal": [(6, 360)],
}
ORDER = ("full", "standard", "lean", "minimal")


def direct_ladder():
    """Ladder for the books' own feeds (no credits, but every changed price is a stored row - ~700 bytes each in
    SQLite): SPORTS_BOOKS_LADDER, default "full"."""
    import os
    v = (os.environ.get("SPORTS_BOOKS_LADDER") or "full").strip().lower()
    return v if v in LADDERS else "full"


EVENTS_EVERY_MIN = 30          # provider game lists (free / one request) - refreshed at most this often
STALE_MIN_S = 20 * 60


def interval_min(ladder, start, now):
    """Minutes between fetches for a game starting at `start` (ISO) as of `now`; None = not fetched (started / beyond
    the ladder's horizon)."""
    s = dt(start)
    if not s or s <= now:
        return None
    hours = (s - now).total_seconds() / 3600
    for max_h, every in LADDERS[ladder]:
        if hours <= max_h:
            return every
    return None


def next_due(ladder, start, now):
    every = interval_min(ladder, start, now)
    return None if every is None else now + timedelta(minutes=every)


def stale_after_s(ladder, start, now):
    every = interval_min(ladder, start, now)
    return max(STALE_MIN_S, 2 * 60 * (every or 60))


def fetches_in(ladder, start, now, hours=24):
    """How many fetches this ladder makes for one game in the next `hours` (the plan's per-day estimate)."""
    t, end, n = now, now + timedelta(hours=hours), 0
    s = dt(start)
    while s and t < min(end, s):
        every = interval_min(ladder, start, t)
        if every is None:
            # beyond the horizon: jump to when the game enters the ladder
            enter = s - timedelta(hours=LADDERS[ladder][-1][0])
            if enter <= t:
                break
            t = enter
            continue
        n += 1
        t += timedelta(minutes=every)
    return n


def fetches_per_game(ladder):
    """Fetches over a game's whole pre-game life on this ladder (steady state)."""
    total, lo = 0, 0
    for max_h, every in LADDERS[ladder]:
        total += int((max_h - lo) * 60 // every)
        lo = max_h
    return total
