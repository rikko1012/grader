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
