"""Shadow slip builder (slip_batches batch-3): eligibility, joint selection, settlement rules, durable results."""
import json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import slip_batches as sb

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _row(i, match, **kw):
    r = {"id": f"l{i}", "nick": f"p{i}", "player": f"p{i}", "match_slug": match, "start": (NOW + timedelta(minutes=90)).isoformat(),
         "prop": "kills", "scope": "12", "line": 30.5, "graded_line": 30.5, "graded_model": "M", "side": "More", "p": 0.62,
         "grade": "B+", "units": 1.0, "state": "OK", "graded_at": (NOW - timedelta(minutes=10)).isoformat(), "awp": False,
         "awp_conf": "confirmed", "igl": False, "higher": 1.0, "lower": 1.0}
    r.update(kw)
    return r


def test_eligibility_rules(monkeypatch):
    monkeypatch.setattr(sb, "_batched", lambda: {"done-match"})
    rows = [_row(1, "m1"), _row(2, "m2", line=31.5),                       # line moved since the grade
            _row(3, "m3", graded_model="OLD"), _row(4, "m4", higher=None),  # older model / side not offered
            _row(5, "m5", prop="hs", awp=True), _row(6, "m6", igl=None),    # AWPer HS / role unknown
            _row(7, "m7", grade="B-"), _row(8, "done-match"),               # below B / match already batched
            _row(9, "m9", prop="hs", awp_conf="inferred"), _row(10, "m10", side="Less", lower=0.9)]
    ok, why = sb.eligible(rows, NOW, "M")
    assert sorted(r["id"] for r in ok) == ["l1", "l10"]
    assert why == {"line changed since the grade": 1, "grade from an older model": 1, "side not offered / payout unknown": 1,
                   "AWPer HS / IGL kills": 1, "role not confirmed": 1, "below B / no units": 1,
                   "match already in an earlier batch": 1, "AWP role only inferred (HS% / list)": 1}
    ok, why = sb.eligible(rows, NOW, "M", dict(sb.CONTROLS, sides=("More",)))
    assert [r["id"] for r in ok] == ["l1"] and why["side not selected"] == 1


def test_joint_selection_never_repeats_match_or_player():
    rows = [_row(i, f"m{i // 2}", p=0.55 + 0.01 * i) for i in range(16)]
    rows += [_row(100, "m0", nick="p1", player="p1", p=0.9)]               # same player as l1, other line
    div, conc = sb.select(rows)
    ms, pl = [], []
    for n, p, legs, _ in div:
        ms += [l["match_slug"] for l in legs]; pl += [l["nick"] for l in legs]
        assert len(legs) == n
    assert len(ms) == len(set(ms)) and len(pl) == len(set(pl)) and div
    sizes = [n for n, *_ in div]
    assert all(sizes.count(n) <= sb.PER_SIZE for n in sb.PAY)


def test_settlement_push_void_refund_and_payout():
    legs = [{"p": 0.6, "mult": 1.0}, {"p": 0.5, "mult": 0.9}, {"p": 0.7, "mult": 1.0}]
    s = {"legs": legs}
    assert sb.settle_slip(s, ["hit", "hit", None]) is None                  # waits for every leg
    r = sb.settle_slip(s, ["hit", "push", "hit"])                         # push drops out: pays as a 2-pick
    assert r["status"] == "won" and r["legs_live"] == 2 and r["return_std"] == 3.0 and abs(r["return_mult"] - 3.0) < 1e-9
    r = sb.settle_slip(s, ["hit", "hit", "hit"])
    assert r["return_std"] == 6.0 and abs(r["return_mult"] - 5.4) < 1e-9 and abs(r["expected"] - 0.21) < 1e-9
    r = sb.settle_slip(s, ["void", "push", "hit"])                         # one live leg: refunded
    assert r["status"] == "refunded" and r["return_std"] == 1.0
    r = sb.settle_slip(s, ["miss", "hit", "void"])
    assert r["status"] == "lost" and r["return_std"] == 0.0
    r = sb.settle_slip({"legs": [{"p": 0.6, "mult": None}, {"p": 0.6, "mult": 1.0}]}, ["hit", "hit"])
    assert r["return_std"] == 3.0 and r["return_mult"] is None               # unknown multiplier stays unknown


def test_leg_results_need_exact_match_and_use_leg_line():
    picks = [{"player": "p1", "prop": "kills", "scope": "12", "match_slug": "m1", "status": "settled", "actual": 31},
             {"player": "p2", "prop": "kills", "scope": "12", "match_slug": "m2", "status": "void", "actual": None},
             {"player": "p3", "prop": "kills", "scope": "1", "match_slug": "m3", "status": "settled", "actual": 16}]
    f = sb.leg_result_from_picks(picks)
    assert f({"nick": "p1", "prop": "kills", "scope": "12", "match_slug": "m1", "line": 30.5, "side": "More"}) == "hit"
    assert f({"nick": "p1", "prop": "kills", "scope": "12", "match_slug": "m1", "line": 31.0, "side": "More"}) == "push"
    assert f({"nick": "p1", "prop": "kills", "scope": "12", "match_slug": "m1", "line": 31.5, "side": "Less"}) == "hit"
    assert f({"nick": "p1", "prop": "kills", "scope": "12", "match_slug": None, "line": 30.5, "side": "More"}) is None
    assert f({"nick": "p2", "prop": "kills", "scope": "12", "match_slug": "m2", "line": 30.5, "side": "More"}) == "void"
    assert f({"nick": "p3", "prop": "kills", "scope": "12", "match_slug": "m3", "line": 15.5, "side": "More"}) is None   # scope must match


def test_results_written_once(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "LOG", tmp_path / "b.jsonl"); monkeypatch.setattr(sb, "RESULTS", tmp_path / "r.jsonl")
    b = {"batch_id": "x1", "version": "batch-3", "slips": [{"kind": "diversified", "n": 2, "legs": [{"p": .6, "mult": 1.0}, {"p": .6, "mult": 1.0}]}]}
    (tmp_path / "b.jsonl").write_text(json.dumps(b) + "\n", encoding="utf-8")
    assert sb.settle_all(lambda l: None) == []                            # nothing settled yet
    assert len(sb.settle_all(lambda l: "hit")) == 1
    assert sb.settle_all(lambda l: "hit") == []                           # never twice
    assert [r["batch_id"] for r in sb.results()] == ["x1"]


def test_a_grade_kept_during_an_outage_is_never_batched_or_recommended(monkeypatch):
    """2026-10-14 owner: a saved grade the board keeps while live data is unavailable is stale - no shadow batch and no
    recommended slip is built on it."""
    import slips
    monkeypatch.setattr(sb, "_batched", lambda: set())
    rows = [_row(1, "m1"), _row(2, "m2", hltv_retry="2026-10-09T11:55:00+00:00", retry_cause="hltv")]
    ok, why = sb.eligible(rows, NOW, "M")
    assert [r["id"] for r in ok] == ["l1"] and why == {"saved grade kept during an outage (stale)": 1}
    legs = slips._legs([dict(r, start=(NOW + timedelta(hours=2)).isoformat(), matchup=r["match_slug"]) for r in rows], NOW.isoformat())
    assert [x["id"] for g in legs for x in g] == ["l1"]
