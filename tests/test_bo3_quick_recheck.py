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


def test_quick_settlement_is_corrected_from_the_full_scoreboard(monkeypatch, tmp_path):
    db(monkeypatch, tmp_path, [(536, 29.5, 29, 1, QUICK), (560, 27.5, 29, 0, QUICK)])
    out = picklog.recheck_bo3_quick(FakeB(), full_rows(), now=NOW)
    assert out["checked"] == 2 and len(out["corrected"]) == 2 and out["confirmed"] == 0
    a, st, hit, res = read(tmp_path, 536)
    assert (a, st, hit) == (30, "settled", 0)                 # 30 vs Less 29.5: the outcome flips
    assert json.loads(res)["full_checked"] is True and json.loads(res)["per_map"] == [[15, 8], [15, 4]]
    assert read(tmp_path, 560)[:3] == (30, "settled", 0)      # total corrected, outcome unchanged
    log = [json.loads(x) for x in (tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {x["id"] for x in log} == {536, 560} and log[0]["old"]["per_map"] == [[15, 8], [14, 4]]
    # processed once: nothing left to re-check, no duplicate correction rows
    again = picklog.recheck_bo3_quick(FakeB(), full_rows(), now=NOW)
    assert again["checked"] == 0 and len((tmp_path / "c.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_equal_full_scoreboard_confirms_without_a_correction(monkeypatch, tmp_path):
    db(monkeypatch, tmp_path, [(1, 29.5, 29, 1, QUICK)])
    out = picklog.recheck_bo3_quick(FakeB(), full_rows(kills=(15, 14)), now=NOW)
    assert out["confirmed"] == 1 and out["corrected"] == [] and not (tmp_path / "c.jsonl").exists()
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
