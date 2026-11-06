"""The member LoL page's server side (server.py never imports lol): the release switch and the member view of the board
file the LoL worker writes - forecasted single lines only, no internal ids or admin fields, 0U stakes."""
import json

import server


def _board(tmp_path):
    rows = [{"platform": "underdog", "market_key": "ud:x", "player": "Test Mid", "team": "Test Team", "role": "Mid",
             "opponent": "Other Team", "competition": "Test League", "best_of": 5, "series_start": "2026-10-20T18:00:00Z",
             "stat": "kills", "scope": "Maps 1-3", "stat_label": "Kills on Maps 1+2+3", "market_type": "single", "odds_type": "balanced",
             "line": 9.5, "sides": {"higher": {"status": "active", "payout_multiplier": "1.0"}, "lower": {"status": "active"}},
             "quote_seen_at": "2026-10-19T10:00:00Z", "link": {"status": "matched", "sid": "lol:series:000001", "pid": "lol:player:000001"},
             "forecast": {"id": 7, "at": "2026-10-19T10:01:00Z", "line": 9.5, "projection": 10.4, "p_over": 0.58, "p_under": 0.42,
                          "p_push": 0.0, "model_side": "over", "side_offered": 1, "model": "lol-1", "current": True, "grade": "B",
                          "units": 0.0, "units_policy": 0.5, "stake_restriction": "admin text", "slip_eligible": False,
                          "slip_reasons": ["x"], "flags": [],
                          "inputs": {"median": 10, "range80": [6, 15], "drivers": ["player share 0.25"], "unknowns": ["draft"],
                                     "market": {"price_status": "x"}}},
             "grade": "B", "stake": 0.0},
            {"platform": "prizepicks", "market_key": "pp:c", "player": "A + B", "market_type": "combo", "forecast": None,
             "link": {"status": "unmatched"}, "line": 20}]
    (tmp_path / "board.json").write_text(json.dumps({"at": "2026-10-19T10:02:00Z", "rows": rows, "match_tables": []}))


def test_member_view_is_filtered_and_switch_defaults_off(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    monkeypatch.delenv("LOL_PUBLIC", raising=False)
    monkeypatch.setitem(server._LOL_PUB, "at", 0.0)
    assert server.lol_public() is False                            # default off
    _board(tmp_path)
    server._LOL_MEMBER.update(mtime=None, body=None)
    view = json.loads(server.lol_member_board())
    row, = view["rows"]                                            # the combo / unforecast line is not shown
    assert row["units"] == 0.0 and row["grade"] == "B" and row["pick"] == "Over 9.5" and row["pick_offered"] is True
    assert [(x["side"], x["offered"], x["payout"]) for x in row["sides"]] == [("Over", True, "1.0"), ("Under", True, None)]
    assert row["scope"] == "Maps 1-3" and row["stat_name"] == "Kills" and row["line_type"] == "Standard"
    assert row["recent_per_game"] is None                         # missing stays missing (shown as unavailable), never 0
    text = json.dumps(view)
    for hidden in ("lol:player", "lol:series", "slip_reasons", "stake_restriction", "price_status", "market_key", "units_policy"):
        assert hidden not in text
    (tmp_path / "public.flag").write_text("x")
    monkeypatch.setitem(server._LOL_PUB, "at", 0.0)
    assert server.lol_public() is True
    out = server.lol_center_admin("/admin/lol/release", {"on": "0"})
    assert out["public"] is False and not (tmp_path / "public.flag").exists()


def test_member_view_unoffered_side_and_alternate_line(tmp_path, monkeypatch):
    """A PrizePicks Demon line offers More only: os-1 (owner 2026-11-06) - it reads "Over only" with its Demon tag, no chance,
    pick or letter (never playable), whichever side the model leans."""
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    _board(tmp_path)
    b = json.loads((tmp_path / "board.json").read_text())
    r = b["rows"][0]
    r.update(platform="prizepicks", odds_type="demon", sides={"higher": {"offered": True}, "lower": {"offered": False}})
    r["forecast"].update(model_side="under", p_over=0.4, p_under=0.6, side_offered=0)
    (tmp_path / "board.json").write_text(json.dumps(b))
    server._LOL_MEMBER.update(mtime=None, body=None)
    row, = json.loads(server.lol_member_board())["rows"]
    assert row["one_sided"] == "Over only" and row["pick_offered"] is False and row["line_type"] == "Demon"
    assert row["grade"] is None and row["p_over"] is None and row["p_under"] is None and row["projection"] is None
    assert row["stake_reason"] == "Over only: Scout grades lines that offer both sides." and "No bet" not in json.dumps(row)
    assert [(x["side"], x["offered"]) for x in row["sides"]] == [("Over", True), ("Under", False)]


def test_admin_query_is_read_only(tmp_path, monkeypatch):
    import sqlite3
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    c = sqlite3.connect(tmp_path / "lol.db")
    c.execute("CREATE TABLE t (x INTEGER)")
    c.execute("INSERT INTO t VALUES (1)")
    c.commit()
    c.close()
    assert server.lol_admin_query("center", "SELECT x FROM t")["rows"] == [{"x": 1}]
    assert "error" in server.lol_admin_query("center", "DELETE FROM t")
    assert "error" in server.lol_admin_query("center", f"WITH a AS (SELECT 1) SELECT * FROM a; ATTACH '{tmp_path / 'x.db'}' AS x")
    assert "error" in server.lol_admin_query("nope", "SELECT 1")
    assert sqlite3.connect(tmp_path / "lol.db").execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_member_slips_explain_why_none_and_map_legs_to_line_keys():
    sm = {"platforms": {
        "underdog": {"recommended": [], "upcoming_forecasts": 44, "upcoming_series": 1,
                     "blocked_by": {"stake 0U (LoL projections not yet validated at real lines)": 44,
                                    "the model's side is not offered on this line": 23}},
        "prizepicks": {"recommended": [{"size": 2, "legs": [{"market_key": "pp:1", "side": "over", "line": 9.5},
                                                            {"market_key": "pp:2", "side": "under", "line": 4.5}],
                                        "entry_payout": None, "model_chance_all_hit": 0.4}],
                       "upcoming_forecasts": 10, "upcoming_series": 2, "blocked_by": {}}}}
    out = server._lol_member_slips(sm)
    ud, pp = out["Underdog"], out["PrizePicks"]
    assert ud["recommended"] == [] and any("0U" in r["text"] for r in ud["why_none"])
    assert any("same match" in r["text"] for r in ud["why_none"]) and any("not offered" in r["text"] for r in ud["why_none"])
    assert [l["key"] for l in pp["recommended"][0]["legs"]] == [server._lol_key("prizepicks", "pp:1"), server._lol_key("prizepicks", "pp:2")]
    assert "pp:1" not in json.dumps(out)                                 # internal keys never shown


def test_member_rows_carry_an_opaque_line_key(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    _board(tmp_path)
    server._LOL_MEMBER.update(mtime=None, body=None)
    row, = json.loads(server.lol_member_board())["rows"]
    assert row["key"] == server._lol_key("underdog", "ud:x") and len(row["key"]) == 16
