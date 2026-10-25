import json
from stats import *
U = json.load(open("cs2_units.json"))
L = json.load(open("lol_units.json"))
def s(name, us, lam="lam", key=lambda u: u["match"]):
    if len(us) < 20: return
    b, ci = boot_slope(us, lambda u: u[lam] - u["line"], lambda u: u["actual"] - u["line"], key=key)
    print(f"{name:34s} n={len(us):4d} slope(actual-line ~ proj-line)={b:+.2f} [{ci[0]:+.2f},{ci[1]:+.2f}]")
for prop in ("kills", "hs"):
    for wk in ("W1", "W2", "W3"):
        s(f"CS2 {prop} M1-2 {wk}", [u for u in U if u["prop"] == prop and u["scope"] == "12" and week(u["start"][:10]).startswith(wk)])
    s(f"CS2 {prop} M1-2 ALL", [u for u in U if u["prop"] == prop and u["scope"] == "12"])
for st in ("kills", "assists"):
    s(f"LoL {st} Map1 (lol-1f)", [u for u in L if u["stat"] == st and u["scope"] == "1"], lam="lambda", key=lambda u: u["sid"])
