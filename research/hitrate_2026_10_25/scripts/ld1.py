import json, math, datetime as dt, random, collections
from statistics import NormalDist
N = NormalDist()
U = [u for u in json.load(open("cs2_units.json")) if u["scope"] == "12"]
D = lambda s: dt.date.fromisoformat(s[:10])
def boot(units, fn, B=2000, seed=7, q=(0.0125, 0.9875)):
    g = collections.defaultdict(lambda: [0.0, 0])
    for u in units: g[u["match"]][0] += fn(u); g[u["match"]][1] += 1
    gs = list(g.values()); rnd = random.Random(seed); est = []
    for _ in range(B):
        s = n = 0
        for _ in gs:
            a, b = gs[rnd.randrange(len(gs))]; s += a; n += b
        est.append(s / n)
    est.sort(); return est[int(q[0] * B)], est[int(q[1] * B) - 1]
out = {}
for prop in ("kills", "hs"):
    T = []
    for u in U:
        if u["prop"] != prop or u["start"][:10] < "2026-10-18": continue
        d0 = D(u["start"]); win = [v for v in U if v["prop"] == prop and d0 - dt.timedelta(days=7) <= D(v["start"]) < d0]
        n = len(win); b = sum(v["actual"] - v["lam"] for v in win) / n if n else 0.0
        lam2 = u["lam"] + n / (n + 400) * b
        T.append(dict(u, lam2=lam2, shift=lam2 - u["lam"]))
    gain = lambda u: abs(u["actual"] - u["lam"]) - abs(u["actual"] - u["lam2"])
    sq = lambda u: (u["actual"] - u["lam"]) ** 2 - (u["actual"] - u["lam2"]) ** 2
    n = len(T); lo, hi = boot(T, gain); slo, shi = boot(T, sq)
    # sides
    side_ld = lambda u: (u["over"] if u["lam2"] > u["line"] else 1 - u["over"])
    side_live = lambda u: (u["over"] if u["p"] >= 0.5 else 1 - u["over"])
    hl = sum(map(side_ld, T)) / n; hv = sum(map(side_live, T)) / n
    # log loss secondary
    ll = lambda p, y: -math.log(max(1e-6, p if y else 1 - p)); G = []
    for u in T:
        p = u["p"]
        if not (0.23 < p < 0.77) or abs(p - 0.5) < 0.04: continue
        sd = (u["line"] - u["lam"]) / N.inv_cdf(1 - p)
        if sd <= 0.5: continue
        p2 = 1 - N.cdf((u["line"] - u["lam2"]) / sd)
        G.append(dict(u, g=ll(p, u["over"]) - ll(p2, u["over"])))
    glo, ghi = boot(G, lambda u: u["g"], q=(0.025, 0.975))
    shifts = sorted(u["shift"] for u in T)
    out[prop] = dict(units=n, matches=len({u["match"] for u in T}), mae_gain=sum(map(gain, T)) / n, mae_gain_ci975=(lo, hi),
                     se_gain=sum(map(sq, T)) / n, se_gain_ci975=(slo, shi), side_hit_ld1=hl, side_hit_live=hv,
                     ll_units=len(G), ll_gain=sum(u["g"] for u in G) / len(G), ll_gain_ci95=(glo, ghi),
                     shift_min=shifts[0], shift_median=shifts[n // 2], shift_max=shifts[-1],
                     passed=lo > 0 and hl >= hv)
    by = collections.defaultdict(list)
    for u in T: by[u["start"][:10]].append(u)
    out[prop]["by_day"] = {d: (len(v), round(sum(map(gain, v)) / len(v), 3), round(v[0]["shift"], 2)) for d, v in sorted(by.items())}
print(json.dumps(out, indent=1))
json.dump(out, open("ld1_result.json", "w"), indent=1)
