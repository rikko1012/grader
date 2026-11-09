"""sports.books - sportsbook player-prop odds for NBA / NFL, collected by ONE scheduled worker and read from storage.

Providers sit behind one adapter contract (base.Provider): a documented odds feed (The Odds API) and the books whose
public feeds answer a plain, honestly identified request (FanDuel, BetRivers). Books whose sites refuse such requests
(DraftKings, BetMGM, Caesars) are probed for health only - never with a browser, a disguised client or a workaround;
their prices come through the documented feed when a key is configured. Member pages and the Scout board read the
stored quotes (store.py, compare.py); nothing they do calls a provider. See sports/books/README.md.

Market sources for the model (sources.py, ledger.py):
Sports market sources beyond the foundation's Kalshi reader (owner decision 2026-10-23: every book we have is a
PRIMARY market source for sports projections). Sports-namespaced: nothing here imports an esports module
(thunderpick.py / polymarket.py / oddspapi.py were read for endpoints and auth patterns only).

| source      | access                                 | NBA / NFL props verified here (2026-10-23)              | consensus |
|-------------|----------------------------------------|---------------------------------------------------------|-----------|
| kalshi      | public REST (foundation adapter)       | yes: N+ ladders, points/reb/ast/threes, NFL yds/rec/att | yes       |
| polymarket  | public gamma API, no key               | yes: "<Player>: <Stat> O/U <line>" markets, NBA points / | yes       |
|             |                                        | rebounds / assists / threes / steals / blocks; NFL      |           |
|             |                                        | passing / rushing / receiving yards, receptions,        |           |
|             |                                        | passing attempts / completions (many books empty: the   |           |
|             |                                        | liquidity / spread filter applies)                      |           |
| oddsapi     | ODDS_API_KEY (Render env only)         | not here: key not set locally. Parser = foundation       | yes, once |
|             |                                        | adapters.oddsapi_offers (documented API shape)          | resolved  |
| thunderpick | THUNDERPICK_API_KEY (Render env only)  | not here: key not set locally; sport paths for NBA / NFL | no - until|
|             |                                        | and the player-prop market shape are UNVERIFIED          | verified  |
| oddspapi    | ODDSPAPI_API_KEY (Render env only)     | not here: key not set locally; player-prop market ids   | no - until|
|             |                                        | UNVERIFIED                                              | verified  |

A source that is not verified runs CAPTURE-ONLY (inventory of what it answers, raw answer hash, no offers) until a
person reviews the first live inventory and flips VERIFIED - parsing an unseen format would invent data.
"""
SOURCES = {
    "kalshi": {"key_env": None, "verified": True, "consensus": True},
    "polymarket": {"key_env": None, "verified": True, "consensus": True},
    "oddsapi": {"key_env": "ODDS_API_KEY", "verified": True, "consensus": True},
    "oddspapi": {"key_env": "ODDSPAPI_API_KEY", "verified": False, "consensus": False},
    # the books' own public feeds (books/fanduel.py, books/betrivers.py), stored as sc-1 offers by books/bridge.py:
    # single-source market candidates mkt-fanduel / mkt-betrivers only - NOT in mc-1 / mi-1, whose registered source
    # set (REGISTER.md section 10) is unchanged (amendment A4)
    "fanduel": {"key_env": None, "verified": True, "consensus": False},
    "betrivers": {"key_env": None, "verified": True, "consensus": False},
}

# more-books-1109 (owner 2026-11-09 "find more sportsbooks you can add"): extra The Odds API regions, OFF by default.
# SPORTS_ODDSAPI_EXTRA_REGIONS=us2,us_ex (needs SPORTS_ODDSAPI=1 + ODDS_API_KEY, the same key and credit ledger) adds the
# priced books below, read with ONE extra event-odds call per game (cost = markets x extra regions). Bookmaker keys and
# regions as listed on https://the-odds-api.com/sports-odds-data/bookmaker-apis.html (read 2026-11-09); paid-only keys
# (courtside, rebet) and books we already read directly (kalshi, polymarket, polymarket_us) or do not know (limitless)
# are not taken. us_dfs is NEVER allowed here: its books (prizepicks, underdog, dabble_us_dfs, pick6) are fixed-payout
# pick'em apps whose Odds API prices are "indicative only"; stored as oddsapi:<book> they would be judged as priced
# lines by cap-3 and enter the market consensus. Dabble / PrizePicks / Underdog come from their own feeds.
# These books are DISPLAY ONLY: each shows its own priced line (its own grade follows the existing priced-line rules,
# cap-3 near-even included), but none is a source of the mc-1 / mi-1 consensus or the mv-1 "Market (N books)" chance,
# so adding them moves no other line's chance or grade (registered source set, REGISTER.md section 10, unchanged).
ODDSAPI_EXTRA_BOOKS = {
    "us2": ("ballybet", "betanysports", "betparx", "espnbet", "fliff", "hardrockbet", "hardrockbet_az",
            "hardrockbet_fl", "hardrockbet_oh"),
    "us_ex": ("novig", "prophetx", "betopenly"),
}
ODDSAPI_DISPLAY_ONLY = frozenset(b for v in ODDSAPI_EXTRA_BOOKS.values() for b in v)
ODDSAPI_PICKEM_KEYS = frozenset(("prizepicks", "underdog", "dabble_us_dfs", "pick6", "betr_us_dfs"))


def oddsapi_display_only(platform):
    """True for an oddsapi:<book> added by SPORTS_ODDSAPI_EXTRA_REGIONS (shown, never a consensus source)."""
    p = str(platform or "")
    return p.startswith("oddsapi:") and p.split(":", 1)[1] in ODDSAPI_DISPLAY_ONLY
