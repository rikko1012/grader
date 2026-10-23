"""Walk-forward contexts, tuning objective and the registered benchmark (REGISTER.md).

    python bench.py contexts dev      build leakage-free per-match contexts for D1..D4 (FINAL never touched)
    python bench.py contexts final    ... for FINAL too (only after the candidate is frozen)
    python bench.py frozen dev|final  run the live hltv._predict_maps on every BO3 context (cached to scratch)
    python bench.py score dev|final   score M0/M1/M2/M3 -> research/map_veto/results_<dev|final>.json

Every context is taken from the day-by-day VetoTable BEFORE that day's matches are added: team histories, Elo,
lineups (each team's previous match), pool-wide rates and template counts all predate the match.
"""
import bisect, collections, datetime as dt, gzip, json, math, os, random, sqlite3, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
import veto_model as vm  # noqa: E402

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\veto\vmodel"
SRC = os.path.join(os.path.dirname(os.path.dirname(SCR)), "cs2acc", "data", "hltv_window.sqlite")
PERIODS = [("D1", "2025-01-01", "2025-06-30"), ("D2", "2025-07-01", "2025-12-31"), ("D3", "2026-01-01", "2026-04-30"),
           ("D4", "2026-05-01", "2026-08-22"), ("FINAL", "2026-08-23", "2026-10-22")]
GHL = (60.0, 120.0, 365.0)
FLOOR = 1e-9


def period_of(d):
    for name, a, b in PERIODS:
        if a <= d <= b:
            return name
    return None


def load_records():
    with gzip.open(os.path.join(SCR, "veto_dataset.json.gz"), "rt", encoding="utf-8") as f:
        return json.load(f)["records"]


# ------------------------------------------------------------------ contexts
def build_contexts(which):
    recs = load_records()
    end = "2026-08-22" if which == "dev" else "2026-10-22"
    by = collections.defaultdict(list)
    for r in recs:
        if r["date"] <= end:
            by[r["date"]].append(r)
    tables = {g: vm.VetoTable(keep=10 ** 6, glob_half_life=g) for g in GHL}
    main = tables[120.0]
    tmpl_plain = collections.Counter()          # undecayed format mix for M1
    snaps, ctxs = {}, []
    for d in sorted(by):
        day = vm._day(d)
        per = period_of(d)
        if per and (which == "final" or per != "FINAL"):
            for g, t in tables.items():
                t._decay_to(day)
            snaps[day] = {g: (dict(t.gC), dict(t.gE), dict(t.tmpl_g)) for g, t in tables.items()}
            snaps[day]["plain"] = dict(tmpl_plain)
            for r in by[d]:
                if r["status"] != "complete":
                    continue
                ctxs.append({"mid": r["mid"], "date": d, "day": day, "period": per, "fmt": r["fmt"], "pool": r["pool"],
                             "teams": r["teams"], "first": r["first"], "template": r["template"],
                             "actions": r["actions"], "event": r["event"], "listed": r["listed"],
                             "lineup_prev": {t: sorted(main.last_lineup[t][1]) if t in main.last_lineup else None
                                             for t in r["teams"]},
                             "elo": {t: main.elo[t] for t in r["teams"]},
                             "tmpl_ev": dict(main.tmpl_ev.get((r["event"], r["fmt"]), {}))})
        for t in tables.values():
            t.add_day(by[d], d)
        for r in by[d]:
            if r["status"] in ("complete", "inferred"):
                tmpl_plain[(r["fmt"], r["template"])] += 1
    js = {str(d): {("plain" if g == "plain" else repr(g)): (_items(v) if g == "plain" else [_items(x) for x in v])
                   for g, v in s.items()} for d, s in snaps.items()}
    with gzip.open(os.path.join(SCR, f"ctx_{which}.json.gz"), "wt", encoding="utf-8") as f:
        json.dump({"ctxs": ctxs, "snaps": js, "main": main.to_json()}, f)
    print(which, len(ctxs), collections.Counter((c["period"], c["fmt"]) for c in ctxs))


def _items(d):
    return [list(k) + [v] for k, v in d.items()]


def _dict(items):
    return {tuple(x[:-1]): x[-1] for x in items}


def load_contexts(which):
    with gzip.open(os.path.join(SCR, f"ctx_{which}.json.gz"), "rt", encoding="utf-8") as f:
        d = json.load(f)
    snaps = {}
    for day, s in d["snaps"].items():
        snaps[int(day)] = {("plain" if g == "plain" else float(g)): (_dict(v) if g == "plain" else tuple(_dict(x) for x in v))
                           for g, v in s.items()}
    for c in d["ctxs"]:
        c["lineup_prev"] = {int(k): v for k, v in c["lineup_prev"].items()}
        c["elo"] = {int(k): v for k, v in c["elo"].items()}
    return {"ctxs": d["ctxs"], "snaps": snaps, "main": vm.VetoTable.from_json(d["main"])}


# ------------------------------------------------------------------ the candidate on a context
STATS_KEYS = ("half_life_days", "k_team", "k_glob", "glob_half_life_days", "max_records", "rho", "identity",
              "fmt_split", "k_strength")


def stats_key(p):
    return tuple(json.dumps(p[k], sort_keys=True) for k in STATS_KEYS)


class Cand:
    def __init__(self, data):
        self.ctxs, self.snaps, self.table = data["ctxs"], data["snaps"], data["main"]
        self.cache_key, self.cache = None, {}

    def stats(self, c, p):
        k = stats_key(p)
        if k != self.cache_key:
            self.cache_key, self.cache = k, {}
        hit = self.cache.get(c["mid"])
        if hit:
            return hit
        gC, gE, _ = self.snaps[c["day"]][p["glob_half_life_days"]]
        ta, tb = c["teams"]
        out = (self.table.team_stats(ta, c["lineup_prev"][ta], c["day"], c["pool"], p, (gC, gE)),
               self.table.team_stats(tb, c["lineup_prev"][tb], c["day"], c["pool"], p, (gC, gE)))
        self.cache[c["mid"]] = out
        return out

    def W(self, c, p):
        sa, sb = self.stats(c, p)
        return vm._utils(sa, sb, p, c["fmt"])

    def tprior(self, c, p):
        tg = self.snaps[c["day"]][p["glob_half_life_days"]][2]
        return vm.template_prior(c["fmt"], tg, c["tmpl_ev"], p)

    def pa(self, c, p):
        ta, tb = c["teams"]
        z = p["first_a0"] + p["first_elo"] * (c["elo"][ta] - c["elo"][tb]) / 100.0
        if p["first_listed"] and c["listed"]:
            z += p["first_listed"] * (1 if c["listed"][0] == ta else -1)
        return 1 / (1 + math.exp(-z))

    def step_ll(self, c, p):
        """log P(observed steps | observed order) under the candidate (the separable step part of the sequence)."""
        W = self.W(c, p)
        ta, tb = c["teams"]
        first = c["first"]
        steps = vm._PARSED[c["template"]]
        left = list(c["pool"])
        ll = 0.0
        eps = p["eps"]
        for (X, act, kind), (s, team, a, m) in zip(steps, c["actions"]):
            real = first if X == "A" else (tb if first == ta else ta)
            w = W[("A" if real == ta else "B", kind)]
            tot = sum(w[x] for x in left)
            q = (1 - eps) * w[m] / tot + eps / len(left)
            ll += math.log(q)
            left.remove(m)
        return ll

    def order_ll(self, c, p):
        tp = self.tprior(c, p)[c["template"]]
        pa = self.pa(c, p)
        return math.log(tp), math.log(pa if c["first"] == c["teams"][0] else 1 - pa)

    def dist(self, c, p, known=None):
        ta, tb = c["teams"]
        return vm.sequence_distribution(self.W(c, p), c["fmt"], c["pool"], self.tprior(c, p), self.pa(c, p),
                                        known or {}, p["eps"], ta, tb)[0]


# ------------------------------------------------------------------ baselines
def uniform_W(pool):
    one = {m: 1.0 for m in pool}
    return {(X, k): one for X in "AB" for k in ("ban1", "banN", "pick")}


def m0_dist(c, known=None):
    tp = {t: 1.0 / len(vm.TEMPLATES[c["fmt"]]) for t in vm.TEMPLATES[c["fmt"]]}
    return vm.sequence_distribution(uniform_W(c["pool"]), c["fmt"], c["pool"], tp, 0.5, known or {}, 0.0,
                                    *c["teams"])[0]


def m1_dist(c, table, snaps, known=None):
    W = {}
    for X, t in zip("AB", c["teams"]):
        recs = table._upto(t, c["day"])
        recs = [r for r in recs if r.acts][-10:]
        rem = collections.Counter(m for r in recs for k, m, _ in r.acts if k != "pick")
        pk = collections.Counter(m for r in recs for k, m, _ in r.acts if k == "pick")
        wr = {m: rem[m] + 1.0 for m in c["pool"]}
        wp = {m: pk[m] + 1.0 for m in c["pool"]}
        W[(X, "ban1")] = W[(X, "banN")] = wr
        W[(X, "pick")] = wp
    plain = snaps[c["day"]]["plain"]
    ts = vm.TEMPLATES[c["fmt"]]
    g = {t: plain.get((c["fmt"], t), 0) + 0.5 for t in ts}
    tot = sum(g.values())
    tp = {t: v / tot for t, v in g.items()}
    return vm.sequence_distribution(W, c["fmt"], c["pool"], tp, 0.5, known or {}, 0.0, *c["teams"])[0]


# ------------------------------------------------------------------ frozen simulator (live code, emulated DB query)
def frozen_histories(recs):
    """hltv._team_history semantics on the export: team matched by normalised name (all team ids with that name), the
    50 latest dated matches before the date (DISTINCT matchid, date ORDER BY date DESC), 7-step vetoes, up to 25."""
    con = sqlite3.connect(SRC)
    tname = {r[0]: vm.norm(r[1]) for r in con.execute("select teamid, name from teams")}
    group = collections.defaultdict(set)
    for t, n in tname.items():
        group[n].add(t)
    by_name = collections.defaultdict(list)
    for r in recs:
        for t in r["teams"]:
            by_name[tname.get(t)].append(r)
    for n in by_name:
        by_name[n].sort(key=lambda r: (r["date"], r["mid"]))
        # de-duplicate (a match listed under two ids with the same name)
        seen, out = set(), []
        for r in by_name[n]:
            if r["mid"] not in seen:
                seen.add(r["mid"])
                out.append(r)
        by_name[n] = out
    return tname, group, by_name


def frozen_team_history(name, before, tname, group, by_name, n=25):
    me_ids = group.get(name, set())
    lst = by_name.get(name, [])
    dates = [r["date"] for r in lst]
    i = bisect.bisect_left(dates, before)
    cand = list(reversed(lst[:i]))[: n * 2]
    hist = {"ban1": {}, "pick": {}, "ban2": {}, "n": 0}
    for r in cand:
        v = [a for a in r["actions"] if not (r.get("inferred_leftover") and a[2] == "leftover")]
        if len(v) != 7 or hist["n"] >= n:
            continue
        hist["n"] += 1
        for step, team, act, mp in v:
            if team not in me_ids:
                continue
            role = "pick" if act == "picked" else "ban1" if step <= 2 else "ban2"
            hist[role][mp] = hist[role].get(mp, 0) + 1
    return hist


def run_frozen(which):
    import hltv
    recs = load_records()
    tname, group, by_name = frozen_histories(recs)
    data = load_contexts(which)
    out_p = os.path.join(SCR, f"frozen_{which}.json")
    done = json.load(open(out_p)) if os.path.exists(out_p) else {}

    class _Dummy:
        def cursor(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            pass
    hltv._connect = lambda: _Dummy()
    t0 = time.time()
    for i, c in enumerate(data["ctxs"]):
        if c["fmt"] != "bo3" or str(c["mid"]) in done:
            continue
        ta, tb = c["teams"]
        na, nb = tname.get(ta), tname.get(tb)
        hh = {na: frozen_team_history(na, c["date"], tname, group, by_name),
              nb: frozen_team_history(nb, c["date"], tname, group, by_name)}
        hltv._team_history = lambda cur, team, before, n=25: hh[hltv._norm(team)]
        res = hltv._predict_maps(na, nb, c["date"], None, 4000, 7, random, c["pool"])
        done[str(c["mid"])] = None if res is None else {"map1": res["map1"], "map2": res["map2"],
                                                         "history": res["history"]}
        if i % 500 == 0:
            print(i, round(time.time() - t0), flush=True)
            json.dump(done, open(out_p, "w"))
    json.dump(done, open(out_p, "w"))
    print("frozen done", len(done), sum(v is not None for v in done.values()))


# ------------------------------------------------------------------ scoring
def forecast_from_dist(dist, fmt):
    n = vm.N_MAPS[fmt]
    pos = [collections.defaultdict(float) for _ in range(n)]
    incl = collections.defaultdict(float)
    sets = collections.defaultdict(float)
    pair = collections.defaultdict(float)
    for seq, p in dist.items():
        for i, m in enumerate(seq):
            pos[i][m] += p
            incl[m] += p
        sets[tuple(sorted(seq))] += p
        if fmt == "bo3":
            pair[seq[:2]] += p
    return {"pos": pos, "incl": incl, "set": sets, "seq": dist, "pair": pair if fmt == "bo3" else None}


def forecast_frozen(fz, pool):
    p1, p2 = fz["map1"], fz["map2"]
    pair = {}
    for a in pool:
        for b in pool:
            if a != b:
                pair[(a, b)] = p1.get(a, 0) * p2.get(b, 0) / max(1 - p2.get(a, 0), 1e-12)
    z = sum(pair.values())
    pair = {k: v / z for k, v in pair.items()}
    return {"pos": [p1, p2, None], "incl": None, "set": None, "seq": None, "pair": pair}


def actual(c):
    picks = [a[3] for a in c["actions"] if a[2] == "picked"]
    left = [a[3] for a in c["actions"] if a[2] == "leftover"]
    seq = tuple(picks + left)
    return {"pos": list(seq), "set": tuple(sorted(seq)), "seq": seq, "pair": tuple(seq[:2]) if c["fmt"] == "bo3" else None}


def score_one(fc, act, pool):
    """{target: (logloss, brier, top1)} for one forecast."""
    out = {}
    for i, m in enumerate(act["pos"]):
        d = fc["pos"][i] if i < len(fc["pos"]) else None
        if d is None:
            continue
        p = max(d.get(m, 0.0), FLOOR)
        br = sum((d.get(x, 0.0) - (x == m)) ** 2 for x in pool)
        top = max(sorted(pool), key=lambda x: d.get(x, 0.0))
        out[f"pos{i + 1}"] = (-math.log(p), br, float(top == m))
    if fc["incl"] is not None:
        sel = set(act["seq"])
        ll = br = 0.0
        for x in pool:
            q = min(max(fc["incl"].get(x, 0.0), FLOOR), 1 - FLOOR)
            y = x in sel
            ll += -math.log(q if y else 1 - q)
            br += (q - y) ** 2
        k = len(sel)
        top = set(sorted(pool, key=lambda x: (-fc["incl"].get(x, 0.0), x))[:k])
        out["incl"] = (ll / len(pool), br / len(pool), len(top & sel) / k)
    for key in ("set", "seq", "pair"):
        d = fc.get(key)
        if d is None or act[key] is None:
            continue
        p = max(d.get(act[key], 0.0), FLOOR)
        top = max(sorted(d), key=lambda x: d[x]) if d else None
        br = sum(v * v for v in d.values()) - 2 * d.get(act[key], 0.0) + 1
        out[key] = (-math.log(p), br, float(top == act[key]))
    return out


def label(fmt, target):
    if target.startswith("pos"):
        i = int(target[3:])
        n = vm.N_MAPS[fmt]
        return "decider" if (i == n and fmt != "bo1") else ("map" if fmt == "bo1" else f"map{i}")
    return target


def boot(vals, groups=None, B=2000, seed=20261023):
    """Mean + 95% percentile interval over matches (one value per match: match-grouped)."""
    n = len(vals)
    if n == 0:
        return None
    rnd = random.Random(seed)
    mean = sum(vals) / n
    bs = []
    for _ in range(B):
        s = 0.0
        for _ in range(n):
            s += vals[rnd.randrange(n)]
        bs.append(s / n)
    bs.sort()
    return [round(mean, 4), round(bs[int(0.025 * B)], 4), round(bs[int(0.975 * B) - 1], 4)]


def boot_fast(vals, B=2000, seed=20261023):
    import numpy as np
    v = np.asarray(vals, dtype=float)
    if len(v) == 0:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(v), size=(B, len(v)))
    bs = np.sort(v[idx].mean(axis=1))
    return [round(float(v.mean()), 4), round(float(bs[int(0.025 * B)]), 4), round(float(bs[int(0.975 * B) - 1]), 4)]


def reliability(pairs, bins=10):
    b = [[0, 0.0, 0.0] for _ in range(bins)]
    for p, y in pairs:
        i = min(int(p * bins), bins - 1)
        b[i][0] += 1
        b[i][1] += p
        b[i][2] += y
    n = sum(x[0] for x in b) or 1
    ece = sum(abs(x[1] - x[2]) for x in b if x[0]) / n
    return {"ece": round(ece, 4), "bins": [[round(i / bins, 1), x[0], round(x[1] / x[0], 4) if x[0] else None,
                                            round(x[2] / x[0], 4) if x[0] else None] for i, x in enumerate(b)]}


def score(which, params=None):
    params = params or vm.PARAMS
    data = load_contexts(which)
    cand = Cand(data)
    fz = json.load(open(os.path.join(SCR, f"frozen_{which}.json")))
    periods = ["D1", "D2", "D3", "D4"] if which == "dev" else ["FINAL"]
    rows = collections.defaultdict(list)       # (period, fmt, target, model) -> [(mid, ll, br, top)]
    calib = collections.defaultdict(list)       # (period-group, model, target) -> [(p, y)]
    cov = collections.Counter()
    live = collections.defaultdict(list)
    t0 = time.time()
    for i, c in enumerate(data["ctxs"]):
        if c["period"] not in periods:
            continue
        pool, fmt = c["pool"], c["fmt"]
        act = actual(c)
        fcs = {"M0": forecast_from_dist(m0_dist(c), fmt), "M1": forecast_from_dist(m1_dist(c, cand.table, cand.snaps), fmt),
               "M3": forecast_from_dist(cand.dist(c, params), fmt),
               # sensitivity: the candidate without the HLTV listing order (F7 off)
               "M3nl": forecast_from_dist(cand.dist(c, {**params, "first_listed": 0.0}), fmt)}
        cov[(c["period"], fmt, "all")] += 1
        if fmt == "bo3":
            f = fz.get(str(c["mid"]))
            if f is not None:
                fcs["M2"] = forecast_frozen(f, pool)
                cov[(c["period"], fmt, "M2")] += 1
            fcs["M2full"] = fcs.get("M2") or {**fcs["M0"], "incl": None, "set": None, "seq": None}
            if "M2" not in fcs:
                fcs["M2full"] = {"pos": fcs["M0"]["pos"][:2] + [None], "incl": None, "set": None, "seq": None,
                                 "pair": fcs["M0"]["pair"]}
        for mdl, fc in fcs.items():
            for tgt, v in score_one(fc, act, pool).items():
                rows[(c["period"], fmt, tgt, mdl)].append((c["mid"],) + v)
            grp = "FINAL" if c["period"] == "FINAL" else "dev"
            for j in range(min(2, len(fc["pos"]))):
                if fc["pos"][j] is not None and j < len(act["pos"]):
                    for x in pool:
                        calib[(grp, fmt, mdl, f"pos{j + 1}")].append((fc["pos"][j].get(x, 0.0), float(x == act["pos"][j])))
            if fc["incl"] is not None:
                for x in pool:
                    calib[(grp, fmt, mdl, "incl")].append((fc["incl"].get(x, 0.0), float(x in act["seq"])))
        # live updates: first 2 / first 4 confirmed actions (M2 cannot condition)
        if fmt in ("bo3", "bo1"):
            for k in (2, 4):
                known = {a[0]: {"step": a[0], "team": a[1], "action": a[2], "map": a[3]} for a in c["actions"][:k]}
                for mdl, fn in (("M0", lambda: m0_dist(c, known)), ("M1", lambda: m1_dist(c, cand.table, cand.snaps, known)),
                                ("M3", lambda: cand.dist(c, params, known))):
                    fc = forecast_from_dist(fn(), fmt)
                    for tgt, v in score_one(fc, act, pool).items():
                        if tgt in ("pos1", "pos2", "pos3", "set", "seq"):
                            live[(c["period"], fmt, k, tgt, mdl)].append((c["mid"],) + v)
        if i % 2000 == 0:
            print(i, round(time.time() - t0), flush=True)
    return summarise(rows, calib, cov, live, periods, which, params)


def summarise(rows, calib, cov, live, periods, which, params):
    groups = {p: [p] for p in periods}
    if which == "dev":
        groups["DEV(D1-D4)"] = periods
    res = {"version": vm.version(params), "params": params, "which": which, "coverage": {"|".join(map(str, k)): v for k, v in cov.items()},
           "tables": {}, "diffs": {}, "calibration": {}, "live": {}}
    fmts = ("bo1", "bo3", "bo5")
    for gname, plist in groups.items():
        for fmt in fmts:
            tg = sorted({k[2] for k in rows if k[1] == fmt})
            for tgt in tg:
                per_model = {}
                for mdl in ("M0", "M1", "M2", "M2full", "M3", "M3nl"):
                    vals = [v for p in plist for v in rows.get((p, fmt, tgt, mdl), [])]
                    if not vals:
                        continue
                    per_model[mdl] = {v[0]: v[1:] for v in vals}
                    res["tables"][f"{gname}|{fmt}|{label(fmt, tgt)}|{mdl}"] = {
                        "n": len(vals), "logloss": boot_fast([v[1] for v in vals]),
                        "brier": round(sum(v[2] for v in vals) / len(vals), 4),
                        "top1": round(sum(v[3] for v in vals) / len(vals), 4)}
                for base in ("M0", "M1", "M2", "M2full", "M3nl"):
                    if base in per_model and "M3" in per_model:
                        common = sorted(set(per_model[base]) & set(per_model["M3"]))
                        d = [per_model[base][m][0] - per_model["M3"][m][0] for m in common]
                        res["diffs"][f"{gname}|{fmt}|{label(fmt, tgt)}|{base}-M3"] = {"n": len(common), "ll_diff": boot_fast(d)}
                if "M2" in per_model:          # head-to-head on M2's coverage, all models
                    common = sorted(per_model["M2"])
                    for mdl in ("M0", "M1", "M3", "M3nl"):
                        if mdl in per_model:
                            vv = [per_model[mdl][m][0] for m in common if m in per_model[mdl]]
                            res["tables"][f"{gname}|{fmt}|{label(fmt, tgt)}|{mdl}@M2cov"] = {
                                "n": len(vv), "logloss": boot_fast(vv),
                                "top1": round(sum(per_model[mdl][m][2] for m in common) / len(common), 4)}
    for k, v in calib.items():
        res["calibration"]["|".join(k)] = reliability(v)
    for gname, plist in groups.items():
        for fmt in ("bo1", "bo3"):
            for kk in (2, 4):
                for tgt in ("pos1", "pos2", "pos3", "set", "seq"):
                    per_model = {}
                    for mdl in ("M0", "M1", "M3"):
                        vals = [v for p in plist for v in live.get((p, fmt, kk, tgt, mdl), [])]
                        if vals:
                            per_model[mdl] = vals
                            res["live"][f"{gname}|{fmt}|after{kk}|{label(fmt, tgt)}|{mdl}"] = {
                                "n": len(vals), "logloss": boot_fast([v[1] for v in vals]),
                                "top1": round(sum(v[3] for v in vals) / len(vals), 4)}
                    if "M3" in per_model:
                        for base in ("M0", "M1"):
                            if base in per_model:
                                d = [a[1] - b[1] for a, b in zip(per_model[base], per_model["M3"])]
                                res["live"][f"{gname}|{fmt}|after{kk}|{label(fmt, tgt)}|{base}-M3"] = {"n": len(d), "ll_diff": boot_fast(d)}
    out = os.path.join(HERE, f"results_{which}.json")
    json.dump(res, open(out, "w"), indent=1)
    print("wrote", out)
    return res


if __name__ == "__main__":
    cmd, which = sys.argv[1], sys.argv[2]
    if which == "final" and not os.path.exists(os.path.join(HERE, "FROZEN.json")):
        sys.exit("FINAL is untouched until FROZEN.json (version + params sha) is committed")
    if cmd == "contexts":
        build_contexts(which)
    elif cmd == "frozen":
        run_frozen(which)
    elif cmd == "score":
        score(which)
