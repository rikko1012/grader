"""rm-1 / th-1 on the real offered P1 lines (start < 2026-10-22 00:00 UTC), with the F6 fit (data < 2026-10-01).

usage: python run_p1.py <scratch cs2acc dir>
  reads  <S>/ed1/p1.csv.gz (ED-1 p1_build), <S>/under/p1_fc.json (saved forecast inputs), <S>/ed1/series.csv.gz (tiers),
         <S>/cands/params_F6.json
  writes <S>/cands/p1_cands.csv.gz
Live = the LOGGED p_over; a replay from the saved inputs is stored beside it (p_replay)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib  # noqa: E402

S = Path(sys.argv[1])
P = json.load(open(S / "cands/params_F6.json"))
pmf = {int(k): v for k, v in P["pi"]["pmf"].items()}
M = {"1": 1, "12": 2, "123": 3}
p = pd.read_csv(S / "ed1/p1.csv.gz")
p["scope"] = p.scope.astype(str)
fc = {r["id"]: r for r in json.load(open(S / "under/p1_fc.json"))["rows"]}
cm2r, inel = [], []
for fid in p.forecast_id:
    pf = ((fc.get(fid) or {}).get("inputs") or {}).get("perf") or {}
    cl = pf.get("cm2_live") or {}
    cm2r.append(((cl.get("inputs") or {}).get("r")) if isinstance(cl, dict) else None)
    inel.append(bool(isinstance(cl, dict) and cl.get("ineligible")))
p["cm2_r"] = pd.to_numeric(pd.Series(cm2r), errors="coerce")
p["cm2_inel"] = inel
p["depth"] = np.where((p.source == "perf-cm2") & p.cm2_r.notna(), p.cm2_r, p.hist_rounds)
p["band"] = np.where(p.depth < 50, "D0", np.where(p.depth < 150, "D1", "deep"))
fam = np.where(p.source == "perf-cm2", "cm2", "p2")
p["rho_cell"] = np.where(fam == "cm2", "12_cm2", p.scope + "_p2")
p["th_cell"] = np.where((p.scope == "12") & (p.source == "perf-2") & p.cm2_inel, "F",
                        np.where(p.band == "deep", "",
                                 np.where(fam == "cm2", "cm2_" + p.band, "p2_" + p.scope + "_" + p.band)))
# tier: match mean Elo fifths on the HLTV population's match-level Elo
ser = pd.read_csv(S / "ed1/series.csv.gz", usecols=["match", "elo_own", "elo_opp"])
me = ser.assign(e=(ser.elo_own + ser.elo_opp) / 2).groupby("match").e.mean()
cuts = np.quantile(me.values, [0.2, 0.4, 0.6, 0.8])
mid = p.match.map(lambda s: int(s.split(":")[1]) if str(s).startswith("hltv:") else None)
p["elo_m"] = mid.map(me)
p["tier"] = np.where(p.elo_m.isna(), "unknown", (np.searchsorted(cuts, p.elo_m.fillna(0)) + 1).astype(str))
p["tier"] = np.where(p.elo_m.isna(), "unknown", p.tier)

rows = []
for (prop, sc), g in p.groupby(["prop", "scope"]):
    g = g.copy()
    sup, w = lib.pi_scope(pmf, M[sc])
    rho = g.rho_cell.map(lambda k: P["rho"][k]["rho"]).values
    a = g.th_cell.map(lambda k: P["th"][k]["a"] if k else 0.0).values
    s_ = g.th_cell.map(lambda k: P["th"][k]["s"] if k else 0.0).values
    line = g.line.values.astype(float)
    if prop == "kills":
        lam = g.lam.values
        c = (g.sd / np.sqrt(np.maximum(g.lam, 1))).values
        g["p_replay"] = lib.p_over_kills(line, lam, c)
        g["p_rm1"] = lib.p_over_kills(line, lam, c, rho, sup, w)
        g["p_th1"] = lib.p_over_kills(line, lam * np.exp(a), c * np.exp(s_))
    else:
        lk, ck, h = g.lam_kills.values, g.c_kills.values, g.hsr.values
        y = np.nan_to_num(g.actual.values, nan=0).astype(float)
        for nm, pk in (("replay", lib.kills_pmf_grid(lk, ck)), ("rm1", lib.kills_pmf_grid(lk, ck, rho, sup, w)),
                       ("th1", lib.kills_pmf_grid(lk * np.exp(a), ck * np.exp(s_)))):
            g[f"p_{nm}"] = lib.hs_stats(pk, h, y, line)["p_over"]
    g["th_in"] = g.th_cell != ""
    g["p_th1"] = np.where(g.th_in, g.p_th1, g.p_over)       # outside the cohort th-1 = live by construction
    rows.append(g)
out = pd.concat(rows).sort_values("forecast_id")
out.to_csv(S / "cands/p1_cands.csv.gz", index=False, compression="gzip")
print(len(out), out.match.nunique(), "replay |gap| mean %.4f max %.4f" % ((out.p_replay - out.p_over).abs().mean(), (out.p_replay - out.p_over).abs().max()),
      "th-1 cohort", int(out.th_in.sum()), out[out.th_in].th_cell.value_counts().to_dict())
