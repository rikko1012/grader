"""lc-1: CS2 / LoL letter containment (owner, Scout upgrade prompt v2 Phase 0 item 4; absolute rule "Never force letters on").

Display only: a held market shows the chance and "Lean Over / Lean Under", no letter, 0U, one banner per sport. Every surface
is tested with SCOUT_LETTER_CONTAINMENT on and off. The flag ships OFF until the owner confirms (coordinator 2026-11-05)."""
import json
import types

import pytest

import letter_containment as lc
import public_record
import results_stated
import results_view
import workspace_api
import workspace_rules as R
from gradecore import containment as gc

CS2_BANNER = "CS2 letters are held until stated chances match results."
LOL_BANNER = "LoL letters are held until stated chances match results."


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A private state file; the flag ON (tests of the contained display) unless a test turns it off; no owner switches."""
    for k in ("SCOUT_CONTAIN_CS2", "SCOUT_CONTAIN_LOL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(lc.FLAG, "1")
    p = tmp_path / lc.FILE_NAME
    gc.set_path(p)
    yield p
    gc.set_path(None)


def _flag(monkeypatch, on):
    monkeypatch.setenv(lc.FLAG, "1" if on else "0")
    gc.reset_cache()


def _release(path, *sports):
    gc.apply_verdicts({s: {"verdict": "release"} for s in sports}, "2026-11-05", "test", out=path)


# ------------------------------------------------------------------------------------------------ who decides
@pytest.mark.parametrize("value", [None, "", "0", "off", "maybe"])
def test_the_flag_ships_off_until_the_owner_confirms(state, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(lc.FLAG, raising=False)
    else:
        monkeypatch.setenv(lc.FLAG, value)
    gc.reset_cache()
    assert not lc.enabled() and not lc.contained("cs2") and not lc.contained("lol") and lc.hold_info("lol") is None
    doc = {"rows": [_row()]}
    assert lc.cs2_board(doc) is doc and public_record.letter_status(None, "cs2")["letters"] == "live"
    monkeypatch.setenv("SCOUT_CONTAIN_LOL", "1")                         # the owner's switch still contains
    gc.reset_cache()
    assert lc.contained("lol") and not lc.contained("cs2")


def test_flag_on_and_nothing_known_is_contained_with_the_plain_banner(state):
    for s in lc.SPORTS:
        assert lc.state(s) == {"contained": True, "stats": [], "source": "default"} and lc.contained(s, "kills")
    assert lc.banner("cs2") == CS2_BANNER and lc.banner("lol") == LOL_BANNER
    assert lc.hold_info("cs2")["banner"] == CS2_BANNER


def test_flag_off_shows_letters_but_the_owner_switch_still_contains(state, monkeypatch):
    _flag(monkeypatch, False)
    assert not lc.contained("cs2") and lc.state("cs2")["source"] == "flag off" and lc.hold_info("cs2") is None
    monkeypatch.setenv("SCOUT_CONTAIN_CS2", "1")
    gc.reset_cache()
    assert lc.contained("cs2") and lc.state("cs2")["source"] == "owner" and not lc.contained("lol")


@pytest.mark.parametrize("off", ["0", "off", "false"])
def test_the_owner_switch_can_never_force_letters_on(state, monkeypatch, off):
    monkeypatch.setenv("SCOUT_CONTAIN_CS2", off)
    monkeypatch.setenv("SCOUT_CONTAIN_LOL", off)
    gc.reset_cache()
    assert lc.contained("cs2") and lc.contained("lol")                     # nothing known: held, whatever the switch says
    assert lc.snapshot()["sports"]["cs2"]["switch"] == {"name": "SCOUT_CONTAIN_CS2", "value": off, "asked": "release", "applied": False}
    gc.apply_verdicts({"cs2": {"verdict": "contain"}}, "2026-11-05", "test", out=state)
    assert lc.contained("cs2")


def test_release_comes_only_from_the_check(state):
    _release(state, "cs2")
    assert not lc.contained("cs2") and lc.state("cs2")["source"] == "check" and lc.contained("lol")
    gc.apply_verdicts({"cs2": {"verdict": "contain:hs"}}, "2026-11-06", "test", out=state)
    assert not lc.contained("cs2", "kills") and lc.contained("cs2", "hs")
    assert lc.banner("cs2") == "CS2 Headshots letters are held until stated chances match results."


# ------------------------------------------------------------------------------------------------ the periodic check
def _p1(n=320, stated=0.62, hit=0.62, book="underdog"):
    """n settled P1 rows, alternating Over / Under, `hit` of them won on the favoured side."""
    rows = []
    for i in range(n):
        over_side = i % 2 == 0
        p = stated if over_side else 1 - stated
        k = i // 2                                   # wins spread evenly through time, on each side
        win = int((k + 1) * hit) > int(k * hit)
        rows.append({"match": f"m{i // 4}", "p": p, "over": int(win) if over_side else int(not win), "prop": "kills" if i % 3 else "hs",
                     "scope": "12", "book": book, "start": f"2026-10-{1 + i // 40:02d}T{i % 24:02d}:00:00+00:00", "units": 1})
    rows.sort(key=lambda r: r["start"])
    return {"at": "2026-11-05T00:00:00+00:00", "rows": rows, "pushes": []}


def test_cs2_legs_are_the_member_results_picks():
    ex = _p1(40)
    ex["rows"].append({"match": "x", "p": 0.55, "over": 1, "prop": "kills", "scope": "12", "book": "underdog", "start": "2026-11-01"})
    legs = lc.cs2_legs(ex)
    picks, _, _ = results_stated.rule_picks(ex)
    assert len(legs) == len(picks) == 40 and results_view.RULE == 0.57
    assert [l["status"] == "win" for l in legs] == [bool(p["win"]) for p in picks]
    assert abs(legs[0]["break_even"] - 3.5 ** -0.5) < 1e-9
    # owner sign-off 2026-11-05: PrizePicks' help-center table is verified, so its legs carry the Power 2-pick break-even
    assert abs(lc.cs2_legs(_p1(5, book="prizepicks"))[0]["break_even"] - 3.0 ** -0.5) < 1e-9


def _srv(tmp_path, export=None, lol_rows=None, export_error=None):
    def p1():
        if export_error:
            raise export_error
        return export or {}
    if lol_rows is not None:
        (tmp_path / "learning.json").write_text(json.dumps({"containment_rows": lol_rows}), encoding="utf-8")
    return types.SimpleNamespace(_p1_export=p1, LOL_CENTER=tmp_path)


def test_job_releases_an_honest_market_and_holds_a_miscalibrated_one(state, tmp_path):
    lol_rows = [{"p": 0.62, "status": "win" if i % 2 else "loss", "match_key": f"s{i // 6}", "stat": "kills",
                 "side": "over" if i % 2 else "under", "platform": "underdog", "at": f"2026-10-{1 + i // 30:02d}T00:00:00Z"}
                for i in range(330)]
    out = lc.run_once(_srv(tmp_path, _p1(stated=0.62, hit=0.62), lol_rows), log=lambda *a, **k: None)
    assert out["results"]["cs2"]["verdict"] == "release" and out["results"]["lol"]["verdict"] == "contain"
    assert not lc.contained("cs2") and lc.contained("lol")
    doc = json.loads(state.read_text(encoding="utf-8"))
    assert doc["version"] == gc.VERSION and {e["scope"] for e in doc["log"]} == {"cs2", "lol"}
    assert out["results"]["cs2"]["legs"]["decided_with_break_even"] == 320
    # 62% stated, 53% won: held again by the next pass
    lc.run_once(_srv(tmp_path, _p1(stated=0.62, hit=0.53)), log=lambda *a, **k: None)
    assert lc.contained("cs2")


def test_a_source_that_cannot_be_read_changes_nothing(state, tmp_path):
    _release(state, "cs2")
    out = lc.run_once(_srv(tmp_path, export_error=OSError("locked")), log=lambda *a, **k: None)
    assert "cs2" in out["errors"] and "lol" in out["errors"]           # no learning.json either
    assert not lc.contained("cs2") and lc.contained("lol")
    lc.run_once(_srv(tmp_path, {}), log=lambda *a, **k: None)           # an empty export: insufficient, unchanged
    assert not lc.contained("cs2")


def test_prizepicks_legs_count_at_their_verified_break_even(state, tmp_path):
    """Owner sign-off 2026-11-05: PrizePicks legs have a known break-even (Power 2-pick, 57.7%), so they count; 62% stated and
    hit sits too close to 57.7% for the EV interval to clear 0, so the verdict is contain (not "insufficient")."""
    lc.run_once(_srv(tmp_path, _p1(stated=0.62, hit=0.62, book="prizepicks")), log=lambda *a, **k: None)
    assert lc.contained("cs2") and lc._JOB["results"]["cs2"]["verdict"] == "contain"


# ------------------------------------------------------------------------------------------------ CS2 surfaces
def _row(**kw):
    return dict({"id": "u1", "nick": "frozen", "prop": "kills", "scope": "12", "line": 30.5, "side": "More", "p": 0.664,
                 "grade": "B+", "model_grade": "B+", "units": 1.0, "units_full": 1.5, "cap_n": 3, "cap_reason": "same-match cap",
                 "stake_reason": None, "state": "GRADE", "book": "underdog", "graded_model": "T"}, **kw)


def test_board_flag_on_shows_lean_0u_and_the_banner(state):
    scout = _row(id="u2", state="SCOUT", grade="SCOUT", model_grade="SCOUT", units=0.0)
    doc = {"rows": [_row(), scout, _row(id="u3", side=None, grade=None, model_grade=None)], "version": "T"}
    out = lc.cs2_board(doc)
    r = out["rows"][0]
    assert (r["grade"], r["model_grade"], r["units"], r["letter_held"], r["p"], r["side"]) == ("LEAN", "LEAN", 0.0, True, 0.664, "More")
    assert "units_full" not in r and "cap_n" not in r and "cap_reason" not in r
    assert out["rows"][1] is scout and out["rows"][2] is doc["rows"][2]
    assert out["letter_hold"]["banner"] == CS2_BANNER
    assert doc["rows"][0]["grade"] == "B+" and doc["rows"][0]["units"] == 1.0          # the board itself is untouched
    assert "No bet" not in json.dumps(out)


def test_board_flag_off_is_the_same_object(state, monkeypatch):
    _flag(monkeypatch, False)
    doc = {"rows": [_row()]}
    assert lc.cs2_board(doc) is doc and lc.cs2_rows(doc["rows"]) is doc["rows"]


def test_card_flag_on_and_off(state, monkeypatch):
    res = {"grade": {"prop": "kills", "grade": "A", "grade_less": "C", "units": 2.0, "units_less": 0.0, "units_free": 2.0, "p_more": 0.75},
           "verdict": _row(grade="A", model_grade="A", units=2.0), "ladder": [_row(line=29.5, grade="A", exact_grade="A"), {"line": 31.5}]}
    out = lc.cs2_card(res)
    assert out["verdict"]["model_grade"] == "LEAN" and out["verdict"]["units"] == 0.0 and out["verdict"]["p"] == 0.664
    assert out["grade"]["grade"] == "LEAN" and out["grade"]["grade_less"] == "LEAN" and out["grade"]["units"] == 0.0
    assert out["grade"]["p_more"] == 0.75 and "units_free" not in out["grade"]
    assert out["ladder"][0]["grade"] == "LEAN" and "exact_grade" not in out["ladder"][0] and out["ladder"][1] == {"line": 31.5}
    assert out["letter_hold"]["banner"] == CS2_BANNER and res["verdict"]["grade"] == "A"    # the logged dicts untouched
    _flag(monkeypatch, False)
    assert lc.cs2_card(res) is res


def test_studio_records_flag_on_and_off(state, monkeypatch):
    b = {"slips": {"2": [{"legs": [{"prop": "kills", "grade": "B+", "units": 1.0, "p": 0.66}, {"prop": "hs", "grade": "B", "units": 0.5}]}]}}
    out = lc.cs2_slip_records(b)
    legs = out["slips"]["2"][0]["legs"]
    assert [(l["grade"], l["units"]) for l in legs] == [("LEAN", 0.0), ("LEAN", 0.0)] and legs[0]["p"] == 0.66
    _flag(monkeypatch, False)
    assert lc.cs2_slip_records(b) is b


def _hub_row(**kw):
    return _row(team="A", team_name="Alpha", opp_name="Beta", match_slug="hltv-1-alpha-vs-beta", start="2026-11-06T18:00:00Z", **kw)


def test_hub_record_flag_on_has_no_letter_and_the_hash_hides_it(state, monkeypatch):
    a, b = workspace_api.record_of(_hub_row()), workspace_api.record_of(_hub_row(grade="A", model_grade="A"))
    for rec in (a, b):
        s = rec["scout"]
        assert s["grade"] is None and s["units"] == 0.0 and s["stake_reason"] is None and s["letter_held"] is True
        assert s["side"] == "over" and s["p_side"] == 0.664
    assert a["scout"]["read_rev"] == b["scout"]["read_rev"]           # a held letter is not recoverable from the read's hash
    _flag(monkeypatch, False)
    c, d = workspace_api.record_of(_hub_row()), workspace_api.record_of(_hub_row(grade="A", model_grade="A"))
    assert c["scout"]["grade"] == "B+" and c["scout"]["units"] == 1.0 and "letter_held" not in c["scout"]
    assert c["scout"]["read_rev"] != d["scout"]["read_rev"]


def test_hub_slip_legs_have_no_rating_while_held(state, monkeypatch):
    leg = {"name": "p0", "sport": "cs2", "match": "m0", "line": 30.5, "multiplier": 1.0, "on_side": True, "p": 0.6, "tag": "T",
           "prop": "kills", "scope": "12"}
    held = workspace_api._held_leg(leg)
    assert held["held"] == CS2_BANNER
    assert workspace_api._held_leg(dict(leg, sport="lol", prop=None, stat="kills"))["held"] == LOL_BANNER
    out = R.ev_status("underdog", "standard", [held, dict(held, name="p1", match="m1", line=31.5)], lambda: None, 0)
    assert out == {"available": False, "reason": CS2_BANNER, "payout": out["payout"]} and out["payout"]["multiple"] == 3.5
    assert not R.leg_edge_ok(held, {"T|kills|12": [[0.57, 0.65]]}) and R.leg_edge_ok(leg, {"T|kills|12": [[0.57, 0.65]]})
    _flag(monkeypatch, False)
    assert workspace_api._held_leg(leg) is leg
    assert "held" not in workspace_api._held_leg(dict(leg, sport="lol"))
    assert R.ev_status("underdog", "standard", [leg, dict(leg, name="p1", match="m1", line=31.5)], lambda: None, 0)["reason"] != CS2_BANNER


def test_hub_board_has_no_edge_while_cs2_is_held(state, monkeypatch):
    calls = []
    monkeypatch.setattr(workspace_api, "_gate", lambda srv: calls.append(1) or {"at_ts": None})
    assert workspace_api._board_columns(object())["edge_cells"] == {} and calls == []
    _flag(monkeypatch, False)
    workspace_api._board_columns(object())
    assert calls == [1]


# ------------------------------------------------------------------------------------------------ LoL surfaces
def _lol_doc():
    return {"rows": [{"key": "k1", "stat": "kills", "grade": "A-", "units": 0.0, "pick": "Over 4.5", "pick_offered": True},
                     {"key": "k2", "stat": "assists", "grade": "Lean", "units": 0.0, "pick": "Under 7.5", "pick_offered": True},
                     {"key": "k3", "stat": "deaths", "grade": None, "pick_offered": False}],
            "match_tables": [{"teams": "A vs B", "markets": [{"stat": "kills", "lines": [{"grade": "A-"}, {"grade": None}]}]}],
            "slips": {"Underdog": {"recommended": [{"size": 2, "legs": [{"key": "k1"}, {"key": "k2"}]}], "why_none": []}}}


def test_lol_board_flag_on_reads_lean_and_offers_no_rated_slip(state):
    out = lc.lol_member_doc(_lol_doc())
    r1, r2, r3 = out["rows"]
    assert (r1["grade"], r1["units"], r1["letter_held"], r1["pick"]) == ("Lean", 0.0, True, "Over 4.5")
    assert r2["grade"] == "Lean" and "letter_held" not in r2 and r3["grade"] is None
    assert out["match_tables"][0]["markets"][0] == {"lines": [{"grade": "Lean"}, {"grade": None}]}
    assert out["slips"]["Underdog"]["recommended"] == [] and out["slips"]["Underdog"]["why_none"][0]["text"] == LOL_BANNER
    assert out["letter_hold"]["banner"] == LOL_BANNER


def test_lol_board_flag_off_keeps_letters(state, monkeypatch):
    _flag(monkeypatch, False)
    out = lc.lol_member_doc(_lol_doc())
    assert out["rows"][0]["grade"] == "A-" and "letter_hold" not in out and len(out["slips"]["Underdog"]["recommended"]) == 1
    assert out["match_tables"][0]["markets"][0] == {"lines": [{"grade": "A-"}, {"grade": None}]}     # the stat stays server-side


def test_lol_member_board_end_to_end(state, tmp_path, monkeypatch):
    import server
    from tests.test_lol_member_page import _board
    monkeypatch.setattr(server, "LOL_CENTER", tmp_path)
    _board(tmp_path)
    server._LOL_MEMBER.update(mtime=None, body=None)
    view = json.loads(server.lol_member_board())
    row, = view["rows"]
    assert row["grade"] == "Lean" and row["units"] == 0.0 and row["letter_held"] is True and row["p_over"] == 0.58
    assert view["letter_hold"]["banner"] == LOL_BANNER
    _flag(monkeypatch, False)                                        # the state is part of the cache key: one rebuild
    view = json.loads(server.lol_member_board())
    assert view["rows"][0]["grade"] == "B" and "letter_hold" not in view


# ------------------------------------------------------------------------------------------------ record status
def test_record_status_reflects_the_real_state(state, monkeypatch):
    assert public_record.letter_status(None, "cs2") == {"letters": "held", "text": CS2_BANNER}
    assert public_record.letter_status(None, "lol") == {"letters": "held", "text": LOL_BANNER}
    _release(state, "cs2")
    assert public_record.letter_status(None, "cs2") == {"letters": "live", "text": "The CS2 board shows letter grades."}
    _flag(monkeypatch, False)
    assert public_record.letter_status(None, "lol")["letters"] == "live"


from tests.test_upgrade_gate import env, get  # noqa: E402,F401  (the running-server fixture)


def _fresh_caches(monkeypatch):
    import server
    for name, val in (("_RESULTS", {}), ("_INFLIGHT", {}), ("_ENC_LOCKS", {}), ("_GB", {})):
        monkeypatch.setattr(server, name, val)


def test_board_and_record_routes_flag_on_and_off(env, state, monkeypatch):
    monkeypatch.setenv("SCOUT_PUBLIC_RECORD", "1")
    monkeypatch.setenv("SCOUT_RECORD_STATUS", "1")
    st, body, _ = get(env.base, "/api/board", cookie="good-m1")
    d = json.loads(body)
    sided = [r for r in d["rows"] if r.get("side") and r.get("state") != "SCOUT"]
    assert st == 200 and sided and d["letter_hold"]["banner"] == CS2_BANNER
    assert all(r["model_grade"] == "LEAN" and r["grade"] == "LEAN" and r["units"] == 0.0 and r["letter_held"] for r in sided)
    assert all(r["p"] is not None for r in sided)
    rec = json.loads(get(env.base, "/api/record?sport=cs2")[1])
    assert rec["status"] == {"letters": "held", "text": CS2_BANNER}
    _flag(monkeypatch, False)
    _fresh_caches(monkeypatch)
    d = json.loads(get(env.base, "/api/board", cookie="good-m1")[1])
    assert "letter_hold" not in d and not any(r.get("letter_held") for r in d["rows"])
    assert any(r.get("model_grade") in lc.LETTERS for r in d["rows"])
    rec = json.loads(get(env.base, "/api/record?sport=cs2")[1])
    assert rec["status"] == {"letters": "live", "text": "The CS2 board shows letter grades."}


# ------------------------------------------------------------------------------------------------ the server wiring
def test_every_member_route_applies_the_mask():
    """The member surfaces read the mask (source check: the job's state is read, never computed, on the request path)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
    for needle in ("letter_containment.cs2_board(\n                    with_ud_links(board_payload(bk_))",       # /api/board
                   "res = letter_containment.cs2_card(res)",                                                   # card
                   "letter_containment.cs2_rows(board_payload(book)[\"rows\"])",                               # match compare
                   "gb = letter_containment.cs2_board(graded_board(st[\"book\"]))",                            # studio
                   "graded = letter_containment.cs2_board(graded_board(book, max_age=2))",                     # studio verify
                   "letter_containment.lol_member_doc(doc)",                                                   # LoL page + hub
                   "letter_containment.start(sys.modules[__name__], DATA_DIR)"):                               # the job
        assert needle in src, needle
    assert "verdict(" not in src.split("def board_payload")[1].split("def board_stake")[0]


def test_hub_scout_cell_and_card_render_a_held_row_as_lean_0u():
    """The hub's one Scout cell and the one player card both draw the letter through gradeChip: a held row reads Lean Over /
    Lean Under with 0U (no letter); the board shows one banner per held sport."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "web" / "upgrade-hub.js").read_text(encoding="utf-8")
    chip = js.split("function gradeChip(r) {")[1].split("\n  }")[0]
    assert chip.index("s.letter_held") < chip.index("if(s.grade)") and "Lean ${sideWord(s.side)}" in chip and ">0U<" in chip
    assert "<div class=\"h-scout-grade\">${gradeChip(r)}${P('ev_band')&&!(P('edge')&&H.showEdge)?bandChip(r):''}</div>" in js and "side===s.side?gradeChip(r)" in js
    assert "letters are held until stated chances match results." in js and "r.scout.letter_held" in js
