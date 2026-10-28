"""(1) rm-1 / th-1 forward interim on their registered cells (P1 units' cands field, AM-1), (2) gd-1 display check from the
pick log (graded chance but logged LEAN = 0U gate), (3) PICKS-rule levels restricted to platform board lines."""
import json, math, os, collections, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ana import D, metrics, by, fmt_table, picks_units, run_levels, boot

p1 = json.load(open(os.path.join(D, "p1_units.json")))["rows"]


def ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -math.log(p if y else 1 - p)


def paired(rows, name):
    """rows: (match, live_p_over, cand_p_over, over). Gain = live log loss - candidate log loss (+ = candidate better)."""
    if not rows:
        return {"cand": name, "units": 0}
    units = [{"match": m, "d": ll(pl, y) - ll(pc, y), "live": ll(pl, y), "cand": ll(pc, y)} for m, pl, pc, y in rows]
    gain = sum(u["d"] for u in units) / len(units)
    ci = boot(units, lambda u: u["d"], lambda u: 1, lambda u: u["match"], B=2000)
    # fixed 57% rule for each arm, as different pick sets
    def staked(sel):
        s = [(m, (y if pc > 0.5 else 1 - y)) for m, pl, pc, y in rows for p in [sel(pl, pc)] if max(p, 1 - p) >= 0.57 for pc in [p]]
        return {"n": len(s), "hit": round(100 * sum(h for _, h in s) / len(s), 1) if s else None}
    return {"cand": name, "units": len(units), "matches": len({u["match"] for u in units}),
            "live_logloss": round(sum(u["live"] for u in units) / len(units), 4),
            "cand_logloss": round(sum(u["cand"] for u in units) / len(units), 4),
            "gain": round(gain, 5), "gain_ci": [round(x, 5) for x in ci],
            "live_57": staked(lambda pl, pc: pl), "cand_57": staked(lambda pl, pc: pc)}


out = {}
for cid in ("rm1", "th1"):
    rows, reasons, cells = [], collections.Counter(), collections.Counter()
    for u in p1:
        c = (u.get("cands") or {}).get(cid)
        if not c:
            continue
        if not c.get("eligible"):
            reasons[c.get("reason")] += 1; continue
        pc = (c.get("cand") or c.get("candidate") or {}).get("p_more", c.get("p_more"))
        pl = (c.get("live") or {}).get("p_more", u["p"])
        if pc is None or u["logged"] >= u["start"]:
            reasons["no candidate chance / after start"] += 1; continue
        cells[(u["prop"], u["scope"])] += 1
        rows.append((u["match"], float(pl), float(pc), int(u["over"])))
    out[cid] = dict(paired(rows, cid), ineligible_reasons=dict(reasons.most_common(8)), cells={f"{k[0]} {k[1]}": v for k, v in cells.items()})
    print(cid, json.dumps(out[cid], default=str)[:900])
# example of an eligible record (structure check)
for u in p1:
    c = (u.get("cands") or {}).get("rm1")
    if c and c.get("eligible"):
        print("rm1 eligible example:", json.dumps(c)[:700]); break

# gd-1 display check: a logged LEAN with a graded chance = a 0U gate (letter shown with the stake reason); count by reason
picks = json.load(open(os.path.join(D, "picks.json")))["rows"]
gd = collections.Counter(); gd_reason = collections.Counter()
for r in picks:
    if r.get("test") == 1: continue
    p = float(r["p_pick"]); g = r["grade"]
    if p >= 0.55 and g == "LEAN":
        gd["chance >= 55% logged LEAN (0U gate)"] += 1
        fl = r.get("flags") or ""
        why = "form fight" if "FORM FIGHT" in fl else "stand-in" if "stand-in" in fl else "small sample" if "0 units: only" in fl or "0 units: no Maps" in fl else "AWPer HS" if "AWPer" in fl else "transfer" if "transfer" in fl else "other"
        gd_reason[why] += 1
    elif p < 0.55 and g != "LEAN":
        gd["chance < 55% logged a letter (mismatch)"] += 1
    elif p >= 0.55 and g != "LEAN":
        exp = "A+" if p >= .8 else "A" if p >= .74 else "A-" if p >= .68 else "B+" if p >= .63 else "B" if p >= .57 else "B-"
        if exp != g and not any(abs(p - c) < 0.0015 for c in (.55, .57, .63, .68, .74, .8)):
            gd["letter != chance band (mismatch)"] += 1
        else:
            gd["letter = chance band"] += 1
    else:
        gd["lean, under 55%"] += 1
out["gd1"] = {"counts": dict(gd), "zero_unit_reasons": dict(gd_reason)}
print("gd-1", out["gd1"])

# board-only PICKS levels
us, dropped = picks_units()
board = [u for u in us if u["book"] in ("underdog", "prizepicks")]
res = run_levels(board, "PICKS rule, platform board lines only (Underdog + PrizePicks)")
res["dropped"] = dropped
json.dump({"cands": out, "board_levels": res}, open(os.path.join(D, "cands_board.json"), "w"), indent=1, default=str)
with open(os.path.join(D, "levels_picks_board.md"), "w", encoding="utf-8") as f:
    f.write(f"\n## {res['tag']}\norder holds: {res['order_holds']}\n")
    for lv in res["levels"]:
        f.write(fmt_table(lv) + "\n")
print("board rows", len(board), res["order_holds"])
