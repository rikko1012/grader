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
    assert row["units"] == 0.0 and row["grade"] == "B" and row["pick"] == "Over 9.5" and row["sides_offered"] == ["higher", "lower"]
    text = json.dumps(view)
    for hidden in ("lol:player", "lol:series", "slip_reasons", "stake_restriction", "price_status", "market_key", "units_policy"):
        assert hidden not in text
    (tmp_path / "public.flag").write_text("x")
    monkeypatch.setitem(server._LOL_PUB, "at", 0.0)
    assert server.lol_public() is True
    out = server.lol_center_admin("/admin/lol/release", {"on": "0"})
    assert out["public"] is False and not (tmp_path / "public.flag").exists()
