"""ED-1 step 2 (population b): the real offered P1 lines, their saved projections and the realized components.

usage: python p1_build.py <p1_units.json> <p1_fc.json> <codex_export dir> <series.csv.gz> <out dir>  ->  <out>/p1.csv.gz

- units: the production P1 export (learning p1_units at 2026-10-23T04:29:55Z). Only matches that START BEFORE the research
  cutoff 2026-10-22 00:00 UTC are kept (later ones are the untouched confirmation set; counted, not analysed).
- forecast rows with the exact saved grader inputs (forecasts_page inputs=1; the UT-1 pull, cutoff id 79170, 0 missing).
- realized: HLTV map rows of the unit's match (export), the scope's maps: kills, HS, rounds, regulation rounds, team kills,
  round wins. Settlement check: P1 'actual' vs the HLTV recomputation.
- model components of the SAVED projection: rounds assumption R_hat = 21.7413 x maps for perf-2 / perf-2-bo3; for perf-cm2
  (cm-2 kills) R_hat = 21.7413 x maps x exp(partial effect of cm-2's two rounds-per-map features) - an approximation, cm-2
  has no explicit rounds term. Model share of team kills s_hat = this player's rebuilt projection / the sum of his team's five
  rebuilt projections in the same match (series.csv.gz, population a) - the saved forecast set has no teammate projections.
  HS per kill = the saved hs_per_kill of the model that made the line."""
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import cm2  # noqa: E402

U, FC, EXP, SER, OUT = sys.argv[1:6]
OUT = Path(OUT)
BR = 21.74132969862184
CUT = "2026-10-22T00:00:00+00:00"
NOS = {"1": (1,), "12": (1, 2), "123": (1, 2, 3)}


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


units = json.load(open(U))
fcs = {r["id"]: r for r in json.load(open(FC))["rows"]}
maps = pd.read_csv(Path(EXP) / "maps.csv.gz", dtype={"lan": str})
pl = pd.read_csv(Path(EXP) / "players.csv.gz", usecols=["mapstatsid", "matchid", "playerid", "teamid", "sideid", "k", "hs"])
sc = pd.read_csv(Path(EXP) / "scores.csv.gz")
sc = sc[(sc.sideid == 0) & (sc.mapid > 0)]
msc = {(m, t, mp): s for m, t, mp, s in sc[["matchid", "teamid", "mapid", "score"]].itertuples(index=False)}
pl = pl[pl.sideid == 0].merge(maps[["matchid", "mapstatsid", "number", "mapid"]], on=["matchid", "mapstatsid"])
want = {int(r["match"].split(":")[1]) for r in units["rows"] if r["match"].startswith("hltv:")}
pl = pl[pl.matchid.isin(want)]
M = {}
for (m, no), g in pl.groupby(["matchid", "number"]):
    tt = list(g.teamid.unique())
    if len(tt) != 2:
        continue
    rd = None
    sa, sb = msc.get((m, tt[0], g.mapid.iloc[0])), msc.get((m, tt[1], g.mapid.iloc[0]))
    if sa is not None and sb is not None:
        rd = sa + sb
    M.setdefault(m, {})[no] = {"rounds": rd, "won": {tt[0]: sa, tt[1]: sb}, "n": len(g),
                               "p": {int(p): (int(t), int(k), None if pd.isna(h) else int(h)) for p, t, k, h in g[["playerid", "teamid", "k", "hs"]].itertuples(index=False)},
                               "tk": g.groupby("teamid").k.sum().to_dict()}
ser = pd.read_csv(SER, usecols=["match", "player", "team", "cm2_ok", "lam_cm2", "lam_p2_1"])
ser["lam12"] = np.where(ser.cm2_ok == 1, ser.lam_cm2, 2 * ser.lam_p2_1)
ser["lam1"] = ser.lam_p2_1
tsum = ser.groupby(["match", "team"])[["lam12", "lam1"]].transform("sum")
ser["s12"], ser["s1"] = ser.lam12 / tsum.lam12, ser.lam1 / tsum.lam1
share = {(m, p): (s1, s12) for m, p, s1, s12 in ser[["match", "player", "s1", "s12"]].itertuples(index=False)}
ci = {n: i for i, n in enumerate(cm2.ARTIFACT["feature_names"])}
F = cm2.FIT

out, skipped = [], {"after_cutoff": 0}
for u in units["rows"]:
    if u["start"] >= CUT:
        skipped["after_cutoff"] += 1
        continue
    fc = fcs[u["forecast_id"]]
    inp = fc.get("inputs") or {}
    pf = inp.get("perf") or {}
    scope, prop = u["scope"], inp.get("prop") or u.get("prop")
    nm = len(NOS[scope])
    rec = {"forecast_id": u["forecast_id"], "match": u["match"], "prop": prop, "scope": scope, "line": u["line"], "p_over": u["p"],
           "over": u["over"], "actual": u["actual"], "lam": u["lam"], "version": u["version"], "revision": u.get("revision"), "book": u["book"], "units": u["units"],
           "source": pf.get("version"), "logged": u["logged"], "start": u["start"],
           "lead_min": (ts(u["start"]) - ts(u["logged"])).total_seconds() / 60, "bo": inp.get("bo"),
           "tonight": (inp.get("environment") or {}).get("tonight"), "data_quality": pf.get("data_quality"),
           "hist_rounds": pf.get("history_rounds_weighted"), "sd": pf.get("sd"), "c_kills": pf.get("spread_c_kills"), "lam_kills": pf.get("lambda_kills"), "hsr": pf.get("hs_per_kill"),
           "is_standin": inp.get("is_standin"), "standin_for": bool(inp.get("standin_for")), "roster_confirmed": inp.get("roster_confirmed")}
    q = fc.get("quote") or {}
    q = json.loads(q) if isinstance(q, str) else q
    tq = (q.get("anchors") or {}).get("target") or {}
    upd = tq.get("source_updated_at") or q.get("updated_at")
    if upd:
        rec["quote_age_min"] = (ts(u["logged"]) - ts(upd)).total_seconds() / 60
    # rounds assumption
    rhat = BR * nm
    cl = pf.get("cm2_live") or {}
    if pf.get("version") == "perf-cm2" and cl.get("features"):
        x = cl["features"]
        part = sum(F["coef"][i] * (x[i] - F["mean"][i]) / F["std"][i] for i in (ci["log_own_historical_rounds_per_map"], ci["log_opponent_historical_rounds_per_map"]))
        rhat *= math.exp(part)
        rec["elo_own"], rec["elo_opp"] = cl["inputs"]["elo_own"], cl["inputs"]["elo_opp"]
    rec["R_hat"] = rhat
    ids = pf.get("hltv_player_ids") or []
    if u["match"].startswith("hltv:") and len(ids) >= 1:
        m = int(u["match"].split(":")[1])
        mm = M.get(m, {})
        pid = next((i for i in ids if 1 in mm and i in mm[1]["p"]), None)
        rec["hltv_in_export"] = int(bool(mm))
        rec["player_found"] = int(pid is not None)
        if pid is not None and all(n in mm and pid in mm[n]["p"] and mm[n]["rounds"] for n in NOS[scope]):
            es = [mm[n] for n in NOS[scope]]
            t = es[0]["p"][pid][0]
            o = [x for x in es[0]["won"] if x != t][0]
            hh = [e["p"][pid][2] for e in es]
            rec.update({"K": sum(e["p"][pid][1] for e in es), "HS": sum(hh) if all(h is not None for h in hh) else np.nan,
                        "R": sum(e["rounds"] for e in es), "Rreg": sum(min(e["rounds"], 24) for e in es),
                        "W": sum(e["won"][t] for e in es), "T": sum(e["tk"].get(t, 0) for e in es),
                        "ot": sum(e["rounds"] > 24 for e in es), "complete": int(all(e["n"] == 10 for e in es)),
                        "opp_matches_saved": int(pf.get("hltv_opp_team") == o) if pf.get("hltv_opp_team") else np.nan,
                        "pid": pid, "team": t, "opp": o})
            sh = share.get((m, pid))
            if sh:
                rec["s_hat"] = sh[0] if scope == "1" else sh[1]
    out.append(rec)
df = pd.DataFrame(out)
df.to_csv(OUT / "p1.csv.gz", index=False, compression="gzip")
print(len(df), skipped, df.K.notna().sum(), df.s_hat.notna().sum())
