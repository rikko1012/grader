"""Controlled rollback (2026-10-13/14): the performance model reads ONLY the pinned input revision N0 (perf_live) - an
inclusion list (pre-backfill matches + later arrivals that pass the pre-repair top-200 rule on the dated snapshot of their
first fetch). Fail closed: no approximate fallback set, a match not evaluated yet stays out, a failed evaluation serves the
last verified evaluation of the same rule marked STALE, or nothing (transient). Loaded once for everyone."""
import os, sys, threading, time
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
import hltv, hltv_store, perf_live

D = date(2026, 9, 1)
SNAP = date(2026, 10, 13)


class _DB:
    def __init__(self):
        self.calls, self.fail_on, self.fail, self.slow = [], None, None, 0.0
        self.pre = [(100,), (300,)]                                               # first fetched before the backfill / seed
        self.late = [(101, "history", None), (202, "outside_top", SNAP), (303, "in", SNAP), (404, "no_snapshot", None),
                     (707, "no_raw_page", None)]                             # a row of another source, no raw page
        self.snaps = [(date(2026, 10, 11), 350), (SNAP, 351)]
        self.player = [(D, 20, 24, 10, False, 10, m) for m in (100, 101, 202, 300, 303, 404, 505, 707)]   # 505: never evaluated
        self.opp = [(D, 70, 24, m) for m in (100, 101, 202, 505)]
        self.rounds = [(D, 13, 9, m) for m in (100, 202)]

    def kind(self, sql):
        if "LEFT JOIN (SELECT matchid, min(fetched_at)" in sql:
            return "pre"
        if "WITH first AS" in sql:
            return "late"
        if "FROM team_rankings GROUP BY date" in sql:
            return "snaps"
        return "pm" if "pm AS" in sql else "opp" if "SUM(ps.k)" in sql else "rounds"


class _Cur:
    def __init__(self, db):
        self.db, self.rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        db, k = self.db, self.db.kind(sql)
        db.calls.append(k)
        if k in ("pre", "late", "snaps"):
            if db.slow:
                time.sleep(db.slow)
            if db.fail and db.fail_on in (None, k):
                raise db.fail
            assert "COALESCE" not in sql and "min(date) FROM snaps" not in sql   # never a later snapshot for an older match
        self.rows = {"pre": db.pre, "late": db.late, "snaps": db.snaps, "pm": db.player, "opp": db.opp, "rounds": db.rounds}[k]

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
    monkeypatch.setattr(hltv, "canonical_team_id", lambda cur, name: 55)
    monkeypatch.setattr(hltv, "dsn", lambda: "postgres://test")
    monkeypatch.setattr(perf_live, "_CACHE", {})
    monkeypatch.setattr(perf_live, "_SERVED", {})
    monkeypatch.setattr(perf_live, "_IN", {"val": None, "next": 0.0, "loading": False, "error": None, "loaded_at": None,
                                           "stale_since": None})
    return d


def _evals(d):
    return d.calls.count("pre")


def test_only_n0_rows_reach_the_projection(db):
    ids, rows = perf_live.player_maps("x", "y", "2025-08-01")
    assert ids == [7] and len(rows) == 3                     # 100, 300 (before the backfill), 303 (passed the old rule); 707 out
    tid, om = perf_live.opp_maps("z", "2025-08-01")
    assert tid == 55 and len(om) == 1                         # 100 only (101 backfill, 202 all-tier, 505 not evaluated)
    rev = perf_live.input_revision()
    assert rev.startswith("N0/n0-r4:pre=2;late_in=1;history_out=1;late_out=1;no_snapshot=1;no_teams=0;no_raw_page=1;")
    assert "snapshots=2026-10-13..2026-10-13" in rev and "STALE" not in rev
    st = perf_live.input_status()
    assert st["ranking_snapshots"] == [("2026-10-11", 350), ("2026-10-13", 351)] and st["stale"] is False
    assert "stake checks" in st["full_tables"]


def test_known_only_from_outside_n0_is_not_in_hltv_as_before_it(db):
    db.player = [(D, 20, 24, 10, False, 10, 101), (D, 18, 22, 9, False, 10, 505)]
    db.opp = [(D, 70, 24, 202)]
    assert perf_live.player_maps("x", "y", "2025-08-01") == (None, [])
    assert perf_live.opp_maps("z", "2025-08-01") == (None, [])
    assert "not found in HLTV" in perf_live.projection("x", "y", "z", False, day=D.toordinal() + 5)["error"]


def test_failed_evaluation_is_fail_closed_never_an_approximate_set(db):
    db.fail, db.fail_on = RuntimeError('relation "raw_pages" does not exist'), "late"      # a schema error, not an outage
    with pytest.raises(Exception) as e:
        perf_live.player_maps("x", "y", "2025-08-01")
    assert hltv.is_transient(e.value)                          # temporarily unavailable - never the full tables
    assert db.calls.count("late") == 1 and "pm" not in db.calls    # no fallback query, no history read
    assert perf_live.input_revision() is None and "RuntimeError" in perf_live.input_status()["last_error"]


def test_stale_evaluation_is_used_marked_and_keeps_new_matches_out(db):
    perf_live.n0()
    perf_live._IN["next"] = 0.0                                # due for re-evaluation
    perf_live._CACHE.clear()
    db.fail = hltv.Unavailable("HLTV database unreachable")
    db.fail_on = "pre"
    db.player = db.player + [(D, 30, 24, 10, False, 10, 606)]  # fetched since the last evaluation
    ids, rows = perf_live.player_maps("x", "y", "2025-08-01")
    assert len(rows) == 3                                      # 606 stays out until it is evaluated
    assert "STALE evaluated" in perf_live.input_revision() and perf_live.input_status()["stale"]
    out = perf_live.projection("x", "y", "z", False, day=D.toordinal() + 5)
    assert out["hltv_data"]["input_revision_stale"] and "refresh failing" in out["data_quality"]


def test_stored_verified_copy_after_restart_and_never_another_rule(db):
    good = perf_live._load_n0()
    db.fail = hltv.Unavailable("HLTV database unreachable")
    hltv_store.save(("perf_n0", "N0", "n0-r4"), dict(good, rule="n0-r3"))      # an older rule's copy: never used
    with pytest.raises(hltv.Unavailable):
        perf_live.n0()
    hltv_store._LAST.clear()
    hltv_store.save(("perf_n0", "N0", "n0-r4"), good)
    perf_live._IN.update(val=None, next=0.0)
    assert perf_live.n0()["late_in"] == frozenset({303}) and "STALE" in perf_live.input_revision()


def test_cache_and_stored_copy_carry_the_revision_and_rule(db):
    since = "2025-08-01"
    hltv_store.save(("perf", "p", hltv._norm("x"), hltv._norm("y"), since), ([7], [("full tables",)] * 9))     # pre-pin copy
    hltv_store.save(("perf", "p", hltv._norm("x"), hltv._norm("y"), since, "N0"), ([7], [("rule r1",)] * 9))  # r1 copy
    perf_live.player_maps("x", "y", since)
    assert all(k[-2:] == ("N0", "n0-r4") for k in perf_live._CACHE)
    perf_live._CACHE.clear()
    db.fail = hltv.Unavailable("HLTV database unreachable")
    ids, rows = perf_live.player_maps("x", "y", since)        # the outage serves THIS revision's own answer
    assert len(rows) == 3 and rows[0][1] == 20


def test_a_match_without_a_raw_page_is_never_the_original_set():
    # 2026-10-14 post-deploy check: r3 took "no raw page" as the 2026-10-11 seed, but the seed has raw pages - the only such
    # rows were a new source's ('cito'), and they entered N0 without the rule
    assert "IS NULL" not in perf_live._PRE_SQL and "x.f < %(from)s" in perf_live._PRE_SQL
    assert "'no_raw_page'" in perf_live._LATE_SQL and "NOT EXISTS (SELECT 1 FROM raw_pages" in perf_live._LATE_SQL


def test_one_evaluation_for_700_members(db):
    db.slow = 0.2
    out, errs = [], []

    def go():
        try:
            out.append(perf_live.n0())
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(200)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs and len(out) == 200 and _evals(db) == 1 and len({id(x) for x in out}) == 1


def test_failed_evaluation_retried_once_per_fail_ttl_not_per_caller(db):
    db.fail = hltv.Unavailable("HLTV database unreachable")
    errs = []

    def go():
        try:
            perf_live.n0()
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=go) for _ in range(100)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(errs) == 100 and all(hltv.is_transient(e) for e in errs) and _evals(db) == 1


def test_due_evaluation_refreshed_by_one_caller_others_keep_it(db):
    first = perf_live.n0()
    perf_live._IN["next"] = 0.0
    db.slow, db.late = 0.2, db.late + [(505, "in", SNAP)]
    got = []
    ts = [threading.Thread(target=lambda: got.append(perf_live.n0())) for _ in range(50)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert _evals(db) == 2 and any(g is first for g in got) and 505 in perf_live.n0()["late_in"]


def test_projection_names_the_answers_and_the_revision_it_used(db):
    out = perf_live.projection("x", "y", "z", False, day=D.toordinal() + 5)
    assert out["hltv_data"]["player_maps"]["state"] == "live" and out["hltv_data"]["opp_maps"]["state"] == "live"
    assert out["hltv_data"]["input_revision"].startswith("N0/n0-r4:") and not out["hltv_data"]["input_revision_stale"]
