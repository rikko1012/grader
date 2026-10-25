import json, sys, time
sys.path.insert(0, '..'); from lq import adm
U = json.load(open("p1.json"))["rows"]
ids = sorted({u["forecast_id"] for u in U })
out = {}
try: out = {int(k): v for k, v in json.load(open("inputs_p1.json")).items()}
except Exception: pass
keep = ("perf", "environment", "is_standin", "standin_for", "roster_confirmed", "sample_maps", "maps_played", "bo", "opp_rank",
        "p_team_market", "odds_provenance", "days_since_last_map", "recent", "scope", "prop", "line", "hltv_series", "gate_sources", "cm2_live", "perf2", "sniping", "is_igl", "line_type", "opp_ml", "team_ml")
for n, i in enumerate(ids):
    if i in out: continue
    for a in range(3):
        try:
            r = adm(f"/admin/forecasts_page.json?after={i-1}&limit=1&inputs=1&cutoff={i}")["rows"]; break
        except Exception as e:
            time.sleep(2); r = []
    if r and r[0]["id"] == i:
        inp = r[0].get("inputs") or {}
        out[i] = {k: inp.get(k) for k in keep if k in inp}
        out[i]["_keys"] = sorted(inp)[:200]
    if n % 200 == 0:
        json.dump(out, open("inputs_p1.json", "w")); print(n, len(out), flush=True)
json.dump(out, open("inputs_p1.json", "w")); print("done", len(out))
