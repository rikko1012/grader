"""2026-10-13 controlled rollback: the performance model reads the pinned input revision N0 (perf_live) - the backfill's
matches and later arrivals outside the pre-repair top-200 rule never reach a projection; a failed load never falls back
to the full tables; the set is loaded once for everyone."""
import os, sys, threading, time
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import hltv, hltv_store, perf_live

D = date(2026, 9, 1)


class _DB:
    def __init__(self):
        self.calls, self.fail, self.bad_rule, self.slow = [], None, False, 0.0
        self.excl = [(101, True), (202, False)]                  # 101 = backfill, 202 = later arrival outside the old rule
        self.player = [(D, 20, 24, 10, False, 10, m) for m in (100, 101, 202, 300)]
        self.opp = [(D, 70, 24, m) for m in (100, 101, 202)]
        self.rounds = [(D, 13, 9, m) for m in (100, 202)]


class _Cur:
    def __init__(self, db):
        self.db, self.rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        db = self.db
        db.calls.append(sql)
        if "raw_pages" in sql:
            if db.slow:
                time.sleep(db.slow)
            if db.fail:
                raise db.fail
            if "team_rankings" in sql and db.bad_rule:
                raise RuntimeError("column r.rank does not exist")      # a schema problem, not an outage
            self.rows = db.excl if "team_rankings" in sql else [r for r in db.excl if r[1]]
        elif "pm AS" in sql:
            assert "ps.matchid" in sql
            self.rows = db.player
        elif "SUM(ps.k)" in sql:
            assert "t.matchid" in sql
            self.rows = db.opp
        else:
            assert "t.matchid" in sql
            self.rows = db.rounds

    def fetchall(self):
        return list(self.rows)


class _Con:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _Cur(self.db)

    def close(self):
        pass


@pytest.fixture
def db(monkeypatch):
    d = _DB()
    monkeypatch.setattr(hltv, "_connect", lambda: _Con(d))
    monkeypatch.setattr(hltv, "_player_ids", lambda cur, nick, team: [7])
    monkeypatch.setattr(hltv, "_team_id", lambda cur, name: 55)
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: 55)
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://test")
    monkeypatch.setattr(perf_live, "_CACHE", {})
    monkeypatch.setattr(perf_live, "_SERVED", {})
    monkeypatch.setattr(perf_live, "_EX", {"val": None, "next": 0.0, "loading": False, "error": None, "loaded_at": None})
    return d


def _excl_calls(d):
    return sum("raw_pages" in s for s in d.calls)


def test_backfill_and_later_lower_tier_rows_never_reach_the_projection(db):
    ids, rows = perf_live.player_maps("x", "y", "2025-08-01")
    assert ids == [7] and len(rows) == 2                     # matches 100 and 300 only
    tid, om = perf_live.opp_maps("z", "2025-08-01")
    assert tid == 55 and len(om) == 1                         # match 100 only
    tid, rr = perf_live.team_rounds("y", "2025-08-01")
    assert len(rr) == 1
    rev = perf_live.input_revision()
    assert rev.startswith("N0:backfill=1;late_out=1;rule=top200@first-fetch;sha=")


def test_known_only_from_the_backfill_is_not_in_hltv_as_before_it(db):
    db.player = [(D, 20, 24, 10, False, 10, 101), (D, 18, 22, 9, False, 10, 202)]
    db.opp = [(D, 70, 24, 101)]
    assert perf_live.player_maps("x", "y", "2025-08-01") == (None, [])
    assert perf_live.opp_maps("z", "2025-08-01") == (None, [])
    assert "not found in HLTV" in perf_live.projection("x", "y", "z", False, day=D.toordinal() + 5)["error"]


def test_cache_and_stored_copy_carry_the_revision(db):
    since = "2025-08-01"
    hltv_store.save(("perf", "p", hltv._norm("x"), hltv._norm("y"), since), ([7], [("full tables",)] * 9))   # a pre-pin copy
    perf_live.player_maps("x", "y", since)
    assert all(k[-1] == "N0" for k in perf_live._CACHE)
    assert hltv_store.load(("perf", "p", hltv._norm("x"), hltv._norm("y"), since, "N0"))[1][1][0][1] == 20
    # an outage now: the N0 answer is served, never the pre-pin copy of the full tables
    perf_live._CACHE.clear()
    db.fail = hltv.Unavailable("HLTV database unreachable")
    ids, rows = perf_live.player_maps("x", "y", since)
    assert len(rows) == 2 and rows[0][1] == 20


def test_no_set_anywhere_is_transient_never_the_full_tables(db):
    db.fail = hltv.Unavailable("HLTV database unreachable")
    with pytest.raises(Exception) as e:
        perf_live.player_maps("x", "y", "2025-08-01")
    assert hltv.is_transient(e.value)
    assert perf_live.input_revision() is None and "unreachable" in perf_live.input_status()["last_error"]


def test_old_rule_query_failure_keeps_the_backfill_excluded(db):
    db.bad_rule = True
    ids, rows = perf_live.player_maps("x", "y", "2025-08-01")
    assert len(rows) == 3                                     # 101 out; 202 kept (the old rule could not be evaluated)
    assert "rule=unavailable (RuntimeError) - backfill only" in perf_live.input_revision()


def test_one_load_for_700_members(db):
    db.slow = 0.2
    out, errs = [], []

    def go():
        try:
            out.append(perf_live.excluded())
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(200)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(out) == 200 and _excl_calls(db) == 1 and len({id(x) for x in out}) == 1


def test_failed_load_retried_once_per_fail_ttl_not_per_caller(db):
    db.fail = hltv.Unavailable("HLTV database unreachable")
    errs = []

    def go():
        try:
            perf_live.excluded()
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(100)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(errs) == 100 and all(hltv.is_transient(e) for e in errs) and _excl_calls(db) == 1


def test_stale_set_refreshed_by_one_caller_others_keep_it(db):
    first = perf_live.excluded()
    perf_live._EX["next"] = 0.0                               # due for its 30-minute re-check
    db.slow, db.excl = 0.2, [(101, True), (202, False), (303, False)]
    got = []
    ts = [threading.Thread(target=lambda: got.append(perf_live.excluded())) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert _excl_calls(db) == 2 and any(g is first for g in got) and 303 in perf_live.excluded()["ids"]


def test_projection_names_the_answers_and_the_revision_it_used(db):
    out = perf_live.projection("x", "y", "z", False, day=D.toordinal() + 5)
    assert out["hltv_data"]["player_maps"]["state"] == "live" and out["hltv_data"]["opp_maps"]["state"] == "live"
    assert out["hltv_data"]["input_revision"].startswith("N0:backfill=1;late_out=1")
