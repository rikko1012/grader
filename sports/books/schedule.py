"""How often each game's props are fetched, and when a stored quote counts as stale - one table for both, so the
board's "stale" label always matches what the worker promises.

A ladder is [(hours to start, minutes between fetches), ...]: a game 90 minutes away is fetched every 3 minutes on
the "direct" ladder; a game further out than the last rung is not fetched at all. Started games are never fetched
(pre-game prices only). Direct book feeds (FanDuel / BetRivers own feeds, no credits) use "direct"
(SPORTS_BOOKS_LADDER). The Odds API uses the densest of ORDER whose estimated credits fit
SPORTS_ODDS_API_MONTHLY_CREDITS (plan.py works it out from the games really listed); "direct" is never one of them.

"direct" (owner 2026-11-06, book-cadence): every game starting within 24 h is re-read often enough that its quotes stay
inside the consumers' freshness rules - the board's market chance (15 min, sports/market_view.py), the Tools +EV
consensus (10 min) and EV pricing (30 min, gradecore/market_tools.py EV_STALE_S). The rules are not loosened; the
fetch is faster: 3 min inside 2 h of the start, 5 min inside 8 h, 8 min inside 24 h. The books job (sports/scheduler.py
"books", sports.worker books) checks every minute on a game day, so a quote is at most rung + ~1 min old when read.
Earlier "full" (10 / 30 / 60 min) left NBA / NFL quotes 16-60 min old, over every one of those rules.
Owner site audit 2026-11-06 N1: the 24-72 h rung was 180 min, so Sunday NFL games read on a Thursday / Friday held quotes up
to ~3 h old and the board's Market edge, Best prices and Price check had nothing fresh to read (NFL showed no Market edge
row). That rung is now 8 min too, so every game within 72 h stays inside the same rules (quote at most 8 min + one ~1-min
job check old; Market edge / Best prices read workspace_ev.price_limits(), 29 min). The 72-168 h rung stays 12-hourly:
books rarely list props that far out, and a 10-min read of every game of next week would multiply the requests for lines
almost nobody prices yet. Request cost (books.plan.estimate, averaged over a week every 3 h: FanDuel 5 tabs per NBA game /
3 per NFL game, BetRivers 1 per game; 10 NBA games a night + a 16-game NFL week): FanDuel ~590 -> ~1,330 requests/hour
(peak ~880 -> ~1,590), BetRivers ~130 -> ~290 (peak ~230 -> ~375); both well under net.py's per-host spacing (FanDuel
2/s = 7,200/h, Kambi 1/s = 3,600/h). The books job also checks every minute while a game is within 72 h (scheduler
books_gameday_h).

Stale = not seen within 2 x the current rung's interval (at least STALE_MIN_S). A quote is shown with its own
observation time; it is never labelled current because it sits in storage.
"""
from datetime import timedelta

from .store import dt

LADDERS = {
    "direct": [(2, 3), (8, 5), (24, 8), (72, 8), (168, 720)],
    "full": [(2, 10), (8, 30), (24, 60), (72, 180), (168, 720)],
    "standard": [(2, 20), (8, 60), (24, 180), (72, 720)],
    "lean": [(3, 60), (12, 180), (36, 720)],
    "minimal": [(6, 360)],
}
ORDER = ("full", "standard", "lean", "minimal")       # the Odds API plan's choices (credits), densest first
DIRECT_DEFAULT = "direct"


def direct_ladder():
    """Ladder for the books' own feeds (no credits, but every CHANGED price is a stored row - ~700 bytes each in
    SQLite; an unchanged one only moves last_seen_at): SPORTS_BOOKS_LADDER, default "direct"."""
    import os
    v = (os.environ.get("SPORTS_BOOKS_LADDER") or DIRECT_DEFAULT).strip().lower()
    return v if v in LADDERS else DIRECT_DEFAULT


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
