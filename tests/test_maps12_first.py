"""Maps 1-2 first (owner 2026-11-12): "make sure map 1-2 lines show before map 1 lines and are prioritized in slip builder over map 1
lines unless the map 1 line has more value".

Slip builders: the hub's Auto-build (slip_quality sq-1, value = rank_p, the per-leg number its search ranks by) and the classic Slip
Studio (slip_builder, value = the leg's chance). One player's stat in one match on two maps scopes -> the longest scope; a shorter
scope only when its value is strictly higher; a tie -> the longer scope; never both scopes in one slip; the count asked for is built.
Board: records go out Maps 1-3, Maps 1-2, Map 1 (stable), and rows by player list a stat's Maps 1-2 row before its Map 1 row."""
import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

import slip_builder as sb
import slip_quality as sq
import workspace_api
import workspace_lol
from tests.test_slip_quality import NOW, rec, legs_of
from tests import test_slip_builder as tsb

ROOT = Path(__file__).resolve().parent.parent


def _pp(recs, size=2, count=1, **o):
    legs, _ = legs_of(recs)
    return sq.build(legs, dict({"platform": "prizepicks", "entry_type": "power", "count": count, "size": size}, **o))


def _scopes(res, name):
    return [l["period"] for s in res["slips"] for l in s["legs"] if l["player"] == name]


def _pair(p12, p1, **kw):
    """The same player + stat + match on Maps 1-2 and on Map 1 (different offer ids), plus other matches to fill slips."""
    return [rec("star", "e1", p12, period="maps12", line=30.5, rid="m12", team="T1", **kw),
            rec("star", "e1", p1, period="map1", line=15.5, rid="m1", team="T1", **kw)]


def _others(n=4, p=0.58):
    return [rec(f"o{i}", f"x{i}", p, team=f"X{i}") for i in range(n)]


# ------------------------------------------------------------------------------------------------ Auto-build (slip_quality)

def test_maps12_chosen_when_both_scopes_exist():
    res = _pp(_pair(0.66, 0.64) + _others(), size=2, count=3)
    assert len(res["slips"]) == 3
    assert "map1" not in _scopes(res, "star") and "maps12" in _scopes(res, "star")


def test_map1_chosen_only_when_its_value_is_strictly_higher():
    res = _pp(_pair(0.62, 0.66) + _others(), size=2, count=3)
    assert set(_scopes(res, "star")) == {"map1"}
    legs, _ = legs_of(_pair(0.62, 0.66))
    kept, notes = sq.scope_choice(legs)
    assert [l["period"] for l in kept] == ["map1"]
    assert notes[0]["margin"] > 0 and notes[0]["kept"]["period"] == "map1" and notes[0]["longest"]["period"] == "maps12"


def test_tie_goes_to_maps12():
    legs, _ = legs_of(_pair(0.64, 0.64))
    assert legs[0]["rank_p"] == legs[1]["rank_p"]
    kept, notes = sq.scope_choice(legs)
    assert [l["period"] for l in kept] == ["maps12"] and notes[0]["margin"] == 0
    res = _pp(_pair(0.64, 0.64) + _others(), size=2, count=3)
    assert set(_scopes(res, "star")) == {"maps12"}


def test_value_is_rank_p_not_the_printed_chance():
    # a band table that ranks the Map 1 leg's band above the Maps 1-2 leg's band although its printed chance is lower
    table = {"k": 10, "lines": {}, "cells": {"cs2|kills|over|2": {"n": 1000, "matches": 300, "printed": .62, "observed": .70},
                                             "cs2|kills|over|3": {"n": 1000, "matches": 300, "printed": .67, "observed": .50}}}
    legs, _ = legs_of(_pair(0.66, 0.61), table=table)
    kept, _ = sq.scope_choice(legs)
    assert [l["period"] for l in kept] == ["map1"]


def test_never_both_scopes_in_one_slip_and_count_is_built():
    recs = _pair(0.70, 0.69) + _others(6, 0.57)
    for size, count in ((2, 8), (3, 5), (4, 4)):
        res = _pp(recs, size=size, count=count)
        assert len(res["slips"]) == count
        for s in res["slips"]:
            assert len([l for l in s["legs"] if l["player"] == "star"]) <= 1


def test_no_repeat_mode_uses_the_preferred_scope_first():
    res = _pp(_pair(0.70, 0.69) + _others(5, 0.57), size=2, count=3, no_repeat=True)
    first = [l["period"] for l in res["slips"][0]["legs"] if l["player"] == "star"]
    assert first == ["maps12"]
    for s in res["slips"]:
        assert len([l for l in s["legs"] if l["player"] == "star"]) <= 1


def test_lol_bo5_prefers_maps13_then_maps12_then_map1():
    def lol(period, p, rid):
        return rec("faker", "s1", p, sport="lol", grade="A-", units=0.0, period=period, rid=rid, team="T1")
    legs, _ = legs_of([lol("maps13", 0.62, "a"), lol("maps12", 0.62, "b"), lol("map1", 0.62, "c")])
    assert [l["period"] for l in sq.scope_choice(legs)[0]] == ["maps13"]
    legs, _ = legs_of([lol("maps13", 0.60, "a"), lol("maps12", 0.66, "b"), lol("map1", 0.66, "c")])
    assert [l["period"] for l in sq.scope_choice(legs)[0]] == ["maps12"]
    legs, _ = legs_of([lol("maps13", 0.60, "a"), lol("maps12", 0.62, "b"), lol("map1", 0.66, "c")])
    assert [l["period"] for l in sq.scope_choice(legs)[0]] == ["map1"]


def test_other_stats_players_and_matches_are_untouched():
    recs = [rec("star", "e1", 0.66, period="maps12", rid="a", team="T1"), rec("star", "e1", 0.60, period="map1", stat="hs", rid="b", team="T1"),
            rec("star", "e2", 0.60, period="map1", rid="c", team="T1"), rec("mate", "e1", 0.60, period="map1", rid="d", team="T1")]
    legs, _ = legs_of(recs)
    assert len(sq.scope_choice(legs)[0]) == 4


# ------------------------------------------------------------------------------------------------ classic Slip Studio (slip_builder)

@pytest.fixture
def studio_db(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "DB_PATH", tmp_path / "slip_studio.db")


def _studio_rows(p12, p1):
    rows = tsb.board(5, per_match=1)
    star = dict(nick="star", player="star", player_id=77, team="T9a", team_name="T9a", opponent="T9b", opp_name="T9b", match_slug="m9")
    rows.append(tsb.row(90, 9, scope="12", p=p12, line=30.5, graded_line=30.5, **star))
    rows.append(tsb.row(91, 9, scope="1", p=p1, line=15.5, graded_line=15.5, **star))
    return rows


def _studio_star(res):
    return [l["scope"] for n in (2, 3, 4) for s in res["slips"][n] for l in s["legs"] if l["nick"] == "star"]


@pytest.mark.parametrize("p12,p1,want", [(0.70, 0.69, "12"), (0.69, 0.70, "1"), (0.70, 0.70, "12")])
def test_studio_scope_all_prefers_maps12_unless_map1_is_higher(studio_db, p12, p1, want):
    res = sb.build(_studio_rows(p12, p1), {"scope": "all", "sizes": [2], "count": 3}, model=tsb.M, now=tsb.NOW)
    assert len(res["slips"][2]) == 3
    assert set(_studio_star(res)) == {want}
    assert any(e["reason"] == sb.SCOPE_REASON and e["count"] == 1 for e in res["excluded"])


def test_studio_single_scope_settings_unchanged(studio_db):
    res = sb.build(_studio_rows(0.60, 0.75), {"scope": "12", "sizes": [2], "count": 2}, model=tsb.M, now=tsb.NOW)
    assert set(_studio_star(res)) <= {"12"}
    assert not any(e["reason"] == sb.SCOPE_REASON for e in res["excluded"])


# ------------------------------------------------------------------------------------------------ board order

def _brec(oid, player, period, stat="kills"):
    return {"sport": "cs2", "event": {"id": "e1", "label": "A vs B", "start_utc": "2030-01-01T00:00:00Z"}, "player": {"key": player, "name": player, "team": "A"},
            "market": {"stat": stat, "stat_label": stat, "period": period, "period_label": period, "line_type": "standard"},
            "offer": {"platform": "prizepicks", "platform_label": "PrizePicks", "offer_id": oid, "line": 10.5, "over": {"offered": True}, "under": {"offered": True}},
            "scout": {"side": "over", "p_side": 0.6, "grade": "B", "projection": 12.0}}


def test_server_record_order_longest_scope_first_and_stable():
    recs = [_brec("1", "a", "map1"), _brec("2", "a", "maps12"), _brec("3", "b", "map1", "hs"), _brec("4", "b", "maps13"), _brec("5", "c", "maps12"),
            _brec("6", "c", "other")]
    assert [r["offer"]["offer_id"] for r in sorted(recs, key=workspace_api.scope_order)] == ["4", "2", "5", "1", "3", "6"]
    assert [r["offer"]["offer_id"] for r in sorted(recs, key=workspace_lol._scope_order)] == ["4", "2", "5", "1", "3", "6"]


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_rows_by_player_list_maps12_before_map1(tmp_path):
    js = tmp_path / "run.mjs"
    js.write_text("import {readFileSync} from 'node:fs';\nimport * as L from %s;\nconst all = JSON.parse(readFileSync(process.argv[2], 'utf8'));\n"
                  "const p = L.groupPlayers(L.groupProps(all, all, false));\n"
                  "console.log(JSON.stringify(p.map(x => x.props.map(g => g.main.market.stat + '/' + g.main.market.period))));\n"
                  % json.dumps((ROOT / "web" / "upgrade-lines.js").as_uri()), encoding="utf-8")
    data = tmp_path / "in.json"
    # the order a board would hand over WITHOUT the server's sort (Map 1 first), to prove the grouping itself puts Maps 1-2 first
    data.write_text(json.dumps([_brec("1", "a", "map1"), _brec("2", "a", "map1", "hs"), _brec("3", "a", "maps12"), _brec("4", "a", "maps12", "hs"),
                                _brec("5", "b", "map1"), _brec("6", "b", "maps13"), _brec("7", "b", "maps12")]), encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(js), str(data)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [["kills/maps12", "kills/map1", "hs/maps12", "hs/map1"], ["kills/maps13", "kills/maps12", "kills/map1"]]

