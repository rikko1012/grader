"""Pre-game Moneylines research picks. Directional scores are not calibrated win probabilities."""
from math import fsum, isfinite, tanh

from . import devig


def finite(value):
    return type(value) in (float, int) and isfinite(value)


def book_signal(prices):
    if len(prices) != 2 or not all(finite(p) and abs(p) >= 100 for p in prices):
        return None
    probs = devig.multiplicative([devig.american_to_decimal(p) for p in prices])
    return probs[0] - probs[1]


def decide(teams, markets, matchup):
    """Give the market group two shares and the matchup group one; average within each group.

    A positive factor favours the first named team. Preserve disagreements in the response.
    At an exact tie prefer the market direction, then a stable name tie-break with no claimed advantage.
    """
    if len(teams) != 2 or not all(isinstance(t, str) and t.strip() for t in teams) or teams[0] == teams[1]:
        return None
    factors = []
    scores = {}
    for group, entries, weight in (("market", markets, 2), ("matchup", matchup, 1)):
        valid = [(label, v) for label, v in entries.items() if finite(v) and -1 <= v <= 1]
        if valid:
            scores[group] = weight * fsum(v for _, v in valid) / len(valid)
            for label, v in valid:
                factors.append({"label": label, "team": teams[0] if v > 0 else teams[1] if v < 0 else None,
                                "weight": weight / len(valid)})
    score = fsum(scores.values())
    tied = abs(score) < 1e-9
    if tied:
        score = scores.get("market", 0)
    if abs(score) < 1e-9:
        name = min(teams, key=lambda t: (t.casefold(), t))
        basis = "Level evidence · alphabetical tie-break" if factors else "No directional evidence · alphabetical tie-break"
    else:
        name = teams[0] if score > 0 else teams[1]
        basis = ("Market tie-break" if tied else "Market and matchup" if len(scores) == 2
                 else "Market only" if "market" in scores else "Matchup only")
    total = sum(f["weight"] for f in factors)
    for factor in factors:
        factor["weight"] = round(factor["weight"] / total, 4)
    return {"team": name, "basis": basis, "factors": factors, "version": "winner-research-1"}


def game_pick(game, matchup=None, spreads=None):
    markets = {}
    for quote in game["quotes"]:
        prices = quote["prices"]
        if quote["kind"] == "american":
            score = book_signal(prices)
            if score is None:
                score = (spreads or {}).get(quote["source"])
                if score is not None:
                    markets[quote["source"] + " spread"] = score
                    continue
                # A plus-money quote identifies an underdog directionally. A single negative
                # price can occur on either side of a near-even book, and cannot establish a favourite.
                for index, price in enumerate(prices):
                    if finite(price) and price > 100:
                        implied = 1 / devig.american_to_decimal(price)
                        markets[quote["source"] + " one-sided underdog price"] = (2*implied - 1) * (1 if index == 0 else -1)
                        break
                continue
        elif quote["kind"] == "midpoint" and len(prices) == 2 and all(finite(p) and 0 < p < 1 for p in prices):
            score = prices[0] - prices[1]
        else:
            continue
        markets[quote["source"]] = score
    return decide(game["teams"], markets, matchup or {})


def cs2_pick(match, oriented, include_book=True):
    teams = [match["teams"]["a"], match["teams"]["b"]]
    markets = {}
    book = match.get("thunderpick") or {}
    if include_book and book.get("eligible", book.get("usable")) and finite(book.get("p_a")) and finite(book.get("p_b")):
        markets["Thunderpick"] = book["p_a"] - book["p_b"]
    for source, block in (match.get("platforms") or {}).items():
        if block and block.get("usable"):
            pa = oriented(block, *teams)
            if finite(pa):
                markets[source.capitalize()] = 2 * pa - 1
    composite = (match.get("side") or {}).get("composite") or {}
    matchup = {}
    for key, label in (("rating", "Lineup rating"), ("form", "Recent form"), ("map_pool", "Map pool"), ("h2h", "Head-to-head")):
        z = (composite.get("signals") or {}).get(key)
        if finite(z):
            matchup[label] = tanh(z / 2)
    return decide(teams, markets, matchup)
