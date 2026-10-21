"""hs-cm2 registered study (PROTOCOL.md, commit e7e5e12). Run once; nothing is fitted or tuned here.

Inputs (read-only, pulled before this script runs):
  - the HLTV dump + N0 revision pickle written by data.py (cm1.SQL rows since 2025-09-01, NULL environment kept);
  - the production forecast log for ids > 37000 with each forecast's grader inputs (paged /admin/forecasts_page.json,
    500 a page, fields this study needs only) - fc_inputs.json.
usage: python study.py <hltv_dump.pkl> <fc_inputs.json> <out result.json>

Arms
  candidate hs-cm2: kills = cm2.projection (lambda, sd = spread_c sqrt(max(lambda,1))), HS per kill = cm1.hs_ratio on the same
      cm-2 tables, HS = perf.hs_pmf / hs_probs with c = cm-2 spread_c (kappa 100); where cm-2 is ineligible perf-2 stays.
  baseline: (1) perf-2 on the N0 rows rebuilt for the same day (perf.project, live row rules of perf_live), HS = mean of
      perf.hs_pmf as the grader does (perf_hs_dist); (2) the published chance (forecast p_more).
Hold-out: matches from 2026-10-17 00:00 UTC to the run day.
Decision (fixed): pass only if (1) MAE-gain 95% interval lies above 0 AND (2) log-loss gain point estimate >= 0."""
import collections
import json
import math
import pickle
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import cm1, cm2, perf  # noqa: E402

HOLD_FROM = "2026-10-17"
HOLD_FROM_TS = "2026-10-17T00:00:00+00:00"
B = 2000
SEED = 20261021
C_CM2 = cm2.FIT["spread_c"]
C_P2 = perf.SPREAD[("kills", "12")]


# ------------------------------------------------------------------------------------------------ helpers
def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def ll(p, y):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return -math.log(p if y else 1 - p)


def boot(rows, f, key="match", seed=SEED):
    """Match-grouped bootstrap (B resamples of whole matches) of the mean of f: [2.5%, 97.5%]."""
    g = collections.defaultdict(list)
    for r in rows:
        g[r[key]].append(f(r))
    sums = [(sum(v), len(v)) for v in g.values()]
    rnd, out = random.Random(seed), []
    n = len(sums)
    for _ in range(B):
        s = c = 0.0
        for _ in range(n):
            a, b = sums[rnd.randrange(n)]
            s += a
            c += b
        out.append(s / c)
    out.sort()
    return [out[int(0.025 * B)], out[int(0.975 * B) - 1]]


def hs_mean(lam_k, hsr, c):
    pmf = perf.hs_pmf(lam_k, "12", hsr, c=c)
    return sum(y * v for y, v in pmf.items())


def p_over_np(lam_k, hsr, line, c):
    o, u, _ = perf.hs_probs(lam_k, "12", hsr, line, c=c)
    return o / (o + u) if o + u > 0 else 0.5


# ------------------------------------------------------------------------------------------------ HLTV structures
def build_hltv(dump):
    rows, n0 = dump["rows"], dump["n0"]
    in_n0 = lambda m: m in n0["pre"] or m in n0["late_in"]
    dn = lambda s: date.fromisoformat(str(s)[:10]).toordinal()
    maps = collections.defaultdict(list)
    for r in rows:
        maps[(r[0], r[3])].append(r)
    pmaps = collections.defaultdict(list)     # perf-2 player rows (perf_live._PLAYER_SQL rule) - N0 only
    omaps = collections.defaultdict(list)     # perf-2 opponent rows (perf_live._OPP_SQL rule) - N0 only
    match_info = {}                            # matchid -> {date, lan, maps: {no: {player: (team, opp, k, hs)}}}
    for (m, no), v in maps.items():
        mi = match_info.setdefault(m, {"date": str(v[0][1])[:10], "lan": v[0][2], "maps": {}})
        mi["maps"][no] = {r[4]: (r[5], r[6], r[7], r[8]) for r in v}
        day = dn(v[0][1])
        ok10 = len(v) == 10 and len({r[4] for r in v}) == 10
        if in_n0(m):
            if ok10:
                for r in v:
                    if r[7] is None or r[9] is None or r[10] is None:
                        continue
                    pmaps[r[4]].append((day, r[7], r[9] + r[10], r[8], None if r[2] is None else bool(r[2]),
                                        not (r[8] == 0 and r[7] >= 12)))
            for t in {r[5] for r in v}:
                o = [r for r in v if r[5] != t]
                own = [r for r in v if r[5] == t]
                if (len(o) == 5 and len({r[4] for r in o}) == 5 and all(r[7] is not None and r[7] >= 0 for r in o) and own
                        and all(r[9] is not None and r[10] is not None and r[9] >= 0 and r[10] >= 0 for r in o)
                        and own[0][9] + own[0][10] > 0):
                    omaps[t].append((day, sum(r[7] for r in o), own[0][9] + own[0][10]))
    return {"match_info": match_info, "pmaps": pmaps, "omaps": omaps}


def perf2(H, pid_list, opp_id, lan, day):
    since = day - 420
    pm = [x for p in pid_list for x in H["pmaps"].get(int(p), []) if since <= x[0] < day]
    om = [x for x in H["omaps"].get(int(opp_id), []) if since <= x[0] < day] if opp_id else []
    pr = perf.project(pm, om, lan, day, "12", "hs")
    return pr, len(pm), len(om)


def cand(T, pid_list, team_id, opp_id, day, lan):
    r = cm2.projection(T, [int(p) for p in pid_list or []], team_id, opp_id, day, lan)
    if r.get("ineligible"):
        return None, r["ineligible"]
    return (r["lambda"], cm1.hs_ratio(r["inputs"]), r["inputs"]["maps"]), None


def series_actual(mi, pid):
    """(HS maps 1+2, team, opp) when the player played maps 1 and 2 with headshots recorded; else (None, reason)."""
    m1, m2 = mi["maps"].get(1), mi["maps"].get(2)
    if not m1 or not m2:
        return None, "maps 1-2 not both in HLTV"
    if pid not in m1 or pid not in m2:
        return None, "player not on both maps"
    a, b = m1[pid], m2[pid]
    if a[3] is None or b[3] is None:
        return None, "headshots not recorded"
    if (a[3] == 0 and (a[2] or 0) >= 12) or (b[3] == 0 and (b[2] or 0) >= 12):
        return None, "headshots flagged unrecorded (0 HS on 12+ kills)"
    return (a[3] + b[3], a[0], a[1]), None


# ------------------------------------------------------------------------------------------------ (1) HLTV-wide
def part1(H, T, today_ord):
    out_rows, excl = [], collections.Counter()
    for m, mi in H["match_info"].items():
        if mi["date"] < HOLD_FROM:
            continue
        m1, m2 = mi["maps"].get(1), mi["maps"].get(2)
        if not m1 or not m2:
            excl["series without maps 1 and 2 in HLTV (player-rows on map 1)"] += len(m1 or {})
            continue
        day = date.fromisoformat(mi["date"]).toordinal()
        lan = None if mi["lan"] is None else bool(mi["lan"])
        for pid in sorted(m1):
            act, why = series_actual(mi, pid)
            if act is None:
                excl[why] += 1
                continue
            hs, team, opp = act
            p2, npm, nom = perf2(H, [pid], opp, lan, day)
            base = hs_mean(p2["lambda_kills"], p2["hs_per_kill"], C_P2)
            c, why = cand(T, [pid], team, opp, day, lan)
            if c:
                cv, elig = hs_mean(c[0], c[1], C_CM2), True
            else:
                cv, elig = base, False
            out_rows.append({"match": m, "player": pid, "day": mi["date"], "actual": hs, "base": base, "cand": cv, "eligible": elig,
                             "ineligible": why, "n0_maps": npm, "base_quality": p2["data_quality"],
                             "cand_kills": c[0] if c else None, "base_kills": p2["lambda_kills"]})
    return out_rows, excl


def mae_block(rs):
    if not rs:
        return {"player_series": 0}
    g = lambda r: abs(r["base"] - r["actual"]) - abs(r["cand"] - r["actual"])
    n = len(rs)
    return {"player_series": n, "matches": len({r["match"] for r in rs}),
            "mae_base_perf2_n0": round(sum(abs(r["base"] - r["actual"]) for r in rs) / n, 4),
            "mae_candidate": round(sum(abs(r["cand"] - r["actual"]) for r in rs) / n, 4),
            "mae_gain": round(sum(g(r) for r in rs) / n, 4), "mae_gain_ci95": [round(x, 4) for x in boot(rs, g)],
            "bias_base": round(sum(r["base"] - r["actual"] for r in rs) / n, 3),
            "bias_candidate": round(sum(r["cand"] - r["actual"] for r in rs) / n, 3)}


# ------------------------------------------------------------------------------------------------ (2) P1 offered lines
def quote_of(r):
    try:
        q = json.loads(r["quote"]) if r.get("quote") else None
    except (TypeError, ValueError):
        return None
    return q if isinstance(q, dict) else None


def resolve_match(H, by_pd, r, pids):
    """The HLTV match of a forecast: its canonical 'hltv:<id>' when the player played in it; else the HLTV match of one of
    his ids within +-1 day of the start against the forecast's opponent id (or with his own team id). (matchid, pid) / None."""
    mid = r.get("match_id")
    if isinstance(mid, str) and mid.startswith("hltv:") and mid[5:].isdigit():
        m = int(mid[5:])
        mi = H["match_info"].get(m)
        if mi:
            for p in pids:
                if any(p in mp for mp in mi["maps"].values()):
                    return m, p, "hltv id"
        return None
    d0 = ts(r["match_start"]).date().toordinal()
    opp = (r["perf"] or {}).get("hltv_opp_team") or r.get("opp_id_rs")
    team = r.get("team_id_rs")
    # the first level with a hit decides: same day vs the opponent id, then +-1 day vs the opponent id; only when the
    # forecast has NO opponent id, the same day by the player's own team id. (Run 1 also fell back to the own team id when
    # the opponent id was known but unmatched, and +-1 day - that settled ~50 lines on another match of the same team;
    # fixed before the reported run, see RESULT.md.)
    levels = [((d0,), lambda t, o: o == opp), ((d0 - 1, d0 + 1), lambda t, o: o == opp)] if opp else         ([((d0,), lambda t, o: t == team)] if team else [])
    for days, ok in levels:
        hits = {(m, p) for p in pids for dd in days for (m, t, o) in by_pd.get((p, dd), ()) if ok(t, o)}
        if len(hits) == 1:
            m, p = hits.pop()
            return m, p, "lookup"
        if hits:
            return ("ambiguous",)
    return None


def part2(H, T, fc, now):
    by_pd = collections.defaultdict(set)
    for m, mi in H["match_info"].items():
        d = date.fromisoformat(mi["date"]).toordinal()
        for mp in mi["maps"].values():
            for p, (t, o, _k, _h) in mp.items():
                by_pd[(p, d)].add((m, t, o))
    excl = collections.Counter()
    # board provenance is a property of the pick (learning.source_of): a pick whose forecasts carry a PrizePicks / Underdog
    # board quote is a board pick on that book, and every forecast of it is a board forecast (some carry no quote)
    pick_book = {}
    for r in fc:
        tq = (((quote_of(r) or {}).get("anchors") or {}).get("target") or {})
        if tq.get("book") in ("prizepicks", "underdog"):
            pick_book.setdefault(r["pick_id"], set()).add(tq["book"])
    # 1) board forecasts of standard PrizePicks / Underdog HS Maps 1-2 lines (any time), each resolved to its unit
    #    (HLTV match x player); 2) the unit's start = the start on its most recently logged forecast (a rescheduled match
    #    keeps its latest time, as the production unit does); 3) hold-out = units starting from 2026-10-17 00:00 UTC up to
    #    the HLTV pull; 4) the unit's forecast = its latest board forecast logged >= 30 min before that start.
    cands, uexcl = collections.defaultdict(list), {}
    for r in fc:
        if r.get("prop") != "hs" or (r.get("scope") or "12") != "12" or not r.get("match_start") or not r.get("logged_at"):
            continue
        q = quote_of(r)
        tq = ((q or {}).get("anchors") or {}).get("target") or {}
        bs = pick_book.get(r["pick_id"]) or set()
        book = tq.get("book") or (next(iter(bs)) if len(bs) == 1 else None)
        if book not in ("prizepicks", "underdog"):
            excl["forecast: no PrizePicks / Underdog board quote on its pick (member / unquoted line)"] += 1
            continue
        if (r.get("line_type") or "standard") != "standard" or ((q or {}).get("feed_odds_type") or "standard") != "standard"                 or (tq.get("market_type") or "standard") != "standard":
            excl["forecast: not a standard line (goblin / demon / promo)"] += 1
            continue
        if r.get("line") is None or not (0 < r["line"] <= 60) or r.get("p_more") is None:
            excl["forecast: no line / implausible line / no chance"] += 1
            continue
        pids = [int(x) for x in ((r["perf"] or {}).get("hltv_player_ids") or [])]
        res = resolve_match(H, by_pd, r, pids) if pids else None
        if not pids or not res or res[0] == "ambiguous":
            uexcl[r["pick_id"]] = (r["match_start"], "player not identified in HLTV" if not pids else
                                   "no HLTV match found" if not res else "HLTV match ambiguous")
            continue
        m, p, how = res
        cands[(m, p)].append(dict(r, book=book, resolved_by=how))
    units = {}
    for k, fs in cands.items():
        last = max(fs, key=lambda x: (x["logged_at"], x["id"]))
        start = ts(last["match_start"])
        if start < ts(HOLD_FROM_TS) or start > now:
            continue
        ok = [f for f in fs if ts(f["logged_at"]) <= start - timedelta(minutes=30)]
        if not ok:
            excl["unit: no standard board forecast >= 30 min before the start"] += 1
            continue
        f = max(ok, key=lambda x: (x["logged_at"], -x["id"]))
        units[k] = dict(f, unit_start=start.isoformat())
    seen = {r["pick_id"] for fs in cands.values() for r in fs}
    for pk, (st, why) in uexcl.items():
        if pk not in seen and ts(st) >= ts(HOLD_FROM_TS) and ts(st) <= now:
            excl[f"pick in hold-out not resolved to an HLTV unit: {why}"] += 1
    rows = []
    pushes = []
    for (m, p), r in units.items():
        mi = H["match_info"][m]
        act, why = series_actual(mi, p)
        if act is None:
            excl[f"unit not settled in HLTV: {why}"] += 1
            continue
        hs, team_h, opp_h = act
        line = float(r["line"])
        if hs == line:
            excl["unit: push on the board line"] += 1
            pushes.append({"match": m, "player": p, "line": line})
            continue
        pf = r["perf"] or {}
        start = ts(r["match_start"])
        day = max(start.date().toordinal(), ts(r["logged_at"]).date().toordinal())
        lan = None if r.get("tonight") is None else r["tonight"] == "lan"
        team_id = r.get("team_id_rs")
        opp_id = pf.get("hltv_opp_team") or r.get("opp_id_rs")
        c, cwhy = cand(T, pf.get("hltv_player_ids") or [], team_id, opp_id, day, lan)
        pub = float(r["p_more"])
        pc = p_over_np(c[0], c[1], line, C_CM2) if c else pub
        y = int(hs > line)
        rows.append({"match": m, "player": p, "forecast_id": r["id"], "pick_id": r["pick_id"], "book": r["book"], "line": line,
                     "start": r["unit_start"], "logged": r["logged_at"], "actual": hs, "over": y, "p_pub": pub, "p_cand": pc,
                     "eligible": bool(c), "ineligible": cwhy, "version": pf.get("version"), "resolved_by": r["resolved_by"],
                     "lam_pub": r.get("lambda"), "lam_cand": hs_mean(c[0], c[1], C_CM2) if c else r.get("lambda"),
                     "team_match": (team_id == team_h, opp_id == opp_h), "model_version": r.get("model_version")})
    return rows, excl, pushes


def calib(rs, key):
    bands = (("50-55%", .5, .55), ("55-60%", .55, .6), ("60-65%", .6, .65), ("65%+", .65, 1.01))
    out = []
    for lab, lo, hi in bands:
        g = [r for r in rs if lo <= max(r[key], 1 - r[key]) < hi]
        if not g:
            out.append({"band": lab, "lines": 0})
            continue
        won = [(r["over"] if r[key] >= .5 else 1 - r["over"]) for r in g]
        out.append({"band": lab, "lines": len(g), "matches": len({r["match"] for r in g}),
                    "printed": round(sum(max(r[key], 1 - r[key]) for r in g) / len(g), 4), "won": round(sum(won) / len(g), 4),
                    "won_ci95": [round(x, 3) for x in boot([dict(r, w=w) for r, w in zip(g, won)], lambda r: r["w"])]})
    return out


def ll_block(rs):
    if not rs:
        return {"lines": 0}
    n = len(rs)
    g = lambda r: ll(r["p_pub"], r["over"]) - ll(r["p_cand"], r["over"])
    gb = lambda r: (r["p_pub"] - r["over"]) ** 2 - (r["p_cand"] - r["over"]) ** 2
    return {"lines": n, "matches": len({r["match"] for r in rs}), "overs": sum(r["over"] for r in rs),
            "log_loss_published": round(sum(ll(r["p_pub"], r["over"]) for r in rs) / n, 5),
            "log_loss_candidate": round(sum(ll(r["p_cand"], r["over"]) for r in rs) / n, 5),
            "log_loss_gain": round(sum(g(r) for r in rs) / n, 5), "log_loss_gain_ci95": [round(x, 5) for x in boot(rs, g)],
            "brier_gain": round(sum(gb(r) for r in rs) / n, 5), "brier_gain_ci95": [round(x, 5) for x in boot(rs, gb)],
            "mean_p_over_published": round(sum(r["p_pub"] for r in rs) / n, 4), "mean_p_over_candidate": round(sum(r["p_cand"] for r in rs) / n, 4),
            "observed_over": round(sum(r["over"] for r in rs) / n, 4)}


def main(dump_path, fc_path, out_path):
    dump = pickle.load(open(dump_path, "rb"))
    through = date.fromisoformat(dump["through"])
    H = build_hltv(dump)
    T = cm2.build_tables(dump["rows"], built_at=dump["pulled_at"], through=through)
    now = ts(dump["pulled_at"])
    p1rows, p1excl = part1(H, T, through.toordinal())
    fc = json.load(open(fc_path))["rows"]
    p2rows, p2excl, pushes = part2(H, T, fc, now)
    r1 = mae_block(p1rows)
    r2 = ll_block(p2rows)
    passed = bool(r1.get("mae_gain_ci95") and r1["mae_gain_ci95"][0] > 0 and r2.get("log_loss_gain") is not None and r2["log_loss_gain"] >= 0)
    res = {
        "protocol": "research/hs_cm2_2026_10_21/PROTOCOL.md @ e7e5e12", "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hltv_pulled_at": dump["pulled_at"], "holdout": f"matches from {HOLD_FROM_TS} to {dump['pulled_at']}",
        "cm2_tables": {k: T["meta"].get(k) for k in ("tables_sha", "rows", "rows_raw", "excluded_maps", "unknown_env_rows", "n_refits", "refits")},
        "n0": {"pre": len(dump["n0"]["pre"]), "late_in": len(dump["n0"]["late_in"]), "late_counts": dump["n0"]["counts"]},
        "bootstrap": {"resamples": B, "grouping": "match", "seed": SEED, "interval": "percentile 2.5-97.5"},
        "metric1_hltv_wide": dict(r1, excluded=dict(p1excl),
                                  candidate_eligible=sum(r["eligible"] for r in p1rows),
                                  ineligible_reasons=dict(collections.Counter(r["ineligible"] for r in p1rows if not r["eligible"])),
                                  eligible_only=mae_block([r for r in p1rows if r["eligible"]]),
                                  baseline_with_n0_history_only=mae_block([r for r in p1rows if r["n0_maps"] > 0])),
        "metric2_p1": dict(r2, excluded=dict(p2excl), pushes_excluded=len(pushes),
                           candidate_eligible=sum(r["eligible"] for r in p2rows),
                           ineligible_reasons=dict(collections.Counter(r["ineligible"] for r in p2rows if not r["eligible"])),
                           by_book={b: ll_block([r for r in p2rows if r["book"] == b]) for b in ("prizepicks", "underdog")},
                           eligible_only=ll_block([r for r in p2rows if r["eligible"]]),
                           published_version=dict(collections.Counter(r["version"] for r in p2rows)),
                           resolved_by=dict(collections.Counter(r["resolved_by"] for r in p2rows)),
                           identity_vs_hltv_match={"team_id_matches": sum(r["team_match"][0] for r in p2rows),
                                                   "opp_id_matches": sum(r["team_match"][1] for r in p2rows)},
                           calibration={"published": calib(p2rows, "p_pub"), "candidate": calib(p2rows, "p_cand")},
                           point={"lines": len(p2rows), "mae_published_lambda": round(sum(abs(r["lam_pub"] - r["actual"]) for r in p2rows) / max(1, len(p2rows)), 4),
                                  "mae_candidate": round(sum(abs(r["lam_cand"] - r["actual"]) for r in p2rows) / max(1, len(p2rows)), 4)}),
        "decision_rule": "pass only if metric1 mae_gain_ci95 lower bound > 0 AND metric2 log_loss_gain >= 0",
        "decision": "PASS" if passed else "FAIL",
        "decision_parts": {"metric1_interval_above_0": bool(r1.get("mae_gain_ci95") and r1["mae_gain_ci95"][0] > 0),
                           "metric2_point_ge_0": bool(r2.get("log_loss_gain") is not None and r2["log_loss_gain"] >= 0)},
    }
    json.dump(res, open(out_path, "w"), indent=1, default=str)
    rows_path = Path(out_path).with_name("units.json")
    json.dump({"p1_lines": p2rows, "hltv_player_series": p1rows}, open(rows_path, "w"), default=str)
    return res


if __name__ == "__main__":
    r = main(*sys.argv[1:4])
    print(json.dumps({k: r[k] for k in ("decision", "decision_parts")}, indent=1))
    for k in ("metric1_hltv_wide", "metric2_p1"):
        print(k, json.dumps({x: v for x, v in r[k].items() if x not in ("calibration", "by_book", "eligible_only", "baseline_with_n0_history_only")}, default=str)[:1500])
