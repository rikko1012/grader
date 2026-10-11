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
