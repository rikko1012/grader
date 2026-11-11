"""Bovada public pregame JSON. No credentials, browser, proxies or location overrides.

Legacy coupons when available; otherwise finite public subscription snapshots.
The subscription listing is followed by every pregame event's main/alt markets.
Event responses are parsed and released one at a time. Quote-specific update times
are absent: observation time belongs to the worker, never event.lastModified.
"""
import os
import re
from datetime import datetime, timezone

from .. import net
from .base import Provider, Event, FetchResult, make_offer
from . import bovada_stream

BASE = "https://www.bovada.lv/services/sports/event/coupon/events/A/description"
PATHS = {"nba": "/basketball/nba", "nfl": "/football/nfl"}
STATS = {
    "nba": {"Total Points": "points", "Total Rebounds": "rebounds", "Total Assists": "assists",
            "Total Made 3 Points Shots": "threes", "Total Points and Assists": "pa",
            "Total Points and Rebounds": "pr", "Total Rebounds and Assists": "ra",
            "Total Points, Rebounds and Assists": "pra"},
    "nfl": {"Total Passing Yards": "pass_yds", "Total Passing Touchdowns": "pass_td",
            "Total Passing Attempts": "pass_att", "Total Completions": "pass_cmp",
            "Total Rushing Yards": "rush_yds", "Total Rush Attempts": "rush_att",
            "Total Receiving Yards": "rec_yds", "Total Receptions": "rec",
            "Total Rushing & Receiving Yards": "rush_rec_yds"},
}


def event_rows(body):
    if not isinstance(body, list):
        raise ValueError("expected Bovada event groups")
    return [e for g in body for e in g.get("events", [])]


def parse(body, league, now):
    events, offers = [], []
    for e in event_rows(body):
        if e.get("type") != "GAMEEVENT" or e.get("live") or e.get("status") != "U":
            continue
        start = datetime.fromtimestamp(float(e["startTime"]) / 1000, timezone.utc)
        if start <= now:
            continue
        teams = e.get("competitors", [])
        home = [t["name"] for t in teams if t.get("home") is True]
        away = [t["name"] for t in teams if t.get("home") is False]
        if len(home) != 1 or len(away) != 1:
            continue
        ev = Event(str(e["id"]), league, e.get("description", ""), home[0], away[0], start.strftime("%Y-%m-%dT%H:%M:%SZ"))
        events.append(ev)
        for group in e.get("displayGroups", []):
            for m in group.get("markets", []):
                if m.get("key") != "2W-OU" or m.get("period", {}).get("description") != "Game":
                    continue
                match = re.fullmatch(r"(.+?) - (.+) \(([A-Z0-9]+)\)", m.get("description", ""))
                if not match or match[1] not in STATS[league]:
                    continue
                lines = {str(o.get("price", {}).get("handicap")) for o in m.get("outcomes", [])}
                for o in m.get("outcomes", []):
                    if o.get("type") not in ("O", "U") or str(o.get("competitorId")) in {str(t["id"]) for t in teams}:
                        continue
                    if m.get("status") != "O" or o.get("status") != "O":
                        continue
                    price = o.get("price", {})
                    offers.append(make_offer(provider="bovada", book="bovada", jurisdiction="bovada.lv",
                        league=league, provider_event_id=ev.provider_event_id, event_name=ev.name,
                        home=ev.home, away=ev.away, start=ev.start, player=match[2],
                        provider_player_id=o.get("competitorId"), market=STATS[league][match[1]],
                        stat_label=m["description"], period="game", line=price.get("handicap"),
                        side="over" if o["type"] == "O" else "under", price_decimal=price.get("decimal"),
                        market_type="alternate" if len(lines) > 1 else "standard", status="open",
                        provider_market_id=m.get("id"), provider_selection_id=o.get("id")))
    return events, offers


class Bovada(Provider):
    name, books, kind = "bovada", ("bovada",), "feed_league"

    def configured(self):
        return os.getenv("SPORTS_BOVADA", os.getenv("SPORTS_INGEST", "0")) == "1", "SPORTS_BOVADA is off"

    def interval_min(self, db, leagues_today, now):
        return 5

    def read_batches(self, db, lg, now=None):
        now = now or datetime.now(timezone.utc)
        res = FetchResult()
        def fetch(path, params=None):
            res.requests += 1
            return net.get_json(BASE + path, params=params, tries=1, timeout=8)
        try:
            league = lg["key"]
            listing = fetch(PATHS[league], None if league == "nba" else
                            {"marketFilterId": "def", "preMatchOnly": "true", "lang": "en"})
            streaming = listing == {}
            if streaming:
                res.requests += 1
                listing = bovada_stream.snapshot(league)
            if league == "nba" and not streaming:
                res.events, res.offers = parse(listing, league, now)
            else:
                rows = [e for e in event_rows(listing) if not e.get("live") and e.get("status") == "U"
                        and float(e.get("startTime", 0)) / 1000 > now.timestamp()]
                rows.sort(key=lambda e: (e["startTime"], str(e["id"])))
                res.http_status = 200
                yield res
                for e in rows:
                    res = FetchResult()
                    path = e.get("link", "")
                    if not path.startswith(PATHS[league] + "/") or not re.fullmatch(r"/[a-zA-Z0-9/_-]+", path):
                        raise ValueError("unexpected Bovada event path")
                    if streaming:
                        res.requests += 1
                        detail = bovada_stream.snapshot(league, path)
                    else:
                        detail = fetch(path)
                    if detail == {} or detail == []:
                        # Listing can lead publication of details. Do not withdraw this game's saved quotes;
                        # they retain their real observation time and expire under the existing freshness rules.
                        res.unmapped.append(f"event {e['id']}: detail unavailable")
                        res.http_status = 200
                        yield res
                        continue
                    events, offers = parse(detail, league, now)
                    # The public stream sends main/alternate groups as separate fullEvent frames.
                    events = list({x.provider_event_id: x for x in events}.values())
                    if not events or any(x.provider_event_id != str(e["id"]) for x in events):
                        raise ValueError("Bovada event detail does not match its listing")
                    res.events, res.offers, res.http_status = events, offers, 200
                    yield res
                return
            res.http_status = 200
        except net.HttpError as e:
            res.events, res.offers = [], []
            res.http_status, res.error, res.blocked = e.status, str(e), e.status in (401, 403)
            res.not_before = e.not_before_iso
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            res.events, res.offers, res.error = [], [], "Bovada response did not match the verified schema"
        yield res

    def read(self, db, lg, now=None):
        """Compatibility/verification helper; the scheduled worker streams read_batches()."""
        total = FetchResult()
        for batch in self.read_batches(db, lg, now):
            total.requests += batch.requests
            if batch.error:
                batch.requests = total.requests
                return batch
            total.events.extend(batch.events)
            total.offers.extend(batch.offers)
            total.unmapped.extend(batch.unmapped)
            total.http_status = batch.http_status
        return total
