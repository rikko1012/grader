"""Offline reconstruction of the LIVE CS2 projection distribution (tag 3007c59b74) on the full HLTV history.

Uses the live code itself - perf.project (perf-2), cm2.build_tables / cm2.projection / cm1.hs_ratio (perf-cm2, hs-cm2) - on
rows built from the local HLTV extract, with the live routing (cs2_scout._perf_input_once / _cm2_live):
  Map 1 kills / HS        perf-2 scope 1 (HS = beta-binomial over the kills pmf, c = 1.442)
  Maps 1-2 kills          cm-2 when eligible (target environment known, player + teams in the tables, maps in 420 days),
                          else perf-2 scope 12
  Maps 1-2 HS             hs-cm2: cm-2 kills x cm1.hs_ratio under the beta-binomial, c = cm-2 spread_c (else perf-2)
  Maps 1-3 kills / HS     perf-2 scope 123
perf-2 history = the performance model's input revision N0 (perf_live, rule n0-r4) reconstructed from raw_pages_meta +
team_rankings: matches first fetched before the 2026-10-12 21:27 UTC backfill, plus later-dated fresh matches whose two teams
are in the VRS top 200 of the snapshot on or before the fetch day. raw_pages_meta keeps ONE fetch time per match (the
production rule takes the minimum over raw_pages), so a seed match re-fetched after the backfill start is counted out here -
the reconstruction is approximate and its size is reported next to production's 8,597-match seed.
Not reproducible offline: BO3.gg fallback (perf-2-bo3) for players with no HLTV maps - those rows are flagged (no_hist) and
reported separately; multi-id nick resolution (one HLTV id per player here)."""
from __future__ import annotations

import bisect
import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import cm1  # noqa: E402
import cm2  # noqa: E402
import perf  # noqa: E402

from features import DATA, OUT, load_maps  # noqa: E402

BACKFILL_FROM = pd.Timestamp("2026-10-12 21:27:00", tz="UTC")
NEW_FROM = date(2026, 10, 12).toordinal()


def n0_matches(pm):
    rp = pd.read_csv(os.path.join(DATA, "hltv", "raw_pages_meta.csv.gz"), usecols=["matchid", "fetched_at", "has_html"])
    rp["f"] = pd.to_datetime(rp.fetched_at, utc=True, format="ISO8601")
    first = rp.groupby("matchid")["f"].min()
    pre = set(first[first < BACKFILL_FROM].index)
    tr = pd.read_csv(os.path.join(DATA, "hltv", "team_rankings.csv.gz"))
    tr["d"] = pd.to_datetime(tr.date).map(lambda x: x.toordinal())
    snaps = sorted(tr.d.unique())
    top = {d: set(g[g["rank"] <= 200].teamid) for d, g in tr.groupby("d")}
    mday = pm.groupby("matchid")["day"].first()
    mteams = pm.groupby("matchid")["teamid"].unique()
    late_in = set()
    for m, f in first[first >= BACKFILL_FROM].items():
        if m not in mday.index or mday[m] < NEW_FROM:
            continue
        fd = f.date().toordinal()
        i = bisect.bisect_right(snaps, fd) - 1
        if i < 0:
            continue
        ts = mteams.get(m)
        if ts is None or len(ts) == 0:
            continue
        if all(t in top[snaps[i]] for t in ts):
            late_in.add(m)
    return pre | late_in, len(pre), len(late_in)


def perf_histories(pm, allowed=None):
    """player -> sorted [(day, k, rounds, hs, lan, hs_ok)], team -> sorted [(day, kills conceded, rounds)] from complete
    scoreboards (live: pm.n == 10 / HAVING count = 5), optionally restricted to an allowed match set (N0)."""
    h = pm[pm.complete]
    if allowed is not None:
        h = h[h.matchid.isin(allowed)]
    h = h.sort_values(["day", "matchid", "number"])
    P = {}
    for p, g in h.groupby("playerid"):
        P[p] = [(int(d), int(k), int(r), None if pd.isna(hs) else int(hs), lan, bool(ok))
                for d, k, r, hs, lan, ok in zip(g.day, g.k, g.rounds, g.hs, g.lan, g.hs_ok)]
    t = h.groupby(["mapstatsid", "teamid"]).agg(day=("day", "first"), conc=("conc_k", "first"), r=("rounds", "first")).reset_index()
    T = {}
    for tid, g in t.sort_values("day").groupby("teamid"):
        T[tid] = [(int(d), int(c), int(r)) for d, c, r in zip(g.day, g.conc, g.r)]
    return P, T


def _slice(rows, day):
    """Rows with day - 420 <= map day < day (perf_live: since = match day - 420 days; perf.project drops >= day)."""
    days = [r[0] for r in rows]
    return rows[bisect.bisect_left(days, day - 420):bisect.bisect_left(days, day)]


def perf2_rows(S, P, T):
    """perf.project (kills, scope 1) per series row -> lam per map, hs_per_kill, history flag."""
    out = np.full((len(S), 3), np.nan)
    hist = np.zeros(len(S), bool)
    dcache = {}
    for i, (p, o, day, lan) in enumerate(zip(S.playerid, S.oppid, S.day, S.lan)):
        pk = dcache.get(p)
        if pk is None:
            rows = P.get(p, [])
            pk = dcache[p] = (rows, [r[0] for r in rows])
        rows, days = pk
        pm_ = rows[bisect.bisect_left(days, day - 420):bisect.bisect_left(days, day)]
        om = _slice(T.get(o, []), day)
        lan_ = None if lan is None or (isinstance(lan, float) and math.isnan(lan)) else bool(lan)
        r = perf.project(pm_, om, lan_, day, "1", "kills")
        out[i] = (r["lambda_kills"], r["hs_per_kill"], len(pm_))
        hist[i] = len(pm_) > 0
    return out, hist


def cm2_tables(pm):
    x = pm[pm.day >= cm1.DATA_START.toordinal()].sort_values(["day", "matchid", "number"])
    rows = [(int(m), str(dt)[:10], lan, int(n), int(p), int(t), int(o), int(k), None if pd.isna(hs) else int(hs), int(a), int(b))
            for m, dt, lan, n, p, t, o, k, hs, a, b in zip(x.matchid, x.date, x.lan, x.number, x.playerid, x.teamid, x.oppid,
                                                           x.k, x.hs, x.team_rounds, x.opp_rounds)]
    return cm2.build_tables(rows, built_at="2026-11-05T23:59:59+00:00", through=date(2026, 11, 5))


def cm2_rows(S, t):
    lam = np.full(len(S), np.nan)
    hsr = np.full(len(S), np.nan)
    why = []
    for i, (p, tm, o, day, lan) in enumerate(zip(S.playerid, S.teamid, S.oppid, S.day, S.lan)):
        lan_ = None if lan is None or (isinstance(lan, float) and math.isnan(lan)) else bool(lan)
        if lan_ is None:
            why.append("env unknown")
            continue
        r = cm2.projection(t, [int(p)], int(tm), int(o), int(day), lan_)
        if r.get("ineligible"):
            why.append(r["ineligible"])
            continue
        lam[i] = r["lambda"]
        hsr[i] = cm1.hs_ratio(r["inputs"])
        why.append("")
    return lam, hsr, why


def build(S, pm=None, verbose=True):
    """S: series rows (features.series_targets / the features frame). Adds live_* and full_* baseline columns."""
    pm = load_maps() if pm is None else pm
    allowed, npre, nlate = n0_matches(pm)
    if verbose:
        print("N0 reconstruction: pre", npre, "late_in", nlate, flush=True)
    S = S.copy()
    for tag, al in (("live", allowed), ("full", None)):
        P, T = perf_histories(pm, al)
        res, hist = perf2_rows(S, P, T)
        S[f"{tag}_p2_lam1"] = res[:, 0]
        S[f"{tag}_p2_hsr"] = res[:, 1]
        S[f"{tag}_p2_nmaps"] = res[:, 2]
        if verbose:
            print(tag, "perf-2 done", flush=True)
    t = cm2_tables(pm)
    if verbose:
        print("cm2 tables", t["meta"]["rows"], t["meta"]["n_refits"], flush=True)
    lam, hsr, why = cm2_rows(S, t)
    S["cm2_lam12"] = lam
    S["cm2_hsr"] = hsr
    S["cm2_why"] = why
    S.attrs["n0"] = {"pre": npre, "late_in": nlate}
    return S


def live_params(S, stat, scope, tag="live"):
    """(lam_kills, kills c, hs_per_kill) of the live route for each row."""
    lam1 = S[f"{tag}_p2_lam1"].to_numpy()
    hsr = S[f"{tag}_p2_hsr"].to_numpy()
    if scope == "1":
        return lam1, np.full(len(S), perf.SPREAD[("kills", "1")]), hsr
    if scope == "123":
        return 3 * lam1, np.full(len(S), perf.SPREAD[("kills", "123")]), hsr
    cm = S["cm2_lam12"].to_numpy()
    ok = np.isfinite(cm)
    lam = np.where(ok, cm, 2 * lam1)
    c = np.where(ok, cm2.FIT["spread_c"], perf.SPREAD[("kills", "12")])
    h = np.where(ok, S["cm2_hsr"].to_numpy(), hsr)
    return lam, c, h


if __name__ == "__main__":
    import features
    pm = load_maps()
    F = features.series_targets(pm)
    start = date(2025, 11, 1).toordinal()
    S = F[F.day >= start][["matchid", "playerid", "teamid", "oppid", "day", "lan"]].reset_index(drop=True)
    B = build(S, pm)
    B.to_pickle(os.path.join(OUT, "baseline.pkl"))
    print(B.attrs, len(B))
