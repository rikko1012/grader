import json
p1 = json.load(open("p1.json"))["rows"]
ids = {r["forecast_id"] for r in p1}
fc = {}
for path in ("../cs2acc/data/forecasts_lite.json", "fc_new.json"):
    for r in json.load(open(path))["rows"]:
        if r["id"] in ids: fc[r["id"]] = r
out = []
for u in p1:
    f = fc.get(u["forecast_id"])
    s = {}
    if f and f.get("shadow"):
        try: s = json.loads(f["shadow"])
        except Exception: s = {}
    src = (s.get("old_spread") or {}).get("source") or (s.get("lm0") or {}).get("live_source")
    mr = s.get("market_rounds") or {}
    out.append(dict(u, src=src, grade=f and f.get("grade"), fc_units=f and f.get("units"), p_win=f and f.get("p_win"),
                    sample_maps=f and f.get("sample_maps"), data_rev=f and f.get("data_rev"), odds_source=f and f.get("odds_source"),
                    perf2_lambda=mr.get("perf2_lambda"), rounds_sum=mr.get("rounds_sum"), online=(s.get("live_online") or {}).get("tonight"),
                    pick_id=f and f.get("pick_id"), has_fc=f is not None))
json.dump(out, open("cs2_units.json", "w"))
import collections
print(len(out), sum(1 for o in out if not o["has_fc"]))
print(collections.Counter((o["start"][:10] >= "2026-10-18", o["src"]) for o in out))
