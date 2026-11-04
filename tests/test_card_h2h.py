"""Research cards carry head-to-head vs tonight's opponent and CS2 player stats (owner 2026-11-04). Display only."""
import workspace_api
import workspace_h2h


SER = [{"date": "2026-10-30", "opponent": "G2", "value": 40}, {"date": "2026-10-20", "opponent": "FaZe", "value": 30},
       {"date": "2026-10-10", "opponent": "g2 ", "value": 33}, {"date": "2026-09-01", "opponent": "G2 Ares", "value": 50}]


def test_h2h_counts_only_exact_opponent_meetings_at_the_line():
    h = workspace_h2h.h2h(SER, "G2", 33)
    assert (h["n"], h["over"], h["under"], h["push"], h["avg"]) == (2, 1, 0, 1, 36.5)    # the academy (G2 Ares) never counts
    assert [s["date"] for s in h["series"]] == ["2026-10-30", "2026-10-10"]


def test_h2h_without_an_opponent_or_meetings():
    assert workspace_h2h.h2h(SER, "", 33) is None and workspace_h2h.h2h(SER, "G2", None) is None
    assert workspace_h2h.h2h(SER, "Vitality", 33)["n"] == 0


def test_cs2_map_stats_read_the_rows_already_fetched():
    maps = [{"date": "2026-10-30", "kills": 20, "headshots": 10, "rounds": 20, "won": True},
            {"date": "2026-10-29", "kills": 10, "headshots": None, "rounds": 20, "won": False},
            {"date": "2026-10-28", "kills": None, "headshots": None, "rounds": 20, "won": True}]
    rows = {r["label"]: r["value"] for r in workspace_api._map_stats(maps)}
    assert rows["Maps played"] == 2 and rows["Kills per map"] == 15.0 and rows["Headshots per map"] == 10.0 and rows["Maps won"] == "1 of 2"
    assert "Kills per round" not in rows and "Headshot share" not in rows        # the saved inputs already carry those
    assert workspace_api._map_stats([]) is None
