"""Sports market sources beyond the foundation's Kalshi reader (owner decision 2026-10-23: every book we have is a
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
    "thunderpick": {"key_env": "THUNDERPICK_API_KEY", "verified": False, "consensus": False},
    "oddspapi": {"key_env": "ODDSPAPI_API_KEY", "verified": False, "consensus": False},
}
