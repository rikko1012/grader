"""Slips backend fixes, 2026-10-21 (bug hunt + upgrades review):

- bug 4: the page's member-copy legs carry no board id -> slips.slip_key raised KeyError and /api/slip_log answered 400,
  so no member copy was ever logged (the old tests stubbed log_loaded);
- bug 5: Studio legs on HLTV-only matches carried match_slug None and never settled (the pick log keys them hltv-<id>);
- bug 8 (one LoL slip failing its re-check ended the whole LoL batch): lol/tests/test_slips_batch_20261021.py;
- verify: a replaced / changed / dismissed Studio slip got ok + copy_text again;
- U7: "build around" - settings.must_include (up to 2 board row ids) seeds every slip of the build, ineligible ids are
  reported in pinned_left_out with the builder's own reason;
- U10: copy text uses the 1Time name, no internal ids; U12: no entry payout in the LoL member payload.
"""
import json
import time

import pytest

import server
import slip_batches
import slip_builder as sb
import slips
from tests.test_api_moneyline_slips import build, call, env, recorded_clock  # noqa: F401  (fixtures)
from tests.test_audit13_site import post, site  # noqa: F401  (fixtures)
from tests.test_slip_builder import M, NOW, board, row


# ------------------------------------------------------------------ bug 4: the page's real copy body is logged
def _page_body(cid="c1234567"):
    """Exactly what web/index.html's Copy slip posts (mode member-copy): legs carry no "id"."""
    return {"cid": cid, "mode": "member-copy", "n": 2, "book": "underdog", "grader": "x",
            "legs": [{"nick": n, "prop": "kills", "scope": "12", "line": 30.5, "side": "More", "book": "underdog",
                      "line_type": "standard", "p": 0.6, "grade": "B", "match_slug": "m" + n, "team": "T" + n, "opponent": "O",
                      "start": "2026-10-22T00:00:00Z", "player_id": 1, "model_outcomes": None, "check": "verified",
                      "orig_line": 30.5, "check_note": ""} for n in ("a", "b")]}


def test_member_copy_without_ids_is_logged_through_the_real_handler(site, tmp_path):
    site.setattr(slips, "LOADED", tmp_path / "loaded.jsonl")
    site.setattr(slips, "_SEEN", {"rec": None, "load": None})
    site.setattr(server, "slip_leg_check", lambda leg, feeds=None, now=None: None)          # the leg checks have their own tests
    h = post("/api/slip_log", body=json.dumps(_page_body()).encode())
    assert h.code == 200 and h.body() == {"ok": True, "duplicate": False}
    rec = json.loads((tmp_path / "loaded.jsonl").read_text().splitlines()[0])
    assert rec["mode"] == "member-copy" and len(rec["legs"]) == 2 and rec["legs"][0]["book"] == "underdog"
    assert rec["key"] == "a/kills/12/underdog/ma:30.5:More|b/kills/12/underdog/mb:30.5:More"
    assert post("/api/slip_log", body=json.dumps(_page_body()).encode()).body() == {"ok": True, "duplicate": True}


def test_slip_key_keeps_board_ids_when_present():
    assert slips.slip_key([{"id": "x", "line": 1.5, "side": "More"}, {"id": 7, "line": 2, "side": "Less"}]) == "7:2:Less|x:1.5:More"


# ------------------------------------------------------------------ bug 5: HLTV-only match legs settle
def test_hltv_only_match_leg_carries_the_pick_log_slug_and_settles():
    assert server._settle_slug({"slug": None, "hltv_match": "2380000"}) == "hltv-2380000"
    assert server._settle_slug({"slug": "a-vs-b-21-10-2026", "hltv_match": "2380000"}) == "a-vs-b-21-10-2026"
    assert server._settle_slug({}) is None
    r = row(1, 1, match_slug=None, settle_slug="hltv-2380000", nick="kyousuke", player="kyousuke")
    rec = sb.pick_record(r)
    assert rec["match_slug"] == "hltv-2380000"
    picks = [{"player": "kyousuke", "prop": rec["prop"], "scope": rec["scope"], "match_slug": "hltv-2380000",
              "status": "settled", "actual": rec["line"] + 5, "result": json.dumps({"source": "hltv"})}]
    leg = dict(rec, side="More")
    assert slip_batches.leg_result_from_picks(picks)(leg) == "hit"
    # a BO3.gg slug still wins; the board row's own match_slug (card stake / anchor lookups) is untouched
    assert sb.pick_record(dict(r, match_slug="bo3-slug"))["match_slug"] == "bo3-slug"


def test_graded_board_rows_pass_the_settle_slug(site):
    site.setattr(server, "BOARD", {"u1": {"side": "More", "graded_line": 30.5, "settle_slug": "hltv-1", "match_slug": None,
                                          "grade": "B", "p": 0.6, "units": 1.0}})
    site.setattr(server.underdog, "board", lambda wait=False: {"at": time.time(), "error": None, "rows": [
        {"id": "u1", "player": "p", "prop": "kills", "scope": "12", "line": 30.5, "team": "A", "opponent": "B",
         "start": "2026-10-22T00:00:00Z", "higher": 1.0, "lower": 1.0}]})
    rows = server.board_payload("underdog")["rows"]
    assert rows and rows[0]["settle_slug"] == "hltv-1" and rows[0]["match_slug"] is None


# ------------------------------------------------------------------ U12: no entry payout to members
def test_lol_member_slips_carry_no_payout():
    out = server._lol_member_slips({"platforms": {"underdog": {"recommended": [
        {"size": 2, "legs": [{"market_key": "k1", "side": "over", "line": 4.5}], "entry_payout": 3.2,
         "model_chance_all_hit": 0.41}], "upcoming_series": 3, "upcoming_forecasts": 5}}})
    sl = next(iter(out.values()))["recommended"][0]
    assert "entry_payout" not in sl and sl["model_chance_all_hit"] == 0.41


# ------------------------------------------------------------------ U10: what members paste
def test_copy_text_uses_the_1time_name_and_no_internal_ids():
    res = sb.build(board(2, per_match=1, seed=16), {"sizes": [2], "count": 1}, model=M, now=NOW)
    t = sb.copy_text(res["slips"][2][0])
    head = t.splitlines()[0]
    assert head == "Underdog - 2-pick entry - standard lines - 1Time grader"
    assert "Prop Grader" not in t and "studio-" not in t and "preview/s" not in t and "slip " not in head


# ------------------------------------------------------------------ U7: build around pinned picks (builder)
def _ids(s):
    return {l["row_id"] for l in s["legs"]}


def test_pinned_pick_seeds_every_slip_of_the_build():
    rows = board(10, per_match=2, seed=3)
    pin = sorted(rows, key=lambda r: r["p"])[0]                       # the weakest pick: never chosen without the pin
    res = sb.build(rows, {"sizes": [2, 3, 4], "count": 3, "must_include": [pin["id"]]}, model=M, now=NOW)
    made = [s for n in (2, 3, 4) for s in res["slips"][n]]
    assert made and all(pin["id"] in _ids(s) for s in made)
    assert all(s["built_around"] == [pin["id"]] for s in made)
    assert res["pinned"] == [{"id": pin["id"], "label": res["pinned"][0]["label"]}] and res["pinned_left_out"] == []
    assert res["builder_version"] == sb.VERSION == "studio-4"
    for s in made:                                                    # in-slip rules hold around the pin
        ms = [l["match_slug"] for l in s["legs"]]
        ps = [l["player_id"] for l in s["legs"]]
        assert len(set(ms)) == len(ms) and len(set(ps)) == len(ps)
    assert len({frozenset(_ids(s)) for s in made}) == len(made)       # never the same slip twice
    plain = sb.build(rows, {"sizes": [2, 3, 4], "count": 3}, model=M, now=NOW)
    assert "built_around" not in plain["slips"][2][0] and plain["pinned"] == [] and plain["pinned_left_out"] == []
    assert "must_include" not in plain["settings"]                     # settings without pins are stored as before


def test_two_pins_and_sizes_that_cannot_hold_them():
    rows = board(10, per_match=2, seed=4)
    a, b = rows[0], next(r for r in rows if r["match_slug"] != rows[0]["match_slug"])
    res = sb.build(rows, {"sizes": [2, 3], "count": 2, "must_include": [a["id"], b["id"]]}, model=M, now=NOW)
    assert [s for s in res["slips"][3]] and all({a["id"], b["id"]} <= _ids(s) for s in res["slips"][3])
    assert len(res["slips"][2]) == 1 and _ids(res["slips"][2][0]) == {a["id"], b["id"]}   # just the two, offered once
    assert "1 of 2 2-pick slips" in res["shortage"][2] and "offered once" in res["shortage"][2]
    res4 = sb.build(rows, {"sizes": [2], "count": 1, "must_include": [a["id"], b["id"], ]}, model=M, now=NOW)
    assert res4["slips"][2]
    # three pins: refused as bad settings (400 at the endpoint)
    with pytest.raises(ValueError):
        sb.normalize_settings({"must_include": ["x", "y", "z"]})


def test_ineligible_pins_are_left_out_with_the_builders_reason():
    rows = board(8, per_match=2, seed=5)
    lean = dict(rows[0], id="lean1", grade="LEAN", units=0)
    moved = dict(rows[2], id="moved1", line=rows[2]["line"] + 1)        # graded at another line
    res = sb.build(rows + [lean, moved], {"sizes": [2], "count": 2, "must_include": ["lean1", "nope"]}, model=M, now=NOW)
    assert res["pinned"] == []
    assert res["pinned_left_out"] == [{"id": "lean1", "why": "below B / no units (Lean, B-)"},
                                      {"id": "nope", "why": "not on the current board (the line may have been pulled or moved)"}]
    assert res["slips"][2]                                             # the build goes on without them
    res = sb.build(rows + [moved], {"sizes": [2], "count": 1, "must_include": ["moved1"]}, model=M, now=NOW)
    assert res["pinned_left_out"] == [{"id": "moved1", "why": "line changed since the grade"}]
    # two pins from one match: the second is left out (one pick per match inside a slip)
    same = [r for r in rows if r["match_slug"] == rows[0]["match_slug"]][:2]
    res = sb.build(rows, {"sizes": [3], "count": 1, "must_include": [same[0]["id"], same[1]["id"]]}, model=M, now=NOW)
    assert [p["id"] for p in res["pinned"]] == [same[0]["id"]]
    assert res["pinned_left_out"][0]["id"] == same[1]["id"] and "same match" in res["pinned_left_out"][0]["why"]


def test_pin_opposite_a_side_already_in_the_batch_is_left_out(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "DB_PATH", tmp_path / "s.db")
    rows = board(8, per_match=1, seed=6)
    bid = sb.current_batch("sess-pin")
    first = sb.build(rows, {"sizes": [2], "count": 1}, batch=bid, key="k1", model=M, now=NOW)
    leg = first["slips"][2][0]["legs"][0]
    other = next(r for r in rows if r["id"] == leg["row_id"])
    flip = dict(other, id="flip1", side="Less" if other["side"] == "More" else "More", lower=1.0, higher=1.0)
    res = sb.build(rows + [flip], {"sizes": [2], "count": 1, "must_include": ["flip1"]}, batch=bid, key="k2", model=M, now=NOW)
    assert res["pinned_left_out"] == [{"id": "flip1", "why": "other side of this player's stat already in this batch"}]


# ------------------------------------------------------------------ U7 + verify through the endpoints
def test_build_endpoint_accepts_must_include_and_reports_left_out(env):
    rows = env["boards"].rows
    pin = rows[5]["id"]
    st, r = build(env, "alice", "pin-req-0001", {"book": "underdog", "sizes": [2, 3], "count": 2, "must_include": [pin, "zzz"]})
    assert st == 200 and [p["id"] for p in r["pinned"]] == [pin]
    assert r["pinned_left_out"] == [{"id": "zzz", "why": "not on the current board (the line may have been pulled or moved)"}]
    made = [s for n in ("2", "3") for s in r["slips"][n]]
    assert len(made) == 4 and all(pin in _ids(s) for s in made) and r["settings"]["must_include"] == [pin, "zzz"]
    for bad in (["a", "b", "c"], "r1", [1], ["bad id!"], ["x" * 129]):
        st, d = build(env, "alice", "pin-req-bad" + str(len(str(bad))), {"book": "underdog", "must_include": bad})
        assert st == 400 and "must_include" in d["error"], bad


@pytest.mark.parametrize("how", ["replaced", "changed", "dismissed"])
def test_verify_refuses_a_slip_that_no_longer_counts(env, how):
    st, a = build(env, "alice", "dead-req-0001", {"sizes": [2], "count": 1})
    slip = a["slips"]["2"][0]
    if how == "replaced":
        st, r = call(env, "POST", "/api/slip_studio/build", {"idem": "dead-req-0002", "replace": slip["slip_id"]}, member="alice")
        assert st == 200
    elif how == "changed":
        assert sb.record_event(a["batch_id"], slip["slip_id"], "verify_changed", key="vc-1")
    else:
        st, _ = call(env, "POST", "/api/slip_studio/event", {"batch_id": a["batch_id"], "slip_id": slip["slip_id"],
                                                              "kind": "dismiss", "idem": "dismiss-0001"}, member="alice")
        assert st == 200
    st, v = call(env, "POST", "/api/slip_studio/verify", {"slip": slip["slip_id"], "idem": "dead-ver-0001"}, member="alice")
    assert st == 200 and v["ok"] is False and v["copy_text"] is None and v["status"] == how
    if how == "replaced":
        assert v["action"] == "replaced" and v["replaced_by"] == r["build_id"]
    else:
        assert v["action"] == "rebuild"
    assert v["changes"]
