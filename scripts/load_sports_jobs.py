"""Peak memory / wall time of every scheduled sports job, each run alone as a child process (the way the service's
scheduler runs it), against a COPY of a real data folder. Starter plan sizing (sports/RUNBOOK.md "Starter").

usage: python load_sports_jobs.py <code_dir> <data_dir_copy> <out.jsonl> [job ...] [--offline]
  --offline: HTTP(S)_PROXY points at a dead port, so jobs that would call providers measure their no-network pass.
Each job: peak RSS of the child INCLUDING its own children (psutil, 20 ms samples), wall time, exit code, and the
store sizes before / after. Results append to out.jsonl (one line per job).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import psutil

code, data, out = sys.argv[1], sys.argv[2], sys.argv[3]
offline = "--offline" in sys.argv
names = [a for a in sys.argv[4:] if not a.startswith("--")]
PY = sys.executable
JOBS = {
    "prebuilt": [PY, "-m", "sports.service", "snapshots"],
    "ui-snapshots": [PY, "-m", "sports.snapshots", "build", "nba", "nfl"],
    "slips-ingest": [PY, "-m", "sports.slips", "ingest"],
    "slips-settle": [PY, "-m", "sports.slips", "settle"],
    "ingest-pickem": [PY, "-m", "sports.worker", "ingest", "nba", "nfl", "--sources", "espn,pp,ud,injuries,oddsapi"],
    "ingest-kalshi": [PY, "-m", "sports.worker", "ingest", "nba", "nfl", "--sources", "kalshi"],
    "settle": [PY, "-m", "sports.worker", "settle", "nba", "nfl"],
    "history": [PY, "-m", "sports.snapshots", "history", "nba", "nfl", "--max-players", "40"],
    "books": [PY, "-m", "sports.books.worker", "--once"],
    "model-forecast": [PY, "-m", "sports.worker", "train", "forecast", "nba", "nfl"],
    "model-learn": [PY, "-m", "sports.worker", "train", "learn"],
    "model-snapshot": [PY, "-m", "sports.worker", "train", "snapshot"],
    "model-fit": [PY, "-m", "sports.worker", "train", "fit"],
    "lab-once": [PY, "-m", "sports.lab.runner", "--once"],
    "archive": [PY, "-m", "sports.archive", "pass"],
    "assistant-idle": [PY, "-m", "sports.assistant.runner"],
}


def size_mb(d):
    return round(sum(f.stat().st_size for f in Path(d).rglob("*") if f.is_file()) / 1e6, 1)


def run(name):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPORTS_")}
    env.update({"SPORTS_DATA_DIR": data, "PYTHONPATH": code, "SPORTS_INGEST": "1", "SPORTS_TRAIN": "1",
                "SPORTS_API": "1", "SPORTS_ASSISTANT": "1", "SPORTS_TOKEN_SECRET": "s" * 40, "PYTHONIOENCODING": "utf-8"})
    env.update({k: v for k, v in os.environ.items() if k.startswith("SPORTS_JOB_") or k in (
        "MALLOC_ARENA_MAX", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "SPORTS_WORKER_CONCURRENCY")})
    if offline:
        env.update({"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "NO_PROXY": ""})
    before = size_mb(data)
    t0 = time.time()
    log = open(Path(out).with_suffix(f".{name}.log"), "w", encoding="utf-8")
    p = subprocess.Popen(JOBS[name], cwd=code, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    pp = psutil.Process(p.pid)
    peak = peak_self = 0.0
    limit = 120 if name == "assistant-idle" else 3600
    while p.poll() is None:
        try:
            procs = [pp] + pp.children(recursive=True)
            rss = [q.memory_info().rss / 1e6 for q in procs]
            peak = max(peak, sum(rss))
            peak_self = max(peak_self, rss[0])
        except psutil.Error:
            pass
        if time.time() - t0 > limit:
            for q in [pp] + pp.children(recursive=True):
                try:
                    q.kill()
                except psutil.Error:
                    pass
            break
        time.sleep(0.02)
    p.wait()
    rec = {"job": name, "rc": p.returncode, "wall_s": round(time.time() - t0, 1), "peak_rss_mb": round(peak, 1),
           "peak_rss_self_mb": round(peak_self, 1), "data_mb_before": before, "data_mb_after": size_mb(data),
           "offline": offline, "t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


for n in names or list(JOBS):
    run(n)
