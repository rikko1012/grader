"""Miss decomposition (FACTOR_AUDIT round 10) - data build. Read-only on local files; no production call.

Inputs : cs2_scout_ops/data/lkb1_1113 (paged picks export ids 7001-27921 + /admin/hltv_export Oct 28 - Nov 13)
         cs2_scout_ops/data/cs2_full/hltv (local HLTV extract to 2026-11-05)
Output : cs2_scout_ops/data/miss_decomp_1113/units.pkl  (one row per player-match: pick units + HLTV history units)

What it does
1. HLTV rows in cm-1 dump format (local extract before 2026-10-28, the export from 2026-10-28) -> cm2.build_tables
   (the live cm-2 tables code, unchanged): opponent refits, Elo at the start of the day, player / team history.
2. Pick units: one per (match_slug, player) with Maps 1-2 kills or HS rows logged before the start: median saved kills and
   HS projection (lambda), expected rounds used (detail.rounds), market team price, map chances (pred_maps).
   Mapped to the HLTV match by exact lower-case player names (6+ of the lobby, unique best match, start day +-1).
3. History units: every complete HLTV best-of-3 lobby (maps 1 and 2 complete) on 2026-06-01 .. 2026-11-13, with cm-2's
   Maps 1-2 kills projection recomputed from the tables (data strictly before the match day).
4. Per unit: actual Maps 1-2 kills / HS / rounds, OT flags, series winner, Elo own / opponent, recent form, role inputs
   (AWP share snapshot, opening attempts), per-map kill-rate ratios.
"""
import gzip
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import cm1  # noqa: E402
import cm2  # noqa: E402

OPS = Path("C:/Users/tikic/cs2_scout_ops/data")
D = OPS / "lkb1_1113"
H = OPS / "cs2_full" / "hltv"
OUT = OPS / "miss_decomp_1113"
OUT.mkdir(exist_ok=True)
SPLIT = date(2026, 10, 28)          # local extract before, export from
HIST0 = date(2026, 6, 1)            # history units from


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ 1. HLTV rows
def local_rows():
    mo = pd.read_csv(H / "match_overview.csv.gz", usecols=["matchid", "date", "lan"])
    mo["date"] = pd.to_datetime(mo["date"]).dt.date
    mo = mo[(mo.date >= cm1.DATA_START) & (mo.date < SPLIT)]
    mm = pd.read_csv(H / "match_maps.csv.gz", usecols=["matchid", "number", "mapid", "mapstatsid"])
    mm = mm[mm.matchid.isin(mo.matchid)]
    sc = pd.read_csv(H / "matches.csv.gz")
    sc = sc[(sc.sideid == 0) & (sc.mapid > 0) & sc.matchid.isin(mo.matchid)][["matchid", "teamid", "mapid", "score"]]
    cols = ["mapstatsid", "matchid", "playerid", "teamid", "sideid", "k", "ok", "od"]
    ps = pd.concat(c[(c.sideid == 0) & c.matchid.isin(mo.matchid)][cols]
                   for c in pd.read_csv(H / "map_player_stats.csv.gz", chunksize=400_000, usecols=cols))
    mh = pd.read_csv(H / "map_headshots.csv.gz", usecols=["mapstatsid", "playerid", "hs"])
    maps = pd.read_csv(H / "maps.csv.gz")
    x = ps.merge(mm, on=["mapstatsid", "matchid"]).merge(mo, on="matchid")
    x = x.merge(sc.rename(columns={"score": "own"}), on=["matchid", "teamid", "mapid"])
    opp = sc.rename(columns={"teamid": "opp", "score": "opp_score"})
    x = x.merge(opp, on=["matchid", "mapid"])
    x = x[x.opp != x.teamid]
    x = x.merge(mh, on=["mapstatsid", "playerid"], how="left")
    x = x.merge(maps.rename(columns={"name": "map"}), on="mapid", how="left")
    x = x.rename(columns={"matchid": "match", "number": "map_no", "playerid": "player", "teamid": "team"})
    return x[["match", "date", "lan", "map_no", "map", "player", "team", "opp", "k", "hs", "own", "opp_score", "ok", "od"]]


def export_rows():
    ex = json.load(gzip.open(D / "hltv_2026-10-28_2026-11-13.json.gz", "rt"))
    e = pd.DataFrame(ex["rows"], columns=ex["cols"])
    e["date"] = pd.to_datetime(e["date"]).dt.date
    e = e[e.date >= SPLIT]
    e["ok"] = np.nan
    e["od"] = np.nan
    return e[["match", "date", "lan", "map_no", "map", "player", "team", "opp", "k", "hs", "own", "opp_score", "ok", "od"]], ex


log("reading HLTV ...")
L = local_rows()
E, EX = export_rows()
allr = pd.concat([L, E], ignore_index=True)
allr["map"] = allr["map"].astype(str).str.lower().str.replace(" ", "").str.replace("de_", "")
log("rows local", len(L), "export", len(E))

rows = [(int(r.match), str(r.date), None if pd.isna(r.lan) else bool(r.lan), int(r.map_no), int(r.player), int(r.team), int(r.opp),
         int(r.k) if not pd.isna(r.k) else None, int(r.hs) if not pd.isna(r.hs) else None,
         int(r.own) if not pd.isna(r.own) else None, int(r.opp_score) if not pd.isna(r.opp_score) else None)
        for r in allr.itertuples(index=False)]
rows.sort(key=lambda r: (r[1], r[0], r[3]))
log("building cm-2 tables ...")
T = cm2.build_tables(rows, built_at="2026-11-13T00:00:00+00:00", through=date(2026, 11, 13))
log("tables", {k: T["meta"][k] for k in ("rows", "full_maps", "players", "teams", "n_refits", "tables_sha")})

# complete maps (cm-2 rule) -> per map and per match frames
clean, _ = cm2.clean_rows(rows)
cm = pd.DataFrame(clean, columns=["match", "date", "lan", "map_no", "player", "team", "opp", "k", "hs", "own", "opp_score"])
cm["date"] = pd.to_datetime(cm["date"]).dt.date
cm["rounds"] = cm["own"] + cm["opp_score"]
extra = allr[["match", "map_no", "player", "map", "ok", "od"]].drop_duplicates(["match", "map_no", "player"])
cm = cm.merge(extra, on=["match", "map_no", "player"], how="left")
log("complete player-map rows", len(cm))

names = {}
pl = pd.read_csv(H / "players.csv.gz", encoding="utf-8")
for r in pl.itertuples():
    names[int(r.playerid)] = str(r.name).lower()
for k, v in EX["players"].items():
    names[int(k)] = str(v).lower()
cm["pname"] = cm["player"].map(names)

# ------------------------------------------------------------------ per-match summary (maps 1 and 2)
mm12 = cm[cm.map_no.isin([1, 2])]
g = mm12.groupby(["match", "map_no"]).agg(rounds=("rounds", "first"), date=("date", "first"), lan=("lan", "first"))
g = g.reset_index()
ms = g.pivot(index="match", columns="map_no", values="rounds")
ms = ms.dropna()
ms.columns = ["r1", "r2"]
ms["R12"] = ms.r1 + ms.r2
ms["ot1"] = (ms.r1 > 24).astype(int)
ms["ot2"] = (ms.r2 > 24).astype(int)
ms["ot"] = ((ms.r1 > 24) | (ms.r2 > 24)).astype(int)
# series winner by map wins over all complete maps of the match
mw = cm.groupby(["match", "map_no", "team"]).agg(own=("own", "first"), opp=("opp_score", "first")).reset_index()
mw["won"] = (mw.own > mw.opp).astype(int)
sw = mw.groupby(["match", "team"]).won.sum().reset_index()
winner = {}
for m, gg in sw.groupby("match"):
    gg = gg.sort_values("won")
    if len(gg) == 2 and gg.won.iloc[1] > gg.won.iloc[0]:
        winner[m] = int(gg.team.iloc[1])
nmaps = cm.groupby("match").map_no.nunique()
mdate = cm.groupby("match").date.first()
mlan = cm.groupby("match").lan.first()
log("matches with maps 1-2 complete", len(ms))

# ------------------------------------------------------------------ helpers on tables
A = T["arrays"]


def elo_of(tid, day):
    e = cm1._block(A, "e_ids", "e_s", "e_e", "e_rows", int(tid))
    if e is None:
        return 1500.0
    i = np.searchsorted(e[:, 0], day, "left")
    return float(e[i - 1, 1]) if i > 0 else 1500.0


# player-map history frame for form / maps / openings (complete maps only)
cm["dord"] = cm["date"].map(lambda d: d.toordinal())
hist = {p: g_.sort_values(["dord", "match", "map_no"])[["dord", "k", "rounds", "map", "ok", "od", "hs"]].to_numpy()
        for p, g_ in cm.groupby("player")}

awp = pd.read_csv(H / "player_awp.csv.gz")
AWP = {int(r.playerid): (float(r.awp_share) if not pd.isna(r.awp_share) else None, int(r.kills or 0)) for r in awp.itertuples()}


def player_inputs(pid, team, opp, day, lan):
    """cm-2 projection (recomputed), Elo, form, openings, per-map rates; all from data strictly before `day`."""
    s = cm1.inputs(T, [pid], team, opp, day, lan)
    out = {"elo_own": elo_of(team, day), "elo_opp": elo_of(opp, day)}
    if not s.get("ineligible") and s.get("maps"):
        x = cm1.features(s)
        if all(math.isfinite(v) for v in x):
            F = cm2.FIT
            eta = F["intercept"] + sum(b * (v - m) / sd for b, v, m, sd in zip(F["coef"], x, F["mean"], F["std"]))
            out["lam_cm2_recon"] = math.exp(eta)
            out["rate_all"] = math.exp(x[0])
            out["hs_ratio"] = cm1.hs_ratio(s)
    h = hist.get(pid)
    if h is not None:
        h = h[h[:, 0] < day]
        h = h[h[:, 0] >= day - 420]
        out["hist_maps"] = len(h)
        if len(h):
            last = h[-10:]
            out["k10"], out["r10"] = float(last[:, 1].sum()), float(last[:, 2].sum())
            h180 = h[h[:, 0] >= day - 180]
            okd = h180[~pd.isna(h180[:, 4].astype(float))]
            out["open_att"] = float((okd[:, 4] + okd[:, 5]).sum()) if len(okd) else 0.0
            out["open_r"] = float(okd[:, 2].sum()) if len(okd) else 0.0
            h365 = h[h[:, 0] >= day - 365]
            pm = defaultdict(lambda: [0.0, 0.0])
            for row in h365:
                pm[row[3]][0] += row[1]
                pm[row[3]][1] += row[2]
            out["per_map"] = {k: (v[0], v[1]) for k, v in pm.items()}
            out["k365"] = float(h365[:, 1].sum())
            out["r365"] = float(h365[:, 2].sum())
    a = AWP.get(pid)
    if a:
        out["awp_share"], out["awp_kills"] = a
    return out


# ------------------------------------------------------------------ 2. pick units
log("pick units ...")
picks = [json.loads(l) for l in gzip.open(D / "picks_7000.jsonl.gz", "rt")]
pre = [r for r in picks if r.get("match_start") and r.get("lambda") is not None and ts(r["logged_at"]) < ts(r["match_start"])
       and r["scope"] == "12" and r["prop"] in ("kills", "hs")]
U = defaultdict(lambda: {"lk": [], "lh": [], "ptm": [], "pwin": [], "rounds": [], "pred_maps": None, "pm_at": None,
                         "ak": None, "ah": None, "src": set()})
for r in pre:
    u = U[(r["match_slug"], r["player_id"])]
    u.update(match_slug=r["match_slug"], player_id=r["player_id"], player=r["player"], team=r["team"], opponent=r["opponent"],
             match_start=r["match_start"])
    (u["lk"] if r["prop"] == "kills" else u["lh"]).append(float(r["lambda"]))
    fa = {}
    try:
        fa = json.loads(r.get("factors") or "{}")
    except ValueError:
        pass
    if fa.get("p_team_market") is not None:
        u["ptm"].append(float(fa["p_team_market"]))
    if r.get("p_win") is not None:
        u["pwin"].append(float(r["p_win"]))
    rd = (fa.get("detail") or {}).get("rounds")
    if rd:
        u["rounds"].append(float(rd))
    if r.get("pred_maps") and (u["pm_at"] is None or r["logged_at"] > u["pm_at"]):
        u["pred_maps"], u["pm_at"] = r["pred_maps"], r["logged_at"]
    if r["status"] == "settled" and r.get("actual") is not None:
        if r["prop"] == "kills":
            u["ak"] = float(r["actual"])
        else:
            u["ah"] = float(r["actual"])
    fl = r.get("flags") or ""
    if r["prop"] == "kills":
        u["src"].add("perf-cm2" if "perf-cm2" in fl else "other")

# HLTV match per slug by exact lower-case names
slug_players = defaultdict(lambda: defaultdict(set))
slug_day = {}
for (slug, pid), u in U.items():
    slug_players[slug][u["team"]].add(str(u["player"]).lower())
    slug_day[slug] = ts(u["match_start"]).date()
cm_by_day = {d: g_ for d, g_ in cm.groupby("date")}


def map_slug(slug):
    """slug 'hltv-<id>' -> that HLTV match; else the unique HLTV match (start day +-1) with the most exact lower-case
    name hits, at least 3 (and at least 6 when both teams have 3+ players with rows)."""
    d0 = slug_day[slug]
    parts = [cm_by_day[d] for d in (d0 - timedelta(days=1), d0, d0 + timedelta(days=1)) if d in cm_by_day]
    if not parts:
        return None
    c = pd.concat(parts)
    allp = set().union(*slug_players[slug].values())
    if slug.startswith("hltv-") and slug[5:].isdigit() and int(slug[5:]) in set(c.match):
        mid = int(slug[5:])
    else:
        hits = c[c.pname.isin(allp)].groupby("match")["pname"].nunique()
        need = 6 if sum(1 for v in slug_players[slug].values() if len(v) >= 3) == 2 else 3
        if hits.empty or hits.max() < need or (hits == hits.max()).sum() > 1:
            return None
        mid = int(hits.idxmax())
    g_ = c[c.match == mid]
    team_tid = {}
    for t, ps_ in slug_players[slug].items():
        v = g_[g_.pname.isin(ps_)]["team"].value_counts()
        team_tid[t] = int(v.idxmax()) if not v.empty else None
    pid_of = g_.drop_duplicates("pname").set_index("pname")["player"].to_dict()
    tids = set(g_.team.unique())
    return {"hltv": mid, "team_tid": team_tid, "pid_of": pid_of, "tids": tids}


slug_map = {s: map_slug(s) for s in slug_players}
log("slugs", len(slug_map), "mapped", sum(1 for v in slug_map.values() if v))

# name -> HLTV id for unmapped (future) slugs: unique exact name among players active in the last 120 days
recent = cm[cm.date >= date(2026, 7, 15)]
name_ids = recent.groupby("pname")["player"].unique()
last_team = recent.sort_values("dord").groupby("player")["team"].last().to_dict()

pu = []
for (slug, pid), u in U.items():
    day = slug_day[slug]
    rec = {"kind": "pick", "match_slug": slug, "player_id": pid, "player": u["player"], "team": u["team"], "opponent": u["opponent"],
           "match_start": u["match_start"], "day": day, "lam_k": float(np.median(u["lk"])) if u["lk"] else None,
           "lam_h": float(np.median(u["lh"])) if u["lh"] else None, "p_team_market": float(np.median(u["ptm"])) if u["ptm"] else None,
           "p_win": float(np.median(u["pwin"])) if u["pwin"] else None, "rounds_used": float(np.median(u["rounds"])) if u["rounds"] else 44.0,
           "pred_maps": u["pred_maps"], "act_k": u["ak"], "act_h": u["ah"], "kills_src": ",".join(sorted(u["src"]))}
    sm = slug_map.get(slug)
    hpid = tid = oid = None
    if sm:
        rec["hltv"] = sm["hltv"]
        hpid = sm["pid_of"].get(str(u["player"]).lower())
        tid = sm["team_tid"].get(u["team"])
        oth = [t for t in sm["tids"] if t != tid] if tid is not None else []
        oid = int(oth[0]) if len(oth) == 1 else None
    else:
        ids = name_ids.get(str(u["player"]).lower())
        if ids is not None and len(ids) == 1:
            hpid = int(ids[0])
            tid = last_team.get(hpid)
    rec.update(hltv_pid=hpid, team_tid=tid, opp_tid=oid)
    pu.append(rec)
P = pd.DataFrame(pu)
# opponent tid for unmapped slugs: the other team's identified players' last team
for slug, g_ in P[P.opp_tid.isna()].groupby("match_slug"):
    tids = g_.groupby("team")["team_tid"].agg(lambda s: s.dropna().mode().iloc[0] if s.notna().any() else None).to_dict()
    for i in g_.index:
        o = [v for k, v in tids.items() if k != P.at[i, "team"]]
        P.at[i, "opp_tid"] = o[0] if o else None
        if P.at[i, "team_tid"] is None or pd.isna(P.at[i, "team_tid"]):
            P.at[i, "team_tid"] = tids.get(P.at[i, "team"])
log("pick units", len(P), "with hltv pid", P.hltv_pid.notna().sum(), "team+opp tid", (P.team_tid.notna() & P.opp_tid.notna()).sum())

# ------------------------------------------------------------------ 3. history units (all complete BO3 lobbies)
log("history units ...")
bo3 = set(nmaps[nmaps.isin([2, 3])].index) & set(ms.index)
hu = []
c12 = mm12[mm12.match.isin(bo3) & (mm12.date >= HIST0)]
agg = c12.groupby(["match", "player", "team", "opp"]).agg(act_k=("k", "sum"), act_h=("hs", "sum"), date=("date", "first"),
                                                          lan=("lan", "first"), hs_na=("hs", lambda s: s.isna().any())).reset_index()
for r in agg.itertuples(index=False):
    hu.append({"kind": "hist", "hltv": int(r.match), "hltv_pid": int(r.player), "team_tid": int(r.team), "opp_tid": int(r.opp),
               "day": r.date, "lan": r.lan, "act_k": float(r.act_k), "act_h": None if r.hs_na else float(r.act_h)})
HU = pd.DataFrame(hu)
log("history units", len(HU), "matches", HU.hltv.nunique())

# ------------------------------------------------------------------ 4. inputs for every unit
def enrich(df, lan_col=None):
    out = []
    for r in df.itertuples(index=False):
        rr = r._asdict()
        if pd.isna(rr.get("hltv_pid")) or pd.isna(rr.get("team_tid")) or pd.isna(rr.get("opp_tid")):
            out.append({})
            continue
        day = rr["day"].toordinal()
        lan = rr.get("lan")
        if lan is None or (isinstance(lan, float) and math.isnan(lan)):
            lan = mlan.get(rr.get("hltv")) if rr.get("hltv") is not None and not pd.isna(rr.get("hltv")) else None
        if lan is None or (isinstance(lan, float) and math.isnan(lan)):
            lan_b = None
        else:
            lan_b = bool(lan)
        x = player_inputs(int(rr["hltv_pid"]), int(rr["team_tid"]), int(rr["opp_tid"]), day, lan_b if lan_b is not None else False)
        x["lan_known"] = lan_b is not None
        out.append(x)
    return pd.concat([df.reset_index(drop=True), pd.DataFrame(out)], axis=1)


P = enrich(P)
log("pick enriched")
HU = enrich(HU)
log("hist enriched")


def match_info(df):
    m = df["hltv"]
    for c in ("R12", "r1", "r2", "ot", "ot1", "ot2"):
        df[c] = m.map(lambda v, c=c: ms[c].get(v) if v is not None and not pd.isna(v) and v in ms.index else None)
    df["winner_tid"] = m.map(lambda v: winner.get(v) if v is not None and not pd.isna(v) else None)
    return df


P = match_info(P)
HU = match_info(HU)
P.to_pickle(OUT / "units_pick.pkl")
HU.to_pickle(OUT / "units_hist.pkl")
json.dump({"tables_meta": T["meta"], "pick_units": len(P), "hist_units": len(HU)}, open(OUT / "build_meta.json", "w"), default=str, indent=1)
log("saved", OUT)
