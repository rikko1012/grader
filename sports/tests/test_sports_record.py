"""The forecast log: append-only saves, settlement from ESPN box scores, and the report."""
from datetime import datetime, timezone

import pytest

from sports import config, edges, espn, record
from sports.tests.conftest import fixture
from sports.tests.test_sports_edges import offer, quote

NBA = config.league("nba")
BEFORE = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
TIP = "2026-10-23T01:30:00Z"                              # DEN @ OKC, the box-score fixture


def _rows(player, key, line, p_over=0.5, market="points", platform="prizepicks", oid=None, start=TIP):
    o = offer(player=player, player_key=key, line=line, market=market, start=start, platform=platform,
              offer_id=oid or f"{platform}:{key}:{market}:{line}", game="DEN @ OKC")
    return edges.build(NBA, [o], [quote(player=player, player_key=key, line=line, market=market, p_over=p_over,
                                        start=start)])


def test_save_is_append_only_and_pre_game_only(con):
    rows = _rows("Nikola Jokic", "nikola jokic", 25.5, 0.55)
    assert record.save(con, rows, now=BEFORE) == 1
    assert record.save(con, rows, now=BEFORE) == 0                   # nothing moved: no new row
    moved = _rows("Nikola Jokic", "nikola jokic", 25.5, 0.58)
    assert record.save(con, moved, now=BEFORE) == 1                  # chance moved 3 points: a new row
    assert record.save(con, moved, now=AFTER) == 0                   # the game has started: never saved
    got = con.execute("SELECT chance, model, fit, sharp_source FROM forecasts ORDER BY id").fetchall()
    assert [round(r["chance"], 2) for r in got] == [0.55, 0.58]
    assert got[0]["model"] == record.MODEL_TAG and got[0]["fit"] and got[0]["sharp_source"] == "pinnacle"


@pytest.fixture
def espn_box(monkeypatch):
    sb = [espn._event(e) for e in fixture("espn_scoreboard_nba.json")["events"]]
    box = espn.parse_summary(fixture("espn_summary_nba.json"), "basketball")
    monkeypatch.setattr(espn, "scoreboard", lambda lg, a, b, refresh=False: sb)
    monkeypatch.setattr(espn, "summary", lambda lg, eid, refresh=False: box if eid == "401909094" else
                        {"completed": True, "start": None, "players": {}})


def test_settle_over_under_void_pending(espn_box, con):
    for player, key, line, p in (("Nikola Jokic", "nikola jokic", 12.5, 0.4),        # scored 13: over
                                 ("Aaron Gordon", "aaron gordon", 21.5, 0.6),         # scored 20: under
                                 ("Zeke Nnaji", "zeke nnaji", 4.5, 0.5),              # did not play: void
                                 ("Nobody Here", "nobody here", 9.5, 0.5)):           # not found: pending
        record.save(con, _rows(player, key, line, p), now=BEFORE)
    assert record.settle(con, now=AFTER) == {"over": 1, "under": 1, "void": 1, "pending": 1}
    got = {r["player"]: (r["outcome"], r["actual"]) for r in con.execute(
        "SELECT f.player, s.outcome, s.actual FROM forecasts f JOIN settlements s ON s.forecast_id = f.id")}
    assert got["Nikola Jokic"] == ("over", 13.0) and got["Aaron Gordon"] == ("under", 20.0)
    assert got["Zeke Nnaji"] == ("void", None) and got["Nobody Here"][0] == "pending"
    assert record.settle(con, now=AFTER) == {"pending": 1}           # settled picks are never re-graded
    n = con.execute("SELECT COUNT(*) FROM settlements").fetchone()[0]
    assert n == 4                                                     # an unchanged pending is not re-written


def test_settle_waits_for_the_game(espn_box, con):
    record.save(con, _rows("Nikola Jokic", "nikola jokic", 12.5), now=BEFORE)
    assert record.settle(con, now=datetime(2026, 10, 23, 3, 0, tzinfo=timezone.utc)) == {}


def test_football_player_missing_from_the_box_uses_his_game_log(monkeypatch, con):
    nfl = config.league("nfl")
    sb = [{"id": "401873006", "start": "2026-10-18T20:25:00Z", "completed": True}]
    box = espn.parse_summary(fixture("espn_summary_nfl.json"), "football")
    monkeypatch.setattr(espn, "scoreboard", lambda lg, a, b, refresh=False: sb)
    monkeypatch.setattr(espn, "summary", lambda lg, eid, refresh=False: box)
    monkeypatch.setattr(espn, "search_athlete", lambda lg, name: [{"id": name, "name": name, "team": "KC"}])
    logs = {"Quiet Receiver": [{"event_id": "401873006", "parts": {"rec": 0, "rec_yds": 0}}], "Inactive Guy": []}
    monkeypatch.setattr(espn, "gamelog", lambda lg, aid, seasons=1, refresh=False: logs[aid])
    start = "2026-10-18T20:25:00Z"
    for name in ("Quiet Receiver", "Inactive Guy"):
        o = offer(league="nfl", player=name, player_key=name.lower(), market="rec_yds", line=10.5, start=start,
                  offer_id=f"x:{name}")
        rows = edges.build(nfl, [o], [quote(league="nfl", player=name, player_key=name.lower(), market="rec_yds",
                                            line=10.5, start=start)])
        record.save(con, rows, now=datetime(2026, 10, 17, tzinfo=timezone.utc))
    assert record.settle(con, now=datetime(2026, 10, 19, 12, tzinfo=timezone.utc)) == {"under": 1, "void": 1}
    for name, want in (("Quiet Receiver", ("under", 0.0)), ("Inactive Guy", ("void", None))):
        got = con.execute("SELECT s.outcome, s.actual FROM settlements s JOIN forecasts f ON f.id = s.forecast_id "
                          "WHERE f.player = ?", (name,)).fetchone()
        assert (got["outcome"], got["actual"]) == want, name


def test_report_counts_each_prop_once_and_scores_it(con):
    games = [("2026-10-23T01:30:00Z", "g1"), ("2026-10-23T23:00:00Z", "g2"), ("2026-10-24T02:00:00Z", "g3")]
    fid = 0
    for i, (start, g) in enumerate(games):
        for j, outcome in enumerate(("under", "under", "over")):
            for platform in ("prizepicks", "underdog"):       # the same prop on two platforms
                fid += 1
                con.execute("INSERT INTO forecasts (id, recorded_at, league, platform, offer_id, player, player_key, "
                            "game, start, market, line, side, chance, model) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (fid, "2026-10-22T00:00:00Z", "nba", platform, f"{platform}:{i}:{j}", f"P{j}", f"p{j}",
                             g, start, "points", 20.5, "under", 0.6, record.MODEL_TAG))
                con.execute("INSERT INTO settlements (forecast_id, settled_at, outcome) VALUES (?,?,?)",
                            (fid, "2026-10-25T00:00:00Z", outcome))
    rep = record.report(con)
    al = rep["all"]
    assert rep["offers"] == 18 and al["props"] == 9 and al["hits"] == 6 and al["games"] == 3
    assert al["hit_rate"] == pytest.approx(2 / 3) and al["mean_chance"] == pytest.approx(0.6)
    assert al["brier"] == pytest.approx((6 * 0.4 ** 2 + 3 * 0.6 ** 2) / 9)
    lo, hi = al["hit_rate_95"]
    assert lo <= 2 / 3 <= hi
    assert set(rep["by_band"]) == {"60%+"} and rep["by_band"]["60%+"]["props"] == 9
