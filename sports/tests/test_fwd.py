"""Forward forecasts, inference only (sports/model/fwd.py, flag SPORTS_FORECAST): flag gating (no fit / learn /
retrain without SPORTS_TRAIN), frozen-artifact hash verification, packaged history, incremental 0-record rerun, replay,
bounded snapshot reads (memory does not follow stored-forecast count), scheduler entry + exclusivity."""
import gzip
import json
import shutil
import sqlite3
import tracemalloc
from dataclasses import replace

import pytest

from sports import config, flags, scheduler, store
from sports.model import datasets, forward, fwd, jobs, learning, pipeline, study
from sports.tests.test_line_correctness_model import _counts, _seen, world  # noqa: F401 (fixture)

AT = "2026-01-31T21:00:00Z"


def _package(art, hist_db, tmp_path):
    """A manifest + history package for the test world (same layout as the committed one)."""
    gz = art / "history_test.db.gz"
    with open(hist_db, "rb") as f, gzip.GzipFile(filename="", mode="wb", fileobj=open(gz, "wb"), mtime=0) as g:
        shutil.copyfileobj(f, g)
    nba = json.loads((art / "sm1_nba.json").read_text(encoding="utf-8"))
    files = {p.name: {"sha256": fwd._file_sha(p)} for p in sorted(art.iterdir()) if p.name != "MANIFEST.json"}
    man = {"version": "fwd-test", "artifact_shas": {"nba": nba["sha"]}, "files": files,
           "history": {"file": gz.name, "db_sha256": fwd._file_sha(hist_db)}}
    (art / "MANIFEST.json").write_text(json.dumps(man), encoding="utf-8")
    return man


@pytest.fixture
def fworld(world, tmp_path, monkeypatch):
    scon, hcon, art, rid1 = world
    hcon.commit()
    hist = tmp_path / "history.db"
    snap = tmp_path / "hist_snapshot.db"
    src = sqlite3.connect(hist)
    src.execute("VACUUM INTO ?", (str(snap),))
    src.close()
    _package(art, snap, tmp_path)
    monkeypatch.setattr(fwd, "ART_DIR", art)
    monkeypatch.setattr(fwd, "MANIFEST", art / "MANIFEST.json")
    monkeypatch.setenv("SPORTS_FORECAST", "1")
    monkeypatch.delenv("SPORTS_TRAIN", raising=False)
    monkeypatch.setattr(store, "opened", _shared(scon))
    yield scon, hcon, art, rid1


def _shared(scon):
    from contextlib import contextmanager

    @contextmanager
    def opened(path=None, read_only=False):
        yield scon
    return opened


def _no_training(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("training code reached from the inference-only path")
    for mod, name in ((forward, "fit"), (learning, "retrain"), (learning, "on_settled"), (study, "run"),
                      (datasets, "nba_crawl"), (datasets, "nfl_build")):
        monkeypatch.setattr(mod, name, boom)


# ---------------------------------------------------------------------------------------------------- flag gating
def test_forecast_flag_off_refuses_and_writes_nothing(fworld, monkeypatch):
    scon, *_ = fworld
    monkeypatch.delenv("SPORTS_FORECAST")
    with pytest.raises(flags.FlagOff):
        fwd.run(["nba"], forecast_at=AT)
    with pytest.raises(flags.FlagOff):
        fwd.main(["run", "nba"])
    assert _counts(scon) == (0, 0)


def test_forecast_without_train_runs_inference_only_and_training_stays_refused(fworld, monkeypatch):
    scon, *_ = fworld
    _no_training(monkeypatch)
    out = fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    assert out["leagues"]["nba"]["written"]["sm-1"] == 1 and out["forecasts_added"] == 1
    assert out["withheld_check"].startswith("ok")
    for argv in (["fit"], ["learn"], ["forecast", "nba"], ["study"], ["dataset", "nba"], ["snapshot"]):
        with pytest.raises(flags.FlagOff):                     # SPORTS_FORECAST=1 does not open any `train` job
            jobs.main(argv)


def test_forecasts_carry_probabilities_and_versions_with_grades_withheld_and_zero_stake(fworld):
    scon, *_ = fworld
    fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    (rid, fc), = store.forecasts(scon, model_version="sm-1")
    assert fc.grade is None and fc.stake_units == 0.0
    assert abs(fc.p_over + fc.p_under + fc.p_push - 1) < 1e-9 and fc.projection_mean is not None
    assert fc.artifact_version.startswith("sm-1:") and fc.data_version.startswith("ds-1:")
    run = json.loads((config.DATA_DIR / "model" / "api" / "forecast_run.json").read_text())
    adm = json.loads((config.DATA_DIR / "model" / "api" / "admin_learning.json").read_text())
    assert adm["forecast_run"] == run and run["forecasts_added"] == 1 and run["artifacts"]["manifest"] == "fwd-test"


# ------------------------------------------------------------------------------------------------- artifact hashes
def test_committed_artifact_package_verifies():
    v = fwd.verify_artifacts()
    assert v["artifact_shas"] == {"nba": "5ec8adf73a6dad18", "nfl": "cc56aeb8b88c31d8", "mc-1": "8119ce6b61539999"}
    man = fwd.manifest()
    assert set(man["files"]) >= {"sm1_nba.json", "sm1_nfl.json", "market_mc1.json", "registry.json",
                                 "model_config_nba.json", "model_config_nfl.json", man["history"]["file"]}


def test_committed_history_package_installs_and_matches_the_artifacts_dataset(tmp_path):
    st = fwd.install_history(dest=tmp_path / "h.db")
    assert st["state"] == "installed"
    assert fwd.install_history(dest=tmp_path / "h.db")["state"] == "present"
    con = fwd.open_history(tmp_path / "h.db")
    cov = datasets.coverage(con)
    con.close()
    for sport in ("nba", "nfl"):
        art = json.loads((fwd.ART_DIR / f"sm1_{sport}.json").read_text(encoding="utf-8"))
        assert cov[sport] == art["dataset"], sport


def test_crlf_checkout_hashes_like_the_lf_blob(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_bytes(b'{\n "x": 1\n}\n')
    b.write_bytes(b'{\r\n "x": 1\r\n}\r\n')
    assert fwd._file_sha(a) == fwd._file_sha(b)


def test_tampered_artifact_refuses_the_run_and_writes_nothing(fworld):
    scon, hcon, art, _ = fworld
    p = art / "sm1_nba.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    mk = next(iter(d["markets"]))
    d["markets"][mk]["cal"] = [1.0, 1.06]                     # a refit / edited artifact
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(fwd.ArtifactError):
        fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    assert _counts(scon) == (0, 0)


def test_tampered_history_package_is_refused(fworld, tmp_path):
    scon, hcon, art, _ = fworld
    man = json.loads((art / "MANIFEST.json").read_text())
    man["history"]["db_sha256"] = "0" * 64
    with pytest.raises(fwd.ArtifactError):
        fwd.install_history(man=man, art_dir=art, dest=tmp_path / "x" / "h.db")
    assert not (tmp_path / "x" / "h.db").exists()


# -------------------------------------------------------------------------------------------- incremental + replay
def test_second_run_adds_zero_records_and_earlier_forecasts_replay_bit_identically(fworld):
    scon, hcon, art, rid1 = fworld
    fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    c1 = _counts(scon)
    out = fwd.run(["nba"], forecast_at="2026-01-31T21:30:00Z", log=lambda *x: None)
    assert _counts(scon) == c1 and out["forecasts_added"] == 0
    assert out["leagues"]["nba"]["unchanged"]["sm-1"] == 1
    a = store.get(scon, rid1)
    rid2 = store.put(scon, _seen(replace(a, line=18.5), "2026-01-31T21:40:00Z"))["record_id"]
    out = fwd.run(["nba"], forecast_at="2026-01-31T21:45:00Z", log=lambda *x: None)
    assert out["forecasts_added"] == 1 and _counts(scon) == (c1[0] + 1, c1[1] + 1)
    for _, f in store.forecasts(scon, model_version="sm-1"):
        orig, again = forward.replay(f.forecast_id, scon)
        assert again == orig
    # the snapshot shows the CURRENT revision's forecast only (board / card / slip read the same revision)
    snap = json.loads((config.DATA_DIR / "model" / "api" / "forecasts_nba.json").read_text())
    assert [v["offer_record_id"] for v in snap["forecasts"] if v["model_version"] == "sm-1"] == [rid2]


# --------------------------------------------------------------------------------------------------- bounded reads
def _add_past_forecasts(scon, n):
    """n synthetic forecasts of an event that has already been played (copies of a stored row, new ids)."""
    cols = [r[1] for r in scon.execute("PRAGMA table_info(records)") if r[1] != "rid"]
    src = scon.execute(f"SELECT {','.join(cols)} FROM records WHERE kind='forecast' LIMIT 1").fetchone()
    ri, sk, ev, ch = (cols.index(c) for c in ("record_id", "source_key", "event_id", "content_hash"))
    icon = forward.inputs_db()
    inp = icon.execute("SELECT * FROM inputs LIMIT 1").fetchone()
    rows, irows = [], []
    for k in range(n):
        r = list(src)
        r[ri] = r[sk] = f"fc-syn-{k}"
        r[ev] = f"nba:espn:past{k % 40}"
        r[ch] = f"{r[ch]}~syn{k}"
        rows.append(r)
        irows.append((f"fc-syn-{k}",) + tuple(inp[1:]))
    scon.executemany(f"INSERT INTO records ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
    scon.commit()
    icon.executemany(f"INSERT INTO inputs VALUES ({','.join('?' * len(inp))})", irows)
    icon.commit()
    icon.close()


def _snapshot_cost(monkeypatch, now):
    decoded = []
    real = store._rec
    monkeypatch.setattr(store, "_rec", lambda row: (decoded.append(1), real(row))[1])
    tracemalloc.start()
    pipeline.write_snapshot(now=now)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    monkeypatch.setattr(store, "_rec", real)
    return len(decoded), peak


def test_snapshot_memory_and_reads_do_not_grow_with_stored_forecasts(fworld, monkeypatch):
    scon, *_ = fworld
    fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    monkeypatch.setattr(store, "connect", lambda path=None, read_only=False: _NoClose(scon))
    n0, peak0 = _snapshot_cost(monkeypatch, AT)
    _add_past_forecasts(scon, 20000)
    n1, peak1 = _snapshot_cost(monkeypatch, AT)
    assert n1 == n0                                            # no stored forecast of a played event is decoded
    assert peak1 < peak0 * 1.5 + 512 * 1024, (peak0, peak1)    # 20,000 more forecasts: same peak (not ~x100)
    fc = json.loads((config.DATA_DIR / "model" / "api" / "forecasts_nba.json").read_text())["forecasts"]
    assert len(fc) == 1


class _NoClose:
    def __init__(self, con):
        self._c = con

    def close(self):
        pass

    def __getattr__(self, k):
        return getattr(self._c, k)


# ------------------------------------------------------------------------------------------------------- scheduler
def test_scheduler_forward_job_is_gated_by_sports_forecast_alone_and_exclusive(monkeypatch):
    monkeypatch.setenv("SPORTS_FORECAST", "1")
    monkeypatch.delenv("SPORTS_TRAIN", raising=False)
    monkeypatch.delenv("SPORTS_INGEST", raising=False)
    J = scheduler.default_jobs()
    spec = J["model-forward"]
    assert spec["flag"] == "SPORTS_FORECAST" and spec["cls"] == scheduler.EXCLUSIVE
    assert spec["argv"][1:4] == ["-m", "sports.model.fwd", "run"]
    assert 100 + spec["cap_mb"] <= 450                         # service + reservation inside the 512 MB container
    s = scheduler.Scheduler(jobs=J, budget=310, now=lambda: 0.0)
    on = {n for n, st in scheduler.JOBS.items() if st["enabled"]}
    assert "model-forward" in on
    assert not on & {"model-fit", "model-learn", "model-forecast", "model-snapshot", "lab"}   # training stays off
    # never beside another job: waits while anything runs, and blocks every start while it runs
    s.running = {"settle": (None, 0.0)}
    assert not s._fits("model-forward")
    s.running = {"model-forward": (None, 0.0)}
    assert not s._fits("settle") and not s._fits("slips-ingest") and not s._fits("prebuilt")


# ------------------------------------------------------------------------------------------- settlement scorecard
def test_settle_scorecard_reads_only_settled_events_and_never_retrains(fworld, monkeypatch):
    from sports.contracts import Settlement
    scon, hcon, art, rid1 = fworld
    fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    _add_past_forecasts(scon, 5000)                              # played events, no settlement: never decoded
    fc = next(f for _, f in store.forecasts(scon, model_version="sm-1") if f.forecast_id.startswith("fc-") and
              "syn" not in f.forecast_id)
    offer = store.get(scon, rid1)
    st = Settlement(settlement_id="s1", platform=offer.platform, offer_id=offer.offer_id, side=fc.chosen_side,
                    line=offer.line, event_id=offer.event_id, player_id=offer.player_id, stat="points",
                    status="win" if fc.chosen_side == "over" else "loss", outcome="over", source_value=20.0,
                    source="espn", source_ref="x", rule_version="test", settled_at="2026-02-01T04:00:00Z")
    store.put(scon, st)
    monkeypatch.setattr(learning, "retrain", lambda *a, **k: (_ for _ in ()).throw(AssertionError("retrain")))
    monkeypatch.setattr(forward, "fit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fit")))
    decoded = []
    real = store._rec
    monkeypatch.setattr(store, "_rec", lambda row: (decoded.append(1), real(row))[1])
    r1 = learning.on_settled(scon, now="2026-02-02T00:00:00Z")
    assert r1["scorecard_rows_added"] == 1 and r1["events_read"] == 1
    assert len(decoded) < 20, len(decoded)                     # this event's settlement + forecasts, not 5,000
    assert learning.on_settled(scon, now="2026-02-02T00:00:00Z")["scorecard_rows_added"] == 0
    # a corrected settlement (new revision) supersedes the earlier scorecard row - once
    store.put(scon, replace(st, settlement_id="s2", revision=2, supersedes="s1", source_value=12.0, status="loss" if st.status == "win" else "win",
                            outcome="under"))
    assert learning.on_settled(scon, now="2026-02-02T00:00:00Z")["scorecard_rows_added"] == 1
    sc = learning.scorecard()
    assert sum(r["settled"] for r in sc if r["model_version"] == "sm-1") == 1


# ---------------------------------------------------------------------------------------------- history refresh
def test_refresh_queues_only_days_that_are_surely_final():
    days = fwd.refresh_days("2026-10-24T07:00:00Z")            # 32 h lag: Oct 22 is the last complete US day
    assert days[0] == "day:20261020" and days[-1] == "day:20261022"
    assert fwd.refresh_days("2026-10-24T09:00:00Z")[-1] == "day:20261023"
    assert fwd.refresh_days("2026-01-31T21:00:00Z") == []       # before the current season: nothing


def test_refresh_failure_never_blocks_the_forecast(fworld, monkeypatch):
    scon, *_ = fworld
    monkeypatch.setattr(datasets, "nba_crawl", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("espn down")))
    monkeypatch.setattr(fwd, "refresh_days", lambda now: ["day:20260130"])
    out = fwd.run(["nba"], forecast_at=AT, log=lambda *x: None)
    assert out["history"]["nba_refresh"]["state"] == "failed" and out["forecasts_added"] == 1
    monkeypatch.setenv("SPORTS_FORECAST_REFRESH", "0")
    assert fwd.refresh_nba(AT) == {"state": "off"}
