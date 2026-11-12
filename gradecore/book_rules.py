"""Which books count as INDEPENDENT prices and which settle like the bet they are compared with.

Owner, 2026-11-12: "use 2 independent books by default, matching the exact line, period and settlement rules. Two sites
using the same underlying feed shouldn't count twice."

One place for both tables. Read by the pick'em +EV board (workspace_ev), the board's Market edge column
(workspace_market_edge, through workspace_ev) and Price check / both-sides pairs (workspace_markets). Display tools only:
nothing here is read by a grade, a projection or a stake. Pure data and arithmetic; imports no sport code.

FAMILY - book -> pricing source. Two books in one family are one opinion and count once. A book that is not listed has
no established independence: it lands in the shared family UNLISTED, so any number of unlisted books count once together.
A relay ("oddsapi:<book>", "oddspapi:<book>") is the book it relays.

SETTLEMENT - (book, sport) -> how a player prop settles. A book with no entry, or an entry with an unknown sit-out rule,
is never used (unknown = excluded). `dnp` is what happens when the player does not take part at all:
  "void"           stake handed back (the pick'em apps' own rule: the pick is removed)
  "fair_price"     the contract resolves at the exchange's last fair price - no side wins or loses on the sit-out, so the
                   quoted price is still a price GIVEN the player plays
  "loses_over"     the Over / Yes side loses (the quoted Over price already carries the sit-out chance)
  "active_roster"  action as soon as the player is on the game-day active roster, even with no play
  None             not read from the book's rules
"""
import json
from pathlib import Path

UNLISTED = "unlisted"
RELAYS = ("oddsapi", "oddspapi", "espn")

# book -> family. Evidence is what this repo's own readers show, or the source named; nothing is assumed.
FAMILY = {
    # FanDuel: read from FanDuel's own sportsbook API (sbapi.<state>.sportsbook.fanduel.com, sports/books/fanduel.py).
    # Flutter's in-house trading; no other book this server ingests is served from that API.
    "fanduel": "fanduel",
    # BetRivers: read from KAMBI's offering API (eu-offering-api.kambicdn.com, operator "rsiusoh",
    # sports/books/betrivers.py). The prices are Kambi's, so every Kambi-served book is this one family.
    "betrivers": "kambi",
    # Bovada: read from bovada.lv's own coupon / subscription JSON (sports/books/bovada.py). Bodog-network pricing; no
    # other ingested book shares it.
    "bovada": "bovada",
    # Thunderpick: its own partner feed, match winner only (thunderpick-integration). Its odds supplier is not
    # established, so it is its own family only against the books above; it prices no player prop.
    "thunderpick": "thunderpick",
    # Kalshi and Polymarket: two separate order books (api.elections.kalshi.com, gamma-api.polymarket.com). Each exchange
    # is its own family, and neither is a sportsbook's feed.
    "kalshi": "kalshi",
    "polymarket": "polymarket",
    # Unibet (AU), PR #64: read from KAMBI's offering API (ap.offering-api.kambicdn.com/offering/v2018/ubau,
    # sports/books/unibet_au.py) and parsed by BetRivers' own Kambi parser (betrivers.parse_props). Same supplier as
    # BetRivers, so the same family: the two never count as two books.
    "unibet_au": "kambi",
    # PointsBet (AU), PR #64: its own API (api.au.pointsbet.com, sports/books/pointsbet_au.py; collector off since it was
    # refused on 2026-11-12). Who prices it is not established here, so it stays in the shared unlisted family.
    "pointsbet_au": UNLISTED,
    # DraftKings: no feed of DraftKings' own is read (its site refuses this server, sports/books/providers.py BLOCKED).
    # PR #62 game quotes and PR #64 "searchable reference lines" are ESPN-supplied (sports/game_books.py, source 'espn',
    # book 'draftkings'): game lines and LINE-ONLY player thresholds (price_over / price_under None) in their own
    # game-books document, never in a board snapshot's book rows - nothing there has a price to read. Its pricing
    # independence is not established here, so it stays in the shared unlisted family; ESPN is a relay of it.
    "draftkings": UNLISTED,
}

# Never a price source for these tools, whatever a feed carries.
#   novig / gemini   reverted release of 2026-11-09 (rows may still sit in the store); rules and fees never verified
#   dabble           its per-side number is not what an entry pays (book_providers.json rules.pick_price)
#   sleeper / prizepicks / underdog   pick'em apps: the bet being priced, never the price
NEVER_A_SOURCE = ("novig", "gemini", "dabble", "sleeper", "prizepicks", "underdog")

# What the pick'em apps do (the bet a fair chance is compared with).
#   PrizePicks help center "DNPs, Reboots, and Ties" and Underdog help center "What happens in the event of a tie / voided
#   selection", as summarised by a web search on 2026-11-12 (neither page was opened directly): a player who does not play a
#   single second / snap is a DNP and the pick is removed; a tie is removed the same way.
PICKEM = {"dnp": "void", "overtime": "included", "push": "void"}

_KALSHI = "Kalshi contract terms (kalshi-public-docs.s3.amazonaws.com/contract_terms/NBAPTS.pdf, NBAREB.pdf, NFLREC.pdf, " \
          "NFLRECYDS.pdf, NFLRSHYDS.pdf, NFLPASSYDS.pdf), read 2026-11-12: a player who withdraws or cannot take part before " \
          "the game resolves at the last fair price; regulation and overtime both count"
_POLY = "Polymarket market description (sports/books/sources.py RULE_PM, read 2026-10-23): full game including overtime; " \
        "a player who is inactive or does not play resolves No / Under"
SETTLEMENT = {
    # FanDuel NFL: voided only when the player does not play a snap (Action Network's table of FanDuel's house rule,
    # updated 2026-05-05). FanDuel NBA: stands once the player takes the court, void if he does not take part, totals
    # include overtime (FanDuel's policy text as reproduced by Action Network / Gambling911). FanDuel's own rules page
    # refused this reader (HTTP 403, 2026-11-12) and was not worked around, so both are second-hand.
    ("fanduel", "nba"): {"dnp": "void", "overtime": "included", "push": "refund", "read_at": "2026-11-12",
                         "source": "FanDuel house rule as reproduced by Action Network / Gambling911 (FanDuel's page: HTTP 403)"},
    ("fanduel", "nfl"): {"dnp": "void", "overtime": "included", "push": "refund", "read_at": "2026-11-12",
                         "source": "actionnetwork.com/education/prop-betting-rules-what-happens-if-player-doesnt-play (updated 2026-05-05)"},
    # BetRivers: the reader keeps only Kambi's "Including Overtime" criteria (sports/books/betrivers.py PERIOD), so overtime
    # is known. Its sit-out rule could not be read: the rules page is drawn in the browser and no copy was found
    # (2026-11-12). Unknown = not used.
    ("betrivers", "nba"): {"dnp": None, "overtime": "included", "push": "refund", "read_at": "2026-11-12",
                           "source": "sit-out rule not read (rules page not readable without a browser)"},
    ("betrivers", "nfl"): {"dnp": None, "overtime": "included", "push": "refund", "read_at": "2026-11-12",
                           "source": "sit-out rule not read (rules page not readable without a browser)"},
    # Bovada NFL (bovada.lv/help/common-faq/football-betting-rules, read 2026-11-12): the player "must be on the game day
    # active roster for bets to have action"; "Players that are active and end up not playing still have action". That is
    # not the apps' rule (no snap = removed), and no measured adjustment exists, so it is not used.
    ("bovada", "nfl"): {"dnp": "active_roster", "overtime": "included", "push": "refund", "read_at": "2026-11-12",
                        "source": "https://www.bovada.lv/help/common-faq/football-betting-rules"},
    # Bovada NBA (bovada.lv/help/common-faq/basketball-betting-rules, read 2026-11-12): overtime counts; the page does not
    # say what happens when the player does not play. Unknown = not used.
    ("bovada", "nba"): {"dnp": None, "overtime": "included", "push": "refund", "read_at": "2026-11-12",
                        "source": "https://www.bovada.lv/help/common-faq/basketball-betting-rules (sit-out rule not stated)"},
    ("kalshi", "nba"): {"dnp": "fair_price", "overtime": "included", "push": "none", "read_at": "2026-11-12", "source": _KALSHI},
    ("kalshi", "nfl"): {"dnp": "fair_price", "overtime": "included", "push": "none", "read_at": "2026-11-12", "source": _KALSHI},
    ("polymarket", "nba"): {"dnp": "loses_over", "overtime": "included", "push": "none", "read_at": "2026-10-23", "source": _POLY},
    ("polymarket", "nfl"): {"dnp": "loses_over", "overtime": "included", "push": "none", "read_at": "2026-10-23", "source": _POLY},
    # Australian books (PR #64): book_providers.json marks both price_comparable false ("Not included in EV or arbitrage
    # until settlement-rule compatibility is verified"). Australian house rules for a player who does not play, and
    # whether overtime counts, were not read for this release. Unknown = not used, and not listed on member pages.
    **{(b, s): {"dnp": None, "overtime": None, "push": None, "read_at": "2026-11-12",
                "source": "Australian house rules not read (book_providers.json price_comparable false)"}
       for b in ("unibet_au", "pointsbet_au") for s in ("nba", "nfl")},
    # DraftKings: only ESPN-supplied lines without prices (see FAMILY); house rules not read. Unknown = not used.
    **{("draftkings", s): {"dnp": None, "overtime": None, "push": None, "read_at": "2026-11-12",
                           "source": "house rules not read; ESPN-supplied reference lines carry no price"} for s in ("nba", "nfl")},
}

# The one documented adjustment for a "loses_over" price: the registered market rule mc-1 (sports/model/market.py,
# REGISTER.md 10) reads a Polymarket Over as p / (1 - d), d = the share of games a rotation player sits out, frozen in
# sports/model/artifacts/market_mc1.json. Only the NBA share was measured; the NFL entry there is 0 with the reason "not
# measurable" - a placeholder, not a measurement - so an NFL "loses_over" price is not used here.
SIT_OUT_MEASURED = ("nba",)
_ART = Path(__file__).resolve().parent.parent / "sports" / "model" / "artifacts" / "market_mc1.json"
_SHARE = {}

REASON_UNKNOWN = "settlement rules not confirmed"
REASON_DIFFERENT = "settlement rules differ"
REASON_NEVER = "not a price source"


def base_book(platform):
    """'oddsapi:fanduel' -> 'fanduel' (a relay is the book it relays)."""
    return str(platform or "").split(":")[-1].lower()


def family_of(platform):
    """The pricing family of a book or relayed book. Not listed = UNLISTED (all unlisted books count once together)."""
    return FAMILY.get(base_book(platform), UNLISTED)


def families(platforms):
    """The distinct families behind these books."""
    return {family_of(p) for p in platforms}


def sit_out_share(sport):
    """The measured share for a 'loses_over' price, or None (not measured / file unreadable)."""
    if sport not in SIT_OUT_MEASURED:
        return None
    if sport not in _SHARE:
        try:
            v = float(json.loads(_ART.read_text(encoding="utf-8"))["dnp_share"][sport])
            _SHARE[sport] = v if 0 < v < 0.5 else None
        except (OSError, ValueError, KeyError, TypeError):
            _SHARE[sport] = None
    return _SHARE[sport]


def rule(platform, sport):
    return SETTLEMENT.get((base_book(platform), sport))


def confirmed(platform, sport):
    """Is this book's own bet on this sport's player prop settled by a rule that was read (sit-out and overtime both
    known)? A book that is priced on its own (Price check's single bets) needs this, whatever the chance is measured by."""
    if base_book(platform) in NEVER_A_SOURCE:
        return False
    r = rule(platform, sport)
    return bool(r) and r.get("dnp") is not None and r.get("overtime") is not None


def for_pickem(platform, sport):
    """May this book's price stand for a pick'em line of this sport? -> (ok, sit-out share to apply, reason when not)."""
    if base_book(platform) in NEVER_A_SOURCE:
        return False, 0.0, REASON_NEVER
    r = rule(platform, sport)
    if r is None or r.get("dnp") is None or r.get("overtime") is None:
        return False, 0.0, REASON_UNKNOWN
    if r["overtime"] != PICKEM["overtime"]:
        return False, 0.0, REASON_DIFFERENT
    if r["dnp"] in ("void", "fair_price"):
        return True, 0.0, None
    if r["dnp"] == "loses_over":
        d = sit_out_share(sport)
        return (True, d, None) if d is not None else (False, 0.0, REASON_DIFFERENT)
    return False, 0.0, REASON_DIFFERENT


def same_bet(a, b, sport):
    """Do two books settle one prop the same way (both known, same sit-out rule, same overtime rule)? The test for a
    both-sides pair and for one sportsbook's price standing in for another's: nothing is adjusted here."""
    if base_book(a) in NEVER_A_SOURCE or base_book(b) in NEVER_A_SOURCE:
        return False
    ra, rb = rule(a, sport), rule(b, sport)
    if not ra or not rb or ra.get("dnp") is None or rb.get("dnp") is None:
        return False
    return ra["dnp"] == rb["dnp"] and ra.get("overtime") == rb.get("overtime") and ra.get("overtime") is not None


def table():
    """Both tables as plain rows (admin view / reports)."""
    books = sorted({b for b, _ in SETTLEMENT} | set(FAMILY))
    return {"families": {b: family_of(b) for b in books}, "never_a_source": list(NEVER_A_SOURCE), "pickem": dict(PICKEM),
            "settlement": [dict(v, book=b, sport=s, pickem_ok=for_pickem(b, s)[0], sit_out_share=for_pickem(b, s)[1])
                           for (b, s), v in sorted(SETTLEMENT.items())]}
