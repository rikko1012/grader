"""2026-10-21 audit, pick log (audit_no 10, 11, 41). Offline: every row below is a TEST FIXTURE in a scratch picks.db."""
import json
import threading
import itertools

import pytest

import learning
import picklog
import slip_batches

_KEYS = itertools.count()


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(picklog, "DB", tmp_path / "picks.db")
    monkeypatch.setattr(picklog, "CORRECTIONS", tmp_path / "corrections.jsonl")
    monkeypatch.setattr(picklog, "_SCHEMA_OK", [False])
    return tmp_path / "picks.db"


def _insert(**kw):
    row = {"key": f"k{next(_KEYS)}", "player": "ZywOo", "prop": "kills", "scope": "12", "line": 40.5,
           "pick": "More", "status": "open", "match_slug": "vitality-vs-furia-10-10-2026", "test": 0,
           "factors": json.dumps({"x": "y" * 2000})}
    row.update(kw)
    with picklog._LOCK:
        con = picklog._db()
        cur = con.execute(f"INSERT INTO picks ({','.join(row)}) VALUES ({','.join('?' * len(row))})", list(row.values()))
        con.commit()
        con.close()
    return cur.lastrowid


# ------------------------------------------------------------------------------------------------ audit 10
def test_void_correction_clears_actual_and_does_not_pause_learning(db, monkeypatch):
    pid = _insert(scope="123", status="settled", actual=41.0, hit=1, settled_at="2026-10-10T20:00:00+00:00")
    monkeypatch.setattr(picklog, "scope_result", lambda r, **k: {"status": "void", "reason": "series ended 2-0 (TEST FIXTURE)"})
    out = picklog.fix_scope_settlements(apply=True, scopes=("123",))
    assert out["applied"] is True
    row = picklog.export()[0]
    assert (row["status"], row["actual"], row["hit"]) == ("void", None, None)
    assert row["settled_at"] == "2026-10-10T20:00:00+00:00"          # existing settlement time kept
    corr = learning.read_corrections(picklog.CORRECTIONS)
    assert corr and corr[-1]["id"] == pid and corr[-1]["new"]["actual"] is None
    assert learning.pending_corrections(picklog.export(), corr) == []


def test_dry_run_reports_not_applied(db, monkeypatch):
    _insert(scope="123", status="settled", actual=41.0, hit=1)
    monkeypatch.setattr(picklog, "scope_result", lambda r, **k: {"status": "void", "reason": "fixture"})
    out = picklog.fix_scope_settlements(apply=False, scopes=("123",))
    assert out["applied"] is False and len(out["differs"]) == 1
    assert picklog.export()[0]["status"] == "settled"


# ------------------------------------------------------------------------------------------------ audit 11
def test_match_slug_index_used(db):
    _insert()
    con = picklog._db()
    plan = " ".join(str(tuple(r)) for r in con.execute(
        "EXPLAIN QUERY PLAN SELECT player FROM picks WHERE match_slug IN (?, ?)", ("a", "b")))
    con.close()
    assert "picks_match" in plan and "SCAN picks" not in plan


def test_reads_do_not_take_the_global_lock(db):
    _insert(status="settled", actual=44.0)
    picklog.export()                                                  # schema initialised
    got = {}

    def read():
        got["m"] = picklog.rows_for_matches({"vitality-vs-furia-10-10-2026"})
        got["e"] = picklog.export()
        got["s"] = picklog.settlement_rows()
        got["r"] = picklog.report()
    with picklog._LOCK:                                               # a writer holds the lock the whole time
        t = threading.Thread(target=read)
        t.start()
        t.join(5)
        assert not t.is_alive(), "a read waited on picklog._LOCK"
    assert len(got["m"]) == 1 and got["m"][0]["actual"] == 44.0 and len(got["e"]) == 1 and len(got["s"]) == 1


def test_lock_free_read_sees_committed_writes_only(db):
    _insert(status="settled", actual=30.0)
    w = picklog._db()
    w.execute("UPDATE picks SET actual = 99 WHERE id = 1")            # open (uncommitted) write transaction
    assert picklog.rows_for_matches({"vitality-vs-furia-10-10-2026"})[0]["actual"] == 30.0
    w.commit()
    w.close()
    assert picklog.rows_for_matches({"vitality-vs-furia-10-10-2026"})[0]["actual"] == 99.0


def test_read_connection_is_read_only(db):
    _insert()
    con = picklog._read_db()
    with pytest.raises(Exception):
        con.execute("DELETE FROM picks")
    con.close()


def test_first_read_initialises_schema(db):
    assert picklog.export() == [] and picklog.rows_for_matches({"x"}) == []


# ------------------------------------------------------------------------------------------------ audit 41
def test_settlement_rows_match_export_for_slip_legs(db):
    _insert(status="settled", actual=44.0, result=json.dumps({"source": "hltv"}))
    _insert(player="apEX", status="void")
    _insert(player="flameZ", status="settled", actual=12.0, result=json.dumps({"review": "held"}))
    rows = picklog.settlement_rows()
    assert "factors" not in rows[0]
    legs = [{"nick": n, "prop": "kills", "scope": "12", "match_slug": "vitality-vs-furia-10-10-2026", "line": 40.5, "side": s}
            for n in ("ZywOo", "apEX", "flameZ") for s in ("More", "Less")]
    full, slim = slip_batches.leg_result_from_picks(picklog.export()), slip_batches.leg_result_from_picks(rows)
    assert [full(l) for l in legs] == [slim(l) for l in legs] == ["hit", "miss", "void", "void", None, None]


def test_rows_for_matches_uncapped_chunks(db):
    for i in range(450):
        _insert(match_slug=f"m{i}", status="settled", actual=float(i))
    assert len(picklog.rows_for_matches({f"m{i}" for i in range(450)})) == 200      # member default cap unchanged
    assert len(picklog.rows_for_matches({f"m{i}" for i in range(450)}, cap=None)) == 450
