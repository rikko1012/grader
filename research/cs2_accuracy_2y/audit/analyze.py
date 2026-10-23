"""AU-1 analysis: problem classes on the P1 audit records (build.py -> units.json) and their effect on measured accuracy.

Metrics per set of units: favoured-side hit rate (p > 0.5 -> Over, p < 0.5 -> Under; p == 0.5 has no favoured side and is
left out of the hit rate only), log loss and Brier of P(Over) vs the outcome. Effects: (a) affected vs unaffected units,
difference with a match-grouped bootstrap 95% interval; (b) the whole P1 cohort with vs without the affected units (paired:
both computed on the same match resample). Intervals: B = 2000 resamples of canonical matches (unit 'match'), seed 20261022.
Nothing is fitted. usage: python analyze.py <S>  ->  S/audit/analysis.json (+ printed tables)
"""
import collections
import json
import math
import random
import re
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

S = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    r"C:\Users\tikic\AppData\Local\Temp\claude\C--Users-tikic-OneDrive-Documents-cs2-scout\6847ff60-df65-4b0c-851b-1b79f55ef3bf\scratchpad\cs2acc")
B, SEED, EPS = 2000, 20261022, 1e-6


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def ts(s):
    s = str(s).replace("Z", "+00:00").replace(" ", "T")
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    d = datetime.fromisoformat(s)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def stats(us, over_key="over"):
    n = len(us)
    if not n:
        return {"n": 0}
    ll = br = 0.0
    fav = hit = 0
    for u in us:
        p, y = min(max(u["p"], EPS), 1 - EPS), u[over_key]
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        br += (p - y) ** 2
        if u["p"] != 0.5:
            fav += 1
            hit += int((u["p"] > 0.5) == (y == 1))
    return {"n": n, "matches": len({u["match"] for u in us}), "hit": hit / fav if fav else None, "fav_n": fav,
            "logloss": ll / n, "brier": br / n, "mean_p_fav": sum(max(u["p"], 1 - u["p"]) for u in us) / n}


def _by_match(us):
    g = collections.defaultdict(list)
    for u in us:
        g[u["match"]].append(u)
    return g


def _agg(us, key):
    """(n, logloss sum, favoured n, hits) for a list of units."""
    n = ll = fav = hit = 0
    for u in us:
        p, y = min(max(u["p"], EPS), 1 - EPS), u[key]
        n += 1
        ll += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        if u["p"] != 0.5:
            fav += 1
            hit += int((u["p"] > 0.5) == (y == 1))
    return (n, ll, fav, hit)


def boot(units, aff, over_key="over", over_key_b=None):
    """aff: set of unit indices. Point + 95% CIs (match-grouped bootstrap) for (affected - unaffected) and (clean cohort - all)
    in hit rate and log loss; over_key_b: an alternative outcome for every unit (corrections) -> (corrected - original)."""
    rnd = random.Random(SEED)
    g = _by_match(units)
    keys = sorted(g)
    A = {k: _agg([u for u in g[k] if u["i"] in aff], over_key) for k in keys}
    R = {k: _agg([u for u in g[k] if u["i"] not in aff], over_key) for k in keys}
    C = {k: _agg(g[k], over_key_b) for k in keys} if over_key_b else None

    def compute(ks):
        a = [sum(A[k][j] for k in ks) for j in range(4)]
        b = [sum(R[k][j] for k in ks) for j in range(4)]
        t = [a[j] + b[j] for j in range(4)]
        r = {}
        if a[0] and b[0] and a[2] and b[2]:
            r["d_hit_aff_minus_rest"] = a[3] / a[2] - b[3] / b[2]
            r["d_ll_aff_minus_rest"] = a[1] / a[0] - b[1] / b[0]
        if b[0] and b[2]:
            r["d_hit_clean_minus_all"] = b[3] / b[2] - t[3] / t[2]
            r["d_ll_clean_minus_all"] = b[1] / b[0] - t[1] / t[0]
        if C:
            c = [sum(C[k][j] for k in ks) for j in range(4)]
            r["d_hit_corrected_minus_orig"] = c[3] / c[2] - t[3] / t[2]
            r["d_ll_corrected_minus_orig"] = c[1] / c[0] - t[1] / t[0]
        return r

    point = compute(keys)
    if not aff:
        return {"point": point}
    draws = collections.defaultdict(list)
    for _ in range(B):
        for k, v in compute([rnd.choice(keys) for _ in keys]).items():
            draws[k].append(v)
    ci = {}
    for k, v in draws.items():
        v.sort()
        ci[k] = [round(v[int(0.025 * len(v))], 4), round(v[int(0.975 * len(v)) - 1], 4)]
    return {"point": {k: round(v, 4) for k, v in point.items()}, "ci95": ci}


def main():
    D = json.load(open(S / "audit" / "units.json"))
    U = D["units"]
    for u in U:
        assert u["over"] == int(u["actual"] > u["line"]) and u["actual"] != u["line"], u["i"]   # P1 label / push exclusion
    h = sqlite3.connect(f"file:{S / 'data' / 'hltv_window.sqlite'}?mode=ro", uri=True)
    teams = dict(h.execute("SELECT teamid, name FROM teams"))
    # each real player's prior maps per team before the match day (roster change / first match for a team)
    tps = sorted({u["tp"] for u in U if u["tp"]})
    prior = collections.defaultdict(list)                 # tp -> [(date, teamid)] series level (mapid 0 rows skipped)
    for i in range(0, len(tps), 500):
        part = tps[i:i + 500]
        for p, t, d in h.execute(f"SELECT ps.playerid, ps.teamid, mo.date FROM player_stats ps JOIN match_overview mo USING (matchid) "
                                 f"WHERE ps.sideid = 0 AND ps.mapid > 0 AND ps.playerid IN ({','.join('?' * len(part))})", part):
            prior[p].append((d, t))

    cls = collections.OrderedDict()

    def add(name, cond, note):
        cls[name] = {"note": note, "idx": {u["i"] for u in U if cond(u)}}

    # ---------------- 1. identity ----------------
    add("I1 projection on a namesake (HLTV id not on the scoreboard; projection read that id's maps)",
        lambda u: u["hltv_match"] and u["pid"] and not u["played"] and u["perf_version"] in ("perf-2", "perf-cm2"),
        "Lucky (Envy) 2026-10-15: id 13843 (ex-Washed) instead of 13497 - old tie-break (all-time maps); fixed 10-21 (180b6e8, 12-month maps)")
    add("I2 namesake HLTV id saved but projection on BO3.gg (no HLTV maps in 420 d for that id)",
        lambda u: u["hltv_match"] and u["pid"] and not u["played"] and u["perf_version"] == "perf-2-bo3",
        "BO3.gg 'brain' -> HLTV id 5615 'brain' (no maps); the player is HLTV 'Brain47'")
    add("I3 HLTV name alias missed -> BO3.gg fallback (the player IS in HLTV under another nick)",
        lambda u: u["perf_version"] == "perf-2-bo3" and u["tp_how"] in ("eval alias", "nick prefix"),
        "dev1ce = HLTV 'device' (known, owner decision), Techno4K = HLTV 'Techno', brain = 'Brain47'")
    add("I4 BO3.gg fallback although HLTV has the exact nick (N0 pin: history outside N0 / not linked)",
        lambda u: u["perf_version"] == "perf-2-bo3" and u["tp_how"] == "nick exact",
        "design: perf_live.player_maps reads N0 only; cm-2 never applies on the fallback path")
    add("I5 match key never resolved to an HLTV match (BO3.gg slug)",
        lambda u: u["hltv_match"] is None,
        "Orgless/Sokerorg listed vs HLTV 'LFO 8' / 'LFO 10' (team alias); Martyrdom vs MARKandLARRY absent from HLTV")
    add("I6 first match for this team in HLTV (roster change / stand-in) - not an error, risk marker",
        lambda u: u["tp"] and u["played_team"] and not any(d < u["start"][:10] and t == u["played_team"] for d, t in prior[u["tp"]])
        and any(d < u["start"][:10] for d, t in prior[u["tp"]]),
        "the player has earlier HLTV maps, none for the team he played this match for")
    add("I7 stand-in / not-on-listed-team flags in the saved inputs",
        lambda u: bool(u["flags"].get("is_standin")) or bool(u["flags"].get("standin_for")) or u["flags"].get("on_listed_team") is False,
        "grader gates: stand-in cap 1U / 0U")
    add("I8 unconfirmed roster flag (roster_confirmed False)",
        lambda u: u["flags"].get("roster_confirmed") is False, "risk marker only")

    team_alias = collections.Counter()
    for u in U:
        if u["hltv_match"] and u["q_team"] and u["hltv_team_names"]:
            hn = {norm(x) for x in u["hltv_team_names"]}
            for x in (u["q_team"], u["q_opp"]):
                if x and norm(x) not in hn:
                    team_alias[(x, " / ".join(u["hltv_team_names"]))] += 1
    # ---------------- 2. settlement ----------------
    add("S1 settled total differs from HLTV (production read)", lambda u: (u.get("hltv") or {}).get("total") not in (None, u["actual"]), "")
    add("S2 settled total not verifiable against HLTV", lambda u: (u.get("hltv") or {}).get("total") is None, "")
    add("S3 overtime on a map inside the line's scope (OT kills/HS included in the total)", lambda u: bool((u.get("hltv") or {}).get("ot_maps")),
        "settlement includes OT (HLTV scoreboard = full map); descriptive, see CX-OT")
    add("S4 forfeited map / data_issues row on the match", lambda u: bool(u["forfeited"]) or bool(u["data_issues"]), "")
    add("S5 Maps 1-3 on a BO5 (map 4-5 excluded)", lambda u: u["scope"] == "123", "")
    add("S6 Map 1 line", lambda u: u["scope"] == "1", "")
    # ---------------- 3. duplicates ----------------
    k1 = collections.defaultdict(list)          # the real player (HLTV id when known, else the nick) x start x stat x scope
    for u in U:
        who = norm(u.get("tp_nick") or u.get("bo3_nick") or u["nick"])
        if who:
            k1[(who, u["start"][:16], u["prop"], u["scope"])].append(u["i"])
    dup_of = {}
    for v in k1.values():
        if len(v) > 1:
            keep = next((i for i in v if U[i]["match"].startswith("hltv:")), v[0])
            for i in v:
                if i != keep:
                    dup_of[i] = keep
    add("D1 same player x start x stat x scope counted as >1 P1 unit (two match keys)", lambda u: u["i"] in dup_of,
        "the BO3.gg-slug copy of a unit also scored under hltv:<id> (identity.assign links only identical team pairs)")
    add("D2 the prop was offered on both books at forecast time (one P1 unit)", lambda u: bool(u["quote"]) and u["quote"]["other_line"] is not None,
        "quote anchors (from 2026-10-15)")
    add("D3 ... and the other book's line differed", lambda u: bool(u["quote"]) and u["quote"]["other_line"] not in (None, u["line"]), "")
    # ---------------- 4. freshness ----------------
    add("F1 forecast logged > 3 h before the start", lambda u: u["lead_min"] > 180, "")
    add("F2 the P1 pick has later (regraded) forecasts inside the last 30 min", lambda u: bool(u["pick_later"]), "excluded by the 30-min rule")

    def moved(u):
        g = u["pg"]
        mv = bool(g) and g["line"] != u["line"]
        mv2 = any(x[0] > u["forecast_id"] and x[2] != u["line"] for x in (u.get("src_lines") or []))
        return mv or mv2

    add("F3 the line moved after the P1 forecast (scraper last line or later same-id forecast differs)", moved, "")
    add("F4 odds blip at forecast time (Thunderpick fallback)", lambda u: (u["odds"] or {}).get("blip") is True,
        "perf lines take no market input; stake cap / shown win chance only")
    # ---------------- 5. leakage ----------------
    add("L1 newest history map on/after the match day", lambda u: u["newest_map_day"] is not None and u["newest_map_day"] >= u["match_day"], "")
    add("L2 cm-2 feature day != match day or refit on/after the match day",
        lambda u: u["cm2_day"] is not None and (u["cm2_day"] != u["match_day"] or date.fromisoformat(u["cm2_refit"]).toordinal() >= u["match_day"]), "")
    add("L3 realized per-map numbers present in the saved inputs", lambda u: u["per_map_nonempty"], "")
    add("L4 HLTV data read / odds retrieved after the forecast time",
        lambda u: any(a and ts(a) > ts(u["logged"]) for a in (u["hd_as_of"] or []) + [u["hltv_series_as_of"], (u["odds"] or {}).get("retrieved_at")]), "")
    add("L5 forecast logged after the Thunderpick-listed start", lambda u: bool((u["odds"] or {}).get("start_utc_from_source"))
        and ts(u["odds"]["start_utc_from_source"]) <= ts(u["logged"]), "")
    add("L6 HLTV match dated before the forecast day", lambda u: u["hltv_date"] is not None and u["hltv_date"] < u["logged"][:10], "")

    # combined: every unit with an identity / duplicate / unverifiable-settlement defect, with and without the N0 design class I4
    u1 = set().union(*(cls[k]["idx"] for k in cls if k.startswith(("I1", "I2", "I3", "I4", "I5", "D1"))))
    u2 = set().union(*(cls[k]["idx"] for k in cls if k.startswith(("I1", "I2", "I3", "I5", "D1"))))
    cls["X1 any defect I1-I5 or D1"] = {"note": "union", "idx": u1}
    cls["X2 any defect I1-I3, I5, D1 (excluding the N0-design class I4)"] = {"note": "union", "idx": u2}

    # corrected outcome vs the closing line (F3): the outcome against the last line the book showed
    for u in U:
        g = u["pg"]
        close = g["line"] if (g and g["line"] != u["line"]) else None
        later = [x for x in (u.get("src_lines") or []) if x[0] > u["forecast_id"] and x[2] != u["line"]]
        if later:
            close = later[-1][2]
        u["close_line"] = close
        u["over_close"] = u["over"] if close is None or u["actual"] == close else int(u["actual"] > close)

    out = {"p1_at": D["p1_at"], "units": len(U), "matches": len({u["match"] for u in U}), "overall": stats(U), "classes": {},
           "team_name_mismatches": [{"listed": a, "hltv": b, "units": n} for (a, b), n in team_alias.most_common()]}
    print("P1", out["overall"])
    for name, c in cls.items():
        aff = c["idx"]
        a = [u for u in U if u["i"] in aff]
        r = {"note": c["note"], "units": len(a), "matches": len({u["match"] for u in a}), "affected": stats(a),
             "rest": stats([u for u in U if u["i"] not in aff])}
        if a and len(a) < len(U):
            r["effect"] = boot(U, aff, over_key_b="over_close" if name.startswith("F3") else None)
        if name.startswith(("I1", "I2", "I3", "I5", "S1", "S2", "F3")):
            r["examples"] = [{k: u.get(k) for k in ("match", "start", "nick", "tp_nick", "bo3_nick", "prop", "scope", "line", "close_line",
                                                    "actual", "p", "book", "perf_version", "version")} for u in a[:12]]
        out["classes"][name] = r
        e = (r.get("effect") or {})
        print(f"{name[:70]:70s} units {r['units']:5d} m {r['matches']:4d} hit {r['affected'].get('hit')} ll {r['affected'].get('logloss')}",
              json.dumps(e.get("point")), json.dumps(e.get("ci95")))
    json.dump(out, open(S / "audit" / "analysis.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
