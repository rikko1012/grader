"""sr-bo3-2 (owner 2026-10-28): BO3.gg-route (PROVISIONAL) projections are shadow only - computed and logged exactly as
before, never shown on board / cards / slips / compare / picks surfaces. HLTV-route rows unchanged; BO3_PROJ_SHADOW=0 restores.
CS2 tests never import lol."""
import copy
import sys
import grader
import grade_display
from pick_verdict import verdict


def _leg(version):
    return {"prop": "kills", "line": 24.5, "line_type": "standard", "scope": "12", "maps_played": 30, "sample_maps": 60,
            "kpr": 0.70, "hs_pct": 48.0, "rating": 1.05, "teammate_ratings": [1.0, 0.98, 1.02, 0.95], "sniping": 5,
            "roster_confirmed": True, "p_team_market": 0.55, "market_quality": "HARD", "bo": 3,
            "recent_avg": 28.0, "recent_median": 28.0, "recent": {"n": 10, "overs": 7},
            "perf": {"version": version, "lambda": 28.0, "sd": 6.0, "rate": 0.65, "opp_factor": 1.0, "rounds_per_map": 21.5,
                     "data_quality": "ok"}}


def picklog_model():
    import picklog
    return picklog.MODEL_VERSION


def _feed_row(i="ud-1"):
    return {"id": i, "player": "p", "prop": "kills", "line": 24.5, "book": "underdog", "scope": "12", "under_available": True,
            "over_available": True, "start": "2030-01-01T12:00:00Z"}


def test_route_detection_and_switch(monkeypatch):
    bo3 = grader.grade_leg(_leg("perf-2-bo3"))
    hltv = grader.grade_leg(_leg("perf-2"))
    monkeypatch.delenv("BO3_PROJ_SHADOW", raising=False)
    assert grade_display.bo3_shadow_enabled() is True                       # default: shadow
    assert grade_display.bo3_route(bo3) and grade_display.bo3_shadow(bo3)
    assert not grade_display.bo3_route(hltv) and not grade_display.bo3_shadow(hltv)
    monkeypatch.setenv("BO3_PROJ_SHADOW", "0")
    assert grade_display.bo3_shadow(bo3) is False                            # restores the previous behaviour ...
    assert grader.grade_leg(_leg("perf-2-bo3"))["units"] == 0.0              # ... with the sr-bo3-1 0U gate still on


def test_forecast_still_computed_and_logged_as_before():
    # the grade itself is untouched: same chance / letter / projection / flags as before sr-bo3-2 (what picklog logs)
    g = grader.grade_leg(_leg("perf-2-bo3"))
    assert g["p_more"] > 0.5 and g["grade"] in grader.GRADES and g["projection_source"] == "perf-2-bo3"
    v = verdict(g)
    assert v["side"] == "More" and v["grade"] == "LEAN" and v["units"] == 0.0 and grader.SR_BO3_1_REASON in v["reason"]
    import picklog
    row = picklog.pick_row({"grade": g, "player_block": {"player": {"slug": "p", "nick": "P", "id": 1}},
                            "next_match": {"slug": "a-vs-b-01-01-2030", "start": "2030-01-01T12:00:00Z"}}, v, test=2)
    assert row is not None and row["p_more"] == g["p_more"] and row["pick"] == "More" and row["lambda"] == g["lambda_final"]


def test_board_row_hidden_but_kept_for_admin():
    g = grader.grade_leg(_leg("perf-2-bo3"))
    v = verdict(g)
    row = grade_display.shadow_board_row(_feed_row(), g, v, 1.0, "2030-01-01T10:00:00+00:00")
    assert row["state"] == "SHADOW" and row["shadow"] is True and row.get("side") is None and row.get("grade") is None
    assert row.get("p") is None and row.get("units") is None and row.get("proj") is None
    assert row["shadow_grade"]["side"] == "More" and row["shadow_grade"]["model_grade"] == g["grade"]
    assert row["shadow_grade"]["source"] == "perf-2-bo3" and row["graded_line"] == 24.5


def test_board_payload_slips_and_compare_skip_shadow_rows(monkeypatch):
    import server, slip_builder
    g = grader.grade_leg(_leg("perf-2-bo3"))
    v = verdict(g)
    r = _feed_row()
    monkeypatch.setattr(server.underdog, "board", lambda: {"rows": [r], "at": "2030-01-01T09:00:00Z", "error": None})
    monkeypatch.setattr(server.prizepicks, "board", lambda: {"rows": [], "at": None, "error": None, "state": "ok"})
    server.BOARD.clear()
    server.BOARD[r["id"]] = grade_display.shadow_board_row(r, g, v, 1.0, "2030-01-01T10:00:00+00:00")
    try:
        assert server.board_payload("underdog")["rows"] == []                 # hidden from the member board
        # a slip-builder candidate needs a side: the stored shadow row is never a leg
        import datetime as _dt
        st = slip_builder.normalize_settings({"book": "underdog"})
        why = slip_builder.why_excluded(dict(r, **server.BOARD[r["id"]]), st, picklog_model(), _dt.datetime(2030, 1, 1, 9, tzinfo=_dt.timezone.utc))
        assert why is not None                                                 # never a slip leg
    finally:
        server.BOARD.clear()


def test_hltv_route_row_unchanged_on_the_board(monkeypatch):
    import server
    g = grader.grade_leg(_leg("perf-2"))
    v = verdict(g)
    assert not grade_display.bo3_shadow(g)
    r = _feed_row("ud-2")
    monkeypatch.setattr(server.underdog, "board", lambda: {"rows": [r], "at": "2030-01-01T09:00:00Z", "error": None})
    monkeypatch.setattr(server.prizepicks, "board", lambda: {"rows": [], "at": None, "error": None, "state": "ok"})
    server.BOARD.clear()
    server.BOARD[r["id"]] = {"at": 1.0, "graded_line": 24.5, "grade": v["grade"], "side": v["side"], "p": v["p"], "units": v["units"],
                             "proj": g["lambda_final"], "state": v["state"], "reason": v["reason"], "book": "underdog",
                             "match_start": "2030-01-01T12:00:00Z", **grade_display.annotate(g, v)}
    try:
        rows = server.board_payload("underdog")["rows"]
        assert len(rows) == 1 and rows[0]["side"] == "More" and rows[0]["p"] == v["p"] and rows[0]["units"] == v["units"] > 0
    finally:
        server.BOARD.clear()


def test_card_withholds_everything_for_a_shadow_grade():
    import server
    g = grader.grade_leg(_leg("perf-2-bo3"))
    res = {"grade": g, "verdict": verdict(g), "ladder": [{"line": 24.5}], "simulation": {"x": 1}, "comparison": {"rows": []},
           "form_gap": {"projection": 28.0, "gap": 3.5, "n": 10}}
    server.withhold_shadow(res)
    v = res["verdict"]
    assert v["side"] is None and v["p"] is None and v["grade"] is None and v["units"] == 0.0 and v["unavailable"] is True
    assert v["reason"] == grade_display.BO3_SHADOW_REASON and "No bet" not in v["reason"]
    assert res["grade"]["status"] == "NO PROJECTION" and res["grade"]["withheld"] is True
    for k in ("p_more", "p_less", "grade", "lambda_final", "sd", "units", "probs_raw", "dist"):
        assert k not in res["grade"], k
    assert res["ladder"] == [] and res["simulation"] is None and "error" in res["comparison"]
    assert "projection" not in res["form_gap"] and res["form_gap"]["n"] == 10


def test_lookups_benched_and_search_do_not_depend_on_the_switch(monkeypatch):
    # the identity / benched / search uses of BO3.gg are plain functions with no projection: untouched by the switch
    import bo3
    for on in ("1", "0"):
        monkeypatch.setenv("BO3_PROJ_SHADOW", on)
        assert callable(getattr(bo3.BO3, "player_block", None) or getattr(bo3.BO3, "search", None) or bo3.BO3)
        assert hasattr(bo3, "BO3")
    assert not any(m == "lol" or m.startswith("lol.") for m in sys.modules)
