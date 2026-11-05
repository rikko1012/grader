"""Full-history CS2 player-series dataset with PRE-MATCH features only (research, never imported by the live grader).

Source: the local read-only HLTV extract (CS2_FULL_DIR, default C:/Users/tikic/cs2_scout_ops/data/cs2_full):
work/player_map.csv.gz (634,687 player-maps, 2024-01-09 .. 2026-11-05) and work/team_match_ctx.csv.gz (walk-forward Elo,
rest days, lineup changes). One row per (match, player) with the targets

  k1 / hs1     Map 1 (map number 1)
  k12 / hs12   Maps 1-2 (the player played maps 1 and 2)
  k123 / hs123 Maps 1-3 (the player played maps 1, 2 and 3 - books void a Maps 1-3 prop when map 3 is not played)

LEAKAGE RULE: every feature of a row on day D is read from state built ONLY from maps dated strictly before D (all rows of
day D are featurised first, then day D's maps update the state). The match's own maps never enter its own features.
History uses complete scoreboards only (10 rows, 5 per team, unique players) - the live rule. Snapshot tables that are not
dated per match (AWP share, Liquipedia roles) are NOT used: they would leak later information into 2024-2025 rows."""
from __future__ import annotations

import math
import os
from collections import defaultdict

import numpy as np
import pandas as pd

DATA = os.environ.get("CS2_FULL_DIR", r"C:/Users/tikic/cs2_scout_ops/data/cs2_full")
OUT = os.path.join(DATA, "proj_full")

HLS = (20.0, 90.0, 365.0)          # player half-lives (days)
POP_KPR = 0.66442 * 0.93           # perf-2 newcomer prior
POP_HS = 0.5106
# per-map player stat vector (decayed sums)
PV = ("maps", "rounds", "k", "hk", "hh", "a", "d", "adr_r", "kast_r", "ok", "od", "mk", "rating", "team_k", "clutch", "won",
      "swing")
IX = {n: i for i, n in enumerate(PV)}
TV = ("maps", "rounds", "conc_k", "team_k", "won", "rw", "ot")   # team state
TIX = {n: i for i, n in enumerate(TV)}


class Decay:
    """Exponentially decayed sums per key, read at any later day. read(day) uses only updates made before the call."""

    def __init__(self, hls, n):
        self.hl = np.asarray(hls, float)[:, None]
        self.n = n
        self.s = {}

    def read(self, key, day):
        v = self.s.get(key)
        if v is None:
            return None
        last, S = v
        return S * np.exp(-(day - last) / self.hl)

    def add(self, key, day, x):
        v = self.s.get(key)
        x = np.asarray(x, float)
        if v is None:
            self.s[key] = (day, np.tile(x, (len(self.hl), 1)))
            return
        last, S = v
        self.s[key] = (day, S * np.exp(-(day - last) / self.hl) + x)


def shrink(num, den, prior, k):
    return (num + k * prior) / (den + k)


def load_maps():
    pm = pd.read_csv(os.path.join(DATA, "work", "player_map.csv.gz"), low_memory=False)
    pm["day"] = pd.to_datetime(pm["date"]).map(lambda d: d.toordinal())
    pm["lan"] = pm["lan"].map(lambda v: None if pd.isna(v) else (str(v) in ("True", "true", "1", "1.0")))
    g = pm.groupby("mapstatsid")
    n = g["playerid"].transform("size")
    nu = g["playerid"].transform("nunique")
    nt = pm.groupby(["mapstatsid", "teamid"])["playerid"].transform("size")
    pm["complete"] = (n == 10) & (nu == 10) & (nt == 5) & (pm.rounds > 0) & (pm.k >= 0)
    pm["hs_ok"] = ~((pm.hs == 0) & (pm.k >= 12)) & pm.hs.notna()
    pm["team_k"] = pm.groupby(["mapstatsid", "teamid"])["k"].transform("sum")
    pm["conc_k"] = pm.groupby(["mapstatsid"])["k"].transform("sum") - pm["team_k"]
    return pm


def series_targets(pm):
    """(matchid, playerid) rows with Map 1 / Maps 1-2 / Maps 1-3 totals. A scope's total exists only if the player played
    every map of the scope; hs*_ok is False if any map of the scope has a missing / implausible HS count."""
    x = pm[pm.number.isin([1, 2, 3])]
    piv = x.pivot_table(index=["matchid", "playerid"], columns="number", values=["k", "hs", "hs_ok"], aggfunc="first")
    piv.columns = [f"{a}{int(b)}" for a, b in piv.columns]
    piv = piv.reset_index()
    for c in ("k1", "k2", "k3", "hs1", "hs2", "hs3"):
        if c not in piv:
            piv[c] = np.nan
    for c in ("hs_ok1", "hs_ok2", "hs_ok3"):
        piv[c] = piv.get(c, pd.Series(False, index=piv.index)).fillna(False).astype(bool)
    out = piv[["matchid", "playerid"]].copy()
    out["k1"] = piv.k1
    out["hs1"] = piv.hs1.where(piv.hs_ok1)
    out["k12"] = piv.k1 + piv.k2
    out["hs12"] = (piv.hs1 + piv.hs2).where(piv.hs_ok1 & piv.hs_ok2)
    out["k123"] = piv.k1 + piv.k2 + piv.k3
    out["hs123"] = (piv.hs1 + piv.hs2 + piv.hs3).where(piv.hs_ok1 & piv.hs_ok2 & piv.hs_ok3)
    meta = (pm.sort_values("number").groupby(["matchid", "playerid"])
            .agg(teamid=("teamid", "first"), oppid=("oppid", "first"), day=("day", "first"), date=("date", "first"),
                 lan=("lan", "first"), bo=("bo", "first"), maps_played=("maps_played", "first"), event=("event", "first"),
                 team=("team", "first"), opponent=("opponent", "first")).reset_index())
    out = out.merge(meta, on=["matchid", "playerid"], how="left")
    return out[out.k1.notna() | out.k12.notna()].reset_index(drop=True)


def build(pm=None, verbose=True):
    pm = load_maps() if pm is None else pm
    S = series_targets(pm)
    ctx = pd.read_csv(os.path.join(DATA, "work", "team_match_ctx.csv.gz"),
                      usecols=["matchid", "teamid", "elo_pre", "elo_n_pre", "opp_elo_pre", "opp_elo_n_pre", "rest_days_pre",
                               "lineup_changes_vs_prev", "players_new_60d"])
    ctx = ctx.drop_duplicates(["matchid", "teamid"])
    S = S.merge(ctx, on=["matchid", "teamid"], how="left")
    S = S.merge(ctx[["matchid", "teamid", "rest_days_pre", "players_new_60d"]].rename(
        columns={"teamid": "oppid", "rest_days_pre": "opp_rest_days_pre", "players_new_60d": "opp_players_new_60d"}),
        on=["matchid", "oppid"], how="left")
    S = S.sort_values(["day", "matchid", "playerid"]).reset_index(drop=True)

    P = Decay(HLS, len(PV))
    PE = Decay((120.0,), 2)              # (player, lan flag): rounds, k
    PM = Decay((180.0,), 2)              # (player, mapid): rounds, k
    PT = Decay((60.0,), 1)               # (player, team): maps
    T = Decay((90.0,), len(TV))
    TP = Decay((180.0,), 2)              # (team, mapid): picks, plays
    G = Decay((180.0,), 4)               # mapid: plays, rounds, kills, picks
    last_day = {}

    hist = pm[pm.complete].copy()
    hist["kast_r"] = hist.kast.fillna(70) / 100 * hist.rounds
    hist["adr_r"] = hist.adr.fillna(75) * hist.rounds
    hist_by_day = {d: g for d, g in hist.groupby("day")}
    rows_by_day = {d: g.index.values for d, g in S.groupby("day")}
    days = sorted(set(hist_by_day) | set(rows_by_day))
    feats = {}
    for d in days:
        idx = rows_by_day.get(d)
        if idx is not None:
            # global map shares (active pool) once per day
            gm = {m: G.read(m, d)[0] for m in list(G.s)}
            plays_tot = sum(v[0] for v in gm.values()) or 1.0
            picks_tot = sum(v[3] for v in gm.values()) or 1.0
            pool = {m: v for m, v in gm.items() if v[0] / plays_tot > 0.02}
            gpick = {m: (v[3] + 1.0) / (picks_tot + len(pool)) for m, v in pool.items()}
            gs = sum(gpick.values()) or 1.0
            gpick = {m: v / gs for m, v in gpick.items()}
            gkpr = {m: (v[2] / max(v[1] * 10, 1)) for m, v in pool.items()}
            grounds = {m: v[1] / max(v[0], 1e-9) for m, v in pool.items()}
            for i in idx:
                r = S.loc[i]
                feats[i] = _row_features(r, d, P, PE, PM, PT, T, TP, last_day, gpick, gkpr, grounds)
        g = hist_by_day.get(d)
        if g is None:
            continue
        for rec in g.itertuples(index=False):
            p, t = rec.playerid, rec.teamid
            P.add(p, d, [1, rec.rounds, rec.k, rec.k if rec.hs_ok else 0, rec.hs if rec.hs_ok else 0, rec.a, rec.d,
                         rec.adr_r, rec.kast_r, rec.ok, rec.od, rec.mk, rec.rating if not pd.isna(rec.rating) else 1.0,
                         rec.team_k, rec.clutches, rec.map_won if not pd.isna(rec.map_won) else 0,
                         rec.swing if not pd.isna(rec.swing) else 0.0])
            if rec.lan is not None:
                PE.add((p, bool(rec.lan)), d, [rec.rounds, rec.k])
            PM.add((p, rec.mapid), d, [rec.rounds, rec.k])
            PT.add((p, t), d, [1])
            last_day[p] = d
        for (ms, t), tg in g.groupby(["mapstatsid", "teamid"]):
            r0 = tg.iloc[0]
            T.add(t, d, [1, r0.rounds, r0.conc_k, r0.team_k, r0.map_won if not pd.isna(r0.map_won) else 0,
                         r0.team_rounds, 1 if r0.rounds > 30 else 0])
            TP.add((t, r0.mapid), d, [1 if r0.picked_by == t else 0, 1])
        for ms, mg in g.groupby("mapstatsid"):
            r0 = mg.iloc[0]
            G.add(r0.mapid, d, [1, r0.rounds, mg.k.sum(), 1 if (not pd.isna(r0.picked_by) and r0.picked_by != 0) else 0])
        if verbose and d % 60 == 0:
            print("day", pd.Timestamp.fromordinal(d).date(), "rows", len(feats), flush=True)
    F = pd.DataFrame.from_dict(feats, orient="index").sort_index()
    out = pd.concat([S, F], axis=1)
    out["elo_diff"] = out.elo_pre - out.opp_elo_pre
    out["elo_mean"] = (out.elo_pre + out.opp_elo_pre) / 2
    out["lan_f"] = out.lan.map(lambda v: np.nan if v is None or (isinstance(v, float) and math.isnan(v)) else float(bool(v)))
    ev = out.event.fillna("").str.lower()
    out["ev_qual"] = ev.str.contains("qualifier|qual\\.|open ").astype(float)
    out["ev_academy"] = ev.str.contains("academy|youth|junior|rising").astype(float)
    out["ev_major"] = ev.str.contains("major|iem|blast|esl pro league|pgl").astype(float)
    return out


def _row_features(r, d, P, PE, PM, PT, T, TP, last_day, gpick, gkpr, grounds):
    p, t, o = r.playerid, r.teamid, r.oppid
    f = {}
    for h, hl in enumerate(HLS):
        v = P.read(p, d)
        v = v[h] if v is not None else np.zeros(len(PV))
        R = v[IX["rounds"]]
        mp = v[IX["maps"]]
        tag = f"_{int(hl)}"
        f["r" + tag] = R
        f["maps" + tag] = mp
        f["kpr" + tag] = shrink(v[IX["k"]], R, POP_KPR, 150.0)
        if h == 1:
            f["hsk" + tag] = shrink(v[IX["hh"]], v[IX["hk"]], POP_HS, 25.0)
            f["apr" + tag] = shrink(v[IX["a"]], R, 0.13, 150.0)
            f["dpr" + tag] = shrink(v[IX["d"]], R, 0.68, 150.0)
            f["adr" + tag] = shrink(v[IX["adr_r"]], R, 75.0, 150.0)
            f["kast" + tag] = shrink(v[IX["kast_r"]], R, 0.70, 150.0)
            f["okpr" + tag] = shrink(v[IX["ok"]], R, 0.10, 150.0)
            f["odpr" + tag] = shrink(v[IX["od"]], R, 0.10, 150.0)
            f["mkpr" + tag] = shrink(v[IX["mk"]], R, 0.08, 150.0)
            f["rating" + tag] = shrink(v[IX["rating"]], mp, 1.0, 5.0)
            f["swing" + tag] = shrink(v[IX["swing"]], mp, 0.0, 5.0)
            f["kshare" + tag] = shrink(v[IX["k"]], v[IX["team_k"]], 0.2, 100.0)
            f["clutch" + tag] = shrink(v[IX["clutch"]], R, 0.01, 150.0)
            f["mapwin" + tag] = shrink(v[IX["won"]], mp, 0.5, 5.0)
            f["hs_hist_k" + tag] = v[IX["hk"]]
    kpr = f["kpr_90"]
    lan = r.lan
    if lan is not None and not (isinstance(lan, float) and math.isnan(lan)):
        e = PE.read((p, bool(lan)), d)
        er, ek = (e[0][0], e[0][1]) if e is not None else (0.0, 0.0)
        f["kpr_env"] = shrink(ek, er, f["kpr_90"], 400.0)
        f["r_env"] = er
    else:
        f["kpr_env"] = kpr
        f["r_env"] = np.nan
    f["days_since"] = (d - last_day[p]) if p in last_day else np.nan
    pt = PT.read((p, t), d)
    f["maps_with_team_60"] = pt[0][0] if pt is not None else 0.0
    for side, tid in (("t", t), ("o", o)):
        v = T.read(tid, d)
        v = v[0] if v is not None else np.zeros(len(TV))
        mp = v[TIX["maps"]]
        f[f"{side}_maps"] = mp
        f[f"{side}_rpm"] = shrink(v[TIX["rounds"]], mp, 21.7, 5.0)
        f[f"{side}_conc_kpr"] = shrink(v[TIX["conc_k"]], v[TIX["rounds"]] * 5, POP_KPR / 0.93, 300.0)
        f[f"{side}_kpr"] = shrink(v[TIX["team_k"]], v[TIX["rounds"]] * 5, POP_KPR / 0.93, 300.0)
        f[f"{side}_mapwin"] = shrink(v[TIX["won"]], mp, 0.5, 5.0)
        f[f"{side}_rshare"] = shrink(v[TIX["rw"]], v[TIX["rounds"]], 0.5, 50.0)
        f[f"{side}_ot"] = shrink(v[TIX["ot"]], mp, 0.08, 10.0)
    # expected maps: each team's (decayed) picks, filled with the global pick share; Map 1 / Maps 1-2 = both teams' mix
    if gpick:
        dist = {}
        for tid in (t, o):
            raw = {}
            for m in gpick:
                x = TP.read((tid, m), d)
                raw[m] = (x[0][0] if x is not None else 0.0) + 1.5 * gpick[m]
            s = sum(raw.values())
            for m in raw:
                dist[m] = dist.get(m, 0.0) + 0.5 * raw[m] / s
        ratio = rk = rr = tw = 0.0
        for m, pmp in dist.items():
            x = PM.read((p, m), d)
            rm, km = (x[0][0], x[0][1]) if x is not None else (0.0, 0.0)
            ratio += pmp * shrink(km, rm, kpr, 200.0) / kpr
            rk += pmp * gkpr.get(m, 0.0)
            rr += pmp * grounds.get(m, 21.7)
            tp = TP.read((t, m), d)
            op = TP.read((o, m), d)
            tw += pmp * ((tp[0][1] if tp is not None else 0.0) - (op[0][1] if op is not None else 0.0))
        f["map_kpr_ratio"] = ratio
        f["map_glob_kpr"] = rk
        f["map_glob_rounds"] = rr
        f["map_play_edge"] = tw
        f["map_entropy"] = -sum(v * math.log(v) for v in dist.values() if v > 0)
    else:
        f["map_kpr_ratio"] = 1.0
        f["map_glob_kpr"] = f["map_glob_rounds"] = f["map_play_edge"] = f["map_entropy"] = np.nan
    f["proj_kpm"] = f["kpr_env"] * (f["o_conc_kpr"] / (POP_KPR / 0.93)) * 21.74   # perf-2-like kills per map
    return f


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    df = build()
    df.to_pickle(os.path.join(OUT, "series_features.pkl"))
    print("rows", len(df), "cols", df.shape[1])
