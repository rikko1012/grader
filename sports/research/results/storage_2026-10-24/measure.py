"""Local storage / memory measurement of one canonical ingest cycle (sandbox only; nothing on Render).

usage: python measure.py <repo> <data_dir> <label> <out.json>
Runs `python -m sports.worker migrate` and `python -m sports.worker ingest nba nfl` (all SOURCES) with
SPORTS_DATA_DIR=<data_dir>, SPORTS_INGEST=1, no paid-feed keys; records wall time, the ingest process's peak RSS
(os.wait4 rusage) and per-file / per-table / per-record-kind sizes after the cycle.
"""
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

repo, data_dir, label, out = sys.argv[1:5]
srcs = sys.argv[5] if len(sys.argv) > 5 else None
data = Path(data_dir)
data.mkdir(parents=True, exist_ok=True)
env = {k: v for k, v in os.environ.items()
       if not k.startswith("SPORTS_") and k not in ("ODDS_API_KEY", "ODDSPAPI_API_KEY", "odds_papi_api")}
env.update({"SPORTS_DATA_DIR": str(data), "SPORTS_INGEST": "1", "PYTHONPATH": repo})
env.update(json.loads(os.environ.get("MEASURE_ENV", "{}")))


def run(args, log):
    t0 = time.time()
    with open(log, "w") as fh:
        p = subprocess.Popen([sys.executable, "-m", "sports.worker", *args], cwd=repo, env=env,
                             stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT)
        _, status, ru = os.wait4(p.pid, 0)
    return {"args": args, "wall_s": round(time.time() - t0, 1), "exit": os.waitstatus_to_exitcode(status),
            "peak_rss_mb": round(ru.ru_maxrss / 1024, 1)}


def db_stats(path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    s = {"file_mb": round(path.stat().st_size / 1e6, 3), "tables": {}}
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    sizes = {}
    try:
        for name, b in con.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"):
            sizes[name] = b
    except sqlite3.Error:
        pass
    idx = {r[0]: r[1] for r in con.execute("SELECT name, tbl_name FROM sqlite_master WHERE type='index'")}
    for t in names:
        rows = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        ib = sum(b for n, b in sizes.items() if idx.get(n) == t)
        s["tables"][t] = {"rows": rows, "data_mb": round(sizes.get(t, 0) / 1e6, 3), "index_mb": round(ib / 1e6, 3)}
    if path.name == "canonical.db":
        s["records_by_kind"] = {
            f"{k}|{src}": {"rows": n, "payload_mb": round(pb / 1e6, 3), "avg_payload_b": round(pb / n)}
            for k, src, n, pb in con.execute(
                "SELECT kind, CASE WHEN platform IS NOT NULL THEN platform ELSE source END, COUNT(*), "
                "SUM(LENGTH(payload)) FROM records GROUP BY 1, 2 ORDER BY 4 DESC")}
    con.close()
    return s


res = {"label": label, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
res["migrate"] = run(["migrate"], data / f"{label}_migrate.log")
res["ingest"] = run(["ingest", "nba", "nfl"] + (["--sources", srcs] if srcs else []), data / f"{label}_ingest.log")
files = {}
for p in sorted(data.rglob("*")):
    if p.is_file() and not p.name.endswith(".log"):
        files[str(p.relative_to(data))] = round(p.stat().st_size / 1e6, 3)
res["files_mb"] = files
res["dbs"] = {p.name: db_stats(p) for p in sorted(data.rglob("*.db"))}
Path(out).write_text(json.dumps(res, indent=1))
print(json.dumps({k: res[k] for k in ("label", "at", "migrate", "ingest")}))
