"""Which ranking rule should sq-1 use? Walk-forward, same slates and hard rules as backtest.py, point estimates plus a day-clustered
interval of the slip hit rate difference against random legs from the same pool. Chosen BEFORE reading backtest.py's final numbers
only in the sense that every rule here is fixed in advance; the rule that ships is reported with the others, never alone.

usage: python -m research.slip_quality_2026_11_04.variants <data dir>   -> variants.json"""
import json
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import slip_quality as sq                                   # noqa: E402
from research.slip_quality_2026_11_04 import backtest as bt   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ORIG = sq.ranked_chance


def _cells(table):
    return (table or {}).get("cells") or {}


def _c(table, sport, stat, side, p):
    i = sq.band_index(p)
    return _cells(table).get(f"{sport}|{stat}|{side}|{i}") if i is not None else None


def printed(sport, stat, side, p, p_push, research, table):
    """The published chance as it is (what 'old highest chance' sorts by)."""
    return p


def shift(sport, stat, side, p, p_push, research, table):
    """Draft 1: the published chance moved by its band's shrunk gap (keeps the order inside a band)."""
    c = _c(table, sport, stat, side, p)
    return p + c["n"] / (c["n"] + table["k"]) * (c["observed"] - c["printed"]) if c else p


def level_printed(sport, stat, side, p, p_push, research, table):
    """Draft 2: the band's settled hit rate, shrunk toward the published chance."""
    c = _c(table, sport, stat, side, p)
    return (c["n"] * c["observed"] + table["k"] * p) / (c["n"] + table["k"]) if c else p


def level_line(sport, stat, side, p, p_push, research, table):
    """sq-1 as shipped: the band's settled hit rate, shrunk toward its stat's settled line (slip_quality.ranked_chance)."""
    return ORIG(sport, stat, side, p, p_push, research, table)[0]


RULES = {"printed chance": printed, "draft 1: shifted chance": shift, "draft 2: band level, printed prior": level_printed,
         "sq-1: band level, settled-line prior": level_line}


def run(units, sizes, label, rng):
    days = sorted({u["day"] for u in units})
    first = days[min(3, len(days) - 1)]
    out = []
    for size in sizes:
        for no_repeat in (False, True):
            res = {k: defaultdict(list) for k in list(RULES) + ["random"]}
            for d in days:
                if d < first:
                    continue
                slate = [u for u in units if u["day"] == d and u["p"] >= bt.MIN_P]
                if len({u["match"] for u in slate}) < size:
                    continue
                table = bt.table_before(units, d)
                for name, fn in RULES.items():
                    sq.ranked_chance = lambda *a, fn=fn: (fn(*a), None)
                    try:
                        slips, _ = bt.run_new(slate, table, size, no_repeat)
                    finally:
                        sq.ranked_chance = ORIG
                    for s in slips:
                        res[name][d].append((int(all(u["hit"] for u in s)), sum(u["hit"] for u in s), len(s)))
                for _ in range(100):
                    for s in bt.run_random(slate, size, no_repeat, rng):
                        res["random"][d].append((int(all(u["hit"] for u in s)) / 100, sum(u["hit"] for u in s) / 100, len(s) / 100))
            row = {"size": size, "no_repeat": no_repeat}
            for name, g in res.items():
                n = sum(x[2] for v in g.values() for x in v)
                ns = sum((1 if name != "random" else .01) for v in g.values() for _ in v)
                row[name] = {"leg_hit": round(sum(x[1] for v in g.values() for x in v) / n, 4) if n else None,
                             "slip_hit": round(sum(x[0] for v in g.values() for x in v) / ns, 4) if ns else None}
                if name != "random":
                    ds = sorted(set(g) & set(res["random"]))
                    def diff(keys):
                        a = sum(x[0] for d in keys for x in g[d]) / max(1, sum(len(g[d]) for d in keys))
                        b = sum(x[0] for d in keys for x in res["random"][d]) / max(1e-9, sum(.01 * len(res["random"][d]) for d in keys))
                        return a - b
                    boots = sorted(diff([ds[rng.randrange(len(ds))] for _ in ds]) for _ in range(1000))
                    row[name]["vs_random"] = [round(diff(ds), 4), round(boots[25], 4), round(boots[974], 4)]
            out.append(row)
            print(label, size, "no_repeat" if no_repeat else "repeats", {k: v for k, v in row.items() if k not in ("size", "no_repeat")})
    return out


def main(data):
    rng = random.Random(11)
    res = {"cs2": run(bt.cs2_units(data), (2, 3, 4), "CS2", rng)}
    if os.path.exists(os.path.join(data, "lol_units.json")):
        res["lol"] = run(bt.lol_units(data), (2, 3), "LoL", rng)
    json.dump(res, open(os.path.join(HERE, "variants.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
