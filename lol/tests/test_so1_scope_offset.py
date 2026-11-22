"""so-1 (LoL round 2, 2026-11-21): the event-scoped stat x scope offset on the lol-1f projection (flag LOL_SCOPE_OFFSET,
default OFF = shadow; LOL_SCOPE_OFFSET=1 turns it on). Event = the exact tournament page. It touches only lol-1f forecasts, never lpf-1 ones; the pool uses the projection BEFORE any
offset (no feedback); a cell under 30 settled player-markets gives no offset; a read failure keeps the last good offsets
and is never cached. Every team, player and number is a TEST FIXTURE."""
import json
import math

import pytest

from lol.center import forecast, store
from lol.tests.test_consolidation import _future_world
from lol.tests.test_lpf1_live import _Stub

EV = "Test League 2026"            # _future_world's series page


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    monkeypatch.setattr(forecast, "_SO1_LAST", {"offsets": None})
    c = store.connect()
    yield c
    c.close()


def _strip(f):
    f = dict(f)
    for k in ("id", "forecast_at", "quote_observed_at", "series_start", "state_sha"):   # state_sha hashes series_start (now + 2 d)
        f.pop(k, None)
    inp = json.loads(f.pop("inputs"))
    inp.pop("history_since", None)
    (inp.get("data_version") or {}).pop("export_at", None)   # wall-clock export time, differs across runs
    return f, inp


def test_event_is_the_exact_tournament_page():
    """Round-2 review fix: seasons / splits / stages of one league are different events (exact page identity)."""
    assert forecast.so1_event("Legends Premier League/Season 4") == "Legends Premier League/Season 4"
    assert forecast.so1_event("Legends Premier League/Season 4") != forecast.so1_event("Legends Premier League/Season 5")
    assert forecast.so1_event("LPL/2026 Season/Split 1") != forecast.so1_event("LPL/2026 Season/Split 2")
    assert forecast.so1_event("2026 Season World Championship/Main Event") != forecast.so1_event("2026 Season World Championship/Play-In")
    assert forecast.so1_event(" Test League 2026 ") == "Test League 2026"
    assert forecast.so1_event("") is None and forecast.so1_event(None) is None
    assert forecast.so1_event("LE:lpl") is None                        # lolesports league page: not an event, no offset


def test_flag_default_off_and_1_turns_it_on(con, tmp_path, monkeypatch):
    monkeypatch.delenv("LOL_SCOPE_OFFSET", raising=False)
    assert not forecast.so1_on() and forecast.so1_state(con, object()) is None
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "0")
    assert not forecast.so1_on()
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "1")
    assert forecast.so1_on()


def test_default_unset_saves_a_plain_lol1f_record(con, tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_LPF1", "0")
    monkeypatch.delenv("LOL_SCOPE_OFFSET", raising=False)
    monkeypatch.setattr(forecast, "so1_offsets", lambda c: {(EV, "kills", "12"): (-0.1, 40)})
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    assert forecast.run(con, db)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    assert f["model_version"] == "lol-1f" and "scope_offset" not in json.loads(f["inputs"])


def _run(con, tmp_path, monkeypatch, offsets, flag="1"):
    monkeypatch.setenv("LOL_LPF1", "0")
    monkeypatch.setenv("LOL_SCOPE_OFFSET", flag)
    monkeypatch.setattr(forecast, "so1_offsets", lambda c: offsets)
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    assert forecast.run(con, db)["saved"] == 1
    _run.db = db
    return dict(con.execute("SELECT * FROM lol_forecasts ORDER BY id DESC LIMIT 1").fetchone())


def test_offset_scales_the_lol1f_projection_and_is_recorded(con, tmp_path, monkeypatch):
    f = _run(con, tmp_path, monkeypatch, {(EV, "kills", "12"): (-0.1, 40)})
    inp = json.loads(f["inputs"])
    so = inp["scope_offset"]
    assert f["model_version"] == "lol-1f+so-1" and so["version"] == "so-1" and so["event"] == EV and so["player_markets"] == 40
    assert math.isclose(so["multiplier"], math.exp(-0.1), abs_tol=1e-4)
    assert math.isclose(f["lambda"], so["lambda_before"] * math.exp(-0.1), rel_tol=1e-3)
    assert 0 < f["p_over"] < 1 and abs(f["p_over"] + f["p_under"] + f["p_push"] - 1) < 1e-3 and f["grade_label"]


def test_rollback_and_other_events_and_thin_cells_change_nothing(tmp_path, monkeypatch):
    outs = []
    for i, (offs, flag) in enumerate([({}, "1"),                                           # no cells at all
                                      ({(EV, "kills", "12"): (-0.1, 40)}, "0"),             # rollback
                                      ({(EV, "kills", "12"): (-0.1, 40)}, ""),              # empty: default off
                                      ({("Other Event", "kills", "12"): (-0.1, 40)}, "1"),   # another event's offsets
                                      ({(EV + "/Season 4", "kills", "12"): (-0.1, 40)}, "1"),  # another season's page
                                      ({(EV, "kills", "12"): (0.0, 12)}, "1")]):            # under 30 pm -> c = 0
        monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / f"c{i}.db"))
        monkeypatch.setattr(forecast, "_SO1_LAST", {"offsets": None})
        c = store.connect()
        try:
            f = _run(c, tmp_path / f"w{i}", monkeypatch, offs, flag)
        finally:
            c.close()
        outs.append(_strip(f))
    base = outs[0]
    assert base[0]["model_version"] != "lol-1f+so-1" and "scope_offset" not in base[1]
    for o in outs[1:]:
        assert o[0] == base[0] and o[1] == base[1]                       # byte-identical record


def test_lpf1_answers_are_never_touched(con, tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "1")
    monkeypatch.setattr(forecast, "so1_offsets", lambda c: {(EV, "kills", "12"): (-0.3, 99)})
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    monkeypatch.setattr(forecast, "lpf1_state", lambda c, h, *a, **k: {"proj": _Stub(), "stats": ("kills",), "version": "lpf-1",
                                                                       "sha": "abc", "cutoff": "2026-11-09"})
    assert forecast.run(con, db)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    inp = json.loads(f["inputs"])
    assert f["model_version"] == "lpf-1" and f["p_over"] == 0.71 and "scope_offset" not in inp
    lam_game = inp["lpf1"]["lambda_game_lol1f"]
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "0")
    con.execute("DELETE FROM lol_forecasts")
    con.commit()
    assert forecast.run(con, db)["saved"] == 1
    inp0 = json.loads(con.execute("SELECT inputs FROM lol_forecasts").fetchone()[0])
    assert inp0["lpf1"]["lambda_game_lol1f"] == lam_game and inp0["lpf1"]["lambda_lol1f"] == inp["lpf1"]["lambda_lol1f"]


def _seed(con, n, stat="kills", scope="12", page="Test League 2026", actual=5.0, lam=6.0, mv="lol-1f", inputs=None):
    for i in range(n):
        sid = f"s{i}"
        con.execute("INSERT OR IGNORE INTO c_series (sid, tournament_page) VALUES (?, ?)", (sid, page))
        mk = f"mk{stat}{scope}{i}"
        con.execute("INSERT INTO lol_forecasts (forecast_at, platform, market_key, line, sid, pid, stat, scope, series_start, model_version, "
                    "lambda, inputs) VALUES ('2026-11-01T00:00:00Z', 'underdog', ?, 4.5, ?, 'p1', ?, ?, '2026-11-01T01:00:00Z', ?, ?, ?)",
                    (mk, sid, stat, scope, mv, lam, json.dumps(inputs or {})))
        con.execute("INSERT INTO lol_settlements (unit_key, version, settled_at, platform, sid, pid, stat, scope, line, status, actual) "
                    "VALUES (?, 1, '2026-11-01T05:00:00Z', 'underdog', ?, 'p1', ?, ?, 4.5, 'graded', ?)",
                    (f"underdog|{mk}|4.5", sid, stat, scope, actual))
    con.commit()


def test_offsets_need_30_player_markets_and_use_the_projection_before_any_offset(con):
    _seed(con, 29)
    assert forecast.so1_offsets(con)[("Test League 2026", "kills", "12")] == (0.0, 29)
    # 20 lol-1f rows + 10 rows whose saved lambda is 50 but whose lol-1f projection was 6 (so-1: lambda_before; lpf-1:
    # inputs.lpf1.lambda_lol1f): the pool reads the projection before any offset, so there is no feedback
    con.execute("DELETE FROM lol_forecasts")
    con.execute("DELETE FROM lol_settlements")
    con.execute("DELETE FROM c_series")
    _seed(con, 20)
    for i in range(20, 30):
        sid = f"s{i}"
        con.execute("INSERT INTO c_series (sid, tournament_page) VALUES (?, 'Test League 2026')", (sid,))
        con.execute("INSERT INTO lol_forecasts (forecast_at, platform, market_key, line, sid, pid, stat, scope, series_start, model_version, "
                    "lambda, inputs) VALUES ('2026-11-01T00:00:00Z', 'underdog', ?, 4.5, ?, 'p1', 'kills', '12', '2026-11-01T01:00:00Z', ?, ?, ?)",
                    (f"m{i}", sid, "lpf-1" if i % 2 else "lol-1f+so-1", 50.0,
                     json.dumps({"lpf1": {"lambda_lol1f": 6.0}} if i % 2 else {"scope_offset": {"lambda_before": 6.0}})))
        con.execute("INSERT INTO lol_settlements (unit_key, version, settled_at, platform, sid, pid, stat, scope, line, status, actual) "
                    "VALUES (?, 1, '2026-11-01T05:00:00Z', 'underdog', ?, 'p1', 'kills', '12', 4.5, 'graded', 5.0)", (f"underdog|m{i}|4.5", sid))
    con.commit()
    c, n = forecast.so1_offsets(con)[("Test League 2026", "kills", "12")]
    assert n == 30 and math.isclose(c, math.log(5.0 / 6.0), abs_tol=1e-9)


def test_a_read_failure_keeps_the_last_good_offsets_and_is_never_cached(con, monkeypatch):
    class H:
        pass
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "1")

    def boom(c):
        raise RuntimeError("locked")
    monkeypatch.setattr(forecast, "so1_offsets", boom)
    assert forecast.so1_state(con, H()) is None and forecast._SO1_LAST["offsets"] is None   # nothing good yet, nothing cached
    good = {(EV, "kills", "12"): (-0.05, 31)}
    monkeypatch.setattr(forecast, "so1_offsets", lambda c: good)
    assert forecast.so1_state(con, H()) == good
    monkeypatch.setattr(forecast, "so1_offsets", boom)
    assert forecast.so1_state(con, H()) == good                       # the failure kept the last good offsets


def test_a_saved_so1_forecast_stands_when_offsets_are_unavailable(con, tmp_path, monkeypatch):
    f = _run(con, tmp_path, monkeypatch, {(EV, "kills", "12"): (-0.1, 40)})
    assert f["model_version"] == "lol-1f+so-1"
    monkeypatch.setattr(forecast, "_SO1_LAST", {"offsets": None})

    def boom(c):
        raise RuntimeError("locked")
    monkeypatch.setattr(forecast, "so1_offsets", boom)
    out = forecast.run(con, _run.db)
    assert "saved" not in out and any(k.startswith("skip: so-1 offsets unavailable") for k in out)
    assert con.execute("SELECT COUNT(*) FROM lol_forecasts").fetchone()[0] == 1


def test_maps_1_3_on_a_best_of_3_scales_every_mixture_part(con, tmp_path, monkeypatch):
    """The A2 mixture path (Maps 1-3 line on a BO3): both parts' means scale by the offset; the chance is the mixture's."""
    from lol import model
    from lol.tests.test_audit_g_lol import _world
    from lol.tests.test_consolidation import RR
    monkeypatch.setenv("LOL_LPF1", "0")
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "1")
    monkeypatch.setattr(forecast, "so1_offsets", lambda c: {(EV, "kills", "123"): (-0.2, 50)})
    db, start, raw = _world(con, tmp_path, monkeypatch, stat="kills_on_maps_1_2_3")
    assert forecast.run(con, db)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts").fetchone())
    inp = json.loads(f["inputs"])
    assert "format_scope_mismatch" in json.loads(f["flags"]) and f["model_version"] == "lol-1f+so-1"
    k, p3, lg = math.exp(-0.2), inp["p_3_games"], inp["lambda_game"]          # lambda_game is saved after the offset
    assert inp["scope_offset"]["lambda_before"] == pytest.approx(lg / k * (2 + p3), abs=2e-3)
    assert f["lambda"] == pytest.approx(lg * (2 + p3), abs=2e-3) and inp["lambda_3_games"] == pytest.approx(3 * lg, abs=2e-3)
    o3, u3, _ = model.probs(3 * lg, RR[("kills", "123")], f["line"])
    o2, u2, _ = model.probs(2 * lg, RR[("kills", "12")], f["line"])
    assert f["p_over"] == pytest.approx(p3 * o3 + (1 - p3) * o2, abs=2e-3)
    assert f["p_under"] == pytest.approx(p3 * u3 + (1 - p3) * u2, abs=2e-3)


def test_rollback_on_the_same_database_saves_a_plain_lol1f_record_next_cycle(con, tmp_path, monkeypatch):
    f = _run(con, tmp_path, monkeypatch, {(EV, "kills", "12"): (-0.1, 40)})
    assert f["model_version"] == "lol-1f+so-1"
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "0")
    out = forecast.run(con, _run.db)
    assert out.get("saved") == 1
    g = dict(con.execute("SELECT * FROM lol_forecasts ORDER BY id DESC LIMIT 1").fetchone())
    assert g["model_version"] == "lol-1f" and "scope_offset" not in json.loads(g["inputs"])
    assert g["lambda"] == pytest.approx(json.loads(f["inputs"])["scope_offset"]["lambda_before"], abs=1e-3)


def test_a_previous_season_page_never_gives_a_new_season_an_offset(con):
    """Round-2 review bug: 'Legends Premier League/Season 4' and '/Season 5' were one event (last '/' segment dropped), so the
    old season's settled lines offset the new season from day one. Exact page identity keeps them apart."""
    _seed(con, 30, page="Legends Premier League/Season 4")
    offs = forecast.so1_offsets(con)
    c, n = offs[("Legends Premier League/Season 4", "kills", "12")]
    assert n == 30 and math.isclose(c, math.log(5.0 / 6.0), abs_tol=1e-9)
    assert ("Legends Premier League", "kills", "12") not in offs
    assert offs.get((forecast.so1_event("Legends Premier League/Season 5"), "kills", "12"), (0.0, 0)) == (0.0, 0)


def test_lolesports_league_pages_never_pool(con):
    _seed(con, 30, page="LE:lpl")
    assert forecast.so1_offsets(con) == {}


@pytest.mark.parametrize("page, offset", [("Test League 2026/Season 4", False),   # the old rule merged this into 'Test League 2026'
                                          ("Test League 2026", True)])            # exact page: the event's own lines
def test_forecast_path_with_real_offsets_uses_only_the_exact_page(con, tmp_path, monkeypatch, page, offset):
    monkeypatch.setenv("LOL_LPF1", "0")
    monkeypatch.setenv("LOL_SCOPE_OFFSET", "1")
    db, start, raw = _future_world(con, tmp_path, monkeypatch)
    _seed(con, 30, scope="12", page=page)
    assert forecast.run(con, db)["saved"] == 1
    f = dict(con.execute("SELECT * FROM lol_forecasts WHERE market_key NOT LIKE 'mk%' ORDER BY id DESC LIMIT 1").fetchone())
    inp = json.loads(f["inputs"])
    if offset:
        assert f["model_version"] == "lol-1f+so-1" and inp["scope_offset"]["event"] == EV
        assert math.isclose(inp["scope_offset"]["c"], math.log(5.0 / 6.0), abs_tol=1e-4)
    else:
        assert f["model_version"] == "lol-1f" and "scope_offset" not in inp
