"""sports.books - sportsbook player-prop odds for NBA / NFL, collected by ONE scheduled worker and read from storage.

Providers sit behind one adapter contract (base.Provider): a documented odds feed (The Odds API) and the books whose
public feeds answer a plain, honestly identified request (FanDuel, BetRivers). Books whose sites refuse such requests
(DraftKings, BetMGM, Caesars) are probed for health only - never with a browser, a disguised client or a workaround;
their prices come through the documented feed when a key is configured. Member pages and the Grader board read the
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
    "bovada": {"key_env": None, "verified": True, "consensus": False},
    "unibet_au": {"key_env": None, "verified": True, "consensus": False, "display_only": True},
    "pointsbet_au": {"key_env": None, "verified": True, "consensus": False, "display_only": True},
}
