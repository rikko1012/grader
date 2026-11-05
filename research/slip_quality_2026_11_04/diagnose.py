"""Why the generated slips looked random: the pre-sq-1 generator vs sq-1 on a copy of the live boards.

usage: python -m research.slip_quality_2026_11_04.diagnose <data dir>   (board_cs2/lol/nfl.json from /api/upgrade/board,
p1_units.json from the paged P1 export, lol_units.json from lol_join). Read-only; prints a report and writes diagnose.json."""
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import slip_quality as sq                                   # noqa: E402
from research.slip_quality_2026_11_04 import old_generator as old   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load(data):
    recs, feeds, at = [], {}, []
    for s in ("cs2", "lol", "nfl", "nba"):
        fn = os.path.join(data, f"board_{s}.json")
        if not os.path.exists(fn):
            continue
        d = json.load(open(fn))
        recs += d.get("records") or []
        feeds[s] = d.get("feeds") or {}
        at += [f["at"] for f in (d.get("feeds") or {}).values() if f.get("at")]
    return recs, feeds, max(sq._ts(a) for a in at)


def lol_units(data):
    fn = os.path.join(data, "lol_units.json")
    if not os.path.exists(fn):
        return []
    return [u for u in json.load(open(fn)) if u.get("offered") == 1]


def table_from(data):
    p1 = json.load(open(os.path.join(data, "p1_units.json")))
    lol = [{"stat": u["stat"], "p_over": u["p"], "p_under": u["p_under"] if u["p_under"] is not None else 1 - u["p"], "model_side": u["side"],
            "sid": u["match"], "over_hit": u["over"], "series_start": u["start"]} for u in lol_units(data)]
    return sq.calibration_table(sq.units_from_p1(p1) + sq.units_from_lol(lol))


def describe_old(r, table):
    side = r["scout"]["side"]
    p = old.chance(r, side)
    q, band = sq.ranked_chance(r["sport"], r["market"]["stat"], side, p, r["scout"].get("p_push"), r["sport"] in sq.RESEARCH_SPORTS, table)
    return {"sport": r["sport"], "player": r["player"]["name"], "stat": r["market"]["stat"], "side": side, "line": r["offer"]["line"],
            "line_type": r["market"]["line_type"], "grade": r["scout"]["grade"], "p": round(p, 4), "rank_p": round(q, 4),
            "research": r["sport"] in sq.RESEARCH_SPORTS, "event": r["event"]["label"]}


def describe_new(l):
    return {"sport": l["sport"], "player": l["player"], "stat": l["stat"], "side": l["side"], "line": l["line"], "line_type": l["line_type"],
            "grade": l["grade"], "p": round(l["p"], 4), "rank_p": round(l["rank_p"], 4), "rank": l["rank"], "research": l["research"],
            "event": l["event_label"], "band": sq.band_text(l["band"], l["sport"], l["line_type"])}


def main(data):
    recs, feeds, now = load(data)
    table = table_from(data)
    open_recs = [r for r in recs if (feeds.get(r["sport"], {}).get(r["offer"]["platform"]) or {}).get("state") == "ok"
                 and r["state"]["kind"] == "ok" and r["event"]["status"] != "started" and sq._ts(r["event"].get("lock_utc") or r["event"].get("start_utc")) > now]
    report = {"now": now.isoformat(), "records": len(recs), "open": len(open_recs), "runs": []}
    for platform, et in (("prizepicks", "power"), ("underdog", "standard")):
        for size in (2, 4):
            opts = {"platform": platform, "entryType": et, "count": 8, "size": size, "mode": "balanced", "target": 60, "tolerance": 5, "mix": "any", "games": "spread"}
            o = old.generate(open_recs, opts)
            graded = [r for r in open_recs if r["offer"]["platform"] == platform and r["scout"].get("grade") and not r["scout"].get("hold")
                      and r["scout"].get("side") and r["offer"][r["scout"]["side"]]["offered"]]
            legs, why = sq.pool(recs, now=now, feeds=feeds, table=table)
            b = sq.build(legs, {"platform": platform, "entry_type": et, "count": 8, "size": size, "mix": "any", "no_repeat": False})
            ol = [describe_old(r, table) for s in o["slips"] for r in s]
            nl = [describe_new(l) for s in b["slips"] for l in s["legs"]]
            run = {"platform": platform, "size": size,
                   "old": {"pool": len(o["pool"]), "graded_offered": len(graded),
                           "graded_above_65": sum(1 for r in graded if old.chance(r, r["scout"]["side"]) > .65),
                           "pool_by_sport": Counter(r["sport"] for r in o["pool"]), "slips": [[describe_old(r, table) for r in s] for s in o["slips"]],
                           "leg_research_share": round(sum(l["research"] for l in ol) / len(ol), 3) if ol else None,
                           "mean_rank_p": round(sum(l["rank_p"] for l in ol) / len(ol), 4) if ol else None,
                           "distinct_props": len({(l["player"], l["stat"]) for l in ol})},
                   "new": {"pool": sum(1 for l in legs if l["book"] == platform), "excluded": why, "shortage": b["shortage"],
                           "slips": [{"why": s["why"], "all_hit": s["all_hit"], "payout": s["payout"], "legs": [describe_new(l) for l in s["legs"]]} for s in b["slips"]],
                           "mean_rank_p": round(sum(l["rank_p"] for l in nl) / len(nl), 4) if nl else None,
                           "distinct_props": len({(l["player"], l["stat"]) for l in nl})}}
            report["runs"].append(run)
            print(f"\n=== {platform} {size}-pick, 8 slips  (old pool {run['old']['pool']} of {len(graded)} graded+offered; "
                  f"{run['old']['graded_above_65']} graded legs above 65% dropped by the 60±5 window; pool by sport {dict(run['old']['pool_by_sport'])})")
            for i, s in enumerate(run["old"]["slips"]):
                print(f" OLD {i+1}: " + " | ".join(f"{l['sport']} {l['player']} {l['stat']} {l['side']} {l['line']}{'' if l['line_type']=='standard' else ' '+l['line_type']} p={l['p']:.3f} rank={l['rank_p']:.3f}{' R' if l['research'] else ''}" for l in s))
            for i, s in enumerate(run["new"]["slips"]):
                print(f" NEW {i+1}: " + " | ".join(f"{l['sport']} {l['player']} {l['stat']} {l['side']} {l['line']}{'' if l['line_type']=='standard' else ' '+l['line_type']} p={l['p']:.3f} rank={l['rank_p']:.3f}" for l in s["legs"]) + f"  all-hit={s['all_hit'] and round(s['all_hit'],3)}")
                print("      " + s["why"])
            print(f" old mean ranked chance {run['old']['mean_rank_p']}  research share {run['old']['leg_research_share']}  distinct props {run['old']['distinct_props']}")
            print(f" new mean ranked chance {run['new']['mean_rank_p']}  distinct props {run['new']['distinct_props']}  excluded {why}  shortage {b['shortage']}")
    json.dump(report, open(os.path.join(HERE, "diagnose.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main(sys.argv[1])
