import random, math, collections
def week(d):  # d = 'YYYY-MM-DD' UTC start
    if d <= "2026-10-17": return "W1 Oct11-17"
    if d <= "2026-10-24": return "W2 Oct18-24"
    return "W3 Oct25"
def boot_ratio(units, num, den=lambda u: 1, key=lambda u: u["match"], B=2000, seed=7):
    g = collections.defaultdict(lambda: [0.0, 0.0])
    for u in units:
        g[key(u)][0] += num(u); g[key(u)][1] += den(u)
    gs = list(g.values())
    if not gs: return (None, None)
    rnd = random.Random(seed); est = []
    for _ in range(B):
        s = n = 0.0
        for _ in range(len(gs)):
            a, b = gs[rnd.randrange(len(gs))]; s += a; n += b
        if n: est.append(s / n)
    est.sort()
    return (est[int(0.025 * len(est))], est[int(0.975 * len(est)) - 1])
def boot_slope(units, x, y, key=lambda u: u["match"], B=2000, seed=11):
    g = collections.defaultdict(list)
    for u in units: g[key(u)].append((x(u), y(u)))
    gs = list(g.values()); rnd = random.Random(seed)
    def slope(pts):
        n = len(pts); mx = sum(p[0] for p in pts) / n; my = sum(p[1] for p in pts) / n
        sxx = sum((p[0] - mx) ** 2 for p in pts)
        return sum((p[0] - mx) * (p[1] - my) for p in pts) / sxx if sxx else 0
    pts = [p for g_ in gs for p in g_]; s0 = slope(pts); est = []
    for _ in range(B):
        bp = []
        for _ in range(len(gs)): bp += gs[rnd.randrange(len(gs))]
        est.append(slope(bp))
    est.sort()
    return s0, (est[int(0.025 * B)], est[int(0.975 * B) - 1])
