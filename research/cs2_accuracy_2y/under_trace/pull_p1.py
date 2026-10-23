"""UT-1 data pull (read-only): the stored P1 units export of the last learning cycle, paged (limit 2000, 0.3 s between pages).
usage: python pull_p1.py <out.json>   (needs the scratchpad lq.py admin helper; the token is never printed)"""
import json, sys, time
sys.path.insert(0, r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad")
from lq import adm

after, rows, head = 0, [], None
while True:
    p = adm(f"/admin/learning/p1_units.json?after={after}&limit=2000")
    head = head or {k: v for k, v in p.items() if k != "rows"}
    rows += p["rows"]
    print(after, len(rows), p.get("total"), flush=True)
    if p.get("next_after") is None:
        break
    after = p["next_after"]
    time.sleep(0.3)
json.dump(dict(head, rows=rows), open(sys.argv[1], "w"))
print("done", len(rows), "at", head.get("at"))
