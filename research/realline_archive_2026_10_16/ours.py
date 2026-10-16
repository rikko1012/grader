"""Set E2: OUR saved pre-match forecasts at the line each was made for (Railway 2026-10-03 .. 10-10, Render 10-11 .. 10-16).
One observation per (HLTV player, HLTV match, stat, scope, platform): the latest board forecast logged >= 30 min before the
start (regrades, line moves on the same platform and host migrations collapse into it); the two platforms are kept apart
when their lines differ, grouped by match. Outcome = the HLTV total in the study rows vs that line (pushes out). HLTV ids
come from the offered-line archive (same platform, player, stat, scope, start within 12 h) or, failing that, from the study
rows by player name + start day + team names. Read-only. Output: ours.pkl."""
import collections
import json
import os
import pickle
import re
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import assemble as A  # noqa: E402  (loads the same sources; the module's own build runs once on import)

norm, ts, iso, day = A.norm, A.ts, A.iso, A.day
# HLTV ids per (book, player, prop, scope, start) from the archive
arch = collections.defaultdict(list)
for g in A.G:
    if g["matchid"] and g["maps"] in A.SCOPE and "2 HLTV matches fit" not in (g.get("note") or ""):
        arch[(g["book"], norm(g["player"]), A.PROP[g["market"]], A.SCOPE[g["maps"]])].append((A.ts(g["start_time"]), int(g["matchid"]), norm(g["opponent"])))
pm_by_day = collections.defaultdict(list)
for r in A.R:
    pm_by_day[r["date"]].append(r)
TEAMN = lambda t: norm(A.TN.get(t, ""))


def hltv_ids(p):
    st = ts(p["match_start"])
    cand_a = sorted(arch.get((p["_book"], norm(p["player"]), p["prop"], p.get("scope") or "12"), []), key=lambda x: abs((x[0] - st).total_seconds()))
    for s, m, opn in cand_a:
        if abs((s - st).total_seconds()) <= 12 * 3600 and (A.same_team(opn, norm(p.get("opponent"))) or A.same_team(opn, norm(p.get("team")))):
            ps = [x for x in A.by_m.get(m, []) if norm(A.PN.get(x["p"])) == norm(p["player"])] or \
                 [x for x in A.by_m.get(m, []) if norm(p["player"]) and (norm(p["player"]) in norm(A.PN.get(x["p"])) or norm(A.PN.get(x["p"])) in norm(p["player"]))]
            if len(ps) == 1:
                return m, ps[0], "archive"
    cands = []
    for d in {day(st), day(st - timedelta(hours=12)), day(st + timedelta(hours=12))}:
        for r in pm_by_day.get(d, []):
            if norm(A.PN.get(r["p"])) == norm(p["player"]):
                tn, on = norm(p.get("team")), norm(p.get("opponent"))
                score = (tn and (tn in TEAMN(r["team"]) or TEAMN(r["team"]) in tn)) + (on and (on in TEAMN(r["opp"]) or TEAMN(r["opp"]) in on))
                cands.append((score, r))
    cands = [c for c in cands if c[0] >= 2]                # both teams agree (several BO1s of one team on one day)
    if cands:
        best = max(c[0] for c in cands)
        top = [r for s, r in cands if s == best]
        if len({r["m"] for r in top}) == 1:
            return top[0]["m"], top[0], "name + day + team"
    return None, None, None


out, why = {}, collections.Counter()
inv = collections.defaultdict(collections.Counter)
for p in A.board:
    d = day(p["match_start"])
    inv[d][f"board rows ({p['_host']})"] += 1
    if (p.get("scope") or "12") not in ("12", "1"):
        why["Maps 1-3 line (no HLTV Maps 1-3 construction in the study)"] += 1
        continue
    start = ts(p["match_start"])
    cut = start - timedelta(minutes=30)
    fs = [f for f in A.fby.get((p["_host"], p["id"]), []) if f.get("logged_at") and ts(f["logged_at"]) <= cut and f.get("p_more") is not None]
    cand = [(ts(f["logged_at"]), "log", f, float(f.get("line") or p["line"])) for f in fs]
    if not cand and p["_host"] == "railway" and p.get("logged_at") and ts(p["logged_at"]) <= cut and p.get("p_more") is not None \
            and (p["logged_at"] < "2026-10-07T21:11" or not A.fby.get(("railway", p["id"]))):
        cand = [(ts(p["logged_at"]), "pick row", p, float(p["line"]))]
    if not cand:
        why["no forecast logged >= 30 min before the start" + (" (Railway pick row written later)" if p["_host"] == "railway" else "")] += 1
        inv[d]["no pre-match forecast"] += 1
        continue
    inv[d]["with pre-match forecast"] += 1
    m, r, how = hltv_ids(p)
    if r is None:
        why["no HLTV player / match identified"] += 1
        inv[d]["no HLTV ids"] += 1
        continue
    t, kind, f, line = max(cand, key=lambda x: x[0])
    scope = p.get("scope") or "12"
    key = (m, r["p"], p["prop"], scope, p["_book"])
    prev = out.get(key)
    if prev and prev["logged"] >= iso(t):
        why["superseded by a later forecast of the same unit + platform (regrade / line move / migration)"] += 1
        continue
    if prev:
        why["superseded by a later forecast of the same unit + platform (regrade / line move / migration)"] += 1
    sh = f.get("shadow") if kind == "log" else None
    try:
        sh = json.loads(sh) if isinstance(sh, str) else (sh or {})
    except ValueError:
        sh = {}
    mr = sh.get("market_rounds") or {}
    out[key] = {"m": m, "p_id": r["p"], "r": r, "prop": p["prop"], "scope": scope, "book": p["_book"], "host": p["_host"], "kind": kind,
                "day": day(start), "start": iso(start), "logged": iso(t), "line": line, "p_live": float(f["p_more"]), "lam_live": f.get("lambda"),
                "grade": f.get("grade"), "units": f.get("units"), "version": f.get("model_version") or "untagged", "how": how,
                "p_rr": mr.get("p_more") if mr.get("status") == "ok" else None, "sample_maps": f.get("sample_maps"), "pick_id": p["id"]}
obs = []
for key, o in out.items():
    r = o.pop("r")
    tot = (r["y1"] if o["scope"] == "1" else r["y"]) if o["prop"] == "kills" else (r["y1_hs"] if o["scope"] == "1" else r["y_hs"])
    if tot is None:
        why["no HLTV total for this stat / scope (map 2 / headshots missing)"] += 1
        continue
    if tot == o["line"]:
        why["push"] += 1
        continue
    pk = next((x for x in A.board if x["_host"] == o["host"] and x["id"] == o["pick_id"]), None)
    if pk is not None and pk.get("status") in ("settled", "push") and pk.get("actual") is not None and abs(float(pk["actual"]) - float(tot)) > 1e-9:
        why["settlement conflict (the platform pick's settled total differs from the HLTV total)"] += 1
        continue
    lam0, c0 = A.proj(r, "n0", o["prop"], o["scope"])
    lam1, _ = A.proj(r, "n", o["prop"], o["scope"])
    o.update(actual=float(tot), y=int(tot > o["line"]), lam_n0=lam0, lam_n=lam1, p_n0=A.p_over(lam0, o["line"], o["prop"], o["scope"]),
             p_n=A.p_over(lam1, o["line"], o["prop"], o["scope"]), hist_r=r["n0"]["r"], lan=r["lan"], elo=r["elo"], n0_target=r["n0_target"])
    obs.append(o)
# the same unit on the other platform at a DIFFERENT line stays; the SAME line on both platforms is one observation
seen = {}
final = []
for o in sorted(obs, key=lambda o: o["logged"], reverse=True):
    k = (o["m"], o["p_id"], o["prop"], o["scope"], o["line"])
    if k in seen:
        why["cross-platform copy (same line on the other platform)"] += 1
        continue
    seen[k] = 1
    final.append(o)
final.sort(key=lambda o: (o["start"], o["m"]))
pickle.dump(final, open(os.path.join(A.RL, "ours.pkl"), "wb"))
json.dump({d: dict(c) for d, c in sorted(inv.items())}, open(os.path.join(A.RL, "ours_inventory.json"), "w"), indent=1)
print("E2 observations", len(final), "matches", len({o["m"] for o in final}), "days", final[0]["day"], "..", final[-1]["day"])
print("by host/kind", collections.Counter((o["host"], o["kind"]) for o in final))
print("dropped:", dict(why))
for d in sorted(inv):
    if "2026-10-02" <= d <= "2026-10-17":
        n = [o for o in final if o["day"] == d]
        print(" ", d, dict(inv[d]), "-> usable", len(n), "matches", len({o["m"] for o in n}))
