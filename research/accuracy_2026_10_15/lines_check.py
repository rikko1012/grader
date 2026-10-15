"""Archived lines only (board standard lines with feed ids, latest forecast >= 30 min before start, Render Oct 11-15). For
each settled unit: the live published chance (as logged), the raw model chance before the 22-78% kills cap
(shadow.live_raw), and the current vs full-history projections recomputed from the study's as-of inputs, turned into
chances at the SAME line with the live distribution (kills: discretised normal c 1.457, cap; HS: normal approx c 1.264).
DEVELOPMENT data (these lines were inspected at the checkpoint)."""
import json, math, os, pickle, sys
from collections import defaultdict
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, r"C:/Users/tikic/cs2_scout/.claude/worktrees/eval-pipeline")
from study import comp, ci, LIVE
import learning as L
R = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "series_v1.pkl"), "rb"))
idx = {(r["p"], r["date"]): r for r in R}
P = json.load(open("recap1015/picks.json")); F = json.load(open("recap1015/forecasts.json")); L.tag_matches(P)
ids = {}
for x in open("ck/pages/forecasts_26575_inputs.jsonl", encoding="utf-8"):
    f = json.loads(x); pf = ((f.get("inputs") or {}).get("perf") or {})
    if pf.get("hltv_player_ids"):
        ids[f["pick_id"]] = pf["hltv_player_ids"]
byp = defaultdict(list)
for f in F:
    byp[f["pick_id"]].append(f)
ts = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00"))
units = defaultdict(list)
for p in P:
    if p.get("test") == 2 and p.get("match_start") and (p.get("scope") or "12") == "12" and ts(p["match_start"]).isoformat() >= "2026-10-11T04:56:45+00:00":
        units[L.unit_key(p)].append(p)
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
def p_over(lam, line, prop):
    c = LIVE["c"] if prop == "kills" else LIVE["c_hs"]; sd = c * math.sqrt(max(lam, 1.0))
    if line != int(line):
        po = 1 - Phi((math.floor(line) + 0.5 - lam) / sd); pu = 1 - po
    else:
        po, pu = 1 - Phi((line + 0.5 - lam) / sd), Phi((line - 0.5 - lam) / sd)
    p = po / (po + pu)
    return min(max(p, 0.22), 0.78) if prop == "kills" else p
rows, miss = [], defaultdict(int)
for k, ps in units.items():
    sett = [p for p in ps if p["status"] == "settled" and p.get("actual") is not None]
    if not sett: continue
    start = min(ts(p["match_start"]) for p in ps); cut = (start - timedelta(minutes=30)).isoformat()
    c = [(f, p) for p in ps if (p.get("line_type") or "standard") == "standard" and p.get("book") and p.get("market_id") for f in byp[p["id"]] if f["logged_at"] <= cut]
    if not c: continue
    f, p = max(c, key=lambda x: (x[0]["logged_at"], -x[0]["id"]))
    a, line = float(sett[0]["actual"]), float(f["line"])
    if a == line: continue
    pid = next((i for pk in ps for i in ids.get(pk["id"], [])), None)
    r = next((idx.get((i, start.date().isoformat())) for pk in ps for i in ids.get(pk["id"], []) if idx.get((i, start.date().isoformat()))), None)
    if r is None:
        miss["no HLTV series row (player / match not in the study data)"] += 1; continue
    sh = json.loads(f["shadow"] or "{}") if f.get("shadow") else {}
    raw = (sh.get("live_raw") or {}).get("p_more_no_push")
    a0, a1 = comp(r, "n0"), comp(r, "n")
    key = "lam" if p["prop"] == "kills" else "lam_hs"
    rows.append({"m": L.match_of(p), "match": L.match_of(p), "prop": p["prop"], "y": int(a > line), "p_live": float(f["p_more"]), "p_raw": raw,
                 "p_cur": p_over(a0[key], line, p["prop"]), "p_full": p_over(a1[key], line, p["prop"]), "line": line, "actual": a,
                 "lam_cur": a0[key], "lam_full": a1[key], "lam_logged": f["lambda"],
                 "p_lb": L.line_blend_p(float(f["p_more"]), p["prop"], "12"),
                 "p_mm": (sh.get("match_m1") or {}).get("p_more"),
                 "p_rr": (sh.get("market_rounds") or {}).get("p_more") if (sh.get("market_rounds") or {}).get("status") == "ok" else None})
print("archived-line units", len(rows), "matches", len({r['match'] for r in rows}), "| not matched:", dict(miss))
ll = lambda p, y: -(y * math.log(max(min(p, 1 - 1e-9), 1e-9)) + (1 - y) * math.log(max(min(1 - p, 1 - 1e-9), 1e-9)))
def auc(rs, k):
    pos = [r[k] for r in rs if r["y"]]; neg = [r[k] for r in rs if not r["y"]]
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg)) if pos and neg else None
for prop in ("kills", "hs", None):
    rs = [r for r in rows if prop is None or r["prop"] == prop]
    nm = prop or "both"
    print(f"\n{nm}: {len(rs)} units / {len({r['match'] for r in rs})} matches")
    for k in ("p_live", "p_cur", "p_full"):
        print(f"  {k:7} log-loss {sum(ll(r[k], r['y']) for r in rs)/len(rs):.4f} (50%: 0.6931) | AUC {auc(rs, k):.3f} | "
              f"Brier {sum((r[k]-r['y'])**2 for r in rs)/len(rs):.4f}")
    g = lambda r: ll(r["p_cur"], r["y"]) - ll(r["p_full"], r["y"])
    c = ci(rs, g, B=1000)
    print(f"  full vs current log-loss gain {sum(g(r) for r in rs)/len(rs):+.4f} ({c[0]:+.4f} to {c[1]:+.4f}); projection MAE current "
          f"{sum(abs(r['lam_cur']-r['actual']) for r in rs)/len(rs):.2f} full {sum(abs(r['lam_full']-r['actual']) for r in rs)/len(rs):.2f} line {sum(abs(r['line']-r['actual']) for r in rs)/len(rs):.2f}")
kr = [r for r in rows if r["prop"] == "kills" and r["p_raw"] is not None]
capped = [r for r in kr if abs(r["p_raw"] - r["p_live"]) > 0.002]
print(f"\nkills cap: {len(capped)} of {len(kr)} units changed by the 22-78% cap; log-loss raw {sum(ll(r['p_raw'], r['y']) for r in kr)/len(kr):.4f} "
      f"vs published {sum(ll(r['p_live'], r['y']) for r in kr)/len(kr):.4f}")
print("reproduction: |current recomputed - logged projection| median", sorted(abs(r["lam_cur"] - r["lam_logged"]) for r in rows if r["lam_logged"])[len(rows)//2])

print("existing challengers on the same units (gain = live - challenger log-loss, + = challenger better)")
for k, nm in (("p_lb", "lb-001"), ("p_rr", "rr-1"), ("p_mm", "match_m1"), ("p_full", "full history (fh-1)"), ("p_cur", "current recomputed")):
    rs = [r for r in rows if r.get(k) is not None]
    g = lambda r: ll(r["p_live"], r["y"]) - ll(r[k], r["y"]); c = ci(rs, g, B=1000)
    print(f"  {nm:22} {len(rs)} units / {len({r['m'] for r in rs})} matches: {sum(g(r) for r in rs)/len(rs):+.4f} ({c[0]:+.4f} to {c[1]:+.4f}) | AUC {auc(rs, k):.3f}")
