"""Actionable-line analysis (PROTOCOL_ACTIONABLE.md, written before this ran). Read-only; nothing fitted."""
import collections
import json
import os
import pickle
import sys
from datetime import timedelta

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble as A  # noqa: E402  (builds E1 on import - only its loaded sources are used here)
import eval20 as E  # noqa: E402

norm, ts, iso, day = A.norm, A.ts, A.iso, A.day
RAIL_END = "2026-10-10T16:40:00+00:00"

# ------------------------------------------------------------------ identity (corrected join)
arch = collections.defaultdict(list)
for g in A.G:
    if g["matchid"] and g["maps"] in A.SCOPE and "2 HLTV matches fit" not in (g.get("note") or ""):
        arch[(g["book"], norm(g["player"]), A.PROP[g["market"]], A.SCOPE[g["maps"]])].append((ts(g["start_time"]), int(g["matchid"]), norm(g["opponent"])))
pm_by_day = collections.defaultdict(list)
for r in A.R:
    pm_by_day[r["date"]].append(r)
TEAMN = lambda t: norm(A.TN.get(t, ""))


def opp_ok(r, p):
    """The HLTV record's opponent (or team) must be the pick's opponent."""
    on = norm(p.get("opponent"))
    return A.same_team(TEAMN(r["opp"]), on) or A.same_team(TEAMN(r["team"]), on)


def hltv_ids(p):
    st = ts(p["match_start"])
    for s, m, opn in sorted(arch.get((p["_book"], norm(p["player"]), p["prop"], p.get("scope") or "12"), []),
                            key=lambda x: abs((x[0] - st).total_seconds())):
        if abs((s - st).total_seconds()) <= 12 * 3600 and (A.same_team(opn, norm(p.get("opponent"))) or A.same_team(opn, norm(p.get("team")))):
            mp = A.by_m.get(m, [])
            ps = [x for x in mp if norm(A.PN.get(x["p"])) == norm(p["player"])] or \
                 [x for x in mp if norm(p["player"]) and (norm(p["player"]) in norm(A.PN.get(x["p"])) or norm(A.PN.get(x["p"])) in norm(p["player"]))]
            if len(ps) == 1 and opp_ok(ps[0], p):
                return m, ps[0], "archive"
    cands = []
    for d in {day(st), day(st - timedelta(hours=12)), day(st + timedelta(hours=12))}:
        for r in pm_by_day.get(d, []):
            if norm(A.PN.get(r["p"])) == norm(p["player"]):
                tn, on = norm(p.get("team")), norm(p.get("opponent"))
                sc = (tn and (tn in TEAMN(r["team"]) or TEAMN(r["team"]) in tn)) + (on and (on in TEAMN(r["opp"]) or TEAMN(r["opp"]) in on))
                if sc >= 2:
                    cands.append(r)
    if cands and len({r["m"] for r in cands}) == 1:
        return cands[0]["m"], cands[0], "name + day + both teams"
    return None, None, None


# ------------------------------------------------------------------ every saved record per unit x platform
recs = collections.defaultdict(list)         # (m, p, prop, scope, book) -> records
why = collections.Counter()
hl = {}
for p in A.board:
    sc = p.get("scope") or "12"
    if sc not in ("12", "1"):
        why["Maps 1-3 (no HLTV construction)"] += 1
        continue
    m, r, how = hltv_ids(p)
    if r is None:
        why["no verified HLTV player / match (identity)"] += 1
        continue
    key = (m, r["p"], p["prop"], sc, p["_book"])
    hl[key] = r
    start = ts(p["match_start"])
    fs = [f for f in A.fby.get((p["_host"], p["id"]), []) if f.get("logged_at") and f.get("p_more") is not None]
    for f in fs:
        q = None
        if f.get("quote"):
            try:
                q = json.loads(f["quote"]) if isinstance(f["quote"], str) else f["quote"]
            except ValueError:
                q = None
        recs[key].append({"t": ts(f["logged_at"]), "line": float(f.get("line") or p["line"]), "p": float(f["p_more"]),
                          "lam": f.get("lambda"), "version": f.get("model_version") or "untagged", "host": p["_host"], "kind": "log",
                          "quote": q, "start": start, "pick": p["id"]})
    if not fs and p["_host"] == "railway" and p.get("logged_at") and p.get("p_more") is not None:
        recs[key].append({"t": ts(p["logged_at"]), "line": float(p["line"]), "p": float(p["p_more"]), "lam": p.get("lambda"),
                          "version": p.get("model_version") or "untagged", "host": "railway", "kind": "pick row", "quote": None,
                          "start": start, "pick": p["id"]})

obs = []
for key, rs in recs.items():
    m, pid, prop, scope, book = key
    start = min(x["start"] for x in rs)
    cut = start - timedelta(minutes=30)
    pre = sorted([x for x in rs if x["t"] <= cut], key=lambda x: x["t"])
    if not pre:
        why["no forecast >= 30 min before the start"] += 1
        continue
    D = pre[0]                                                   # the decision-time forecast: the earliest one
    r = hl[key]
    tot = (r["y1"] if scope == "1" else r["y"]) if prop == "kills" else (r["y1_hs"] if scope == "1" else r["y_hs"])
    if tot is None:
        why["no HLTV total for this stat / scope"] += 1
        continue
    if tot == D["line"]:
        why["push at the original line"] += 1
        continue
    before = [x for x in rs if x["t"] < start]
    V = max(before, key=lambda x: x["t"])                        # last line our board recorded before the start
    verifiable = not (D["host"] == "railway" and iso(start) > RAIL_END)
    q = D["quote"] or {}
    tq = (q.get("anchors") or {}).get("target") or {}
    oq = (q.get("anchors") or {}).get("other") or {}
    side_over = D["p"] >= 0.5
    off = tq.get("over_offered") if side_over else tq.get("under_offered")
    if off is None and q:
        off = q.get("over_available") if side_over else q.get("under_available")
    other_line = oq.get("line") if oq and oq.get("observed_at") and ts(oq["observed_at"]) <= D["t"] else None
    lam = float(D["lam"]) if D["lam"] is not None else None
    obs.append({"m": m, "p_id": pid, "prop": prop, "scope": scope, "book": book, "host": D["host"], "kind": D["kind"], "version": D["version"],
                "day": day(start), "start": iso(start), "t_dec": iso(D["t"]), "lead_h": (start - D["t"]).total_seconds() / 3600,
                "line": D["line"], "p_live": D["p"], "lam": lam, "actual": float(tot), "y": int(tot > D["line"]),
                "side_offered": off, "quote_logged": bool(q), "other_line": other_line,
                "v_line": V["line"], "v_t": iso(V["t"]), "v_lag_min": (start - V["t"]).total_seconds() / 60, "v_p": V["p"],
                "verifiable": verifiable, "n_records": len(rs)})
# same line on both platforms = one observation (keep the earlier decision)
seen, final = {}, []
for o in sorted(obs, key=lambda o: o["t_dec"]):
    k = (o["m"], o["p_id"], o["prop"], o["scope"], o["line"])
    if k in seen:
        why["same line on the other platform (copy)"] += 1
        continue
    seen[k] = 1
    final.append(o)
final.sort(key=lambda o: (o["start"], o["m"]))
for o in final:
    o["flat"] = 0.5
E.add_base(final)
pickle.dump(final, open(os.path.join(A.RL, "actionable.pkl"), "wb"))

print(f"# Actionable-line analysis\n\ndecision-time observations: {len(final)} / {len({o['m'] for o in final})} distinct HLTV matches, "
      f"match days {final[0]['day']} .. {final[-1]['day']}")
print("excluded / collapsed:", dict(why))
lead = np.array([o["lead_h"] for o in final])
print(f"decision lead time before the start: median {np.median(lead):.1f} h (25% {np.percentile(lead, 25):.1f} h, 75% {np.percentile(lead, 75):.1f} h)")
print("by host / record kind:", dict(collections.Counter((o["host"], o["kind"]) for o in final)))
print("offered side of the favoured side logged:", dict(collections.Counter(str(o["side_offered"]) for o in final if o["quote_logged"])),
      "| no quote logged (before 2026-10-15 02:44):", sum(1 for o in final if not o["quote_logged"]))


def block(rows, label):
    if len(rows) < 30:
        print(f"\n### {label}: {len(rows)} observations - too few")
        return
    E.prob_table(rows, ["flat", "p_BASE", "p_live"], ["flat", "p_BASE"], label)
    E.selection(rows, ["p_live"], label)
    g = [o for o in rows if o["lam"] is not None]
    for prop in ("kills", "hs"):
        gg = [o for o in g if o["prop"] == prop]
        if len(gg) >= 40:
            bt = E.Boot(gg)
            b = bt.slope(np.array([o["lam"] - o["line"] for o in gg]), np.array([o["actual"] - o["line"] for o in gg]))
            dm = bt.ratio(np.array([(o["lam"] - o["actual"]) ** 2 - (o["line"] - o["actual"]) ** 2 for o in gg]))
            print(f"  {prop}: slope of (actual - original line) on (recorded projection - original line) {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f}); "
                  f"MSE projection - MSE line {dm[0]:+.2f} ({dm[1]:+.2f}, {dm[2]:+.2f}); {len(gg)} obs / {len(bt.keys)} matches")


block(final, "ALL decision-time forecasts at their original line (line-verified)")
block([o for o in final if o["host"] == "railway"], "Railway era (Oct 3-10; mostly older pipelines)")
block([o for o in final if o["host"] == "render"], "Render era (Oct 11-16; perf-2 tags)")
block([o for o in final if o["side_offered"] is True], "line + favoured side verified offered (quote logged, from 2026-10-15)")

# ------------------------------------------------------------------ movement (our own timestamped records only)
mv = [o for o in final if o["verifiable"]]
print(f"\n## Line movement after the decision (our own records; {len(mv)} verifiable obs / {len({o['m'] for o in mv})} matches; "
      f"{len(final) - len(mv)} Railway obs after its export have no later record and are excluded here)")
lag = np.array([o["v_lag_min"] for o in mv])
print(f"last verified line captured: median {np.median(lag):.0f} min before the start (25% {np.percentile(lag, 25):.0f}, 75% {np.percentile(lag, 75):.0f}, "
      f"share within 60 min {np.mean(lag <= 60):.0%})")
unch = [o for o in mv if abs(o["v_line"] - o["line"]) < 1e-9]
moved = [o for o in mv if abs(o["v_line"] - o["line"]) >= 1e-9]
print(f"unchanged: {len(unch)} obs / {len({o['m'] for o in unch})} matches | moved: {len(moved)} obs / {len({o['m'] for o in moved})} matches")
if moved:
    size = np.array([o["v_line"] - o["line"] for o in moved])
    print("move size distribution (last verified - original):", dict(sorted(collections.Counter(np.round(size, 1)).items())))
    bt = E.Boot(moved)
    tow = np.array([1.0 if (o["lam"] is not None and np.sign(o["v_line"] - o["line"]) == np.sign(o["lam"] - o["line"])) else 0.0 for o in moved])
    tows = np.array([1.0 if np.sign(o["v_line"] - o["line"]) == np.sign(o["p_live"] - 0.5) else 0.0 for o in moved])
    print(f"moved toward the recorded projection: {bt.ratio(tow)[0]:.3f} ({bt.ratio(tow)[1]:.3f}, {bt.ratio(tow)[2]:.3f}); toward the side live favoured: "
          f"{bt.ratio(tows)[0]:.3f} ({bt.ratio(tows)[1]:.3f}, {bt.ratio(tows)[2]:.3f})")
    for lo, hi in ((0.4, 0.6), (0.9, 1.1), (1.4, 99)):
        g = [o for o, s in zip(moved, np.abs(size)) if lo <= s <= hi]
        if g:
            t_ = np.array([1.0 if (o["lam"] is not None and np.sign(o["v_line"] - o["line"]) == np.sign(o["lam"] - o["line"])) else 0.0 for o in g])
            print(f"  |move| {lo}-{hi}: {len(g)} obs / {len({o['m'] for o in g})} matches; toward projection {t_.mean():.3f}")
    bt2 = E.Boot(moved)
    b = bt2.slope(size, np.array([o["actual"] - o["line"] for o in moved]))
    print(f"slope of (actual - original line) on the move: {b[0]:+.3f} ({b[1]:+.3f}, {b[2]:+.3f})")
for lab, g in (("unchanged lines", unch), ("moved lines", moved), ("moved TOWARD the projection", [o for o in moved if o["lam"] is not None and np.sign(o["v_line"] - o["line"]) == np.sign(o["lam"] - o["line"])]),
               ("moved AWAY from the projection", [o for o in moved if o["lam"] is not None and np.sign(o["v_line"] - o["line"]) != np.sign(o["lam"] - o["line"])])):
    if len(g) >= 30:
        E.selection(g, ["p_live"], f"{lab} (scored at the ORIGINAL line)")

# ------------------------------------------------------------------ closing benchmark (separate)
cb = [dict(o, line=o["v_line"], y=int(o["actual"] > o["v_line"]), p_close=o["v_p"]) for o in mv if o["actual"] != o["v_line"]]
for o in cb:
    o["flat"] = 0.5
E.add_base(cb)
print(f"\n## Closing benchmark (separate): the LAST line our board recorded before the start, with live's chance recorded then; "
      f"{len(cb)} obs / {len({o['m'] for o in cb})} matches")
E.prob_table(cb, ["flat", "p_BASE", "p_close"], ["flat", "p_BASE"], "last verified pre-start line")
E.selection(cb, ["p_close"], "last verified pre-start line")

# ------------------------------------------------------------------ comparison-platform information available at the decision
cp = [o for o in final if o["other_line"] is not None]
print(f"\n## other-platform line logged at the decision (from 2026-10-15 03:26): {len(cp)} obs / {len({o['m'] for o in cp})} matches")
for lab, g in (("other line equal", [o for o in cp if abs(o["other_line"] - o["line"]) < 1e-9]),
               ("other line differs", [o for o in cp if abs(o["other_line"] - o["line"]) >= 1e-9])):
    print(f"  {lab}: {len(g)} obs / {len({o['m'] for o in g})} matches")
    if len(g) >= 30:
        E.selection(g, ["p_live"], f"{lab}")
