import sys, json
sys.path.insert(0, r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad")
import lq
after, cut, out = 80675, None, []
while True:
    p = lq.adm(f"/admin/forecasts_page.json?limit=2000&after={after}" + (f"&cutoff={cut}" if cut else ""))
    cut = p["cutoff_id"]; out += p["rows"]
    print(after, len(p["rows"]), cut, flush=True)
    after = p["next_after"]
    if p["done"]: break
json.dump({"cutoff_id": cut, "rows": out}, open("fc_new.json", "w"))
