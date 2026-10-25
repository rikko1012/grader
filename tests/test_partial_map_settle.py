"""2026-10-25 audit (research/esports_accuracy_2026_10_25): a map page captured before the map finished must never settle
a pick. hltv:2399658 map 2 (mapstatsid 241953) was stored with 24 round rows of a 29-round overtime map, and 19 P1 units
settled on its partial scoreboard (every one 1-8 kills / headshots short). Offline: rows are TEST FIXTURES shaped like
the real case."""
from datetime import timedelta

import hltv
import picklog
from tests.test_audit13_settle import (B, NO_MAPS, START, add_pick, at, db, fake_hltv, hltv_rows, mi, pick,  # noqa: F401
                                       sched)


class _Cur:
    """Answers _SERIES_SQL with fixed rows (matchid, date, number, map, k, hs, own, opp, opp name, team, lan, opp id,
    round rows)."""
    def __init__(self, rows):
        self.rows = rows

    def execute(self, q, a=None):
        self.q = q

    def fetchall(self):
        return self.rows


def _row(number, k, hs, own, opp, nr):
    import datetime as dt
    return (2399658, dt.date(2026, 10, 17), number, "Mirage" if number == 1 else "Ancient", k, hs, own, opp, "Opp", "Team",
            False, 11251, nr)


def test_series_rows_marks_a_map_whose_round_history_is_shorter_than_its_score():
    rows = hltv._series_rows(_Cur([_row(1, 10, 7, 13, 5, 18), _row(2, 26, 14, 16, 13, 24)]), [25807], "2000-01-01")
    assert [r.get("partial") for r in sorted(rows, key=lambda r: r["number"])] == [None, True]
    assert "map_rounds" in hltv._SERIES_SQL


def test_complete_maps_and_maps_without_round_history_are_unchanged():
    rows = hltv._series_rows(_Cur([_row(1, 10, 7, 13, 5, 18), _row(2, 28, 15, 16, 13, 29)]), [25807], "2000-01-01")
    assert all("partial" not in r for r in rows)
    rows = hltv._series_rows(_Cur([_row(1, 10, 7, 13, 5, 0), _row(2, 28, 15, 16, 13, 0)]), [25807], "2000-01-01")
    assert all("partial" not in r for r in rows)          # no stored round history (older pages): nothing to compare


def test_settle_totals_waits_on_a_partial_map(monkeypatch):
    class Con:
        def cursor(self):
            class C(_Cur):
                def __enter__(s): return s
                def __exit__(s, *a): return False
            return C([_row(1, 10, 7, 13, 5, 18), _row(2, 26, 14, 16, 13, 24)])
        def close(self): pass
    monkeypatch.setattr(hltv, "dsn", lambda: "x")
    monkeypatch.setattr(hltv, "_connect", lambda: Con())
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [25807])
    out = hltv.settle_totals("player", "Team", 2399658)
    assert out["found"] and out["kills"] is None and out["headshots"] is None and out["partial"]


def test_scope_result_waits_while_a_line_map_is_partial_then_settles_on_the_full_map(db, mi, monkeypatch):
    mi(schedule=[sched(901, START)])
    series = fake_hltv(monkeypatch, hltv_rows(901, "2026-10-10", kills=(10, 26)))
    series[1]["partial"] = True                                      # map 2 captured at 24 of 29 rounds
    add_pick()
    at(monkeypatch, START + timedelta(minutes=180))
    res = picklog.scope_result(pick(db))
    assert res["status"] == "wait" and "incomplete" in res["reason"]
    picklog.settle(B(), NO_MAPS)
    assert pick(db)["status"] == "open"                              # never settled on the partial scoreboard
    series[1].pop("partial"); series[1]["kills"] = 28                # the finished map is stored
    picklog.settle(B(), NO_MAPS)
    r = pick(db)
    assert (r["status"], r["actual"]) == ("settled", 38)


def test_a_partial_map_outside_the_line_scope_does_not_hold_a_map1_line(db, mi, monkeypatch):
    mi(schedule=[sched(901, START)])
    series = fake_hltv(monkeypatch, hltv_rows(901, "2026-10-10", kills=(10, 26)))
    series[1]["partial"] = True
    add_pick(scope="1", line=9.5)
    res = picklog.scope_result(pick(db))
    assert res["status"] == "ok" and res["total"] == 10
