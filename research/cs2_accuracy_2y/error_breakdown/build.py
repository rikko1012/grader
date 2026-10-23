"""ED-1 step 1 (population a): rebuild the live projections on the two-year HLTV export, pre-match only, and attach the
realized components of every player-series x scope.

usage: python build.py <codex_export dir> <out dir>      ->  <out>/series.csv.gz, <out>/build_meta.json

Projections (the live code, not a re-implementation):
- cm-2 kills Maps 1-2: cm2.clean_rows + cm1.build_tables(env=cm2.env) on the export rows (same tuple layout as cm1.SQL), then
  cm1.inputs / cm1.features and the cm-2 artifact (cm2_model.json) exactly as cm2.projection. hs-cm2 HS Maps 1-2 = cm-2 lambda x
  cm1.hs_ratio. APPROXIMATIONS (documented in ERROR_BREAKDOWN.md): history = the whole export (2024-10-23 on) instead of
  2025-09-01 on; opponent refits from the first Monday with >= 1,000 rows (cm1.FIRST_REFIT moved from 2025-10-27 to 2024-11-04);
  Elo starts 2024-10-23 instead of 2025-09-01; HS from map_player_stats.hs (live: map_headshots.hs); cm-2 coefficients were fit
  through 2026-09-30 on overlapping data, so the mean model is IN-SAMPLE here.
- perf-2 (Map 1 / Maps 1-3 live, Maps 1-2 fallback): perf.py PARAMS formula evaluated on the same window sums (the cm-1 sums are
  the perf-2 sums: hl 120, window 420, k0 / ke / ko / k_hs priors, unknown env = overall only). APPROXIMATION: full history, not
  the N0 pin (N0 = a 2026 match-id list that does not exist for the 2-year window) - i.e. this is fh-1-style perf-2.
Realized (HLTV map rows, the scope's maps): kills, HS, rounds, regulation rounds (sum of min(rounds, 24)), overtime rounds, team
rounds won, team kills, opponent kills. Pre-match features: everything strictly before the match day."""
import json
import math
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
import cm1  # noqa: E402
import cm2  # noqa: E402
import perf  # noqa: E402

EXP, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
SCORE_FROM = date(2025, 1, 1)
CUTOFF = date(2026, 10, 22)          # research cutoff: matches on/after it are the untouched confirmation set
P = perf.PARAMS
HL, WIN = 120.0, 420
BASE = date(2024, 10, 1).toordinal()
t0 = time.time()

maps = pd.read_csv(EXP / "maps.csv.gz", dtype={"lan": str})
maps = maps[pd.to_datetime(maps.date).dt.date < CUTOFF]
scores = pd.read_csv(EXP / "scores.csv.gz")
pl = pd.read_csv(EXP / "players.csv.gz", usecols=["mapstatsid", "matchid", "playerid", "teamid", "sideid", "k", "hs"])
pl = pl[pl.sideid == 0].merge(maps[["matchid", "mapstatsid", "number", "mapid", "date", "lan", "forfeited_maps"]], on=["matchid", "mapstatsid"])
sc = scores[scores.sideid == 0]
ser = sc[sc.mapid == 0].groupby("matchid").score.agg(["max", "sum"])
# series format (pre-match fact). A BO1's mapid-0 row holds the ROUND score (13-x), not a map count - never derive the
# format from it as 2*max-1 (that leaked BO1 overtime into the feature in the first run; fixed before any result was used)
fmt = {m: (1 if mx >= 4 or (mx == 1 and sm == 1) else 2 if mx == 1 else 2 * mx - 1) for m, (mx, sm) in ser.iterrows()}
msc = {(m, t, mp): s for m, t, mp, s in sc[sc.mapid > 0][["matchid", "teamid", "mapid", "score"]].itertuples(index=False)}

# ---- cm1.SQL-shaped rows: (matchid, date, lan, map_no, player, team, opp, k, hs, own, opp_score)
teams_on_map = pl.groupby(["matchid", "number"]).teamid.unique().to_dict()
rows = []
for r in pl.itertuples(index=False):
    tt = teams_on_map[(r.matchid, r.number)]
    if len(tt) != 2:
        continue
    opp = int(tt[0] if tt[1] == r.teamid else tt[1])
    own_s, opp_s = msc.get((r.matchid, r.teamid, r.mapid)), msc.get((r.matchid, opp, r.mapid))
    lan = None if not isinstance(r.lan, str) else r.lan == "t"
    rows.append((int(r.matchid), str(r.date), lan, int(r.number), int(r.playerid), int(r.teamid), opp, int(r.k),
                 None if pd.isna(r.hs) else int(r.hs), None if own_s is None else int(own_s), None if opp_s is None else int(opp_s)))
rows.sort(key=lambda x: (x[1], x[0], x[3]))
print("rows", len(rows), round(time.time() - t0), flush=True)

cm1.FIRST_REFIT = date(2024, 11, 4)   # first Monday after two weeks of export history (refits need >= 1,000 rows anyway)
T = cm2.build_tables(rows, built_at="2026-10-22T00:00:00+00:00", through=CUTOFF)
print("tables", T["meta"]["tables_sha"], T["meta"]["rows"], T["meta"]["excluded_maps"], round(time.time() - t0), flush=True)
clean, _ = cm2.clean_rows(rows)

# ---- map-level realized table from the CLEAN maps (complete scoreboards)
mp = defaultdict(list)
for r in clean:
    mp[(r[0], r[3])].append(r)
maprec = {}      # (match, map_no) -> dict
for (m, no), v in mp.items():
    rd = v[0][9] + v[0][10]
    tk = defaultdict(int)
    for r in v:
        tk[r[5]] += r[7]
    maprec[(m, no)] = {"day": date.fromisoformat(v[0][1][:10]).toordinal(), "lan": v[0][2], "rounds": rd,
                       "won": {r[5]: r[9] for r in v}, "tk": dict(tk),
                       "p": {r[4]: (r[5], r[7], r[8]) for r in v}}
forfeit = set(maps.loc[maps.forfeited_maps > 0, "matchid"])


class WSum:
    """Exponentially weighted (half-life-style e-folding HL days) sums over [D - WIN, D) per entity, vectorized."""
    def __init__(self, ent, day, X):
        ent, day, X = np.asarray(ent, np.int64), np.asarray(day, np.int64), np.asarray(X, float).reshape(len(ent), -1)
        self.key = ent * 100000 + (day - BASE)
        o = np.argsort(self.key, kind="stable")
        self.key, day, X = self.key[o], day[o], X[o]
        w = np.exp((day - BASE) / HL)
        self.cs = np.vstack([np.zeros((1, X.shape[1])), np.cumsum(X * w[:, None], 0)])
        self.cw = np.concatenate([[0.0], np.cumsum(w)])
        self.day = day

    def q(self, ent, D):
        ent, D = np.asarray(ent, np.int64), np.asarray(D, np.int64)
        lo = np.searchsorted(self.key, ent * 100000 + (D - WIN - BASE), "left")
        hi = np.searchsorted(self.key, ent * 100000 + (D - BASE), "left")
        f = np.exp(-(D - BASE) / HL)
        last = np.where(hi > lo, self.day[np.maximum(hi - 1, 0)], -1)
        return (self.cs[hi] - self.cs[lo]) * f[:, None], (self.cw[hi] - self.cw[lo]) * f, hi - lo, last


# team-map rows: rounds, won, ot, team kills, opp kills
tm = []
for (m, no), e in maprec.items():
    ts = list(e["won"])
    for t in ts:
        o = ts[0] if ts[1] == t else ts[1]
        tm.append((t, e["day"], e["rounds"], e["won"][t], float(e["rounds"] > 24), e["tk"][t], e["tk"][o]))
tm = np.array(tm, float)
WT = WSum(tm[:, 0], tm[:, 1], tm[:, 2:])
# player-map rows: kills, team kills, rounds; and player x team pair rows
pm_, pt_ = [], []
for (m, no), e in maprec.items():
    for p, (t, k, hs) in e["p"].items():
        pm_.append((p, e["day"], k, e["tk"][t], e["rounds"]))
        pt_.append((p * 20000 + t, e["day"], 1.0))
pm_ = np.array(pm_, float)
WP = WSum(pm_[:, 0], pm_[:, 1], pm_[:, 2:])
pt_ = np.array(pt_, float)
WPT = WSum(pt_[:, 0], pt_[:, 1], pt_[:, 2:])

# lineups per (team, match) from map 1 (or the lowest map) and the previous match strictly before the day
lineup = {}
for (m, no), e in sorted(maprec.items()):
    for p, (t, k, hs) in e["p"].items():
        lineup.setdefault((t, m), {"day": e["day"], "players": set()})
        if lineup[(t, m)]["day"] == e["day"]:
            lineup[(t, m)]["players"].add(p)
by_team = defaultdict(list)
for (t, m), v in lineup.items():
    by_team[t].append((v["day"], m, frozenset(v["players"])))
cont = {}
for t, lst in by_team.items():
    lst.sort()
    days = [x[0] for x in lst]
    for i, (d, m, s) in enumerate(lst):
        j = np.searchsorted(days, d, "left") - 1
        cont[(t, m)] = (len(s & lst[j][2]) / max(len(s), 1), d - lst[j][0]) if j >= 0 else (np.nan, np.nan)

# ---- targets: every player of every clean map-1 series in the scoring window
series = defaultdict(dict)
for (m, no), e in maprec.items():
    series[m][no] = e
out = []
nproj = 0
for m, mm in series.items():
    if 1 not in mm or m in forfeit:
        continue
    e1 = mm[1]
    D = e1["day"]
    if date.fromordinal(D) < SCORE_FROM:
        continue
    lan = e1["lan"]
    teams = list(e1["won"])
    for p, (t, k1, hs1) in e1["p"].items():
        o = teams[0] if teams[1] == t else teams[1]
        s = cm1.inputs(T, [p], t, o, D, bool(lan) if lan is not None else False)
        nproj += 1
        if s.get("ineligible"):
            continue
        x = cm1.features(s)
        eta = cm2.FIT["intercept"] + sum(b * (v - mu) / sd for b, v, mu, sd in zip(cm2.FIT["coef"], x, cm2.FIT["mean"], cm2.FIT["std"]))
        cm2_ok = lan is not None and s["maps"] > 0 and all(math.isfinite(v) for v in x)
        rate_all = (s["k"] + P["k0"] * P["pop"] * P["prior_m"]) / (s["r"] + P["k0"])
        rate = (s["ke"] + P["ke"] * rate_all) / (s["re"] + P["ke"]) if lan is not None else rate_all
        oppf = ((s["ck"] + P["ko"] * P["pop"] * 5) / (s["cr"] * 5 + P["ko"] * 5)) / P["pop"]
        hsr = (s["hk"] + P["k_hs"] * P["pop_hs"]) / (s["hkk"] + P["k_hs"])
        rec = {"match": m, "day": D, "player": p, "team": t, "opp": o, "lan": np.nan if lan is None else float(lan), "bo": fmt.get(m, np.nan),
               "maps_played": len(mm), "cm2_ok": int(cm2_ok), "lam_cm2": math.exp(eta) if cm2_ok else np.nan,
               "p2_rate": rate, "p2_rate_all": rate_all, "p2_opp": oppf, "hsr": hsr, "lam_p2_1": rate * oppf * P["base_r"],
               "elo_own": s["elo_own"], "elo_opp": s["elo_opp"], "r_w": s["r"], "re_w": s["re"], "nmaps_hist": s["maps"], "dup": s["dup"] or np.nan}
        for i, v in enumerate(x):
            rec[f"x{i}"] = v
        # realized per scope
        for sc_, nos in (("1", (1,)), ("12", (1, 2)), ("123", (1, 2, 3))):
            if all(n in mm and p in mm[n]["p"] for n in nos):
                es = [mm[n] for n in nos]
                kk = sum(e["p"][p][1] for e in es)
                hh = [e["p"][p][2] for e in es]
                hok = all(h is not None and not (h == 0 and e["p"][p][1] >= 12) for h, e in zip(hh, es))
                R = sum(e["rounds"] for e in es)
                rec.update({f"K_{sc_}": kk, f"HS_{sc_}": sum(hh) if hok else np.nan, f"R_{sc_}": R,
                            f"Rreg_{sc_}": sum(min(e["rounds"], 24) for e in es), f"W_{sc_}": sum(e["won"][t] for e in es),
                            f"T_{sc_}": sum(e["tk"][t] for e in es), f"O_{sc_}": sum(e["tk"][o] for e in es),
                            f"ot_{sc_}": sum(e["rounds"] > 24 for e in es)})
        out.append(rec)
    if len(out) % 20000 < 10:
        print(len(out), round(time.time() - t0), flush=True)
df = pd.DataFrame(out)
print("series rows", len(df), "projected", nproj, round(time.time() - t0), flush=True)

# ---- pre-match team / player history features (strictly before the match day)
D = df.day.values
for side, col in (("tm", "team"), ("op", "opp")):
    S, W_, N, last = WT.q(df[col].values, D)
    wr = S[:, 0]
    df[f"{side}_rpm"] = (S[:, 0] + 21.74 * 8) / (W_ + 8)                           # rounds per map (8 map prior)
    df[f"{side}_rws"] = (S[:, 1] + 0.5 * 100) / (S[:, 0] + 100)                  # round-win share (100 rounds prior)
    df[f"{side}_ot"] = (S[:, 2] + 0.12 * 10) / (W_ + 10)                         # OT map rate (10 map prior)
    df[f"{side}_tkpr"] = (S[:, 3] + 0.665 * 5 * 100) / (S[:, 0] + 100)           # team kills per round
    df[f"{side}_okpr"] = (S[:, 4] + 0.665 * 5 * 100) / (S[:, 0] + 100)           # kills conceded per round
    df[f"{side}_wmaps"] = W_
    df[f"{side}_nmaps"] = N
    df[f"{side}_rest"] = np.where(last >= 0, D - last, np.nan)
S, W_, N, last = WP.q(df.player.values, D)
df["pl_share"] = (S[:, 0] + 0.2 * 50) / (S[:, 1] + 50)                             # share of team kills (50 team-kill prior)
df["pl_rest"] = np.where(last >= 0, D - last, np.nan)
S2, W2, N2, _ = WPT.q(df.player.values * 20000 + df.team.values, D)
df["pl_team_frac"] = np.where(W_ > 0, W2 / np.maximum(W_, 1e-9), np.nan)          # share of his recent maps with this team
df["pl_team_maps"] = N2
cc = [cont.get((t, m), (np.nan, np.nan)) for t, m in zip(df.team.values, df.match.values)]
df["roster_cont"] = [c[0] for c in cc]
df["team_gap_days"] = [c[1] for c in cc]
co = [cont.get((t, m), (np.nan, np.nan)) for t, m in zip(df.opp.values, df.match.values)]
df["opp_roster_cont"] = [c[0] for c in co]
df.to_csv(OUT / "series.csv.gz", index=False, compression="gzip")
meta = {"tables": {k: v for k, v in T["meta"].items() if k not in ("refits",)}, "rows_export": len(rows), "series_rows": len(df),
        "matches": int(df.match.nunique()), "score_from": str(SCORE_FROM), "cutoff": str(CUTOFF), "first_refit": "2024-11-04",
        "seconds": round(time.time() - t0)}
json.dump(meta, open(OUT / "build_meta.json", "w"), indent=1, default=str)
print(json.dumps(meta, default=str))
