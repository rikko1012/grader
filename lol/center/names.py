"""Name and role normalization for LoL identities (exact matching only - never fuzzy)."""
import re
import unicodedata

ROLES = ("Top", "Jungle", "Mid", "Bot", "Support")
_ROLE = {"top": "Top", "toplane": "Top", "jungle": "Jungle", "jungler": "Jungle", "jng": "Jungle", "jg": "Jungle",
         "mid": "Mid", "middle": "Mid", "midlane": "Mid", "bot": "Bot", "adc": "Bot", "ad carry": "Bot", "bottom": "Bot",
         "marksman": "Bot", "support": "Support", "sup": "Support", "supp": "Support"}
ROLE_LABEL = {"Top": "Top", "Jungle": "Jungle", "Mid": "Mid", "Bot": "Bot/ADC", "Support": "Support"}


def norm(s):
    """Case, accents, spaces and punctuation removed: 'Xi'an Team WE' -> 'xianteamwe'. Exact comparison only."""
    s = unicodedata.normalize("NFKD", str(s or ""))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def page_key(title):
    """A MediaWiki page title as one key: underscores = spaces, first letter case-insensitive, rest case-sensitive."""
    t = re.sub(r"[ _]+", " ", str(title or "")).strip()
    return (t[:1].upper() + t[1:]) if t else ""


def role(value):
    """Leaguepedia / feed role text -> one of ROLES, or None when it is not exactly one role ('TOP/JNG/MID' -> None)."""
    v = str(value or "").strip().lower()
    return _ROLE.get(v)
