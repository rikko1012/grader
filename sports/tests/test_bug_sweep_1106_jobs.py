"""Regression tests for the 2026-11-06 bug sweep, sports jobs (#5 #6 #7 #9 #10 #12 #37 #38 #39 #40 #41 #42 #44 #45).
Each test reproduces the report's failure case (C:/Users/tikic/cs2_scout_ops/logs/bug_sweep_2026-11-06.md). No
network."""
import gzip
import json
import shutil
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from sports import config, net, store, worker
from sports.contracts import Membership, Settlement
from sports.games import backfill_job, collect, espn_game, espn_props, store as gstore
from sports.tests import helpers as h

NOW = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)


@pytest.fixture
def gdb(tmp_path):
    db = gstore.connect(tmp_path / "games.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------------------------------------- #5
def test_5_previous_season_is_not_done_when_every_game_failed(gdb):
    lister = lambda lg, s, t: [f"{s}-{t}-{i}" for i in range(100)] if t == 2 else []     # noqa: E731
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    out = backfill_job.run(gdb, ("nba",), 1e9, now, ingest=lambda d, lg, ev: "HTTP 429 from site.api.espn.com (held)",
                           lister=lister)
    assert out["nba:2026:2"]["errors"] == 100
    assert json.loads(gstore.setting(gdb, "backfill:nba:2026:2"))["done"] is False      # was True (permanent)
    stored = []

    def ok(d, lg, ev):
        stored.append(ev)
        return "stored"
    later = now + timedelta(hours=backfill_job.RELIST_H + 1)                              # ESPN healthy again
    backfill_job.run(gdb, ("nba",), 1e9, later, ingest=ok, lister=lister)
    assert sum(1 for e in stored if e.startswith("2026-2-")) == 100                     # the season is read again
    assert json.loads(gstore.setting(gdb, "backfill:nba:2026:2"))["done"] is True        # and only now done


# ---------------------------------------------------------------------------------------------------------- #6
def test_6_failed_venue_lookup_is_never_cached(gdb, monkeypatch):
    nfl = config.league("nfl")

    def down(lg, vid):
        raise net.HttpError(503, "https://sports.core.api.espn.com/v2/venues/X")
    monkeypatch.setattr(espn_game, "venue_raw", down)
    assert collect._venue(gdb, nfl, "X") == {"indoor": None}
    assert gstore.setting(gdb, "venue:nfl:X") is None                                    # was {"indoor": null} forever
    monkeypatch.setattr(espn_game, "venue_raw", lambda lg, vid: {"indoor": True, "grass": False,
                                                                 "fullName": "AT&T Stadium"})
    assert collect._venue(gdb, nfl, "X")["indoor"] is True                               # read again once ESPN is back
    assert json.loads(gstore.setting(gdb, "venue:nfl:X"))["indoor"] is True


# ---------------------------------------------------------------------------------------------------------- #7
def test_7_one_malformed_game_never_stalls_the_other_games(gdb, monkeypatch):
    nba = config.league("nba")
    games = [{"id": "A", "start": "2026-11-06T20:00:00Z", "completed": False},
             {"id": "B", "start": "2026-11-06T21:00:00Z", "completed": False}]

    def summary(lg, eid):
        if eid == "A":
            raise ValueError("Expecting value: line 1 column 1 (char 0)")              # a non-JSON 200
        return {}
    monkeypatch.setattr(espn_game, "summary_raw", summary)
    monkeypatch.setattr(espn_game, "context", lambda body: {})
    monkeypatch.setattr(espn_props, "fetch", lambda lg, eid, ath: ([], {}))
    out = collect._context(gdb, nba, games, NOW, lambda: NOW)
    assert out["games"] == 2 and out["errors"] == 1
    assert gstore.setting(gdb, "at:context:nba:B") is not None                           # B was processed
    rows = gdb.all("SELECT provider_event_id, finished_at, ok FROM sports_game_fetches WHERE kind = 'context' "
                   "ORDER BY provider_event_id") if _has_col(gdb, "provider_event_id") else \
        gdb.all("SELECT * FROM sports_game_fetches WHERE kind = 'context'")
    assert len(rows) == 2 and all(r["finished_at"] for r in rows)                        # A's fetch row is finished


def _has_col(db, col):
    return any(r["name"] == col for r in db.all("PRAGMA table_info(sports_game_fetches)"))


def test_7_tick_still_reaches_the_kalshi_step(gdb, monkeypatch):
    monkeypatch.setattr(collect, "_rosters", lambda db, lg, now: 0)
    monkeypatch.setattr(collect, "_power", lambda db, lg, now: False)
    monkeypatch.setattr(collect, "_injuries", lambda db, lg, now: False)
    monkeypatch.setattr(collect, "_games_window", lambda lg, now, b, a: [
        {"id": "A", "start": "2026-11-06T20:00:00Z", "completed": False}])

    def bad(lg, eid):
        raise ValueError("Expecting value")
    monkeypatch.setattr(espn_game, "summary_raw", bad)
    calls = []
    monkeypatch.setattr(collect, "_kalshi", lambda db, lg, games, now, clock: calls.append(lg["key"]) or {})
    out = collect.tick(gdb, ("nba",), now=NOW)
    assert "error" not in out["nba"] and calls == ["nba"]


# ---------------------------------------------------------------------------------------------------------- #12
def test_12_a_timed_out_job_is_never_retried_beside_its_running_thread():
    con = store.connect()
    live, peak, lock = [0], [0], threading.Lock()

    def fn(ctx):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(1.0)
        with lock:
            live[0] -= 1
    try:
        j = worker.Jobs(con, attempts=3, timeout_s=0.3, sleep=lambda s: None)
        res = j.run("k-timeout", "settle", fn)
        assert res["state"] == "failed" and "timeout" in res["error"] and res["attempts"] == 1
        time.sleep(1.2)
        assert peak[0] == 1                                                              # was 3 copies at once
    finally:
        time.sleep(0.1)
        con.close()


def _tracked_sleeper(secs):
    live, peak, lock = [0], [0], threading.Lock()

    def fn(*a, **k):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(secs)
        with lock:
            live[0] -= 1
        return {}
    return fn, live, peak


def test_12_a_timed_out_league_settle_stops_the_rest_of_the_run(monkeypatch):
    """#12 review: cmd_settle ran {lg: jobs.run(...)} league after league, so nfl's settle (then results and learning)
    ran beside nba's timed-out thread on the same connection (peak 2). It now stops at the first still-running job."""
    import argparse
    from sports import flags, snapshots
    from sports.model import learning
    monkeypatch.setattr(flags, "require", lambda name: None)
    fn, live, peak = _tracked_sleeper(1.0)
    monkeypatch.setattr(worker, "settle_league", fn)
    after = []
    monkeypatch.setattr(snapshots, "write_results", lambda con, lg, **k: after.append(("results", lg)))
    monkeypatch.setattr(learning, "on_settled", lambda con: after.append("learning"))
    orig = worker.Jobs
    monkeypatch.setattr(worker, "Jobs", lambda con: orig(con, attempts=3, timeout_s=0.3, sleep=lambda s: None))
    con = store.connect()
    try:
        out = worker.cmd_settle(argparse.Namespace(leagues=["nba", "nfl"], since=None, bucket_min=60), con)
        assert out["still_running"] is True and out["nba"]["still_running"] is True
        assert out["nfl"]["state"] == "not_run" and after == []
        time.sleep(1.2)
        assert peak[0] == 1                                                              # was 2
        assert worker._still_running(out)
    finally:
        time.sleep(0.1)
        con.close()


def test_12_loop_ends_when_a_job_is_still_running(monkeypatch):
    """#12 review: `worker loop` went on to settle (and later ingest again) beside a timed-out job's thread."""
    import argparse
    from sports import flags
    monkeypatch.setattr(flags, "require", lambda name: None)
    monkeypatch.setattr(worker, "rss_mb", lambda: 10)
    settled = []
    monkeypatch.setattr(worker, "cmd_ingest", lambda a, con: {"nba": {"state": "failed", "still_running": True}})
    monkeypatch.setattr(worker, "cmd_settle", lambda a, con: settled.append(1) or {})
    monkeypatch.setattr(worker.time, "sleep", lambda s: (_ for _ in ()).throw(AssertionError("looped on")))
    res = worker.cmd_loop(argparse.Namespace(leagues=["nba"], sources="espn", bucket_min=5, every=10, kalshi_every=30), None)
    assert res == {"still_running": True, "step": "ingest"} and settled == []

    monkeypatch.setattr(worker, "cmd_ingest", lambda a, con: {"nba": {"state": "done"}})
    monkeypatch.setattr(worker, "cmd_settle", lambda a, con: {"still_running": True})
    res = worker.cmd_loop(argparse.Namespace(leagues=["nba"], sources="espn", bucket_min=5, every=10, kalshi_every=30), None)
    assert res == {"still_running": True, "step": "settle"}


def test_12_timeout_never_waits_on_the_write_lock_its_job_holds():
    """#12 review: the timeout path's ledger write took store._WRITE_LOCK, which the timed-out job held inside a
    write_tx, so run() blocked until the job's batch ended. It now waits at most LEDGER_WAIT_S and writes later."""
    con = store.connect()
    try:
        j = worker.Jobs(con, attempts=3, timeout_s=0.2, sleep=lambda s: None)
        j.LEDGER_WAIT_S = 0.1
        held = threading.Event()

        def fn(ctx):
            with store.write_tx(con):
                held.set()
                time.sleep(1.5)
            return {}
        t0 = time.time()
        res = j.run("k12-lock", "settle", fn)
        took = time.time() - t0
        assert held.is_set() and took < 1.0                                              # was ~1.5 s (the tx length)
        assert res["still_running"] and res.get("ledger_deferred") is True
        time.sleep(1.5)
        assert j.flush(wait_s=1.0) == 0
        assert con.execute("SELECT state, error FROM jobs WHERE job_key = 'k12-lock'").fetchone()[0] == "failed"
    finally:
        time.sleep(0.1)
        con.close()


def test_12_settle_job_has_its_own_job_timeout_below_the_scheduler_kill(monkeypatch):
    from sports import scheduler
    for std in (True, False):
        monkeypatch.setattr(scheduler, "standard", lambda std=std: std)
        j = scheduler.default_jobs()["settle"]
        t = int(j["env"]["SPORTS_JOB_TIMEOUT_S"])                                       # was unset -> 300 s
        assert j["timeout_s"] - 120 <= t < j["timeout_s"]


def test_12_ledger_commit_waits_for_another_threads_batch():
    con = store.connect()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS t12 (x INTEGER)")
        con.commit()
        j = worker.Jobs(con)
        con.execute("INSERT INTO jobs (job_key, job, state, attempts, created_at) VALUES ('k12','t','pending',0,'x')")
        con.commit()
        entered, done = threading.Event(), threading.Event()

        def batch():
            try:
                with store.write_tx(con):
                    con.execute("INSERT INTO t12 VALUES (1)")
                    entered.set()
                    time.sleep(0.3)
                    raise RuntimeError("batch fails")
            except RuntimeError:
                pass
            done.set()
        th = threading.Thread(target=batch)
        th.start()
        entered.wait(2)
        j._set("k12", state="running")                     # must not commit the half-written batch
        th.join()
        assert done.is_set()
        assert con.execute("SELECT COUNT(*) FROM t12").fetchone()[0] == 0
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------------------- #45
def test_45_quarantine_many_never_commits_another_threads_batch(monkeypatch):
    """The report's interleaving: thread B is between its quarantine inserts when thread A's batch starts on the shared
    connection (A sees B's open transaction, so it issues no BEGIN), then B's commit lands inside A's batch."""
    con = store.connect()
    try:
        con.execute("CREATE TABLE IF NOT EXISTS t45 (x INTEGER)")
        con.commit()
        b_between, a_inserted = threading.Event(), threading.Event()
        real_q = store.quarantine
        n = [0]

        def q(*a, **k):
            real_q(*a, **k)
            n[0] += 1
            if n[0] == 1:
                b_between.set()
                a_inserted.wait(0.5)                    # base: A writes here; fixed: A waits for B's lock
        monkeypatch.setattr(store, "quarantine", q)

        def batch():
            b_between.wait(2)
            try:
                with store.write_tx(con):
                    con.execute("INSERT INTO t45 VALUES (1)")
                    a_inserted.set()
                    time.sleep(0.3)
                    raise RuntimeError("batch fails")
            except RuntimeError:
                pass
        th = threading.Thread(target=batch)
        th.start()
        store.quarantine_many(con, [{"kind": "player", "source": "espn", "source_key": f"x{i}", "reason": "r"}
                                    for i in range(2)], "nba")
        th.join()
        assert con.execute("SELECT COUNT(*) FROM t45").fetchone()[0] == 0              # was 1 (half the batch)
        assert not con.in_transaction
        assert con.execute("SELECT COUNT(*) FROM quarantine WHERE source_key LIKE 'x%'").fetchone()[0] == 2
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------------------- #44
def test_44_gamelog_memberships_are_never_decoded(monkeypatch, tmp_path):
    from sports import snapshots
    from sports.tests.test_context_reconcile import world
    con = store.connect()
    try:
        world(con)
        mems = [Membership(player_id=f"nba:player:espn:9{i:04d}", team_id=h.NY, valid_from=f"2026-0{1 + i % 9}-01T00:00:00Z",
                           valid_to=f"2026-0{1 + i % 9}-01T00:00:00Z", source="espn_gamelog", observed_at=h.T0,
                           known_at=h.T0) for i in range(300)]
        store.put_many(con, mems)
        assert all(m.source == "espn" for _, m in worker._latest_memberships(con, "nba"))
        assert len(worker._latest_memberships(con, "nba")) == 4
        decoded = []
        real = snapshots._latest

        def spy(c, kind, *a, **k):
            out = real(c, kind, *a, **k)
            if kind == "membership":
                decoded.append(len(out))
            return out
        monkeypatch.setattr(snapshots, "_latest", spy)
        snapshots.build(con, "nba", now="2026-10-23T20:10:00Z", base=tmp_path, history=False, results=False)
        assert decoded and max(decoded) <= 4                                             # was 304 (every gamelog row)
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------------------- #37
def test_37_non_json_200_is_a_recorded_failure_not_an_escape(tmp_path, monkeypatch):
    import requests
    from sports.books import base, fanduel, store as bstore, worker as bworker
    from sports.books.base import Event, FetchResult
    db = bstore.connect(tmp_path / "books.db", dsn="")
    try:
        now = datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
        game = Event("36220759", "nba", "New York Knicks @ Boston Celtics", "Boston Celtics", "New York Knicks",
                     (now + timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        monkeypatch.setattr(bworker.canon, "keys_for", lambda lg, prov, evs, now=None: {e.provider_event_id: "espn:401"
                                                                                        for e in evs})
        fd = fanduel.FanDuel()
        monkeypatch.setattr(fd, "events", lambda lg: FetchResult(events=[game], requests=1))

        def html(*a, **k):
            r = requests.Response()
            r.status_code, r._content = 200, b"<html>geo</html>"
            return r
        monkeypatch.setattr(net, "get", html)

        class Other(base.Provider):
            name = "other"
            calls = []

            def events(self, lg):
                return FetchResult(events=[game], requests=1)

            def props(self, lg, ev):
                self.calls.append(ev.provider_event_id)
                return FetchResult(offers=[], requests=1, http_status=200)
        other = Other()
        s = bworker.run_once(db, ("nba",), now, {"fanduel": fd, "other": other}, probe=False)
        assert other.calls == ["36220759"]                                               # the next provider still ran
        assert "answer not readable" in s["providers"]["fanduel"]["error"]
        assert bstore.provider_state(db, "fanduel")["cooldown_until"]                    # backs off
        assert all(r["finished_at"] for r in db.all("SELECT finished_at FROM sports_book_fetches"))

        class Boom(Other):
            name = "boom"

            def props(self, lg, ev):
                raise KeyError("unexpected shape")                                      # any other provider escape
        s = bworker.run_once(db, ("nba",), now + timedelta(minutes=10), {"boom": Boom()}, probe=False)
        assert "KeyError" in s["providers"]["boom"]["error"]
    finally:
        db.close()


# ---------------------------------------------------------------------------------------------------------- #38
def test_38_oddspapi_alias_key_is_seen_by_capture_key_state_and_inventory(monkeypatch):
    from sports.books import inventory, oddspapi, sources
    monkeypatch.setenv("odds_papi_api", "k")
    assert oddspapi.api_key() == "k"
    assert sources.key_state()["oddspapi"] == "key set"                                  # was 'key not set here'
    monkeypatch.setattr(oddspapi.OddsPapi, "account", lambda self, res: {})
    assert sources.capture("oddspapi", "nba")["status"] != "key not set here"
    assert "not set" not in str(inventory.capture("oddspapi", "nba").get("status"))
    monkeypatch.delenv("odds_papi_api")
    assert sources.key_state()["oddspapi"] == "key not set here"


# ---------------------------------------------------------------------------------------------------------- #39
def test_39_hold_query_is_postgres_safe_and_the_lease_is_always_released(tmp_path, monkeypatch):
    from sports.books import store as bstore, worker as bworker
    seen = []

    class PG:
        """psycopg2's rule: with a params tuple a literal '%' is a placeholder."""
        def all(self, sql, params=()):
            seen.append((sql, params))
            if params is not None and "%" in sql.replace("%s", ""):
                raise IndexError("tuple index out of range")
            return []
    bworker.load_holds(PG(), NOW)
    assert seen and "%" not in seen[0][0] and seen[0][1] == ("hold:%",)
    gseen = []

    class GPG(PG):
        def all(self, sql, params=()):
            gseen.append(sql)
            return super().all(sql, params)
    db = bstore.connect(tmp_path / "books.db", dsn="")
    try:
        def boom(d, n):
            raise IndexError("tuple index out of range")
        monkeypatch.setattr(bworker, "load_holds", boom)
        with pytest.raises(IndexError):
            bworker.run_once(db, ("nba",), NOW, {}, probe=False)
        assert bstore.take_lease(db, "someone-else", 60, now=NOW)                         # was held 15 min
    finally:
        db.close()


def test_39_games_tick_hold_query_is_postgres_safe():
    import inspect
    src = inspect.getsource(collect.tick)
    assert "LIKE 'hold:%'" not in src and '("hold:%",)' in src


# ---------------------------------------------------------------------------------------------------------- #40
def test_40_depth_chart_outage_is_never_cached(monkeypatch):
    from sports import cache, espn
    nfl = config.league("nfl")
    monkeypatch.setattr(espn, "teams", lambda lg: [{"id": "1", "abbr": "DAL"}, {"id": "2", "abbr": "NYG"}])

    def down(url, **k):
        raise net.HttpError(503, url)
    monkeypatch.setattr(net, "get_json", down)
    assert espn.depth_index(nfl) == {}
    assert cache.get("espn:depth", "nfl", 6 * 3600) is None                             # nothing stored
    body = {"depthchart": [{"positions": {"qb": {"position": {"abbreviation": "QB"},
                                                 "athletes": [{"displayName": "Joe X", "id": "9"}]}}}]}
    monkeypatch.setattr(net, "get_json", lambda url, **k: body)
    idx = espn.depth_index(nfl)
    parsed = espn.parse_depth(body, "football", "DAL")
    assert idx == {**espn.parse_depth(body, "football", "NYG"), **parsed} or idx         # recovered at once
    assert cache.get("espn:depth", "nfl", 6 * 3600) is not None


def test_40_one_failed_team_keeps_its_last_good_rows_uncached(monkeypatch):
    from sports import cache, espn
    nfl = config.league("nfl")
    monkeypatch.setattr(espn, "teams", lambda lg: [{"id": "1", "abbr": "DAL"}, {"id": "2", "abbr": "NYG"}])
    good = {"x": {"name": "Old QB", "team": "NYG", "depth": 0, "pos": "QB", "role": "starter", "id": "7"}}
    cache.put("espn:depth", "nfl", good)
    import sqlite3
    with cache._WRITE:                                            # age the stored copy past its 6 h
        cache._con().execute("UPDATE kv SET at = at - 7 * 3600 WHERE bucket = 'espn:depth'")
        cache._con().commit()

    def one_down(url, **k):
        if "/teams/2/" in url:
            raise net.HttpError(503, url)
        return {}
    monkeypatch.setattr(net, "get_json", one_down)
    idx = espn.depth_index(nfl)
    assert idx.get("x", {}).get("team") == "NYG"                                         # last good rows kept
    assert cache.get("espn:depth", "nfl", 6 * 3600) is None                             # the partial read not stored
    del sqlite3


def test_40_one_failing_team_never_rereads_every_team_on_each_call(monkeypatch):
    """#40 review: with one team failing every time, the 6 h cache was never written, so every call (board, pregame
    news every 15 min) re-read all teams. The partial answer is now reused for DEPTH_PARTIAL_TTL_S only."""
    from sports import cache, espn
    nfl = config.league("nfl")
    monkeypatch.setattr(espn, "teams", lambda lg: [{"id": str(i), "abbr": f"T{i}"} for i in range(1, 33)])
    calls = []

    def one_down(url, **k):
        calls.append(url)
        if "/teams/2/" in url:
            raise net.HttpError(404, url)
        return {}
    monkeypatch.setattr(net, "get_json", one_down)
    espn.depth_index(nfl)
    assert len(calls) == 32
    for _ in range(5):
        espn.depth_index(nfl)
    assert len(calls) == 32                                                              # was 32 more per call
    assert cache.get("espn:depth", "nfl", 6 * 3600) is None                             # never the 6 h copy
    with cache._WRITE:                                                                   # past the short reuse
        cache._con().execute("UPDATE kv SET at = at - ? WHERE bucket = 'espn:depth_partial'",
                             (espn.DEPTH_PARTIAL_TTL_S + 1,))
        cache._con().commit()
    espn.depth_index(nfl)
    assert len(calls) == 64                                                              # read again after it


# ---------------------------------------------------------------------------------------------------------- #41
def test_41_a_truncated_download_never_becomes_the_cached_file(tmp_path, monkeypatch):
    import io
    import sqlite3
    from sports.model import datasets
    monkeypatch.setattr(datasets, "db_path", lambda: tmp_path / "model" / "history.db")
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE files (name TEXT PRIMARY KEY, url TEXT, fetched_at TEXT, sha256 TEXT, bytes INTEGER, "
                "rows INTEGER)")
    full = gzip.compress(b"a,b\n" + b"1,2\n" * 5000)

    class R:
        def __init__(self, content):
            self.content = content
    monkeypatch.setattr(net, "get", lambda url, **k: R(full[: len(full) // 2]))           # cut short
    with pytest.raises((EOFError, OSError)):
        datasets._download(con, "pbp.csv.gz", "https://x/pbp.csv.gz", gz=True)
    local = tmp_path / "model" / "files" / "pbp.csv.gz"
    assert not local.exists()                                                          # no fresh-looking partial file
    assert not list(local.parent.glob("*.part"))
    monkeypatch.setattr(net, "get", lambda url, **k: R(full))
    with datasets._download(con, "pbp.csv.gz", "https://x/pbp.csv.gz", gz=True) as f:
        assert sum(1 for _ in f) == 5001
    del io


# ---------------------------------------------------------------------------------------------------------- #42
def _settlement(o, side, status, value, revision, known):
    outcome = None if status in ("void", "unresolved") else ("push" if status == "push" else
                                                              (side if status == "win" else
                                                               ("under" if side == "over" else "over")))
    return Settlement(settlement_id=f"st-{o.offer_id}-{side}-{revision}", platform=o.platform, offer_id=o.offer_id,
                      side=side, line=o.line, event_id=o.event_id, player_id=o.player_id, stat=o.stat, status=status,
                      outcome=outcome, source_value=value, source="espn", source_ref="t", rule_version="t",
                      revision=revision, supersedes=f"st-{o.offer_id}-{side}-{revision - 1}" if revision > 1 else None,
                      settled_at=known, observed_at=known, known_at=known)


def _shadow_fc(con, sid, rid, o):
    con.execute("INSERT INTO shadow_forecasts (shadow_id, model_version, league, stat, event_id, player_id, "
                "offer_record_id, platform, line, chosen_side, p_side, p_push) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, "sm-2", "nba", o.stat, o.event_id, o.player_id, rid, o.platform, o.line, "over", 0.6, 0.0))
    con.commit()


def test_42_corrected_settlement_supersedes_and_is_picked_up(tmp_path):
    from sports.model import shadow
    scon = store.connect()
    p = tmp_path / "shadow.db"
    try:
        o1, o2 = h.offer(oid="pp:1"), h.offer(oid="pp:2", line=30.5)
        r1, r2 = store.put(scon, o1)["record_id"], store.put(scon, o2)["record_id"]
        con = shadow.db(p)
        _shadow_fc(con, "A", r1, o1)
        _shadow_fc(con, "B", r2, o2)
        con.close()
        store.put(scon, _settlement(o1, "over", "loss", 18.0, 1, "2026-10-24T04:00:00Z"))
        shadow.settle(scon, p, now="2026-10-24T05:00:00Z")
        store.put(scon, _settlement(o1, "over", "win", 24.0, 2, "2026-10-24T06:00:00Z"))   # provider correction
        shadow.settle(scon, p, now="2026-10-24T07:00:00Z")
        rows = shadow.scorecard_rows(p)
        assert [r["status"] for r in rows] == ["win"]                                     # was loss AND win (twice)
        # case 1: every forecast already scored -> the correction is still applied
        p2 = tmp_path / "s2.db"
        con = shadow.db(p2)
        _shadow_fc(con, "C", r2, o2)
        con.close()
        store.put(scon, _settlement(o2, "over", "unresolved", None, 1, "2026-10-24T04:00:00Z"))
        shadow.settle(scon, p2, now="2026-10-24T05:00:00Z")
        assert [r["status"] for r in shadow.scorecard_rows(p2)] == ["unresolved"]
        store.put(scon, _settlement(o2, "over", "win", 31.0, 2, "2026-10-24T06:00:00Z"))
        shadow.settle(scon, p2, now="2026-10-24T07:00:00Z")
        assert [r["status"] for r in shadow.scorecard_rows(p2)] == ["win"]                # was stuck on unresolved
        assert shadow.status(p2)["counts"]["nba:points"]["settled"] == 1
    finally:
        scon.close()


# ---------------------------------------------------------------------------------------------------------- #9
@pytest.fixture(scope="module")
def hist(tmp_path_factory):
    from sports.model import forward
    gz = forward.ART_DIR / "history_ds1_2026-10-23.db.gz"
    if not gz.exists():
        pytest.skip("packaged history not present")
    db = tmp_path_factory.mktemp("h9") / "h.db"
    with gzip.open(gz, "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return db


def _up(gid, start, pid, team, opp, home, pos="G"):
    return {"game_id": gid, "player_id": pid, "team": team, "opp": opp, "home": home, "start_utc": start,
            "position": pos, "season": 2026, "season_type": "regular"}


def test_9_an_unplayed_game_is_never_a_prior_game_under_the_flag(hist, monkeypatch):
    import sqlite3
    from sports.model import featurize
    hcon = sqlite3.connect(hist)
    hcon.row_factory = sqlite3.Row
    try:
        r = hcon.execute("SELECT p.player_id, p.team, COUNT(*) n FROM nba_pg p GROUP BY p.player_id, p.team "
                         "ORDER BY n DESC LIMIT 1").fetchone()
        pid, team = r["player_id"], r["team"]
        opp = hcon.execute("SELECT team FROM nba_pg WHERE team != ? LIMIT 1", (team,)).fetchone()["team"]
        t1 = _up("t1", "2026-10-25T23:00:00Z", pid, team, opp, 1)
        t2 = _up("t2", "2026-10-26T23:00:00Z", pid, team, opp, 0)
        cols = ("prior_n", "starter_lag", "l10_min", "S_pts", "ew_min", "rest_days")

        def row(ups):
            df = featurize.nba_frame(hcon, 3, 10, upcoming=ups, as_of="2026-10-24T20:00:00Z", with_lags=False)
            i = [k for k in np.flatnonzero(df["is_up"] == 1) if df["game_id"][k] == "t2"][0]
            return {c: float(df[c][i]) for c in cols}, df
        alone, _ = row([t2])
        monkeypatch.delenv("SPORTS_FIX_PRIOR_GAMES", raising=False)
        both_off, _ = row([t1, t2])
        assert both_off["prior_n"] == alone["prior_n"] + 1                               # the bug, unchanged when off
        monkeypatch.setenv("SPORTS_FIX_PRIOR_GAMES", "1")
        both_on, df = row([t1, t2])
        for c in ("prior_n", "starter_lag", "l10_min", "S_pts", "ew_min"):
            a, b = alone[c], both_on[c]
            assert (a != a and b != b) or a == pytest.approx(b), c                       # t2 as if t1 did not exist
        assert sorted(df["game_id"][k] for k in np.flatnonzero(df["is_up"] == 1)) == ["t1", "t2"]
        assert featurize.split_upcoming([t1, t2]) == [[t1], [t2]]
        assert featurize.split_upcoming([t2]) == [[t2]]
    finally:
        hcon.close()


# ---------------------------------------------------------------------------------------------------------- #10
def test_10_projection_mean_flag(monkeypatch):
    from sports import board_view
    from sports.model import dists, forward
    lo, ks = dists.grid("rush_att")
    mu = 20.0
    p = np.array([np.exp(k * np.log(mu) - mu - np.sum(np.log(np.arange(1, k + 1)))) for k in ks], float)
    p = (p / p.sum())[None, :]
    t = dists.transform(p, lo, ks, [mu], 0.97, 0.88)[0]
    po, pu, _ = dists.line_probs(t, lo, 19.5)
    pmf_mean = float(dists.mean_of(t[None, :], ks)[0])
    monkeypatch.delenv("SPORTS_FIX_PROJ_MEAN", raising=False)
    assert forward.shown_projection_mean(t, ks, mu) == (mu, None)                        # off: unchanged
    assert board_view.lean_of(po, pu, mu, 19.5) is None                                  # the board's lean today
    assert board_view.lean_of(po, pu, pmf_mean, 19.5) == "under"                         # gradecheck's lean
    monkeypatch.setenv("SPORTS_FIX_PROJ_MEAN", "1")
    m, code = forward.shown_projection_mean(t, ks, mu)
    assert code == forward.PROJ_MEAN_CODE and m == pytest.approx(pmf_mean)
    assert board_view.lean_of(po, pu, m, 19.5) == "under"                                # board == gradecheck
    assert forward.shown_projection_mean(t, ks, mu, tw=True) == (mu, None)               # tw-1 already PMF mean
