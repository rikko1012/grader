"""Regression tests for the 2026-11-06 bug sweep, non-sports jobs (#18 learning cycles, #22 shadow forecast log,
#57 workspace read slots). Each reproduces the report's failure case."""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest


# ---------------------------------------------------------------------------------------------------------- #18
def _fake_children(monkeypatch):
    import subprocess
    started, release = [], threading.Event()

    class P:
        returncode, stdout, stderr = 0, "", ""

    def run(cmd, **kw):
        started.append(cmd)
        release.wait(5)
        return P()
    monkeypatch.setattr(subprocess, "run", run)
    return started, release


def _wait_for(cond):
    for _ in range(250):
        if cond():
            return
        threading.Event().wait(0.02)


def test_18_admin_refresh_and_timer_never_start_two_learning_cycles(monkeypatch, tmp_path):
    """The report's case: an admin fresh=1 cycle is running when the 15-minute timer fires."""
    import learning
    import server
    started, release = _fake_children(monkeypatch)
    monkeypatch.setattr(learning, "dashboard", lambda: {})
    learning.STATE.update(running=False, last_error=None)
    st, _, _ = server.learning_http("/admin/learning.json", {"fresh": "1"})       # admin click -> child A
    assert st == 200
    _wait_for(lambda: started)
    assert server.learning_cycle_once() is False                                 # the timer: no child B
    server.learning_http("/admin/learning.json", {"fresh": "1"})                 # a quick second click: nothing
    threading.Event().wait(0.2)
    assert len(started) == 1
    release.set()
    _wait_for(lambda: not server._LEARNING_SPAWN.locked())
    assert server.learning_cycle_once() is True and len(started) == 2           # free again afterwards


def test_18_two_quick_refresh_clicks_start_one_cycle(monkeypatch):
    import learning
    import server
    started, release = _fake_children(monkeypatch)
    monkeypatch.setattr(learning, "dashboard", lambda: {})
    learning.STATE.update(running=False, last_error=None)
    ts = [threading.Thread(target=server.learning_http, args=("/admin/learning.json", {"fresh": "1"}))
          for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(5)
    _wait_for(lambda: started)
    threading.Event().wait(0.2)
    assert len(started) == 1                                                     # was up to one per click
    release.set()
    _wait_for(lambda: not server._LEARNING_SPAWN.locked())


# ---------------------------------------------------------------------------------------------------------- #22
def test_22_a_failed_forecast_write_is_retried_on_the_next_refresh(monkeypatch, tmp_path):
    monkeypatch.setenv("CS2_DATA_DIR", str(tmp_path))
    import moneyline
    monkeypatch.setattr(moneyline, "_FC_LAST", {})
    monkeypatch.setattr(moneyline, "_FC_LAST_TP", {})
    now = datetime(2026, 10, 11, 23, 0, tzinfo=timezone.utc)
    m = {"match_id": "hltv:1", "tie_possible": False, "disagreement": False, "start": "2026-10-12T01:00:00Z",
         "teams": {"a": {"name": "A"}, "b": {"name": "B"}},
         "baseline": {"platform": "kalshi", "prob_a": 0.6, "reason": "r", "rule": "x"}}
    monkeypatch.setattr(moneyline, "_assemble", lambda srcs, now: [m])
    monkeypatch.setattr(moneyline, "_sides_tp_row", lambda *a, **k: None)
    monkeypatch.setattr(moneyline, "_candidate_row", lambda *a, **k: None)
    monkeypatch.setattr(moneyline, "tp_policy", lambda: None)

    def locked():
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(moneyline, "_connect", locked)
    with pytest.raises(sqlite3.OperationalError):
        moneyline.log_forecasts([], now)
    assert moneyline._FC_LAST == {}                                # was marked written although it was lost
    written = []

    class Con:
        def executemany(self, sql, rows):
            written.extend(rows)

        def commit(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(moneyline, "_connect", lambda: Con())
    assert moneyline.log_forecasts([], now + timedelta(minutes=2)) == 1   # was 0 for 30 minutes
    assert written and written[0][0] == "hltv:1" and "hltv:1" in moneyline._FC_LAST


# ---------------------------------------------------------------------------------------------------------- #57
def test_57_a_failed_reader_open_gives_its_slot_back(tmp_path):
    import workspace_store as ws
    s = ws.Store(tmp_path / "t.db", start=False)
    real = s._open

    def broken():
        raise sqlite3.OperationalError("unable to open database file")
    s._open = broken
    for _ in range(ws.READ_SLOTS):
        with pytest.raises(sqlite3.OperationalError):
            with s.reader():
                pass
    assert s._n_readers == 0                                       # was READ_SLOTS (every slot lost)
    s._open = real
    with s.reader(wait=0.5) as con:                                # was Busy('no read slot') for good
        assert con.execute("SELECT 1").fetchone()[0] == 1
    closed = []

    class Half:
        def execute(self, sql):
            raise sqlite3.OperationalError("disk I/O error")

        def close(self):
            closed.append(1)
    s._open = lambda: Half()
    while not s._readers.empty():                                  # force a fresh open
        s._readers.get_nowait().close()
        s._n_readers -= 1
    with pytest.raises(sqlite3.OperationalError):
        with s.reader():
            pass
    assert closed == [1] and s._n_readers == 0                    # the half-opened connection is closed
