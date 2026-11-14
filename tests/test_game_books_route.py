from types import SimpleNamespace
import workspace_api


def test_game_prices_require_the_leagues_existing_access(monkeypatch):
    import workspace_game_books
    calls = []
    monkeypatch.setattr(workspace_api, "access", lambda *a: {"nba": False, "nfl": False})
    monkeypatch.setattr(workspace_game_books, "shared", lambda *a: calls.append(a))
    h = SimpleNamespace(send=lambda status, body: (status, body))
    assert workspace_api.get_game_books(h, None, {"league": "nba"}, None, None, None)[0] == 404
    assert workspace_api.get_game_books(h, None, {"league": "cs2"}, None, None, None)[0] == 400
    assert not calls


def test_game_prices_pass_only_valid_league_to_shared_cache(monkeypatch):
    import workspace_game_books
    calls = []
    monkeypatch.setattr(workspace_api, "access", lambda *a: {"nba": True, "nfl": True})
    def shared(srv, league):
        calls.append(league)
        return 200, b'{"events":[]}'
    monkeypatch.setattr(workspace_game_books, "shared", shared)
    h = SimpleNamespace(send=lambda status, body: (status, body))
    assert workspace_api.get_game_books(h, None, {"league": "nfl"}, None, None, None)[0] == 200
    assert calls == ["nfl"]

def test_bridge_wrong_league(monkeypatch):
    import workspace_game_books as bridge
    import workspace_sports
    class Server:
        def shared(self,key,compute,ttl):return compute()
    monkeypatch.setattr(workspace_sports,'call',lambda *a: ({'league':'nba','events':[]},None))
    assert bridge.shared(Server(),'nfl')[0]==503


def test_winner_games_keep_only_real_moneylines_on_the_offered_team(monkeypatch):
    import json
    import workspace_game_books as bridge
    srv = SimpleNamespace(shared=lambda key, compute, ttl: compute())
    event = {"id": "espn:42", "home": "Home", "away": "Away", "seen_at": "seen", "start_utc": "start",
             "books": {"dk": {"label": "DraftKings", "markets": {"money-line": {"homeOdds": -120},
                                                                  "spread": {"awayOdds": -110}}}},
             "reference_props": ["player threshold"]}
    doc = {"observed_at": "read", "events": [event, dict(event, id="spread-only", books={})]}
    monkeypatch.setattr(bridge, "shared", lambda *args: (200, json.dumps(doc).encode()))
    status, body = bridge.moneylines(srv, "nba")
    games = json.loads(body)["games"]
    assert status == 200 and len(games) == 1 and games[0]["teams"] == ["Away", "Home"]
    assert games[0]["quotes"] == [{"source": "DraftKings", "kind": "american", "prices": [None, -120]}]
    assert games[0]["seen_at"] == "seen" and b"reference_props" not in body
