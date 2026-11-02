"""Results reads never acquire the learning worker's schema/write lock."""
import collections
import json
import random
import sqlite3

import pytest

import learning
import results_view
import server


def test_results_reads_committed_export_while_learning_is_writing(tmp_path, monkeypatch):
    path = tmp_path / "learning # report.db"
    monkeypatch.setattr(learning, "DB_PATH", path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    old = {"at": "committed", "rows": [], "pushes": []}
    new = dict(old, at="next cycle")
    con.execute("INSERT INTO meta VALUES('p1_units', ?)", (json.dumps(old),))
    con.commit()
    # Forbid schema setup on a member read (old code fails here, rather than
    # making the regression suite wait for twelve 30-second lock timeouts).
    monkeypatch.setattr(learning, "connect", lambda: pytest.fail("Results attempted a schema migration"))
    con.execute("BEGIN IMMEDIATE")
    con.execute("UPDATE meta SET v=?", (json.dumps(new),))
    try:
        assert server._p1_export() == old
        assert server.results_payload()["as_of"] == "committed"
        con.commit()
        assert server._p1_export() == new
    finally:
        con.close()


def test_results_does_not_create_database_or_initialize_schema(tmp_path, monkeypatch):
    path = tmp_path / "not-created.db"
    monkeypatch.setattr(learning, "DB_PATH", path)
    assert server._p1_export() == {}
    assert not path.exists()
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE unrelated(x)")
    assert server._p1_export() == {}
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("unrelated",)]


def test_corrupt_export_is_not_presented_as_an_empty_record(tmp_path, monkeypatch):
    path = tmp_path / "bad.db"
    monkeypatch.setattr(learning, "DB_PATH", path)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE meta(k TEXT, v TEXT)")
        con.execute("INSERT INTO meta VALUES('p1_units', 'broken')")
    with pytest.raises(json.JSONDecodeError):
        server._p1_export()


def expanded_ci(picks, B, seed):
    """The released implementation, kept as an exact numerical oracle."""
    groups = collections.defaultdict(list)
    for p in picks:
        groups[p['match']].append(p['win'])
    keys = list(groups)
    if len(keys) < 5:
        return None
    rng, samples = random.Random(seed), []
    for _ in range(B):
        values = [w for k in (rng.choice(keys) for _ in keys) for w in groups[k]]
        samples.append(sum(values) / len(values))
    samples.sort()
    return [round(samples[int(.025 * B)], 4), round(samples[int(.975 * B) - 1], 4)]


@pytest.mark.parametrize('seed', [0, 7, 91])
@pytest.mark.parametrize('matches', [0, 4, 5, 37])
def test_results_interval_identical_with_unequal_match_sizes(matches, seed):
    rng = random.Random(seed)
    picks = [dict(match=f'm{m}', win=rng.randrange(2)) for m in range(matches) for _ in range(1 + m * 7 % 83)]
    assert results_view._ci(picks, 1000, seed) == expanded_ci(picks, 1000, seed)
