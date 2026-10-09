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


def test_underdog_name_alias_and_same_side_internal():
    res = _res()
    rows = [_row(player="cairne X")]                                                 # Underdog writes the name differently
    assert cmp.match_offers(res, rows, NOW)[0] == []
    offers, _ = cmp.match_offers(res, rows, NOW, aliases=("cairne X",))             # the name the line was requested under
    assert len(offers) == 1
    p = cmp.panel(res, rows, board_at=NOW.timestamp(), now=NOW, aliases=("cairne X",))
    for it in p["internal"]:
        side = p["ours"]["side"]
        assert it["p_side"] == (it["p_over"] if side == "Over" else it["p_under"])   # same side as the pick
    assert p["concern"] == (p["evidence_against"][0] if p["evidence_against"] else None)
    assert p["one_worse"] is not None and p["one_worse"]["chance"] < p["offers"][0]["model_chance"]   # 1 worse line = lower chance


def test_line_check_before_slip():
    board = {"at": NOW.timestamp() - 60, "rows": [_row(), _row(id="z", line=31.5, higher=1.2, lower=0.8, alt=True), _row(id="m1", scope="1", line=15.5)]}
    ok = cmp.line_check(board, "cairne", "kills", "12", "30.5", "fnatic", START, now=NOW)
    assert ok["same_line"] and not ok["changed"] and [r["line"] for r in ok["rows"]] == [30.5, 31.5]
    moved = cmp.line_check(board, "cairne", "kills", "12", "29.5", "fnatic", START, now=NOW)
    assert moved["changed"] and "30.5" in moved["note"]
    gone = cmp.line_check(board, "cairne", "hs", "12", "14.5", None, None, now=NOW)
    assert gone["changed"] and gone["rows"] == [] and "no line" in gone["note"]
    other = cmp.line_check(board, "cairne", "kills", "12", "30.5", "fnatic Academy", START, now=NOW)
    assert other["changed"] and other["rows"] == []                                 # academy team is not this player
    assert cmp.line_check(dict(board, at=NOW.timestamp() - 3600), "cairne", "kills", "12", "30.5", now=NOW)["stale"]
