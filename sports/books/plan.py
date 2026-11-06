"""Requests / credits per day from the games really on the schedule, before a refresh schedule is chosen.

Games: ESPN's NBA / NFL schedule for the next 7 days (the same games every provider lists). Per provider and ladder:
  fetches/day   = sum over games of schedule.fetches_in(ladder, start, now, 24 h)
  requests/day  = fetches x requests per game fetch (FanDuel: one per tab - NBA 5, NFL 3; BetRivers: 1; Odds API: 1)
                  + game-list refreshes (one per league every EVENTS_EVERY_MIN)
  credits/day   = Odds API fetches x markets per call (the documented cost is markets RETURNED x regions; the markets
                  requested are the upper bound, and the five named books count as one region)
The Odds API ladder is the densest whose credits/day fit SPORTS_ODDS_API_MONTHLY_CREDITS / 30 after the reserve
(SPORTS_ODDS_API_RESERVE, default 10% of the month).
"""
import os
from datetime import datetime, timedelta, timezone

from .. import config, espn
from . import fanduel, schedule, theoddsapi
from .store import iso

PLANS = {"Starter (free)": 500, "20K ($30/mo)": 20000, "100K ($59/mo)": 100000, "5M ($119/mo)": 5000000}


def monthly_credits():
    try:
        return int(os.environ.get("SPORTS_ODDS_API_MONTHLY_CREDITS") or 500)
    except ValueError:
        return 500


def reserve(monthly=None):
    m = monthly if monthly is not None else monthly_credits()
    try:
        return int(os.environ["SPORTS_ODDS_API_RESERVE"])
    except (KeyError, ValueError):
        return m // 10


def daily_allowance(monthly=None):
    m = monthly if monthly is not None else monthly_credits()
    return max(0, (m - reserve(m))) / 30.0


def games(leagues=("nba", "nfl"), now=None, days=7):
    """{league: [start ISO, ...]} - ESPN's schedule for the next `days` days, pre-game only."""
    now = now or datetime.now(timezone.utc)
    out = {}
    for key in leagues:
        lg = config.league(key)
        evs = espn.scoreboard(lg, now.date(), (now + timedelta(days=days)).date())
        out[key] = [e["start"] for e in evs if e.get("start") and not e.get("completed") and e["start"] > iso(now)]
    return out


def requests_per_fetch(provider, league):
    if provider == "fanduel":
        return len(fanduel.TABS.get(league, ()))
    return 1


def estimate(starts_by_league, now=None):
    """-> {"per_league": {...}, "odds_api": {ladder: credits/day}, "direct": {provider: requests/day}, ...}."""
    now = now or datetime.now(timezone.utc)
    lists_per_day = 24 * 60 // schedule.EVENTS_EVERY_MIN
    out = {"games": {k: len(v) for k, v in starts_by_league.items()}, "direct": {}, "odds_api": {}, "per_game": {}}
    direct = schedule.direct_ladder()                    # the books' own feeds' ladder (SPORTS_BOOKS_LADDER)
    out["direct_ladder"] = direct
    for prov in ("fanduel", "betrivers"):
        req = 0
        for key, starts in starts_by_league.items():
            f = sum(schedule.fetches_in(direct, s, now) for s in starts)
            req += f * requests_per_fetch(prov, key) + lists_per_day
        out["direct"][prov] = req
    for ladder in schedule.ORDER:
        credits = 0
        for key, starts in starts_by_league.items():
            per = theoddsapi.credits_per_event(config.league(key))
            credits += sum(schedule.fetches_in(ladder, s, now) for s in starts) * per
        out["odds_api"][ladder] = credits
        out["per_game"][ladder] = schedule.fetches_per_game(ladder)
    return out


def choose_ladder(est, monthly=None):
    """The densest Odds API ladder whose estimated credits/day fit the daily allowance; None if even 'minimal' does
    not fit (then the worker spends nothing and says so)."""
    allow = daily_allowance(monthly)
    for ladder in schedule.ORDER:
        if est["odds_api"][ladder] <= allow:
            return ladder
    return None
