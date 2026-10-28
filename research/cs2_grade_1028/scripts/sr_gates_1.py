"""sr-gates-1 scoring (research/cs2_grade_1028/REGISTER.md section sr-gates-1). P1 units joined to forecasts + pick inputs;
chronological halves by distinct match start; cohorts recomputed from the P1 chance and the saved inputs."""
import json, math, random, collections, sys, os
S = r"C:/Users/tikic/AppData/Local/Temp/claude/C--Users-tikic-OneDrive-Documents-cs2-scout/6847ff60-df65-4b0c-851b-1b79f55ef3bf/scratchpad/cs2grade1028/"
OUT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(OUT, "joined.json")

if os.path.exists(CACHE):
    units = json.load(open(CACHE))
else:
    p1 = json.load(open(S + "p1_units.json"))["rows"]
    fc = {}
    for r in json.load(open(S + "forecasts.json", encoding="utf-8"))["rows"]:
        fc[r["id"]] = {"pick_id": r.get("pick_id"), "grade": r.get("grade"), "units": r.get("units"), "pick": r.get("pick")}
    picks = {}
    for r in json.load(open(S + "picks.json", encoding="utf-8"))["rows"]:
        try: fac = json.loads(r.get("factors") or "{}")
        except Exception: fac = {}
        picks[r["id"]] = {"flags": r.get("flags") or "", "sniping": fac.get("sniping"), "recent": fac.get("recent") or {},
                          "rust": fac.get("days_since_last_map"), "sd_reasons": (fac.get("detail") or {}).get("sd_reasons") or [],
                          "test": r.get("test")}
    units = []
    for u in p1:
        if u["logged"] >= u["start"]: continue
        f = fc.get(u["forecast_id"]) or {}
        pk = picks.get(f.get("pick_id")) or {}
        p_over = float(u["p"]); side = "Over" if p_over > 0.5 else "Under"
        p = p_over if side == "Over" else 1 - p_over
        hit = int(u["over"]) if side == "Over" else 1 - int(u["over"])
        units.append({"match": u["match"], "start": u["start"], "day": u["start"][:10], "prop": u["prop"], "scope": u["scope"],
                      "side": side, "p": p, "hit": hit, "units": u.get("units") or 0, "f_units": f.get("units"), "f_grade": f.get("grade"),
                      "flags": pk.get("flags", ""), "sniping": pk.get("sniping"), "recent": pk.get("recent", {}), "rust": pk.get("rust"),
                      "sd_reasons": pk.get("sd_reasons", []), "has_pick": bool(pk)})
    json.dump(units, open(CACHE, "w"))
print("units", len(units), "with pick inputs", sum(u["has_pick"] for u in units), "matches", len({u["match"] for u in units}))

# chronological halves by distinct match start
starts = {}
for u in units: starts[u["match"]] = min(starts.get(u["match"], u["start"]), u["start"])
order = sorted(starts, key=lambda m: (starts[m], m))
h1 = set(order[:len(order) // 2]); h2 = set(order[len(order) // 2:])
print("half 1 matches", len(h1), starts[order[0]][:16], "->", starts[order[len(order)//2 - 1]][:16],
      "| half 2 matches", len(h2), starts[order[len(order)//2]][:16], "->", starts[order[-1]][:16])
for u in units: u["half"] = 1 if u["match"] in h1 else 2

M = 0.14
def form_fight(u):
    rec = u["recent"] if u["scope"] == "12" else {}
    if not rec.get("n") or u["p"] < 0.57: return False
    sm = (rec["overs"] + 2) / (rec["n"] + 4)
    return (u["side"] == "Over" and sm <= 0.5 - M) or (u["side"] == "Under" and sm >= 0.5 + M)
def tape_fight(u):
    rec = u["recent"] if u["scope"] == "12" else {}
    if not rec.get("n") or u["p"] < 0.57 or form_fight(u): return False
    rate = rec["overs"] / rec["n"]
    return (u["side"] == "Over" and rate <= 0.50) or (u["side"] == "Under" and rate >= 0.60)
def awper_hs(u): return u["prop"] == "hs" and u["sniping"] is not None and u["sniping"] > 40
def rust_mid(u): return u["rust"] is not None and 15 <= u["rust"] <= 28

def boot(us, B=1000, seed=7):
    g = collections.defaultdict(lambda: [0, 0])
    for u in us: g[u["match"]][0] += u["hit"]; g[u["match"]][1] += 1
    gs = list(g.values()); rnd = random.Random(seed); est = []
    for _ in range(B):
        s = n = 0
        for _ in range(len(gs)):
            a, b = gs[rnd.randrange(len(gs))]; s += a; n += b
        est.append(s / n)
    est.sort(); return est[int(0.025 * B)], est[int(0.975 * B) - 1]

def stats(us):
    if not us: return {"n": 0}
    w = sum(u["hit"] for u in us); n = len(us)
    lo, hi = boot(us)
    ov = [u for u in us if u["side"] == "Over"]
    over_share = len(ov) / n; over_hit = (sum(u["hit"] for u in ov) / len(ov)) if ov else None
    return {"n": n, "matches": len({u["match"] for u in us}), "wl": f"{w}-{n-w}", "hit": round(100 * w / n, 1),
            "ci": [round(100 * lo, 1), round(100 * hi, 1)], "stated": round(100 * sum(u["p"] for u in us) / n, 1),
            "gap": round(100 * (sum(u["p"] for u in us) / n - w / n), 1),
            "over_share": round(100 * over_share, 1), "over_hit": None if over_hit is None else round(100 * over_hit, 1),
            "balance": round(abs(over_share - (over_hit if over_hit is not None else over_share)), 3)}

def lodo(us):
    """leave-one-day-out: worst pooled hit when one UTC start day is removed."""
    days = sorted({u["day"] for u in us}); worst = None
    for d in days:
        r = [u for u in us if u["day"] != d]
        if not r: continue
        h = sum(u["hit"] for u in r) / len(r)
        if worst is None or h < worst[1]: worst = (d, round(100 * h, 1), len(r))
    return worst

def lift_rule(name, us):
    a = [u for u in us if u["half"] == 1]; b = [u for u in us if u["half"] == 2]
    sa, sb, sp = stats(a), stats(b), stats(us)
    ok_min = sa.get("n", 0) >= 40 and sa.get("matches", 0) >= 15 and sb.get("n", 0) >= 40 and sb.get("matches", 0) >= 15
    res = {"cohort": name, "half1": sa, "half2": sb, "pooled": sp, "min_evidence": ok_min, "lodo_worst": lodo(us) if us else None}
    if not ok_min:
        res["verdict"] = "not decidable (minimum evidence not met)"; return res
    c = {"halves >= 50": sa["hit"] >= 50 and sb["hit"] >= 50, "pooled lower > 50": sp["ci"][0] > 50,
         "|hit-stated| <= 5": abs(sp["gap"]) <= 5, "balance <= 0.15": sp["balance"] <= 0.15 and sa["balance"] <= 0.15 and sb["balance"] <= 0.15,
         "no upset day flips < 50": res["lodo_worst"][1] >= 50}
    res["conditions"] = c; res["verdict"] = "PASS" if all(c.values()) else "FAIL"
    return res

def cap_rule(name, us):
    a = [u for u in us if u["half"] == 1]; b = [u for u in us if u["half"] == 2]
    sa, sb, sp = stats(a), stats(b), stats(us)
    ok_min = sa.get("n", 0) >= 40 and sa.get("matches", 0) >= 15 and sb.get("n", 0) >= 40 and sb.get("matches", 0) >= 15
    res = {"cohort": name, "half1": sa, "half2": sb, "pooled": sp, "min_evidence": ok_min, "lodo_worst": lodo(us) if us else None}
    if not ok_min:
        res["verdict"] = "not decidable (minimum evidence not met)"; return res
    c = {"half1 upper < 50": sa["ci"][1] < 50, "half2 upper < 50": sb["ci"][1] < 50}
    c45 = sa["ci"][1] < 45 and sb["ci"][1] < 45
    res["conditions"] = c
    res["verdict"] = ("PASS -> 0U" if c45 else "PASS -> 0.5U") if all(c.values()) else "FAIL (no cap)"
    return res

elig = [u for u in units if u["p"] >= 0.55]
out = {}
out["FORM FIGHT lift"] = lift_rule("FORM FIGHT (recomputed), chance >= 55%", [u for u in elig if form_fight(u)])
out["AWPer-HS lift"] = lift_rule("AWPer on HS (sniping > 40), chance >= 55%", [u for u in elig if awper_hs(u)])
out["TAPE FIGHT raise (>=63%)"] = lift_rule("TAPE FIGHT (recomputed), chance >= 63%", [u for u in elig if tape_fight(u) and u["p"] >= 0.63])
out["TAPE FIGHT info (>=55%)"] = lift_rule("TAPE FIGHT (recomputed), chance >= 55% (info)", [u for u in elig if tape_fight(u)])
out["rust 15-28 cap"] = cap_rule("rust 15-28 d, chance >= 55%", [u for u in elig if rust_mid(u)])
# cross-checks: first-grade flag text membership
out["xcheck FORM FIGHT flag"] = {"cohort": "flag text FORM FIGHT, chance >= 55%", "pooled": stats([u for u in elig if "FORM FIGHT" in u["flags"]])}
out["xcheck AWPer flag"] = {"cohort": "flag text AWPer, chance >= 55%", "pooled": stats([u for u in elig if "AWPer on a headshot" in u["flags"]])}
out["xcheck TAPE FIGHT flag"] = {"cohort": "flag text TAPE FIGHT, chance >= 55%", "pooled": stats([u for u in elig if "TAPE FIGHT" in u["flags"]])}
out["xcheck rust flag"] = {"cohort": "sd_reasons rust 15-28d, chance >= 55%", "pooled": stats([u for u in elig if "rust 15-28d" in u["sd_reasons"]])}
# context: the uncapped board (chance >= 55%, none of the cohorts) and all staked
out["context all >= 55%"] = {"cohort": "all P1 units, chance >= 55%", "pooled": stats(elig), "half1": stats([u for u in elig if u["half"] == 1]), "half2": stats([u for u in elig if u["half"] == 2])}
out["context rust 15-28 all"] = {"cohort": "rust 15-28 d, all chances", "pooled": stats([u for u in units if rust_mid(u)])}
out["context rust >28"] = {"cohort": "rust >28 d, chance >= 55%", "pooled": stats([u for u in elig if u["rust"] is not None and u["rust"] >= 29])}
json.dump(out, open(os.path.join(OUT, "sr_gates_1_results.json"), "w"), indent=1)
for k, v in out.items():
    print("\n##", k, "-", v["cohort"])
    for h in ("half1", "half2", "pooled"):
        if h in v: print(f"  {h:6s}", v[h])
    if "conditions" in v: print("  conditions", v["conditions"])
    if "lodo_worst" in v: print("  worst leave-one-day-out", v["lodo_worst"])
    if "verdict" in v: print("  VERDICT:", v["verdict"])
