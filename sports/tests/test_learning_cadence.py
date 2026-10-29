"""New-data triggers measure data since the latest actual fit, including challengers."""
import sqlite3

import pytest

from sports.model import datasets, forward, learning


@pytest.mark.parametrize("sport", ["nba", "nfl"])
def test_new_data_trigger_uses_latest_fit_snapshot(tmp_path, monkeypatch, sport):
    path = tmp_path / "history.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE games (game_id TEXT, league TEXT, start_utc TEXT)")
        con.execute(f"CREATE TABLE {sport}_pg (game_id TEXT)")
        con.execute("INSERT INTO games VALUES ('old', ?, '2026-10-20T00:00:00Z')", (sport,))
        con.executemany(f"INSERT INTO {sport}_pg VALUES (?)", [("old",)] * learning.SAMPLE_TRIGGER[sport])
        con.execute("INSERT INTO games VALUES ('new', ?, '2026-10-27T00:00:00Z')", (sport,))
        con.execute(f"INSERT INTO {sport}_pg VALUES ('new')")
    monkeypatch.setattr(datasets, "connect", lambda **kw: sqlite3.connect(path))
    monkeypatch.setattr(learning, "now_utc", lambda: "2026-10-28T00:00:00Z")
    monkeypatch.setattr(forward, "load_artifact", lambda s: {
        "fitted_at": "2026-10-01T00:00:00Z", "data_as_of": "2026-10-01T00:00:00Z"})
    with sqlite3.connect(":memory:") as con:
        con.execute("CREATE TABLE fits (sport TEXT, artifact_sha TEXT, fitted_at TEXT, data_as_of TEXT)")
        # An older fit with a later data cutoff must not be combined with the newest fit.
        con.execute("INSERT INTO fits VALUES (?, 'older', '2026-10-25T00:00:00Z', '2026-10-28T00:00:00Z')", (sport,))
        con.execute("INSERT INTO fits VALUES (?, 'latest', '2026-10-27T00:00:00Z', '2026-10-26T00:00:00Z')", (sport,))
        result = learning.retrain_due(sport, con)
        assert result["new_rows"] == 1
        assert result["days_since_fit"] == 1
        assert result["due"] is False
        con.execute("DELETE FROM fits")
        assert learning.retrain_due(sport, con)["due"] is True
