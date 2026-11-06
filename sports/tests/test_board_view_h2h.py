"""Audit 2026-11-05 (B5): the member board's NBA / NFL H2H and SZN cells read board_view.json's linesH2h / linesSeason. The snapshot
job now writes them with every row (tonight's opponent team id per row), as the sheets path already did. Display only."""
from sports import board_view


def _games():
    out = []
    for i, (v, opp, season) in enumerate([(25, "nba:team:espn:BOS", 2027), (18, "nba:team:espn:MIA", 2027), (22, "nba:team:espn:BOS", 2027),
                                           (30, "nba:team:espn:BOS", 2026), (12, "nba:team:espn:CHI", 2026)]):
        out.append({"participation": "played", "values": {"points": v}, "season": season, "start_utc": f"2026-10-{20 - i:02d}",
                    "opponent": {"team_id": opp}})
    return out


def test_player_rows_carry_h2h_and_season_counts_for_tonights_opponent():
    rows = [{"key": "e1|p1|points|full_game", "scope": "full_game", "stat": "points", "quotes": [{"line": 20.5}]}]
    v = board_view.player_rows(rows, _games(), {"e1|p1|points|full_game": "nba:team:espn:BOS"})["e1|p1|points|full_game"]
    assert v["linesH2h"]["20.5"] == [3, 0, 0, 3]          # 25, 22, 30 against BOS
    assert v["linesSeason"]["20.5"] == [2, 1, 0, 3] and v["season"] == 2027      # the newest game's season: 25, 18, 22
    assert v["lines"]["20.5"][:4] == [3, 2, 0, 5]          # the L10 counts are unchanged


def test_without_an_opponent_the_h2h_is_empty_and_without_ids_nothing_is_added():
    rows = [{"key": "k", "scope": "full_game", "stat": "points", "quotes": [{"line": 20.5}]}]
    v = board_view.player_rows(rows, _games(), {"k": None})["k"]
    assert v["linesH2h"]["20.5"] == [0, 0, 0, 0] and v["linesSeason"]["20.5"][3] == 3
    assert "linesH2h" not in board_view.player_rows(rows, _games())["k"]
