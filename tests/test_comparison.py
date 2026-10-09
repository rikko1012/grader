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
    assert lo["over_if_decided"] > mid["over_if_decided"]                           # lower line: better Over at equal payout
    assert abs(lo["payout_adj_over"] - lo["over_if_decided"] * 0.8) < 1e-3          # payout counted, not just the gap
    old = cmp.panel(res, rows, board_at=NOW.timestamp() - 40 * 60, now=NOW)       # our board copy is 40 min old
    assert all(o["stale"] for o in old["offers"]) and old["stale_board"] and old["best_line"] == {}   # stale never "best"
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
