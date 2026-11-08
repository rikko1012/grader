"""LoL role tags for display (owner 2026-11-07: roles on every player card - Top, Jungle, Mid, ADC, Support).

DISPLAY ONLY: the board row's text. Not a forecast input (the model keeps its own role from forecast.ROLE_IN / History.role),
not part of the LoL model identity (m.VERSION + fit_sha). names.ROLE_LABEL is left as it is.

Source, in order: the offer's linked role (market_links.role: Riot LE:PlayerGame / Leaguepedia game data, normalised by
names.role - a combined feed position like 'TOP/JNG/MID' is None there), then the latest forecast's role (the same game
data the forecast used). Neither = None, and the page shows nothing."""
from .names import ROLES

LABEL = {"Top": "Top", "Jungle": "Jungle", "Mid": "Mid", "Bot": "ADC", "Support": "Support"}
_FROM_FORECAST = {r.lower(): r for r in ROLES}     # forecast.ROLE_IN values ('top', 'bot', ...) -> names.ROLES


def label(link_role, forecast_role=None):
    if link_role in LABEL:
        return LABEL[link_role]
    r = _FROM_FORECAST.get(str(forecast_role or "").strip().lower())
    return LABEL.get(r) if r else None
