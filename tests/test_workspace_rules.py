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
    legs = [leg("a", "T1", "e1"), leg("b", "T2", "e2", sport="nba", sample=True)]
    pp, ud = R.entry_status("prizepicks", legs), R.entry_status("underdog", legs)
    assert pp["status"] == "valid" and pp["ok"] and not pp["trackable"] and pp["track_note"] == "NBA legs are sample data."
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
            assert r["read_at"] == R.READ_AT and r["status"] in ("published", "not_published") and r["text"]
            if r["status"] == "not_published":
                assert "Scout does not offer" in r["text"], k


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


def test_ev_flex_formula():
    legs = [ev_leg(i=i) for i in range(3)]
    out = ev(legs, et="flex")
    assert out["available"] and out["ev"] == pytest.approx(R.flex_return([0.6] * 3) - 1, abs=1e-4)


@pytest.mark.parametrize("kw,gate,text", [
    ({"platform": "prizepicks"}, None, "PrizePicks shows lineup payouts only in its app"),
    ({"legs": [ev_leg(i=0, sample=True, name="Delvane"), ev_leg(i=1)]}, None, "Delvane is sample data"),
    ({"legs": [ev_leg(i=i) for i in range(4)], "et": "flex"}, None, "two 4-pick flex payouts"),
    ({"legs": [ev_leg(i=0, multiplier=1.05), ev_leg(i=1)]}, None, "does not publish how multipliers combine"),
    ({"legs": [ev_leg(i=0, match="same"), ev_leg(i=1, match="same")]}, None, "no tested joint model"),
    ({"legs": [ev_leg(i=0, line=30.0), ev_leg(i=1)]}, None, "changes the payout tier"),
    ({"legs": [ev_leg(i=0, on_side=False), ev_leg(i=1)]}, None, "No saved chance for this side"),
    ({"legs": [ev_leg(i=0, p=0.56), ev_leg(i=1)]}, None, "Results cover 57% and up"),
    ({}, good_gate(matches=120), "Version T has 120 of 300 settled matches"),
    ({}, good_gate(observed=0.55, ci=(0.52, 0.58)), "Printed 60.0%, won 55.0%"),
    ({}, good_gate(printed=0.64, observed=0.60, ci=(0.58, 0.66)), "a gap of 4.0 points"),
    ({}, good_gate(age_h=30), "The results export is 30 hours old"),
])
def test_ev_reason_row(kw, gate, text):
    out = ev(gate=gate, **kw)
    assert out["available"] is False and text in out["reason"]


def test_ev_reasons_come_in_order():
    legs = [ev_leg(i=0, match="same", multiplier=1.05, sample=True, name="x"), ev_leg(i=1, match="same")]
    assert "sample data" in ev(legs)["reason"]
    assert "multipliers" in ev([dict(l, sample=False) for l in legs])["reason"]


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
