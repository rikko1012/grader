"""Coverage inventory of stored vetoes -> research/map_veto/coverage_numbers.json (+ tables printed as markdown).
Counts only (no outcome analysis). Tier = both teams' pre-match series-Elo rank among teams active in the previous
120 days: T1 both top-30, T2 both top-100, T3 otherwise (Elo starts at 1500 on 2024-10-01, so early tiers are rough).
usage: python coverage.py > tables.md"""
import collections, gzip, json, os, sys
import datetime as dt

SCR = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\veto\vmodel"
HERE = os.path.dirname(os.path.abspath(__file__))


def quarter(d):
    return f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}"


def main():
    with gzip.open(os.path.join(SCR, "veto_dataset.json.gz"), "rt", encoding="utf-8") as f:
        recs = json.load(f)["records"]
    last = {}
    by_day = collections.defaultdict(list)
    for r in recs:
        by_day[r["date"]].append(r)
    tier = {}
    for d in sorted(by_day):
        d0 = dt.date.fromisoformat(d)
        active = {}
        for t, (ld, e) in last.items():
            if (d0 - ld).days <= 120:
                active[t] = e
        rank = {t: i + 1 for i, (t, _) in enumerate(sorted(active.items(), key=lambda x: -x[1]))}
        for r in by_day[d]:
            rk = [rank.get(t, 10 ** 6) for t in r["teams"]]
            tier[r["mid"]] = "T1" if len(rk) == 2 and max(rk) <= 30 else "T2" if len(rk) == 2 and max(rk) <= 100 else "T3"
            for t in r["teams"]:
                last[t] = (d0, r["elo"].get(str(t), 1500.0))
    tab = collections.defaultdict(collections.Counter)
    pools = collections.defaultdict(collections.Counter)
    tmpl = collections.defaultdict(collections.Counter)
    misc = collections.Counter()
    teams, events = set(), set()
    for r in recs:
        q = quarter(r["date"])
        f = r["fmt"] or "unknown"
        tab["quarter_status"][(q, r["status"])] += 1
        tab["quarter_format"][(q, f)] += 1
        tab["quarter_tier"][(q, tier[r["mid"]])] += 1
        tab["format_status"][(f, r["status"])] += 1
        tab["lan"][(q, "LAN" if r["lan"] else "online")] += 1
        if r["status"] in ("complete", "inferred"):
            tmpl[f][r["template"]] += 1
            pools[r["date"][:7]][",".join(r["pool"])] += 1
        misc["pool_" + str(r.get("pool_source"))] += 1
        misc["listed_order_known"] += r["listed"] is not None
        misc["lineups_both_5plus"] += all(len(v) >= 5 for v in r["lineups"].values()) and len(r["lineups"]) == 2
        if r["status"] == "complete" and r["listed"]:
            misc["complete_first_actor_is_listed_team1"] += r["first"] == r["listed"][0]
            misc["complete_with_listed"] += 1
        teams.update(r["teams"])
        events.add(r["event"])
    out = {"records": len(recs), "distinct_matches": len({r["mid"] for r in recs}), "teams": len(teams),
           "events": len(events), "first_date": recs[0]["date"], "last_date": recs[-1]["date"],
           "tables": {k: {"|".join(kk): v for kk, v in sorted(t.items())} for k, t in tab.items()},
           "templates": {k: dict(v.most_common()) for k, v in tmpl.items()},
           "pools_by_month": {k: dict(v.most_common()) for k, v in sorted(pools.items())},
           "misc": dict(misc), "tier_by_mid": None}
    json.dump(out, open(os.path.join(HERE, "coverage_numbers.json"), "w"), indent=1)
    json.dump(tier, open(os.path.join(SCR, "tiers.json"), "w"))
    qs = sorted({quarter(r["date"]) for r in recs})

    def grid(name, cols):
        t = tab[name]
        print(f"\n| quarter | " + " | ".join(cols) + " | total |")
        print("|" + "---|" * (len(cols) + 2))
        for q in qs:
            row = [t.get((q, c), 0) for c in cols]
            print(f"| {q} | " + " | ".join(str(x) for x in row) + f" | {sum(row)} |")
        tot = [sum(t.get((q, c), 0) for q in qs) for c in cols]
        print("| **all** | " + " | ".join(str(x) for x in tot) + f" | {sum(tot)} |")
    print("### status by quarter"); grid("quarter_status", ["complete", "inferred", "partial", "anomalous", "missing"])
    print("\n### format by quarter"); grid("quarter_format", ["bo1", "bo3", "bo5", "unknown"])
    print("\n### tier by quarter"); grid("quarter_tier", ["T1", "T2", "T3"])
    print("\n### LAN / online"); grid("lan", ["LAN", "online"])
    print("\n### templates", json.dumps(out["templates"], indent=1))
    print("\n### misc", json.dumps(out["misc"], indent=1))
    print("\n### pools by month")
    for k, v in out["pools_by_month"].items():
        print(k, v)


if __name__ == "__main__":
    main()
