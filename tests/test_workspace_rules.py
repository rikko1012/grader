"""workspace_rules: per-combination entry status and the EV gate, as pure functions."""
import ast
from pathlib import Path

import pytest

import workspace_rules as R

ROOT = Path(R.__file__).parent
NOW = 1_790_000_000.0


def leg(nick, team, event="e1", sport="cs2", side="over", book=None, sample=False):
    return {"sport": sport, "nick": nick, "team": team, "event": event, "side": side, "book": book, "sample": sample}


def test_a_plain_prizepicks_entry_is_valid_even_with_the_always_on_note():
    e = R.entry_status("prizepicks", [leg("a", "T1", "e1"), leg("b", "T2", "e2")], "power")
    assert e["status"] == "valid" and e["ok"] and e["trackable"] and e["problems"] == []


@pytest.mark.parametrize("n,ok", [(1, False), (2, True), (6, True), (7, False)])
def test_prizepicks_size(n, ok):
    legs = [leg(f"p{i}", f"T{i}", f"e{i}") for i in range(n)]
    assert R.entry_status("prizepicks", legs)["ok"] is ok


@pytest.mark.parametrize("et,n,ok", [("standard", 1, False), ("standard", 8, True), ("standard", 9, False), ("flex", 2, False), ("flex", 3, True), ("flex", 8, True)])
def test_underdog_size(et, n, ok):
    legs = [leg(f"p{i}", f"T{i}", f"e{i}") for i in range(n)]
    assert R.entry_status("underdog", legs, et)["ok"] is ok


def test_same_player_prizepicks_refused_published():
    e = R.entry_status("prizepicks", [leg("a", "T1", "e1"), leg("a", "T1", "e2"), leg("b", "T2", "e3")])
    assert e["status"] == "refused" and e["problems"][0]["code"] == "entry" and " - " not in e["problems"][0]["text"]


def test_same_player_same_side_underdog_refused_published():
    assert R.entry_status("underdog", [leg("a", "T1", "e1"), leg("a", "T1", "e2"), leg("b", "T2", "e3")])["status"] == "refused"


def test_opposite_sides_underdog_are_not_published():
    e = R.entry_status("underdog", [leg("a", "T1", "e1", side="over"), leg("a", "T1", "e2", side="under"), leg("b", "T2", "e3")])
    assert e["status"] == "not_published" and "does not publish" in e["problems"][0]["text"] and any(r["rule"] == "both_sides" for r in e["rules"])


def test_published_refusal_outranks_not_published():
    e = R.entry_status("underdog", [leg("a", "T1", "e1", sport="cs2"), leg("b", "T1", "e2", sport="nba")])
    assert e["status"] == "refused" and {p["code"] for p in e["problems"]} == {"entry", "not_published"}


def test_mixed_sports_by_platform():
    legs = [leg("a", "T1", "e1"), leg("b", "T2", "e2", sport="nba")]
    pp, ud = R.entry_status("prizepicks", legs), R.entry_status("underdog", legs)
    assert pp["status"] == "valid" and pp["ok"] and pp["trackable"]
    assert any(r["rule"] == "mixed_sports" and r["status"] == "published" and r["source"] == R.PP_PICKS for r in pp["rules"])
    assert ud["status"] == "not_published" and not ud["ok"] and "CS2 and NBA" in ud["problems"][0]["text"]


def test_opposing_players_by_platform():
    legs = [leg("a", "T1", "e1"), leg("b", "T2", "e1")]
    assert R.entry_status("prizepicks", legs)["status"] == "not_published"
    assert R.entry_status("underdog", legs)["status"] == "valid"


def test_teammates_plus_another_team_is_allowed():
    legs = [leg("a", "T1", "e1"), leg("b", "T1", "e1"), leg("c", "T2", "e2")]
    assert R.entry_status("prizepicks", legs)["ok"] and R.entry_status("underdog", legs)["ok"]


def test_two_platforms_in_one_entry_refused():
    e = R.entry_status("underdog", [leg("a", "T1", "e1", book="underdog"), leg("b", "T2", "e2", book="prizepicks")])
    assert e["status"] == "refused" and "one platform" in e["problems"][0]["text"]


def test_unknown_platform_and_entry_type_are_not_published():
    assert R.entry_status("fanduel", [leg("a", "T1"), leg("b", "T2", "e2")])["status"] == "not_published"
    assert R.entry_status("underdog", [leg("a", "T1"), leg("b", "T2", "e2")], "rivals")["status"] == "not_published"


def test_missing_team_is_not_offered():
    assert R.entry_status("underdog", [leg("a", ""), leg("b", "T2", "e2")])["status"] == "not_published"


def test_every_rule_row_has_a_source_or_says_why():
    for k, v in R.TABLE.items():
        for p in ("prizepicks", "underdog"):
            r = v[p]
            assert r["read_at"] == R.READ_AT and r["status"] in ("published", "not_published", "scout") and r["text"]
            if r["status"] == "not_published":
                assert "Grader does not offer" in r["text"], k
            if r["status"] == "published":
                assert r["source"], (k, p)                  # "published" means a page says so, and the row names the page
            if r["status"] == "scout":
                assert r["source"] is None and "Grader" in r["text"], (k, p)


def test_rows_say_no_more_than_their_page():
    """The PrizePicks page: "unless Stacks are available", and "for the most part" on combinations. Grader's own reading is a note."""
    same = R.TABLE["same_player"]["prizepicks"]
    assert "unless Stacks are available" in same["text"] and "carry no Stacks" in same["note"]
    assert "for the most part" in R.TABLE["mixed_sports"]["prizepicks"]["text"].lower()
    assert R.TABLE["goblins_demons"]["underdog"]["status"] == "scout" and R.TABLE["two_platforms"]["prizepicks"]["status"] == "scout"
    gob = R.TABLE["goblins_demons"]["prizepicks"]                  # the PrizePicks fact stays; Grader's own builder rule is a note
    assert gob["text"] == "Allowed in any number" and "leaves goblins out unless you allow them" in gob["note"]


def test_payout_copy_equals_the_sports_table():
    """CS2 cannot import sports (isolation tests), so the file is read as text."""
    tree = ast.parse((ROOT / "sports" / "payouts.py").read_text(encoding="utf-8"))
    vals = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") in ("UD_STANDARD", "UD_FLEX", "UD_SOURCE")}
    assert vals["UD_STANDARD"] == R.UD_STANDARD and vals["UD_FLEX"] == R.UD_FLEX and vals["UD_SOURCE"] == R.UD_PAYOUTS


def test_flex_return_matches_the_equal_chance_formula():
    from math import comb
    for n in (3, 5, 6, 7, 8):
        p = 0.6
        want = sum(comb(n, k) * p ** (n - k) * (1 - p) ** k * m for k, m in R.UD_FLEX[n].items())
        assert abs(R.flex_return([p] * n) - want) < 1e-12


# ---- EV: one test per reason, in the published order
def ev_leg(**kw):
    i = kw.pop("i", 0)
    return dict({"name": f"p{i}", "sample": False, "sport": "cs2", "match": f"m{i}", "line": 30.5 + i, "multiplier": 1.0, "on_side": True, "p": 0.6, "tag": "T", "prop": "kills",
                 "scope": "12"}, **kw)


def good_gate(matches=320, printed=0.6, observed=0.59, ci=(0.56, 0.63), age_h=2.0):
    return {"at_ts": NOW - age_h * 3600, "cells": {"T|kills|12": {"matches": matches, "bands": [{"lo": 0.57, "hi": 0.60, "printed": 0.585, "observed": 0.58, "ci": [0.55, 0.61], "picks": 300},
                                                                                                 {"lo": 0.60, "hi": 0.65, "printed": printed, "observed": observed, "ci": list(ci), "picks": 300}]}}}


def legs2(**kw):
    return [ev_leg(i=0, **kw), ev_leg(i=1, **kw)]


def ev(legs=None, platform="underdog", et="standard", gate=None):
    return R.ev_status(platform, et, legs if legs is not None else legs2(), good_gate() if gate is None else gate, NOW)


def test_ev_available_with_the_published_formula():
    out = ev()
    assert out["available"] and out["hit_chance"] == 0.36 and out["ev"] == pytest.approx(0.36 * 3.5 - 1, abs=1e-4) and out["payout"]["multiple"] == 3.5
    assert out["payout"]["source"] == R.UD_PAYOUTS


def test_prizepicks_ev_on_the_owner_verified_table():
    """Owner sign-off 2026-11-05: PrizePicks' help-center table is verified - Power 2 pays 3x, Flex by losses."""
    out = ev(platform="prizepicks", et="power")
    assert out["available"] and out["hit_chance"] == 0.36 and out["ev"] == pytest.approx(0.36 * 3.0 - 1, abs=1e-4)
    assert out["payout"]["multiple"] == 3.0 and out["payout"]["platform"] == "prizepicks"
    assert out["payout"]["source"] == "https://www.prizepicks.com/help-center/payouts" and out["payout"]["page_date"] == "2026-10-19"
    legs = [ev_leg(i=i, multiplier=None) for i in range(3)]          # PrizePicks sends no per-pick multiplier on a standard line
    out = ev(legs, platform="prizepicks", et="flex")
    tab = {0: 3.0, 1: 1.0}
    assert out["available"] and out["ev"] == pytest.approx(R.flex_return([0.6] * 3, tab) - 1, abs=1e-4)
    out = ev([ev_leg(i=i) for i in range(6)], platform="prizepicks", et="power")
    assert out["available"] and out["payout"]["multiple"] == 37.5


def test_ev_flex_formula():
    legs = [ev_leg(i=i) for i in range(3)]
    out = ev(legs, et="flex")
    assert out["available"] and out["ev"] == pytest.approx(R.flex_return([0.6] * 3) - 1, abs=1e-4)


@pytest.mark.parametrize("kw,gate,text", [
    ({"platform": "prizepicks"}, None, "PrizePicks lists no standard payout for 2 picks"),     # PrizePicks entries: power / flex
    ({"platform": "prizepicks", "et": "flex"}, None, "PrizePicks lists no flex payout for 2 picks"),   # Flex 2: not in the app
    ({"platform": "prizepicks", "et": "power", "legs": [ev_leg(i=0, line_type="goblin"), ev_leg(i=1)]}, None,
     "Special-line payouts are set on the PrizePicks lineup screen"),
    ({"platform": "prizepicks", "et": "power", "legs": [ev_leg(i=0, multiplier=1.5), ev_leg(i=1)]}, None,
     "PrizePicks does not publish the payout of a modified pick"),
    ({"legs": [ev_leg(i=i) for i in range(4)], "et": "flex"}, None, "two 4-pick flex payouts"),
    ({"legs": [ev_leg(i=0, multiplier=1.05), ev_leg(i=1)]}, None, "does not publish how multipliers combine"),
    ({"legs": [ev_leg(i=0, match="same"), ev_leg(i=1, match="same")]}, None, "no tested joint model"),
    ({"legs": [ev_leg(i=0, line=30.0), ev_leg(i=1)]}, None, "changes the payout tier"),
    ({"legs": [ev_leg(i=0, on_side=False), ev_leg(i=1)]}, None, "No saved chance for this side"),
    ({"legs": [ev_leg(i=0, p=0.56), ev_leg(i=1)]}, None, "Results cover 57% and up"),
    ({}, good_gate(matches=120), "This model version has 120 of 300 settled matches"),
    ({}, good_gate(observed=0.55, ci=(0.52, 0.58)), "Printed 60.0%, won 55.0%"),
    ({}, good_gate(printed=0.64, observed=0.60, ci=(0.58, 0.66)), "a gap of 4.0 points"),
    ({}, good_gate(age_h=30), "The results export is 30 hours old"),
])
def test_ev_reason_row(kw, gate, text):
    out = ev(gate=gate, **kw)
    assert out["available"] is False and text in out["reason"]


@pytest.mark.parametrize("gate", [lambda: None, lambda: {"at_ts": None, "cells": {}}])
def test_a_read_that_failed_is_never_reported_as_a_count(gate):
    out = R.ev_status("underdog", "standard", legs2(), gate, NOW)
    assert out["available"] is False and out["reason"] == "The results export is not available." and out["payout"]["multiple"] == 3.5


def test_no_reason_carries_an_internal_version_name():
    assert "tag" not in R.ev_status("underdog", "standard", legs2(tag="SECRET"), lambda: {"at_ts": NOW, "cells": {}}, NOW)["reason"]
    assert "SECRET" not in R.ev_status("underdog", "standard", legs2(tag="SECRET"), lambda: {"at_ts": NOW, "cells": {}}, NOW)["reason"]


def test_ev_reasons_come_in_order():
    legs = [ev_leg(i=0, match="same", multiplier=1.05, on_side=False), ev_leg(i=1, match="same")]
    assert "multipliers" in ev(legs)["reason"]
    assert "joint model" in ev([dict(l, multiplier=1.0) for l in legs])["reason"]


def test_ev_gate_is_not_read_until_the_step_that_needs_it():
    calls = []
    R.ev_status("underdog", "standard", [ev_leg(i=0, p=0.5), ev_leg(i=1)], lambda: calls.append(1), NOW)
    assert calls == []


def test_payout_shows_once_the_table_applies():
    out = ev([ev_leg(i=0, line=30.0), ev_leg(i=1)])
    assert out["payout"]["multiple"] == 3.5


def test_gate_skips_the_resampling_for_a_cell_that_cannot_pass():
    rows = [{"version": "T", "p": 0.6, "over": 1, "match": f"m{i}", "prop": "kills", "scope": "12"} for i in range(40)]
    assert R.gate_from_export({"rows": rows}, {"T"})["cells"]["T|kills|12"]["bands"][0]["ci"] is None


def test_gate_counts_the_exact_tag_only():
    rows = [{"version": "T", "p": 0.6, "over": i % 2, "match": f"m{i}", "prop": "kills", "scope": "12"} for i in range(40)]
    rows += [{"version": "OLD", "p": 0.6, "over": 1, "match": f"o{i}", "prop": "kills", "scope": "12"} for i in range(40)]
    g = R.gate_from_export({"at": "x", "rows": rows}, {"T"}, NOW, B=50, min_matches=1)
    assert set(g["cells"]) == {"T|kills|12"} and g["cells"]["T|kills|12"]["matches"] == 40
    b = g["cells"]["T|kills|12"]["bands"][0]
    assert b["picks"] == 40 and b["observed"] == 0.5 and b["ci"] is not None


# ---- SCOUT_SLIP_HEADER: gradecore/slip.py on an available EV only
from gradecore import slip as GS    # noqa: E402  (the test may import gradecore; workspace_rules never does)


@pytest.mark.parametrize("value,on", [(None, False), ("", False), ("0", False), ("off", False), ("1", True), ("on", True)])
def test_slip_header_flag_reads_default_off(value, on):
    assert R.slip_header_on({} if value is None else {"SCOUT_SLIP_HEADER": value}) is on


def test_workspace_rules_still_imports_no_gradecore():
    tree = ast.parse((ROOT / "workspace_rules.py").read_text(encoding="utf-8"))
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any(x.startswith("gradecore") for x in names)


def test_leg_ev_is_push_adjusted_and_a_push_is_never_a_win():
    assert R.leg_ev(0.6, 2.0) == pytest.approx(0.2)
    assert R.leg_ev(0.5, 2.0, p_push=0.1) == pytest.approx(0.1)     # the push returns the stake only


def test_slip_header_standard_matches_ev_and_gives_leg_ev_without_a_second_stake():
    out = ev()
    h = R.slip_header(out, "standard", legs2(), GS.evaluate)
    assert h["payout_multiple"] == 3.5 and h["p_all_win"] == 0.36 and h["pushes"] == "excluded"
    assert h["expected_return"] == pytest.approx(out["ev"], abs=1e-4) == pytest.approx(0.26, abs=1e-4)
    assert "quarter_kelly" not in h and not any("kelly" in k for k in h)   # review 2026-11-04: one stake per line
    assert h["leg_price"] == pytest.approx(3.5 ** 0.5, abs=1e-4)
    assert [x["ev"] for x in h["legs"]] == [round(0.6 * 3.5 ** 0.5 - 1, 4)] * 2 and [x["line"] for x in h["legs"]] == [30.5, 31.5]
    assert "independent" in h["basis"]


def test_slip_header_flex_has_no_per_leg_ev():
    legs = [ev_leg(i=i) for i in range(3)]
    out = ev(legs, et="flex")
    h = R.slip_header(out, "flex", legs, GS.evaluate)
    assert h["expected_return"] == pytest.approx(R.flex_return([0.6] * 3) - 1, abs=1e-4) and h["p_all_win"] == pytest.approx(0.216)
    assert "legs" not in h and "leg_price" not in h


@pytest.mark.parametrize("kw,gate", [
    ({"platform": "prizepicks"}, None),                          # payout unknown
    ({"legs": [ev_leg(i=0, line=30.0), ev_leg(i=1)]}, None),     # a push is possible
    ({}, good_gate(matches=120)),                                # chances not validated
    ({"legs": [ev_leg(i=0, match="same"), ev_leg(i=1, match="same")]}, None),
])
def test_slip_header_absent_unless_payout_verified_and_chances_validated(kw, gate):
    legs = kw.get("legs", legs2())
    out = ev(gate=gate, **kw)
    assert out["available"] is False
    calls = []
    assert R.slip_header(out, kw.get("et", "standard"), legs, lambda *a: calls.append(a)) is None and calls == []


def test_slip_header_refuses_a_second_number_and_an_unpriced_push():
    out = ev()
    assert R.slip_header(out, "standard", legs2(), lambda legs, pay: dict(GS.evaluate(legs, pay), net_ev=0.5)) is None
    assert R.slip_header(dict(out, payout=dict(out["payout"], multiple=3.0)), "standard", legs2(), GS.evaluate) is None
    assert R.header_payout("standard", 2)(("win", "push")) is None and R.header_payout("flex", 4) is None
    assert R.header_payout("standard", 2)(("win", "loss")) == 0.0



# ---- review 2026-11-04: the board's Edge column never shows an edge the slip's EV check refuses
@pytest.mark.parametrize("leg_kw,gate", [
    ({}, None), ({"p": 0.61}, None), ({"line": 30.0}, None), ({"on_side": False}, None), ({"p": 0.56}, None),
    ({"sport": "lol"}, None), ({"sport": "nba"}, None), ({"sport": "nfl"}, None), ({"multiplier": 1.05}, None),
    ({"tag": "OTHER"}, None), ({"prop": "headshots"}, None), ({"p": 0.66}, None),
    ({}, good_gate(matches=120)), ({"p": 0.61}, good_gate(observed=0.55, ci=(0.52, 0.58))),
    ({"p": 0.61}, good_gate(printed=0.64, observed=0.60, ci=(0.58, 0.66))), ({}, good_gate(age_h=30)),
    ({}, {"at_ts": None, "cells": {}}),
])
def test_board_edge_shows_exactly_where_the_slip_ev_check_passes(leg_kw, gate):
    g = good_gate() if gate is None else gate
    one = ev_leg(i=0, **leg_kw)
    slip = R.ev_status("underdog", "standard", [one, ev_leg(i=1, p=0.585)], g, NOW)
    assert R.leg_edge_ok(one, R.edge_cells(g, NOW)) is bool(slip["available"]), (leg_kw, slip)


def test_edge_cells_are_empty_without_a_fresh_export():
    assert R.edge_cells(None, NOW) == {} and R.edge_cells(lambda: None, NOW) == {}
    assert R.edge_cells(good_gate(age_h=30), NOW) == {}
    assert R.edge_cells(good_gate(), NOW) == {"T|kills|12": [[0.57, 0.6], [0.6, 0.65]]}
