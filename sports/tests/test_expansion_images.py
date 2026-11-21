"""v3 card pictures, sports side (sports/expansion_images.py): only player-specific PrizePicks art is kept, outside the offer rows."""
import json

from sports import expansion_images as ei


def _pp_body():
    def player(i, url, combo=False):
        return {"type": "new_player", "id": str(i), "attributes": {"image_url": url, "combo": combo}}
    return {"data": [], "included": [
        player(1, "https://static.prizepicks.com/images/players/soccer/one.webp"),
        player(2, "https://static.prizepicks.com/images/players/soccer/shared.webp"),
        player(3, "https://static.prizepicks.com/images/players/soccer/shared.webp"),
        player(4, "https://static.prizepicks.com/images/players/placeholder.png"),
        player(5, "https://images.fotmob.com/image_resources/playerimages/5.png"),
        player(6, "https://static.prizepicks.com/images/players/soccer/jersey_9.png"),
        player(7, "https://static.prizepicks.com/images/players/soccer/combo.webp", combo=True),
        player(8, None), {"type": "team", "id": "9", "attributes": {"logo": "https://static.prizepicks.com/l.png"}}]}


def test_prizepicks_pictures_keep_only_player_specific_art(tmp_path):
    assert ei.from_body(_pp_body(), "soccer") == {"soccer:prizepicks:1": "https://static.prizepicks.com/images/players/soccer/one.webp"}
    assert ei.from_body(_pp_body(), "nba") == {}
    assert ei.remember(_pp_body(), "soccer", tmp_path) == 1
    first = (tmp_path / "images.json").stat().st_mtime_ns
    assert ei.remember(_pp_body(), "soccer", tmp_path) == 0 and (tmp_path / "images.json").stat().st_mtime_ns == first   # unchanged: not rewritten
    assert ei.for_board(tmp_path, [{"player_id": "soccer:prizepicks:1"}, {"player_id": "soccer:prizepicks:2"}]) == \
        {"soccer:prizepicks:1": "https://static.prizepicks.com/images/players/soccer/one.webp"}
    assert ei.for_board(tmp_path / "none", []) == {}
    assert ei.remember({"included": "broken"}, "soccer", tmp_path) == 0   # never raises


def test_offer_rows_and_revisions_are_unchanged_by_pictures():
    """The pictures never enter an offer row: its revision hash (which forecasts match on) is the same as before."""
    from sports import expansion_offers as eo
    text = open(eo.__file__, encoding="utf-8").read()
    assert "image_url" not in text and "expansion_images" not in text


def test_research_body_carries_the_board_players_pictures_and_offers_stay_the_same(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from sports import expansion_board as eb, expansion_research as er
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setattr(eb, "_PREPARED", {})
    monkeypatch.setattr(eb, "_RESEARCH_BODIES", {})
    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")              # noqa: E731
    row = {"sport": "soccer", "platform": "prizepicks", "offer_id": "1", "revision": "r", "event_id": "e",
           "player_id": "soccer:prizepicks:1", "stat": "shots", "scope": "full_game", "line": 1.5,
           "start": iso(now + timedelta(hours=2)), "observed_at": iso(now)}
    eb.save("soccer", [row], {}, iso(now))
    (tmp_path / "soccer/research.json").write_text(json.dumps(
        {"sport": "soccer", "at": iso(now), "players": {er.key(row): {"identity": None, "games": []}}}))
    req = SimpleNamespace(query={"sport": "soccer", "research": "1"})
    before = json.loads(eb.http_board(req)[1])
    assert "images" not in before                                   # no images.json: the same answer as before
    assert ei.remember(_pp_body(), "soccer", tmp_path / "soccer") == 1
    after = json.loads(eb.http_board(req)[1])
    assert after["images"] == {"soccer:prizepicks:1": "https://static.prizepicks.com/images/players/soccer/one.webp"}
    assert after["offers"] == before["offers"]                      # offer rows (and their revisions) untouched


def test_both_collectors_remember_pictures():
    import inspect
    from sports import expansion_board as eb
    assert "remember(body, \"soccer\", directory)" in inspect.getsource(eb._soccer_prizepicks)
    assert "remember(body, sport, root(sport))" in inspect.getsource(eb.collect)
