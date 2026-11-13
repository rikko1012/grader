"""lkb-1 lobby kill budget - walk-forward scoring exactly as frozen in FROZEN_DEFINITIONS.md (commit 2e22af6).
Read-only on local files: cs2_scout_ops/data/lkb1_1113 (paged picks export + one hltv_export) and cs2_scout_ops/data/cs2_full/hltv.
usage: python score.py  -> prints the report tables and writes score.json next to the data."""
import gzip
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from statistics import NormalDist

OPS = "C:/Users/tikic/cs2_scout_ops/data"
D = f"{OPS}/lkb1_1113"
H = f"{OPS}/cs2_full/hltv"
FIT0, FIT1 = date(2026, 10, 21), date(2026, 10, 31)
TEST0, TEST1 = date(2026, 11, 1), date(2026, 11, 13)
T1_END = date(2026, 11, 7)
ALPHAS = [round(0.1 * i, 1) for i in range(11)]
ND = NormalDist()
MOTIVATING = None   # filled below: the five MOUZ / Vitality BLAST matches of Nov 11-13 (review section 3b)


def ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


# ---------------------------------------------------------------- picks (saved first-grade forecasts)
rows = [json.loads(l) for l in gzip.open(f"{D}/picks_7000.jsonl.gz", "rt")]
pre = [r for r in rows if r.get("match_start") and r.get("lambda") and ts(r["logged_at"]) < ts(r["match_start"])]
for r in pre:
    r["day"] = ts(r["match_start"]).date()

# lobby projections: median kills lambda per (slug, scope, team, player) over all pre-start rows (board + card)
lob = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
for r in pre:
    if r["prop"] == "kills" and r["scope"] in ("12", "1"):
        lob[(r["match_slug"], r["scope"])][r["team"]][r["player_id"]].append(float(r["lambda"]))
lobby = {}
for key, teams in lob.items():
    if len(teams) != 2:
        continue
    med = {t: {p: float(np.median(v)) for p, v in pl.items()} for t, pl in teams.items()}
    if all(len(v) >= 4 for v in med.values()):
        lobby[key] = {"T": sum(5 * np.mean(list(v.values())) for v in med.values()), "players": med}

# ---------------------------------------------------------------- HLTV normal (complete maps only)
def hltv_maps():
    ex = json.load(gzip.open(f"{D}/hltv_2026-10-28_2026-11-13.json.gz", "rt"))
    e = pd.DataFrame(ex["rows"], columns=ex["cols"])
    e["date"] = pd.to_datetime(e["date"]).dt.date
    e["rounds"] = e["own"] + e["opp_score"]
    ge = e.groupby(["match", "map_no"])
    me = pd.DataFrame({"date": ge["date"].first(), "k": ge["k"].sum(), "n": ge["player"].nunique(),
                       "nt": ge["team"].nunique(), "rounds": ge["rounds"].first(), "rmin": ge["rounds"].min(),
                       "rmax": ge["rounds"].max(), "tmax": ge["team"].agg(lambda s: s.value_counts().max())}).reset_index()
    me = me[(me.n == 10) & (me.nt == 2) & (me.tmax == 5) & (me.rmin == me.rmax) & (me.rounds > 0)]
    ov = pd.read_csv(f"{H}/match_overview.csv.gz", usecols=["matchid", "date"])
    ov["date"] = pd.to_datetime(ov["date"]).dt.date
    ov = ov[(ov.date >= FIT0 - timedelta(days=91)) & (ov.date < date(2026, 10, 28))]
    mm = pd.read_csv(f"{H}/match_maps.csv.gz", usecols=["matchid", "number", "mapid", "mapstatsid"])
    mm = mm[mm.matchid.isin(ov.matchid)]
    sc = pd.read_csv(f"{H}/matches.csv.gz")
    sc = sc[(sc.sideid == 0) & (sc.mapid > 0) & sc.matchid.isin(ov.matchid)]
    rd = sc.groupby(["matchid", "mapid"]).agg(rounds=("score", "sum"), nt_sc=("teamid", "nunique")).reset_index()
    ps = pd.concat(c[(c.sideid == 0) & c.matchid.isin(ov.matchid)][["mapstatsid", "matchid", "playerid", "teamid", "k"]]
                   for c in pd.read_csv(f"{H}/map_player_stats.csv.gz", chunksize=400_000,
                                        usecols=["mapstatsid", "matchid", "playerid", "teamid", "sideid", "k"]))
    g = ps.groupby("mapstatsid")
    mp = pd.DataFrame({"k": g["k"].sum(), "n": g["playerid"].nunique(), "nt": g["teamid"].nunique(),
                       "tmax": g["teamid"].agg(lambda s: s.value_counts().max()), "kmin": g["k"].min()}).reset_index()
    mp = mp.merge(mm, on="mapstatsid").merge(rd[rd.nt_sc == 2], on=["matchid", "mapid"]).merge(ov, on="matchid")
    mp = mp[(mp.n == 10) & (mp.nt == 2) & (mp.tmax == 5) & (mp.rounds > 0) & (mp.kmin >= 0)]
    mp = mp[~mp.matchid.isin(set(me["match"]))]
    allm = pd.concat([mp[["date", "k", "rounds"]], me[["date", "k", "rounds"]]])
    return allm, e


allm, ex = hltv_maps()
allm["kpr"] = allm["k"] / allm["rounds"]
_norm_cache = {}


def normal(day):
    if day not in _norm_cache:
        w = allm[(allm.date >= day - timedelta(days=90)) & (allm.date <= day - timedelta(days=1))]
        _norm_cache[day] = (float(w.kpr.mean()), float(w.rounds.mean()), len(w))
    return _norm_cache[day]


# ---------------------------------------------------------------- scored rows (board rule)
seen, board = set(), []
for r in sorted(pre, key=lambda r: r["id"]):
    if not (r.get("on_board") == 1 and r.get("test") in (0, 2) and r["line_type"] == "standard"
            and r["status"] == "settled" and r.get("actual") is not None and r["prop"] in ("kills", "hs")
            and r["scope"] in ("12", "1") and r["pick"] in ("More", "Less")):
        continue
    k = (r["player_id"], r["prop"], r["scope"], float(r["line"]), r["pick"], r["day"])
    if k in seen:
        continue
    seen.add(k)
    a, ln = float(r["actual"]), float(r["line"])
    if a == ln:
        continue
    r["over"] = int(a > ln)
    board.append(r)

# matching to HLTV for the upset / OT check: exact lower-case player names, HLTV match on the start date +-1 day
names = {int(k): v.lower() for k, v in json.load(gzip.open(f"{D}/hltv_2026-10-28_2026-11-13.json.gz", "rt"))["players"].items()}
ex["pname"] = ex["player"].map(names)
ex_by_day = {d: g for d, g in ex.groupby("date")}
slug_players = defaultdict(lambda: defaultdict(set))
slug_pwin = defaultdict(lambda: defaultdict(list))
for r in pre:
    slug_players[r["match_slug"]][r["team"]].add(str(r["player"]).lower())
    if r.get("p_win") is not None:
        slug_pwin[r["match_slug"]][r["team"]].append(float(r["p_win"]))
slug_day = {r["match_slug"]: r["day"] for r in pre}


def hltv_result(slug):
    d0 = slug_day[slug]
    cands = pd.concat([ex_by_day[d] for d in (d0 - timedelta(days=1), d0, d0 + timedelta(days=1)) if d in ex_by_day]) \
        if any(d in ex_by_day for d in (d0 - timedelta(days=1), d0, d0 + timedelta(days=1))) else None
    if cands is None:
        return None
    allp = set().union(*slug_players[slug].values())
    hits = cands[cands.pname.isin(allp)].groupby("match")["pname"].nunique()
    if hits.empty or hits.max() < 6 or (hits == hits.max()).sum() > 1:
        return None
    mid = int(hits.idxmax())
    g = cands[cands.match == mid]
    maps = g.groupby("map_no").agg(team=("team", "first"), own=("own", "first"), opp=("opp_score", "first"),
                                   opp_team=("opp", "first"))
    wins = defaultdict(int)
    for _, m in maps.iterrows():
        wins[m.team if m.own > m.opp else m.opp_team] += 1
    winner_tid = None
    if wins:
        top = max(wins.values())
        if sum(1 for v in wins.values() if v == top) == 1:
            winner_tid = max(wins, key=wins.get)
    ot = any((m.own + m.opp) > 24 for no, m in maps.iterrows() if no in (1, 2))
    # pick team -> HLTV team id by its players
    team_tid = {}
    for t, ps in slug_players[slug].items():
        tids = g[g.pname.isin(ps)]["team"].value_counts()
        team_tid[t] = int(tids.idxmax()) if not tids.empty else None
    upset = None
    pw = {t: float(np.median(v)) for t, v in slug_pwin[slug].items() if v}
    if winner_tid is not None and pw:
        dog = [t for t, p in pw.items() if p < 0.5]
        if dog and team_tid.get(dog[0]) is not None:
            upset = int(team_tid[dog[0]] == winner_tid)
        elif pw and all(p >= 0.5 for p in pw.values()):
            upset = 0
    return {"hltv": mid, "ot": int(ot), "upset": upset}


# ---------------------------------------------------------------- candidate factors
def factor_table(alpha, mode="cand", window=None):
    """f per (slug, scope). mode cand / sign (PL-S) / shuffle (PL-X)."""
    keys = sorted(k for k in lobby if window is None or window[0] <= slug_day[k[0]] <= window[1])
    out = {}
    base = {}
    for k in keys:
        kpr, rr, _ = normal(slug_day[k[0]])
        N = kpr * rr * (2 if k[1] == "12" else 1)
        base[k] = (lobby[k]["T"], N)
    if mode == "cand":
        for k, (T, N) in base.items():
            out[k] = (T + alpha * (N - T)) / T
    elif mode == "sign":
        slugs = sorted({k[0] for k in keys})
        s = dict(zip(slugs, np.random.default_rng(20261113).choice([-1, 1], size=len(slugs))))
        for k, (T, N) in base.items():
            f = (T + alpha * (N - T)) / T
            out[k] = 1 + s[k[0]] * abs(f - 1)
    elif mode == "shuffle":
        rng = np.random.default_rng(20261114)
        for sc in ("12", "1"):
            ks = [k for k in keys if k[1] == sc]
            perm = rng.permutation(len(ks))
            for i, k in enumerate(ks):
                Tx = base[ks[perm[i]]][0]
                N = base[k][1]
                out[k] = (Tx + alpha * (N - Tx)) / Tx
    return out


# alpha fit: FIT units = one per player-match (Maps 1-2 kills), median projection vs settled actual
units = defaultdict(dict)
for r in pre:
    if r["prop"] == "kills" and r["scope"] == "12" and r["status"] == "settled" and r.get("actual") is not None:
        units[(r["match_slug"], r["player_id"])]["actual"] = float(r["actual"])
        units[(r["match_slug"], r["player_id"])]["team"] = r["team"]
fit_units = []
for (slug, pid), u in units.items():
    key = (slug, "12")
    if key in lobby and FIT0 <= slug_day[slug] <= FIT1:
        lam = lobby[key]["players"].get(u["team"], {}).get(pid)
        if lam is not None:
            fit_units.append((key, lam, u["actual"]))
sse = {}
for a in ALPHAS:
    ft = factor_table(a, window=(FIT0, FIT1))
    sse[a] = sum((act - lam * ft[k]) ** 2 for k, lam, act in fit_units)
ALPHA = min(sse, key=sse.get)

# c per (stat, scope) for the candidate chance (FIT rows)
cs = defaultdict(list)
for r in board:
    if FIT0 <= r["day"] <= FIT1:
        lam, ln, p = float(r["lambda"]), float(r["line"]), float(r["p_more"])
        if abs(lam - ln) >= 1 and 0.02 < p < 0.98 and abs(ND.inv_cdf(p)) > 1e-9:
            cs[(r["prop"], r["scope"])].append(abs(lam - ln) / (abs(ND.inv_cdf(p)) * math.sqrt(lam)))
C = {k: float(np.median(v)) for k, v in cs.items()}


def apply(rowset, ft):
    """per row: live side/hit, candidate side/hit, live/cand p_more."""
    out = []
    for r in rowset:
        lam, ln, p = float(r["lambda"]), float(r["line"]), float(r["p_more"])
        f = ft.get((r["match_slug"], r["scope"]), 1.0)
        lam2 = lam * f
        live_over = r["pick"] == "More"
        cand_over = live_over
        if (lam - ln) * (lam2 - ln) < 0:
            cand_over = not live_over
        c = C.get((r["prop"], r["scope"]), C.get((r["prop"], "12")))
        pc = min(max(p, 1e-4), 1 - 1e-4)
        p2 = ND.cdf(ND.inv_cdf(pc) + (lam2 - lam) / (c * math.sqrt(lam))) if f != 1.0 else p
        out.append({"m": r["match_slug"], "day": r["day"], "prop": r["prop"], "scope": r["scope"], "over": r["over"],
                    "lo": int(live_over), "co": int(cand_over), "lh": int(live_over == bool(r["over"])),
                    "ch": int(cand_over == bool(r["over"])), "p": p, "p2": p2, "f": f, "units": float(r.get("units") or 0),
                    "elig": int((r["match_slug"], r["scope"]) in ft)})
    return pd.DataFrame(out)


def boot(df, fn, n=2000, seed=7):
    ms = df["m"].unique()
    idx = {m: np.where(df["m"].values == m)[0] for m in ms}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = rng.choice(ms, size=len(ms), replace=True)
        ii = np.concatenate([idx[m] for m in pick])
        v = fn(df.iloc[ii])
        if v is not None and not np.isnan(v):
            vals.append(v)
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def dhit(d):
    return d.ch.mean() - d.lh.mean()


def side_hit(d, col_side, col_hit, over):
    s = d[d[col_side] == over]
    return s[col_hit].mean() if len(s) else np.nan


def d_over(d):
    return side_hit(d, "co", "ch", 1) - side_hit(d, "lo", "lh", 1)


def d_under(d):
    return side_hit(d, "co", "ch", 0) - side_hit(d, "lo", "lh", 0)


def brier(d, col):
    return float(((d[col] - d.over) ** 2).mean())


def logloss(d, col):
    p = d[col].clip(1e-4, 1 - 1e-4)
    return float(-(d.over * np.log(p) + (1 - d.over) * np.log(1 - p)).mean())


def summary(d, label, with_ci=True):
    s = {"label": label, "rows": len(d), "matches": int(d.m.nunique()), "flips": int((d.lo != d.co).sum()),
         "live_hit": d.lh.mean(), "cand_hit": d.ch.mean(), "d_hit": dhit(d),
         "live_over_n": int(d.lo.sum()), "cand_over_n": int(d.co.sum()),
         "live_over_hit": side_hit(d, "lo", "lh", 1), "cand_over_hit": side_hit(d, "co", "ch", 1),
         "live_under_hit": side_hit(d, "lo", "lh", 0), "cand_under_hit": side_hit(d, "co", "ch", 0),
         "live_over_share": d.lo.mean(), "cand_over_share": d.co.mean(),
         "brier_live": brier(d, "p"), "brier_cand": brier(d, "p2"), "ll_live": logloss(d, "p"), "ll_cand": logloss(d, "p2")}
    s["d_brier"] = s["brier_cand"] - s["brier_live"]
    for side, col_s, col_p in (("over", 1, None), ("under", 0, None)):
        for who, cs_, cp in (("live", "lo", "p"), ("cand", "co", "p2")):
            x = d[d[cs_] == col_s]
            if len(x):
                stated = (x[cp] if col_s == 1 else 1 - x[cp]).mean()
                s[f"{who}_{side}_stated_minus_hit"] = float(stated - (x.over == col_s).mean())
    if with_ci and len(d):
        s["ci_d_hit"] = boot(d, dhit)
        s["ci_d_over"] = boot(d, d_over)
        s["ci_d_under"] = boot(d, d_under)
        s["ci_d_brier"] = boot(d, lambda x: brier(x, "p2") - brier(x, "p"))
    return s


def verdict(d, res, ft, label):
    s = summary(d, label)
    over_ok = (s["cand_over_hit"] >= s["live_over_hit"]) and (s["cand_under_hit"] >= s["live_under_hit"])
    C1 = s["ci_d_hit"][0] > 0
    C3 = len(d) >= len(d)  # graded count: every row keeps a side by construction
    C4 = abs(s["cand_over_share"] - 0.5) <= abs(s["live_over_share"] - 0.5) + 0.01
    C5 = s["d_brier"] <= 0
    sub = {}
    for name, mask in (("upset", d.m.map(lambda m: (res.get(m) or {}).get("upset") == 1)),
                       ("ot", d.m.map(lambda m: (res.get(m) or {}).get("ot") == 1)),
                       ("rest", d.m.map(lambda m: (res.get(m) or {}).get("upset") == 0 and (res.get(m) or {}).get("ot") == 0))):
        x = d[mask.astype(bool)]
        sub[name] = {"rows": len(x), "matches": int(x.m.nunique()), "live_hit": x.lh.mean() if len(x) else None,
                     "cand_hit": x.ch.mean() if len(x) else None, "d_hit": dhit(x) if len(x) else None,
                     "flips": int((x.lo != x.co).sum())}
    unk = d[d.m.map(lambda m: res.get(m) is None or (res.get(m) or {}).get("upset") is None)]
    sub["unclassified"] = {"rows": len(unk), "matches": int(unk.m.nunique())}
    C6 = all(sub[k]["d_hit"] is not None and sub[k]["d_hit"] >= 0 for k in ("upset", "ot", "rest"))
    halves = {"T1": dhit(d[d.day <= T1_END]), "T2": dhit(d[d.day > T1_END])}
    C7 = all(v >= 0 for v in halves.values())
    return s, {"C1": C1, "C2": over_ok, "C3": C3, "C4": C4, "C5": C5, "C6": C6, "C7": C7}, sub, halves


if __name__ == "__main__":
    test_rows = [r for r in board if TEST0 <= r["day"] <= TEST1]
    fit_rows = [r for r in board if FIT0 <= r["day"] <= FIT1]
    print("alpha SSE grid:", {a: round(v, 1) for a, v in sse.items()}, "-> alpha", ALPHA, "fit units", len(fit_units))
    print("c per (stat, scope):", {f"{k[0]}/{k[1]}": round(v, 3) for k, v in C.items()})
    res = {m: hltv_result(m) for m in {r["match_slug"] for r in test_rows}}
    out = {"alpha": ALPHA, "sse": sse, "c": {f"{k[0]}/{k[1]}": v for k, v in C.items()}, "fit_units": len(fit_units)}
    ft_c = factor_table(ALPHA, "cand", window=(TEST0, TEST1))
    elig_test = sorted({k for k in ft_c if TEST0 <= slug_day[k[0]] <= TEST1})
    fs = np.array([ft_c[k] for k in elig_test])
    print("eligible test lobbies", len(elig_test), "f quantiles", np.round(np.percentile(fs, [0, 10, 25, 50, 75, 90, 100]), 4))
    nk = {k: normal(slug_day[k[0]]) for k in elig_test}
    print("normal KPR/rounds/maps (first, last test day):", nk[elig_test[0]], normal(TEST1))
    lob_kpr = [lobby[k]["T"] / (normal(slug_day[k[0]])[1] * (2 if k[1] == "12" else 1)) for k in elig_test if k[1] == "12"]
    print("implied lobby KPR (Maps 1-2, T / (R_norm x 2)) mean", round(float(np.mean(lob_kpr)), 3))
    for mode in ("cand", "sign", "shuffle"):
        ft = factor_table(ALPHA, mode, window=(TEST0, TEST1))
        d = apply(test_rows, ft)
        s, crit, sub, halves = verdict(d, res, ft, mode)
        out[mode] = {"summary": s, "criteria": crit, "subsets": sub, "halves": halves}
        print(f"\n=== {mode} ===")
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}, default=str))
        print("criteria", crit)
        print("subsets", json.dumps(sub, default=lambda v: round(v, 4) if isinstance(v, float) else str(v)))
        print("halves", {k: round(v, 4) for k, v in halves.items()})
        if mode == "cand":
            # descriptive slices + sensitivity
            for lab, mask in (("kills 12", (d.prop == "kills") & (d.scope == "12")), ("hs 12", (d.prop == "hs") & (d.scope == "12")),
                              ("map1 (kills+hs)", d.scope == "1"), ("staked >0U", d.units > 0), ("eligible lobbies only", d.elig == 1)):
                x = d[mask]
                ss = summary(x, lab, with_ci=True) if len(x) else {"rows": 0}
                out.setdefault("slices", {})[lab] = ss
                print("slice", lab, json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in ss.items()}, default=str))
            mot = {"vitality-vs-falcons-esports-11-11-2026", "mouz-cs2-vs-g2-11-11-2026", "furia-vs-vitality-12-11-2026",
                   "fut-cs2-vs-mouz-cs2-12-11-2026", "vitality-vs-mouz-cs2-13-11-2026"} & set(d.m.unique())  # review 3b
            x = d[~d.m.isin(mot)]
            ss = summary(x, "excl motivating")
            out["sensitivity_excl_motivating"] = {"matches_excluded": sorted(mot), **ss}
            print("sensitivity excl", sorted(mot), json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in ss.items()}, default=str))
            fd = apply(fit_rows, factor_table(ALPHA, "cand", window=(FIT0, FIT1)))
            ss = summary(fd, "FIT window (in-sample)")
            out["fit_window"] = ss
            print("FIT window", json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in ss.items()}, default=str))
            # direction of flips
            fl = d[d.lo != d.co]
            out["flips"] = {"to_over": int((fl.co == 1).sum()), "to_under": int((fl.co == 0).sum()),
                            "to_over_hit_cand": float(fl[fl.co == 1].ch.mean()) if (fl.co == 1).any() else None,
                            "to_under_hit_cand": float(fl[fl.co == 0].ch.mean()) if (fl.co == 0).any() else None}
            print("flips", out["flips"])
    json.dump(out, open(f"{D}/score.json", "w"), default=lambda v: v.item() if isinstance(v, (np.floating, np.integer, np.bool_)) else str(v), indent=1)
