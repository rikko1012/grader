"""Where the sports package keeps its data, and the league table every module shares.

SPORTS_DATA_DIR - persistent folder (default: <CS2_DATA_DIR>/sports on the server's disk, else sports/.data here).
                  Its own files only: cache.db (API answers), sports.db (forecast log + settlements), lines.jsonl
                  (every pick'em line seen), output/.
ODDS_API_KEY    - optional The Odds API key (the-odds-api.com). Adds a multi-book consensus beside Pinnacle. Without it
                  Pinnacle (free, no key) is the only sharp price. Never written to a file.
"""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


def _data_dir():
    if os.environ.get("SPORTS_DATA_DIR"):
        return Path(os.environ["SPORTS_DATA_DIR"])
    if os.environ.get("CS2_DATA_DIR"):              # the server's persistent disk - sports gets its own subfolder
        return Path(os.environ["CS2_DATA_DIR"]) / "sports"
    return PKG_DIR / ".data"


DATA_DIR = _data_dir()
CACHE_DB = DATA_DIR / "cache.db"
FORECAST_DB = DATA_DIR / "sports.db"
LINES_ARCHIVE = DATA_DIR / "lines.jsonl"
OUT_DIR = DATA_DIR / "output"
DISPERSION_FILE = PKG_DIR / "dispersion.json"
USER_AGENT = "sports_scout/0.1 (player-prop research tool; python-requests)"

# One row per league. family picks the market table (markets.py). espn = (sport, league) in ESPN's URLs; espn_uid is the
# league number inside ESPN's athlete uids ("s:40~l:46~a:3134907" -> NBA). pp = PrizePicks league_id, ud = Underdog
# sport_id, pin = Pinnacle league id, odds_api = The Odds API sport key. Ids read 2026-10-23 from each source.
LEAGUES = {
    "nba": {"name": "NBA", "family": "basketball", "espn": ("basketball", "nba"), "espn_uid": "46",
            "pp": 7, "ud": "NBA", "pin": 487, "odds_api": "basketball_nba", "half_life_games": 10},
    "wnba": {"name": "WNBA", "family": "basketball", "espn": ("basketball", "wnba"), "espn_uid": "59",
             "pp": 3, "ud": "WNBA", "pin": 578, "odds_api": "basketball_wnba", "half_life_games": 10},
    "cbb": {"name": "College basketball", "family": "basketball", "espn": ("basketball", "mens-college-basketball"),
            "espn_uid": "41", "pp": 20, "ud": "CBB", "pin": 493, "odds_api": "basketball_ncaab", "half_life_games": 8},
    "nfl": {"name": "NFL", "family": "football", "espn": ("football", "nfl"), "espn_uid": "28",
            "pp": 9, "ud": "NFL", "pin": 889, "odds_api": "americanfootball_nfl", "half_life_games": 5},
    "cfb": {"name": "College football", "family": "football", "espn": ("football", "college-football"),
            "espn_uid": "23", "pp": 15, "ud": "CFB", "pin": 880, "odds_api": "americanfootball_ncaaf", "half_life_games": 4},
}


def league(key):
    k = str(key or "").strip().lower()
    if k not in LEAGUES:
        raise SystemExit(f"unknown league '{key}' - one of: {', '.join(LEAGUES)}")
    return dict(LEAGUES[k], key=k)


def odds_api_key():
    return os.environ.get("ODDS_API_KEY") or None
