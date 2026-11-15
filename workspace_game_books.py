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


def moneylines(srv, league):
    """Slim winner-only response for the classic tab. Never send player-prop reference rows."""
    def compute():
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
