"""Fresh holdout pull (after scoring): pick rows with id > START (default: the last id of the 2026-11-09 export).
Read-only, paged (1,000 rows per request, one request at a time). Token never printed.
usage: python pull_holdout.py [start_id] [outfile]  -> $GU_DIR/<outfile>"""
import gzip, json, os, sys, time
sys.path.insert(0, "C:/Users/tikic/cs2_scout_ops")
from lq import adm
OUT = os.environ.get("GU_DIR", "C:/Users/tikic/cs2_scout_ops/data/grade_up_1111/")
START = int(sys.argv[1]) if len(sys.argv) > 1 else 25313
NAME = sys.argv[2] if len(sys.argv) > 2 else "picks_after.jsonl.gz"


def get(path):
    for t in range(3):
        try:
            return adm(path)
        except Exception as e:
            print("retry", t, type(e).__name__, flush=True)
            time.sleep(20 * (t + 1))
    raise SystemExit("failed " + path.split("?")[0])


f = gzip.open(OUT + NAME, "wt", encoding="utf-8")
after, cutoff, n = START, None, 0
while True:
    t0 = time.time()
    d = get(f"/admin/picks.json?after={after}&limit=1000" + (f"&cutoff={cutoff}" if cutoff else ""))
    cutoff = d["cutoff_id"]
    for r in d["picks"]:
        r.pop("factors", None); r.pop("pred_maps", None)
        f.write(json.dumps(r) + "\n"); n += 1
    print("picks", n, "cutoff", cutoff, "sec", round(time.time() - t0, 1), flush=True)
    if d["done"]:
        break
    after = d["next_after"]; time.sleep(2)
f.close(); print("DONE", n)
