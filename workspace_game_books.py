"""File-backed game quotes, shared across members; no bookmaker requests in the web process."""
import json
from datetime import datetime, timezone


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
