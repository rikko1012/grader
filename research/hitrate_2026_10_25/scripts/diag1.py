import json, collections
from stats import *
U = json.load(open("cs2_units.json"))
for u in U:
    u["d"] = u["start"][:10]; u["wk"] = week(u["d"]); u["side"] = "Over" if u["p"] >= 0.5 else "Under"
    u["pp"] = max(u["p"], 1 - u["p"]); u["hit"] = u["over"] if u["p"] >= 0.5 else 1 - u["over"]
    u["src2"] = u["src"] or "pre"
def r(name, us):
    if not us: return
    n=len(us); f=lambda k: sum(k(u) for u in us)/n
    a=boot_ratio(us, lambda u: u["actual"]-u["lam"]); b=boot_ratio(us, lambda u: u["actual"]-u["line"])
    w=sum(u["hit"] for u in us)
    print(f"{name:40s} n={n:4d} m={len({u['match'] for u in us}):3d} hit={w/n:.3f} act-proj={f(lambda u:u['actual']-u['lam']):+.2f}[{a[0]:+.2f},{a[1]:+.2f}] act-line={f(lambda u:u['actual']-u['line']):+.2f}[{b[0]:+.2f},{b[1]:+.2f}] proj-line={f(lambda u:u['lam']-u['line']):+.2f} |proj-line|={f(lambda u:abs(u['lam']-u['line'])):.2f} MAEproj={f(lambda u:abs(u['actual']-u['lam'])):.2f} MAEline={f(lambda u:abs(u['actual']-u['line'])):.2f}")
for prop in ("kills","hs"):
    print(f"\n### {prop} Maps 1-2")
    for wk in sorted({u["wk"] for u in U}):
        us=[u for u in U if u["prop"]==prop and u["scope"]=="12" and u["wk"]==wk]
        r(wk, us)
        for s in sorted({u["src2"] for u in us}): r(f"  {wk} {s}", [u for u in us if u["src2"]==s])
        for s in ("Over","Under"): r(f"  {wk} side {s}", [u for u in us if u["side"]==s])
print("\n### by day kills 12")
for d in sorted({u["d"] for u in U}): r(d, [u for u in U if u["prop"]=="kills" and u["scope"]=="12" and u["d"]==d])
# perf-cm2 vs saved perf2 lambda
print("\n### perf-cm2 kills 12: cm2 lam vs perf2 lam (saved)")
us=[u for u in U if u["src"]=="perf-cm2" and u["prop"]=="kills" and u["perf2_lambda"]]
n=len(us)
print(n, "cm2 bias", sum(u["actual"]-u["lam"] for u in us)/n, "perf2 bias", sum(u["actual"]-u["perf2_lambda"] for u in us)/n,
      "MAE cm2", sum(abs(u["actual"]-u["lam"]) for u in us)/n, "MAE perf2", sum(abs(u["actual"]-u["perf2_lambda"]) for u in us)/n, "MAE line", sum(abs(u["actual"]-u["line"]) for u in us)/n)
print(boot_ratio(us, lambda u: abs(u["actual"]-u["perf2_lambda"])-abs(u["actual"]-u["lam"])))
