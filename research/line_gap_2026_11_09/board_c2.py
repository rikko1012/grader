"""(c2) at board level, test weeks W1-W3: baseline = the P1 app row of every unit; new = the softer of the two app lines
for the P1 row's live side when both apps offer different lines (graded with the live side at that line). Read-only."""
import json, sys
from datetime import datetime
import numpy as np
sys.path.insert(0, ".")
import score as S
rows = json.load(open(S.D + "rows.json"))
for r in rows:
    r["wk"] = S.week(r); r["base"] = S.side(r["p"])
by = {}
for r in rows:
    by.setdefault(r["unit"], []).append(r)
out = []
dt = []
for u, v in by.items():
    p1 = [r for r in v if r["is_p1_book"]]
    if not p1 or p1[0]["wk"] in (None, "W0"):
        continue
    b = dict(p1[0]); b["new"] = b["base"]; b["src"] = "p1"
    if len(v) == 2 and v[0]["line"] != v[1]["line"] and b["base"]:
        lo, hi = sorted(v, key=lambda r: r["line"])
        sf = lo if b["base"] == "o" else hi
        gap_min = abs((datetime.fromisoformat(v[0]["t"]) - datetime.fromisoformat(v[1]["t"])).total_seconds()) / 60
        if sf is not p1[0] and gap_min <= float(__import__("os").environ.get("C2_MAX_MIN", "1e9")):
            # outcome at the softer line, side = live side at the softer line
            b = dict(sf, base=b["base"], new=sf["base"], over_base=b["over"], src="softer")
            dt.append(abs((datetime.fromisoformat(v[0]["t"]) - datetime.fromisoformat(v[1]["t"])).total_seconds()) / 60)
    out.append(b)
# base outcome must be at the P1 line: rebuild a paired structure
pair = []
for b in out:
    if b["src"] == "softer":
        pair.append({"match": b["match"], "base": b["base"], "over_b": b["over_base"], "new": b["new"], "over_n": b["over"]})
    else:
        pair.append({"match": b["match"], "base": b["base"], "over_b": b["over"], "new": b["new"], "over_n": b["over"]})
def st(rows, k, o):
    g = [r for r in rows if r[k]]
    w = [S.win(r[k], r[o]) for r in g]
    res = {"graded": len(g), "hit": round(100 * np.mean(w), 2), "over_share": round(100 * np.mean([r[k] == "o" for r in g]), 2)}
    for s in "ou":
        ws = [S.win(r[k], r[o]) for r in g if r[k] == s]
        res[s + "_n"], res[s + "_hit"] = len(ws), round(100 * np.mean(ws), 2)
    return res
def d(q):
    a = [S.win(r["new"], r["over_n"]) for r in q if r["new"]]; b = [S.win(r["base"], r["over_b"]) for r in q if r["base"]]
    return 100 * (np.mean(a) - np.mean(b))
res = {"units": len(pair), "switched_to_softer": sum(1 for b in out if b["src"] == "softer"),
       "base": st(pair, "base", "over_b"), "new": st(pair, "new", "over_n"), "diff_ci": S.boot(pair, d),
       "side_ci": {s: [S.boot([r for r in pair if r["base"] == s], lambda q: 100 * (np.mean([S.win(r["new"], r["over_n"]) for r in q if r["new"]]) - np.mean([S.win(r["base"], r["over_b"]) for r in q])))] for s in "ou"},
       "minutes_between_the_two_app_forecasts_median": float(np.median(dt)) if dt else None,
       "minutes_p90": float(np.percentile(dt, 90)) if dt else None}
print(json.dumps(res, indent=1))
json.dump(res, open(S.D + "board_c2.json", "w"), indent=1)
