"""2026-10-18 Render out-of-memory fix: the web process's caches are bounded by bytes."""
import membudget


def test_budget_lru_evicts_oldest_by_bytes_and_keeps_recent():
    c = membudget.BudgetLRU("t", max_mb=0.01)                       # 10 KB
    for i in range(50):
        c.put(i, "x", cost=1000)
    assert len(c) == 10 and c.stats()["mb"] <= 0.01 and c.evictions == 40
    assert c.get(0) is None and c.get(49) == "x"
    c.get(40)                                                       # recently used survives the next insert
    c.put(99, "y", cost=1000)
    assert c.get(40) == "x" and c.get(41) is None
    c.put("huge", "z", cost=10 ** 9)                                # larger than the budget: not cached
    assert c.get("huge") is None


def test_json_cost_tracks_size():
    small, big = membudget.json_cost({"a": 1}), membudget.json_cost({"a": "x" * 10000})
    assert big > 40000 > small


def test_memory_report_shape():
    import server
    m = server._memory_report()
    assert set(m["caches"]) == {"bo3_finished", "bo3_expiring", "contexts", "results"} and "server_rss_mb" in m
