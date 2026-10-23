"""History baseline: the player's own recent numbers (ESPN game logs) as a projection and a chance at a line.

mean  = recency-weighted average of his last games (half-life config.LEAGUES[..]["half_life_games"]; this season and
        last, preseason left out; basketball games with 0 minutes left out)
shape = the market's measured variance function (dispersion.json), the same one fair.py uses

DISPLAY ONLY until measured. sports-market-maker's walk-forward test found a full public-data NBA model (minutes,
matchup, injuries, betting lines) beat the player's own recent average by only ~2% Brier, and by under 1% for
heavy-minute players - the ones the books list. A plain recent average is weaker than the sharp price, so edges.py ranks by
the sharp chance and shows this beside it; record.py logs both so their accuracy can be compared on the same lines.
"""
from . import fair, markets

MAX_GAMES = {"basketball": 40, "football": 20}
THIN = 5            # fewer games than this: flagged


def played_games(games, family):
    if family == "basketball":
        return [g for g in games if (g["parts"].get("min") or 0) > 0]
    return list(games)


def baseline(games, family, market, half_life, line=None):
    spec = markets.market(family, market)
    if not spec:
        return None
    gs = [g for g in played_games(games, family) if markets.value(family, market, g["parts"]) is not None]
    gs = gs[:MAX_GAMES.get(family, 30)]
    if not gs:
        return {"games": 0, "note": "no games in ESPN's game log"}
    vals = [markets.value(family, market, g["parts"]) for g in gs]
    w = [0.5 ** (i / float(half_life)) for i in range(len(vals))]
    mean = sum(v * x for v, x in zip(vals, w)) / sum(w)
    out = {"games": len(vals), "mean": round(mean, 2), "last5": vals[:5],
           "avg10": round(sum(vals[:10]) / len(vals[:10]), 2), "thin": len(vals) < THIN}
    if line is not None:
        a, b = fair.shape_params(family, market, spec["shape"])
        o, u, p = fair.dist_probs(spec["shape"], a, b, max(mean, 0.05), float(line))
        last10 = vals[:10]
        out.update({"p_over": o, "p_under": u, "p_push": p,
                    "over_last10": sum(1 for v in last10 if v > line), "n_last10": len(last10)})
    return out
