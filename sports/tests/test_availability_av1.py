"""av-1 availability hold (owner 2026-11-09): a player ESPN rules out for a game gets no side, letter, stake or slip leg
on that game's lines; projections, chances and logged forecasts stay as they are. Plus the freshness rule shared by the
board display, the live NBA teammate-absence input and the shadow inputs: an injury entry is current while it is in the
newest report read (last_seen_at), never judged by its first sighting. Every name in the fixtures is a TEST FIXTURE."""
import json
from types import SimpleNamespace

import pregame_news as core
import pytest

from sports import availability as av
from sports import picks, snapshots, store
from sports.contracts import ContextSnapshot, Membership, Player, Team
from sports.model import forward, shadow
from sports.slips import pool
from sports.tests import helpers as h
from sports.tests.test_sanity_guard import _fc, _row

KAT = "nba:player:espn:3134907"            # NY, the forecast player
MATE = "nba:player:espn:3934672"           # NY teammate
GAME = "2026-10-23T23:00:00Z"              # h.START: a 2026-10-23 US date


def inj(pid, status, at, present="yes", as_of="2026-10-23T18:00:00Z", league="nba"):
    return ContextSnapshot(league=league, kind="injury", subject_id=pid, event_id=None, status=status, present=present,
                           detail="Ankle", as_of=as_of, observed_at=at, known_at=at, source_updated_at=as_of,
                           source_time_unknown=as_of is None)


def desig(fs="OUT", rd="2026-10-25", date="2026-10-23T18:00Z"):
    return {"status": "Out", "fantasy_status": fs, "return_date": rd, "espn_date": date}


# ------------------------------------------------------------------------------------------------------ pure rules
def test_current_is_the_newest_read_and_an_outage_keeps_the_last_state():
    a = inj(KAT, "out", "2026-10-20T10:00:00Z")                         # first seen 3 days ago, seen again now
    b = inj(MATE, "out", "2026-10-20T10:00:00Z")                        # dropped off the report yesterday
    recs = [(a, "2026-10-23T19:30:00Z"), (b, "2026-10-22T19:30:00Z")]
    read, cur = av.current(recs, "2026-10-23T20:00:00Z")
    assert read == "2026-10-23T19:30:00Z" and set(cur) == {KAT}
    # feed down for 6 h: nothing moves (relative rule), the last state is kept
    assert set(av.current(recs, "2026-10-24T01:30:00Z")[1]) == {KAT}
    # no read for more than 24 h: nothing is current (never a hold from a stale report)
    assert av.current(recs, "2026-10-25T01:30:00Z")[1] == {}
    # a sighting after `now` (a read racing the build) counts as now
    assert set(av.current([(a, "2026-10-23T21:00:00Z"), (b, None)], "2026-10-23T20:00:00Z")[1]) == {KAT}


def test_hold_rules():
    out = inj(KAT, "out", "2026-10-23T19:00:00Z")
    assert av.hold(out, desig(), GAME) == "av_out"
    assert av.hold(inj(KAT, "injured_reserve", "x"), desig("IR", "2026-12-06"), GAME) == "av_injured_reserve"
    assert av.hold(inj(KAT, "suspended", "x"), desig("SUSP"), GAME) == "av_suspended"
    assert av.hold(out, desig("PUP-R", "2026-11-22"), GAME) == "av_out"
    assert av.hold(out, desig("RESERVE-EX", "2026-11-29"), GAME) == "av_out"
    # not a ruling: NFL early-week "Out" with fantasyStatus QUESTIONABLE / DOUBTFUL / GTD, IR designated to return,
    # and ESPN's bare "Out" with no designation or return date (live 2026-11-11: a QB already named Sunday's starter)
    for fs in ("QUESTIONABLE", "DOUBTFUL", "PROBABLE", "GTD", "IR-R", None, ""):
        assert av.hold(out, desig(fs), GAME) is None, fs
    assert av.hold(out, desig(None, None), GAME) is None
    assert av.hold(out, desig(rd=None), GAME) is None                             # no return date: no ruling
    # ESPN expects him back for this game (return date = the game's US date; 23:00Z is still the 23rd in New York)
    assert av.hold(out, desig(rd="2026-10-23"), GAME) is None
    assert av.hold(out, desig(rd="2026-10-23"), "2026-10-24T02:30:00Z") is None    # 22:30 ET on the 23rd
    assert av.hold(out, desig(rd="2026-10-24"), "2026-10-24T02:30:00Z") == "av_out"
    # Questionable / Doubtful / Probable / Day-to-day never hold
    for st in ("questionable", "doubtful", "probable", "day_to_day", "unknown"):
        assert av.hold(inj(KAT, st, "x"), desig(), GAME) is None, st
    # missing data never hides a player
    assert av.hold(None, desig(), GAME) is None
    assert av.hold(out, None, GAME) is None                                        # designation unknown
    assert av.hold(out, desig(date="2026-10-20T18:00Z"), GAME) is None             # another (older) ESPN entry
    assert av.hold(inj(KAT, "out", "x", present="no"), desig(), GAME) is None
    # NFL official game-day list
    assert av.hold(None, None, GAME, {"active": False}) == "av_inactive"
    assert av.hold(None, None, GAME, {"did_not_play": True}) == "av_inactive"
    assert av.hold(None, None, GAME, {"active": None, "did_not_play": None}) is None
    assert av.hold(None, None, GAME, {"active": True, "starter": False}) is None


def test_switch_off_holds_nothing(monkeypatch):
    monkeypatch.setenv("SPORTS_AVAIL_HOLD", "0")
    assert av.hold(inj(KAT, "out", "x"), desig(), GAME) is None
    assert av.hold(None, None, GAME, {"active": False}) is None


def test_hold_text_is_a_plain_fact():
    for code in av.CODES:
        t = av.text(code).lower()
        for banned in ("unconfirmed", "may ", "might", "maybe", "likely", "probably", "unsure", "expected"):
            assert banned not in t, (code, banned)
    assert av.x_of("av_out") == {"b": "av_out", "t": "Ruled out (ESPN injury report)", "v": "av-1"}
    assert av.text("av_inactive") == "Inactive (official game-day list)"


def test_guard_row_holds_every_quote_keeps_chances_and_logs_the_line():
    fcs = {"f2": _fc("f2", 0.62, 0.38, 26.0, line=24.5)}
    quotes = [{"platform": "underdog", "record_id": "r4", "line": 24.5, "forecast_id": "f2"},
              {"platform": "betrivers", "record_id": "r5", "line": 24.5,
               "line_read": {"f": "f2", "ln": 24.5, "po": 0.62, "pu": 0.38, "pp": 0}},
              {"platform": "fanduel", "record_id": "r6", "line": 22.5}]                     # no Grader view
    base = _row("points", "nba:espn:9", json.loads(json.dumps(quotes)))
    assert snapshots.guard_row("nba", base, fcs, None, "T") == []
    assert base["quotes"][0]["x"].get("ln") == "over" and "x" not in base["quotes"][2]
    row = _row("points", "nba:espn:9", json.loads(json.dumps(quotes)))
    log = snapshots.guard_row("nba", row, fcs, None, "T", avail="av_out")
    assert [q["x"] for q in row["quotes"]] == [av.x_of("av_out")] * 3
    assert [(e["record_id"], e["forecast_id"], e["reason"], e["rule"]) for e in log] == [
        ("r4", "f2", "av_out", "av-1"), ("r5", "f2", "av_out", "av-1"), ("r6", None, "av_out", "av-1")]
    assert log[0]["p_over"] == 0.62 and log[0]["projection"] == 26.0                    # chances unchanged
    assert fcs["f2"]["p_over"] == 0.62


def test_slip_pool_and_tracked_picks_leave_a_held_line_out(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "snap_root", lambda base=None: tmp_path)
    (tmp_path / "nba").mkdir()
    snapshots.write_sanity(tmp_path / "nba", [{"record_id": "r4", "forecast_id": "f2", "reason": "av_out"}],
                           {"league": "nba"}, [])
    held = pool._sanity_held("nba")
    assert held[("r4", None)] == "av_out"
    f = SimpleNamespace(model_version="sm-1", stat="points", projection_mean=26.0, line=24.5, chosen_side="over",
                        forecast_id="another-forecast", p_over=0.62, p_under=0.38)
    assert pool.guard_reason(f, "nba", "r4", held) == "not playing: Ruled out (ESPN injury report)"
    assert pool.guard_reason(f, "nba", "r9", held) is None
    picks._HELD.clear()
    assert picks.held_quote("nba", "r4", "another-forecast") == "av_out"
    assert picks.held_quote("nba", "r9", "f2") is None


# ------------------------------------------------------------------------------------------------ snapshot build
NOW = "2026-10-23T21:00:00Z"


def _world(con):
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    store.put(con, h.event())
    for pid, name in ((KAT, "Fixture Forward"), (MATE, "Fixture Guard")):
        store.put(con, Player(player_id=pid, league="nba", name=name, position="F", status="active",
                              observed_at=h.T0, known_at=h.T0))
        store.put(con, Membership(player_id=pid, team_id=h.NY, valid_from="2026-07-01T00:00:00Z", source="espn",
                                  observed_at=h.T0, known_at=h.T0))
    rid = store.put(con, h.offer())["record_id"]
    store.put(con, h.forecast(offer_snapshot_ids=(rid,), input_snapshot_ids=(rid,), line=20.5, model_version="sm-1",
                              p_over=0.6, p_under=0.4, p_push=0.0, p_over_given_no_push=0.6,
                              p_under_given_no_push=0.4, projection_mean=23.0))
    return rid


def _news(tmp_path, monkeypatch, items):
    db = tmp_path / "pgn.db"
    monkeypatch.setenv("SPORTS_NEWS_DB", str(db))
    con = core.connect(db)
    core.observe(con, "sports", "nba", "injury", "league:nba", {(pid, ""): p for pid, p in items.items()},
                 observed_at="2026-10-23T20:40:00Z", source="espn injuries", full=True)
    con.close()


def _build(con, tmp_path, now=NOW):
    snapshots.build(con, "nba", now=now, base=tmp_path, history=False, results=False)
    root = snapshots.snap_root(tmp_path) / "nba"
    board = json.loads((root / "board.json").read_text(encoding="utf-8"))
    pf = json.loads((root / "players" / f"{snapshots.safe_name(KAT)}.json").read_text(encoding="utf-8"))
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    san = json.loads((root / "sanity.json").read_text(encoding="utf-8"))
    return board, pf, man, san


def _quotes(pf):
    return [q for r in pf["markets"] for q in r["quotes"]]


def test_build_holds_an_out_player_and_changes_nothing_else(tmp_path, monkeypatch):
    con = store.connect()
    rid = _world(con)
    store.put(con, inj(MATE, "questionable", "2026-10-23T20:30:00Z", as_of="2026-10-23T17:00:00Z"))
    store.put(con, inj(KAT, "out", "2026-10-23T20:30:00Z"))
    monkeypatch.setenv("SPORTS_NEWS_DB", str(tmp_path / "none.db"))              # no archive: designation unknown
    _, pf0, man0, _ = _build(con, tmp_path / "a")
    assert all((q.get("x") or {}).get("v") != "av-1" for q in _quotes(pf0))       # missing data never holds
    assert pf0["injury"]["status"] == "out"
    _news(tmp_path, monkeypatch, {KAT: desig(date="2026-10-23T18:00Z"), MATE: desig("QUESTIONABLE",
                                                                                   date="2026-10-23T17:00Z")})
    _, pf, man, san = _build(con, tmp_path / "b")
    qs = _quotes(pf)
    assert qs and all(q["x"] == av.x_of("av_out") for q in qs)
    assert [k for k in san["keys"] if k[0] == rid][0][2] == "av_out"
    assert man["availability"]["held_rows"] == {"av_out": 1} and man["availability"]["listed"] == 2
    # projection, chances and forecasts are the same as without the hold
    assert pf["forecasts"] == pf0["forecasts"]
    assert [(q["record_id"], q.get("forecast_id")) for q in qs] == [(q["record_id"], q.get("forecast_id"))
                                                                     for q in _quotes(pf0)]
    con.close()


def test_build_questionable_is_not_held(tmp_path, monkeypatch):
    con = store.connect()
    _world(con)
    store.put(con, inj(KAT, "questionable", "2026-10-23T20:30:00Z"))
    _news(tmp_path, monkeypatch, {KAT: dict(desig("QUESTIONABLE"), status="Questionable")})
    _, pf, man, _ = _build(con, tmp_path / "b")
    assert all((q.get("x") or {}).get("v") != "av-1" for q in _quotes(pf))
    assert pf["injury"]["status"] == "questionable" and man["availability"]["held_rows"] == {}
    con.close()


def test_build_expires_a_player_who_left_the_report_and_keeps_state_through_an_outage(tmp_path, monkeypatch):
    con = store.connect()
    _world(con)
    _news(tmp_path, monkeypatch, {KAT: desig()})
    store.put(con, inj(KAT, "out", "2026-10-22T20:00:00Z"))                  # listed yesterday ...
    store.put(con, inj(MATE, "out", "2026-10-23T20:30:00Z"))                 # ... the newest read lists only MATE
    _, pf, _, _ = _build(con, tmp_path / "a")
    assert pf["injury"] is None                                               # stale label gone
    assert all((q.get("x") or {}).get("v") != "av-1" for q in _quotes(pf))
    # KAT seen again in the newest read (an unchanged duplicate: only last_seen_at moves) -> listed and held
    assert store.put(con, inj(KAT, "out", "2026-10-23T20:30:00Z"))["status"] == "duplicate"
    _, pf, _, _ = _build(con, tmp_path / "b")
    assert pf["injury"]["status"] == "out" and all(q["x"]["b"] == "av_out" for q in _quotes(pf))
    # injury feed down for 2 h (no new read): the last state is kept, never invented or dropped
    _, pf, man, _ = _build(con, tmp_path / "c", now="2026-10-23T22:30:00Z")
    assert man["availability"]["report_read_at"] == "2026-10-23T20:30:00Z"
    assert all(q["x"]["b"] == "av_out" for q in _quotes(pf))
    con.close()


def test_nfl_inactive_list_holds_the_line(tmp_path, monkeypatch):
    db = tmp_path / "pgn.db"
    monkeypatch.setenv("SPORTS_NEWS_DB", str(db))
    c = core.connect(db)
    core.observe(c, "sports", "nfl", "injury", "league:nfl", {("nfl:player:espn:7", ""): desig()},
                 observed_at="2026-10-23T20:40:00Z", full=True)
    core.observe(c, "sports", "nfl", "lineup", "event:nfl:401", {
        ("nfl:player:espn:7", "401"): {"active": False, "did_not_play": None},
        ("nfl:player:espn:8", "401"): {"active": True}}, observed_at="2026-10-23T21:30:00Z")
    c.close()
    inj_, lineup = av.designations("nfl", ["nfl:espn:401"])
    assert inj_["nfl:player:espn:7"]["fantasy_status"] == "OUT"
    assert av.hold(None, None, GAME, lineup.get(("nfl:player:espn:7", "401"))) == "av_inactive"
    assert av.hold(None, None, GAME, lineup.get(("nfl:player:espn:8", "401"))) is None
    assert av.designations("nba", ["nfl:espn:401"])[1] == {}                  # NBA reads no lineup flags
    monkeypatch.setenv("SPORTS_NEWS_DB", str(tmp_path / "missing" / "x.db"))
    assert av.designations("nfl", ["nfl:espn:401"]) == ({}, {})               # unreadable archive: no hold


# --------------------------------------------------------------------------- live teammate input + shadow inputs
def test_teammate_absence_counts_a_long_standing_out_and_drops_one_off_the_report(monkeypatch):
    con = store.connect()
    for t in h.NBA_TEAMS:
        store.put(con, Team(team_id=f"nba:team:espn:{t['id']}", league="nba", abbr=t["abbr"], name=t["name"],
                            observed_at=h.T0, known_at=h.T0))
    for pid in (KAT, MATE):
        store.put(con, Membership(player_id=pid, team_id=h.NY, valid_from="2026-07-01T00:00:00Z", source="espn",
                                  observed_at=h.T0, known_at=h.T0))
    # KAT: Out since 30 h ago, unchanged (duplicate sightings only move last_seen_at), still listed now
    store.put(con, inj(KAT, "out", "2026-10-22T14:00:00Z"))
    assert store.put(con, inj(KAT, "out", "2026-10-23T19:55:00Z"))["status"] == "duplicate"
    # MATE: listed Out 2 h ago, missing from the newest read (dropped off the report)
    store.put(con, inj(MATE, "out", "2026-10-23T18:00:00Z"))
    rows = [{"game_id": "g1", "team": "NY", "opp": "BOS"}]
    got = forward.injury_absences(con, "nba", rows, h.T0)
    assert got[("g1", "NY")] == [KAT] and got[("g1", "BOS")] == []
    # no read for more than 24 h -> unknown (None), never "nobody"
    assert forward.injury_absences(con, "nba", rows, "2026-10-25T20:00:00Z")[("g1", "NY")] is None
    # the shadow inputs use the same rule
    assert shadow._report(con, None, "nba", h.T0, rows)[0] == {KAT: "out"}
    con.close()
