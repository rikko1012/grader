"""Book quotes -> canonical sc-1 OfferSnapshot records, from the SAME fetch (no second provider request).

The books worker (worker.run_once) reads FanDuel / BetRivers (own public feeds) and, when enabled, OddsPapi. Each
successful game read is handed to sink(), which stores it in the canonical store the UI snapshots and the model read.

One OfferSnapshot per (feed, book, game, player, stat, period, market type, LINE): the two sides of one line are one
offer; a side the book does not list is NOT OFFERED (never invented, never 50%). Platform = the book for its own feed
("fanduel", "betrivers"), "oddspapi:<book>" for OddsPapi. Prices are the book's decimal odds. source_updated_at = the
provider's own change time when it publishes one (BetRivers changedDate, OddsPapi changedAt), else None with
source_time_unknown. A game read completely marks that game's offers of the platform that it no longer lists as removed
(only that game: a pass fetches the games that are due, never the whole slate).

Events come from the books worker's own ESPN mapping (event_key "espn:<id>" -> sc-1 "<league>:espn:<id>"); players are
resolved exactly with the canonical resolver (names + current rosters), never fuzzily: an unresolved player is stored
with player_id None (never graded, shown as unresolved).
"""
from __future__ import annotations

import hashlib
from dataclasses import replace

from .. import names, store
from ..adapters import Batch
from ..contracts import OfferSnapshot, SideQuote

RULE = "book house rules (not read)"
PERIOD_SCOPE = {"game": "full_game", "regulation": "regulation"}
FEED = {"fanduel": "fanduel sbapi (own public feed, Ohio)", "betrivers": "Kambi offering API (own public feed, Ohio)",
        "oddspapi": "OddsPapi v4 (documented feed)"}


def platform_of(offer):
    return offer["book"] if offer["provider"] in ("fanduel", "betrivers") else f"{offer['provider']}:{offer['book']}"


def _pair_key(o):
    raw = "|".join(str(o.get(k)) for k in ("provider", "book", "jurisdiction", "provider_event_id", "player_key",
                                           "market", "period", "market_type", "line"))
    return hashlib.sha1(raw.encode()).hexdigest()[:24]


def to_offers(offers, lg, event_key, observed_at, resolver=None):
    """Normalized book offers (base.make_offer dicts) of ONE game -> Batch of OfferSnapshot (two sides per line)."""
    b = Batch()
    league = lg["key"]
    event_id = f"{league}:espn:{event_key.split(':', 1)[1]}" if event_key and event_key.startswith("espn:") else None
    groups = {}
    for o in offers:
        if o.get("market") is None or o.get("period") not in PERIOD_SCOPE:
            continue
        groups.setdefault(_pair_key(o), {})[o["side"]] = o
    for key, sides in groups.items():
        o = next(iter(sides.values()))
        pid, why = (resolver.player(o["player"], None, event_id) if resolver and event_id else
                    (None, "event unresolved" if not event_id else "no resolver"))
        quotes = tuple(SideQuote(s, s in sides and sides[s].get("price_decimal") is not None, "decimal",
                                 price=(sides[s].get("price_decimal") if s in sides else None))
                       for s in ("over", "under"))
        upd = max((x.get("provider_updated_at") for x in sides.values() if x.get("provider_updated_at")), default=None)
        status = "open" if all(x.get("status") == "open" for x in sides.values()) else "suspended"
        extras = (("feed", FEED.get(o["provider"], o["provider"])), ("provider", o["provider"]),
                  ("jurisdiction", o.get("jurisdiction")), ("provider_event_id", o["provider_event_id"]),
                  ("provider_market_id", o.get("provider_market_id")))
        if event_id is None:
            extras += (("unresolved_event", "book game not mapped to an ESPN event"),)
        elif pid is None:
            extras += (("unresolved_player", why),)
        b.add(OfferSnapshot(
            platform=platform_of(o), offer_id=key, market_id=o.get("provider_market_id"), league=league,
            event_id=event_id, event_ref=o.get("event_name"), player_id=pid, player_name=o["player"],
            player_key=names.key(o["player"]), team=None, stat=o["market"], source_stat=o.get("stat_label") or "",
            scope=PERIOD_SCOPE[o["period"]], line=float(o["line"]), sides=quotes,
            variant="main" if o.get("market_type") == "standard" else "alternate",
            promotional=o.get("market_type") == "promo", availability=status, rule_version=RULE,
            listed_start_utc=o.get("start"), listed_time_kind="start", source_updated_at=upd,
            observed_at=observed_at, source_time_unknown=upd is None, extras=extras, known_at=observed_at))
    return b


def store_game(con, league, platforms, event_id, batch, observed_at, complete):
    """Store one game's offers; after a COMPLETE read, offers of these platforms on this game that the read no longer
    lists get a 'removed' revision (never after a failed / partial read, never on other games)."""
    res = store.put_many(con, batch.records)
    store.quarantine_many(con, batch.quarantine, league, observed_at)
    removed = 0
    if complete and event_id:
        seen = {(r.platform, r.offer_id) for r in batch.records}
        stale = []
        for p in platforms:
            for _, o in store.offers(con, league=league, event_id=event_id, platform=p, limit=100000):
                if (o.platform, o.offer_id) in seen or o.availability == "removed":
                    continue
                stale.append(replace(o, availability="removed", observed_at=observed_at, known_at=observed_at))
        removed = store.put_many(con, stale)["revision"]
    return {k: res[k] for k in ("inserted", "duplicate", "revision", "invalid")} | {"removed": removed}


def sink(con, resolver_for):
    """-> callback(provider, lg, event_key, offers, complete, observed_at) for worker.run_once / _league_reads.
    resolver_for(league) returns the canonical resolver (built once per ingest cycle)."""
    totals = {}

    def put(provider, lg, event_key, offers, complete, observed_at):
        batch = to_offers(offers, lg, event_key, observed_at, resolver_for(lg["key"]))
        plats = sorted({platform_of(o) for o in offers}) or ([provider] if provider in ("fanduel", "betrivers")
                                                              else [])
        eid = batch.records[0].event_id if batch.records else (
            f"{lg['key']}:espn:{event_key.split(':', 1)[1]}" if event_key and event_key.startswith("espn:") else None)
        r = store_game(con, lg["key"], plats, eid, batch, observed_at, complete)
        t = totals.setdefault(provider, {"offers": 0, "inserted": 0, "revision": 0, "duplicate": 0, "removed": 0,
                                         "unresolved_player": 0})
        t["offers"] += len(batch.records)
        t["unresolved_player"] += sum(1 for x in batch.records if x.player_id is None)
        for k in ("inserted", "revision", "duplicate", "removed"):
            t[k] += r[k]
    put.totals = totals
    return put
