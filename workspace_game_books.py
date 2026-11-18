"""File-backed game quotes, shared across members; no bookmaker requests in the web process."""
import json
import math
from datetime import datetime, timezone
from gradecore import winner


def attach_series_pick(match, orient_names):
    # Linked platform blocks already carry the canonical team A probability.
    oriented = (lambda block, a, b: block.get("prob_a")) if match.get("match_id") else orient_names
    match["research_pick"] = winner.cs2_pick(match, oriented)
    if (match.get("thunderpick") or {}).get("eligible"):
        match["pick_without_book"] = winner.cs2_pick(match, oriented, include_book=False)


def shared(srv, league):
    if league not in ("nba", "nfl"):
        return 400, {"error": "unsupported league"}

    def compute():
        import workspace_sports
        try:
            doc, _ = workspace_sports.call(srv, "/api/sports/game-books", {"league": league})
            if doc.get("league") != league or not isinstance(doc.get("events"), list):
                raise ValueError("snapshot identity")
            doc = dict(doc)
            at = datetime.fromisoformat(doc["observed_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - at).total_seconds()
            doc["stale"] = age < -60 or age > 1800
            return 200, json.dumps(doc, separators=(",", ":")).encode()
        except Exception:
            return 503, b'{"error":"game_prices_unavailable"}'
    return srv.shared(("ws_game_books", league), compute, ttl=30)


# Exchange reference sources per sport: (card source, description). Polymarket is read for tennis only.
_EXCHANGE = 'Exchange bid/ask midpoint · price reference, not used in grades'
EXCHANGES = {'soccer': {'kalshi': ('Kalshi', _EXCHANGE)},
             'tennis': {'kalshi': ('Kalshi', _EXCHANGE),
                        'polymarket': ('Polymarket', _EXCHANGE + ' · not tradable from the US')}}


def _exchange_cards(rows, league, now):
    """Kalshi / Polymarket match-winner midpoints as their own card per matched fixture: a price reference, never a
    pick or a grade input. A malformed row is skipped; it can never take the tab down."""
    import workspace_expansion
    cards = []
    for row in rows if isinstance(rows, list) else []:
        try:
            outcomes = row['outcomes']
            prices = [o['mid'] for o in outcomes]
            exchange = EXCHANGES[league].get(row['platform'])
            if (not workspace_expansion._eligible(row, league, now) or not exchange
                    or len(outcomes) != (3 if league == 'soccer' else 2)
                    or any(type(v) is not float or not 0 < v < 1 for v in prices)
                    or not all(isinstance(o['label'], str) and o['label'] for o in outcomes)
                    or (league == 'soccer' and outcomes[1]['label'] != 'Draw')
                    or not isinstance(row['match'], str) or not isinstance(row['offer_id'], str)):
                continue
            cards.append({'id': row['platform'] + ':' + row['offer_id'], 'label': row['match'],
                'teams': [o['label'] for o in outcomes], 'start': row['start'], 'seen_at': row['observed_at'],
                'quotes': [{'source': exchange[0], 'kind': 'midpoint', 'prices': prices, 'description': exchange[1]}],
                'pick': None})
        except (KeyError, TypeError, IndexError, AttributeError):
            continue
    return cards


def moneylines(srv, league):
    """Slim winner-only response for the classic tab. Never send player-prop reference rows."""
    def compute():
        if league in ('soccer', 'tennis'):
            import workspace_sports
            import workspace_expansion
            try:
                doc, _ = workspace_sports.call(srv, '/api/sports/expansion/board', {'sport': league})
                if doc.get('sport') != league:
                    raise ValueError('wrong sport')
                now = datetime.now(timezone.utc).timestamp()
                games = []
                for market in doc.get('game_odds', []):
                    outcomes = market.get('outcomes') or []
                    if (not workspace_expansion._eligible(market, league, now) or not market.get('main')
                            or market.get('market_type') != 'Match'
                            or len(outcomes) != (3 if league == 'soccer' else 2)
                            or any(o.get('line') is not None for o in outcomes)):
                        continue
                    games.append({'id': market['platform'] + ':' + market['offer_id'], 'label': market['match'],
                        'teams': [o['label'] for o in outcomes], 'start': market['start'], 'seen_at': market['observed_at'],
                        'quotes': [{'source': workspace_sports.BOOK_LABEL.get(market['platform'], market['platform']),
                                    'kind': 'american', 'prices': [o['american'] for o in outcomes],
                                    'description': 'American odds · ' + market['jurisdiction']}],
                        'pick': None})
                games.extend(_exchange_cards(doc.get('reference_prices'), league, now))
                return 200, json.dumps({'league': league, 'observed_at': doc['built_at'], 'games': games}, separators=(',', ':')).encode()
            except Exception:
                return 503, b'{"error":"game_prices_unavailable"}'
        if league == "lol":
            try:
                doc = json.loads((srv.LOL_CENTER / "moneylines.json").read_bytes())
            except FileNotFoundError:
                return 200, b'{"league":"lol","pending":true,"games":[]}'
            for game in doc["games"]:
                game["pick"] = winner.game_pick(game, game.get("winner_matchup"))
            return 200, json.dumps(doc, separators=(",", ":")).encode()
        status, body = shared(srv, league)
        if status != 200:
            return status, body
        doc = json.loads(body)
        games = []
        for event in doc["events"]:
            quotes = []
            spreads = {}
            for book in event["books"].values():
                market = book["markets"].get("money-line") or {}
                prices = [market.get(side) for side in ("awayOdds", "homeOdds")]
                prices = [v if type(v) in (int, float) and math.isfinite(v) and abs(v) >= 100 else None for v in prices]
                if any(v is not None for v in prices):
                    quotes.append({"source": book["label"], "kind": "american", "prices": prices})
                    spread = book["markets"].get("spread") or {}
                    a, h = spread.get("awaySpread"), spread.get("homeSpread")
                    if winner.finite(a) and winner.finite(h) and a == -h:
                        spreads[book["label"]] = math.tanh(-a / 10)
            if quotes:
                game = {"id": event["id"], "teams": [event["away"], event["home"]], "start": event["start_utc"],
                        "seen_at": event["seen_at"], "quotes": quotes}
                game["pick"] = winner.game_pick(game, event.get("winner_matchup"), spreads)
                games.append(game)
        return 200, json.dumps({"league": league, "observed_at": doc["observed_at"], "pending": bool(doc.get("pending")),
                                "games": games}, separators=(",", ":")).encode()
    return srv.shared(("winner_games", league), compute, ttl=30)
