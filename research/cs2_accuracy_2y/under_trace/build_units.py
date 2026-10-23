"""UT-1: one record per P1 unit with every step of its projection, its line, its settled actual and HLTV context.

Inputs (read-only pulls in the scratch dir): p1_units.json (pull_p1.py), p1_fc.json (pull_fc.py: the forecast row + saved grader
inputs of each unit's forecast), hltv_dump_<day>.pkl (research/hs_cm2_2026_10_21/data.py: cm1.SQL rows since 2025-09-01 + N0 sets).
Nothing is fitted. usage: python build_units.py <scratch dir>  ->  <scratch>/units.json

Decomposition of the published projection (perf-2 family; kills, the scope's maps m, R = rounds_per_map x m):
  raw     = his own time-weighted kills / rounds (no prior)            -> L_raw   = raw x R
  prior   = rate_all (raw shrunk toward 0.93 x pop by k0=300 rounds)   -> d_prior = (rate_all - raw) x R
  env     = rate (tonight's LAN/online sample blended in, ke=800)      -> d_env   = (rate - rate_all) x R
  opp     = rate x opponent factor                                      -> d_opp   = rate x (opp - 1) x R
  cm-2    = cm-2 kills replaces perf-2 kills (perf-cm2 lines)          -> d_cm2
  HS      = every kills step x his HS-per-kill; cm-2 HS lines use cm-2's own HS per kill (d_cm2 includes that change);
            d_pmf = published HS mean (beta-binomial pmf) - lambda_kills x hs_per_kill.
raw is recovered exactly from the saved numbers: k = rate_all (r + k0) - k0 pop prior_m, raw = k / r (r = weighted rounds).
"""
import collections
import json
import math
import pickle
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import perf  # noqa: E402

P = perf.PARAMS
PERF_VERSIONS = ("perf-2", "perf-2-bo3", "perf-1", "perf-1-bo3", "perf-cm2")   # grader.CONFIG perf_versions
SCOPES = {"1": (1,), "12": (1, 2), "123": (1, 2, 3)}


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(timezone.utc)


def hltv_index(dump):
    """match -> {map_no: {player: (team, opp, k, hs, rounds)}}, date, lan; player-map rows for perf-2 replays (N0 flag kept)."""
    rows, n0 = dump["rows"], dump["n0"]
    in_n0 = lambda m: m in n0["pre"] or m in n0["late_in"]
    maps = collections.defaultdict(list)
    for r in rows:
        maps[(r[0], r[3])].append(r)
    mi, pmaps, omaps = {}, collections.defaultdict(list), collections.defaultdict(list)
    for (m, no), v in maps.items():
        e = mi.setdefault(m, {"date": str(v[0][1])[:10], "lan": v[0][2], "maps": {}})
        e["maps"][no] = {r[4]: (r[5], r[6], r[7], r[8], (r[9] or 0) + (r[10] or 0) if r[9] is not None and r[10] is not None else None) for r in v}
        day = date.fromisoformat(str(v[0][1])[:10]).toordinal()
        n0f = in_n0(m)
        # perf_live._PLAYER_SQL rule: 10 rows / 10 distinct players on the map, kills and both scores present
        if len(v) == 10 and len({r[4] for r in v}) == 10:
            for r in v:
                if r[7] is None or r[9] is None or r[10] is None:
                    continue
                pmaps[r[4]].append((day, r[7], r[9] + r[10], r[8], None if r[2] is None else bool(r[2]), not (r[8] == 0 and r[7] >= 12), n0f))
        # perf_live._OPP_SQL rule: the 5 opponents' kills (all present, >= 0), positive rounds
        for t in {r[5] for r in v}:
            o = [r for r in v if r[5] != t]
            own = [r for r in v if r[5] == t]
            if (len(o) == 5 and len({r[4] for r in o}) == 5 and all(r[7] is not None and r[7] >= 0 for r in o) and own
                    and all(r[9] is not None and r[10] is not None and r[9] >= 0 and r[10] >= 0 for r in o)
                    and own[0][9] + own[0][10] > 0):
                omaps[t].append((day, sum(r[7] for r in o), own[0][9] + own[0][10], n0f))
    return mi, pmaps, omaps


def replay_perf2(pmaps, omaps, pids, opp, lan, day, scope, prop, n0_only):
    since = day - 420
    pm = [x[:6] for p in pids for x in pmaps.get(int(p), []) if since <= x[0] < day and (x[6] or not n0_only)]
    om = [x[:3] for x in omaps.get(int(opp), []) if since <= x[0] < day and (x[3] or not n0_only)] if opp else []
    if not pm:
        return None
    return perf.project(pm, om, lan, day, scope, prop)


def actual_from_hltv(mi, m, pids, scope):
    """(kills, hs, rounds, team) over the scope's maps when he played every one of them in HLTV, else None."""
    e = mi.get(m)
    if not e:
        return None
    k = h = rr = 0
    team = None
    for no in SCOPES[scope]:
        mp = e["maps"].get(no)
        if not mp:
            return None
        hit = [p for p in pids if p in mp]
        if not hit:
            return None
        t, o, kk, hh, rounds = mp[hit[0]]
        if kk is None or rounds is None:
            return None
        k += kk
        h = None if (h is None or hh is None) else h + hh
        rr += rounds
        team = t
    return {"kills": k, "hs": h, "rounds": rr, "team": team}


def decompose(pf, src, prop, scope, lam_pub):
    """The step decomposition of one perf-family projection (module docstring); {} for legacy."""
    rec = {}
    m = int(pf.get("maps") or perf.MAPS[scope])
    R = float(pf.get("rounds_per_map") or P["base_r"]) * m
    p2 = pf.get("perf2") if src == "perf-cm2" and isinstance(pf.get("perf2"), dict) else pf
    ra, rt, opp = float(pf["rate_all_envs"]), float(pf["rate"]), float(pf["opp_factor"])
    r = float(pf.get("history_rounds_weighted") or 0.0)
    pm_ = P.get("prior_m", 1.0) if not str(p2.get("version", "")).startswith("perf-1") else 1.0
    k = ra * (r + P["k0"]) - P["k0"] * P["pop"] * pm_
    raw = k / r if r > 0 else None
    hs = prop == "hs"
    hsr2 = float(p2.get("hs_per_kill") or 0) if hs else 1.0
    lam_p2k = rt * opp * R
    x = (lambda v: v * hsr2)
    L_raw = x((raw if raw is not None else ra) * R)
    d_prior = x((ra - raw) * R) if raw is not None else 0.0
    d_env, d_opp = x((rt - ra) * R), x(rt * (opp - 1) * R)
    perf2_lam = lam_p2k * hsr2
    if src == "perf-cm2":
        cm = pf.get("cm2_live") or {}
        lam_ck = float(cm.get("lambda") or pf["lambda_kills"])
        hsr_c = float(pf.get("hs_per_kill") or 0) if hs else 1.0
        d_cm2 = lam_ck * hsr_c - perf2_lam
        model_lam = lam_ck * hsr_c
        rec.update(cm2_features=cm.get("features"), cm2_inputs=cm.get("inputs"), cm2_lambda_kills=lam_ck, cm2_hsr=hsr_c if hs else None)
    else:
        d_cm2, model_lam = 0.0, perf2_lam
    d_pmf = float(lam_pub) - model_lam            # HS: pmf mean vs lam_k x hsr; kills: rounding of lambda_final only
    rec.update(R=R, maps=m, raw_rate=raw, rate_all=ra, rate=rt, opp_factor=opp, hist_rounds=r, env_rounds=float(pf.get("env_rounds_weighted") or 0),
               opp_rounds=float(pf.get("opp_rounds_weighted") or 0), hsr_p2=hsr2 if hs else None,
               perf2_lambda=perf2_lam, perf2_lambda_logged=p2.get("lambda"), model_lambda=model_lam,
               L_raw=L_raw, d_prior=d_prior, d_env=d_env, d_opp=d_opp, d_cm2=d_cm2, d_pmf=d_pmf,
               pop_baseline=P["pop"] * R * hsr2, hltv_player_ids=pf.get("hltv_player_ids"), hltv_opp_team=pf.get("hltv_opp_team"))
    return rec


def main(scr):
    scr = Path(scr)
    p1 = json.load(open(scr / "p1_units.json"))
    fc = {r["id"]: r for r in json.load(open(scr / "p1_fc.json"))["rows"]}
    dump = pickle.load(open(sorted(scr.glob("hltv_dump_*.pkl"))[-1], "rb"))
    mi, pmaps, omaps = hltv_index(dump)
    out, miss = [], collections.Counter()
    for u in p1["rows"]:
        f = fc.get(u["forecast_id"])
        if f is None:
            miss["forecast row not fetched"] += 1
            continue
        i = f.get("inputs") or {}
        pf = i.get("perf") if isinstance(i.get("perf"), dict) else {}
        scope, prop = u["scope"] or "12", u["prop"]
        src = pf.get("version") if (pf.get("lambda") is not None and pf.get("sd") and pf.get("version") in PERF_VERSIONS) else "legacy"
        line, p = float(u["line"]), float(u["p"])
        rec = {k: u[k] for k in ("match", "p", "over", "lam", "actual", "line", "prop", "scope", "version", "revision", "units",
                                 "forecast_id", "logged", "start", "book")}
        rec.update(source=src, side="Over" if p > 0.5 else "Under" if p < 0.5 else "none", conf=max(p, 1 - p),
                   pick=f.get("pick"), grade=f.get("grade"), has_inputs=bool(i), line_type=i.get("line_type"), bo=i.get("bo"),
                   tonight=(i.get("environment") or {}).get("tonight"), data_quality=pf.get("data_quality"),
                   perf_error=pf.get("error"), probs_raw=f.get("probs_raw"), data_rev=f.get("data_rev"),
                   recent_avg=i.get("recent_avg"), recent_median=i.get("recent_median"), maps_played=i.get("maps_played"),
                   p_team_market=i.get("p_team_market"), market_quality=i.get("market_quality"), opp_rank=i.get("opp_rank"),
                   player_maps=pf.get("player_maps"), opp_maps=pf.get("opp_maps"), cm2_live_inelig=(pf.get("cm2_live") or {}).get("ineligible"))
        try:
            sh = json.loads(f.get("shadow") or "{}")
        except ValueError:
            sh = {}
        rec["legacy_p_more"] = (sh.get("legacy") or {}).get("p_more")
        rec["legacy_lambda"] = (sh.get("legacy") or {}).get("lambda")
        rec["prior_smooth_lambda"] = (sh.get("prior_smooth") or {}).get("lambda")
        mr = sh.get("market_rounds") or {}
        rec["rr1_rounds_sum"] = mr.get("rounds_sum")
        if src != "legacy":
            rec.update(decompose(pf, src, prop, scope, u["lam"]))
            # N0 vs full-history replay of perf-2 (HLTV ids only; perf-2-bo3 rows are BO3.gg history - not replayed)
            pids = [int(v) for v in (pf.get("hltv_player_ids") or [])]
            if pids and src in ("perf-2", "perf-cm2"):
                day = max(ts(u["start"]).date().toordinal(), ts(u["logged"]).date().toordinal())
                lan = None if rec["tonight"] is None else rec["tonight"] == "lan"
                a = replay_perf2(pmaps, omaps, pids, pf.get("hltv_opp_team"), lan, day, scope, prop, True)
                b = replay_perf2(pmaps, omaps, pids, pf.get("hltv_opp_team"), lan, day, scope, prop, False)
                rec.update(replay_n0=a and a["lambda"], replay_full=b and b["lambda"],
                           replay_full_hist_rounds=b and b["history_rounds_weighted"], replay_n0_hist_rounds=a and a["history_rounds_weighted"],
                           replay_full_rate=b and b["rate"], replay_full_opp=b and b["opp_factor"])
        # HLTV actuals (rounds actually played over the scope's maps) for canonical HLTV matches
        mid = u["match"]
        if isinstance(mid, str) and mid.startswith("hltv:") and mid[5:].isdigit():
            pids = [int(v) for v in ((pf or {}).get("hltv_player_ids") or [])]
            a = actual_from_hltv(mi, int(mid[5:]), pids, scope) if pids else None
            if a:
                rec.update(hltv_kills=a["kills"], hltv_hs=a["hs"], hltv_rounds=a["rounds"])
        out.append(rec)
    json.dump({"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "p1_at": p1.get("at"), "hltv_pulled_at": dump["pulled_at"],
               "p1_excluded": p1.get("excluded"), "p1_pushes": p1.get("pushes"), "missing": dict(miss), "units": out},
              open(scr / "units.json", "w"), default=str)
    print("units", len(out), dict(miss), collections.Counter(r["source"] for r in out))


if __name__ == "__main__":
    main(sys.argv[1])
