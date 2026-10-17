"""Where LoL keeps its data, and the settings every LoL module shares.

LOL_DATA_DIR   - persistent folder for LoL (default: <CS2_DATA_DIR>/lol on the server's disk, else lol/.data here).
                 Its own files only: cache.db (API answers), history.db (game history, unless LOL_DB_DSN), output/.
LOL_DB_DSN     - optional PostgreSQL for the game history (the HLTV way: a sync job writes, the scout only reads).
                 Tables are all named lol_* so a shared database never collides with the HLTV scraper's tables.
LEAGUEPEDIA_USER / LEAGUEPEDIA_BOT_PASSWORD - Leaguepedia bot password (Special:BotPasswords on lol.fandom.com).
                 Without them Leaguepedia is skipped and history comes from the LoL Esports feed.
"""
import os
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent


def _data_dir():
    if os.environ.get("LOL_DATA_DIR"):
        return Path(os.environ["LOL_DATA_DIR"])
    if os.environ.get("CS2_DATA_DIR"):              # the server's persistent disk - LoL gets its own subfolder
        return Path(os.environ["CS2_DATA_DIR"]) / "lol"
    return PKG_DIR / ".data"


DATA_DIR = _data_dir()
CACHE_DB = DATA_DIR / "cache.db"
HISTORY_DB = DATA_DIR / "history.db"
OUT_DIR = DATA_DIR / "output"
USER_AGENT = "lol_scout/0.1 (LoL esports stats tool; python-requests)"


def db_dsn():
    return os.environ.get("LOL_DB_DSN") or None


def leaguepedia_credentials():
    """(user, bot password) or None. The user is the bot login name, e.g. 'YourName@lolscout'."""
    user, pw = os.environ.get("LEAGUEPEDIA_USER"), os.environ.get("LEAGUEPEDIA_BOT_PASSWORD")
    return (user, pw) if user and pw else None
