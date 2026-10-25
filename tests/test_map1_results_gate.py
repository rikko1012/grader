"""Owner 2026-10-25: "get rid of map 1's on the result page until we have 500 stakes to go off of". Map 1 (scope "1") is
left out of the member Results view - rows AND every total - until Map 1 has MAP1_RESULTS_MIN_STAKED settled staked P1
units; admin diagnostics always show Map 1 and the count. Every row below is a LABELLED TEST FIXTURE."""
import json

import results_view
import server


def _u(i, p, over, scope="12", units=0.5, prop="kills"):
    return {"match": f"m{i}", "p": p, "over": over, "prop": prop, "scope": scope, "version": "v1", "units": units,
            "forecast_id": i, "start": "2026-10-20T12:00:00+00:00", "logged": "2026-10-20T10:00:00+00:00"}


def _export(n_map1_staked, map1_unstaked=0):
    rows = [_u(i, 0.62, 1) for i in range(6)]                                         # Maps 1-2: 6 Over picks, all won
    rows += [_u(100 + i, 0.70, 0, scope="1") for i in range(n_map1_staked)]           # Map 1 staked: every one lost
    rows += [_u(5000 + i, 0.70, 0, scope="1", units=0) for i in range(map1_unstaked)]  # Map 1, 0U: not a stake
    return {"rows": rows, "excluded": {"push on the board line": 2}, "at": "2026-10-25T06:00:00+00:00",
            "pushes": [_u(9000, 0.65, 0), _u(9001, 0.66, 0, scope="1")]}


def test_the_threshold_is_500():
    assert results_view.MAP1_RESULTS_MIN_STAKED == 500


def test_count_is_staked_settled_map1_units_once_each():
    ex = _export(10, map1_unstaked=7)
    ex["rows"].append(dict(ex["rows"][-8]))                  # a duplicated forecast id is one unit
    assert results_view.map1_staked_settled(ex) == 10 + 1    # 10 scored + the staked Map 1 push; 0U rows never count


def test_map1_hidden_below_500_with_totals_recomputed():
    out = results_view.member_results(_export(499 - 1), B=50)     # 498 scored + 1 push = 499 staked
    P = out["picks"]
    # only Maps 1-2: 6 won + the pushed 0.65 pick counted as won; no Map 1 loss anywhere in the totals
    assert (P["picks"], P["wins"], P["losses"], P["pushes"]) == (7, 7, 0, 1)
    assert P["printed"] == round((6 * 0.62 + 0.65) / 7, 4)
    assert out["lines"]["settled"] == 6 and out["lines"]["matches"] == 6 and out["lines"]["pushes"] == 1
    assert all(r["scope"] != "Map 1" for r in out["by_stat"])
    assert sum(r["picks"] for r in out["by_side"]) == 7 and sum(r["picks"] for r in out["by_chance"]) == 7
    assert sum(r["picks"] for r in out["by_version"]) == 7
    assert "Map 1 lines are not included." in out["definition"]
    assert "units" not in json.dumps(out).lower()             # the member view never names stakes


def test_map1_shown_at_500():
    out = results_view.member_results(_export(499), B=50)         # 499 scored + 1 staked push = 500
    P = out["picks"]
    assert (P["picks"], P["wins"], P["losses"], P["pushes"]) == (7 + 500, 7 + 1, 499, 2)
    assert ("Kills", "Map 1", 500) in {(r["stat"], r["scope"], r["picks"]) for r in out["by_stat"]}
    assert out["lines"]["settled"] == 6 + 499 and out["lines"]["pushes"] == 2
    assert "Map 1" not in out["definition"]


def test_unstaked_map1_rows_do_not_open_the_gate():
    out = results_view.member_results(_export(0, map1_unstaked=800), B=20)
    assert all(r["scope"] != "Map 1" for r in out["by_stat"]) and out["picks"]["picks"] == 7


def test_payload_hides_and_admin_diagnostics_keep_the_count(monkeypatch):
    monkeypatch.setattr(server, "_p1_export", lambda: _export(40))
    server._MAP1_STATUS[:] = [0.0, None]
    pay = server.results_payload()
    assert all(r["scope"] != "Map 1" for r in pay["by_stat"])
    st = server.map1_results_status()
    assert st == {"staked_settled": 41, "threshold": 500, "shown_on_member_results": False, "as_of": "2026-10-25T06:00:00+00:00"}
    monkeypatch.setattr(server, "_p1_export", lambda: _export(600))
    server._MAP1_STATUS[:] = [0.0, None]
    assert server.map1_results_status()["shown_on_member_results"] is True
    assert any(r["scope"] == "Map 1" for r in server.results_payload()["by_stat"])


def test_admin_diagnostics_page_shows_the_map1_count():
    html = (server.WEB / "diagnostics.html").read_text(encoding="utf-8")
    assert "map1_results" in html and "settled staked P1 units" in html
