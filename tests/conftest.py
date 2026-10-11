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
