"""Canonical identities for teams, events and players - exact and corroborated, never fuzzy (owner brief 3).

Canonical ids
  team    "<league>:team:espn:<ESPN team id>"
  event   "<league>:espn:<ESPN event id>"
  player  "<league>:player:espn:<ESPN athlete id>"         (resolved)
          "<league>:player:prov:<team abbr>:<names.key>"   (PROVISIONAL - never graded; Player.resolved False)

Rules
- A team abbreviation maps to ESPN's only by an exact entry in team_aliases.json (or by being ESPN's own).
- An event is found by league + US calendar date (Eastern, DST-correct) + BOTH teams; one missing team or two candidate
  games = unresolved, with the reason.
- A player is found by exact names.key on the roster of HIS team in that event. Two roster players with one key, or
  a name on neither team, = unresolved. Without a team (some Underdog lines) both teams of the event are searched
  and exactly one match is required.
- Kalshi's event/market times are expected EXPIRATION times (start + ~3 h on 2026-10-23), never starts: Kalshi is
  matched by the date in its ticker + both team codes.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import names

_ALIASES = Path(__file__).resolve().parent / "team_aliases.json"
_ALIAS_CACHE = {}


# ----------------------------------------------------------------------------------------------------- time zones
def _nth_sunday(year, month, n):
    d = datetime(year, month, 1, tzinfo=timezone.utc)
    first = d + timedelta(days=(6 - d.weekday()) % 7)
    return first + timedelta(weeks=n - 1)


def eastern_offset_hours(t: datetime) -> int:
    """US Eastern UTC offset at instant t (-4 EDT / -5 EST), US rules since 2007: DST from the 2nd Sunday of March
    07:00 UTC (2 am EST) to the 1st Sunday of November 06:00 UTC (2 am EDT). No tz database needed."""
    t = t.astimezone(timezone.utc)
    start = _nth_sunday(t.year, 3, 2) + timedelta(hours=7)
    end = _nth_sunday(t.year, 11, 1) + timedelta(hours=6)
    return -4 if start <= t < end else -5


def us_date(start_utc: str | None) -> str | None:
    """The US (Eastern) calendar date of a UTC instant - the date leagues, ESPN and Kalshi tickers file a game under."""
    if not start_utc:
        return None
    t = datetime.fromisoformat(start_utc.replace("Z", "+00:00")).astimezone(timezone.utc)
    return (t + timedelta(hours=eastern_offset_hours(t))).date().isoformat()


# ----------------------------------------------------------------------------------------------------------- teams
def _alias_table(league):
    if league not in _ALIAS_CACHE:
        try:
            data = json.loads(_ALIASES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        _ALIAS_CACHE[league] = {k.upper(): v for k, v in (data.get(league) or {}).items()}
    return _ALIAS_CACHE[league]


def team_id(league, espn_id):
    return f"{league}:team:espn:{espn_id}"


class Teams:
    """ESPN's teams for one league: abbr -> team_id, plus the exact alias table."""

    def __init__(self, league, espn_teams):
        """espn_teams: [{"id", "abbr", "name"}] (ESPN team list or scoreboard competitors)."""
        self.league = league
        self.by_abbr = {t["abbr"].upper(): team_id(league, t["id"]) for t in espn_teams if t.get("abbr")}
        self.abbr_of = {v: k for k, v in self.by_abbr.items()}

    def resolve(self, abbr, on_date=None):
        """Source abbreviation -> canonical team_id or None."""
        if not abbr:
            return None
        a = str(abbr).strip().upper()
        if a in self.by_abbr:
            return self.by_abbr[a]
        ent = _alias_table(self.league).get(a)
        if not ent:
            return None
        if on_date and ent.get("valid_to") and on_date > ent["valid_to"]:
            return None                       # alias closed (relocation / rename); valid_from = first corroboration only
        return self.by_abbr.get(ent["espn"].upper())


# ---------------------------------------------------------------------------------------------------------- events
class EventIndex:
    def __init__(self, events, teams: Teams):
        """events: Event records (one league)."""
        self.teams = teams
        self.by_key = {}
        self.events = {}
        for e in events:
            self.events[e.event_id] = e
            day = us_date(e.start_utc)
            if not day:
                continue
            k = (day, frozenset((e.home_team_id, e.away_team_id)))
            self.by_key.setdefault(k, []).append(e.event_id)
            for r in e.reschedules:           # a rescheduled game is still found under its original date
                od = us_date(r.from_utc)
                if od and od != day:
                    self.by_key.setdefault((od, frozenset((e.home_team_id, e.away_team_id))), []).append(e.event_id)

    def resolve(self, day, team_a, team_b):
        """-> (event_id, None) or (None, reason). team_a / team_b are source abbreviations."""
        if not day:
            return None, "no date"
        ta, tb = self.teams.resolve(team_a, day), self.teams.resolve(team_b, day)
        if not ta or not tb:
            miss = [x for x, t in ((team_a, ta), (team_b, tb)) if not t]
            return None, f"team abbreviation not mapped: {', '.join(str(m) for m in miss)}"
        ids = sorted(set(self.by_key.get((day, frozenset((ta, tb))), [])))
        if not ids:
            return None, f"no ESPN game {team_a} / {team_b} on {day}"
        if len(ids) > 1:
            return None, "two ESPN games match (double-header?)"
        return ids[0], None


def parse_game(game):
    """'NYK @ BOS' -> ('NYK', 'BOS') (away, home); 'NYK vs BOS (Oct 23)' (Kalshi, away listed first) -> same."""
    if not game:
        return None, None
    g = str(game).split(" (")[0]
    for sep in (" @ ", " vs ", " vs. ", " at "):
        if sep in g:
            a, _, h = g.partition(sep)
            return a.strip() or None, h.strip() or None
    return None, None


# --------------------------------------------------------------------------------------------------------- players
def player_id(league, espn_id):
    return f"{league}:player:espn:{espn_id}"


def provisional_player_id(league, team_abbr, name):
    return f"{league}:player:prov:{(team_abbr or '?').upper()}:{names.key(name)}"


class Rosters:
    """team_id -> {player_key: [player_id, ...]} from ESPN rosters / box scores (active players)."""

    def __init__(self):
        self.by_team = {}

    def add(self, team, pid, name):
        self.by_team.setdefault(team, {}).setdefault(names.key(name), set()).add(pid)

    def resolve(self, name, team_ids):
        """-> (player_id, None) or (None, reason). team_ids: the candidate teams (the offer's team, else both)."""
        k = names.key(name)
        hits = set()
        for t in team_ids:
            hits |= self.by_team.get(t, {}).get(k, set())
        if len(hits) == 1:
            return next(iter(hits)), None
        if not hits:
            return None, "name not on the roster of the event's team(s)"
        return None, "two roster players share this name"
