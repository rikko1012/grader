"""Projection-comparison layer (comparison.py): exact joins, chances at every offered line from the grade's own
distribution, stale / duplicate / payout handling, honest source labels, snapshots on change only, grade untouched."""
import copy, json, os, random, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comparison as cmp
import grader
from tests.test_perf import _inputs

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
START = "2026-10-09T15:00:00Z"


def _res(prop="kills", scope="12", line=30.5, seed=1):
    d = _inputs(random.Random(seed), scope); d["prop"] = prop; d["line"] = line
    g = grader.grade_leg(dict(d))
    return {"grade": g, "grader_inputs": d, "verdict": {"side": "More" if g["p_more"] >= 0.5 else "Less", "p": max(g["p_more"], 1 - g["p_more"])},
            "player_block": {"player": {"nick": "cairne"}, "playing_for": "fnatic", "recent_form": {"decided": 10, "overs": 3}},
            "next_match": {"start": "2026-10-09T15:00:00.000+00:00", "opponent": "K27", "slug": "fnatic-vs-k27"}}


def _row(**kw):
    r = {"id": "u1", "player": "cairne", "team": "Fnatic", "opponent": "K27", "start": START, "prop": "kills", "scope": "12",
         "line": 30.5, "higher": 1.0, "lower": 1.0, "alt": False, "price_higher": 1.9, "price_lower": 1.9,
         "updated_at": (NOW - timedelta(minutes=2)).isoformat()}
    r.update(kw)
    return r


def test_exact_join_rejects_wrong_markets():
    res = _res()
    rows = [_row(),
            _row(id="u2", scope="1", line=15.5),                                    # Map 1 vs Maps 1-2
            _row(id="u3", team="fnatic Academy"),                                   # namesake / academy team
            _row(id="u4", start="2026-10-10T15:00:00Z"),                            # another match
            _row(id="u5", opponent="BIG"),                                          # another opponent
            _row(id="u6", start=None),                                              # time unknown
            _row(id="u7", prop="hs", line=14.5),                                    # another stat: not this market at all
            _row(id="u8", player="cairneX")]                                        # another player
    offers, rejected = cmp.match_offers(res, rows, NOW)
    assert [o["id"] for o in offers] == ["u1"] and offers[0]["team_verified"]
    reasons = " | ".join(r for _, r in rejected)
    for word in ("different maps", "different team", "different match time", "different opponent", "match time unknown"):
        assert word in reasons
    assert len(rejected) == 5


def test_chances_at_lines_match_the_grade_for_every_distribution():
    for prop, scope, line, seed in (("kills", "12", 30.5, 2), ("kills", "1", 15.0, 3), ("kills", "123", 44.0, 4)):
        res = _res(prop, scope, line, seed)
        po, pu, pp = cmp.probs_at(res["grade"]["dist"], line)
        r = res["grade"]["probs_raw"]
        assert abs(po - r["over"]) < 2e-3 and abs(pu - r["under"]) < 2e-3 and abs(pp - r["push"]) < 2e-3
    d = _inputs(random.Random(5), "12"); d["prop"] = "hs"; d["line"] = 15.5
    d["perf"] = dict(d["perf"], lambda_kills=30.0, hs_per_kill=0.5, **{"lambda": 15.0})
    g = grader.grade_leg(dict(d)); assert g["dist"]["kind"] == "hs_bb"
    po, pu, pp = cmp.probs_at(g["dist"], 15.5)
    assert abs(po - g["probs_raw"]["over"]) < 2e-3 and pp == 0.0


def test_panel_lines_payouts_stale_duplicates_and_honest_labels():
    res = _res(line=30.5)
    before = copy.deepcopy(res["grade"])
    rows = [_row(), _row(),                                                         # duplicate listing counts once
            _row(id="a1", line=28.5, higher=0.8, lower=1.2, alt=True),             # alternate with different payouts
            _row(id="a2", line=32.5, higher=1.3, lower=0.75, alt=True,
                 updated_at=(NOW - timedelta(hours=6)).isoformat())]              # unchanged for hours: still current
    p = cmp.panel(res, rows, board_at=NOW.timestamp() - 60, now=NOW)
    assert res["grade"] == before                                                   # context only: grade untouched
    assert [o["line"] for o in p["offers"]] == [28.5, 30.5, 32.5]
    assert [o["stale"] for o in p["offers"]] == [False, False, False]
    for o in p["offers"]:
        assert abs(o["p_over"] + o["p_under"] + o["p_push"] - 1) < 1e-6
    lo, mid = p["offers"][0], p["offers"][1]
    assert lo["over_if_decided"] > mid["over_if_decided"]                           # lower line: higher Over chance
    assert not any("payout_adj" in k for o in p["offers"] for k in o)               # no value claim (entry payouts unverified)
    assert p["best_line"] == {} and p["payouts_differ"] == {"over": True, "under": True}   # different payouts: listed, not ranked
    eq = cmp.panel(res, [_row(), _row(id="b1", line=29.5)], board_at=NOW.timestamp() - 60, now=NOW)
    assert eq["best_line"]["over"][0]["line"] == 29.5 and eq["best_line"]["under"][0]["line"] == 30.5   # equal payouts
    old = cmp.panel(res, rows, board_at=NOW.timestamp() - 40 * 60, now=NOW)       # our board copy is 40 min old
    assert all(o["stale"] for o in old["offers"]) and old["stale_board"] and old["best_line"] == {}
    assert p["independent"]["count"] == 0 and p["independent"]["consensus"] is None and "No independent projection" in p["independent"]["note"]
    assert any("internal" in it["label"] for it in p["internal"]) or p["internal"] == []
    assert any("Last 10" in a for a in p["evidence_against"]) == (res["verdict"]["side"] == "More")


def test_best_line_needs_a_choice():
    p = cmp.panel(_res(), [_row()], board_at=NOW.timestamp(), now=NOW)
    assert p["best_line"] == {} and len(p["offers"]) == 1


def test_no_grade_no_panel_and_board_failure_is_harmless():
    assert cmp.panel({"grade": {}}, []) is None
    p = cmp.panel(_res(), None, now=NOW)                                           # book feed down: panel still renders
    assert p["offers"] == [] and p["ours"]["projection"]


def test_snapshots_append_only_on_change(tmp_path, monkeypatch):
    monkeypatch.setattr(cmp, "snapshot_path", lambda: tmp_path / "s.jsonl"); monkeypatch.setattr(cmp, "_LAST", {})
    res = _res()
    p1 = cmp.panel(res, [_row()], now=NOW)
    assert cmp.snapshot(res, p1, "TAG") is True
    assert cmp.snapshot(res, cmp.panel(res, [_row()], now=NOW + timedelta(minutes=1)), "TAG") is False   # nothing changed
    assert cmp.snapshot(res, cmp.panel(res, [_row(id="u9", line=31.5)], now=NOW), "TAG") is True      # line moved: new record
    lines = [json.loads(x) for x in (tmp_path / "s.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["offers"][0]["line"] for r in lines] == [30.5, 31.5] and all(r["model_version"] == "TAG" for r in lines)


def test_identity_ids_strict_and_ambiguity():
    res = _res(); res["player_block"]["player"]["id"] = 77
    rows = [_row(player="cairne X", ud_player_id="u-cairne")]                       # Underdog writes the name differently
    assert cmp.match_offers(res, rows, NOW)[0] == []                                 # no id map: exact name only
    offers, _ = cmp.match_offers(res, rows, NOW, id_map={"u-cairne": 77})          # stable id learnt from board grades
    assert len(offers) == 1 and offers[0]["identity"] == "id"
    offers, rej = cmp.match_offers(res, [_row(ud_player_id="u-other")], NOW, id_map={"u-other": 99})   # same name, other player's id
    assert offers == [] and "different player" in rej[0][1]
    offers, _ = cmp.match_offers(res, [_row(player="cairne X", ud_player_id="u-cairne", team="fnatic Academy")], NOW, id_map={"u-cairne": 77})
    assert offers == []                                                             # id never bypasses the team check
    two = [_row(ud_player_id="a"), _row(id="u2", ud_player_id="b", line=31.5)]     # two Underdog players fit by name
    offers, rej = cmp.match_offers(res, two, NOW)
    assert offers == [] and all("ambiguous" in r for _, r in rej)


def test_evidence_is_grounded_and_same_side_internal():
    res = _res()
    p = cmp.panel(res, [_row()], board_at=NOW.timestamp(), now=NOW)
    for it in p["internal"]:
        side = p["ours"]["side"]
        assert it["p_side"] == (it["p_over"] if side == "Over" else it["p_under"])
    for x in p["supports"] + p["evidence_against"]:
        assert x["basis"] in ("model", "stake", "context", "data") and x["text"]
    perf_on = str(res["grade"].get("projection_source", "")).startswith("perf")
    l10 = [x for x in p["supports"] + p["evidence_against"] if x["text"].startswith("Last ")]
    assert all(x["basis"] == ("context" if perf_on else "model") or x["basis"] == "stake" for x in l10)   # not in a perf projection
    assert all(x["basis"] == "context" for x in p["supports"] + p["evidence_against"] if "previous model" in x["text"])
    if p["concern"] and any(x["basis"] in ("model", "data") for x in p["evidence_against"]):
        assert p["concern"]["basis"] in ("model", "data")                            # the headline concern is from the calculation
    assert p["one_worse"] is not None and p["one_worse"]["chance"] < p["offers"][0]["model_chance"]


def test_line_check_states():
    board = {"at": NOW.timestamp() - 60, "rows": [_row(ud_player_id="u1"), _row(id="z", line=31.5, higher=1.2, lower=0.8, alt=True, ud_player_id="u1"),
                                                  _row(id="m1", scope="1", line=15.5, ud_player_id="u1")]}
    assert cmp.line_check(board, "cairne", "kills", "12", "30.5", "fnatic", START, now=NOW)["status"] == "verified"
    moved = cmp.line_check(board, "cairne", "kills", "12", "29.5", "fnatic", START, now=NOW)
    assert moved["status"] == "changed" and moved["new_line"] == 30.5 and "30.5" in moved["note"]
    assert cmp.line_check(board, "cairne", "hs", "12", "14.5", now=NOW)["status"] == "unavailable"
    assert cmp.line_check(board, "cairne", "kills", "12", "30.5", "fnatic Academy", START, now=NOW)["status"] == "unavailable"
    assert cmp.line_check(None, "", "", "", failed=True)["status"] == "failed"
    assert cmp.line_check({"at": 0, "rows": [], "error": "HTTP 503"}, "cairne", "kills", "12", "30.5", now=NOW)["status"] == "failed"
    # stable id: the Underdog row belongs to player 77 -> a namesake with id 12 doesn't get it
    assert cmp.line_check(board, "cairne", "kills", "12", "30.5", "fnatic", START, now=NOW, pid="12", id_map={"u1": 77})["status"] == "unavailable"
    assert cmp.line_check(board, "cairne", "kills", "12", "30.5", "fnatic", START, now=NOW, pid="77", id_map={"u1": 77})["status"] == "verified"
    amb = {"at": NOW.timestamp(), "rows": [_row(ud_player_id="a"), _row(id="q", ud_player_id="b")]}
    assert cmp.line_check(amb, "cairne", "kills", "12", "30.5", now=NOW)["status"] == "ambiguous"
    assert cmp.line_check(dict(board, at=NOW.timestamp() - 3600), "cairne", "kills", "12", "30.5", now=NOW)["stale"]


def test_verified_team_aliases_are_exact_pairs():
    assert cmp._team_same("Ground Zero.AU", "Ground Zero") is True
    assert cmp._team_same("Desi Boyz", "Desi Boyz v6") is True
    assert cmp._team_same("Ground Zero.EU", "Ground Zero") is False                  # no global suffix stripping
    assert cmp._team_same("Desi Boyz v5", "Desi Boyz v6") is False
    assert cmp._team_same("KZG", "Abyssal") is False and cmp._team_same("Team Abyssal", "KZG") is False
    assert all(len(e) == 3 and e[2] for e in cmp.VERIFIED_TEAM_ALIASES)              # every alias carries its evidence


def test_source_is_not_quality():
    g = {"projection_source": "perf-2-bo3", "flags": ["PROVISIONAL: performance model on BO3.gg history"]}
    assert cmp.source_label(g) == "BO3.gg" and cmp.data_status(g, {})[0] == "ok"          # BO3.gg alone is not a warning
    assert cmp.source_label({"projection_source": "perf-2"}) == "HLTV"
    assert cmp.source_label({"projection_source": "legacy (performance model unavailable: x)"}).startswith("older model")
    assert cmp.data_status({"flags": ["OLD PROJECTION: ..."]}, {})[0] == "ok"
    assert cmp.data_status({"flags": ["Gate C: roster not confirmed"]}, {}) == ["verify", "roster not confirmed"]
    assert cmp.data_status({"flags": ["RUST: last official map 40 days ago - SD inflated"]}, {})[0] == "verify"
    assert cmp.data_status({"flags": ["Note: last official map 12 days ago"]}, {})[0] == "ok"
    assert cmp.data_status({"flags": []}, {"state": "SCOUT"})[0] == "verify"
    assert cmp.data_status({"flags": ["Projection ... data: thin history)"]}, {})[0] == "limited"
