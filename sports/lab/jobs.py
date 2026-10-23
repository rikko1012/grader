"""lab.db (<SPORTS_DATA_DIR>/lab/lab.db): the lab's own queue and results - never the canonical store, never the model
registry.

  experiments  id = "lx-" + hash(spec incl. data cutoff) (queueing the same spec twice is idempotent); state
               queued -> running -> done | failed | failed_replay; attempts; heartbeat (a dead run is re-queued and
               resumes from its saved dataset)
  datasets     the exact dataset snapshot each experiment trained on (zlib JSON) + its sha256 (inputs_hash)
  predictions  per-row predictions (zlib JSON) + outputs_hash = sha256(model JSON + predictions)
  replays      every replay: when, from which inputs hash, the outputs hash it produced, match yes / no

A result counts only if retraining from the SAVED dataset reproduces outputs_hash exactly (checked right after
training and on every admin replay); otherwise the experiment is failed_replay and is not releasable.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import zlib

from .. import config as sconfig
from ..contracts import now_utc
from . import ALGORITHMS, LAB_VERSION, dataset, features, sources

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, spec TEXT NOT NULL, state TEXT NOT NULL,
         created_by TEXT, created_at TEXT NOT NULL, started_at TEXT, heartbeat_at TEXT, finished_at TEXT,
         attempts INTEGER NOT NULL DEFAULT 0, error TEXT, result TEXT, inputs_hash TEXT, outputs_hash TEXT,
         kind TEXT NOT NULL DEFAULT 'train')""",
    "CREATE INDEX IF NOT EXISTS experiments_state ON experiments (state, created_at)",
    "CREATE TABLE IF NOT EXISTS datasets (id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, rows INTEGER, body BLOB NOT NULL)",
    "CREATE TABLE IF NOT EXISTS predictions (id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, body BLOB NOT NULL)",
    """CREATE TABLE IF NOT EXISTS replays (rid INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, at TEXT NOT NULL,
         requested_by TEXT, inputs_hash TEXT, outputs_hash TEXT, expected TEXT, match INTEGER, state TEXT NOT NULL,
         error TEXT)""",
]
MAX_ATTEMPTS = 3
STALE_S = 1800
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def path():
    return sconfig.DATA_DIR / "lab" / "lab.db"


def connect(p=None):
    p = p or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=5, check_same_thread=False, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    for s in SCHEMA:
        con.execute(s)
    return con


def _pack(o):
    return zlib.compress(json.dumps(o, sort_keys=True, separators=(",", ":"), default=str).encode(), 6)


def _unpack(b):
    return json.loads(zlib.decompress(b))


# ------------------------------------------------------------------------------------------------------- specs
def validate_spec(d: dict) -> dict:
    allowed = {"league", "stat", "source", "algorithm", "features", "seed", "train_start", "train_end", "tune_end",
               "test_end", "min_prior", "cutoff"}
    extra = set(d) - allowed
    if extra:
        raise ValueError(f"unknown spec field(s): {sorted(extra)}")
    s = {"league": d.get("league"), "stat": d.get("stat"), "source": d.get("source") or "ds-1",
         "algorithm": d.get("algorithm") or "ridge", "features": d.get("features") or [],
         "seed": d.get("seed", 20261023), "min_prior": d.get("min_prior", 5)}
    if s["league"] not in ("nba", "nfl"):
        raise ValueError("league must be nba or nfl")
    if s["stat"] not in sources.TARGETS[s["league"]]:
        raise ValueError(f"stat must be one of {sources.TARGETS[s['league']]}")
    if s["source"] not in ("ds-1", "canonical"):
        raise ValueError("source must be ds-1 or canonical")
    if s["algorithm"] not in ALGORITHMS:
        raise ValueError(f"algorithm must be one of {sorted(ALGORITHMS)}")
    if not isinstance(s["features"], list) or not all(isinstance(f, str) for f in s["features"]) \
            or len(s["features"]) > 14:
        raise ValueError("features must be a list of feature names")
    s["features"] = sorted(set(s["features"]))
    if s["algorithm"] == "ridge" and not s["features"]:
        raise ValueError("ridge needs at least one feature")
    uses = {"prior_mean": set(), "shrunk_mean": {"prior_n"}}.get(s["algorithm"])
    if uses is not None and not set(s["features"]) <= uses:           # never list a feature the model ignores
        raise ValueError(f"{s['algorithm']} uses only {sorted(uses) or 'no features'}; select features with ridge")
    dataset.validate_features(s)
    if isinstance(s["seed"], bool) or not isinstance(s["seed"], int) or not 0 <= s["seed"] < 2 ** 32:
        raise ValueError("seed must be an integer 0 .. 2^32-1")
    if isinstance(s["min_prior"], bool) or not isinstance(s["min_prior"], int) or not 1 <= s["min_prior"] <= 40:
        raise ValueError("min_prior must be 1 .. 40")
    for k in ("train_start", "train_end", "tune_end", "test_end"):
        v = d.get(k)
        if not isinstance(v, str) or not _DAY.match(v):
            raise ValueError(f"{k} must be YYYY-MM-DD")
        s[k] = v
    if not (s["train_start"] < s["train_end"] < s["tune_end"] < s["test_end"]):
        raise ValueError("dates must satisfy train_start < train_end < tune_end < test_end (chronological)")
    cutoff = d.get("cutoff") or now_utc()
    if not isinstance(cutoff, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", cutoff):
        raise ValueError("cutoff must be a UTC time YYYY-MM-DDTHH:MM:SSZ")
    s["cutoff"] = cutoff
    return s


def queue(con, spec, by):
    s = validate_spec(spec)
    eid = "lx-" + dataset.digest(s)[:16]
    con.execute("INSERT OR IGNORE INTO experiments (id, spec, state, created_by, created_at) VALUES (?,?,?,?,?)",
                (eid, json.dumps(s, sort_keys=True), "queued", by, now_utc()))
    return eid, get(con, eid)["state"]


def queue_replay(con, eid, by):
    e = get(con, eid)
    if e is None:
        raise KeyError(eid)
    if e["state"] not in ("done", "failed_replay"):
        raise ValueError(f"experiment is {e['state']} - nothing to replay yet")
    cur = con.execute("INSERT INTO replays (id, at, requested_by, state) VALUES (?,?,?, 'queued')", (eid, now_utc(), by))
    return cur.lastrowid


def get(con, eid):
    r = con.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["spec"] = json.loads(d["spec"])
    d["result"] = json.loads(d["result"]) if d["result"] else None
    d["replays"] = [dict(x) for x in con.execute("SELECT rid, at, requested_by, inputs_hash, outputs_hash, expected, "
                                                 "match, state, error FROM replays WHERE id=? ORDER BY rid",
                                                 (eid,)).fetchall()]
    return d


def listing(con, limit=50):
    out = []
    for r in con.execute("SELECT id, spec, state, created_by, created_at, finished_at, error, inputs_hash, "
                         "outputs_hash, result FROM experiments ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall():
        spec, res = json.loads(r["spec"]), json.loads(r["result"]) if r["result"] else {}
        test = (res.get("metrics") or {}).get("splits", {}).get("test", {})
        out.append({"id": r["id"], "state": r["state"], "created_by": r["created_by"], "created_at": r["created_at"],
                    "finished_at": r["finished_at"], "error": r["error"], "league": spec["league"],
                    "stat": spec["stat"], "source": spec["source"], "algorithm": spec["algorithm"],
                    "seed": spec["seed"], "test_mae": test.get("mae"), "test_rows": test.get("rows"),
                    "test_vs_baseline": (res.get("metrics") or {}).get("test_vs_baseline"),
                    "replay_verified": res.get("replay", {}).get("match")})
    return out


def predictions(con, eid):
    r = con.execute("SELECT body FROM predictions WHERE id=?", (eid,)).fetchone()
    return _unpack(r["body"]) if r else None


# --------------------------------------------------------------------------------------------- the pipeline
def _train(spec, rows):
    from . import algorithms                            # numpy only in the runner process
    model = algorithms.fit(spec, rows)
    preds = algorithms.predict(model, rows)
    return model, preds, dataset.digest({"model": model, "preds": preds})


def _load_dataset(con, eid):
    r = con.execute("SELECT sha256, body FROM datasets WHERE id=?", (eid,)).fetchone()
    if r is None:
        return None, None
    rows = _unpack(r["body"])
    if dataset.digest(rows) != r["sha256"]:
        raise RuntimeError("saved dataset does not match its hash")
    return rows, r["sha256"]


def run_experiment(con, eid, source_path=None, beat=lambda: None):
    e = get(con, eid)
    spec = e["spec"]
    rows, ih = _load_dataset(con, eid)                    # resume: a dataset already saved is reused, never rebuilt
    if rows is None:
        raw = sources.load(spec["source"], spec["league"], spec["stat"], spec["cutoff"], source_path)
        beat()
        rows = dataset.build(spec, raw)
        ih = dataset.digest(rows)
        con.execute("INSERT OR REPLACE INTO datasets (id, sha256, rows, body) VALUES (?,?,?,?)",
                    (eid, ih, len(rows), _pack(rows)))
        con.execute("UPDATE experiments SET inputs_hash=? WHERE id=?", (ih, eid))
    beat()
    import numpy as np
    from . import algorithms, evaluate
    model, preds, oh = _train(spec, rows)
    metrics = evaluate.evaluate(spec, model, rows, preds)
    beat()
    saved, _ = _load_dataset(con, eid)                    # replay check from the SAVED inputs, not memory
    _, _, oh2 = _train(spec, saved)
    match = oh2 == oh
    pred_rows = []
    for r, (mu, sd) in zip(rows, preds):
        po = pu = pp = None
        if r["line"] is not None:
            po, pu, pp = (round(x, 6) for x in algorithms.probs(mu, sd, r["line"]))
        pred_rows.append({"row_key": r["row_key"], "split": r["split"], "game_id": r["game_id"],
                          "player_id": r["player_id"], "start_utc": r["start_utc"], "forecast_at": r["forecast_at"],
                          "actual": r["y"], "pred_mean": mu, "pred_sd": sd, "line": r["line"],
                          "line_source": r["line_source"], "line_record_id": r["line_ref"], "p_over": po,
                          "p_under": pu, "p_push": pp, "source_refs": r["refs"]})
    con.execute("INSERT OR REPLACE INTO predictions (id, sha256, body) VALUES (?,?,?)",
                (eid, oh, _pack(pred_rows)))
    result = {"model_version": f"lab-{eid}", "lab_version": LAB_VERSION, "production": False,
              "replaces_production": False, "algorithm": model["algorithm"], "seed": spec["seed"],
              "features": [features.FEATURES[f] for f in spec["features"]],
              "always_computed": ["prior_mean (eligibility, baseline, reference line)"],
              "feature_registry": features.REGISTRY_VERSION, "dates": {k: spec[k] for k in (
                  "train_start", "train_end", "tune_end", "test_end", "cutoff")},
              "counts": dataset.counts(rows), "model": model, "metrics": metrics,
              "inputs_hash": ih, "outputs_hash": oh, "numpy": np.__version__,
              "replay": {"at": now_utc(), "match": match, "outputs_hash": oh2}}
    state = "done" if match else "failed_replay"
    con.execute("INSERT INTO replays (id, at, requested_by, inputs_hash, outputs_hash, expected, match, state) VALUES "
                "(?,?,?,?,?,?,?, 'done')", (eid, now_utc(), "training", ih, oh2, oh, int(match)))
    con.execute("UPDATE experiments SET state=?, result=?, outputs_hash=?, finished_at=?, error=? WHERE id=?",
                (state, json.dumps(result, default=str), oh, now_utc(),
                 None if match else "replay from saved inputs did not reproduce the result", eid))
    return state


def run_replay(con, rid):
    r = con.execute("SELECT * FROM replays WHERE rid=?", (rid,)).fetchone()
    e = get(con, r["id"])
    try:
        rows, ih = _load_dataset(con, r["id"])
        if rows is None:
            raise RuntimeError("no saved dataset")
        _, _, oh = _train(e["spec"], rows)
        match = oh == e["outputs_hash"]
        con.execute("UPDATE replays SET inputs_hash=?, outputs_hash=?, expected=?, match=?, state='done' WHERE rid=?",
                    (ih, oh, e["outputs_hash"], int(match), rid))
        if not match:
            con.execute("UPDATE experiments SET state='failed_replay', error=? WHERE id=?",
                        ("replay from saved inputs did not reproduce the result", r["id"]))
        return match
    except Exception as ex:                             # noqa: BLE001
        con.execute("UPDATE replays SET state='failed', match=0, error=? WHERE rid=?", (str(ex)[:300], rid))
        con.execute("UPDATE experiments SET state='failed_replay', error=? WHERE id=?",
                    (f"replay failed: {str(ex)[:200]}", r["id"]))
        return False


def claim(con):
    """Next work item: a queued replay first (cheap), else the oldest queued (or stale running) experiment."""
    con.execute("BEGIN IMMEDIATE")
    try:
        r = con.execute("SELECT rid FROM replays WHERE state='queued' ORDER BY rid LIMIT 1").fetchone()
        if r:
            con.execute("UPDATE replays SET state='running' WHERE rid=?", (r["rid"],))
            con.execute("COMMIT")
            return ("replay", r["rid"])
        stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - STALE_S))
        r = con.execute("SELECT id, attempts FROM experiments WHERE state='queued' OR (state='running' AND "
                        "heartbeat_at < ?) ORDER BY created_at LIMIT 1", (stale,)).fetchone()
        if r is None:
            con.execute("COMMIT")
            return None
        if r["attempts"] >= MAX_ATTEMPTS:
            con.execute("UPDATE experiments SET state='failed', error=COALESCE(error, 'too many attempts'), "
                        "finished_at=? WHERE id=?", (now_utc(), r["id"]))
            con.execute("COMMIT")
            return ("skip", r["id"])
        con.execute("UPDATE experiments SET state='running', attempts=attempts+1, started_at=?, heartbeat_at=? "
                    "WHERE id=?", (now_utc(), now_utc(), r["id"]))
        con.execute("COMMIT")
        return ("train", r["id"])
    except Exception:
        con.execute("ROLLBACK")
        raise


def work_once(con, source_path=None):
    item = claim(con)
    if item is None:
        return None
    kind, ref = item
    if kind == "replay":
        return ("replay", ref, run_replay(con, ref))
    if kind == "skip":
        return ("skip", ref, None)

    def beat():
        con.execute("UPDATE experiments SET heartbeat_at=? WHERE id=?", (now_utc(), ref))
    try:
        return ("train", ref, run_experiment(con, ref, source_path, beat))
    except Exception as ex:                             # noqa: BLE001 - recorded; a retry resumes from the dataset
        e = get(con, ref)
        final = e["attempts"] >= MAX_ATTEMPTS or isinstance(ex, (ValueError, FileNotFoundError))
        con.execute("UPDATE experiments SET state=?, error=?, finished_at=? WHERE id=?",
                    ("failed" if final else "queued", f"{type(ex).__name__}: {str(ex)[:300]}", now_utc(), ref))
        return ("train", ref, "failed" if final else "requeued")
