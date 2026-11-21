"""Member-facing league names for soccer / tennis competition codes (soccer-epl, owner 2026-11-20: "add EPL league for
soccer"). Display only: the code (ESPN slug, e.g. eng.1) stays the identity everywhere; this module only names it.
No sports import (the main web process never loads the sports package). An unknown code has no label (None) and the
page keeps showing what it showed before."""

LABELS = {
    "eng.1": "Premier League", "esp.1": "La Liga", "ger.1": "Bundesliga", "ita.1": "Serie A", "fra.1": "Ligue 1",
    "usa.1": "MLS", "mex.1": "Liga MX", "bra.1": "Brasileirão", "arg.1": "Liga Profesional (Argentina)",
    "ned.1": "Eredivisie", "por.1": "Primeira Liga", "sco.1": "Scottish Premiership", "bel.1": "Belgian Pro League",
    "tur.1": "Süper Lig", "aus.1": "A-League", "jpn.1": "J1 League", "ksa.1": "Saudi Pro League",
    "eng.2": "Championship", "eng.fa": "FA Cup", "eng.league_cup": "EFL Cup",
    "uefa.champions": "Champions League", "uefa.europa": "Europa League", "uefa.europa.conf": "Conference League",
    "conmebol.libertadores": "Copa Libertadores", "conmebol.sudamericana": "Copa Sudamericana",
    "uefa.nations": "UEFA Nations League", "concacaf.nations.league": "CONCACAF Nations League",
    "fifa.friendly": "International friendly", "fifa.world": "World Cup",
    "fifa.worldq.uefa": "World Cup qualifier (UEFA)", "fifa.worldq.conmebol": "World Cup qualifier (CONMEBOL)",
    "fifa.worldq.concacaf": "World Cup qualifier (CONCACAF)", "fifa.worldq.afc": "World Cup qualifier (AFC)",
    "fifa.worldq.caf": "World Cup qualifier (CAF)",
    "atp": "ATP", "wta": "WTA", "atp_challenger": "ATP Challenger", "wta125": "WTA 125",
}


def label(code):
    """The member-facing name of a competition code, or None when it has none."""
    return LABELS.get(code) if isinstance(code, str) else None
