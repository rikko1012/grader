"""lg-lol-1 scoring (definitions frozen in FROZEN.md, commit e894270). usage: python score.py <ds.pkl> <out.json>
Walk-forward by series day (expanding window, on/off gate only), series-grouped paired bootstrap CIs."""
import pickle, json, sys, random, collections
import numpy as np

D = pickle.load(open(sys.argv[1], "rb"))
ROWS = D["rows"]
TEST0 = "2026-10-24"
ORDER = ["Lean", "B-", "B", "B+", "A-", "A", "A+"]
B, SEED, MINCHG = 2000, 7, 30
for r in ROWS:
    r["pm"] = (r["sid"], r["pid"], r["stat"], r["scope"])
    r["graded"] = r["st"] == "graded"


def win(r, side):
    return None if not r["graded"] else int((r["over_hit"] == 1) == (side == "over"))


def step(g, k):
    if g not in ORDER:
        return g
    return ORDER[max(0, min(len(ORDER) - 1, ORDER.index(g) + k))]


def other(s):
    return "under" if s == "over" else "over"


def soft(r):
    g = r["gap"]
    return None if g is None or g == 0 else ("under" if g > 0 else "over")


# ---- rules: each returns a list of units {sid, day, b:{side,letter,inc}, r:{...}, row} for its universe
def unit(r, side=None, letter=None, inc=True):
    return dict(sid=r["sid"], day=r["day"], row=r,
                b=dict(side=r["side"], letter=r["grade"], inc=True),
                r=dict(side=side or r["side"], letter=letter if letter is not None else r["grade"], inc=inc))


def R_a(th):
    def f(rows):
        out = []
        for r in rows:
            if r["main"] and r["gap"] is not None and abs(r["gap"]) >= th:
                s = soft(r)
                out.append(unit(r, side=s if s != r["side"] else r["side"]))
        return out
    return f


def R_a3(rows):
    out = []
    for r in rows:
        if r["main"] and r["gap"] == 0 and r["ud_nv_over"] is not None and abs(r["ud_nv_over"] - 0.5) >= 0.03:
            ps = "over" if r["ud_nv_over"] > 0.5 else "under"
            out.append(unit(r, side=ps))
    return out


def R_b1(rows):
    out = []
    for r in rows:
        if r["main"] and r["gap"] is not None and abs(r["gap"]) >= 0.5:
            out.append(unit(r, letter=step(r["grade"], 1 if soft(r) == r["side"] else -1)))
    return out


def mkt(r):
    b = r["bump"]
    if b is None or b == 0:
        return None
    return "agree" if (b > 0) == (r["side"] == "over") else "disagree"


def R_d1(rows):
    return [unit(r, side=other(r["side"]) if mkt(r) == "disagree" else r["side"]) for r in rows if mkt(r)]


def R_d2(rows):
    return [unit(r, letter=step(r["grade"], 1 if mkt(r) == "agree" else -1)) for r in rows if mkt(r)]


def R_c1(rows):
    pms = collections.defaultdict(list)
    for r in rows:
        if r["main"] and r["gap"] is not None:
            pms[r["pm"]].append(r)
    out = []
    for pm, rs in pms.items():
        plats = {x["platform"] for x in rs}
        if len(plats) < 2:
            continue
        # one row per app main line (if an app has several settled lines for this player-market, each is compared to the other app's)
        sides = {x["side"] for x in rs}
        lines = {x["line"] for x in rs}
        if len(sides) != 1 or len(lines) < 2:
            continue
        s = sides.pop()
        keep = min(lines) if s == "over" else max(lines)
        for x in rs:
            out.append(unit(x, inc=(x["line"] == keep)))
    return out


RULES = {"R-a1": (R_a(0.5), "side"), "R-a2": (R_a(1.0), "side"), "R-a3": (R_a3, "side"), "R-b1": (R_b1, "letter"),
         "R-c1": (R_c1, "side"), "R-d1": (R_d1, "side"), "R-d2": (R_d2, "letter")}


# ---- metrics
def met(units, k, kind):
    """k = 'b' or 'r'. side kind: graded included rows; letter kind: lettered included rows."""
    sel = []
    for u in units:
        v = u[k]
        if not v["inc"] or not u["row"]["graded"]:
            continue
        if kind == "letter" and v["letter"] not in ORDER[1:]:
            continue
        sel.append((v["side"], win(u["row"], v["side"])))
    n = len(sel)
    if not n:
        return dict(n=0)
    ov = [w for s, w in sel if s == "over"]
    un = [w for s, w in sel if s == "under"]
    return dict(n=n, hit=sum(w for _, w in sel) / n, over_n=len(ov), over_hit=(sum(ov) / len(ov)) if ov else None,
                under_n=len(un), under_hit=(sum(un) / len(un)) if un else None, over_share=len(ov) / n,
                W=sum(w for _, w in sel), L=n - sum(w for _, w in sel))


def changed(units, kind):
    return sum(1 for u in units if (u["b"]["side"] != u["r"]["side"] or u["b"]["inc"] != u["r"]["inc"]) if kind == "side") + \
        sum(1 for u in units if u["b"]["letter"] != u["r"]["letter"] if kind == "letter")


def gate(fn, kind, train_rows):
    us = fn(train_rows)
    if changed(us, kind) < MINCHG:
        return False, "fewer than 30 changes in training"
    mb, mr = met(us, "b", kind), met(us, "r", kind)
    if not mb.get("n") or not mr.get("n"):
        return False, "empty"
    return mr["hit"] > mb["hit"], f"train hit {mr['hit']:.3f} vs {mb['hit']:.3f}"


def wf_units(fn, kind):
    days = sorted({r["day"] for r in ROWS if r["day"] >= TEST0})
    out, log = [], []
    for d in days:
        on, why = gate(fn, kind, [r for r in ROWS if r["day"] < d])
        us = fn([r for r in ROWS if r["day"] == d])
        if not on:
            for u in us:
                u["r"] = dict(u["b"])
        out += us
        log.append((d, on, why))
    return out, log


def boot(units, kind):
    g = collections.defaultdict(list)
    for u in units:
        g[u["sid"]].append(u)
    keys = list(g)
    rng = random.Random(SEED)
    diffs, ob, orr, ub, ur = [], [], [], [], []
    for _ in range(B):
        s = [u for k in (keys[rng.randrange(len(keys))] for _ in keys) for u in g[k]]
        a, b = met(s, "r", kind), met(s, "b", kind)
        if a.get("n") and b.get("n"):
            diffs.append(a["hit"] - b["hit"])
            orr.append(a["hit"]); ob.append(b["hit"])
    pc = lambda x: (round(100 * float(np.percentile(x, 2.5)), 1), round(100 * float(np.percentile(x, 97.5)), 1)) if x else None
    return dict(diff_ci=pc(diffs), rule_ci=pc(orr), base_ci=pc(ob))


def summarize(units, kind):
    mb, mr = met(units, "b", kind), met(units, "r", kind)
    bt = boot(units, kind)
    r1 = lambda x: None if x is None else round(100 * x, 1)
    res = dict(universe_rows=len(units), series=len({u["sid"] for u in units}), changed=changed(units, kind),
               base={k: (r1(v) if isinstance(v, float) else v) for k, v in mb.items()},
               rule={k: (r1(v) if isinstance(v, float) else v) for k, v in mr.items()}, **bt)
    if mb.get("n") and mr.get("n"):
        c1 = bt["diff_ci"] is not None and bt["diff_ci"][0] > 0
        c2 = all((mr[k] is None and mb[k] is None) or (mr[k] is not None and (mb[k] is None or mr[k] >= mb[k] - 1e-12))
                 for k in ("over_hit", "under_hit"))
        c3 = mr["n"] >= mb["n"]
        c4 = abs(mr["over_share"] - 0.5) <= abs(mb["over_share"] - 0.5) + 1e-12
        res["checks"] = dict(ci_above_0=c1, over_and_under_not_worse=c2, count_not_reduced=c3, split_not_more_lopsided=c4)
        res["passes"] = bool(c1 and c2 and c3 and c4)
    return res


OUT = dict(stats=D["stats"])
for name, (fn, kind) in RULES.items():
    wfu, log = wf_units(fn, kind)
    OUT[name] = dict(kind=kind, walk_forward_test=summarize(wfu, kind), gate_log=log,
                     fixed_test=summarize(fn([r for r in ROWS if r["day"] >= TEST0]), kind),
                     fixed_all_days=summarize(fn(ROWS), kind))
    print(name, json.dumps(OUT[name]["walk_forward_test"]), flush=True)


# ---- descriptive evidence (not rules): hit of the baseline side by feature bucket, all days and test days
def bucket(rows, keyf):
    g = collections.defaultdict(list)
    for r in rows:
        k = keyf(r)
        if k is not None and r["graded"]:
            g[k].append(r)
    out = {}
    for k, rs in sorted(g.items(), key=lambda kv: str(kv[0])):
        us = [unit(r) for r in rs]
        m = met(us, "b", "side")
        bt = boot(us, "side")
        out[str(k)] = dict(n=m["n"], series=len({r["sid"] for r in rs}), hit=round(100 * m["hit"], 1), ci=bt["base_ci"],
                           over_n=m["over_n"], over_hit=None if m["over_hit"] is None else round(100 * m["over_hit"], 1),
                           under_n=m["under_n"], under_hit=None if m["under_hit"] is None else round(100 * m["under_hit"], 1))
    return out


def gbucket(r):
    if not r["main"] or r["gap"] is None:
        return None
    if r["gap"] == 0:
        return "lines equal"
    a = "Scout agrees with soft side" if soft(r) == r["side"] else "Scout disagrees"
    return f"{a} |gap|{'>=1' if abs(r['gap']) >= 1 else '=0.5'}"


def bbucket(r):
    m = mkt(r)
    if r["bump"] is None:
        return "open unknown"
    if m is None:
        return "no bump"
    h = r["bump_hours"]
    return f"market {m}s" + ("" if h is None else (" (last move <6 h before start)" if h < 6 else " (>=6 h)"))


def pbucket(r):
    if not r["main"] or r["gap"] != 0 or r["ud_nv_over"] is None:
        return None
    if abs(r["ud_nv_over"] - 0.5) < 0.03:
        return "price gap <0.03"
    ps = "over" if r["ud_nv_over"] > 0.5 else "under"
    return "Scout agrees with UD price side" if ps == r["side"] else "Scout disagrees with UD price side"


OUT["descriptive"] = {}
for nm, rows in (("all_days", ROWS), ("test_days", [r for r in ROWS if r["day"] >= TEST0])):
    OUT["descriptive"][nm] = dict(gap=bucket(rows, gbucket), bump=bucket(rows, bbucket), price=bucket(rows, pbucket),
                                  soft_side_platform=bucket(rows, lambda r: (r["platform"], "agrees" if soft(r) == r["side"] else "disagrees")
                                                            if r["main"] and r["gap"] not in (None, 0) else None))
json.dump(OUT, open(sys.argv[2], "w"), indent=1, default=str)
print(json.dumps(OUT["descriptive"], indent=1))
