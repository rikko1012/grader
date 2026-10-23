"""Which provider supplies which book, and the health probes for books whose own feeds refuse this server.

ROUTES: book -> providers that can supply it (a book may come from its own public feed AND the documented feed; both
are stored with their provider, and comparisons count a book once - its most recently seen two-sided price).
BLOCKED: books whose public sites answered our plain, honestly identified request with a refusal (2026-10-23, Ohio
server). They are probed once per PROBE_EVERY_H with ONE such request so the health table shows a dated, real result;
a refusal is never worked around (no browser, no disguised client, no proxy). Their prices come from The Odds API.
"""
from .. import net
from .base import FetchResult
from .betrivers import BetRivers
from .fanduel import FanDuel
from .theoddsapi import TheOddsAPI

PROVIDERS = {"theoddsapi": TheOddsAPI(), "fanduel": FanDuel(), "betrivers": BetRivers()}
BOOKS = ("draftkings", "fanduel", "betmgm", "caesars", "betrivers")
ROUTES = {"draftkings": ("theoddsapi",), "fanduel": ("fanduel", "theoddsapi"), "betmgm": ("theoddsapi",),
          "caesars": ("theoddsapi",), "betrivers": ("betrivers", "theoddsapi")}
PROBE_EVERY_H = 6
BLOCKED = {
    "draftkings": "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusoh/v1/leagues/42648",
    "betmgm": "https://www.oh.betmgm.com/en/api/clientconfig",
    "caesars": ("https://api.americanwagering.com/regions/us/locations/oh/brands/czr/sb/v3/sports/basketball/events/"
                "schedule?competitionIds=5806c896-4eec-4de1-874f-afed93114b8c"),
}


def probe(book):
    """One plain request to a blocked book's public endpoint -> FetchResult (blocked=True on 401 / 403)."""
    res = FetchResult(requests=1)
    try:
        r = net.get(BLOCKED[book], headers={"Accept": "application/json"}, tries=1)
        res.http_status = r.status_code
    except net.HttpError as e:
        res.http_status, res.error, res.blocked = e.status, f"direct feed refused: {e}", e.status in (401, 403)
    return res
