"""2026-11-18 sports service restart with no record of why: exit / boot records, the RSS ring, the stale-snapshot
refusal (no heavy slip-pool build inside the service) and the shared research=1 body."""
import json
from types import SimpleNamespace

import pytest

from sports import config, prebuilt, service, service_life, web


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", "s" * 40)
    monkeypatch.setattr(prebuilt, "_MEM", __import__("collections").OrderedDict())
    monkeypatch.setattr(service_life, "_RING", __import__("collections").deque(maxlen=service_life.RING_MINUTES))
    monkeypatch.setattr(service_life, "_LAST", {"minute": None, "saved": 0.0})
    return tmp_path


def test_boot_without_an_exit_record_is_unclean_and_carries_the_rss_tail(data_dir):
    service_life.sample(250.0, now=1_000_000.0)
    service_life.sample(390.0, now=1_000_060.0)                     # second minute; ring saved on the first call
    service_life.sample(395.0, now=1_000_400.0)                     # past SAVE_EVERY_S: saved again
    rec = service_life.boot("svc-1")
    assert rec["previous_exit"] == "unclean" and [s["rss_mb"] for s in rec["previous_rss_tail"]] == [250.0, 390.0, 395.0]
    assert service_life.recent(10)[-1]["previous_exit"] == "unclean"


def test_watchdog_exit_record_is_named_on_the_next_boot(data_dir):
    service_life.exit_record("rss_over_max", **service._exit_detail(412.0))
    rec = service_life.boot("svc-1")
    prev = rec["previous_exit"]
    assert prev["reason"] == "rss_over_max" and prev["rss_mb"] == 412.0 and prev["max_mb"] == service.MAX_MB
    assert {"serving", "inflight", "web_cache", "encoded_bodies", "children"} <= set(prev)
    assert not (data_dir / "service_life/last_exit.json").exists()              # consumed once
    assert service_life.boot("svc-1")["previous_exit"] == "unclean"
    assert len(service_life.recent(10)) == 2


def test_exit_record_is_bounded_in_time_and_written_before_exit(data_dir, monkeypatch):
    import threading, time
    service._record_exit("rss_over_max", 401.0)
    assert json.loads((data_dir / "service_life/last_exit.json").read_text())["reason"] == "rss_over_max"
    gate = threading.Event()
    monkeypatch.setattr(service, "_exit_detail", lambda mb: gate.wait(10) or {})   # a lock that never frees
    t0 = time.time()
    service._record_exit("rss_over_max", 401.0, wait_s=0.2)
    assert time.time() - t0 < 2
    gate.set()


def test_records_never_raise_when_the_disk_write_fails(monkeypatch):
    monkeypatch.setattr(service_life, "_write", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    service_life.exit_record("rss_over_max", rss_mb=1)
    service_life.sample(1.0, now=5_000_000.0)
    assert service_life.trim() in (True, False)


def test_ops_lists_restarts_and_cgroup(data_dir):
    service_life.boot("svc-1")
    o = service.ops()
    assert o["restarts"] and "cgroup" in o


def test_stale_snapshot_answers_busy_instead_of_building_in_the_service(monkeypatch):
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, 60))
    prebuilt.write("tsnap", {("tsnap", "a"): {"v": 1}})
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, -1))                   # older than its max age
    calls = []
    with pytest.raises(web.Busy):
        web.cached(("tsnap", "a"), lambda: calls.append(1) or {"v": 2})
    assert calls == [] and web.cache_stats()["stale_snapshot_refused"] >= 1
    assert web.cached(("tsnap", "b"), lambda: calls.append(1) or {"v": 3}) == {"v": 3}   # never built: still built here
    monkeypatch.setenv("SPORTS_PREBUILT_STALE_BUILD", "1")                          # owner switch: old behaviour
    assert web.cached(("tsnap", "a"), lambda: calls.append(1) or {"v": 2}) == {"v": 2} and calls == [1, 1]


def test_research_body_is_byte_identical_to_the_former_per_request_encoding(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone
    from sports import expansion_board as eb, expansion_research as er
    monkeypatch.setenv("SCOUT_EXPANSION_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SCOUT_SOCCER_ENABLED", "1")
    monkeypatch.setattr(eb, "_PREPARED", {})
    monkeypatch.setattr(eb, "_RESEARCH_BODIES", {})
    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"sport": "soccer", "platform": "prizepicks", "offer_id": "1", "revision": "r", "event_id": "e",
           "player_id": "p", "stat": "shots", "scope": "full_game", "line": 1.5, "start": iso(now + timedelta(hours=2)),
           "observed_at": iso(now)}
    eb.save("soccer", [row], {}, iso(now))
    (tmp_path / "soccer/research.json").write_text(json.dumps(
        {"sport": "soccer", "at": iso(now), "players": {er.key(row): {"identity": None, "games": []}}}))
    req = SimpleNamespace(query={"sport": "soccer", "research": "1"})
    code, body, ctype = eb.http_board(req)
    _, data = eb.board(SimpleNamespace(query={"sport": "soccer"}))
    assert code == 200 and ctype == "application/json"
    assert body == web._json(200, er.attach("soccer", data))[1]
    assert eb.http_board(req)[1] is body                                          # shared, not re-encoded
    (tmp_path / "soccer/research.json").write_text(json.dumps({"sport": "soccer", "at": "x", "players": {}}))
    assert eb.http_board(req)[1] != body                                          # new research file, new body
