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


@pytest.fixture(autouse=True)
def private_letter_containment(tmp_path):
    """lc-1 (letter_containment.py): each test reads its own, empty containment state - never a DATA_DIR copy.
    SCOUT_LETTER_CONTAINMENT ships off (letters as before); tests/test_letter_containment.py turns it on itself."""
    from gradecore import containment as gc
    gc.set_path(tmp_path / "letter_containment.json")
    yield
    gc.set_path(None)


@pytest.fixture(autouse=True)
def private_map_chance_check(monkeypatch, tmp_path):
    """The HLTV map-chances check (mapchances.py, run by the veto-serve background thread) writes to the test's own folder,
    never to the repo root (a server-start test can let that thread run)."""
    import mapchances
    monkeypatch.setattr(mapchances, "CHECK_DB", tmp_path / "map_chance_check.db")
    monkeypatch.setattr(mapchances, "_DB_INIT", [None])


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
