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


def test_cgroup_detail_events_pressure_and_peak(tmp_path):
    """2026-10-18: the container's true peak, limit hits and pressure stalls are read (cgroup v2 files, TEST values)."""
    (tmp_path / "memory.peak").write_text("2100400000\n")
    (tmp_path / "memory.events").write_text("low 0\nhigh 0\nmax 7\noom 0\noom_kill 0\n")
    (tmp_path / "memory.pressure").write_text("some avg10=0.50 avg60=0.20 avg300=0.10 total=1234\n"
                                              "full avg10=0.10 avg60=0.05 avg300=0.01 total=99\n")
    out = {}
    membudget._cgroup_extra(str(tmp_path), True, out)
    assert out["container_peak_mb"] == 2100.4
    assert out["container_events"]["max"] == 7 and out["container_events"]["oom_kill"] == 0
    assert out["container_pressure"] == {"some_avg10": 0.5, "some_avg60": 0.2, "some_avg300": 0.1, "full_avg10": 0.1,
                                         "full_avg60": 0.05, "full_avg300": 0.01}
    missing = {}
    membudget._cgroup_extra(str(tmp_path / "none"), True, missing)                  # files absent: nothing, no error
    assert missing == {}


def test_minute_samples_are_bounded_and_track_peaks(monkeypatch):
    seq = iter([1800.0, 2100.4, 1900.0])
    monkeypatch.setattr(membudget, "process_memory", lambda: {"container_mb": next(seq), "container_anon_mb": 900.0,
                                                              "container_file_cache_mb": 900.0, "container_detail_mb": {"dirty": 3.0}})
    monkeypatch.setattr(membudget, "processes", lambda: {"web": 600.0, "lol": 300.0})
    monkeypatch.setattr(membudget, "HISTORY", membudget.deque(maxlen=2))
    monkeypatch.setattr(membudget, "PEAK", {})
    for _ in range(3):
        s = membudget.sample()
    assert len(membudget.HISTORY) == 2 and s["rss_lol"] == 300.0 and s["dirty"] == 3.0
    assert membudget.PEAK["total"]["mb"] == 2100.4 and membudget.PEAK["anon"]["mb"] == 900.0


def test_memory_report_shape():
    import server
    m = server._memory_report()
    assert set(m["caches"]) == {"bo3_finished", "bo3_expiring", "contexts", "results"} and "server_rss_mb" in m


def test_slip_leg_rejected_when_side_not_offered(monkeypatch):
    import server
    rows = [{"player": "TeSeS", "prop": "hs", "scope": "12", "line": 16.0, "odds_type": "standard", "over_available": True, "under_available": False},
            {"player": "kyousuke", "prop": "hs", "scope": "12", "line": 19.5, "odds_type": "standard", "over_available": True, "under_available": True}]
    monkeypatch.setattr(server.prizepicks, "board", lambda: {"rows": rows})
    monkeypatch.setattr(server.underdog, "board", lambda: {"rows": []})
    leg = lambda nick, side, line: {"nick": nick, "prop": "hs", "scope": "12", "line": line, "side": side, "book": "prizepicks"}
    assert "not offered" in server.slip_leg_problem(leg("TeSeS", "Less", 16))
    assert server.slip_leg_problem(leg("TeSeS", "More", 16)) is None
    assert server.slip_leg_problem(leg("kyousuke", "Less", 19.5)) is None
    assert "not on the platform" in server.slip_leg_problem(leg("kyousuke", "Less", 20.5))    # moved / withdrawn line
