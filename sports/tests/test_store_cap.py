"""2026-11-05: the 4,000 MB cap from the old 5 GB disk paused every NBA/NFL feed for ~14 h on a 105 GB disk, silently."""
import types

import pytest

from sports import worker


def test_cap_is_at_least_half_the_disk(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(total=105_583e6, used=8_393e6, free=97_190e6))
    cap, free = worker.store_cap_mb(env_cap=4000, base=tmp_path)
    assert cap == pytest.approx(52_791.5) and free == pytest.approx(97_190)
    monkeypatch.setattr(shutil, "disk_usage", lambda p: types.SimpleNamespace(total=5_000e6, used=1e6, free=4_000e6))
    assert worker.store_cap_mb(env_cap=4000, base=tmp_path)[0] == 4000       # a small disk keeps the configured cap


def test_low_free_disk_still_pauses(monkeypatch):
    monkeypatch.setattr(worker, "data_mb", lambda: 100.0)
    monkeypatch.setattr(worker, "store_cap_mb", lambda: (50_000.0, 1_000.0))
    with pytest.raises(worker.BudgetExceeded, match="disk free"):
        worker._check_budget()


def test_a_fully_paused_ingest_is_a_failed_job(monkeypatch):
    monkeypatch.setattr(worker.flags, "require", lambda f: None)
    monkeypatch.setattr(worker.Jobs, "run", lambda self, key, job, fn: {"state": "failed", "error": "sports data 9000 MB > cap 4000 MB: ingestion paused"})
    monkeypatch.setattr(worker.store, "size_mb", lambda: 1.0)
    a = types.SimpleNamespace(sources="pp", leagues=["nba", "nfl"], bucket_min=1)
    with pytest.raises(SystemExit) as e:
        worker.cmd_ingest(a, None)
    assert e.value.code == 3
