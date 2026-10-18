"""bo3-env-2 (2026-10-18 finish-fixes item 1): a BO3.gg map whose environment is unknown stays unknown for perf-2-bo3 - it
counts in the overall history and in neither the LAN-only nor the online-only history (the old rule made it online).
Every player, match and number here is a TEST FIXTURE."""
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

import cs2_scout
import perf
import perf_shadow

DAY = date(2026, 10, 18).toordinal()


@pytest.mark.parametrize("label,match_label,want", [
    ("lan", None, True), ("online", None, False), ("LAN", None, True), (" Online ", None, False),
    (None, None, None), ("", None, None), ("", "", None),
    ("hybrid", None, None),                              # an unrecognised label never becomes online
    (None, "lan", True), (None, "online", False),       # the same historical match's verified label
    ("hybrid", "lan", True), ("", "online", False),
    (True, None, True), (False, None, False), (None, "?", None)])
def test_hist_env(label, match_label, want):
    assert cs2_scout.hist_env(label, match_label) is want


def _pb(envs, match_env=None):
    rows = []
    for i, e in enumerate(envs):
        d = date.fromordinal(DAY - 3 - i * 4).isoformat()
        r = {"date": d, "kills": 15 + (i * 7) % 9, "rounds": 21 + i % 5, "headshots": 7, "match_id": 100 + i}
        if e != "absent":
            r["env"] = e
        rows.append(r)
    return {"recent_maps": rows, "_match_env": match_env or {}}


def test_rows_keep_unknown_as_none_and_use_the_verified_match_label():
    pb = _pb(["lan", "online", "absent", "weird", "absent"], match_env={104: "online"})
    assert [r[4] for r in cs2_scout.bo3_perf_rows(pb)] == [True, False, None, None, False]
    assert [r[4] for r in cs2_scout.bo3_perf_rows(pb, "bo3-env-1")] == [True, False, False, False, False]   # the old rule
    assert perf_shadow._bo3_rows(pb) == cs2_scout.bo3_perf_rows(pb)                       # the shadow record uses the same rows


def test_perf_project_keeps_unknown_overall_and_out_of_both_subsets():
    rows = cs2_scout.bo3_perf_rows(_pb(["lan", "online", "absent", "lan", "absent", "online"]))
    known = [r for r in rows if r[4] is not None]
    for lan in (True, False):
        a = perf.project(rows, [], lan, DAY, "12", "kills")
        b = perf.project(known, [], lan, DAY, "12", "kills")
        assert a["history_rounds_weighted"] > b["history_rounds_weighted"]          # overall history includes unknown maps
        assert a["env_rounds_weighted"] == b["env_rounds_weighted"]                  # neither subset does
    assert perf.project(rows, [], None, DAY, "12", "kills")["env_rounds_weighted"] == 0.0


def test_known_labels_are_identical_and_the_changed_cohort_is_online_tonight_with_unknown_maps():
    known = _pb(["lan", "online", "online", "lan", "online"])
    assert cs2_scout.bo3_perf_rows(known) == cs2_scout.bo3_perf_rows(known, "bo3-env-1")       # known inputs: identical
    mixed = _pb(["lan", "absent", "online", "absent", "online", "lan"])
    new, old = cs2_scout.bo3_perf_rows(mixed), cs2_scout.bo3_perf_rows(mixed, "bo3-env-1")
    p = lambda rows, lan: perf.project(rows, [], lan, DAY, "12", "kills")
    assert p(new, False)["lambda"] != p(old, False)["lambda"]                       # online tonight: the online history changes
    assert p(new, False)["env_rounds_weighted"] < p(old, False)["env_rounds_weighted"]
    assert p(new, False)["rate_all_envs"] == p(old, False)["rate_all_envs"]          # the overall rate does not
    # LAN tonight: old (False) and new (None) are both outside the LAN subset on these inputs - measured, not assumed
    assert p(new, True)["lambda"] == p(old, True)["lambda"]


def test_live_perf_input_saves_the_rule_labels_and_the_before_projection(monkeypatch):
    import perf_live
    monkeypatch.setattr(perf_live, "projection", lambda *a, **k: {"error": "player 'tester' not found in HLTV"})
    monkeypatch.setattr(perf_live, "opp_maps", lambda opp, since: (None, []))
    monkeypatch.setattr(perf_live, "g7_fields", lambda pf: {})
    pb = dict(_pb(["lan", "absent", "online", "absent", "online"]), player={"nick": "tester"}, _env={"tonight": "online"})
    args = SimpleNamespace(scope="12", prop="kills", match_start=date.fromordinal(DAY).isoformat() + "T12:00:00Z", opponent="Opp")
    out = cs2_scout._perf_input_once(pb, args, "12")
    assert out["version"] == "perf-2-bo3" and out["env_rule"] == "bo3-env-2"
    assert out["env_labels"] == {"lan": 1, "online": 2, "unknown": 2}
    assert out["env_rule_before"]["rule"] == "bo3-env-1" and out["env_rule_before"]["lambda"] != out["lambda"]
    pb_known = dict(_pb(["lan", "online", "online"]), player={"nick": "tester"}, _env={"tonight": "online"})
    out2 = cs2_scout._perf_input_once(pb_known, args, "12")
    assert out2["env_labels"]["unknown"] == 0 and "env_rule_before" not in out2
