"""Synthetic ingestion writer for the "ingesting" load scenario: commits offer REVISIONS (line +-0.5) into the sports
store continuously, like the worker does, until killed. No network.
usage: python load_sports_ingest.py <code_dir> <sports_data_dir> [batch=300] [every_s=1.0]
Prints one JSON line per second: {"revisions": n, "batches": k, "ms_per_batch": x}."""
import dataclasses
import json
import os
import sys
import time

code, data = sys.argv[1], sys.argv[2]
batch = int(sys.argv[3]) if len(sys.argv) > 3 else 300
every = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
sys.path.insert(0, code)
os.environ["SPORTS_DATA_DIR"] = data

from sports import store  # noqa: E402
from sports.contracts import now_utc  # noqa: E402

con = store.connect()
base = [o for _, o in store.offers(con, league="nba", limit=3000)] + [o for _, o in store.offers(con, league="nfl", limit=3000)]
base = [o for o in base if o.line is not None]
n = k = 0
i = 0
last = time.time()
ms = []
while True:
    t0 = time.time()
    now = now_utc()
    recs = []
    for _ in range(batch):
        o = base[i % len(base)]
        i += 1
        step = 0.5 * (1 + i // len(base))                # always a new line -> a real revision, never a duplicate
        recs.append(dataclasses.replace(o, line=o.line + step, observed_at=now, known_at=now))
    try:
        store.put_many(con, recs)
        n += len(recs)
        k += 1
    except Exception as e:                              # noqa: BLE001
        print(json.dumps({"error": type(e).__name__, "detail": str(e)[:200]}), flush=True)
    ms.append((time.time() - t0) * 1000)
    if time.time() - last >= 1:
        print(json.dumps({"written": n, "batches": k, "ms_per_batch": round(sum(ms) / len(ms), 1)}), flush=True)
        last, ms = time.time(), []
    time.sleep(max(0.0, every - (time.time() - t0)))
