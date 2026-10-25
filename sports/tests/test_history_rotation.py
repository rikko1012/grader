"""Player game logs refresh in small rotating batches (sports/snapshots.ingest_history): least recently checked
first, so every board player is re-read instead of always the first max_players in id order. No network."""
from sports import snapshots, store


def test_small_runs_rotate_through_every_player(monkeypatch):
    seen = []
    monkeypatch.setattr(snapshots, "_gamelog_bodies", lambda lg, aid, seasons, cache_only: seen.append(aid) or [])
    con = store.connect()
    pids = [f"nfl:player:espn:{i}" for i in range(10)]
    snapshots.ingest_history(con, "nfl", pids, observed_at="2026-10-25T10:00:00Z", max_players=4)
    snapshots.ingest_history(con, "nfl", pids, observed_at="2026-10-25T10:15:00Z", max_players=4)
    snapshots.ingest_history(con, "nfl", pids, observed_at="2026-10-25T10:30:00Z", max_players=4)
    assert sorted(seen[:10], key=int) == [str(i) for i in range(10)]        # all ten covered within three runs
    assert seen[10:] == seen[:2]                                            # then the oldest-checked again
    con.close()
