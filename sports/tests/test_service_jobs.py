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
    monkeypatch.setattr(prebuilt, "_MEM", __import__("collections").OrderedDict())
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
    f = prebuilt._key_file("tsnap", ("tsnap", "a"))
    raw = f.read_bytes()
    f.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))                                # flip one bit
    monkeypatch.setattr(prebuilt, "_MEM", __import__("collections").OrderedDict())
    assert prebuilt.lookup(("tsnap", "a")) is prebuilt.MISS and prebuilt.STATS["bad_tag"] >= 1
    prebuilt.write("tsnap", {("tsnap", "a"): 1})
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, -1))                   # older than max age
    assert prebuilt.lookup(("tsnap", "a")) is prebuilt.MISS
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, 60))
    monkeypatch.setenv("SPORTS_TOKEN_SECRET", "other" * 10)                       # a file not signed with OUR key
    monkeypatch.setattr(prebuilt, "_MEM", __import__("collections").OrderedDict())
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
    monkeypatch.setattr(pm, "build", lambda c, lg, pf, now=None, policy="member", **kw: real(c, lg, pf, now=w.NOW,
                                                                                              policy=policy, **kw))
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
def test_schedule_names_every_worker_side_process_with_its_flag(monkeypatch):
    for f in ("SCOUT_SOCCER_ENABLED", "SCOUT_TENNIS_ENABLED"):      # opt-in soccer / tennis jobs: the base table only
        monkeypatch.delenv(f, raising=False)
    s = service.schedule()
    want = {"assistant": "SPORTS_ASSISTANT", "prebuilt": "SPORTS_API", "ui-snapshots-nba": "SPORTS_INGEST", "ui-snapshots-nfl": "SPORTS_INGEST",
            "mapping-audit": "SPORTS_INGEST",
            "slips-ingest": "SPORTS_INGEST", "ingest-nba": "SPORTS_INGEST", "ingest-nfl": "SPORTS_INGEST", "ingest-kalshi": "SPORTS_INGEST", "books": "SPORTS_INGEST",
            "settle": "SPORTS_INGEST", "slips-settle": "SPORTS_INGEST", "grades-daily": "SPORTS_INGEST", "model-forward": "SPORTS_FORECAST",
            "model-report": "SPORTS_FORECAST", "model-forecast": "SPORTS_TRAIN",
            "model-learn": "SPORTS_TRAIN", "model-snapshot": "SPORTS_TRAIN", "model-fit": "SPORTS_TRAIN",
            "lab": "SPORTS_TRAIN", "history": "SPORTS_INGEST", "archive": "SPORTS_INGEST",
            "archive-verify": "SPORTS_INGEST", "picks-ingest": "SPORTS_INGEST", "pregame-news": "SPORTS_INGEST", "games-backfill": "SPORTS_INGEST", "player-context": "SPORTS_INGEST", "picks-settle": "SPORTS_INGEST", "sheets": "SPORTS_INGEST",
            "results-public": "SPORTS_INGEST", "settle-post": "SPORTS_INGEST",      # 2026-11-08: own jobs
            "results-ledger": "SPORTS_INGEST"}                                       # settlepost-1120
    assert {k: v["flag"] for k, v in s.items()} == want
    assert s["slips-ingest"]["argv"][1:] == ["-m", "sports.slips", "ingest"]
    assert s["slips-settle"]["argv"][1:] == ["-m", "sports.slips", "settle"]
    assert s["grades-daily"]["argv"][1:] == ["-m", "sports.model.daily", "run"]      # REGISTER A9a
    assert s["picks-ingest"]["argv"][1:] == ["-m", "sports.picks", "ingest"]
    assert s["picks-settle"]["argv"][1:] == ["-m", "sports.picks", "settle"]
    assert s["assistant"]["argv"][1:] == ["-m", "sports.assistant.runner", "--once"] and s["assistant"]["trigger"]
    assert s["lab"]["argv"][1:] == ["-m", "sports.lab.runner", "--once"]
    assert s["prebuilt"]["every_s"] <= min(v[2] for v in web.SNAPSHOTS.values())  # refreshed before max age
    for name, spec in s.items():
        assert spec["timeout_s"], name                                         # every child is bounded in time
        assert spec["cap_mb"] > 0 and spec["cls"] in ("light", "heavy", "exclusive"), name
    assert {n for n, v in s.items() if v["cls"] == "exclusive"} == {"ingest-nba", "ingest-nfl", "model-forward", "model-forecast", "model-fit",
                                                                         "lab"}


def test_supervise_starts_one_scheduler_and_no_job_with_flags_off(monkeypatch):
    for f in ("SPORTS_INGEST", "SPORTS_API", "SPORTS_ASSISTANT", "SPORTS_TRAIN", "SPORTS_FORECAST",
              "SCOUT_SOCCER_ENABLED", "SCOUT_TENNIS_ENABLED"):
        monkeypatch.delenv(f, raising=False)
    from sports import scheduler
    monkeypatch.setattr(scheduler, "JOBS", {})
    stop = threading.Event()
    sch = service.supervise(stop)
    try:
        time.sleep(0.6)
        assert sch.running == {} and len(scheduler.JOBS) == 32 and not any(j["enabled"] for j in scheduler.JOBS.values())
    finally:
        stop.set()          # heavy-1118: a failed assert left this scheduler thread running into later replays


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
    assert "dockerfilePath" not in rnd and "Dockerfile.sports" not in rnd   # esports keeps the default Dockerfile
    tpl = (ROOT / "deploy" / "render.sports-api.yaml").read_text(encoding="utf-8")   # opt-in sports template only
    assert "dockerfilePath: ./Dockerfile.sports" in tpl and tpl.count("dockerfilePath") == 1


def test_the_snapshots_command_runs_in_a_child_process(tmp_path):
    import subprocess
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_")}
    env.update({"SPORTS_DATA_DIR": str(tmp_path), "SPORTS_TOKEN_SECRET": SECRET, "PYTHONPATH": str(ROOT)})
    r = subprocess.run([sys.executable, "-m", "sports.service", "snapshots"], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert r.returncode == 1 and out["slips_pool"]["error"] == "sports_store_not_ready"   # no store yet: reported


def test_a_streamed_snapshot_writes_each_key_before_the_next_is_built(monkeypatch):
    """2026-11-18 (prebuilt killed over its 288 MB cap 7 times): a generator builder's keys are written one at a time
    and read back exactly like a dict builder's."""
    monkeypatch.setitem(web.SNAPSHOTS, "tsnap", ("x:y", 10, 60))
    written = []
    real = prebuilt._write_tagged
    monkeypatch.setattr(prebuilt, "_write_tagged", lambda path, obj, key: written.append(obj["key"]) or real(path, obj, key))

    def gen():
        for i in range(3):
            assert written == [("tsnap", j) for j in range(i)]          # every earlier key already on disk
            yield ("tsnap", i), {"v": i}
    prebuilt.write("tsnap", gen())
    assert [prebuilt.lookup(("tsnap", i)) for i in range(3)] == [{"v": 0}, {"v": 1}, {"v": 2}]
    r = prebuilt.read("tsnap")
    assert set(r["items"]) == {("tsnap", i) for i in range(3)} and prebuilt.index("tsnap")["keys"] == [repr(("tsnap", i)) for i in range(3)]
