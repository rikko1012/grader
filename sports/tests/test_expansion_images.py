"""v3 card pictures, sports side (sports/expansion_images.py): only player-specific PrizePicks art is kept, outside the offer rows."""
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
