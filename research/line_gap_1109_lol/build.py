"""lg-lol-1 dataset builder (definitions: FROZEN.md). Read-only on a local copy of the LoL data-center tables.
usage: python build.py <lol_1109.db> <out.pkl>
One row per settled app line (A15 latest pre-match forecast per platform|market_key|line), de-duplicated, with the
other app's main line, opening line, bump and Underdog no-vig price - all from quote events observed <= forecast_at."""
import sqlite3, json, sys, pickle, collections
from datetime import datetime, timedelta, timezone

DB, OUT = sys.argv[1], sys.argv[2]
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
c.row_factory = sqlite3.Row
COLLECTOR0 = "2026-10-17T02:40:59Z"
MAIN = {"prizepicks": "standard", "underdog": "balanced"}
SCOPE_Q = {"maps_1": "1", "maps_1_2": "12", "maps_1_2_3": "123"}


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def amer(a):
    a = float(a)
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


# ---- A15 record
q = ("SELECT f.*, s.status AS st, s.actual, s.over_hit FROM forecasts f JOIN (SELECT platform, market_key, line, MAX(id) AS id "
     "FROM forecasts WHERE forecast_at < series_start GROUP BY platform, market_key, line) lf ON lf.id = f.id "
     "JOIN (SELECT unit_key, MAX(version) AS v FROM settlements GROUP BY unit_key) lv ON lv.unit_key = f.platform || '|' || "
     "f.market_key || '|' || f.line JOIN settlements s ON s.unit_key = lv.unit_key AND s.version = lv.v")
raw = [dict(r) for r in c.execute(q)]
rec = [r for r in raw if r["st"] in ("graded", "push")]
stats = dict(a15_rows=len(raw), a15_status=dict(collections.Counter(r["st"] for r in raw)))
best = {}
for r in rec:
    k = (r["sid"], r["pid"], r["stat"], r["scope"], r["platform"], r["odds_type"], r["line"])
    if k not in best or r["id"] > best[k]["id"]:
        best[k] = r
stats["dupes_removed"] = len(rec) - len(best)
rows = list(best.values())

# ---- quote market -> player-market
mk2pm = {}
for r in c.execute("SELECT DISTINCT market_key, platform, odds_type, sid, pid, stat, scope, series_start FROM forecasts"):
    mk2pm[r["market_key"]] = (r["sid"], r["pid"], r["stat"], r["scope"])
quotes = [dict(r) for r in c.execute("SELECT * FROM quotes ORDER BY observed_at, id")]
name2 = collections.defaultdict(set)          # (lower name, start day) -> player-markets' (sid, pid)
for qq in quotes:
    pm = mk2pm.get(qq["market_key"])
    if pm and qq["player"]:
        name2[(qq["player"].strip().lower(), ts(qq["start_time"]).astimezone(timezone.utc).date().isoformat())].add(pm[:2])
unmapped, byname = collections.Counter(), 0
for qq in quotes:
    if qq["market_key"] in mk2pm:
        continue
    sc = SCOPE_Q.get(qq["scope"])
    if qq["stat"] not in ("kills", "assists", "deaths") or not sc:
        unmapped["not modelled stat/scope"] += 1
        continue
    cand = name2.get((qq["player"].strip().lower(), ts(qq["start_time"]).astimezone(timezone.utc).date().isoformat())) if qq["start_time"] else None
    if cand and len(cand) == 1:
        sid, pid = next(iter(cand))
        mk2pm[qq["market_key"]] = (sid, pid, qq["stat"], sc)
        byname += 1
    else:
        unmapped["no exact name match" if not cand else "ambiguous"] += 1
stats["quote_events"] = len(quotes)
stats["quote_events_mapped_by_name"] = byname
stats["quote_events_unmapped"] = dict(unmapped)

# ---- event streams per (player-market, platform, odds_type)
stream = collections.defaultdict(list)
for qq in quotes:
    pm = mk2pm.get(qq["market_key"])
    if pm:
        stream[(pm, qq["platform"], qq["odds_type"])].append(qq)


def state(pm, plat, ot, t):
    """(line, sides dict, last line-change time) offered at t, or None."""
    ev = [e for e in stream.get((pm, plat, ot), []) if e["observed_at"] <= t]
    if not ev or ev[-1]["event"] == "removed":
        return None
    last = ev[-1]
    lchg = None
    for a, b in zip(ev, ev[1:]):
        if b["line"] != a["line"] and b["event"] != "removed":
            lchg = b["observed_at"]
    try:
        sides = json.loads(last["sides"] or "{}")
    except Exception:
        sides = {}
    return last["line"], sides, lchg


def opening(pm, plat, ot):
    ev = stream.get((pm, plat, ot), [])
    if not ev:
        return None, None
    known = ts(ev[0]["observed_at"]) >= ts(COLLECTOR0) + timedelta(minutes=30)
    return ev[0]["line"], known


def ud_nv(sides):
    try:
        h, l = sides["higher"], sides["lower"]
        if h.get("status", "active") != "active" or l.get("status", "active") != "active":
            return None
        ph, pl = amer(h["american_price"]), amer(l["american_price"])
        return ph / (ph + pl)
    except Exception:
        return None


out = []
for r in rows:
    pm = (r["sid"], r["pid"], r["stat"], r["scope"])
    t = r["forecast_at"]
    side = "over" if (r["p_over"] or 0) >= (r["p_under"] or 0) else "under"
    if r.get("model_side") in ("over", "under") and abs((r["p_over"] or 0) - (r["p_under"] or 0)) < 1e-3:
        side = r["model_side"]
    pnp = (r["p_over"] or 0) + (r["p_under"] or 0)
    p_side = ((r["p_over"] if side == "over" else r["p_under"]) / pnp) if pnp else None
    d = dict(id=r["id"], sid=r["sid"], pid=r["pid"], stat=r["stat"], scope=r["scope"], platform=r["platform"],
             odds_type=r["odds_type"], line=r["line"], t=t, start=r["series_start"], day=r["series_start"][:10],
             best_of=r["best_of"], model=r["model_version"], lam=r["lambda"], p_over=r["p_over"], p_side=p_side,
             side=side, grade=r["grade"], side_offered=r["side_offered"], st=r["st"], actual=r["actual"],
             over_hit=r["over_hit"], main=MAIN.get(r["platform"]) == r["odds_type"])
    # this app/odds_type: state at t, opening, bump
    me = state(pm, r["platform"], r["odds_type"], t)
    d["line_at_t"] = me[0] if me else None
    o, known = opening(pm, r["platform"], r["odds_type"])
    d["open_line"], d["open_known"] = o, known
    d["bump"] = (r["line"] - o) if (o is not None and known) else None
    d["bump_hours"] = ((ts(r["series_start"]) - ts(me[2])).total_seconds() / 3600) if (me and me[2]) else None
    # other app main line
    other = "underdog" if r["platform"] == "prizepicks" else "prizepicks"
    os_ = state(pm, other, MAIN[other], t)
    d["other_line"] = os_[0] if os_ else None
    d["gap"] = (r["line"] - os_[0]) if (os_ and d["main"]) else None
    d["gap_pct"] = (d["gap"] / os_[0]) if (d["gap"] is not None and os_[0]) else None
    # Underdog no-vig at its main line (this row if UD, else the other app's)
    uds = me[1] if (r["platform"] == "underdog" and me) else (os_[1] if (other == "underdog" and os_) else None)
    d["ud_nv_over"] = ud_nv(uds) if uds else None
    out.append(d)

stats["rows"] = len(out)
pickle.dump(dict(rows=out, stats=stats), open(OUT, "wb"))
print(json.dumps(stats, indent=1))
