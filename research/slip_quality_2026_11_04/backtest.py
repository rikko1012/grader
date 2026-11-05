"""Walk-forward backtest of slip generation on settled history: sq-1 vs the generator it replaces vs random legs from the same pool.

usage: python -m research.slip_quality_2026_11_04.backtest <data dir>
  CS2: the stored P1 export (p1_units.json: the latest board forecast >= 30 min before the start on a standard board line;
       pushes excluded by P1). LoL: lol_units.json (settled LoL pre-match forecasts on offered sides, from lol_join).
Each slate = the units of one UTC start day. The pool is the same for all three generators: the favoured side at a printed chance
of 55% or more (the letter range), one platform, 8 slips per batch. sq-1's band table is rebuilt every day from units that started
on EARLIER days only. Random = legs drawn uniformly from the same pool under the same hard rules (one leg per match, no repeat when
asked), averaged over 200 draws per slate. Leg hit rates carry match-clustered 95% intervals; slip hit rates and paired differences
carry slate-day-clustered intervals (a slip spans matches, so the slate is the smallest independent unit). Writes backtest.json."""
import json
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import slip_quality as sq                                   # noqa: E402
from research.slip_quality_2026_11_04 import old_generator as old   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
B = 2000
MIN_P = 0.55
COUNT = 8
RANDOM_DRAWS = 200


def cs2_units(data):
    ex = json.load(open(os.path.join(data, "p1_units.json")))
    out = []
    for r in ex["rows"]:
        p = r["p"]
        if p is None or r["over"] not in (0, 1):
            continue
        side = "over" if p >= .5 else "under"
        out.append({"id": f"cs2-{r['forecast_id']}", "sport": "cs2", "match": r["match"], "stat": r["prop"], "side": side, "p": max(p, 1 - p),
                    "hit": int(r["over"] if side == "over" else 1 - r["over"]), "day": r["start"][:10], "start": r["start"], "line": r["line"]})
    return out


def lol_units(data):
    out = []
    for u in json.load(open(os.path.join(data, "lol_units.json"))):
        if u.get("offered") != 1 or u["p_under"] is None:
            continue
        side = "over" if u["p"] >= u["p_under"] else "under"
        out.append({"id": f"lol-{u['platform']}-{u['match']}-{u['pid']}-{u['stat']}-{u['line']}-{u['odds_type']}", "sport": "lol", "match": u["match"],
                    "stat": u["stat"], "side": side, "p": u["p"] if side == "over" else u["p_under"], "hit": int(u["over"] if side == "over" else 1 - u["over"]),
                    "day": u["start"][:10], "start": u["start"], "line": u["line"], "pid": u["pid"], "prop": (u["match"], u["pid"], u["stat"], u["scope"])})
    return out


def table_before(units, day):
    prior = [{"sport": u["sport"], "stat": u["stat"], "side": u["side"], "printed": u["p"], "hit": u["hit"], "match": u["match"]} for u in units if u["day"] < day]
    return sq.calibration_table(prior)


def as_leg(u, table):
    q, band = sq.ranked_chance(u["sport"], u["stat"], u["side"], u["p"], 0.0, False, table)
    who = u.get("pid") or u["id"]
    return {"key": u["id"], "sport": u["sport"], "book": "prizepicks", "row_id": u["id"], "side": u["side"], "line": u["line"], "line_type": "standard",
            "event": u["match"], "event_label": u["match"], "player": who, "pkey": who, "team": f"T-{u['match']}-{who}", "stat": u["stat"], "stat_label": u["stat"],
            "period": "p", "period_label": "p", "p": u["p"], "p_push": 0.0, "rank_p": q, "band": band, "research": False, "grade": "B", "multiplier": None,
            "model_version": None, "prop": u.get("prop") or (u["match"], who, u["stat"]), "_u": u}


def as_record(u):
    who = u.get("pid") or u["id"]
    return {"sport": u["sport"], "key": u["id"], "event": {"id": u["match"]}, "player": {"key": str(who), "name": str(who), "id": str(who), "team": f"T-{u['match']}-{who}"},
            "market": {"stat": u["stat"], "period": "p"}, "offer": {"platform": "prizepicks", "line": u["line"], "over": {"offered": True}, "under": {"offered": True}},
            "scout": {"side": u["side"], "p_over": u["p"] if u["side"] == "over" else 1 - u["p"], "p_under": u["p"] if u["side"] == "under" else 1 - u["p"], "grade": "B", "hold": None},
            "state": {"kind": "ok"}, "_u": u}


def run_new(slate, table, size, no_repeat):
    legs = sorted((as_leg(u, table) for u in slate), key=lambda l: (-l["rank_p"], -l["p"], l["key"]))
    for i, l in enumerate(legs):
        l["rank"] = i + 1
    b = sq.build(legs, {"platform": "prizepicks", "entry_type": "power", "count": COUNT, "size": size, "no_repeat": no_repeat})
    return [[l["_u"] for l in s["legs"]] for s in b["slips"]], [s["all_hit"] for s in b["slips"]]


def run_old(slate, size, no_repeat, mode):
    recs = [as_record(u) for u in slate]
    o = old.generate(recs, {"platform": "prizepicks", "entryType": "power", "count": COUNT, "size": size, "mode": mode, "target": 60, "tolerance": 5,
                            "mix": "any", "games": "spread", "noRepeatProps": no_repeat}, check=lambda *a: True)
    return [[r["_u"] for r in s] for s in o["slips"]]


def pkey(u):
    return u.get("prop") or u["id"]


def run_random(slate, size, no_repeat, rng):
    by_match = defaultdict(list)
    for u in slate:
        by_match[u["match"]].append(u)
    slips, used, sigs = [], set(), set()
    for _ in range(COUNT):
        for _try in range(30):
            ms = [m for m, us in by_match.items() if any(not (no_repeat and pkey(x) in used) for x in us)]
            if len(ms) < size:
                return slips
            pick = [rng.choice([x for x in by_match[m] if not (no_repeat and pkey(x) in used)]) for m in rng.sample(ms, size)]
            sig = frozenset(x["id"] for x in pick)
            if sig not in sigs:
                break
        else:
            return slips
        sigs.add(sig)
        used |= {pkey(x) for x in pick}
        slips.append(pick)
    return slips


def boot(groups, stat, rng, b=B):
    """groups: {cluster: [values]}; stat(list of value lists) -> float. Percentile 95% interval."""
    keys = list(groups)
    if len(keys) < 2:
        return None
    out = []
    for _ in range(b):
        s = [groups[keys[rng.randrange(len(keys))]] for _ in keys]
        v = stat(s)
        if v is not None:
            out.append(v)
    out.sort()
    return [round(out[int(.025 * len(out))], 4), round(out[int(.975 * len(out)) - 1], 4)]


def ratio(s):
    n = sum(len(x) for x in s)
    return sum(sum(x) for x in s) / n if n else None


def summarise(name, rows, rng):
    """rows: [{day, legs: [unit], hit}] one per slip (random: weight w)."""
    if not rows:
        return {"generator": name, "slips": 0}
    legs_by_match, slips_by_day = defaultdict(list), defaultdict(list)
    for s in rows:
        w = s.get("w", 1.0)
        for u in s["legs"]:
            legs_by_match[u["match"]].append((u["hit"], w))
        slips_by_day[s["day"]].append((s["hit"], w))
    wr = lambda s: (lambda n: sum(h * w for x in s for h, w in x) / n if n else None)(sum(w for x in s for _, w in x))
    lg, sg = dict(legs_by_match), dict(slips_by_day)
    nslips = sum(w for x in sg.values() for _, w in x)
    nlegs = sum(w for x in lg.values() for _, w in x)
    return {"generator": name, "slips": round(nslips, 1), "days": len(sg), "legs": round(nlegs, 1), "matches": len(lg),
            "leg_hit": round(wr(list(lg.values())), 4), "leg_hit_ci": boot(lg, wr, rng),
            "printed": round(sum(u["p"] * s.get("w", 1) for s in rows for u in s["legs"]) / nlegs, 4),
            "slip_hit": round(wr(list(sg.values())), 4), "slip_hit_ci": boot(sg, wr, rng)}


def paired(a_rows, b_rows, rng):
    """Day-clustered difference in slip hit rate (a - b) over days where both built slips."""
    da, db = defaultdict(list), defaultdict(list)
    for s in a_rows:
        da[s["day"]].append((s["hit"], s.get("w", 1.0)))
    for s in b_rows:
        db[s["day"]].append((s["hit"], s.get("w", 1.0)))
    days = sorted(set(da) & set(db))
    if len(days) < 2:
        return None
    g = {d: (da[d], db[d]) for d in days}
    def diff(s):
        na, nb = sum(w for x, _ in s for _, w in x), sum(w for _, y in s for _, w in y)
        return sum(h * w for x, _ in s for h, w in x) / na - sum(h * w for _, y in s for h, w in y) / nb
    keys = list(g)
    point = diff([g[d] for d in keys])
    out = sorted(diff([g[keys[rng.randrange(len(keys))]] for _ in keys]) for _ in range(B))
    return {"days": len(days), "diff": round(point, 4), "ci": [round(out[int(.025 * B)], 4), round(out[int(.975 * B) - 1], 4)]}


def backtest(units, label, sizes=(2, 3, 4), seed=7):
    rng = random.Random(seed)
    days = sorted({u["day"] for u in units})
    first = days[min(3, len(days) - 1)]                         # sq-1 needs some earlier settled days for its table
    out = {"label": label, "units": len(units), "days": [d for d in days if d >= first], "runs": []}
    for size in sizes:
        for no_repeat in (False, True):
            rows = {"sq-1": [], "old balanced 60±5 (default)": [], "old highest chance": [], "random": []}
            pred = []
            for d in days:
                if d < first:
                    continue
                slate = [u for u in units if u["day"] == d and u["p"] >= MIN_P]
                if len({u["match"] for u in slate}) < size:
                    continue
                table = table_before(units, d)
                ns, ah = run_new(slate, table, size, no_repeat)
                for s, a in zip(ns, ah):
                    rows["sq-1"].append({"day": d, "legs": s, "hit": int(all(u["hit"] for u in s))})
                    pred.append(a)
                for mode, name in (("balanced", "old balanced 60±5 (default)"), ("ranked", "old highest chance")):
                    for s in run_old(slate, size, no_repeat, mode):
                        rows[name].append({"day": d, "legs": s, "hit": int(all(u["hit"] for u in s))})
                rs = [run_random(slate, size, no_repeat, rng) for _ in range(RANDOM_DRAWS)]
                for x in rs:
                    for s in x:
                        rows["random"].append({"day": d, "legs": s, "hit": int(all(u["hit"] for u in s)), "w": 1.0 / RANDOM_DRAWS})
            run = {"size": size, "no_repeat": no_repeat, "summary": [summarise(k, v, rng) for k, v in rows.items()],
                   "sq1_vs_old_default": paired(rows["sq-1"], rows["old balanced 60±5 (default)"], rng),
                   "sq1_vs_old_highest": paired(rows["sq-1"], rows["old highest chance"], rng),
                   "sq1_vs_random": paired(rows["sq-1"], rows["random"], rng),
                   "sq1_predicted_all_hit": round(sum(pred) / len(pred), 4) if pred else None}
            out["runs"].append(run)
            print(f"\n{label} {size}-pick, no repeat={no_repeat}  (sq-1 predicted all-hit {run['sq1_predicted_all_hit']})")
            for s in run["summary"]:
                if s.get("slips"):
                    print(f"  {s['generator']:30} slips={s['slips']:7} days={s['days']:3} legs={s['legs']:8} printed={s['printed']:.3f} "
                          f"leg hit={s['leg_hit']:.3f} {s['leg_hit_ci']}  slip hit={s['slip_hit']:.3f} {s['slip_hit_ci']}")
            for k in ("sq1_vs_old_default", "sq1_vs_old_highest", "sq1_vs_random"):
                print(f"  {k}: {run[k]}")
    return out


def main(data):
    res = {"cs2": backtest(cs2_units(data), "CS2 (P1)")}
    if os.path.exists(os.path.join(data, "lol_units.json")):
        res["lol"] = backtest(lol_units(data), "LoL (settled forecasts)", sizes=(2, 3))
    json.dump(res, open(os.path.join(HERE, "backtest.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
