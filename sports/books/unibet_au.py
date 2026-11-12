"""Unibet Australia public Kambi offers. AU identity is retained end to end.

Only NBA coverage verified. This is a separate operator, never a fallback for a
refused US feed. No browser, login, location override or alternative host retry.
"""
import os
from datetime import datetime, timezone
from .. import net
from .base import Provider, FetchResult
from .betrivers import parse_events, parse_props

BASE = "https://ap.offering-api.kambicdn.com/offering/v2018/ubau"
PARAMS = {"lang": "en_AU", "market": "AU"}


class UnibetAU(Provider):
    name, books, leagues = "unibet_au", ("unibet_au",), ("nba",)

    def configured(self):
        return os.getenv("SPORTS_UNIBET_AU", os.getenv("SPORTS_INGEST", "0")) == "1", "SPORTS_UNIBET_AU is off"

    def _read(self, path, parser):
        res = FetchResult()
        try:
            body = net.get_json(BASE + path, params=PARAMS, tries=1, timeout=12)
            res.requests, res.http_status = 1, 200
            parser(body, res)
        except net.HttpError as e:
            res.requests, res.http_status, res.error, res.blocked = int(not e.held), e.status, str(e), e.status in (401, 403)
            res.not_before = e.not_before_iso
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            res.requests, res.offers, res.events, res.error = 1, [], [], "answer not readable: " + type(e).__name__
        return res

    def events(self, lg):
        if lg['key'] not in self.leagues: return FetchResult()
        def read(body, res):
            now = datetime.now(timezone.utc)
            res.events = [e for e in parse_events(body, lg) if datetime.fromisoformat(e.start.replace('Z','+00:00')) > now]
        return self._read('/listView/basketball/nba.json', read)

    def props(self, lg, event):
        if lg['key'] not in self.leagues: return FetchResult()
        def read(body, res):
            res.offers = parse_props(body, lg, event, provider=self.name, book=self.name, region='AU')
        return self._read('/betoffer/event/' + event.provider_event_id + '.json', read)
