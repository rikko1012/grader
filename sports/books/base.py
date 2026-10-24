"""The adapter contract every odds provider implements, and the normalized offer every provider returns.

Provider (subclass):
    name                 provider id stored with every quote ("theoddsapi", "fanduel", "betrivers")
    books                book ids it can return ("draftkings", "fanduel", ...)
    configured()         -> (bool, reason): credentials / settings present (never fetches)
    leagues              league keys it supports ("nba", "nfl")
    events(lg)           -> FetchResult(events=[Event], requests, credits...)  upcoming games, pre-game only
    props(lg, event)     -> FetchResult(offers=[offer dict], requests, credits...)  one game's player props
A provider never writes storage and never decides schedules; the worker does both. Replacing a provider = a new
subclass registered in PROVIDERS; Scout reads storage and does not change.

Offer (one priced side of one market at one line - never a two-sided pair):
    provider, book, jurisdiction, league, provider_event_id, event_name, home, away, start,
    player, player_key, provider_player_id, market (canonical: markets.MARKETS), stat_label (the book's own words),
    period ("game" = full game incl. overtime; "regulation" = excl. overtime), line, side ("over" | "under"),
    price_american, price_decimal, market_type ("standard" | "alternate" | "promo"), status ("open" | "suspended"),
    provider_market_id, provider_selection_id, provider_updated_at (None when the provider publishes none)
A one-sided offer (an "X+" ladder rung, an Over-only line) is stored as exactly that: no opposite side is invented.
"""
import hashlib
from dataclasses import dataclass, field

from .. import names

STANDARD, ALTERNATE, PROMO = "standard", "alternate", "promo"
OFFER_FIELDS = ("provider", "book", "jurisdiction", "league", "provider_event_id", "event_name", "home", "away",
                "start", "player", "player_key", "provider_player_id", "market", "stat_label", "period", "line",
                "side", "price_american", "price_decimal", "market_type", "status", "provider_market_id",
                "provider_selection_id", "provider_updated_at")
REQUIRED = ("provider", "book", "league", "provider_event_id", "player", "market", "period", "line", "side",
            "price_decimal", "market_type")


@dataclass
class Event:
    provider_event_id: str
    league: str
    name: str
    home: str
    away: str
    start: str                      # UTC 'YYYY-MM-DDTHH:MM:SSZ' as the provider lists it


@dataclass
class FetchResult:
    events: list = field(default_factory=list)
    offers: list = field(default_factory=list)
    requests: int = 0               # HTTP requests sent
    credits_used: int = None        # provider quota units spent (documented feeds), None when not metered
    credits_remaining: int = None
    http_status: int = None
    error: str = None               # None = success (an empty list is a successful empty answer)
    blocked: bool = False           # the provider refused this server (403 / access denied) - not retried soon
    not_before: str = None          # the provider's Retry-After as a UTC time: nothing is sent to it before then
    budget_denied: str = None       # a metered call NOT made because the quota guard said no (not a provider failure)
    unmapped: list = field(default_factory=list)   # provider market names that mapped to no stat (shown in health)


class Provider:
    name = "base"
    books = ()
    leagues = ("nba", "nfl")
    kind = "direct"                 # "feed" = documented, metered API; "direct" = a book's own public feed

    def configured(self):
        return True, ""

    def events(self, lg):
        raise NotImplementedError

    def props(self, lg, event):
        raise NotImplementedError


def american_to_decimal(a):
    a = float(a)
    if a == 0:
        raise ValueError("american odds 0")
    return round(1 + (a / 100.0 if a > 0 else 100.0 / -a), 6)


def decimal_to_american(d):
    d = float(d)
    if d <= 1:
        raise ValueError("decimal odds <= 1")
    return int(round((d - 1) * 100)) if d >= 2 else int(round(-100 / (d - 1)))


def offer_key(o):
    """Identity of one offered side: provider, book, jurisdiction, game, player, stat, period, market type, line, side.
    A moved line is a new offer (the old one is closed as removed); a new price on the same line is a new quote."""
    raw = "|".join(str(o.get(k)) for k in ("provider", "book", "jurisdiction", "provider_event_id", "player_key",
                                           "market", "period", "market_type", "line", "side"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


def make_offer(**kw):
    """A complete offer dict (missing optional fields = None); raises ValueError when a required field is missing."""
    o = {k: kw.get(k) for k in OFFER_FIELDS}
    if o["player"] and not o["player_key"]:
        o["player_key"] = names.key(o["player"])
    if o["price_decimal"] is None and o["price_american"] is not None:
        o["price_decimal"] = american_to_decimal(o["price_american"])
    if o["price_american"] is None and o["price_decimal"] is not None:
        o["price_american"] = decimal_to_american(o["price_decimal"])
    if o["price_american"] is not None:
        o["price_american"] = int(round(float(o["price_american"])))      # Kambi sends "-141" as text
    if o["price_decimal"] is not None:
        o["price_decimal"] = round(float(o["price_decimal"]), 6)
    o["status"] = o["status"] or "open"
    missing = [k for k in REQUIRED if o.get(k) in (None, "")]
    if missing:
        raise ValueError(f"offer missing {missing}")
    if o["side"] not in ("over", "under") or o["market_type"] not in (STANDARD, ALTERNATE, PROMO):
        raise ValueError(f"bad side / market type: {o['side']} / {o['market_type']}")
    o["line"] = float(o["line"])
    o["offer_key"] = offer_key(o)
    return o
