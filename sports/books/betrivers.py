"""BetRivers - Kambi's public offering API (eu-offering-api.kambicdn.com), the CDN JSON betrivers.com's own pages read.
No login; it answered a plain, honestly identified request from an Ohio server on 2026-10-23 with operator "rsiusoh"
(BetRivers Ohio - no location is worked around). Endpoints as used by sjhouston23/oddswrap and
declanwalpole/sportsbook-odds-scraper (read for the endpoint shape only); no browser, no client disguise.

  listView/<path>.json             upcoming games of a league (basketball/nba, american_football/nfl)
  betoffer/event/<id>.json         one game's offers; every outcome carries its own changedDate (the provider's time)

Kept (criterion lifetime FULL_TIME_OVERTIME = "game"; FULL_TIME = "regulation"; halves / quarters left out):
  standard   "<stat> by the player - Including Overtime", outcomes Over / Under (line / 1000, odds / 1000 decimal);
             a line listed with Over only is stored with its one side
  alternate  "<N>+ <stat> By The Player - Including Overtime", outcome "Yes" = Over N - 0.5 (one side - no "No" is
             invented; Kambi lists none for these)
"""
import os
import re

from .. import net
from ..lines import utc_iso
from .base import ALTERNATE, PROMO, STANDARD, Event, FetchResult, Provider, make_offer

BASE = "https://eu-offering-api.kambicdn.com/offering/v2018"
PATHS = {"nba": "basketball/nba", "nfl": "american_football/nfl"}
PARAMS = {"lang": "en_US", "market": "US"}
STAT = {
    "basketball": {"points scored": "points", "points": "points", "rebounds": "rebounds", "assists": "assists",
                   "3-point field goals made": "threes", "three-point field goals made": "threes",
                   "points, rebounds & assists": "pra", "points & rebounds": "pr", "points & assists": "pa",
                   "rebounds & assists": "ra", "steals": "steals", "blocks": "blocks", "steals & blocks": "stocks",
                   "turnovers": "turnovers"},
    "football": {"total receiving yards": "rec_yds", "receiving yards": "rec_yds", "total receptions": "rec",
                 "receptions": "rec", "total rushing yards": "rush_yds", "rushing yards": "rush_yds",
                 "total passing yards": "pass_yds", "passing yards": "pass_yds",
                 "total passing touchdowns": "pass_td", "passing touchdowns": "pass_td",
                 "total rushing attempts": "rush_att", "rushing attempts": "rush_att",
                 "total pass attempts": "pass_att", "pass attempts": "pass_att",
                 "total pass completions": "pass_cmp", "pass completions": "pass_cmp",
                 "total rushing & receiving yards": "rush_rec_yds", "rushing & receiving yards": "rush_rec_yds"},
}
PERIOD = {"FULL_TIME_OVERTIME": "game", "FULL_TIME": "regulation"}
_OU = re.compile(r"^(?P<stat>.+?) by the player - including overtime$", re.I)
_REG = re.compile(r"^(?P<stat>.+?) by the player$", re.I)
_LADDER = re.compile(r"^(?P<n>\d+)\+ (?P<stat>.+?) by the player - including overtime$", re.I)


def operator():
    return (os.environ.get("SPORTS_BETRIVERS_OPERATOR") or "rsiusoh").lower()


def jurisdiction():
    op = operator()
    return op[-2:] if op.startswith("rsius") else op


def parse_events(body, lg):
    out = []
    for item in (body or {}).get("events") or []:
        e = item.get("event") or {}
        if e.get("state") != "NOT_STARTED" or not e.get("homeName") or not e.get("awayName"):
            continue
        out.append(Event(str(e.get("id")), lg["key"], e.get("name") or "", e["homeName"], e["awayName"],
                         utc_iso(e.get("start"))))
    return out


def _f(x, div=1.0):
    try:
        return float(x) / div
    except (TypeError, ValueError):
        return None


def parse_props(body, lg, event):
    stats = STAT[lg["family"]]
    out = []
    common = {"provider": "betrivers", "book": "betrivers", "jurisdiction": jurisdiction(), "league": lg["key"],
              "provider_event_id": event.provider_event_id, "event_name": event.name, "home": event.home,
              "away": event.away, "start": event.start}
    for b in (body or {}).get("betOffers") or []:
        crit = b.get("criterion") or {}
        label, period = (crit.get("label") or "").strip(), PERIOD.get(crit.get("lifetime"))
        outs = [o for o in b.get("outcomes") or [] if o.get("participant")]
        if not period or not outs or str(b.get("eventId")) != event.provider_event_id:
            continue
        tags = " ".join(b.get("tags") or []).upper()
        promo = "BOOST" in tags or "PROMO" in tags
        ladder = _LADDER.match(label)
        ou = None if ladder else (_OU.match(label) if period == "game" else _REG.match(label))
        for o in outs:
            dec, line = _f(o.get("odds"), 1000.0), _f(o.get("line"), 1000.0)
            status = {"OPEN": "open", "SUSPENDED": "suspended"}.get(o.get("status"))
            lab = (o.get("label") or o.get("englishLabel") or "").lower()
            if not dec or not status:
                continue
            if ladder and lab == "yes":
                stat = stats.get(ladder.group("stat").lower())
                if stat:
                    out.append(make_offer(**common, player=o["participant"], provider_player_id=o.get("participantId"),
                                          market=stat, stat_label=label, period=period,
                                          line=int(ladder.group("n")) - 0.5, side="over",
                                          price_american=o.get("oddsAmerican"), price_decimal=dec,
                                          market_type=PROMO if promo else ALTERNATE, status=status,
                                          provider_market_id=b.get("id"), provider_selection_id=o.get("id"),
                                          provider_updated_at=utc_iso(o.get("changedDate"))))
            elif ou and lab in ("over", "under") and line is not None:
                stat = stats.get(ou.group("stat").lower())
                if stat:
                    out.append(make_offer(**common, player=o["participant"], provider_player_id=o.get("participantId"),
                                          market=stat, stat_label=label, period=period, line=line, side=lab,
                                          price_american=o.get("oddsAmerican"), price_decimal=dec,
                                          market_type=PROMO if promo else STANDARD, status=status,
                                          provider_market_id=b.get("id"), provider_selection_id=o.get("id"),
                                          provider_updated_at=utc_iso(o.get("changedDate"))))
    return out


class BetRivers(Provider):
    name = "betrivers"
    books = ("betrivers",)

    def events(self, lg):
        res = FetchResult()
        try:
            body = net.get_json(f"{BASE}/{operator()}/listView/{PATHS[lg['key']]}.json", params=PARAMS, tries=1)
            res.requests, res.http_status, res.events = 1, 200, parse_events(body, lg)
        except net.HttpError as e:
            res.requests, res.http_status, res.error, res.blocked = int(not e.held), e.status, str(e), e.status in (401, 403)
            res.not_before = e.not_before_iso
        return res

    def props(self, lg, event):
        res = FetchResult()
        try:
            body = net.get_json(f"{BASE}/{operator()}/betoffer/event/{event.provider_event_id}.json",
                                params={**PARAMS, "includeParticipants": "true"}, tries=1)
            res.requests, res.http_status, res.offers = 1, 200, parse_props(body, lg, event)
        except net.HttpError as e:
            res.requests, res.http_status, res.error, res.blocked = int(not e.held), e.status, str(e), e.status in (401, 403)
            res.not_before = e.not_before_iso
        return res
