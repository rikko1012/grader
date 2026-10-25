import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def no_live_prizepicks(monkeypatch):
    """Tests never call PrizePicks itself: the direct read fails unless a test supplies a response."""
    import prizepicks

    def offline():
        raise ConnectionError("network disabled in tests")
    monkeypatch.setattr(prizepicks, "_direct", offline)


@pytest.fixture(autouse=True)
def private_hltv_store(monkeypatch, tmp_path):
    """Each test gets its own empty stored copy of HLTV answers (never the real one on disk)."""
    import hltv_store
    monkeypatch.setattr(hltv_store, "DB_PATH", tmp_path / "hltv_store.db")
    monkeypatch.setattr(hltv_store, "_local", __import__("threading").local())
    monkeypatch.setattr(hltv_store, "_LAST", {})


def pin_clock(monkeypatch, at, modules):
    """Run a test as if the wall clock read `at` (it keeps ticking from there). For tests built on market data recorded at a
    fixed moment: without this they fail once the recorded matches have started (2026-10-12 review)."""
    import time as _time, datetime as _dt
    real = _time.time
    offset = at.timestamp() - real()
    monkeypatch.setattr(_time, "time", lambda: real() + offset)

    class Clock(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime.fromtimestamp(real() + offset, tz)

        @classmethod
        def utcnow(cls):
            return _dt.datetime.utcfromtimestamp(real() + offset)
    for m in modules:
        if getattr(m, "datetime", None) is _dt.datetime:
            monkeypatch.setattr(m, "datetime", Clock)


def pytest_configure(config):
    config.addinivalue_line("markers", "lw1: run with the live lw-1 line-informed chance (grader.CONFIG['lw1']) switched on")


@pytest.fixture(autouse=True)
def model_path_unless_lw1(request, monkeypatch):
    """lw-1 (2026-10-25) sits on top of the performance model. The suite's grader tests check the model path underneath it
    (lw-1 off); tests marked @pytest.mark.lw1 run with the live lw-1 config (tests/test_lw1.py)."""
    import grader
    import lw1
    monkeypatch.setitem(grader.CONFIG, "lw1", dict(lw1.PARAMS) if request.node.get_closest_marker("lw1") else None)
