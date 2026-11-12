"""Are the AWPer headshot picks already inside the public record? (owner 2026-11-11: "just add it to the record").
The record (/record -> public_record._cs2 -> results_stated.rule_picks) reads the stored P1 export and counts every unit
whose chance on the favoured side is >= results_view.RULE, whatever its stake. This checks, on real data, that every
AWPer-HS unit that meets that rule is a row of the export. Read-only: one P1 export read + paged forecast ids.
usage: python record_check.py pull | check   -> $GU_DIR/record_check.json"""
import gzip, json, os, sys, time
from collections import Counter
sys.path.insert(0, "C:/Users/tikic/cs2_scout_ops")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT = os.environ.get("GU_DIR", "C:/Users/tikic/cs2_scout_ops/data/grade_up_1111/")
LG = "C:/Users/tikic/cs2_scout_ops/data/line_gap_1109/"
AWP = "AWPer on a headshot prop"


def pull():
    from lq import adm
    d, after = None, 0                                           # the stored export, in its own pages (1,000 rows a request)
    while True:
        t = time.time()
        pg = adm(f"/admin/learning/p1_units.json?after={after}")
        if d is None:
            d = pg
        else:
            d["rows"].extend(pg.get("rows") or [])
        print("p1 rows", len(d["rows"]), "of", pg.get("total"), "sec", round(time.time() - t, 1), flush=True)
        if not pg.get("rows") or pg.get("next_after") is None or len(d["rows"]) >= (pg.get("total") or 0):
            break
        after = pg["next_after"]; time.sleep(1)
    json.dump(d, gzip.open(OUT + "p1_units_now.json.gz", "wt"))
    if len(sys.argv) > 2 and sys.argv[2] == "p1":
        return
    f = gzip.open(OUT + "forecasts_after.jsonl.gz", "wt", encoding="utf-8")
    after, cutoff, n = 156393, None, 0
    while True:
        t = time.time()
        d = adm(f"/admin/forecasts_page.json?after={after}&limit=2000&compact=1" + (f"&cutoff={cutoff}" if cutoff else ""))
        cutoff = d["cutoff_id"]
        for r in d["rows"]:
            f.write(json.dumps({"id": r["id"], "pick_id": r["pick_id"]}) + "\n"); n += 1
        print("forecasts", n, "of", d.get("total_in_cutoff"), "sec", round(time.time() - t, 1), flush=True)
        if d["done"]:
            break
        after = d["next_after"]; time.sleep(2)
    f.close()


def check():
    from results_view import RULE
    import results_stated
    ex = json.load(gzip.open(OUT + "p1_units_now.json.gz", "rt"))
    f2p = {}
    for path in (LG + "forecasts_compact.jsonl.gz", OUT + "forecasts_after.jsonl.gz"):
        with gzip.open(path, "rt") as f:
            for l in f:
                d = json.loads(l); f2p[d["id"]] = d["pick_id"]
    picks = {}
    for path in (LG + "picks.jsonl.gz", OUT + "picks_refresh.jsonl.gz", OUT + "picks_board.jsonl.gz"):
        with gzip.open(path, "rt") as f:
            for l in f:
                d = json.loads(l); picks[d["id"]] = d
    # unit = player x match start (to the hour) x stat x maps: one match saved under two slugs is still one unit
    ukey = lambda p: (p.get("player_id") or p.get("player"), str(p.get("match_start") or "")[:13], p.get("prop"), p.get("scope") or "12")
    is_awp = lambda p: p is not None and p.get("prop") == "hs" and AWP in (p.get("flags") or "")
    out = {"rule": RULE, "export_at": ex.get("at"), "export_rows": len(ex["rows"]), "export_pushes": len(ex.get("pushes") or [])}
    # 1. AWPer-HS units inside the export (scored rows + pushes)
    covered, unmapped = set(), 0
    c = Counter()
    for kind in ("rows", "pushes"):
        for r in ex.get(kind) or []:
            p = picks.get(f2p.get(r["forecast_id"]))
            if p is None:
                unmapped += 1; continue
            covered.add(ukey(p))
            if not is_awp(p):
                continue
            ps = max(r["p"], 1 - r["p"])
            c[kind + " awp"] += 1
            if ps >= RULE:
                c[kind + " awp meets rule"] += 1
                if kind == "rows":
                    c["w" if (r["over"] if r["p"] >= 0.5 else 1 - r["over"]) else "l"] += 1
                    c["staked at the time" if (r.get("units") or 0) > 0 else "0 units at the time"] += 1
            elif ps >= 0.55:
                c[kind + " awp 55-57 (below the record rule)"] += 1
    out["export_forecasts_not_in_local_pulls"] = unmapped
    out["awp_in_export"] = dict(c)
    # 2. every AWPer-HS cohort unit (pick rows) -> is its unit in the export?
    coh = {}
    for p in picks.values():
        if not is_awp(p) or p.get("on_board") != 1 or p.get("line_type") != "standard" or (p.get("test") or 0) == 1:
            continue
        if not ("2026-10-11" <= (p.get("match_start") or "")[:10] <= "2026-11-11"):
            continue
        coh.setdefault(ukey(p), []).append(p)
    st = Counter(); missing = []
    for k, ps_ in coh.items():
        stat = {p["status"] for p in ps_}
        meets = any((p.get("p_pick") or 0) >= RULE for p in ps_)
        st["units"] += 1
        st["units meeting the rule (a line at %d%%+)" % round(RULE * 100)] += meets
        if k in covered:
            st["units in the export"] += 1
            st["units meeting the rule AND in the export"] += meets
        else:
            why = ("void" if stat <= {"void"} else "open" if "open" in stat else "push" if stat <= {"push", "void"} else "settled")
            st["not in export: " + why] += 1
            if meets and why == "settled":
                missing.append({"player": ps_[0]["player"], "match": ps_[0]["match_slug"], "start": ps_[0]["match_start"],
                                "lines": [(p["book"], p["line"], p["p_pick"], p["logged_at"], p["status"]) for p in ps_]})
    out["cohort"] = dict(st); out["missing_settled_meeting_rule"] = missing
    # why a settled unit meeting the rule is not a row: the P1 forecast rule itself (applies to every pick), not the gate
    from datetime import datetime, timedelta
    ts = lambda x: datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    why = Counter()
    for m in missing:
        early = [l for l in m["lines"] if ts(l[3]) <= ts(m["start"]) - timedelta(minutes=30)]
        why["no board forecast 30+ min before the start" if not early else
            "57%+ only on a line that was not the unit's P1 line / later than 30 min" if not any(l[2] >= RULE for l in early)
            else "other (unit not scoreable in the export: settlement source / identity)"] += 1
    out["missing_why"] = dict(why)
    # the same count for headshot units of players the AWP rule does NOT mark: is the miss rate any different?
    oth = {}
    for p in picks.values():
        if p.get("prop") != "hs" or AWP in (p.get("flags") or "") or p.get("on_board") != 1 or p.get("line_type") != "standard":
            continue
        if (p.get("test") or 0) == 1 or p.get("status") != "settled" or not ("2026-10-11" <= (p.get("match_start") or "")[:10] <= "2026-11-11"):
            continue
        if (p.get("p_pick") or 0) >= RULE:
            oth.setdefault(ukey(p), []).append(p)
    out["other_hs_units_meeting_rule"] = {"units": len(oth), "in_export": sum(1 for k in oth if k in covered)}
    aw = [k for k, ps_ in coh.items() if any(p["status"] == "settled" and (p.get("p_pick") or 0) >= RULE for p in ps_)]
    out["awp_hs_units_meeting_rule_settled"] = {"units": len(aw), "in_export": sum(1 for k in aw if k in covered)}
    # 3. the record totals as the code computes them on this export (no code change)
    pk, pushed, _ = results_stated.rule_picks(ex)
    out["record_totals_model_chance"] = {"picks": len(pk), "wins": sum(x["win"] for x in pk), "pushes": len(pushed)}
    json.dump(out, open(OUT + "record_check.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in out.items() if k != "missing_settled_meeting_rule"}, indent=1, default=str))


if __name__ == "__main__":
    {"pull": pull, "check": check}[sys.argv[1]]()
