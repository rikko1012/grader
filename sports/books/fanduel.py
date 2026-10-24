"""FanDuel Sportsbook - its public sportsbook API (sbapi.<state>.sportsbook.fanduel.com), the JSON fanduel.com's own
pages read. No login; it answered a plain, honestly identified request from an Ohio server on 2026-10-23 (FanDuel is
licensed in Ohio, so no location is worked around). The `_ak` parameter is the web app's public application key, not a
user credential. Endpoints as used by sjhouston23/oddswrap (MIT) - read only, no browser and no client disguise.

  content-managed-page?page=CUSTOM&customPageId=nba|nfl     the league page: events + competitions
  event-page?eventId=<id>&tab=<tab>                        one game's tab: markets + runners with prices

Kept: competition exactly "NBA" / "NFL" (FanDuel lists "eBasketball H2H GG League" simulated games beside the NBA),
pre-game, full-game player markets:
  standard   "<player> - Points", MOVING_HANDICAP, runners "<player> Over" / "<player> Under" at one handicap
  alternate  "<player> - Alt Points", runners "<player> Over 24.5" (one side each - no Under is invented)
             "To Score 15+ Points" / "To Record 8+ Assists" / "6+ Made Threes", runners = players (Over N - 0.5)
Quarter / half markets and stats outside markets.MARKETS are left out. FanDuel publishes no quote update time.
"""
import os
import re

from .. import net
from ..lines import utc_iso
from .base import ALTERNATE, STANDARD, Event, FetchResult, Provider, make_offer

AK = os.environ.get("SPORTS_FANDUEL_AK") or "FhMFpcPWXMeyZxOx"
HEADERS = {"Accept": "application/json"}                     # the session's honest User-Agent (net.py)
COMPETITION = {"nba": "NBA", "nfl": "NFL"}
TABS = {"nba": ("player-points", "player-rebounds", "player-assists", "player-threes", "player-combos"),
        "nfl": ("passing-props", "receiving-props", "rushing-props")}
STAT = {
    "basketball": {"Points": "points", "Rebounds": "rebounds", "Assists": "assists", "Made Threes": "threes",
                   "Pts + Reb + Ast": "pra", "Pts + Reb": "pr", "Pts + Ast": "pa", "Reb + Ast": "ra",
                   "Steals": "steals", "Blocks": "blocks", "Turnovers": "turnovers"},
    "football": {"Passing Yds": "pass_yds", "Passing TDs": "pass_td", "Pass Attempts": "pass_att",
                 "Pass Completions": "pass_cmp", "Rushing Yds": "rush_yds", "Rushing Attempts": "rush_att",
                 "Total Receptions": "rec", "Receiving Yds": "rec_yds", "Rushing + Receiving Yds": "rush_rec_yds",
                 "Passing + Rushing Yds": "pass_rush_yds"},
}
_PERIOD = re.compile(r"QUARTER|HALF|1ST_|2ND_|3RD_|4TH_")
_ALT_RUNNER = re.compile(r"^(?P<name>.+) (?P<side>Over|Under) (?P<line>\d+(?:\.\d+)?)$")
_THRESHOLD = re.compile(r"^(?:To (?:Score|Record) )?(?P<n>\d+)\+ (?P<stat>.+)$")


def state():
    return (os.environ.get("SPORTS_FANDUEL_STATE") or "oh").lower()


def _base():
    host = f"sbapi.{state()}.sportsbook.fanduel.com"
    net._GAPS.setdefault(host, 1.0)                            # 1 request / second, shared by every caller
    return f"https://{host}/api"


def parse_events(body, lg):
    att = (body or {}).get("attachments") or {}
    comps = {str(c.get("competitionId")): c.get("name") for c in (att.get("competitions") or {}).values()}
    out = []
    for e in (att.get("events") or {}).values():
        name = e.get("name") or ""
        if (comps.get(str(e.get("competitionId"))) != COMPETITION.get(lg["key"]) or " @ " not in name or "(" in name
                or e.get("inPlay")):
            continue
        away, _, home = name.partition(" @ ")
        out.append(Event(str(e.get("eventId")), lg["key"], name, home, away, utc_iso(e.get("openDate"))))
    return out


def _price(runner):
    w = runner.get("winRunnerOdds") or {}
    am = ((w.get("americanDisplayOdds") or {}).get("americanOdds"))
    dec = (((w.get("trueOdds") or {}).get("decimalOdds") or {}).get("decimalOdds"))
    return am, dec


def _status(market, runner):
    if market.get("marketStatus") == "SUSPENDED" or runner.get("runnerStatus") == "SUSPENDED":
        return "suspended"
    return "open" if market.get("marketStatus") == "OPEN" and runner.get("runnerStatus") == "ACTIVE" else None


def parse_props(body, lg, event):
    """One event-page tab -> offers. Unknown shapes are skipped, never guessed."""
    fam = lg["family"]
    stats = STAT[fam]
    att = (body or {}).get("attachments") or {}
    out = []
    common = {"provider": "fanduel", "book": "fanduel", "jurisdiction": state(), "league": lg["key"],
              "provider_event_id": event.provider_event_id, "event_name": event.name, "home": event.home,
              "away": event.away, "start": event.start, "period": "game", "provider_updated_at": None}
    for m in (att.get("markets") or {}).values():
        mtype, mname = m.get("marketType") or "", (m.get("marketName") or "").strip()
        if (str(m.get("eventId")) != event.provider_event_id or m.get("inPlay") or _PERIOD.search(mtype)
                or mname.lower().startswith(("1st", "2nd", "3rd", "4th"))):
            continue
        runners = m.get("runners") or []
        player, _, tail = mname.partition(" - ")
        if tail and mtype.startswith("PLAYER_") and "_ALT_" not in mtype and m.get("bettingType") == "MOVING_HANDICAP":
            stat = stats.get(tail)
            sides = {}
            for r in runners:
                nm = r.get("runnerName") or ""
                for side in ("Over", "Under"):
                    if nm == f"{player} {side}":
                        sides[side.lower()] = r
            if not stat or len(sides) != len(runners) or not sides:
                continue
            lines = {r.get("handicap") for r in sides.values()}
            if len(lines) != 1 or None in lines:
                continue                                       # never pair two different lines
            for side, r in sides.items():
                st = _status(m, r)
                am, dec = _price(r)
                if st and dec:
                    out.append(make_offer(**common, player=player, market=stat, stat_label=tail, line=r["handicap"],
                                          side=side, price_american=am, price_decimal=dec, market_type=STANDARD,
                                          status=st, provider_market_id=m.get("marketId"),
                                          provider_selection_id=r.get("selectionId")))
        elif tail.startswith("Alt ") and "_ALT_" in mtype:
            stat = stats.get(tail[4:])
            if not stat:
                continue
            for r in runners:
                g = _ALT_RUNNER.match(r.get("runnerName") or "")
                st = _status(m, r)
                am, dec = _price(r)
                if g and g.group("name") == player and st and dec:
                    out.append(make_offer(**common, player=player, market=stat, stat_label=tail,
                                          line=float(g.group("line")), side=g.group("side").lower(),
                                          price_american=am, price_decimal=dec, market_type=ALTERNATE, status=st,
                                          provider_market_id=m.get("marketId"),
                                          provider_selection_id=r.get("selectionId")))
        elif not tail:
            g = _THRESHOLD.match(mname)
            stat = stats.get(g.group("stat")) if g else None
            if not stat:
                continue
            for r in runners:
                st = _status(m, r)
                am, dec = _price(r)
                nm = (r.get("runnerName") or "").strip()
                if nm and r.get("isPlayerSelection", True) and st and dec:
                    out.append(make_offer(**common, player=nm, market=stat, stat_label=mname,
                                          line=int(g.group("n")) - 0.5, side="over", price_american=am,
                                          price_decimal=dec, market_type=ALTERNATE, status=st,
                                          provider_market_id=m.get("marketId"),
                                          provider_selection_id=r.get("selectionId")))
    return out


class FanDuel(Provider):
    name = "fanduel"
    books = ("fanduel",)

    def events(self, lg):
        res = FetchResult()
        try:
            body = net.get_json(f"{_base()}/content-managed-page", headers=HEADERS, tries=1,
                                params={"page": "CUSTOM", "customPageId": lg["key"], "_ak": AK})
            res.requests, res.http_status = 1, 200
            res.events = parse_events(body, lg)
        except net.HttpError as e:
            res.requests, res.http_status, res.error = int(not e.held), e.status, str(e)
            res.blocked, res.not_before = e.status in (401, 403), e.not_before_iso
        return res

    def props(self, lg, event):
        res = FetchResult()
        for tab in TABS.get(lg["key"], ()):
            try:
                body = net.get_json(f"{_base()}/event-page", headers=HEADERS, tries=1,
                                    params={"eventId": event.provider_event_id, "tab": tab, "_ak": AK})
                res.requests += 1
                res.http_status = 200
                res.offers += parse_props(body, lg, event)
            except net.HttpError as e:
                res.requests += int(not e.held)
                res.http_status, res.error = e.status, f"{tab}: {e}"
                res.blocked, res.not_before = e.status in (401, 403), e.not_before_iso
                break                                          # one failed tab stops this game; the worker backs off
        return res
