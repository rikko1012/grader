"""Real-line archive, 2026-09-28 .. 2026-10-16 (owner 2026-10-16 night). Read-only on every source.

Sources
  OFFERED LINES  prop_grades (HLTV scraper DB): every line the platforms offered that the scraper read (Underdog from
                 2026-10-02, PrizePicks from 2026-10-11), the last line seen before the start, graded against HLTV.
  OUR FORECASTS  Railway pick log (export 2026-10-10 16:40 UTC) + Railway forecast log (2026-10-07 21:11 .. 10-08 23:24)
                 and the Render pick + forecast logs (paged export 2026-10-16 ~18:40 UTC; complete from 2026-10-11 04:56).
  PERFORMANCE    the 2026-10-15 study rebuilt through 2026-10-16 (acc2/series.pkl): perf-2 inputs from STRICTLY earlier
                 days, both history views (n0 ~ the live pin, n = full history).
Rules
  - one observation per (HLTV player, HLTV match, stat, scope, platform line): the same line on both platforms counts ONCE
    (a cross-platform copy); different lines on the two platforms are both kept; uncertainty is always grouped by match.
  - our forecast for a line = the latest forecast of a board pick at that platform / player / stat / scope / line logged
    >= 30 min before the start (Railway before 10-07 21:11: the pick row itself, only when it was last written >= 30 min
    before the start); regrades and host migrations never add observations.
  - outcome = the archive's HLTV grading (pushes and voids out), cross-checked against the HLTV totals in the study rows.
Output: archive.pkl (rows) + inventory.json."""
import collections
import json
import math
import os
import pickle
import re
import sys
from datetime import datetime, timedelta, timezone

SP = r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad"
RL = os.path.join(SP, "rl20")
sys.path.insert(0, r"C:\Users\tikic\cs2_scout\.claude\worktrees\eval-pipeline")
import perf  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")
norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
same_team = lambda a, b: bool(a and b) and (a in b or b in a or a[:4] == b[:4])
ts = lambda s: datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc) if s else None
iso = lambda d: d.isoformat(timespec="seconds")
day = lambda s: str(s)[:10]
PROP = {"kills": "kills", "headshots": "hs"}
SCOPE = {"1-2": "12", "1": "1"}


def load(fn, key):
    d = json.load(open(os.path.join(SP, fn), encoding="utf-8"))
    return d[key] if isinstance(d, dict) and key in d else d


# ------------------------------------------------------------------ performance rows (strictly pre-match inputs)
R = pickle.load(open(os.path.join(RL, "acc2", "series_m1.pkl"), "rb"))
META = json.load(open(os.path.join(RL, "acc2", "dump", "meta.json")))
PN = {int(k): v for k, v in META["players"].items()}
TN = {int(k): v[0] for k, v in META["teams"].items()}
rec = {(r["m"], r["p"]): r for r in R}
by_m = collections.defaultdict(list)
for r in R:
    by_m[r["m"]].append(r)
sys.path.insert(0, os.path.join(RL, "acc2"))
from study import comp, LIVE  # noqa: E402

Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def p_over(lam, line, prop, scope):
    """The live distribution at a line: discretised normal, sd = c sqrt(lam) (perf.SPREAD), P(Over | no push); kills 22-78% cap."""
    c = perf.SPREAD.get((prop, scope), perf.SPREAD[(prop, "12")])
    sd = c * math.sqrt(max(lam, 1.0))
    if line != int(line):
        po = 1 - Phi((math.floor(line) + 0.5 - lam) / sd)
        pu = 1 - po
    else:
        po, pu = 1 - Phi((line + 0.5 - lam) / sd), Phi((line - 0.5 - lam) / sd)
    p = po / (po + pu)
    return min(max(p, 0.22), 0.78) if prop == "kills" else p


def proj(r, view, prop, scope):
    c = comp(r, view)
    lam = c["lam"] if prop == "kills" else c["lam_hs"]
    return lam / 2 if scope == "1" else lam, c


# ------------------------------------------------------------------ our forecasts (Railway + Render)
RW_P = load("picks_now.json", "picks")
RW_F = {}
for fn in ("forecasts_dump.json", "forecasts_now.json", "forecasts_now2.json"):
    for f in load(fn, "forecasts"):
        RW_F[f["id"]] = f
RW_F_END = max(f["logged_at"] for f in RW_F.values())
RN_P = load(os.path.join("audit1016", "picks.json"), "picks")
RN_F = load(os.path.join("audit1016", "forecasts.json"), "forecasts")
fby = collections.defaultdict(list)
for host, F in (("railway", RW_F.values()), ("render", RN_F)):
    for f in F:
        fby[(host, f["pick_id"])].append(f)
board = []
for host, P in (("railway", RW_P), ("render", RN_P)):
    for p in P:
        if p.get("test") != 2 and not p.get("book"):
            continue
        if (p.get("line_type") or "standard") != "standard" or p.get("line") is None or not p.get("match_start"):
            continue
        board.append(dict(p, _host=host, _book=p.get("book") or "underdog"))
bidx = collections.defaultdict(list)
for p in board:
    bidx[(p["_book"], norm(p["player"]), p["prop"], p.get("scope") or "12", float(p["line"]))].append(p)


def our_forecast(book, player, prop, scope, line, start, opponent=None):
    """Latest saved board forecast >= 30 min before the start for this platform line (None when there is none); the pick's
    opponent must be the line's opponent (several BO1s of one team on one day)."""
    cut = start - timedelta(minutes=30)
    best = None
    for p in bidx.get((book, norm(player), prop, scope, float(line)), []):
        st = ts(p["match_start"])
        if abs((st - start).total_seconds()) > 12 * 3600:
            continue
        if opponent and not same_team(norm(p.get("opponent")), norm(opponent)) and not same_team(norm(p.get("team")), norm(opponent)):
            continue
        fs = [f for f in fby.get((p["_host"], p["id"]), []) if f.get("logged_at") and ts(f["logged_at"]) <= cut
              and f.get("p_more") is not None and float(f.get("line") or p["line"]) == float(line)]
        cand = [(ts(f["logged_at"]), "log", f) for f in fs]
        if not cand and p["_host"] == "railway" and p.get("logged_at") and ts(p["logged_at"]) <= cut and p.get("p_more") is not None \
                and (p["logged_at"] < "2026-10-07T21:11" or not fby.get(("railway", p["id"]))):
            cand = [(ts(p["logged_at"]), "pick row", p)]
        for t, kind, f in cand:
            if best is None or t > best[0]:
                best = (t, kind, f, p)
    if best is None:
        return None
    t, kind, f, p = best
    sh = f.get("shadow") if kind == "log" else None
    try:
        sh = json.loads(sh) if isinstance(sh, str) else (sh or {})
    except ValueError:
        sh = {}
    mr = sh.get("market_rounds") or {}
    return {"host": p["_host"], "kind": kind, "logged": iso(t), "p": float(f["p_more"]), "lam": f.get("lambda"), "grade": f.get("grade"),
            "units": f.get("units"), "version": f.get("model_version") or "untagged", "pick_id": p["id"],
            "p_rr": mr.get("p_more") if mr.get("status") == "ok" else None, "rec_hit": f.get("rec_hit"), "sample_maps": f.get("sample_maps")}


# ------------------------------------------------------------------ offered lines -> observations
G = json.load(open(os.path.join(RL, "prop_grades.json")))
inv = collections.defaultdict(collections.Counter)
obs, miss = [], collections.Counter()
units = collections.defaultdict(list)
for g in G:
    d = day(g["start_time"])
    inv[d]["offered lines (all types)"] += 1
    inv[d][f"offered {g['book']}"] += 1
    if g["odds_type"] != "standard":
        inv[d]["adjusted / alt (kept out)"] += 1
        continue
    if g["maps"] not in SCOPE:
        inv[d]["Maps 1-3 / Map 2 (kept out: no HLTV construction)"] += 1
        continue
    inv[d]["standard Maps 1-2 / Map 1"] += 1
    units[(g["matchid"], norm(g["player"]), PROP[g["market"]], SCOPE[g["maps"]])].append(g)
for (m, pl, prop, scope), gs in units.items():
    d = day(gs[0]["start_time"])
    inv[d]["unique player props"] += 1
    lines = {}
    for g in gs:                                   # a copy = the same line on the other platform: one observation
        lines.setdefault(float(g["line"]), []).append(g)
    if len(lines) < len(gs):
        inv[d]["cross-platform copies merged"] += len(gs) - len(lines)
    for line, gg in lines.items():
        g = gg[0]
        start = ts(g["start_time"])
        books = sorted({x["book"] for x in gg})
        why = None
        if g["status"] == "pending":
            why = "not settled yet (pending)"
        elif g["status"] in ("void", "no_match") or m is None:
            why = f"no outcome ({g['status']})"
        elif g["result"] == "push":
            why = "push"
        elif any("2 HLTV matches fit" in (x.get("note") or "") for x in gg):
            why = "ambiguous HLTV match (the teams played twice; the archive took the closest by date)"
        r = None
        if why is None:
            mp = by_m.get(int(m), [])
            ps = [x for x in mp if norm(PN.get(x["p"])) == pl]
            if not ps:                             # name variant (dev1ce / device, KralleJ / kralle): unique substring in this match only
                ps = [x for x in mp if pl and norm(PN.get(x["p"])) and (pl in norm(PN.get(x["p"])) or norm(PN.get(x["p"])) in pl)]
            r = ps[0] if len(ps) == 1 else None
            if r is not None and not (same_team(norm(TN.get(r["opp"])), norm(g["opponent"])) or same_team(norm(TN.get(r["team"])), norm(g["opponent"]))):
                r, why = None, "the archive's HLTV match has another opponent (wrong match for this line)"
            if r is not None and scope == "12" and r.get("y") is None:
                r, why = None, "Maps 1-2 line but map 2 has no full scoreboard"
            if r is None and why is None:
                why = "player not identified in the HLTV scoreboard of this match"
        fc = {b: our_forecast(b, g["player"], prop, scope, line, start, g["opponent"]) for b in books}
        fc = {b: v for b, v in fc.items() if v}
        inv[d]["observations (distinct lines)"] += 1
        if fc:
            inv[d]["with our saved pre-match forecast"] += 1
        if why:
            inv[d][f"out: {why}"] += 1
            miss[why] += 1
            continue
        y = 1 if g["result"] == "higher" else 0
        actual = float(g["actual"]) if g["actual"] is not None else None
        hl_tot = (r["y1"] if scope == "1" else r["y"]) if prop == "kills" else (r["y1_hs"] if scope == "1" else r["y_hs"])
        lam0, c0 = proj(r, "n0", prop, scope)
        lam1, c1 = proj(r, "n", prop, scope)
        o = {"m": int(m), "day": d, "start": iso(start), "books": books, "player": g["player"], "p_id": r["p"], "prop": prop, "scope": scope,
             "line": line, "y": y, "actual": actual, "hltv_total": hl_tot, "agree": (hl_tot is None or actual is None or abs(hl_tot - actual) < 1e-9),
             "lam_n0": lam0, "lam_n": lam1, "p_n0": p_over(lam0, line, prop, scope), "p_n": p_over(lam1, line, prop, scope),
             "rate": c0["rate"], "opp": c0["opp"], "hsr": c0["hsr"], "hist_r": r["n0"]["r"], "hist_r_n": r["n"]["r"], "maps_n0": r["n0"]["maps"],
             "lan": r["lan"], "elo": r["elo"], "event": r["event"], "n0_target": r["n0_target"], "rounds": r["rounds"] if scope == "12" else r.get("rounds1"), "won": r["won"], "map1_only": r.get("map1_only"),
             "fc": fc, "other_line": None}
        obs.append(o)
        inv[d]["usable settled observations"] += 1
        if fc:
            inv[d]["usable, with our forecast"] += 1
# the other platform's line for the same unit (pl-2 style anchor), when the two lines differ
bu = collections.defaultdict(list)
for o in obs:
    bu[(o["m"], o["p_id"], o["prop"], o["scope"])].append(o)
for v in bu.values():
    if len(v) == 2:
        v[0]["other_line"], v[1]["other_line"] = v[1]["line"], v[0]["line"]
# member-entered lines per day (kept separate; never part of the product cohort)
for host, P in (("railway", RW_P), ("render", RN_P)):
    for p in P:
        if p.get("test") in (0, None) and not p.get("book") and p.get("match_start"):
            inv[day(p["match_start"])][f"member-entered lines ({host})"] += 1
        if (p.get("test") == 2 or p.get("book")) and p.get("match_start"):
            inv[day(p["match_start"])][f"our board lines logged ({host})"] += 1
pickle.dump(obs, open(os.path.join(RL, "archive.pkl"), "wb"))
json.dump({d: dict(c) for d, c in sorted(inv.items())}, open(os.path.join(RL, "inventory.json"), "w"), indent=1)
print("observations", len(obs), "matches", len({o["m"] for o in obs}), "days", min(o["day"] for o in obs), "..", max(o["day"] for o in obs))
print("with our forecast", sum(1 for o in obs if o["fc"]), "| outcome disagrees with HLTV study total", sum(1 for o in obs if not o["agree"]))
print("excluded:", dict(miss))
print("Railway forecast log ends", RW_F_END)
