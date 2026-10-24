"""The Odds API (the-odds-api.com) - the documented, metered feed for DraftKings, FanDuel, BetMGM, Caesars and BetRivers
player props. Needs ODDS_API_KEY (environment only; never written to a file, never printed - errors are redacted).

  GET /v4/sports/<sport>/events?apiKey=...                     upcoming games (costs no credits, per the docs)
  GET /v4/sports/<sport>/events/<id>/odds?apiKey=...&markets=a,b,c&bookmakers=...&oddsFormat=american
      one game's markets for the listed books in ONE call (markets batched; cost per the docs: markets x regions,
      where up to 10 named bookmakers count as one region)
Response headers x-requests-remaining / x-requests-used / x-requests-last are recorded with every fetch, so the worker
can stop before the plan's credits run out. Each bookmaker's and market's last_update is kept as provider time.
Book keys: draftkings, fanduel, betmgm, williamhill_us (Caesars), betrivers. The feed does not say which state a price
is from, so jurisdiction is stored as "us".
"""
import os

from .. import config, markets, net
from ..lines import utc_iso
from .base import ALTERNATE, STANDARD, Event, FetchResult, Provider, make_offer

BASE = "https://api.the-odds-api.com/v4"
BOOKS = {"draftkings": "draftkings", "fanduel": "fanduel", "betmgm": "betmgm", "williamhill_us": "caesars",
         "betrivers": "betrivers"}
ALTERNATE_KEYS = {           # documented alternate-line keys; requested only with SPORTS_ODDS_API_ALTERNATES=1
    "basketball": ("player_points_alternate", "player_rebounds_alternate", "player_assists_alternate",
                   "player_threes_alternate", "player_points_rebounds_assists_alternate"),
    "football": ("player_pass_yds_alternate", "player_rush_yds_alternate", "player_reception_yds_alternate",
                 "player_receptions_alternate"),
}


def market_keys(lg):
    """The market keys one event call asks for (batched into a single request)."""
    keys = list(markets.ODDS_API.get(lg["family"], {}))
    if os.environ.get("SPORTS_ODDS_API_ALTERNATES") == "1":
        keys += list(ALTERNATE_KEYS.get(lg["family"], ()))
    return keys


def credits_per_event(lg):
    """Documented cost of one event-odds call: markets x regions (the five named books are one region)."""
    return len(market_keys(lg))


def _int(h):
    try:
        return int(float(h))
    except (TypeError, ValueError):
        return None


def _redact(msg):
    key = config.odds_api_key() or ""
    return msg.replace(key, "***") if key else msg


def parse_events(body, lg):
    return [Event(str(e.get("id")), lg["key"], f"{e.get('away_team')} @ {e.get('home_team')}", e.get("home_team"),
                  e.get("away_team"), utc_iso(e.get("commence_time"))) for e in body or [] if e.get("id")]


def parse_props(body, lg, event):
    fam = lg["family"]
    std = markets.ODDS_API.get(fam, {})
    out = []
    common = {"provider": "theoddsapi", "jurisdiction": "us", "league": lg["key"],
              "provider_event_id": event.provider_event_id, "event_name": event.name, "home": event.home,
              "away": event.away, "start": event.start, "period": "game"}
    for bk in (body or {}).get("bookmakers") or []:
        book = BOOKS.get(bk.get("key"))
        if not book:
            continue
        for mk in bk.get("markets") or []:
            key = mk.get("key") or ""
            alt = key.endswith("_alternate")
            stat = std.get(key[: -len("_alternate")] if alt else key)
            if not stat:
                continue
            for o in mk.get("outcomes") or []:
                side = (o.get("name") or "").lower()
                if side not in ("over", "under") or o.get("point") is None or o.get("price") is None:
                    continue
                out.append(make_offer(**common, book=book, player=o.get("description"), market=stat, stat_label=key,
                                      line=o["point"], side=side, price_american=o["price"], price_decimal=None,
                                      market_type=ALTERNATE if alt else STANDARD, status="open",
                                      provider_market_id=key, provider_selection_id=None,
                                      provider_updated_at=utc_iso(mk.get("last_update") or bk.get("last_update"))))
    return out


class TheOddsAPI(Provider):
    name = "theoddsapi"
    books = tuple(BOOKS.values())
    kind = "feed"

    def configured(self):
        return (True, "") if config.odds_api_key() else (False, "ODDS_API_KEY is not set")

    def _get(self, path, params, res):
        try:
            r = net.get(f"{BASE}{path}", params={"apiKey": config.odds_api_key(), **params}, tries=1)
        except net.HttpError as e:
            res.requests += int(not e.held)
            res.http_status, res.error = e.status, _redact(f"{e} {e.body[:120]}")
            res.blocked, res.not_before = e.status in (401, 403), e.not_before_iso
            return None
        res.requests += 1
        res.http_status = r.status_code
        res.credits_remaining = _int(r.headers.get("x-requests-remaining"))
        last = _int(r.headers.get("x-requests-last"))
        res.credits_used = (res.credits_used or 0) + (last or 0)
        return r.json()

    def events(self, lg):
        res = FetchResult()
        body = self._get(f"/sports/{lg['odds_api']}/events", {}, res)
        if body is not None:
            res.events = parse_events(body, lg)
        return res

    def props(self, lg, event):
        res = FetchResult()
        body = self._get(f"/sports/{lg['odds_api']}/events/{event.provider_event_id}/odds",
                         {"markets": ",".join(market_keys(lg)), "bookmakers": ",".join(BOOKS),
                          "oddsFormat": "american", "dateFormat": "iso"}, res)
        if body is not None:
            res.offers = parse_props(body, lg, event)
        return res
