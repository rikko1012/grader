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


def test_budget_stops_starting_players_and_keeps_the_rest_first(monkeypatch):
    """sweep 2026-11-23 (nbanfl-3 / SO-3): the history job hit its 900 s timeout five runs in a row. A run now stops starting
    players at its wall-clock deadline, exits with counts, and only players actually read are stamped (the rest go first next run)."""
    clock = [100.0]
    seen = []

    def bodies(lg, aid, seasons, cache_only):
        seen.append(aid)
        clock[0] += 10.0                                    # each player takes 10 s
        return []
    monkeypatch.setattr(snapshots, "_gamelog_bodies", bodies)
    monkeypatch.setattr(snapshots.time, "monotonic", lambda: clock[0])
    con = store.connect()
    pids = [f"nfl:player:espn:{i}" for i in range(10)]
    c = snapshots.ingest_history(con, "nfl", pids, observed_at="2026-10-25T10:00:00Z", max_players=8, deadline=135.0)
    assert seen == ["0", "1", "2", "3"] and c["budget_stopped"] == 4 and c["no_log"] == 4
    seen.clear()
    c = snapshots.ingest_history(con, "nfl", pids, observed_at="2026-10-25T10:15:00Z", max_players=3)
    assert seen == ["4", "5", "6"] and "budget_stopped" not in c        # never-read players first; no deadline = as before
    con.close()


def test_history_budget_env(monkeypatch):
    monkeypatch.delenv("SPORTS_HISTORY_BUDGET_S", raising=False)
    assert snapshots.history_budget_s() == snapshots.HISTORY_BUDGET_S < 900
    for v, want in (("300", 300.0), ("x", snapshots.HISTORY_BUDGET_S), ("0", snapshots.HISTORY_BUDGET_S), ("-5", snapshots.HISTORY_BUDGET_S)):
        monkeypatch.setenv("SPORTS_HISTORY_BUDGET_S", v)
        assert snapshots.history_budget_s() == want


def test_history_cli_splits_the_budget_between_leagues(monkeypatch):
    monkeypatch.setenv("SPORTS_INGEST", "1")
    monkeypatch.setenv("SPORTS_HISTORY_BUDGET_S", "600")
    got = {}
    monkeypatch.setattr(snapshots, "board_player_ids", lambda con, lg, now: [f"{lg}:player:espn:1"])
    monkeypatch.setattr(snapshots, "ingest_history", lambda con, lg, ids, **kw: got.setdefault(lg, kw["deadline"]) and {"players": 0})
    t = snapshots.time.monotonic()
    snapshots.main(["history", "nba", "nfl", "--max-players", "60"])
    assert 0 < got["nba"] - t <= 300.5 and 300 < got["nfl"] - t <= 600.5


def test_a_run_that_reads_no_player_before_its_budget_fails(monkeypatch):
    """Advisor review: a run whose board scan used the whole budget must not exit 0 (that would clear job_failing while
    nothing refreshes)."""
    import pytest
    monkeypatch.setenv("SPORTS_INGEST", "1")
    monkeypatch.setattr(snapshots, "board_player_ids", lambda con, lg, now: [f"{lg}:player:espn:1"])
    monkeypatch.setattr(snapshots, "ingest_history", lambda con, lg, ids, **kw: {"players": 0, "unchanged": 0, "no_log": 0, "errors": 0, "budget_stopped": 1})
    with pytest.raises(SystemExit) as e:
        snapshots.main(["history", "nba", "nfl"])
    assert e.value.code == 2
    assert not snapshots.history_starved({"nba": {"players": 0, "no_log": 1, "budget_stopped": 5}})      # read one: not starved
    assert not snapshots.history_starved({"nba": {"players": 0}})                                        # nothing to read: fine
