"""sports.books - sportsbook player-prop odds for NBA / NFL, collected by ONE scheduled worker and read from storage.

Providers sit behind one adapter contract (base.Provider): a documented odds feed (The Odds API) and the books whose
public feeds answer a plain, honestly identified request (FanDuel, BetRivers). Books whose sites refuse such requests
(DraftKings, BetMGM, Caesars) are probed for health only - never with a browser, a disguised client or a workaround;
their prices come through the documented feed when a key is configured. Member pages and the Scout board read the
stored quotes (store.py, compare.py); nothing they do calls a provider. See sports/books/README.md.
"""
