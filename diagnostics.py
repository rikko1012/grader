#!/usr/bin/env python3
"""
diagnostics.py - admin diagnostic dashboard (2026-10-09). Read-only. DIAGNOSTIC FLAGS, never a cause per loss: a
correctly estimated 60% pick loses 40% of the time without any bug.

Cohorts (never mixed):
- "first": each line's FIRST published forecast (the picks row, immutable);
- "eval":  the predefined evaluation snapshot - the LATEST forecast logged at least 30 minutes before the start;
- "slips": legs of the shadow slip batches (selection performance).
Every summary is split by board offers vs member research lines, stat (kills / headshots), map scope and model version.

Per settled line (where data allows): forecast vs actual total; predicted vs actual rounds (performance model, HLTV
settlement); predicted vs actual kill rate; predicted vs actual headshots per kill; whether the actual fell inside the
forecast's own 80% range (from the forecast's own distribution); calibration bins; match-grouped 95% intervals.
Exclusions are counted: no saved inputs, logged after start, pushes, voids, unsettled.
"""
import collections
import json
import math
import random
from datetime import datetime, timedelta

EVAL_LEAD_MIN = 30
PLAUSIBLE_MAX = {"kills": {"1": 50, "12": 90, "123": 130}, "hs": {"1": 35, "12": 60, "123": 90}}   # same as server.PLAUSIBLE_MAX
BINS = ((0.0, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 0.8), (0.8, 1.01))
# Forward HS check (predefined 2026-10-09, before any of these forecasts existed): board headshot lines, Maps 1-2,
# evaluation snapshot logged after FORWARD_FROM. Bands on the chance of OVER; current model vs the previous model
# (perf1_g7 shadow) and the smooth-prior challenger. Development data showed a possible Over-probability overstatement
# (-8.7 pts, 95% -17.9 to +1.0, 169 lines) - not confirmed; this is the forward test.
FORWARD_FROM = "2026-10-09T23:40:00+00:00"
HS_BANDS = ((0.0, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 1.01))
FORWARD_MODELS = (("current", None), ("previous (perf-1 / G7)", "perf1_g7"), ("smooth-prior challenger", "prior_smooth"))
# A shadow row counts only when the projection that graded it is the challenger's own (shadow.<key>.source, logged from
# the 2026-10-13 fix on). prior_smooth rows logged before it were graded by the OLD pipeline (its version was not in
# perf_versions: lambda = shadow.legacy on 316 of 316 Oct 11 forecasts) - INVALID, excluded and counted (CHALLENGERS.md).
SHADOW_SOURCE_SUFFIX = {"prior_smooth": "-prior-smooth"}


def shadow_p_more(sh, key):
    """(p_more, excluded) of shadow `key` in a forecast's shadow dict; excluded = a logged row its own model did not grade."""
    e = sh.get(key) or {}
    suf = SHADOW_SOURCE_SUFFIX.get(key)
    if e.get("p_more") is not None and suf and not str(e.get("source") or "").endswith(suf):
        return None, True
    return e.get("p_more"), False


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _boot(groups, stat, n=1000, seed=3):
    """Match-grouped bootstrap 95% interval of stat(list of rows)."""
    ms = list(groups)
    if len(ms) < 2:
        return None
    rnd = random.Random(seed)
    vals = sorted(stat([r for m in (rnd.choice(ms) for _ in ms) for r in groups[m]]) for _ in range(n))
    return [round(vals[int(0.025 * n)], 4), round(vals[int(0.975 * n) - 1], 4)]


def _quantiles(dist, qs=(0.1, 0.9)):
    """10th / 90th percentile of a grade's distribution (normal / mix / hs_bb)."""
    k = (dist or {}).get("kind")
    if k == "normal":
        z = 1.2815515655446004
        return dist["lam"] - z * dist["sd"], dist["lam"] + z * dist["sd"]
    if k == "mix":
        import grader
        lo = hi = None
        xs = [x / 2 for x in range(0, 400)]
        for x in xs:
            c = sum(w * grader.phi((x - lam) / sd) for w, lam, sd in dist["cases"])
            if lo is None and c >= qs[0]:
                lo = x
            if hi is None and c >= qs[1]:
                hi = x
                break
        return lo, hi
    if k == "hs_bb":
        import perf
        pmf = perf.hs_pmf(dist["lam_kills"], dist["scope"], dist["hs_per_kill"], c=dist.get("c"))
        cdf, lo, hi = 0.0, None, None
        for y in sorted(pmf):
            cdf += pmf[y]
            if lo is None and cdf >= qs[0]:
                lo = y
            if hi is None and cdf >= qs[1]:
                hi = y
        return lo, hi
    return None, None


def build(picks, forecasts, batches=(), now=None):
    import grader
    now = now or datetime.now().astimezone()
    by_pick = collections.defaultdict(list)
    for f in forecasts:
        by_pick[f["pick_id"]].append(f)
    excl = collections.Counter()
    rows = {"first": [], "eval": []}
    fwd = []
    for p in picks:
        t = p.get("test") or 0
        if t == 1:
            continue                                            # owner tests: never in diagnostics
        # platforms never mixed: "board" = Underdog board lines (unchanged), "board_pp" = PrizePicks board lines
        cohort_src = ("board_pp" if p.get("book") == "prizepicks" else "board") if t == 2 else "member"
        if p["status"] in ("open",):
            excl["pending"] += 1; continue
        if p["status"] == "void":
            excl["void"] += 1; continue
        if p["status"] == "push":
            excl["push"] += 1; continue
        if p.get("actual") is None or p.get("hit") is None:
            excl["no result"] += 1; continue
        if not (0 < p["line"] <= PLAUSIBLE_MAX.get(p["prop"], {}).get(p.get("scope") or "12", 1e9)):
            excl["implausible line (typo)"] += 1; continue
        start = _ts(p.get("match_start"))
        try:
            res = json.loads(p.get("result") or "{}")
        except ValueError:
            res = {}
        base = {"pick_id": p["id"], "source": cohort_src, "prop": p["prop"], "scope": p.get("scope") or "12",
                "match": p.get("match_slug") or f"{p.get('player')}|{p.get('match_start')}", "actual": p["actual"], "line": p["line"],
                "settled_from": res.get("source") or "unknown", "per_map": res.get("per_map"), "rounds": res.get("rounds"),
                "maps_used": res.get("maps_used")}
        # FIRST published forecast = the picks row itself
        if start and _ts(p.get("logged_at")) and _ts(p["logged_at"]) >= start:
            excl["first forecast after the start"] += 1
        else:
            rows["first"].append(dict(base, version=p.get("model_version") or "untagged", side=p["pick"], p=p.get("p_pick"),
                                      lam=p.get("lambda"), grade=p.get("grade"), units=p.get("units"), hit=p["hit"], inputs=None))
        # EVAL snapshot = latest forecast >= 30 min before the start (needs the forecast history, from 2026-10-08)
        fs = [f for f in by_pick.get(p["id"], []) if start and _ts(f.get("logged_at")) and _ts(f["logged_at"]) <= start - timedelta(minutes=EVAL_LEAD_MIN)]
        if not fs:
            excl["no forecast 30+ min before the start (or before history began)"] += 1
            continue
        f = max(fs, key=lambda x: x["logged_at"])
        side = f.get("pick") or p["pick"]
        if p["actual"] == f["line"]:
            excl["eval snapshot line pushed"] += 1; continue
        hit = int((p["actual"] > f["line"]) == (side == "More"))
        rows["eval"].append(dict(base, version=f.get("model_version") or "untagged", side=side, p=f.get("p_pick"), lam=f.get("lambda"),
                                 grade=f.get("grade"), units=f.get("units"), hit=hit, line=f["line"], inputs=f.get("inputs")))
        if cohort_src == "board" and p["prop"] == "hs" and base["scope"] == "12" and _ts(f["logged_at"]) >= _ts(FORWARD_FROM):
            try:
                sh = json.loads(f.get("shadow") or "null") or {}
            except ValueError:
                sh = {}
            pm = {"current": f.get("p_more") if f.get("p_more") is not None else (f.get("p_pick") if side == "More" else 1 - f["p_pick"]) if f.get("p_pick") is not None else None}
            bad = []
            for name, key in FORWARD_MODELS[1:]:
                pm[name], x = shadow_p_more(sh, key)
                if x:
                    bad.append(name)
            fwd.append({"match": base["match"], "over": int(p["actual"] > f["line"]), "p": pm, "version": f.get("model_version") or "untagged",
                        "excluded": bad})
    out = {"generated": now.isoformat(timespec="seconds"), "eval_lead_min": EVAL_LEAD_MIN, "exclusions": dict(excl), "cohorts": {}}
    for cname, rs in rows.items():
        for r in rs:                                            # per-row diagnostics (+ flags, never causes)
            r["flags"] = []
            r["err"] = (r["lam"] - r["actual"]) if r["lam"] is not None else None
            gi = r.pop("inputs", None)
            if gi:
                try:
                    g = grader.grade_leg(dict(gi))
                    lo, hi = _quantiles(g.get("dist"))
                    if lo is not None:
                        r["in80"] = int(lo <= r["actual"] <= hi)
                    pf = gi.get("perf") if isinstance(gi.get("perf"), dict) else {}
                    if pf.get("rounds_per_map") and r["rounds"] and r["maps_used"]:
                        pred_r = pf["rounds_per_map"] * len(r["maps_used"])
                        act_r = sum(r["rounds"][i - 1] for i in r["maps_used"] if i - 1 < len(r["rounds"]))
                        r["rounds_pred"], r["rounds_act"] = round(pred_r, 1), act_r
                        if act_r < 0.8 * pred_r:
                            r["flags"].append("short match (rounds 20%+ below expected)")
                        elif act_r > 1.2 * pred_r:
                            r["flags"].append("long match (rounds 20%+ above expected)")
                        if r["per_map"] and pf.get("rate") is not None:
                            kills = sum(r["per_map"][i - 1][0] for i in r["maps_used"] if i - 1 < len(r["per_map"]))
                            r["rate_pred"] = round(pf["rate"] * pf.get("opp_factor", 1.0), 4)
                            r["rate_act"] = round(kills / act_r, 4) if act_r else None
                            if r["rate_act"] is not None and abs(r["rate_act"] / r["rate_pred"] - 1) > 0.25:
                                r["flags"].append("kill rate 25%+ off the forecast rate")
                            if r["prop"] == "hs" and pf.get("hs_per_kill") and kills:
                                hs = sum(r["per_map"][i - 1][1] or 0 for i in r["maps_used"] if i - 1 < len(r["per_map"]))
                                r["hsk_pred"], r["hsk_act"] = round(pf["hs_per_kill"], 3), round(hs / kills, 3)
                                if abs(r["hsk_act"] - r["hsk_pred"]) > 0.12:
                                    r["flags"].append("headshots per kill 0.12+ off the forecast")
                    src = str(g.get("projection_source") or "")
                    if "unreachable" in src:
                        r["flags"].append("HLTV-outage projection")
                    elif src.startswith("legacy"):
                        r["flags"].append("older-model projection")
                    elif src.endswith("-bo3"):
                        r["flags"].append("BO3.gg-history projection")
                except Exception:
                    r["flags"].append("replay failed")
            if r["settled_from"] == "bo3":
                r["flags"].append("settled from BO3.gg (not HLTV-verified)")
        groups = collections.defaultdict(list)
        for r in rs:
            groups[(r["source"], r["prop"], r["scope"], r["version"])].append(r)
        summ = []
        for (src, prop, sc, ver), g_ in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            bym = collections.defaultdict(list)
            for r in g_:
                bym[r["match"]].append(r)
            dec = [r for r in g_ if r["p"] is not None]
            ll = lambda xs: sum(-math.log(max(r["p"] if r["hit"] else 1 - r["p"], 1e-6)) for r in xs) / len(xs) if xs else None
            errs = [r["err"] for r in g_ if r["err"] is not None]
            cov = [r["in80"] for r in g_ if "in80" in r]
            top = [r for r in g_ if r.get("grade") in ("A+", "A", "A-", "B+")]
            summ.append({
                "source": src, "prop": prop, "scope": sc, "version": ver, "n": len(g_), "matches": len(bym),
                "hit_rate": round(sum(r["hit"] for r in g_) / len(g_), 4), "hit_ci": _boot(bym, lambda xs: sum(r["hit"] for r in xs) / len(xs)),
                "predicted": round(sum(r["p"] for r in dec) / len(dec), 4) if dec else None,
                "log_loss": round(ll(dec), 4) if dec else None,
                "proj_mae": round(sum(abs(e) for e in errs) / len(errs), 3) if errs else None,
                "proj_bias": round(sum(errs) / len(errs), 3) if errs else None,
                "bias_ci": _boot({m: [r for r in xs if r["err"] is not None] for m, xs in bym.items()},
                                 lambda xs: sum(r["err"] for r in xs) / max(len(xs), 1)) if errs else None,
                "in80": round(sum(cov) / len(cov), 4) if cov else None, "in80_n": len(cov),
                "rounds_bias": (round(sum(r["rounds_pred"] - r["rounds_act"] for r in g_ if "rounds_act" in r) / max(sum("rounds_act" in r for r in g_), 1), 2)
                                if any("rounds_act" in r for r in g_) else None),
                "calibration": [{"bin": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(b), "predicted": round(sum(r["p"] for r in b) / len(b), 4),
                                 "actual": round(sum(r["hit"] for r in b) / len(b), 4)}
                                for lo, hi in BINS for b in [[r for r in dec if lo <= max(r["p"], 1 - r["p"]) < hi]] if b],
                "top_selections": {"n": len(top), "hit_rate": round(sum(r["hit"] for r in top) / len(top), 4) if top else None,
                                   "predicted": round(sum(r["p"] for r in top if r["p"] is not None) / len(top), 4) if top else None},
                "flags": dict(collections.Counter(f for r in g_ for f in r["flags"])),
                "settled_from": dict(collections.Counter(r["settled_from"] for r in g_)),
            })
        # projection source (2026-10-10): HLTV performance model vs the provisional BO3.gg-history fallback vs the older
        # pipeline - the fallback's thin-history prior looked high on tier-3 players (e.g. 28 projected vs a 16 average)
        src_of = lambda r: ("BO3.gg-history (provisional)" if "BO3.gg-history projection" in r["flags"]
                            else "older model (HLTV outage)" if "HLTV-outage projection" in r["flags"]
                            else "older model" if "older-model projection" in r["flags"] else "HLTV performance model")
        by_src = collections.defaultdict(list)
        for r in rs:
            if r["source"] == "board" and r["err"] is not None and "replay failed" not in r["flags"] and "in80" in r:
                by_src[(src_of(r), r["prop"], r["scope"])].append(r)
        src_summ = []
        for (src, prop, sc), g_ in sorted(by_src.items()):
            bym = collections.defaultdict(list)
            for r in g_:
                bym[r["match"]].append(r)
            src_summ.append({"projection": src, "prop": prop, "scope": sc, "n": len(g_), "matches": len(bym),
                             "proj_bias": round(sum(r["err"] for r in g_) / len(g_), 3),
                             "bias_ci": _boot(bym, lambda xs: sum(r["err"] for r in xs) / len(xs)),
                             "hit_rate": round(sum(r["hit"] for r in g_) / len(g_), 4),
                             "predicted": round(sum(r["p"] for r in g_ if r["p"] is not None) / max(sum(r["p"] is not None for r in g_), 1), 4)})
        out["cohorts"][cname] = {"summary": summ, "by_projection_source": src_summ,
                                 "flagged_examples": [{k: r.get(k) for k in ("pick_id", "source", "prop", "scope", "version", "side", "line", "lam",
                                                                             "actual", "p", "hit", "rounds_pred", "rounds_act", "rate_pred",
                                                                             "rate_act", "hsk_pred", "hsk_act", "flags")}
                                                      for r in rs if r["flags"]][-60:]}
    out["forward_hs"] = forward_hs(fwd)
    # slip selections (shadow batches): exposure across batches + leg performance where settled
    leg_by = {(str(p.get("player") or "").lower(), p.get("prop"), p.get("scope") or "12", p.get("match_slug")): p for p in picks if p.get("test") == 2}
    expo_m, expo_p, legs = collections.Counter(), collections.Counter(), []
    for b in batches:
        for s in b.get("slips") or []:
            for l in s.get("legs") or []:
                expo_m[l.get("match_slug")] += 1
                expo_p[(l.get("nick") or l.get("player") or "").lower()] += 1
                p = leg_by.get(((l.get("nick") or l.get("player") or "").lower(), l.get("prop"), l.get("scope") or "12", l.get("match_slug")))
                if p and p["status"] == "settled" and p.get("actual") is not None and p["actual"] != l["line"]:
                    legs.append({"kind": s.get("kind"), "p": l.get("p"), "hit": int((p["actual"] > l["line"]) == (l.get("side") == "More"))})
    out["cohorts"]["slips"] = {
        "batches": len(batches), "leg_exposure_max_per_match": max(expo_m.values(), default=0),
        "leg_exposure_max_per_player": max(expo_p.values(), default=0),
        "legs_settled": len(legs), "leg_hit_rate": round(sum(x["hit"] for x in legs) / len(legs), 4) if legs else None,
        "leg_predicted": round(sum(x["p"] for x in legs if x["p"]) / len(legs), 4) if legs else None,
    }
    return out


def forward_hs(fwd):
    """Predicted vs actual Over rate per predefined band, per model, with match-grouped 95% intervals."""
    res = {"since": FORWARD_FROM, "lines": len(fwd), "matches": len({r["match"] for r in fwd}), "models": [],
           "versions": dict(collections.Counter(r.get("version") or "untagged" for r in fwd))}   # live version(s) behind "current"
    for name, _ in FORWARD_MODELS:
        rs = [r for r in fwd if r["p"].get(name) is not None]
        bands = []
        for lo, hi in HS_BANDS:
            b = [r for r in rs if lo <= r["p"][name] < hi]
            g = collections.defaultdict(list)
            for r in b:
                g[r["match"]].append(r)
            bands.append({"band": f"{lo:.2f}-{min(hi, 1):.2f}", "n": len(b), "matches": len(g),
                          "predicted": round(sum(r["p"][name] for r in b) / len(b), 4) if b else None,
                          "actual": round(sum(r["over"] for r in b) / len(b), 4) if b else None,
                          "actual_ci": _boot(g, lambda xs: sum(r["over"] for r in xs) / len(xs)) if b else None})
        g = collections.defaultdict(list)
        for r in rs:
            g[r["match"]].append(r)
        res["models"].append({"model": name, "n": len(rs), "bands": bands,
                              # exact live tag of each counted row (report per tag, MODEL_VERSIONS.md) + invalid rows left out
                              "versions": dict(collections.Counter(r.get("version") or "untagged" for r in rs)),
                              "excluded_invalid": sum(name in (r.get("excluded") or ()) for r in fwd),
                              "gap": round(sum(r["over"] - r["p"][name] for r in rs) / len(rs), 4) if rs else None,
                              "gap_ci": _boot(g, lambda xs: sum(r["over"] - r["p"][name] for r in xs) / len(xs)) if rs else None,
                              "log_loss": round(sum(-math.log(max(r["p"][name] if r["over"] else 1 - r["p"][name], 1e-6)) for r in rs) / len(rs), 4) if rs else None})
    return res
