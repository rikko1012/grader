"""hc-1 roster-core H2H (h2h_core.py) on real HLTV rows: 3DMAX's five became Envy on 2026-10-15 (an org switch), and tomaszin met
that core as BESTIA (2026-03-31, 2026-04-02) and as MIBR (2026-09-02) before MIBR met Envy on 2026-10-15 (HLTV 2399260). The rows
are the local HLTV extract (tests/fixtures/h2h_core_envy_tomaszin.json, built by research/h2h_core_2026_11_20/make_fixture.py)."""
import json
import os
import threading

import pytest

import h2h_core
import workspace_h2h

FX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "h2h_core_envy_tomaszin.json"), encoding="utf-8"))
ENVY_FIVE = {13138, 13497, 14176, 20482, 21969}          # Maka, Lucky, misutaaa, Kursy, Graviti (HLTV ids)


def _core():
    ids = h2h_core.current_lineup(FX["lineup_rows"])
    names = {int(k): v for k, v in FX["names"].items()}
    return {"team_id": FX["opponent_team_id"], "lineup": [{"id": p, "name": names[p]} for p in ids], "names": names,
            "matches": h2h_core.core_matches(FX["core_rows"], ids)}


def _ser():
    ser = [{k: s[k] for k in ("date", "opponent", "value", "maps", "values")} for s in FX["series"]]
    return ser, [s["mid"] for s in FX["series"]], [s["oid"] for s in FX["series"]]


def test_current_lineup_is_envy_newest_five_by_id():
    assert set(h2h_core.current_lineup(FX["lineup_rows"])) == ENVY_FIVE
    assert h2h_core.current_lineup(FX["lineup_rows"][:4]) is None            # fewer than five: no roster, no core H2H
    six = FX["lineup_rows"] + [[99999, 1]]                                   # a one-map stand-in: the five with the most maps
    assert set(h2h_core.current_lineup(six)) == ENVY_FIVE


def test_name_rule_misses_the_org_switch_and_roster_rule_finds_it():
    ser, mids, oids = _ser()
    old = workspace_h2h.h2h(ser, "Envy", 24.5)
    assert old["n"] == 0                                                     # no series vs a team called Envy before 2026-10-15
    new = workspace_h2h.h2h(ser, "Envy", 24.5, mids=mids, oids=oids, core=_core())
    got = {(s["date"], s["opponent"]) for s in new["series"]}
    assert {("2026-04-02", "3DMAX"), ("2026-09-02", "3DMAX")} <= got
    assert all(s["via"] == "roster" and len(s["faced"]) >= 3 for s in new["series"])
    assert set(new["core"]["players"]) == {"Maka", "Lucky", "misutaaa", "Kursy", "Graviti"}
    assert new["n"] == len(new["series"]) and new["over"] + new["under"] + new["push"] == new["n"]
    sept = next(s for s in new["series"] if s["date"] == "2026-09-02")
    assert sept["value"] == 23 and sum(sept["values"]) == 23 and len(sept["maps"]) == 2
    b = workspace_h2h.board_h2h(ser, "Envy", 24.5, mids=mids, oids=oids, core=_core())
    assert (b["n"], b["over"], b["under"]) == (new["n"], new["over"], new["under"])     # board cell = card


def test_his_own_team_switch_counts_bestia_and_mibr_both():
    ser, mids, oids = _ser()
    new = workspace_h2h.h2h(ser, "Envy", 24.5, mids=mids, oids=oids, core=_core())
    met = {(s["date"], s["value"]) for s in new["series"]}
    his_team = {fx["team"] for fx in FX["series"] if (fx["date"], fx["value"]) in met}
    assert his_team == {"BESTIA", "MIBR"}                                    # he changed teams too; both meetings count


def test_a_series_with_the_core_as_his_teammates_never_counts():
    ser, mids, oids = _ser()
    core = _core()
    flipped = [o + 1 if h2h_core.hltv_mid(m) in core["matches"] else o for m, o in zip(mids, oids)]   # they were not the side he faced
    assert workspace_h2h.h2h(ser, "Envy", 24.5, mids=mids, oids=flipped, core=core)["n"] == 0


def test_same_team_meetings_still_count_as_org_meetings():
    ser, mids, oids = _ser()
    core = dict(_core(), matches={})                                          # no roster meeting at all
    ser = [dict(ser[0], opponent="Envy")] + ser[1:]
    h = workspace_h2h.h2h(ser, "Envy", 24.5, mids=mids, oids=oids, core=core)
    assert h["n"] == 1 and h["series"][0]["via"] == "org" and "faced" not in h["series"][0]


def test_without_a_core_the_answer_is_byte_for_byte_today():
    ser, mids, oids = _ser()
    assert workspace_h2h.h2h(ser, "MIBR", 20.5) == workspace_h2h.h2h(ser, "MIBR", 20.5, mids=mids, oids=oids, core=None)
    assert workspace_h2h.board_h2h(ser, "3DMAX", 20.5) == workspace_h2h.board_h2h(ser, "3DMAX", 20.5, mids=mids, oids=oids)


def test_team_core_meetings_without_him_with_the_score():
    mine = {1: {"side": 10, "team": "MIBR", "date": "2026-09-01", "faced": [1, 2, 3]},
            2: {"side": 10, "team": "MIBR", "date": "2026-08-01", "faced": [1, 2, 3]},
            3: {"side": 10, "team": "MIBR", "date": "2026-07-01", "faced": [1, 2, 3]}}
    theirs = {1: {"side": 20, "team": "3DMAX", "date": "2026-09-01", "faced": [7, 8, 9]},
              2: {"side": 20, "team": "3DMAX", "date": "2026-08-01", "faced": [7, 8, 9]},
              3: {"side": 10, "team": "MIBR", "date": "2026-07-01", "faced": [7, 8, 9]}}       # same side: teammates, not a meeting
    out = h2h_core.team_core_meetings(mine, theirs, played={2}, scores={1: {10: 2, 20: 1}})
    assert [m["hltv_match"] for m in out] == [1]                                               # 2: he played it (his own H2H)
    assert (out[0]["maps_won"], out[0]["maps_lost"], out[0]["opponent"]) == (2, 1, "3DMAX")


def test_cache_keeps_the_last_good_answer_and_never_caches_a_failure(monkeypatch):
    h2h_core._CACHE.clear()
    assert h2h_core._cached(("t", 1), lambda: {"ok": 1}) == {"ok": 1}
    monkeypatch.setattr(h2h_core, "CORE_TTL", 0)
    assert h2h_core._cached(("t", 1), lambda: 1 / 0, ttl=0) == {"ok": 1}       # failed read: the last good answer
    assert h2h_core._CACHE[("t", 1)][2] is False                              # marked failed: retried in FAIL_RETRY, not kept as good
    assert h2h_core._cached(("t", 2), lambda: 1 / 0) is None                  # nothing good yet: no core (exact-opponent H2H only)


def test_single_flight_one_read_for_many_callers():
    h2h_core._CACHE.clear()
    calls, gate = [], threading.Event()

    def read():
        calls.append(1)
        gate.wait(1)
        return {"x": 1}
    ts = [threading.Thread(target=h2h_core._cached, args=(("sf", 1), read)) for _ in range(30)]
    [t.start() for t in ts]
    gate.set()
    [t.join() for t in ts]
    assert len(calls) == 1 and h2h_core._cached(("sf", 1), read) == {"x": 1}


def test_workspace_core_h2h_uses_the_cached_history_and_core(monkeypatch):
    import workspace_api
    ser, mids, oids = _ser()
    monkeypatch.setattr(h2h_core, "opponent_core_state", lambda opp, peek=False: (_core() if opp == "Envy" else None, True))
    monkeypatch.setattr(h2h_core, "team_core", lambda nick, team, opp: [])
    full = {"ser": ser, "mids": mids, "oids": oids}
    h = workspace_api._core_h2h(None, {"nick": "tomaszin", "team": "MIBR"}, "Envy", 24.5, full=full, team_core=True)
    assert h["n"] == 2 and h["team_core"] == [] and h["core"]["min"] == 3
    b = workspace_api._core_h2h(None, {}, "Envy", 24.5, board=True, full=full)
    assert b["n"] == h["n"]
    # an opponent HLTV cannot resolve: the exact-name rule alone, as before
    assert workspace_api._core_h2h(None, {}, "Nobody", 24.5, full=full) == workspace_h2h.h2h(ser, "Nobody", 24.5)
    # an older cached history without oids: no roster meeting is claimed
    assert workspace_api._core_h2h(None, {}, "Envy", 24.5, full={"ser": ser, "mids": mids})["n"] == 0


@pytest.mark.parametrize("mid,want", [("hltv-2399260", 2399260), (2399260, 2399260), ("bo3-77", None), (None, None)])
def test_hltv_mid(mid, want):
    assert h2h_core.hltv_mid(mid) == want


class _Cur:
    """Answers h2h_core's three reads from the real HLTV rows of the fixture (the SQL itself runs on the live Postgres)."""
    def __init__(self):
        self.out, self.sql = [], []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if "array_agg" in sql:
            ids = set(params["ids"])
            self.out = [r for r in FX["core_rows"] if r[3] >= params["since"] and len(ids & set(r[4])) >= params["k"]]
        elif "FROM players" in sql:
            self.out = [(int(k), v) for k, v in FX["names"].items() if int(k) in params["ids"]]
        elif sql is h2h_core._SERIES_SQL:
            assert params["tid"] == FX["opponent_team_id"]
            self.out = [(FX["lineup_from_match"], "2026-10-14")]
        elif sql is h2h_core._LINEUP_SQL:
            assert params["tid"] == FX["opponent_team_id"] and params["mid"] == FX["lineup_from_match"]
            self.out = [tuple(r) for r in FX["lineup_rows"]]
        elif sql is h2h_core._NEWEST_SQL:                                   # the extract ends before 2026-10-15: nobody has moved
            assert params["until"] == "2026-10-15"
            self.out = [(p, FX["opponent_team_id"], "2026-10-14", FX["lineup_from_match"]) for p in params["ids"]]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self.out

    def fetchone(self):
        return self.out[0] if self.out else None


def test_read_opponent_flow_on_real_rows(monkeypatch):
    import hltv
    from datetime import date
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: FX["opponent_team_id"] if name == "Envy" else None)
    cur = _Cur()
    core = h2h_core.read_opponent(cur, "Envy", today=date(2026, 10, 15))
    assert {p["id"] for p in core["lineup"]} == ENVY_FIVE and {p["name"] for p in core["lineup"]} >= {"Maka", "Graviti"}
    assert core["matches"][2397317]["side"] == 4914 and core["matches"][2397317]["team"] == "3DMAX"
    assert core["matches"][2392665]["faced"] == sorted([13138, 13497, 14176, 21969])
    assert h2h_core.read_opponent(_Cur(), "Unknown Org") is None             # no exact HLTV team: no core
    assert all(p["since"] == "2025-10-15" for s, p in cur.sql if p and "since" in p and s is not h2h_core._NEWEST_SQL)   # 365 days back
    assert any((p or {}).get("fresh") == "2026-08-16" for _, p in cur.sql)  # the lineup is a series of the last 60 days, never an old roster


def test_board_cell_waits_while_the_core_is_not_read(monkeypatch):
    import workspace_api
    ser, mids, oids = _ser()
    full = {"ser": ser, "mids": mids, "oids": oids}
    monkeypatch.setattr(h2h_core, "opponent_core_state", lambda opp, peek=False: (None, False))     # failed read / no slot yet
    assert workspace_api._core_h2h(None, {}, "Envy", 24.5, board=True, full=full) is workspace_api.CORE_PENDING
    card = workspace_api._core_h2h(None, {}, "Envy", 24.5, full=full)                              # the card: exact name only
    assert card == workspace_h2h.h2h(ser, "Envy", 24.5)
    monkeypatch.setattr(h2h_core, "opponent_core_state", lambda opp, peek=False: (None, True))      # read fine, no core exists
    b = workspace_api._core_h2h(None, {}, "Envy", 24.5, board=True, full=full)
    assert b is not workspace_api.CORE_PENDING and b == workspace_h2h.board_h2h(ser, "Envy", 24.5)


def test_known_state_survives_a_later_failure(monkeypatch):
    h2h_core._CACHE.clear()
    assert h2h_core._cached_state(("k", 1), lambda: None) == (None, True)          # read fine: no core (known)
    monkeypatch.setattr(h2h_core, "CORE_TTL", 0)
    assert h2h_core._cached_state(("k", 1), lambda: 1 / 0, ttl=0) == (None, True)  # later failure: still known (last good)
    assert h2h_core._cached_state(("k", 2), lambda: 1 / 0) == (None, False)        # never read: unknown
    assert h2h_core.opponent_core_state("Nobody here", peek=True) == (None, False)


class _TeamCur(_Cur):
    """read_team_core's reads on real ids: tomaszin's newest team (MIBR, HLTV 11251 in the extract) and a stand-in score table."""
    MIBR = 11251

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if "ORDER BY mo.date DESC, ps.matchid DESC LIMIT 1" in sql:
            self.out = [(self.MIBR,)]
        elif sql is h2h_core._SERIES_SQL:
            assert params["tid"] == self.MIBR
            self.out = [(2399000, "2026-10-10")]
        elif sql is h2h_core._LINEUP_SQL:
            assert params["tid"] == self.MIBR and params["mid"] == 2399000
            self.out = [(FX["player_id"], 2), (101, 2), (102, 2), (103, 2), (104, 2)]
        elif sql is h2h_core._NEWEST_SQL:
            self.out = [(p, self.MIBR, "2026-10-10", 2399000) for p in params["ids"]]
        elif "array_agg" in sql:
            assert FX["player_id"] not in params["ids"] and set(params["ids"]) == {101, 102, 103, 104}
            self.out = [[2397317, self.MIBR, "MIBR", "2026-09-02", [101, 102, 103]],
                        [2390001, self.MIBR, "MIBR", "2026-05-01", [101, 102, 104]],
                        [2390002, self.MIBR, "MIBR", "2026-04-20", [101, 102]]]
        elif "SELECT DISTINCT ps.matchid" in sql:
            self.out = [(2397317,)]                                                 # he played that one: his own H2H
        elif "FROM matches WHERE" in sql:
            assert "mapid <> 0" in sql and params["m"] == [2390001]
            self.out = [(2390001, self.MIBR, 3, 13), (2390001, 4914, 3, 7), (2390001, self.MIBR, 1, 10), (2390001, 4914, 1, 13),
                        (2390001, self.MIBR, 8, 13), (2390001, 4914, 8, 11)]
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self.out[0] if self.out else None


def test_read_team_core_flow(monkeypatch):
    import hltv
    from datetime import date
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [FX["player_id"]])
    theirs = {2397317: {"side": 4914, "team": "3DMAX", "date": "2026-09-02", "faced": [13138, 13497, 14176]},
              2390001: {"side": 4914, "team": "3DMAX", "date": "2026-05-01", "faced": [13138, 13497, 21969]}}
    out = h2h_core.read_team_core(_TeamCur(), "tomaszin", "MIBR", {"matches": theirs, "names": {int(k): v for k, v in FX["names"].items()}},
                                  today=date(2026, 10, 15))
    assert len(out) == 1 and out[0]["hltv_match"] == 2390001
    assert (out[0]["team"], out[0]["opponent"], out[0]["maps_won"], out[0]["maps_lost"]) == ("MIBR", "3DMAX", 2, 1)
    assert out[0]["faced"] == ["Maka", "Lucky", "Graviti"]
    assert h2h_core.read_team_core(_TeamCur(), "tomaszin", "MIBR", None) == []


# ---------------------------------------------------------------- stale lineups (owner review 2026-11-20: ENCE -> ex-ENCE, 3DMAX -> Envy)
ST = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "h2h_core_stale_lineups.json"), encoding="utf-8"))["teams"]


class _StaleCur:
    """_lineup_of's three reads answered from the production rows in h2h_core_stale_lineups.json."""

    def __init__(self, team):
        self.t, self.out, self.sql = ST[team], [], []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if sql is h2h_core._SERIES_SQL:
            assert params["tid"] == self.t["team_id"]
            self.out = [tuple(self.t["series"])]
        elif sql is h2h_core._LINEUP_SQL:
            assert params["mid"] == self.t["series"][0]
            self.out = [tuple(r) for r in self.t["lineup_rows"]]
        elif sql is h2h_core._NEWEST_SQL:
            assert params["since"] == self.t["series"][1] and set(params["ids"]) == {r[0] for r in self.t["lineup_rows"]}
            self.out = [tuple(r) for r in self.t["newest"]]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self.out

    def fetchone(self):
        return self.out[0] if self.out else None


@pytest.mark.parametrize("team", ["3DMAX", "ENCE"])
def test_a_team_whose_five_play_for_another_team_id_has_no_lineup(team):
    from datetime import date
    t = ST[team]
    five = {r[0] for r in t["lineup_rows"]}
    assert h2h_core.moved_players(t["newest"], t["team_id"], t["series"][1], t["series"][0]) == five    # all five moved on
    assert h2h_core._lineup_of(_StaleCur(team), t["team_id"], today=date(2026, 11, 20)) is None        # no core: name-only H2H


@pytest.mark.parametrize("team, was", [("Envy", "3DMAX"), ("ex-ENCE", "ENCE")])
def test_the_team_they_play_for_now_keeps_the_five(team, was):
    from datetime import date
    got = h2h_core._lineup_of(_StaleCur(team), ST[team]["team_id"], today=date(2026, 11, 20))
    assert sorted(got) == sorted(r[0] for r in ST[was]["lineup_rows"])                                 # the same five, current id


def test_read_opponent_3dmax_is_none(monkeypatch):
    import hltv
    from datetime import date
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: ST[name]["team_id"])
    assert h2h_core.read_opponent(_StaleCur("3DMAX"), "3DMAX", today=date(2026, 11, 20)) is None


def test_moved_rule_edges():
    five = [1, 2, 3, 4, 5]
    assert h2h_core.moved_players([(1, 9, "2026-11-01", 101), (2, 9, "2026-11-01", 101)], 7, "2026-11-01", 100) == {1, 2}  # same day, later id
    assert h2h_core.moved_players([(1, 9, "2026-11-01", 99)], 7, "2026-11-01", 100) == set()      # an earlier series: no move
    assert h2h_core.moved_players([(1, 7, "2026-11-05", 120)], 7, "2026-11-01", 100) == set()     # newer series, same team id
    assert h2h_core.live_lineup(five, {1, 2}) == [3, 4, 5]                                         # one or two left: dropped
    assert h2h_core.live_lineup(five, {1, 2, 3}) is None                                           # three left: no lineup
    assert h2h_core.live_lineup(five, set()) == five


def test_a_failed_read_is_logged_never_silent_and_never_cached(caplog):
    import logging
    h2h_core._CACHE.clear()

    class QueryError(Exception):
        pgcode = "42703"

        def __str__(self):
            return 'column "nope" does not exist\nLINE 1: SELECT nope'

    def boom():
        raise QueryError()

    def dropped():
        raise ConnectionError("could not connect host=secret.example port=5432")

    with caplog.at_level(logging.WARNING, logger="h2h_core"):
        assert h2h_core._cached_state(("opp", "envy", "d"), boom) == (None, False)
        msg = " ".join(r.getMessage() for r in caplog.records)
        assert "h2h_core read failed" in msg and "42703" in msg and 'column "nope"' in msg and "LINE 1" not in msg
        assert h2h_core.FAILS["n"] >= 1 and "42703" in h2h_core.FAILS["last"]
        caplog.clear()
        assert h2h_core._cached_state(("opp", "x", "d"), dropped) == (None, False)
        msg = " ".join(r.getMessage() for r in caplog.records)
    assert "unreachable" in msg and "secret.example" not in msg                                    # connection details never logged


def test_a_database_blip_in_the_team_lookup_is_a_failure_not_no_core(monkeypatch):
    import hltv
    h2h_core._CACHE.clear()

    def blip(cur, name):
        raise ConnectionError("dropped")
    monkeypatch.setattr(hltv, "canonical_team_id", blip)
    monkeypatch.setattr(h2h_core, "_with_cursor", lambda fn: fn(None))
    assert h2h_core.opponent_core_state("Some Team") == (None, False)                             # unknown (board waits), never 'no core'
