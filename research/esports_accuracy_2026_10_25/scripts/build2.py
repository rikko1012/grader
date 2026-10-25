import json, collections
p1 = json.load(open("p1.json"))["rows"]
ids = {r["forecast_id"] for r in p1}
fc = {}
for path in ("../cs2acc/data/forecasts_lite.json", "fc_new.json", "fc_new2.json"):
    for r in json.load(open(path))["rows"]:
        if int(r["id"]) in ids: fc[int(r["id"])] = r
print("p1", len(p1), "fc found", len(fc))
f = next(iter(fc.values())); print(sorted(f))
json.dump({str(k): v for k, v in fc.items()}, open("fc_p1.json", "w"))
