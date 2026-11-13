"""Regression coverage for the November 13 Render memory incident."""
import pytest
import membudget
import server


@pytest.fixture
def results(monkeypatch):
    monkeypatch.setattr(server, "_RESULTS", {})
    monkeypatch.setattr(server, "_RES_BYTES", [0, 0])
    monkeypatch.setattr(server, "_SWEPT", [0])
    monkeypatch.setattr(server, "RESULTS_MAX_BYTES", 100)
    return server._RESULTS


def test_incoming_response_evicts_before_exceeding_budget(results):
    server._store_result("a", 200, b"a" * 60, 60, 60)
    server._store_result("b", 200, b"b" * 60, 60, 60)
    assert list(results) == ["b"]
    assert server._RES_BYTES[0] == 60


def test_replacing_response_does_not_count_old_bytes(results):
    for _ in range(20):
        server._store_result("a", 200, b"a" * 60, 60, 60)
    assert server._RES_BYTES[0] == 60


def test_encoded_cache_counts_retained_uncompressed_response(results):
    server._store_result("a", 200, b"a" * 60, 60, 60)
    server._store_result("encoded", (b"x" * 80, '"etag"'), b"z" * 10, 60, 60)
    assert list(results) == ["encoded"]
    assert server._RES_BYTES[0] == 90


def test_one_oversized_answer_preserves_existing_singleflight_contract(results):
    server._store_result("huge", 200, b"a" * 200, 60, 60)
    assert results["huge"][2] == b"a" * 200
    server._store_result("small", 200, b"b", 60, 60)
    assert list(results) == ["small"]
    assert server._RES_BYTES[0] == 1


def test_lower_cache_cap_evicts_oldest_preserving_recent_values():
    cache = membudget.BudgetLRU("test", 1)
    cache.put("old", 1, cost=400000)
    cache.put("recent", 2, cost=400000)
    cache.cap_mb(.5)
    assert cache.get("old") is None and cache.get("recent") == 2
    cache.cap_mb(2)
    assert cache.max_bytes == 500000


def test_worker_does_not_treat_dirty_or_shared_pages_as_free():
    m = {"container_limit_mb": 2147, "container_mb": 2100,
         "container_detail_mb": {"inactive_file": 600, "dirty": 100, "shmem": 30}}
    assert not membudget.worker_headroom(m)["allowed"]
    m["container_detail_mb"] = {"inactive_file": 600}
    assert membudget.worker_headroom(m)["allowed"]
    assert membudget.worker_headroom({})["allowed"]


def test_learning_defers_then_retries_and_releases_both_locks(monkeypatch, tmp_path):
    import learning
    monkeypatch.setattr(learning, "DB_PATH", tmp_path / "learning.db")
    monkeypatch.setattr(learning, "STATE", {})
    memory = {"container_limit_mb": 2147, "container_mb": 1900}
    monkeypatch.setattr(membudget, "process_memory", lambda: memory)
    calls = []
    monkeypatch.setattr(learning, "_cycle_safe", lambda: calls.append(1))
    assert server.learning_cycle_once() is False
    assert not calls and not server._LEARNING_SPAWN.locked()
    assert learning.STATE["memory_admission"]["reason"] == "memory pressure"
    memory["container_mb"] = 1200
    assert server.learning_cycle_once() is True and calls == [1]


def test_container_caps_preserve_smaller_existing_budgets(monkeypatch):
    caches = [membudget.BudgetLRU("test", mb) for mb in (150, 20, 100)]
    monkeypatch.setattr(server.bo3, "_MEM", caches[0])
    monkeypatch.setattr(server.bo3, "_TMEM", caches[1])
    monkeypatch.setattr(server.cs2_scout, "_CONTEXT", caches[2])
    monkeypatch.setattr(membudget, "process_memory", lambda: {"container_limit_mb": 2147})
    server.apply_container_cache_limits()
    assert [c.max_bytes for c in caches] == [75000000, 20000000, 60000000]
