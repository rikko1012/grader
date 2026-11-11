"""lav-1 / lnk-1 / link_mismatch (owner 2026-11-09: every projection accounts for whether a player is playing):
- a player his team no longer fields (not in its last lineup, which came after his last game for it) gets no new forecast
  and the board shows his line with no forecast (no side, letter or stake); missing data never holds; a read failure keeps
  the previous hold; it works with lpf-1 (a held line never keeps an old lpf-1 grade; the lpf-1 fallback is unchanged);
- the board shows no saved forecast once the line's link is definitely lost or points to another series / player;
- a forecast whose team is not in the settled series settles link_mismatch (never a DNP), for units settled from now on.
Every team, player and number is a TEST FIXTURE."""
import json
from types import SimpleNamespace

import pytest

from lol.center import build, fix_dnp_links, forecast, settle, store
from lol.tests.test_consolidation import _future_world


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    monkeypatch.setenv("LOL_LPF1", "0")
    c = store.connect()
    yield c
    c.close()


HELD = ("held", {"his_last_game": "2026-10-01", "team_last_game": "2026-10-20", "team_games_since": 4})


# ------------------------------------------------------------------ the rule (lineup_state)
def _h(team_games, lineup, mine, monkeypatch):
    idx = SimpleNamespace(tm={"T": [(d, 0, 0, 30, 1, f"g{d}", 1) for d in team_games]}, last_lineup=lambda t, day: dict(lineup))
    monkeypatch.setattr(forecast, "factor_index", lambda h: idx)
    return SimpleNamespace(by_player={"P": [{"team_id": "T", "_day": d} for d in mine]})


FIVE_WITHOUT = {"top": "a", "jungle": "b", "mid": "c", "bot": "d", "support": "e"}


def test_lineup_state_holds_only_a_player_his_team_fielded_without_after_his_last_game(monkeypatch):
    day = 1000
    assert forecast.lineup_state(_h([980, 990], FIVE_WITHOUT, [970, 980], monkeypatch), "P", "T", day)[0] == "held"
    assert forecast.lineup_state(_h([980, 990], dict(FIVE_WITHOUT, mid="P"), [970], monkeypatch), "P", "T", day)[0] == "ok"
    assert forecast.lineup_state(_h([980, 990], FIVE_WITHOUT, [990], monkeypatch), "P", "T", day)[0] == "ok"   # same day as team's last
    # missing data never holds: no games of his for the team, a partial lineup, an old identity / coverage gap
    assert forecast.lineup_state(_h([980, 990], FIVE_WITHOUT, [], monkeypatch), "P", "T", day)[0] == "ok"
    part = {k: v for k, v in FIVE_WITHOUT.items() if k != "mid"}
    assert forecast.lineup_state(_h([980, 990], part, [970], monkeypatch), "P", "T", day)[0] == "ok"
    assert forecast.lineup_state(_h([980, 990], FIVE_WITHOUT, [800], monkeypatch), "P", "T", day)[0] == "ok"
    assert forecast.lineup_state(_h([], {}, [970], monkeypatch), "P", "T", day)[0] == "ok"
    assert forecast.lineup_state(SimpleNamespace(), None, "T", day)[0] == "ok"


def test_lineup_state_read_failure_is_unknown(monkeypatch):
    def boom(h):
        raise RuntimeError("history read failed")
    monkeypatch.setattr(forecast, "factor_index", boom)
    assert forecast.lineup_state(SimpleNamespace(by_player={}), "P", "T", 1000) == ("unknown", None)


# ------------------------------------------------------------------ run + board
def test_held_player_gets_no_forecast_and_the_board_hides_the_saved_one(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    assert forecast.run(con, db)["saved"] == 1                               # he was in the lineup: graded
    b = forecast.board(con)
    assert b["rows"][0]["forecast"] and b["rows"][0]["grade"] and b["rows"][0]["hold"] is None
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: HELD)
    out = forecast.run(con, db)
    assert "saved" not in out and out["held"] == 1 and out["skip: held: not in the team's current lineup"] == 1
    row, = forecast.board(con)["rows"]
    assert row["forecast"] is None and row["grade"] is None and row["stake"] is None
    assert row["hold"]["reason"] == "Not in the team's current lineup" and row["hold"]["v"] == "lav-1"
    assert forecast.board(con)["counts"]["held"] == 1
    assert con.execute("SELECT COUNT(*) FROM lol_forecasts").fetchone()[0] == 1   # nothing new saved, nothing rewritten
    legs, left = forecast_slips(con)
    assert not legs and any("current lineup" in w for ws in left.values() for w in ws)
    # back in the lineup: the same saved forecast is shown again, unchanged
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: ("ok", None))
    assert forecast.run(con, db).get("unchanged") == 1
    row, = forecast.board(con)["rows"]
    assert row["forecast"] and row["hold"] is None


def forecast_slips(con):
    from lol.center import slips
    return slips.candidates(con, "underdog", preview=True)


def test_a_read_failure_keeps_a_hold_and_never_creates_one(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: ("unknown", None))
    assert forecast.run(con, db)["saved"] == 1                               # never held on missing data
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: HELD)
    forecast.run(con, db)
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: ("unknown", None))
    out = forecast.run(con, db)
    assert out["held"] == 1 and forecast.board(con)["rows"][0]["forecast"] is None     # the hold stands through the failure


def test_a_failed_cycle_keeps_the_last_holds(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: HELD)
    forecast.run(con, db)

    class Boom(Exception):
        pass

    def boom(*a, **k):
        raise Boom()
    monkeypatch.setattr(forecast, "History", boom)
    with pytest.raises(Boom):
        forecast.run(con, db)
    assert len(forecast.avail_holds(con)) == 1


# ------------------------------------------------------------------ lpf-1
class _Stub:
    def target(self, sid, pid, tid, opp, role, day, league, tournament, best_of, page=None):
        return {"match": sid, "pid": pid, "patch": "26.1", "best_of": best_of}

    def project(self, t, stat, scope, lam_game, line):
        return {"p_over": 0.71, "p_under": 0.29, "p_push": 0.0, "mean": line + 2.0, "median": int(line + 2), "range80": [1, 30],
                "p_win": 0.6, "p_decider": 0.4}


def _lp_ok(c, h, *a, **k):
    return {"proj": _Stub(), "stats": ("kills",), "version": "lpf-1", "sha": "abc", "cutoff": "2026-11-09"}


def _lp_down(c, h, *a, **k):
    return {"proj": None, "stats": ("kills",), "version": "lpf-1", "sha": None, "cutoff": None, "error": "RuntimeError: x"}


def test_hold_with_lpf1_and_the_lpf1_fallback_is_unchanged(con, tmp_path, monkeypatch):
    monkeypatch.delenv("LOL_LPF1", raising=False)
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lpf1_state", _lp_ok)
    assert forecast.run(con, db)["saved"] == 1
    assert forecast.board(con)["rows"][0]["forecast"]["model"].startswith("lpf-1")
    # held while lpf-1 is down: the saved lpf-1 grade is NOT kept on the board ("last saved lpf-1 forecast stands" never
    # applies to a held player - the hold comes first)
    monkeypatch.setattr(forecast, "lpf1_state", _lp_down)
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: HELD)
    out = forecast.run(con, db)
    assert out["held"] == 1 and not any(k.startswith("skip: lpf-1") for k in out)
    assert forecast.board(con)["rows"][0]["forecast"] is None
    # playing again, lpf-1 still down: the lpf-1 fallback rule as before - the last saved lpf-1 forecast stands and shows
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: ("ok", None))
    out = forecast.run(con, db)
    assert "saved" not in out and any(k.startswith("skip: lpf-1 answer unavailable") for k in out)
    fc = forecast.board(con)["rows"][0]["forecast"]
    assert fc["model"].startswith("lpf-1") and fc["p_over"] == 0.71
    assert [tuple(r) for r in con.execute("SELECT model_version, p_over FROM lol_forecasts")] == [("lpf-1", 0.71)]


def test_prepass_requests_no_lpf1_answer_for_a_held_player(con, tmp_path, monkeypatch):
    monkeypatch.delenv("LOL_LPF1", raising=False)
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lineup_state", lambda *a: HELD)
    from lol import lpf1_client as C
    calls = []
    monkeypatch.setattr(C, "artifact_sha", lambda: "abc")
    monkeypatch.setattr(C, "run_child", lambda req, *a, **k: calls.append(req) or ({}, {}))
    out = forecast.run(con, db)
    assert out["held"] == 1 and calls == []                                  # no child process started for a held line


# ------------------------------------------------------------------ lnk-1: the board's link check
def _link(con, **kw):
    sets = ", ".join(f"{k} = ?" for k in kw)
    con.execute(f"UPDATE market_links SET {sets}", list(kw.values()))
    con.commit()


def test_lost_link_hides_the_saved_forecast_missing_data_keeps_it(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    _link(con, status="unmatched", reason="team_not_known")                  # a failed build: missing data -> kept
    assert forecast.board(con)["rows"][0]["forecast"]
    _link(con, status="unmatched", reason="player_not_on_team")
    row, = forecast.board(con)["rows"]
    assert row["forecast"] is None and row["hold"]["v"] == "lnk-1"
    _link(con, status="quarantined", reason="series_ambiguous")
    assert forecast.board(con)["rows"][0]["forecast"] is None


def test_relinked_series_hides_the_old_forecast_until_resaved(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    sid = con.execute("SELECT sid FROM market_links").fetchone()[0]
    con.execute("UPDATE lol_forecasts SET sid = 'lol:series:OLD'")           # the saved record carries an outdated link
    con.commit()
    assert forecast.board(con)["rows"][0]["forecast"] is None
    assert forecast.run(con, db)["saved"] == 1                               # same numbers, new link: saved again
    f = con.execute("SELECT sid FROM lol_forecasts ORDER BY id DESC").fetchone()[0]
    assert f == sid and forecast.board(con)["rows"][0]["forecast"]


# ------------------------------------------------------------------ settlement: link_mismatch, never a DNP
def _complete(con, start):
    from lol.tests.test_center import game_rows, put
    day = start.strftime("%Y-%m-%d")
    for n in (1, 2):
        sg, sp = game_rows("M9", n, "Alpha Esports", "Beta Gaming", f"{day} 2{n}:00:00")
        put(con, "ScoreboardGames", [sg])
        put(con, "ScoreboardPlayers", sp)
    put(con, "MatchScheduleGame", [{"GameId": f"M9_{n}", "MatchId": "M9", "N_GameInMatch": str(n), "Match_DateTime_UTC": f"{day} 20:00:00"} for n in (1, 2)])
    put(con, "MatchSchedule", [{"MatchId": "M9", "OverviewPage": "Test League 2026", "Team1": "Alpha Esports", "Team2": "Beta Gaming",
                                "BestOf": "3", "Team1Score": "2", "Team2Score": "0", "Winner": "1",
                                "DateTime_UTC": start.strftime("%Y-%m-%d %H:%M:%S")}], at="2026-10-16T00:00:00Z")
    build.build(con)


def test_forecast_team_not_in_the_series_settles_link_mismatch(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    _complete(con, start)
    con.execute("UPDATE lol_forecasts SET tid = 'lol:team:OTHER'")
    con.commit()
    assert forecast.settle_all(con) == {"link_mismatch": 1}
    s = dict(con.execute("SELECT * FROM lol_settlements").fetchone())
    assert s["status"] == "link_mismatch" and "not in the linked series" in s["detail"]


def test_team_in_the_series_settles_as_before(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    _complete(con, start)
    assert forecast.settle_all(con) == {"graded": 1}


def test_units_already_settled_keep_their_rule_and_the_cleanup_is_separate(con, tmp_path, monkeypatch):
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    forecast.run(con, db)
    _complete(con, start)
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    unit = f"{f['platform']}|{f['market_key']}|{f['line']}"
    # an old false DNP: recorded before the fix, forecast team not in the series it was settled on
    con.execute("UPDATE lol_forecasts SET tid = 'lol:team:OTHER'")
    con.execute("INSERT INTO lol_settlements (unit_key, version, settled_at, platform, sid, pid, stat, scope, line, status, detail, "
                "fingerprint) VALUES (?, 1, '2026-10-25T00:00:00Z', ?, ?, ?, ?, ?, ?, 'dnp', '{}', 'x')",
                (unit, f["platform"], f["sid"], f["pid"], f["stat"], "maps_1_2", f["line"]))
    con.commit()
    # the clean-up: a dry run lists it and writes nothing; --apply appends the link_mismatch version
    n0 = con.execute("SELECT COUNT(*) FROM lol_settlements").fetchone()[0]
    dry = fix_dnp_links.run(con)
    assert dry["units"] == 1 and dry["rows"][0]["unit"] == unit and "new_status" not in dry["rows"][0]
    assert con.execute("SELECT COUNT(*) FROM lol_settlements").fetchone()[0] == n0
    done = fix_dnp_links.run(con, apply=True)
    assert done["rows"][0]["new_status"] == "link_mismatch"
    last = con.execute("SELECT status FROM lol_settlements WHERE unit_key = ? ORDER BY version DESC LIMIT 1", (unit,)).fetchone()[0]
    assert last == "link_mismatch" and fix_dnp_links.run(con)["units"] == 0
    store.set_meta(con, "settle_pass", None)
    forecast.settle_all(con)                                                 # a later full pass never undoes the clean-up
    last = con.execute("SELECT status FROM lol_settlements WHERE unit_key = ? ORDER BY version DESC LIMIT 1", (unit,)).fetchone()[0]
    assert last == "link_mismatch"
    # a unit whose latest settlement is another status (an old dnp awaiting the clean-up) is settled by the old rule
    con.execute("INSERT INTO lol_settlements (unit_key, version, settled_at, platform, sid, pid, stat, scope, line, status, detail, "
                "fingerprint) VALUES (?, 9, '2026-10-26T00:00:00Z', ?, ?, ?, ?, ?, ?, 'dnp', '{}', 'y')",
                (unit, f["platform"], f["sid"], f["pid"], f["stat"], "maps_1_2", f["line"]))
    store.set_meta(con, "settle_pass", None)
    con.commit()
    seen = []
    real = settle.record
    monkeypatch.setattr(settle, "record", lambda *a, **k: seen.append(k.get("tid")) or real(*a, **k))
    forecast.settle_all(con)                                                 # a full pass (new settle code)
    assert seen == [None]                                                    # the old unit is settled by the old rule
