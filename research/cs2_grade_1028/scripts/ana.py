"""Multilevel grading check, CS2 board, 2026-10-28.
Two rules: PICKS = first published grade per exact line (picks table row); P1 = latest board forecast >= 30 min before the
start on a standard offered line (learning export). Rows logged after the start are dropped. Every figure: stated chance vs
hit rate (W-L-P), Brier, log loss, bias (projection - actual), with match-grouped bootstrap 95% intervals.
usage: python -I ana.py [picks|p1|both]"""
import json, math, random, collections, sys, os, re
D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, D)


def grade_from_p(p):
    if p >= 0.80: return "A+"
    if p >= 0.74: return "A"
    if p >= 0.68: return "A-"
    if p >= 0.63: return "B+"
    if p >= 0.57: return "B"
    if p >= 0.55: return "B-"
    return "LEAN"


BAND = {"A+": "A", "A": "A", "A-": "A", "B+": "B", "B": "B", "B-": "B", "LEAN": "Lean", "C": "Lean"}


def boot(units, num, den, key, B=1000, seed=7):
    g = collections.defaultdict(lambda: [0.0, 0.0])
    for u in units:
        g[key(u)][0] += num(u); g[key(u)][1] += den(u)
    gs = list(g.values())
    if not gs: return (None, None)
    rnd = random.Random(seed); est = []
    for _ in range(B):
        s = n = 0.0
        for _ in range(len(gs)):
            a, b = gs[rnd.randrange(len(gs))]; s += a; n += b
        if n: est.append(s / n)
    est.sort()
    return (est[int(0.025 * len(est))], est[int(0.975 * len(est)) - 1])


def metrics(us, B=600):
    """us: dicts with p (chance of the picked side), hit (1/0/None push), proj, actual, match."""
    dec = [u for u in us if u["hit"] is not None]
    w = sum(u["hit"] for u in dec); l = len(dec) - w; push = len(us) - len(dec)
    if not dec:
        return {"n": len(us), "wlp": f"{w}-{l}-{push}"}
    stated = sum(u["p"] for u in dec) / len(dec)
    hit = w / len(dec)
    ci = boot(dec, lambda u: u["hit"], lambda u: 1, lambda u: u["match"], B=B)
    gap_ci = boot(dec, lambda u: u["p"] - u["hit"], lambda u: 1, lambda u: u["match"], B=B)
    brier = sum((u["p"] - u["hit"]) ** 2 for u in dec) / len(dec)
    eps = 1e-6
    ll = -sum(math.log(max(eps, u["p"] if u["hit"] else 1 - u["p"])) for u in dec) / len(dec)
    bias = [u["proj"] - u["actual"] for u in us if u.get("proj") is not None and u.get("actual") is not None]
    over_share = sum(1 for u in dec if u["side"] == "Over") / len(dec)
    over_rate = sum(1 for u in dec if (u["hit"] if u["side"] == "Over" else 1 - u["hit"])) / len(dec)
    return {"n": len(us), "wlp": f"{w}-{l}-{push}", "matches": len({u["match"] for u in dec}),
            "stated": round(100 * stated, 1), "hit": round(100 * hit, 1), "hit_ci": [round(100 * x, 1) for x in ci],
            "gap": round(100 * (stated - hit), 1), "gap_ci": [round(100 * x, 1) for x in gap_ci],
            "brier": round(brier, 4), "logloss": round(ll, 4),
            "bias": round(sum(bias) / len(bias), 2) if bias else None,
            "over_share": round(100 * over_share, 1), "over_rate": round(100 * over_rate, 1),
            "balance_gap": round(100 * (over_share - over_rate), 1)}


def by(us, keyf, name, min_n=1, B=600):
    g = collections.defaultdict(list)
    for u in us:
        g[keyf(u)].append(u)
    rows = []
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1])):
        if len(v) >= min_n:
            rows.append(dict(metrics(v, B=B), group=str(k)))
    return {"level": name, "rows": rows}


def order_holds(rows, order):
    """grade ORDER: hit rate non-increasing along the given band order (A > B > Lean)."""
    h = [next((r["hit"] for r in rows if r["group"] == o and r.get("hit") is not None), None) for o in order]
    h = [x for x in h if x is not None]
    return all(h[i] >= h[i + 1] for i in range(len(h) - 1)) if len(h) > 1 else None


def fmt_table(lv):
    out = [f"\n### {lv['level']}", "| group | n | W-L-P | matches | stated % | hit % [95%] | gap pts [95%] | Brier | log loss | bias (proj-act) | Over share % / Over rate % |",
           "|---|---:|---|---:|---:|---|---|---:|---:|---:|---|"]
    for r in lv["rows"]:
        if r.get("hit") is None:
            out.append(f"| {r['group']} | {r['n']} | {r['wlp']} | | | | | | | | |"); continue
        out.append(f"| {r['group']} | {r['n']} | {r['wlp']} | {r['matches']} | {r['stated']} | {r['hit']} [{r['hit_ci'][0]}, {r['hit_ci'][1]}] | "
                   f"{r['gap']:+} [{r['gap_ci'][0]:+}, {r['gap_ci'][1]:+}] | {r['brier']} | {r['logloss']} | {r['bias'] if r['bias'] is not None else ''} | "
                   f"{r['over_share']} / {r['over_rate']} |")
    return "\n".join(out)


# ---------------------------------------------------------------- context helpers
def source_of(flags):
    s = flags or ""
    if "complete model (perf-cm2" in s: return "perf-cm2"
    if "performance model (perf-2-bo3" in s: return "perf-2-bo3 (BO3.gg)"
    if "performance model (perf-2" in s: return "perf-2 (HLTV N0)"
    if "performance model (perf-1" in s: return "perf-1"
    if "OLD PROJECTION" in s or "old pipeline" in s: return "legacy"
    return "legacy/unknown"


def pwin_band(p):
    if p is None: return "no price"
    if p >= 0.65: return "favourite 65%+"
    if p >= 0.5: return "slight fav 50-65%"
    if p >= 0.35: return "slight dog 35-50%"
    return "underdog <35%"


def load_ctx():
    try:
        c = json.load(open(os.path.join(D, "hltv_ctx.json")))
    except Exception:
        return {}, {}
    ranks = collections.defaultdict(list)
    for r in c["ranks"]:
        ranks[r["teamid"]].append((str(r["date"]), r["rank"]))
    teams = collections.defaultdict(set)
    for r in c["teams"]:
        teams[r["matchid"]].add(r["teamid"])
    return teams, ranks


def rank_on(ranks, tid, day):
    rs = [rk for d, rk in ranks.get(tid, []) if d <= day]
    return rs[-1] if rs else None


def tier_of(mid, day, teams, ranks):
    if mid not in teams: return "non-HLTV match"
    rs = [rank_on(ranks, t, day) for t in teams[mid]]
    rs = [r for r in rs if r]
    if not rs: return "unranked"
    best = min(rs)
    return "top-30 team" if best <= 30 else "rank 31-100" if best <= 100 else "rank 100+"


def ot_of(result):
    try:
        rr = json.loads(result).get("rounds") or []
        return "overtime map" if any(r > 24 for r in rr) else "no overtime"
    except Exception:
        return "unknown"


# ---------------------------------------------------------------- PICKS rule
def picks_units():
    p = json.load(open(os.path.join(D, "picks.json")))["rows"]
    teams, ranks = load_ctx()
    out, dropped = [], collections.Counter()
    for r in p:
        if r.get("test") == 1: dropped["owner test"] += 1; continue
        if r["status"] not in ("settled", "push"): dropped[r["status"]] += 1; continue
        if r.get("match_start") and r["logged_at"][:19] >= r["match_start"][:19]: dropped["logged after start"] += 1; continue
        side = "Over" if r["pick"] == "More" else "Under"
        hit = None if r["status"] == "push" else int(r["hit"] or 0)
        f = {}
        try: f = json.loads(r.get("factors") or "{}")
        except Exception: pass
        det = f.get("detail") or {}
        env = f.get("environment") or {}
        mid = int(r["match_slug"][5:]) if (r["match_slug"] or "").startswith("hltv-") else None
        day = (r["match_start"] or "")[:10]
        u = {"p": float(r["p_pick"]), "hit": hit, "proj": r.get("lambda"), "actual": r.get("actual"), "match": r["match_slug"],
             "side": side, "book": r.get("book") or "member-typed", "prop": r["prop"], "scope": r.get("scope") or "12",
             "grade": r["grade"] if r["grade"] in ("A+", "A", "A-", "B+", "B", "B-") else "LEAN", "units": r.get("units") or 0,
             "line_type": r.get("line_type"), "version": r.get("model_version"), "source": source_of(r.get("flags")),
             "bo": ((det.get("map_prob_basis") or {}).get("format") or "unknown"), "p_win": det.get("p_win"),
             "env": env.get("tonight") or "unknown", "day": day, "tier": tier_of(mid, day, teams, ranks),
             "ot": ot_of(r.get("result")), "odds_src": ((det.get("market_effect") or {}).get("selected") or "none"),
             "key": r["key"], "player": r["slug"], "line": r["line"], "sd_reasons": det.get("sd_reasons") or [],
             "sample_maps": r.get("sample_maps"), "id": r["id"], "market": r.get("market")}
        out.append(u)
    return out, dropped


def dupes(us):
    g = collections.defaultdict(list)
    for u in us:
        g[(u["player"], u["prop"], u["line"], u["scope"], u["match"])].append(u["book"])
    both = sum(1 for v in g.values() if len(set(v)) > 1)
    return {"lines": len(g), "offered on 2+ platforms": both, "rows": len(us)}


def run_levels(us, tag, order_rows=True):
    levels = []
    levels.append({"level": "overall", "rows": [dict(metrics(us), group="all")]})
    levels.append(by(us, lambda u: u["book"], "platform"))
    levels.append(by(us, lambda u: u["prop"], "stat"))
    levels.append(by(us, lambda u: {"12": "Maps 1-2", "1": "Map 1", "123": "Maps 1-3"}.get(u["scope"], u["scope"]), "scope"))
    levels.append(by(us, lambda u: (u["prop"], {"12": "Maps 1-2", "1": "Map 1", "123": "Maps 1-3"}.get(u["scope"], u["scope"])), "stat x scope"))
    levels.append(by(us, lambda u: u["grade"], "grade (letter)", min_n=1))
    levels.append(by(us, lambda u: BAND[u["grade"]], "grade band"))
    levels.append(by(us, lambda u: "staked" if u["units"] > 0 else "0U", "staked vs 0U"))
    levels.append(by(us, lambda u: (BAND[u["grade"]], "staked" if u["units"] > 0 else "0U"), "band x staked"))
    levels.append(by(us, lambda u: u["side"], "Over vs Under"))
    levels.append(by(us, lambda u: (u["prop"], u["side"]), "stat x side"))
    levels.append(by(us, lambda u: u["source"], "projection source"))
    levels.append(by(us, lambda u: u["line_type"], "line type"))
    levels.append(by(us, lambda u: u["bo"], "series format"))
    levels.append(by(us, lambda u: u["tier"], "tier (best HLTV rank of the two teams)"))
    levels.append(by(us, lambda u: pwin_band(u["p_win"]), "strength gap (player's team win chance)"))
    levels.append(by(us, lambda u: (pwin_band(u["p_win"]), u["side"]), "strength gap x side"))
    levels.append(by(us, lambda u: u["env"], "LAN / online"))
    levels.append(by(us, lambda u: u["ot"], "overtime in the series"))
    levels.append(by(us, lambda u: u["odds_src"], "odds source"))
    levels.append(by(us, lambda u: u["version"], "model version tag", min_n=30))
    levels.append(by(us, lambda u: u["day"], "UTC start day", min_n=1, B=200))
    levels.append(by([u for u in us if u["units"] > 0], lambda u: u["day"], "UTC start day, staked only", min_n=1, B=200))
    orders = {"grade band": order_holds(levels[6]["rows"], ["A", "B", "Lean"]),
              "grade letter": order_holds(levels[5]["rows"], ["A+", "A", "A-", "B+", "B", "B-", "LEAN"])}
    return {"tag": tag, "levels": levels, "order_holds": orders}


def main(which):
    res = {}
    if which in ("picks", "both"):
        us, dropped = picks_units()
        res["picks"] = run_levels(us, "PICKS rule (first grade per exact line)")
        res["picks"]["dropped"] = dict(dropped); res["picks"]["duplicates"] = dupes(us)
        res["picks"]["books_overlap_note"] = "a line on both books is two rows (one per platform), each its own first grade"
    if which in ("p1", "both"):
        import p1join
        us, meta = p1join.units()
        res["p1"] = run_levels(us, "P1 rule (latest board forecast >= 30 min before start, standard offered line)")
        res["p1"]["meta"] = meta; res["p1"]["duplicates"] = dupes(us)
    json.dump(res, open(os.path.join(D, f"levels_{which}.json"), "w"), indent=1)
    with open(os.path.join(D, f"levels_{which}.md"), "w", encoding="utf-8") as f:
        for k, v in res.items():
            f.write(f"\n## {v['tag']}\n")
            f.write(f"order holds: {v['order_holds']}; dropped: {v.get('dropped')}; meta: {v.get('meta')}; duplicates: {v.get('duplicates')}\n")
            for lv in v["levels"]:
                f.write(fmt_table(lv) + "\n")
    print("done", which)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "picks")
