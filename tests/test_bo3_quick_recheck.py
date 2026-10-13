"""2026-10-12 settlement audit: BO3.gg's quick scoreboard had map 2 one kill short (Fruju vs MARKandLARRY). Picks settled
from the quick scoreboard are re-derived from the full one once it is out; corrections keep old + new values."""
import json
import sqlite3
from datetime import datetime, timezone

import picklog

NOW = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
SLUG = "fruju-vs-markandlarry-12-10-2026"
QUICK = {"source": "bo3", "rounds": [None, None], "won": [None, None], "per_map": [[15, 8], [14, 4]]}


class FakeB:
    def __init__(self, status="finished"):
        self.status = status

    def player_matches(self, pid, pages=1, ttl=None):
        return {"results": [{"slug": SLUG, "status": self.status, "games": []}]}


def full_rows(rounds=(18, 19), kills=(15, 15)):
    def map_rows(b, pid, matches, want):
        return [{"number": 1, "map": "de_inferno", "kills": kills[0], "headshots": 8, "rounds": rounds[0], "won": False},
                {"number": 2, "map": "de_mirage", "kills": kills[1], "headshots": 4, "rounds": rounds[1], "won": True}]
    return map_rows


def db(monkeypatch, tmp_path, picks):
    monkeypatch.setattr(picklog, "DB", tmp_path / "p.db")
    monkeypatch.setattr(picklog, "CORRECTIONS", tmp_path / "c.jsonl")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    con = picklog._db()
    for pid, line, actual, hit, res in picks:
        con.execute("INSERT INTO picks (id, player, player_id, team, opponent, match_slug, match_start, prop, scope, line, pick, "
                    "status, actual, hit, result, settled_at, test) VALUES (?, 'yourwombat', 28164, 'MARKandLARRY', 'Fruju', ?, "
                    "'2026-10-12T09:00:00Z', 'kills', '12', ?, 'Less', 'settled', ?, ?, ?, '2026-10-12T11:33:45+00:00', 0)",
                    (pid, SLUG, line, actual, hit, json.dumps(res)))
    con.commit()
    con.close()


def read(tmp_path, pid):
    return sqlite3.connect(tmp_path / "p.db").execute("SELECT actual, status, hit, result FROM picks WHERE id=?", (pid,)).fetchone()


def test_quick_total_stands_and_disagreement_is_held_for_review(monkeypatch, tmp_path):
    """Discrepancy audit 2026-10-12: from 2026-03 the QUICK scoreboard matches HLTV (~99.2%) better than the FULL one (96.1%,
    team-kills subtracted). The quick total stands; the disagreement is recorded; a line between the two is held."""
    db(monkeypatch, tmp_path, [(536, 29.5, 29, 1, QUICK), (560, 27.5, 29, 0, QUICK)])
    out = picklog.recheck_bo3_quick(FakeB(), full_rows(), now=NOW)                 # full says 30
    assert out["checked"] == 2 and len(out["disagree"]) == 2 and out["held"] == 1
    a, st, hit, res = read(tmp_path, 536)
    assert (a, st, hit) == (29, "settled", 1)                                        # never replaced
    r = json.loads(res)
    assert r["discrepancy"] == {"quick_total": 29, "full_total": 30} and r["review"] is True and r["provisional"] is True
    assert json.loads(read(tmp_path, 560)[3])["review"] is False                      # 27.5 is not between 29 and 30
    assert not (tmp_path / "c.jsonl").exists()                                        # no correction written
    assert picklog.recheck_bo3_quick(FakeB(), full_rows(), now=NOW)["checked"] == 0   # each pick once


def test_full_scoreboard_corrections_are_reverted_once(monkeypatch, tmp_path):
    full_detail = {"source": "bo3", "rounds": [18, 19], "won": [False, True], "per_map": [[15, 8], [15, 4]], "full_checked": True}
    db(monkeypatch, tmp_path, [(536, 29.5, 30, 0, full_detail)])
    (tmp_path / "c.jsonl").write_text(json.dumps({"id": 536, "old": {"actual": 29, "status": "settled", "hit": 1, "per_map": [[15, 8], [14, 4]]},
                                                  "new": {"actual": 30, "status": "settled", "hit": 0},
                                                  "reason": "BO3.gg full scoreboard replaced the quick scoreboard"}) + "\n", encoding="utf-8")
    done = picklog.revert_full_scoreboard_corrections(now=NOW)
    assert [d["id"] for d in done] == [536] and done[0]["held_for_review"] is True
    a, st, hit, res = read(tmp_path, 536)
    assert (a, st, hit) == (29, "settled", 1)
    r = json.loads(res)
    assert r["reverted_full"] and r["review"] and r["discrepancy"] == {"quick_total": 29, "full_total": 30} and r["per_map"] == [[15, 8], [14, 4]]
    log = [json.loads(x) for x in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(log) == 2 and log[1]["reason"].startswith("reverted")
    assert picklog.revert_full_scoreboard_corrections(now=NOW) == []                 # idempotent


def test_equal_full_scoreboard_confirms_without_a_correction(monkeypatch, tmp_path):
    db(monkeypatch, tmp_path, [(1, 29.5, 29, 1, QUICK)])
    out = picklog.recheck_bo3_quick(FakeB(), full_rows(kills=(15, 14)), now=NOW)
    assert out["confirmed"] == 1 and out["disagree"] == [] and not (tmp_path / "c.jsonl").exists()
    assert read(tmp_path, 1)[:3] == (29, "settled", 1) and json.loads(read(tmp_path, 1)[3])["full_checked"] is True


def test_waits_while_the_full_scoreboard_is_missing(monkeypatch, tmp_path):
    db(monkeypatch, tmp_path, [(1, 29.5, 29, 1, QUICK)])
    out = picklog.recheck_bo3_quick(FakeB(), full_rows(rounds=(None, None)), now=NOW)
    assert out["waiting"] == 1 and out["checked"] == 0 and read(tmp_path, 1)[:3] == (29, "settled", 1)


def test_full_and_hltv_settlements_are_never_rechecked(monkeypatch, tmp_path):
    full = {"source": "bo3", "rounds": [18, 19], "won": [False, True], "per_map": [[15, 8], [14, 4]]}
    hltv = {"source": "hltv", "rounds": [18, 19], "won": [False, True], "per_map": [[15, 8], [14, 4]], "hltv_match": 1}
    db(monkeypatch, tmp_path, [(1, 29.5, 29, 1, full), (2, 29.5, 29, 1, hltv)])
    assert picklog.recheck_bo3_quick(FakeB(), full_rows(), now=NOW)["checked"] == 0


def test_audit_summary_frozen_cohort_and_bo3_fallback(monkeypatch, tmp_path):
    """Owner review: report 'N wrong among V verified; U unresolved' on a frozen cohort; picks HLTV can't verify are
    checked against BO3.gg's full scoreboard (the original source)."""
    db(monkeypatch, tmp_path, [(536, 29.5, 29, 1, QUICK), (537, 27.5, 30, 0, QUICK)])
    con = sqlite3.connect(tmp_path / "p.db")
    con.execute("UPDATE picks SET settled_at='2026-10-13T00:00:00+00:00' WHERE id=537")   # settled after the cutoff
    con.commit(); con.close()
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: {"status": "none", "reason": "player not in HLTV"})
    import hltv
    monkeypatch.setattr(hltv, "same_team", lambda a, b: a == b)

    def map_rows(b, pid, matches, want):
        return [dict(x, team="MARKandLARRY") for x in full_rows()(b, pid, matches, want)]
    out = picklog.fix_scope_settlements(apply=False, scopes=("12",), until="2026-10-12T23:59:59+00:00", bo3=FakeB(), map_rows=map_rows)
    s = out["summary"]
    assert s["cohort"] == 1 and s["verified"] == 1 and s["verified_bo3"] == 1 and s["wrong"] == 1 and s["unresolved"] == 0
    assert s["line"] == "1 wrong among 1 verified; 0 unresolved" and out["differs"][0]["new"] == {"actual": 30, "status": "settled", "hit": 0}
    # without the BO3.gg source the same pick stays unresolved - never counted as verified
    s2 = picklog.fix_scope_settlements(apply=False, scopes=("12",), until="2026-10-12T23:59:59+00:00")["summary"]
    assert (s2["verified"], s2["unresolved"], s2["line"]) == (0, 1, "0 wrong among 0 verified; 1 unresolved")


def test_bo3_settlement_is_rechecked_against_hltv(monkeypatch, tmp_path):
    """Audit D4: a BO3.gg settlement HLTV disagrees with (chelo 15 vs 16) is corrected and logged; an equal one is marked."""
    full = {"source": "bo3", "rounds": [18, 19], "won": [False, True], "per_map": [[15, 8], [14, 4]]}
    db(monkeypatch, tmp_path, [(1, 29.5, 29, 1, full), (2, 28.5, 29, 0, full)])
    hl = {"status": "ok", "total": 30, "maps_used": [1, 2], "maps": ["de_inferno", "de_mirage"], "per_map": [[15, 8], [15, 4]],
          "rounds": [18, 19], "won": [False, True], "hltv_match": 2399999}
    monkeypatch.setattr(picklog, "scope_result", lambda r, same_day_only=False: dict(hl, total=30 if r["id"] == 1 else 29))
    out = picklog.recheck_bo3_with_hltv(now=NOW)
    assert out["checked"] == 2 and out["confirmed"] == 1 and [c["id"] for c in out["corrected"]] == [1]
    assert read(tmp_path, 1)[:3] == (30, "settled", 0) and json.loads(read(tmp_path, 1)[3])["source"] == "hltv"
    assert json.loads(read(tmp_path, 2)[3])["hltv_checked"] is True
    log = [json.loads(x) for x in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(log) == 1 and log[0]["old"]["actual"] == 29 and log[0]["new"]["actual"] == 30
    assert picklog.recheck_bo3_with_hltv(now=NOW)["checked"] == 0          # each pick once


def test_hltv_recheck_leaves_picks_alone_when_hltv_is_down(monkeypatch, tmp_path):
    full = {"source": "bo3", "rounds": [18, 19], "won": [False, True], "per_map": [[15, 8], [14, 4]]}
    db(monkeypatch, tmp_path, [(1, 29.5, 29, 1, full)])
    def boom(r, same_day_only=False):
        raise ConnectionError("down")
    monkeypatch.setattr(picklog, "scope_result", boom)
    out = picklog.recheck_bo3_with_hltv(now=NOW)
    assert out["waiting"] == 1 and read(tmp_path, 1)[:3] == (29, "settled", 1)
