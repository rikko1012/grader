import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def private_sports_data(monkeypatch, tmp_path):
    """Every test gets its own empty sports data folder (cache, forecast log, lines archive) and no network at all."""
    from sports import cache, config, net
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CACHE_DB", tmp_path / "cache.db")
    monkeypatch.setattr(config, "FORECAST_DB", tmp_path / "sports.db")
    monkeypatch.setattr(config, "LINES_ARCHIVE", tmp_path / "lines.jsonl")
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "output")
    monkeypatch.setattr(cache, "_local", threading.local())
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    for k in ("ODDSPAPI_API_KEY", "odds_papi_api", "SPORTS_ODDSPAPI", "SPORTS_ODDSPAPI_MONTHLY_REQUESTS",
              "SPORTS_ODDSPAPI_CS2_FLOOR"):
        monkeypatch.delenv(k, raising=False)

    def offline(*a, **k):
        raise ConnectionError("network disabled in tests")
    monkeypatch.setattr(net, "get", offline)
    monkeypatch.setattr(net, "_HOLD", {})                 # Retry-After holds never leak between tests
    yield
    con = getattr(cache._local, "con", None)
    if con is not None:
        con.close()


@pytest.fixture
def con():
    from sports import record
    c = record.connect()
    yield c
    c.close()


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
