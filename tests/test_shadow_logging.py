"""2026-10-18 audit: a pre-match shadow record was repeated only when the live / fh-1 arms changed, so cm-1 becoming available
after its deploy (37 forward units without a value) or moving with its 2-hourly tables was never saved while the live grade
stayed the same. A shadow arm that appears, becomes available / unavailable, or changes its value now appends a new
timestamped record; an unchanged record is still not repeated, earlier records are never touched and nothing is backdated.
Every value below is a LABELLED TEST FIXTURE."""
import copy
import json

import pytest

import picklog

START = "2026-10-20T15:00:00+00:00"
CM1_VALUE = {"version": "cm-1", "p_more": 0.6021, "lambda": 31.4, "tables": {"built_at": "2026-10-20T04:00:00+00:00", "tables_sha": "t1"}}


@pytest.fixture
def con(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    c = picklog._db()
    yield c
    c.close()


def rec(cm1="absent", **arms):
    r = {"challenger": "fh-1", "line": 29.5, "prop": "kills", "scope": "12", "inputs_sha": "in-1", "gi_sha": "gi-1",
         "live": {"p_more": 0.55, "lambda": 30.4, "units": 1.0, "grade": "B", "side": "More"},
         "fh1": {"p_more": 0.58, "lambda": 31.0, "units": 1.0, "grade": "B", "side": "More"},
         "published": {"p_more": 0.55, "lambda": 30.4}, "n0_check": {"exact": True}, "history": {"inputs_sha": "in-1"},
         "read_at": {"player": "2026-10-20T03:00:00+00:00"}, "adds": {"player_maps": 0}, "identical_history": False,
         "group": "established", "lc1": {"p_more": 0.54, "z": 0.3, "version": "lc-1"}}
    if cm1 != "absent":
        r["cm1"] = copy.deepcopy(cm1)
    r.update(copy.deepcopy(arms))
    return r


def write(con, r, at):
    out = picklog._shadow_forecast(con, 1, {"logged_at": at, "match_start": START}, {"rec": r, "bodies": {}})
    con.commit()
    return out


def stored(con):
    return [(x["logged_at"], json.loads(x["rec"])) for x in con.execute("SELECT logged_at, rec FROM shadow_forecasts ORDER BY id")]


def test_an_unchanged_record_is_not_repeated(con):
    assert write(con, rec(CM1_VALUE), "2026-10-20T05:00:00+00:00") is True
    assert write(con, rec(CM1_VALUE), "2026-10-20T05:05:00+00:00") is False
    other_build = dict(CM1_VALUE, tables={"built_at": "2026-10-20T06:00:00+00:00", "tables_sha": "t2"})    # same value
    assert write(con, rec(other_build), "2026-10-20T06:05:00+00:00") is False
    assert len(stored(con)) == 1


def test_a_new_shadow_arm_appends_with_the_live_grade_identical(con):
    write(con, rec(), "2026-10-20T05:00:00+00:00")                        # recorded before cm-1 existed (the deploy gap)
    assert write(con, rec(CM1_VALUE), "2026-10-20T05:10:00+00:00") is True
    assert write(con, rec(CM1_VALUE, xx1={"p_more": 0.5}), "2026-10-20T05:20:00+00:00") is True     # a later registration
    (t0, r0), (t1, r1), (t2, r2) = stored(con)
    assert (t0, t1, t2) == ("2026-10-20T05:00:00+00:00", "2026-10-20T05:10:00+00:00", "2026-10-20T05:20:00+00:00")
    assert "cm1" not in r0 and r1["cm1"]["p_more"] == 0.6021 and r2["xx1"] == {"p_more": 0.5}       # old record untouched
    assert r0["live"] == r1["live"] == r2["live"] and r0["fh1"] == r1["fh1"] == r2["fh1"]


def test_availability_changes_append_both_ways(con):
    write(con, rec({"version": "cm-1", "ineligible": "cm-1 tables not built yet"}), "2026-10-20T05:00:00+00:00")
    assert write(con, rec(CM1_VALUE), "2026-10-20T05:10:00+00:00") is True                         # became available
    assert write(con, rec({"version": "cm-1", "error": "OSError: tables unreadable"}), "2026-10-20T05:20:00+00:00") is True
    assert write(con, rec({"version": "cm-1", "error": "OSError: tables unreadable (again)"}), "2026-10-20T05:25:00+00:00") is False
    assert write(con, rec(CM1_VALUE), "2026-10-20T05:30:00+00:00") is True                         # recovered
    assert len(stored(con)) == 4


def test_a_cm1_value_change_appends_and_the_latest_pre_match_record_carries_it(con):
    write(con, rec(CM1_VALUE), "2026-10-20T05:00:00+00:00")
    moved = dict(CM1_VALUE, p_more=0.6049, tables={"built_at": "2026-10-20T06:00:00+00:00", "tables_sha": "t2"})
    assert write(con, rec(moved), "2026-10-20T06:05:00+00:00") is True
    rows = stored(con)
    assert [r["cm1"]["p_more"] for _, r in rows] == [0.6021, 0.6049]
    assert rows[0][1]["live"] == rows[1][1]["live"]                       # only the shadow arm moved


def test_records_are_still_written_only_before_the_start():
    """write_row appends shadow records only for an open pick before its start (nothing after the start, nothing backdated)."""
    row = {"logged_at": "2026-10-20T15:05:00+00:00", "match_start": START}
    assert picklog._before_start(row, {"match_start": START}) is False
    assert picklog._before_start(dict(row, logged_at="2026-10-20T14:00:00+00:00"), {"match_start": START}) is True
