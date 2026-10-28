"""(1) hs-cm2 forward: HS Maps 1-2 P1 units since 2026-10-21 - the published chance (perf-cm2 HS) vs the fh-1 record's
perf-2 arm of the same forecast (rec.live, the perf-2 replay). (2) Stake-rule cohorts under the PICKS rule (first grade):
what the capped / zeroed cohorts actually hit."""
import json, math, os, sys, collections, re
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from ana import boot, metrics, picks_units
from score_cands import summary

p1 = {u["forecast_id"]: u for u in json.load(open(os.path.join(D, "p1_units.json")))["rows"]}
fh = json.load(open(os.path.join(D, "fh1.json")))["rows"]
rows, miss = [], collections.Counter()
seen = set()
for r in fh:
    if r["prop"] != "hs" or r["scope"] != "12" or r["logged_at"] < "2026-10-21T20:00": continue
    u = p1.get(r["forecast_id"])
    if not u: miss["forecast not a P1 unit"] += 1; continue
    if r["forecast_id"] in seen: continue
    seen.add(r["forecast_id"])
    live = (r["rec"].get("live") or {})
    if live.get("projection_source") != "perf-2" or live.get("p_more") is None: miss["no perf-2 arm"] += 1; continue
    if abs(float(r["live_p_more"]) - float(u["p"])) > 0.002: miss["published chance differs from P1 chance"] += 1
    rows.append({"match": u["match"], "pl": float(live["p_more"]), "pc": float(u["p"]), "y": int(u["over"]), "prop": "hs", "scope": "12", "day": u["start"][:10]})
hs = summary(rows, "hs-cm2 (published HS Maps 1-2 since Oct 21) vs the perf-2 arm of the same forecast")
hs["missing"] = dict(miss)
print(json.dumps({k: v for k, v in hs.items() if k != "slices"}, default=str))

# stake-rule cohorts (PICKS rule, platform board lines)
us, _ = picks_units()
picks = {r["id"]: r for r in json.load(open(os.path.join(D, "picks.json")))["rows"]}
board = [u for u in us if u["book"] in ("underdog", "prizepicks")]
def coh(name, pred):
    s = [u for u in board if pred(u, picks[u["id"]].get("flags") or "")]
    m = metrics(s, B=400)
    return dict(m, cohort=name)
out = {"hs_cm2": hs, "stake_rules": []}
tests = [
    ("thin data <20 maps (1.5U cap)", lambda u, f: any("thin" in x for x in u["sd_reasons"])),
    ("sample <10 maps", lambda u, f: any("<10" in x for x in u["sd_reasons"])),
    ("rust 15-28d", lambda u, f: "rust 15-28d" in u["sd_reasons"]),
    ("rust >28d (1U cap)", lambda u, f: "rust >28d" in u["sd_reasons"]),
    ("stand-in (1U cap)", lambda u, f: "STAND-IN" in f),
    ("SOFT BOOK (1.5U cap)", lambda u, f: "SOFT BOOK" in f),
    ("NO MARKET (1.5U cap)", lambda u, f: "NO MARKET" in f),
    ("TAPE FIGHT (0.5U cap)", lambda u, f: "TAPE FIGHT" in f),
    ("FORM FIGHT (0U)", lambda u, f: "FORM FIGHT" in f),
    ("AWPer on HS (0U)", lambda u, f: "AWPer on a headshot" in f),
    ("PROVISIONAL BO3.gg route (no cap today)", lambda u, f: "PROVISIONAL" in f),
    ("kills CAP 78% hit", lambda u, f: "CAP: model chance" in f),
    ("Rested +4% (legacy only)", lambda u, f: "Rested (" in f),
    ("Star lower (legacy only)", lambda u, f: "Star (rating" in f),
    ("Hot form (legacy only)", lambda u, f: "Hot form" in f),
    ("no flag of the above", lambda u, f: not any(k in f for k in ("STAND-IN", "SOFT BOOK", "NO MARKET", "TAPE FIGHT", "FORM FIGHT", "AWPer", "PROVISIONAL", "CAP:")) and not u["sd_reasons"]),
]
for name, pred in tests:
    out["stake_rules"].append(coh(name, pred))
# by chance band the picked side had >= 57% and was staked: units distribution and hit by stake size
g = collections.defaultdict(list)
for u in board: g[u["units"]].append(u)
out["by_units"] = [dict(metrics(v, B=300), units=k) for k, v in sorted(g.items())]
json.dump(out, open(os.path.join(D, "hs_rules.json"), "w"), indent=1, default=str)
for r in out["stake_rules"]:
    print(f"{r['cohort']:42s} n {r['n']:5d} {r['wlp']:>14s} stated {r.get('stated')} hit {r.get('hit')} {r.get('hit_ci')} gap {r.get('gap')} over {r.get('over_share')}/{r.get('over_rate')}")
for r in out["by_units"]:
    print("units", r["units"], r["n"], r["wlp"], "stated", r.get("stated"), "hit", r.get("hit"), r.get("hit_ci"))
