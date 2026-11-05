"""LoL settled units joined to their last forecast logged before the series start (pushes and DNP excluded)."""
import json, os, collections, random
import sys
D = sys.argv[1]
d = json.load(open(os.path.join(D, "lol_settled.json")))
st = {}
for s in d["settlements"]:
    if s["unit_key"] not in st or s["version"] > st[s["unit_key"]]["version"]:
        st[s["unit_key"]] = s
fc = collections.defaultdict(list)
for f in d["forecasts"]:
    ln = f["line"]
    for fmt in (str(ln), str(int(ln)) if float(ln).is_integer() else None):
        if fmt:
            fc[f"{f['platform']}|{f['market_key']}|{fmt}"].append(f)
units, miss = [], 0
for k, s in st.items():
    if s["status"] != "graded" or s["over_hit"] not in (0, 1):
        continue
    fs = [f for f in fc.get(k, []) if f["forecast_at"] < (f["series_start"] or "9")]
    if not fs:
        miss += 1
        continue
    f = max(fs, key=lambda f: f["forecast_at"])
    if f["p_over"] is None:
        continue
    units.append({"match": s["sid"], "pid": s["pid"], "stat": f["stat"], "scope": f["scope"], "odds_type": f["odds_type"], "platform": f["platform"],
                  "line": f["line"], "p": f["p_over"], "p_under": f["p_under"], "over": s["over_hit"], "start": f["series_start"], "version": f["model_version"],
                  "side": f["model_side"], "grade": f["grade"], "offered": f["side_offered"], "tid": f["tid"]})
print("units", len(units), "no forecast", miss)
json.dump(units, open(os.path.join(D, "lol_units.json"), "w"))
print(collections.Counter((u["version"], u["stat"], u["odds_type"]) for u in units).most_common(20))


def ci(us, B=500):
    g = collections.defaultdict(list)
    for m, h in us:
        g[m].append(h)
    ks = list(g); rng = random.Random(7); out = []
    for _ in range(B):
        s = n = 0
        for _ in ks:
            v = g[ks[rng.randrange(len(ks))]]; s += sum(v); n += len(v)
        out.append(s / n)
    out.sort(); return out[int(.025 * B)], out[int(.975 * B)]


for stat in ("kills", "assists", "deaths"):
    for side in ("over", "under"):
        for lo, hi in ((.5, .55), (.55, .6), (.6, .65), (.65, .7), (.7, .8), (.8, 1.01)):
            us, pr = [], []
            for u in units:
                if u["stat"] != stat:
                    continue
                pu = u["p_under"] if u["p_under"] is not None else 1 - u["p"]
                sd = "over" if u["p"] >= pu else "under"
                sp = max(u["p"], pu)
                if sd == side and lo <= sp < hi:
                    us.append((u["match"], u["over"] if sd == "over" else 1 - u["over"])); pr.append(sp)
            if len(us) >= 15:
                a, b = ci(us)
                print(f"{stat:7} {side:5} [{lo:.2f},{hi:.2f}) n={len(us):4} m={len({x[0] for x in us}):3} printed={sum(pr)/len(pr):.3f} won={sum(h for _,h in us)/len(us):.3f} ci=({a:.3f},{b:.3f})")
