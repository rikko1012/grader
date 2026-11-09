"""lpf-1 from the LoL worker without loading it (flag LOL_LPF1; default off). Memory rule (2026-11-09): the esports container
sits at its limit, so the lpf-1 history store, features and trees (build peak ~300 MB) never enter the web process or the
LoL worker. The worker's forecast cycle (lol/center/forecast.run) asks for the lines it needs; ONE short-lived child process
(`python -m lol.lpf1_live predict`) builds the projector from the history database, answers every request, writes a small
JSON and exits. Answers are reused while the history version and the artifact stay the same, so a quiet cycle starts no
child. A failed / timed-out child answers nothing: those lines are skipped for the cycle and the last saved forecast stands
(never another model's grade, never a cached failure).

This module is stdlib only (no numpy, no lol.lpf / lp1) so importing it costs the worker nothing.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VERSION = "lpf-1"
REPO = Path(__file__).resolve().parents[1]
ARTIFACT = Path(__file__).resolve().with_name("lpf1_model.json.gz")
STATS = ("kills", "assists", "deaths")
CHILD_TIMEOUT_S = int(os.environ.get("LOL_LPF1_CHILD_TIMEOUT_S", "600"))


def enabled():
    return os.environ.get("LOL_LPF1", "0") == "1"


def live_stats():
    """Stats the flag moves (LOL_LPF1_STATS, default kills + assists: the two markets accepted 2026-11-09)."""
    raw = os.environ.get("LOL_LPF1_STATS", "kills,assists")
    return tuple(s for s in (x.strip() for x in raw.split(",")) if s in STATS)


def artifact_sha(path=None):
    p = Path(path or ARTIFACT)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


class Pending(Exception):
    """The line's lpf-1 answer is not computed yet (collected for the child)."""


class Unavailable(Exception):
    """No lpf-1 answer this cycle (child failed / timed out / the target could not be projected)."""


def req_key(t, stat, scope, lam_game, line):
    return json.dumps([t, stat, scope, round(float(lam_game), 6), float(line)], sort_keys=True, default=str)


class RequestProjector:
    """Stands in for lpf1_live.Projector inside forecast_one: target() packs the pre-match fields, project() returns the
    child's answer, or records the request (Pending) / raises Unavailable."""

    def __init__(self, answers):
        self.answers = answers          # {key: result dict or None (no answer this cycle)}
        self.requests = {}

    def target(self, sid, pid, tid, opp, role, day, league, tournament, best_of, tournament_page=None):
        return {"match": sid, "pid": pid, "team": tid, "opp": opp, "role": role, "day": int(day), "league": league,
                "tournament": tournament, "best_of": int(best_of or 1), "tournament_page": tournament_page}

    def project(self, t, stat, scope, lam_game, line):
        k = req_key(t, stat, scope, lam_game, line)
        if k in self.answers:
            if self.answers[k] is None:
                raise Unavailable(k)
            return self.answers[k]
        self.requests[k] = {"t": t, "stat": stat, "scope": scope, "lam_game": float(lam_game), "line": float(line)}
        raise Pending(k)


def history_path(hdb):
    """The SQLite file behind a lol/history.DB (None for Postgres: the child then uses the configured DSN)."""
    if getattr(hdb, "kind", "sqlite") != "sqlite":
        return None
    for r in hdb.query("PRAGMA database_list"):
        if r.get("name") == "main" and r.get("file"):
            return r["file"]
    return None


def run_child(requests, history_db, center_db, artifact=None, timeout=None, runner=None):
    """-> (answers {key: result or None}, info). The child gets the requests in a temp file and writes its answers to another."""
    runner = runner or subprocess.run
    info = {"requests": len(requests)}
    with tempfile.TemporaryDirectory(prefix="lpf1_") as d:
        rq, out = Path(d) / "req.json", Path(d) / "out.json"
        rq.write_text(json.dumps(requests), encoding="utf-8")
        cmd = [sys.executable, "-m", "lol.lpf1_live", "predict", "--req", str(rq), "--out", str(out), "--center", str(center_db)]
        if history_db:
            cmd += ["--history", str(history_db)]
        if artifact:
            cmd += ["--artifact", str(artifact)]
        env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
        t0 = time.time()
        try:
            r = runner(cmd, cwd=str(REPO), env=env, capture_output=True, text=True, timeout=timeout or CHILD_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            info.update(error="timeout", seconds=round(time.time() - t0, 1))
            return {k: None for k in requests}, info
        info.update(code=r.returncode, seconds=round(time.time() - t0, 1))
        if r.returncode != 0 or not out.exists():
            info["error"] = (r.stderr or "")[-300:] or "no output"
            return {k: None for k in requests}, info
        res = json.loads(out.read_text(encoding="utf-8"))
    info.update({k: res.get(k) for k in ("peak_rss_mb", "build_s", "artifact_sha", "answered")})
    ans = res.get("answers") or {}
    return {k: ans.get(k) for k in requests}, info


# answers of the current history version (worker memory: a few KB per line)
_ANS = {"version": None, "map": {}}


def answers_for(version):
    if _ANS["version"] != version:
        _ANS["version"], _ANS["map"] = version, {}
    return _ANS["map"]


def remember(version, answers):
    """Only real answers are kept; a None (failure) is never cached."""
    m = answers_for(version)
    m.update({k: v for k, v in answers.items() if v is not None})
