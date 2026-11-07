"""pgn-1 pre-game news archive (pregame_news/): append-only, change-only, tombstones only from complete lists,
point-in-time reads, bounded storage, the CS2 read-only pick-log scanner and 700 concurrent writers."""
import json
import sqlite3
import threading
import time

import pytest

import pregame_news as pn


@pytest.fixture
def con(tmp_path):
    c = pn.connect(tmp_path / "news.db")
    yield c
    c.close()


def _rows(con):
    return [dict(r) for r in con.execute("SELECT * FROM news ORDER BY id")]


def test_change_only_appends_and_tombstones(con):
    a = pn.observe(con, "sports", "nba", "injury", "league:nba",
                   {("p1", ""): {"status": "Questionable"}, ("p2", ""): {"status": "Out"}},
                   observed_at="2026-11-05T10:00:00Z", full=True)
    assert a["new"] == 2
    b = pn.observe(con, "sports", "nba", "injury", "league:nba",
                   {("p1", ""): {"status": "Questionable"}, ("p2", ""): {"status": "Out"}},
                   observed_at="2026-11-05T10:15:00Z", full=True)
    assert b["unchanged"] == 2 and b["new"] == b["changed"] == 0
    c = pn.observe(con, "sports", "nba", "injury", "league:nba", {("p1", ""): {"status": "Out"}},
                   observed_at="2026-11-05T10:30:00Z", full=True)
    assert c["changed"] == 1 and c["absent"] == 1
    rows = _rows(con)
    assert len(rows) == 4
    assert json.loads(rows[-1]["payload"]) == {"absent": True} and rows[-1]["subject"] == "p2"
    # p2 listed again -> a new row (back on the report)
    d = pn.observe(con, "sports", "nba", "injury", "league:nba",
                   {("p1", ""): {"status": "Out"}, ("p2", ""): {"status": "Out"}}, observed_at="2026-11-05T11:00:00Z",
                   full=True)
    assert d["changed"] == 1


def test_partial_list_never_writes_tombstones(con):
    pn.observe(con, "sports", "nba", "depth", "team:nba:BOS", {("p1", ""): {"depth": 0}, ("p2", ""): {"depth": 1}},
               full=True)
    r = pn.observe(con, "sports", "nba", "depth", "team:nba:BOS", {("p1", ""): {"depth": 0}}, full=False)
    assert r["absent"] == 0
    r = pn.observe(con, "sports", "nba", "depth", "team:nba:BOS", {}, full=False)
    assert r["absent"] == 0


def test_rows_are_append_only(con):
    pn.observe(con, "cs2", "cs2", "roster", "match", {("7", "m1"): {"is_standin": False}})
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE news SET payload = '{}'")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM news")


def test_as_of_is_point_in_time(con):
    pn.observe(con, "sports", "nfl", "injury", "league:nfl", {("p9", ""): {"status": "Questionable"}},
               observed_at="2026-11-01T12:00:00Z", full=True)
    pn.observe(con, "sports", "nfl", "injury", "league:nfl", {("p9", ""): {"status": "Out"}},
               observed_at="2026-11-01T16:00:00Z", full=True)
    pn.observe(con, "sports", "nfl", "injury", "league:nfl", {}, observed_at="2026-11-02T12:00:00Z", full=True)
    assert pn.as_of(con, "sports", "p9", "2026-11-01T11:59:59Z") == []
    assert pn.as_of(con, "sports", "p9", "2026-11-01T13:00:00Z")[0]["news"]["status"] == "Questionable"
    assert pn.as_of(con, "sports", "p9", "2026-11-01T17:00:00Z")[0]["news"]["status"] == "Out"
    assert pn.as_of(con, "sports", "p9", "2026-11-03T00:00:00Z") == []          # cleared from the report


def test_budget_stops_new_rows_and_never_deletes(con, monkeypatch):
    pn.observe(con, "sports", "nba", "injury", "league:nba", {("p1", ""): {"s": 1}})
    monkeypatch.setenv("PREGAME_NEWS_MAX_MB", "0.000001")
    r = pn.observe(con, "sports", "nba", "injury", "league:nba", {("p1", ""): {"s": 2}, ("p3", ""): {"s": 1}})
    assert r["budget_skipped"] == 2 and r["changed"] == r["new"] == 0
    assert len(_rows(con)) == 1
    assert pn.stats(con)["meta"]["budget_skipped"] == "2"


def test_payload_is_capped(con):
    pn.observe(con, "sports", "nba", "injury", "league:nba", {("p1", ""): {"x": "y" * 10000}})
    p = _rows(con)[0]["payload"]
    assert len(p) <= pn.MAX_PAYLOAD and json.loads(p)["truncated"] is True


def test_switch_off(con, monkeypatch):
    monkeypatch.setenv("PREGAME_NEWS", "0")
    assert pn.observe(con, "sports", "nba", "injury", "league:nba", {("p1", ""): {"s": 1}}).get("disabled")
    assert _rows(con) == []


def test_hot_writer_700_concurrent_writers(tmp_path):
    """700 simultaneous graded lines (the owner's load rule): change-only writes stay fast, nothing raises, and a
    repeated payload never reaches the database."""
    w = pn.HotWriter(lambda: tmp_path / "hot.db")
    errs = []

    def one(i):
        try:
            w.write("cs2", "cs2", "roster", "match", str(i % 70), "m1", {"is_standin": i % 2 == 0, "n": i % 70})
        except Exception as e:      # noqa: BLE001
            errs.append(e)
    t0 = time.time()
    ts = [threading.Thread(target=one, args=(i,)) for i in range(700)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    dt = time.time() - t0
    assert not errs and w.errors == 0
    c = pn.connect(tmp_path / "hot.db")
    n = c.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    assert n == 70                    # i % 70 fixes both payload fields: one row per subject
    assert dt < 30
    assert w.write("cs2", "cs2", "roster", "match", "1", "m1", {"is_standin": False, "n": 1}) is False   # cached


def _picklog(monkeypatch, tmp_path):
    import picklog
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    monkeypatch.setattr(picklog, "data_revision", lambda: None)
    return picklog


def _grade(picklog, pid, start, standin=False, test=2):
    res = {"grade": {"prop": "kills", "line": 30.5, "line_type": "standard", "p_more": 0.55, "p_less": 0.45,
                     "lambda_final": 31.0, "scope": "12"},
           "player_block": {"player": {"slug": f"p{pid}", "nick": "a", "id": pid}},
           "grader_inputs": {"roster_confirmed": True, "is_standin": standin, "standin_for": "X" if standin else None,
                             "on_listed_team": True, "scout": False, "perf": {"lambda": 31}},
           "next_match": {"slug": f"x-vs-y-{pid}", "start": start}}
    v = {"state": "GRADE", "side": "More", "p": 0.55 + (0.05 if standin else 0), "grade": "B-", "units": 0.5}
    return picklog.log_grade(res, v, test=test)


def test_cs2_scan_reads_the_pick_log_read_only_and_archives_each_forecast(monkeypatch, tmp_path):
    from pregame_news import cs2_picks
    picklog = _picklog(monkeypatch, tmp_path)
    _grade(picklog, 11, "2099-12-16T15:00:00Z")
    _grade(picklog, 11, "2099-12-16T15:00:00Z", standin=True)          # a changed forecast: a stand-in now
    _grade(picklog, 12, "2099-12-16T15:00:00Z", test=1)                # an owner load test: never archived
    _grade(picklog, 13, "2000-01-01T00:00:00Z")                        # logged after the start: not pre-game
    before = (tmp_path / "picks.db").read_bytes()
    news = tmp_path / "pregame_news.db"
    r = cs2_picks.scan(tmp_path / "picks.db", news)
    assert r["archived"] == 2 and r["read"] == 4
    assert (tmp_path / "picks.db").read_bytes() == before                 # the pick log is never written
    c = pn.connect(news)
    rows = [json.loads(x["payload"]) for x in c.execute("SELECT payload FROM news ORDER BY id")]
    assert [x["is_standin"] for x in rows] == [False, True] and rows[1]["standin_for"] == "X"
    assert "perf" not in rows[0]
    assert cs2_picks.scan(tmp_path / "picks.db", news)["read"] == 0       # cursor kept
    assert pn.as_of(c, "cs2", "12", "2100-01-01T00:00:00Z") == []


def test_cs2_scan_without_a_pick_log_does_nothing(tmp_path):
    from pregame_news import cs2_picks
    assert cs2_picks.scan(tmp_path / "missing.db", tmp_path / "n.db") == {"read": 0, "archived": 0, "skipped": 0}


def test_frozen_forecast_writer_is_not_touched():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "pregame_news" not in (root / "picklog.py").read_text(encoding="utf-8")
