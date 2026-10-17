import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def private_lol_data(monkeypatch, tmp_path):
    """Every test gets its own empty LoL data folder (cache, history, output) and no network at all."""
    from lol import cache, config, net
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr(config, "HISTORY_DB", tmp_path / "history.db")
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "output")
    monkeypatch.setattr(cache, "_local", threading.local())
    monkeypatch.delenv("LOL_DB_DSN", raising=False)

    def offline(*a, **k):
        raise ConnectionError("network disabled in tests")
    monkeypatch.setattr(net, "_send", offline)


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def db():
    from lol import history
    d = history.DB(dsn="")
    yield d
    d.close()


@pytest.fixture
def pg_db():
    """A PostgreSQL history database - only when LOL_TEST_PG_DSN points at a scratch database."""
    dsn = os.environ.get("LOL_TEST_PG_DSN")
    if not dsn:
        pytest.skip("LOL_TEST_PG_DSN not set")
    from lol import history
    d = history.DB(dsn=dsn)
    for t in ("lol_player_games", "lol_games", "lol_sync_state"):
        d.execute(f"DELETE FROM {t}")
    d.commit()
    yield d
    d.close()
