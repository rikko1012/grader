import json, collections
from stats import boot_ratio
R = json.load(open("lol_p1.json"))
U = []
for r in R:
    if r["st"] != "graded": continue
    p = r["p_over"] / max(r["p_over"] + r["p_under"], 1e-9)
    side = r["model_side"]; hit = r["over_hit"] if side == "over" else 1 - r["over_hit"]
    pp = p if side == "over" else 1 - p
    U.append(dict(r, p=p, side=side, hit=hit, pp=pp, err=r["actual"] - r["lambda"], match=r["sid"], d=r["series_start"][:10]))
# dedupe across platforms: one unit per (sid,pid,stat,scope) - keep latest forecast
best = {}
for u in U:
    k = (u["sid"], u["pid"], u["stat"], u["scope"])
    if k not in best or u["id"] > best[k]["id"]: best[k] = u
D = list(best.values())
print("rows", len(U), "unique units", len(D), "series", len({u['sid'] for u in D}))
dup = collections.defaultdict(list)
for u in U: dup[(u["sid"], u["pid"], u["stat"], u["scope"])].append(u)
dd = [v for v in dup.values() if len(v) > 1]
print("dup units", len(dd), "side disagreements", sum(1 for v in dd if len({x['side'] for x in v}) > 1), "same-line", sum(1 for v in dd if len({x['line'] for x in v})==1))
def row(name, us):
    if not us: return
    n = len(us); w = sum(u["hit"] for u in us); lo, hi = boot_ratio(us, lambda u: u["hit"]); el, eh = boot_ratio(us, lambda u: u["err"])
    print(f"{name:48s} n={n:4d} s={len({u['sid'] for u in us}):3d} W-L={w}-{n-w:<4d} hit={w/n:.3f} [{lo:.3f},{hi:.3f}] printed={sum(u['pp'] for u in us)/n:.3f}  err={sum(u['err'] for u in us)/n:+.2f} [{el:+.2f},{eh:+.2f}] lam={sum(u['lambda'] for u in us)/n:.2f}")
for nm, f in (("all", lambda u: True), ("57%+", lambda u: u["pp"] >= 0.57), ("offered side", lambda u: u["side_offered"] == 1)):
    print("\n==", nm)
    for d in sorted({u["d"] for u in D}):
        row(d, [u for u in D if u["d"] == d and f(u)])
print("\n== by day x stat x scope x side (all)")
for d in sorted({u["d"] for u in D}):
    for st in ("kills", "assists"):
        for sc in sorted({u["scope"] for u in D}):
            for sd in ("over", "under"):
                row(f"{d} {st} m{sc} {sd}", [u for u in D if u["d"] == d and u["stat"] == st and u["scope"] == sc and u["side"] == sd])
print("\n== version x stat")
for v in ("lol-1", "lol-1f"):
    for st in ("kills", "assists"):
        row(f"{v} {st}", [u for u in D if u["model_version"] == v and u["stat"] == st])
print("\n== league"); [row(str(l), [u for u in D if u["league"] == l]) for l in sorted({str(u['league']) for u in D})]
print("\n== grade"); [row(str(l), [u for u in D if str(u["grade_label"]) == l]) for l in sorted({str(u['grade_label']) for u in D})]
print("\n== bands")
for lo, hi in ((0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 0.8), (0.8, 1.01)):
    row(f"{lo}-{hi}", [u for u in D if lo <= u["pp"] < hi])
json.dump(D, open("lol_units.json", "w"))
