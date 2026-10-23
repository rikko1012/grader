"""Worker-side schedule (sports/service.py schedule / supervise), prebuilt snapshots (sports/prebuilt.py) and the
sports-only image packaging. No network."""
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from sports import prebuilt, service, store, web
from sports.tests import slip_world as w

ROOT = Path(__file__).resolve().parents[2]
SECRET = "s" * 40


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", SECRET)
    monkeypatch.setattr(web, "_CACHE", {})
    monkeypatch.setattr(prebuilt, "_MEM", {})
    yield


# ------------------------------------------------------------------------------------------- prebuilt snapshots
def test_snapshot_round_trip_keeps_exact_types(monkeypatch):
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, 60))
    val = {"legs": [{"prop_keys": ("p1|pts",), "ids": {1, 2}}]}
    prebuilt.write("tsnap", {("tsnap", "nba"): val})
    assert prebuilt.lookup(("tsnap", "nba")) == val
    assert isinstance(prebuilt.lookup(("tsnap", "nba"))["legs"][0]["prop_keys"], tuple)
    assert prebuilt.lookup(("tsnap", "nfl")) is prebuilt.MISS                     # key not in the snapshot
    assert prebuilt.lookup(("not_registered", "nba")) is prebuilt.MISS


def test_cached_serves_the_prebuilt_value_without_building(monkeypatch):
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, 60))
    prebuilt.write("tsnap", {("tsnap", "a"): {"v": 1}})
    calls = []
    assert web.cached(("tsnap", "a"), lambda: calls.append(1) or {"v": 2}) == {"v": 1} and calls == []
    assert web.cached(("tsnap", "b"), lambda: calls.append(1) or {"v": 2}) == {"v": 2} and calls == [1]   # miss


def test_stale_tampered_or_unsigned_snapshots_are_never_used(monkeypatch):
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, 60))
    prebuilt.write("tsnap", {("tsnap", "a"): 1})
    f = prebuilt.snap_dir() / "tsnap.snap"
    raw = f.read_bytes()
    f.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))                                # flip one bit
    monkeypatch.setattr(prebuilt, "_MEM", {})
    assert prebuilt.lookup(("tsnap", "a")) is prebuilt.MISS and prebuilt.STATS["bad_tag"] >= 1
    prebuilt.write("tsnap", {("tsnap", "a"): 1})
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, -1))                   # older than max age
    assert prebuilt.lookup(("tsnap", "a")) is prebuilt.MISS
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, 60))
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", "other" * 10)                       # a file not signed with OUR key
    monkeypatch.setattr(prebuilt, "_MEM", {})
    assert prebuilt.lookup(("tsnap", "a")) is prebuilt.MISS
    monkeypatch.delenv("SPORTS_TOKEN_SECRET")
    assert prebuilt.lookup(("tsnap", "a")) is prebuilt.MISS and not prebuilt.state()["enabled"]
    with pytest.raises(RuntimeError):
        prebuilt.write("tsnap", {})


def test_slips_pool_is_prebuilt_under_the_handlers_exact_cache_keys(monkeypatch):
    from sports.slips import pool as pm
    con = store.connect()
    w.world(con, [(f"h{i}", f"T{i}", i, "over" if i % 2 else "under", 0.6 + 0.01 * i) for i in range(1, 7)])
    con.close()
    real = pm.build
    monkeypatch.setattr(pm, "build", lambda c, lg, pf, now=None, policy="member": real(c, lg, pf, now=w.NOW,
                                                                                        policy=policy))
    rep = prebuilt.build(["slips_pool"])
    assert rep["slips_pool"]["ok"] and rep["slips_pool"]["keys"] == 4, rep
    import sports.api_slips as api
    built = []
    monkeypatch.setattr(pm, "build", lambda *a, **k: built.append(1))             # a request must not build
    p = api._pool("nba", "underdog")
    assert built == [] and len(p["legs"]) == 6 and p["platform"] == "underdog"


def test_snapshot_build_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setitem(web.SNAPSHOTS, "broken", ("sports.does_not_exist:fn", 10, 60))
    store.connect().close()
    rep = prebuilt.build(["broken"])
    assert rep["broken"]["ok"] is False and "ModuleNotFoundError" in rep["broken"]["error"]


# --------------------------------------------------------------------------------------------------- schedule
def test_schedule_names_every_worker_side_process_with_its_flag():
    s = service.schedule()
    want = {"ingest-loop": "SPORTS_INGEST", "snapshots": "SPORTS_API", "slips-ingest": "SPORTS_INGEST",
            "slips-settle": "SPORTS_INGEST", "assistant-runner": "SPORTS_ASSISTANT", "lab-runner": "SPORTS_TRAIN"}
    assert {k: v["flag"] for k, v in s.items()} == want
    assert s["slips-ingest"]["argv"][1:] == ["-m", "sports.slips", "ingest"]
    assert s["slips-settle"]["argv"][1:] == ["-m", "sports.slips", "settle"]
    assert s["assistant-runner"]["argv"][1:] == ["-m", "sports.assistant.runner"]
    assert s["lab-runner"]["argv"][1:] == ["-m", "sports.lab.runner"]
    assert s["snapshots"]["every_s"] <= min(v[2] for v in web.SNAPSHOTS.values())  # refreshed before max age
    for name, spec in s.items():
        assert (spec["every_s"] is None) == (spec["timeout_s"] is None), name      # periodic jobs always time out


def test_supervise_starts_only_flagged_jobs(monkeypatch):
    for f in ("SPORTS_INGEST", "SPORTS_API", "SPORTS_ASSISTANT", "SPORTS_TRAIN"):
        monkeypatch.delenv(f, raising=False)
    monkeypatch.setattr(service, "JOBS", {})
    stop = threading.Event()
    assert service.supervise(stop) == {}
    assert all(not j["enabled"] for j in service.JOBS.values()) and len(service.JOBS) == 6
    stop.set()


def test_periodic_job_repeats_and_a_hung_job_is_killed(monkeypatch):
    monkeypatch.setattr(service, "JOBS", {})
    monkeypatch.setenv("SPORTS_API", "1")
    stop = threading.Event()
    jobs = {"quick": {"argv": [sys.executable, "-c", "pass"], "every_s": 0.2, "timeout_s": 5, "flag": "SPORTS_API"},
            "hung": {"argv": [sys.executable, "-c", "import time; time.sleep(60)"], "every_s": 30, "timeout_s": 1.5,
                     "flag": "SPORTS_API"}}
    ts = service.supervise(stop, jobs)
    deadline = time.time() + 30
    while time.time() < deadline and (service.JOBS["quick"]["starts"] < 3 or not service.JOBS["hung"]["last_exit"]):
        time.sleep(0.1)
    stop.set()
    for t in ts.values():
        t.join(15)
    assert service.JOBS["quick"]["starts"] >= 3 and service.JOBS["quick"]["last_exit"]["code"] == 0
    assert service.JOBS["hung"]["last_exit"]["code"] == "timeout" and service.JOBS["hung"]["last_exit"]["ran_s"] < 10


def test_job_alerts(monkeypatch):
    base = {"flags": {"SPORTS_INGEST": False, "SPORTS_API": True}, "worker": {}, "feeds": {"platforms": {}},
            "rss_mb": 1, "rss_max_mb": 900, "service": {"requests": 0, "rejected_busy": 0, "errors_5xx": 0},
            "db_pool": {}, "token_configured": True,
            "prebuilt": {"snapshots": {"slips_pool": {"age_s": 10, "max_age_s": 90}}}}
    assert service.alerts(dict(base, jobs={})) == []
    codes = {a["code"] for a in service.alerts(dict(base, jobs={
        "slips-ingest": {"enabled": True, "every_s": 300, "failures_in_a_row": 3},
        "assistant-runner": {"enabled": True, "every_s": None, "starts": 6, "last_exit": {"ran_s": 3}}},
        prebuilt={"snapshots": {"slips_pool": {"age_s": None, "max_age_s": 90}}}))}
    assert codes == {"job_failing", "job_crash_loop", "snapshot_stale"}


# -------------------------------------------------------------------------------------------------- packaging
def test_esports_image_is_unchanged_and_sports_deps_are_sports_only():
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "anthropic" not in req
    dock = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements-sports" not in dock and 'CMD ["python", "server.py"]' in dock
    sreq = (ROOT / "requirements-sports.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in sreq and "anthropic" in sreq
    sdock = (ROOT / "Dockerfile.sports").read_text(encoding="utf-8")
    assert "requirements-sports.txt" in sdock and "sports.service" in sdock
    rnd = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "dockerfilePath: ./Dockerfile.sports" in rnd and rnd.count("dockerfilePath") == 1   # esports keeps default


def test_the_snapshots_command_runs_in_a_child_process(tmp_path):
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_")}
    env.update({"SPORTS_DATA_DIR": str(tmp_path), "SPORTS_TOKEN_SECRET": SECRET, "PYTHONPATH": str(ROOT)})
    r = subprocess.run([sys.executable, "-m", "sports.service", "snapshots"], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 1 and out["slips_pool"]["error"] == "sports_store_not_ready"   # no store yet: reported
