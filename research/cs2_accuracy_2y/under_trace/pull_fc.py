"""UT-1 data pull (read-only): the production forecast rows (with decoded grader inputs) of every P1 unit's forecast id.
Bounded keyset pages of /admin/forecasts_page.json (never the unpaged export): consecutive wanted ids are fetched in one page
when they lie within SPAN ids of each other (limit <= 60 < PAGE_MAX 500), 0.3 s between pages; a fixed cutoff.
usage: python pull_fc.py <p1_units.json> <out.json>"""
import json, sys, time
sys.path.insert(0, r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad")
from lq import adm

SPAN = 60
want = sorted({r["forecast_id"] for r in json.load(open(sys.argv[1]))["rows"]})
cut = max(want)
got, i, pages = {}, 0, 0
while i < len(want):
    j = i
    while j + 1 < len(want) and want[j + 1] - want[i] < SPAN:
        j += 1
    lim = want[j] - want[i] + 1
    for attempt in range(5):
        try:
            p = adm(f"/admin/forecasts_page.json?after={want[i] - 1}&cutoff={cut}&limit={lim}&inputs=1")
            break
        except Exception as e:  # noqa: BLE001
            print("retry", want[i], type(e).__name__, flush=True)
            time.sleep(5 * (attempt + 1))
    else:
        raise SystemExit("failed at %d" % want[i])
    ws = set(want[i:j + 1])
    for r in p["rows"]:
        if r["id"] in ws:
            got[r["id"]] = r
    pages += 1
    if pages % 50 == 0:
        print(pages, len(got), "/", len(want), flush=True)
    i = j + 1
    time.sleep(0.3)
missing = [w for w in want if w not in got]
json.dump({"cutoff": cut, "missing": missing, "rows": [got[k] for k in sorted(got)]}, open(sys.argv[2], "w"))
print("done", len(got), "missing", len(missing), "pages", pages)
