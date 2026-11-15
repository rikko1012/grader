"""Model lab: spec validation, chronological / point-in-time datasets, deterministic training, replay from saved
inputs (and failure when it does not reproduce), admin-only routes, exports, and isolation from production."""
import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sports import config, model_checks, store, web
from sports.lab import algorithms, dataset, jobs, sources
from sports.tests import helpers as h

GAMES = """CREATE TABLE games (league TEXT NOT NULL, game_id TEXT NOT NULL, season INTEGER, season_type TEXT,
         week INTEGER, start_utc TEXT, home TEXT, away TEXT, home_score REAL, away_score REAL, status TEXT,
         neutral INTEGER, venue TEXT, spread_home REAL, total_line REAL, odds_source TEXT, roof TEXT, temp REAL,
         wind REAL, espn_id TEXT, source TEXT, observed_at TEXT, known_at TEXT, PRIMARY KEY (league, game_id))"""
NBA_PG = """CREATE TABLE nba_pg (game_id TEXT NOT NULL, player_id TEXT NOT NULL, name TEXT, team TEXT,
         opp TEXT, home INTEGER, starter INTEGER, played INTEGER, dnp_reason TEXT, ejected INTEGER, position TEXT,
         min REAL, pts REAL, fgm REAL, fga REAL, fg3m REAL, fg3a REAL, ftm REAL, fta REAL, oreb REAL, dreb REAL,
         reb REAL, ast REAL, stl REAL, blk REAL, tov REAL, pf REAL, pm REAL, PRIMARY KEY (game_id, player_id))"""
SPEC = {"league": "nba", "stat": "points", "source": "ds-1", "algorithm": "ridge",
        "features": ["l5_mean", "l10_mean", "prior_n", "home", "rest_days", "min_l5"], "seed": 7,
        "train_start": "2024-10-01", "train_end": "2025-01-15", "tune_end": "2025-03-01", "test_end": "2025-05-01",
        "min_prior": 5, "cutoff": "2025-06-01T00:00:00Z"}


@pytest.fixture
def history():
    p = config.DATA_DIR / "model" / "history.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.execute(GAMES)
    con.execute(NBA_PG)
    rnd = random.Random(1)
    t0 = datetime(2024, 10, 1, 23, 30, tzinfo=timezone.utc)
    level = {"p1": 25.0, "p2": 14.0, "p3": 8.0}
    for g in range(200):
        start = t0 + timedelta(days=g)
        z = lambda t: t.strftime("%Y-%m-%dT%H:%M:%SZ")   # noqa: E731
        con.execute("INSERT INTO games (league, game_id, start_utc, status, known_at) VALUES ('nba',?,?,?,?)",
                    (f"g{g}", z(start), "final", z(start + timedelta(hours=4))))
        for i, (pid, lv) in enumerate(level.items()):
            played = 0 if (g + i) % 17 == 0 else 1
            pts = max(0, round(lv + 0.03 * g + rnd.gauss(0, 4))) if played else None
            con.execute("INSERT INTO nba_pg (game_id, player_id, home, starter, played, min, pts) VALUES (?,?,?,?,?,?,?)",
                        (f"g{g}", pid, (g + i) % 2, 1, played, 30.0 + i if played else 0.0, pts))
    con.commit()
    con.close()
    return p


def _work(con):
    out = []
    while True:
        r = jobs.work_once(con)
        if r is None:
            return out
        out.append(r)


def test_spec_validation():
    bad = [{**SPEC, "league": "mlb"}, {**SPEC, "stat": "steals"}, {**SPEC, "algorithm": "xgboost"},
           {**SPEC, "features": ["snap_pct_l5"]}, {**SPEC, "tune_end": "2025-01-01"}, {**SPEC, "seed": "7"},
           {**SPEC, "evil": 1}, {**SPEC, "features": []}, {**SPEC, "cutoff": "now"},
           {**SPEC, "algorithm": "prior_mean"}]
    for s in bad:
        with pytest.raises(ValueError):
            jobs.validate_spec(s)
    assert jobs.validate_spec(SPEC)["features"] == sorted(SPEC["features"])


def test_dataset_is_chronological_and_point_in_time(history):
    spec = jobs.validate_spec(SPEC)
    raw = sources.load("ds-1", "nba", "points", spec["cutoff"])
    rows = dataset.build(spec, raw)
    by = {s: [r for r in rows if r["split"] == s] for s in ("train", "tune", "test")}
    assert max(r["start_utc"] for r in by["train"]) < min(r["start_utc"] for r in by["tune"])
    assert max(r["start_utc"] for r in by["tune"]) < min(r["start_utc"] for r in by["test"])
    games = {}
    for r in rows:                                        # every row of one game in one partition
        games.setdefault(r["game_id"], set()).add(r["split"])
    assert all(len(v) == 1 for v in games.values())
    # features never see the target game: rewrite a target value and the same row's features do not move
    target = by["test"][10]
    raw2 = [dict(x, v=dict(x["v"])) for x in raw]
    for x in raw2:
        if x["row_key"] == target["row_key"]:
            x["v"]["points"] = 999.0
    again = {r["row_key"]: r for r in dataset.build(spec, raw2)}
    assert again[target["row_key"]]["x"] == target["x"] and again[target["row_key"]]["y"] == 999.0
    assert all(r["prior_games"] >= 5 for r in rows) and all(r["line_source"] == "reference" for r in rows)
    c = dataset.counts(rows)
    assert c["train"]["rows"] + c["tune"]["rows"] + c["test"]["rows"] == len(rows) and c["test"]["games"] > 0


def test_experiment_trains_evaluates_and_replays_from_saved_inputs(history):
    con = jobs.connect()
    eid, state = jobs.queue(con, SPEC, "admin")
    assert state == "queued" and jobs.queue(con, SPEC, "admin")[0] == eid            # idempotent
    assert _work(con)[0] == ("train", eid, "done")
    e = jobs.get(con, eid)
    res = e["result"]
    assert e["state"] == "done" and res["replay"]["match"] and res["production"] is False
    assert res["replaces_production"] is False and res["model_version"] == f"lab-{eid}"
    assert res["algorithm"] == "ridge@1" and res["seed"] == 7 and len(res["model"]["weights"]) >= 6
    assert [f["name"] for f in res["features"]] == sorted(SPEC["features"])
    assert all(f["definition"] and f["validated_effect"] is None for f in res["features"])
    m = res["metrics"]
    assert m["splits"]["test"]["rows"] == res["counts"]["test"]["rows"] > 0
    assert m["baseline"]["algorithm"] == "prior_mean@1" and m["baseline"]["splits"]["test"]["rows"] == \
        m["splits"]["test"]["rows"]
    tvb = m["test_vs_baseline"]
    assert tvb["ci95"][0] <= tvb["delta_mae"] <= tvb["ci95"][1] and "seed 7" in tvb["bootstrap"]
    cal = m["calibration"]["reference"]
    assert cal["scored"] > 0 and sum(b["n"] for b in cal["bins"]) == cal["scored"]
    # an admin replay later reproduces the same hash
    rid = jobs.queue_replay(con, eid, "admin")
    assert _work(con)[0] == ("replay", rid, True)
    # tampering with the recorded result makes the replay fail the experiment
    con.execute("UPDATE experiments SET outputs_hash='tampered' WHERE id=?", (eid,))
    jobs.queue_replay(con, eid, "admin")
    _work(con)
    assert jobs.get(con, eid)["state"] == "failed_replay"
    con.close()


def test_non_reproducible_training_fails_release(history, monkeypatch):
    calls = {"n": 0}
    real = algorithms.predict

    def drifting(model, rows):                            # a learner that changes between runs
        calls["n"] += 1
        return [(m + 0.001 * calls["n"], s) for m, s in real(model, rows)]
    monkeypatch.setattr(algorithms, "predict", drifting)
    con = jobs.connect()
    eid, _ = jobs.queue(con, {**SPEC, "seed": 8}, "admin")
    assert _work(con)[0] == ("train", eid, "failed_replay")
    e = jobs.get(con, eid)
    assert e["state"] == "failed_replay" and "did not reproduce" in e["error"]
    con.close()


def test_same_inputs_same_outputs_across_algorithms(history):
    spec = jobs.validate_spec({**SPEC, "algorithm": "shrunk_mean", "features": ["prior_n"]})
    rows = dataset.build(spec, sources.load("ds-1", "nba", "points", spec["cutoff"]))
    a, b = jobs._train(spec, rows), jobs._train(spec, json.loads(json.dumps(rows)))
    assert a[2] == b[2] and a[0]["k"] in algorithms.SHRINK_GRID


def test_lab_distribution_properties():
    for mu, sd in ((0.7, 0.9), (21.3, 6.2), (250.0, 60.0)):
        dist = lambda ln, mu=mu, sd=sd: algorithms.probs(mu, sd, ln)     # noqa: E731
        lines = [x / 2 for x in range(0, int(mu * 4) + 6)]
        assert model_checks.check_bounds_and_sum(dist, lines) == []
        assert model_checks.check_push(dist, lines) == []
        assert model_checks.check_monotonic(dist, lines) == []


def test_canonical_source_uses_offered_lines(monkeypatch):
    con = store.connect()
    store.put(con, h.event())
    pid = "nba:player:espn:3134907"
    for i in range(8):
        start = f"2026-10-{10 + i:02d}T23:30:00Z"
        store.put(con, h.event(eid=f"nba:espn:q{i}", start=start, status="final"))
        store.put(con, h.stat(pid=pid, part="pts", value=20.0 + i, eid=f"nba:espn:q{i}",
                              observed_at=f"2026-10-{11 + i:02d}T05:00:00Z"))
    store.put(con, h.offer(oid="pp:q7", line=24.5, event_id="nba:espn:q7", observed_at="2026-10-17T20:00:00Z"))
    con.close()
    spec = jobs.validate_spec({"league": "nba", "stat": "points", "source": "canonical", "algorithm": "prior_mean",
                               "features": [], "train_start": "2026-10-01", "train_end": "2026-10-16",
                               "tune_end": "2026-10-17", "test_end": "2026-10-20", "min_prior": 3,
                               "cutoff": "2026-10-24T00:00:00Z"})
    rows = dataset.build(spec, sources.load("canonical", "nba", "points", spec["cutoff"]))
    q7 = next(r for r in rows if r["game_id"] == "nba:espn:q7")
    assert q7["line"] == 24.5 and q7["line_source"] == "offered" and q7["line_ref"].startswith("offer:")
    assert q7["x"]["prior_mean"] == 23.0                   # games q0..q6, all known before q7's forecast time


def _req(path, role="admin", method="GET", body=None, **q):
    out = web.dispatch(web.Request(path=path, query=q, method=method, member="m_admin", role=role,
                                   body=json.dumps(body).encode() if body is not None else b""))
    return out[0], (json.loads(out[1]) if out[2].startswith("application/json") else out[1])


@pytest.mark.parametrize("sub,method", [("overview", "GET"), ("experiments", "POST"), ("experiment", "GET"),
                                        ("export", "GET"), ("replay", "POST")])
def test_every_lab_route_refuses_a_member_token(sub, method):
    """UI 1114: in service mode the lab API lives on the sports service; a member token gets 403 and no token 401 on every
    lab route (the esports server mints an admin-role token only for the owner: tests/test_sports_lab_owner_only.py)."""
    path = "/api/sports/admin/lab/" + sub
    assert _req(path, role="member", method=method, body={} if method == "POST" else None, id="lx-1")[0] == 403
    assert _req(path, role=None, method=method, body={} if method == "POST" else None, id="lx-1")[0] == 401


def test_lab_routes_are_admin_only_and_never_touch_production(history):
    assert _req("/api/sports/admin/lab/overview", role="member")[0] == 403
    assert _req("/api/sports/admin/lab/overview", role=None)[0] == 401
    con = store.connect()
    rid = store.put(con, h.offer(line=20.0))["record_id"]
    store.put(con, h.forecast(offer_snapshot_ids=(rid,)))
    before = store.counts(con)
    con.close()
    hist_mtime = history.stat().st_mtime_ns
    code, ov = _req("/api/sports/admin/lab/overview")
    assert code == 200 and ov["sources"]["ds-1"] and any(f["name"] == "l5_mean" for f in ov["features"])
    assert ov["registry"]["missing"]                       # the model registry API is not there yet - said so
    code, q = _req("/api/sports/admin/lab/experiments", method="POST", body=SPEC)
    assert code == 202 and q["state"] == "queued"
    assert _req("/api/sports/admin/lab/experiments", method="POST", body={**SPEC, "league": "x"})[0] == 400
    lc = jobs.connect()
    _work(lc)
    lc.close()
    code, e = _req("/api/sports/admin/lab/experiment", id=q["id"])
    assert code == 200 and e["state"] == "done" and e["spec"]["seed"] == 7
    code, csv_body = _req("/api/sports/admin/lab/export", id=q["id"], format="csv")
    lines = csv_body.decode().splitlines()
    assert code == 200 and lines[0].startswith("row_key,split") and len(lines) - 1 == sum(
        e["result"]["counts"][s]["rows"] for s in ("train", "tune", "test"))
    code, js = _req("/api/sports/admin/lab/export", id=q["id"])
    assert js["rows"] == len(lines) - 1 and js["predictions"][0]["source_refs"][0].startswith("ds-1:nba_pg:")
    code, r = _req("/api/sports/admin/lab/replay", method="POST", body={"id": q["id"]})
    assert code == 202
    assert _req("/api/sports/admin/lab/replay", method="POST", body={"id": "lx-none"})[0] == 404
    con = store.connect()
    assert store.counts(con) == before                     # no forecast / record written by the lab
    con.close()
    assert history.stat().st_mtime_ns == hist_mtime        # the model agent's data untouched
    assert not (config.DATA_DIR / "model" / "registry").exists()
