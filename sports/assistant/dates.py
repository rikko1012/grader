"""Explicit date routing (owner brief 11: "route dates explicitly").

resolve(phrase, league, now) -> {"phrase", "rule", "start_utc", "end_utc", "us_dates", "timezone", "assumption"} or
None. Ranges are half-open [start_utc, end_utc) in UTC. Sports days are US Eastern calendar days (the day leagues, ESPN
and Kalshi file a game under - identity.us_date), so 'last night' = every game whose Eastern date is yesterday. The
resolved range is ECHOED to the member and to the model; tools never take a vague phrase, only UTC bounds.

Season windows (rule ds-1, stated in every answer that uses one):
  NBA / WNBA / CBB  season year Y = Sep 1 Y 00:00 ET .. Aug 1 Y+1 00:00 ET, Y = this year from September, else last year
  NFL / CFB         Aug 1 Y 00:00 ET .. Mar 1 Y+1 00:00 ET, Y = this year from August, else last year
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from ..identity import eastern_offset_hours

RULE_VERSION = "ds-1"
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                            "nov", "dec"))}
# A real month name or abbreviation as a whole word (bug sweep 2026-11-06 #3 / #33: "[a-z]*" after a 3-letter prefix
# read "Mark 3", "markets 2", "Junior 5", "declined 4" as dates). Shared with grounding._MDAY.
MONTH_WORD = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
              r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b")
_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10, "fourteen": 14,
            "thirty": 30}


def _z(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def eastern_midnight_utc(d: date) -> datetime:
    """00:00 US Eastern on calendar day d, as a UTC instant (DST-exact: the offset in force at that local midnight)."""
    guess = datetime(d.year, d.month, d.day, 5, 0, tzinfo=timezone.utc)      # 00:00 EST
    off = eastern_offset_hours(guess - timedelta(hours=1))
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - timedelta(hours=off)


def eastern_today(now: datetime) -> date:
    now = now.astimezone(timezone.utc)
    return (now + timedelta(hours=eastern_offset_hours(now))).date()


def _days(d0: date, d1: date, phrase, rule, assumption=None):
    """Eastern days d0 .. d1 inclusive."""
    s, e = eastern_midnight_utc(d0), eastern_midnight_utc(d1 + timedelta(days=1))
    days = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    return {"phrase": phrase, "rule": f"{RULE_VERSION}:{rule}", "start_utc": _z(s), "end_utc": _z(e),
            "us_dates": days if len(days) <= 14 else [days[0], "...", days[-1]], "timezone": "US/Eastern game days",
            "assumption": assumption}


def season_window(league: str, now: datetime):
    t = eastern_today(now)
    if league in ("nfl", "cfb"):
        y = t.year if t.month >= 8 else t.year - 1
        d0, d1 = date(y, 8, 1), date(y + 1, 3, 1)
        label = f"{y} {league.upper()} season"
    else:
        y = t.year if t.month >= 9 else t.year - 1
        d0, d1 = date(y, 9, 1), date(y + 1, 8, 1)
        label = f"{y}-{str(y + 1)[2:]} {league.upper()} season"
    return d0, d1 - timedelta(days=1), label


def resolve(phrase: str, league: str = "nba", now: datetime | None = None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    p = re.sub(r"\s+", " ", str(phrase or "").strip().lower())
    today = eastern_today(now)
    if not p:
        return None
    if p in ("last night", "yesterday", "last nights games", "last night's games"):
        y = today - timedelta(days=1)
        return _days(y, y, phrase, "yesterday", "games filed under yesterday's US Eastern date")
    if p in ("today", "tonight", "today's games", "tonights games", "tonight's games"):
        return _days(today, today, phrase, "today")
    if p in ("tomorrow",):
        t = today + timedelta(days=1)
        return _days(t, t, phrase, "tomorrow")
    if p in ("this season", "season", "this year", "season to date"):
        d0, d1, label = season_window(league, now)
        d1 = min(d1, today)
        return _days(d0, d1, phrase, "season", f"{label} window (rule {RULE_VERSION}), through today")
    if p in ("last season", "previous season"):
        d0, d1, label = season_window(league, now - timedelta(days=365))
        return _days(d0, d1, phrase, "last_season", f"{label} window (rule {RULE_VERSION})")
    if p in ("this week",):
        d0 = today - timedelta(days=today.weekday())
        return _days(d0, today, phrase, "this_week", "Monday through today, US Eastern")
    if p in ("last week", "past week"):
        return _days(today - timedelta(days=7), today - timedelta(days=1), phrase, "last_7_days",
                     "the 7 Eastern days before today")
    m = re.fullmatch(r"(?:last|past) (\d{1,3}|\w+) days?", p)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _WORDNUM.get(m.group(1))
        if n and 1 <= n <= 366:
            return _days(today - timedelta(days=n), today - timedelta(days=1), phrase, f"last_{n}_days",
                         f"the {n} Eastern days before today")
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", p)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        return _days(d, d, phrase, "date")
    m = re.fullmatch(r"(" + MONTH_WORD + r")\.? (\d{1,2})(?:st|nd|rd|th)?(?:,? (\d{4}))?", p)
    if m:
        y = int(m.group(3)) if m.group(3) else today.year
        try:
            d = date(y, _MONTHS[m.group(1)[:3]], int(m.group(2)))
        except ValueError:
            return None
        if not m.group(3) and d > today + timedelta(days=60):       # "Dec 30" asked in January = last December
            d = date(y - 1, d.month, d.day)
        return _days(d, d, phrase, "date", None if m.group(3) else f"year {d.year} assumed")
    return None


_SCAN = re.compile(r"\b(last night(?:'s games)?|yesterday|tonight|today|tomorrow|this season|last season|this week|"
                   r"last week|past week|(?:last|past) (?:\d{1,3}|one|two|three|four|five|six|seven|ten|fourteen|thirty) "
                   r"days?|\d{4}-\d{2}-\d{2}|" + MONTH_WORD + r"\.? "
                   r"\d{1,2}(?:st|nd|rd|th)?(?:,? \d{4})?)\b", re.I)


def scan(question: str, league: str, now: datetime | None = None):
    """Every date phrase in a question, resolved (the orchestrator echoes these before any tool runs)."""
    out, seen = [], set()
    for m in _SCAN.finditer(question or ""):
        ph = m.group(0)
        if ph.lower() in seen:
            continue
        seen.add(ph.lower())
        r = resolve(ph, league, now)
        if r:
            out.append(r)
    return out
