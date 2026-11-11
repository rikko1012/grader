"""gu-1111 NBA projection arms P1-P11 (FROZEN_RULES.md section 2): one multiplier on the sm-1 mean per arm, gamma by
Poisson deviance on box scores of EARLIER windows only, scored at the real offered lines by the owner hit-rate rule.

  python -m research.sports_lab.grade_up_1111.nba_factors <dev_wf_dir> <final_wf_dir> <frame.pkl> <nba_full.db> <lg1_ext.pkl> <out_dir>

dev_wf_dir   = sacc_1109.wf dev windows (sm1_<stat>__dev_*.npz, monthly refits, out-of-sample sm-1 PMFs)
final_wf_dir = sacc_1109.wf final windows (sm1frozen_<stat>__final_*.npz, the frozen live artifact)
Research only: reads local files, writes <out_dir>.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from research.sports_lab.grade_up_1111 import common as C
from research.sports_lab.sacc_1109 import cand as SC

MARKETS = ("points", "rebounds", "assists", "threes")
STAT = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3m"}
ALLOW = {"points": "allow_pts_pm", "rebounds": "allow_reb_pm", "assists": "allow_ast_pm", "threes": "allow_fg3m_pm"}
EVAL_DEV_FROM = "2026-01-01"
FRESH_FROM = "2026-11-06T03:00:00Z"
CITY = {"ATL": (33.76, -84.40), "BKN": (40.68, -73.98), "BOS": (42.37, -71.06), "CHA": (35.23, -80.84),
        "CHI": (41.88, -87.67), "CLE": (41.50, -81.69), "DAL": (32.79, -96.81), "DEN": (39.75, -105.01),
        "DET": (42.34, -83.06), "GS": (37.77, -122.39), "HOU": (29.75, -95.36), "IND": (39.76, -86.16),
        "LAC": (33.94, -118.34), "LAL": (34.04, -118.27), "MEM": (35.14, -90.05), "MIA": (25.78, -80.19),
        "MIL": (43.04, -87.92), "MIN": (44.98, -93.28), "NO": (29.95, -90.08), "NY": (40.75, -73.99),
        "OKC": (35.46, -97.52), "ORL": (28.54, -81.38), "PHI": (39.90, -75.17), "PHX": (33.45, -112.07),
        "POR": (45.53, -122.67), "SA": (29.43, -98.44), "SAC": (38.58, -121.50), "TOR": (43.64, -79.38),
        "UTAH": (40.77, -111.90), "WSH": (38.90, -77.02)}


def _km(a, b):
    la1, lo1, la2, lo2 = map(np.radians, (a[0], a[1], b[0], b[1]))
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return float(2 * 6371 * np.arcsin(np.sqrt(h)))


def _ew_prior(x, hl):
    """EW mean of the values BEFORE each position (shifted by one), half-life hl items; NaN at the first."""
    a = 0.5 ** (1.0 / hl)
    out, s, w = np.full(len(x), np.nan), 0.0, 0.0
    for i, v in enumerate(x):
        if w > 0:
            out[i] = s / w
        if v == v:
            s, w = a * s + v, a * w + 1.0
    return out


# --------------------------------------------------------------------------------------------------- extra features
def extra_features(f, db):
    """Adds the pre-game columns the frame does not carry. Every value uses games that started before the row's."""
    f = f.sort_values(["ts", "game_id", "player_id"]).reset_index(drop=True)
    f["key"] = f["game_id"] + "|" + f["player_id"]
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    pf = pd.read_sql_query("SELECT game_id, player_id, pf FROM player_games WHERE played = 1", con)
    con.close()
    f = f.merge(pf, on=["game_id", "player_id"], how="left")
    # P1 inputs: foul rate, rotation length
    f["pf36_ew10"] = np.nan
    for _, g in f.groupby("player_id", sort=False):
        e_pf, e_min = _ew_prior(g["pf"].to_numpy(float), 10), _ew_prior(g["min"].to_numpy(float), 10)
        f.loc[g.index, "pf36_ew10"] = 36 * e_pf / np.maximum(e_min, 1.0)
    tg = f.groupby(["team", "game_id"], sort=False).agg(ts=("ts", "first"), n10=("min", lambda m: int((m >= 10).sum())),
                                                        opp=("opp", "first"), home=("home", "first"),
                                                        season=("season", "first")).reset_index()
    tg = tg.sort_values(["team", "ts"]).reset_index(drop=True)
    tg["rot_len5"] = tg.groupby("team")["n10"].transform(lambda s: s.shift(1).rolling(5, min_periods=1).mean())
    # P6: venue = the home team's city; km since the team's previous game; 0 for a season opener
    venue = np.where(tg["home"] == 1, tg["team"], tg["opp"])
    tg["venue"] = venue
    pv = tg.groupby("team")["venue"].shift(1)
    ps = tg.groupby("team")["season"].shift(1)
    tg["travel_km"] = [0.0 if (p is None or p != p or s0 != s1 or p not in CITY or v not in CITY) else _km(CITY[p], CITY[v])
                       for p, v, s0, s1 in zip(pv, tg["venue"], ps, tg["season"])]
    tg["alt_away"] = ((tg["home"] != 1) & tg["venue"].isin(["DEN", "UTAH"])).astype(float)
    f = f.merge(tg[["team", "game_id", "rot_len5", "travel_km", "alt_away"]], on=["team", "game_id"], how="left")
    # P2: stat per minute allowed by the opponent to the player's position group over its 20 prior games
    for mk, c in STAT.items():
        a = f.groupby(["opp", "pos_g", "game_id"], sort=False).agg(ts=("ts", "first"), s=(c, "sum"), m=("min", "sum")).reset_index()
        a = a.sort_values(["opp", "pos_g", "ts"]).reset_index(drop=True)
        grp = a.groupby(["opp", "pos_g"])
        a["S"] = grp["s"].transform(lambda x: x.shift(1).rolling(20, min_periods=5).sum())
        a["M"] = grp["m"].transform(lambda x: x.shift(1).rolling(20, min_periods=5).sum())
        a["A"] = a["S"] / a["M"].where(a["M"] > 0)
        # league mean of A over all teams as of each time (latest value per team, forward-filled)
        L = {}
        for g, x in a.groupby("pos_g"):
            w = x.pivot_table(index="ts", columns="opp", values="A", aggfunc="last").sort_index().ffill()
            L[g] = w.mean(axis=1)
        a["L"] = [L[g].get(t, np.nan) for g, t in zip(a["pos_g"], a["ts"])]
        a[f"opp_pos_{mk}"] = np.log(a["A"] / a["L"])
        f = f.merge(a[["opp", "pos_g", "game_id", f"opp_pos_{mk}"]], on=["opp", "pos_g", "game_id"], how="left")
    f = with_without(f)
    return f


def with_without(f):
    """P4: shrunk log ratio of the player's per-game stat without / with each top-3 usage teammate who missed the
    team's PREVIOUS game (known before tip). Same season and the one before, prior games only."""
    f = f.sort_values(["ts", "game_id", "player_id"]).reset_index(drop=True)
    for mk in MARKETS:
        f[f"ww_{mk}"] = 0.0
    f["ww_n_out"] = 0.0
    played = f.groupby(["team", "game_id"], sort=False)["player_id"].agg(set).to_dict()
    tgs = f.groupby(["team", "game_id"], sort=False)["ts"].first().reset_index().sort_values(["team", "ts"])
    order = {t: list(g["game_id"]) for t, g in tgs.groupby("team")}
    last_usage = {}                                    # (team, player) -> (last pre-game usage, team game index)
    rows_by_tg = {k: v.to_numpy() for k, v in f.groupby(["team", "game_id"], sort=False).groups.items()}
    usage = f["usage_pg_ew10"].to_numpy(float)
    pid = f["player_id"].to_numpy()
    season = f["season"].to_numpy()
    statv = {mk: f[c].to_numpy(float) for mk, c in STAT.items()}
    hist = {}                                          # (team, player) -> list of (season, game_id, row index)
    out = {mk: np.zeros(len(f)) for mk in MARKETS}
    n_out = np.zeros(len(f))
    for team, gl in order.items():
        recent = {}                                    # player -> (usage, last team-game index played)
        for i, gid in enumerate(gl):
            idx = rows_by_tg[(team, gid)]
            if i >= 1:
                prev = played[(team, gl[i - 1])]
                cands = [(u, p) for p, (u, j) in recent.items() if i - j <= 10 and u == u]
                top = [p for _, p in sorted(cands, reverse=True)[:3]]
                outq = [q for q in top if q not in prev]
                if outq:
                    for r in idx:
                        p = pid[r]
                        h = [x for x in hist.get((team, p), ()) if x[0] >= season[r] - 1]
                        if not h:
                            continue
                        k = 0
                        for q in outq:
                            if q == p:
                                continue
                            wo = [x[2] for x in h if q not in played[(team, x[1])]]
                            wi = [x[2] for x in h if q in played[(team, x[1])]]
                            k += 1
                            if len(wo) < 1 or len(wi) < 3:
                                continue
                            sh = len(wo) / (len(wo) + 10.0)
                            for mk in MARKETS:
                                a, b = statv[mk][wo].mean(), statv[mk][wi].mean()
                                if a > 0 and b > 0:
                                    out[mk][r] += sh * float(np.clip(np.log(a / b), -1.0, 1.0))
                        n_out[r] = k
            for r in idx:
                p = pid[r]
                recent[p] = (usage[r], i)
                hist.setdefault((team, p), []).append((season[r], gid, r))
    for mk in MARKETS:
        f[f"ww_{mk}"] = out[mk]
    f["ww_n_out"] = n_out
    return f


MIN_INPUTS = ["min_ew3", "min_ew10", "min_ew30", "min_last", "min_l3", "starter_lag", "starter_r10", "abs_spread",
              "spread_x_starter", "out_min48", "out_starters", "b2b", "pf36_ew10", "rot_len5", "early", "missed"]


def min_inputs(f):
    d = pd.DataFrame(index=f.index)
    for c in ("min_ew3", "min_ew10", "min_ew30", "min_last", "min_l3", "starter_lag", "starter_r10", "abs_spread",
              "out_starters", "b2b", "pf36_ew10", "rot_len5"):
        d[c] = f[c].astype(float)
    d["spread_x_starter"] = f["abs_spread"].astype(float) * f["starter_lag"].astype(float)
    d["out_min48"] = f["out_min"].astype(float) / 48.0
    d["early"] = (f["season_n"].astype(float) <= 3).astype(float)
    d["missed"] = (f["missed_since_last"].astype(float) > 0).astype(float)
    return d[MIN_INPUTS].to_numpy(float)


def fit_minutes(X, y, alpha=1.0):
    mu, sd = np.nanmean(X, 0), np.nanstd(X, 0)
    sd = np.where(sd > 1e-9, sd, 1.0)
    Z = np.nan_to_num((X - mu) / sd, nan=0.0)
    A = np.column_stack([np.ones(len(Z)), Z])
    R = alpha * np.eye(A.shape[1])
    R[0, 0] = 0
    beta = np.linalg.solve(A.T @ A + R, A.T @ y)
    return mu, sd, beta


def pred_minutes(X, m):
    mu, sd, beta = m
    Z = np.nan_to_num((X - mu) / sd, nan=0.0)
    return np.clip(np.column_stack([np.ones(len(Z)), Z]) @ beta, 0, 48)


# ------------------------------------------------------------------------------------------------------------- arms
def raw_columns(d, mk):
    """arm -> (names, matrix of RAW pre-game columns; NaN = unknown). d = wf rows joined to the frame."""
    g = lambda c: d[c].to_numpy(float)
    ew10 = np.maximum(np.nan_to_num(g("min_ew10"), nan=10.0), 1.0)
    st = g("starter_lag")
    load = ew10 / 30.0
    b2b = g("b2b")
    busy = (g("games_last_7d") >= 4).astype(float)
    arms = {
        "P1 min2": (["log_mhat_vs_ew"], [g("z_min2")]),
        "P2 opp_pos": (["log_allowed_to_pos"], [g(f"opp_pos_{mk}")]),
        "P3 pace_total": (["log_total", "log_pace", "total_x_pace"],
                          [np.log(g("total_line") / 225.0), np.log((g("t_pace_ew10") + g("o_pace_ew10")) / 200.0),
                           np.log(g("total_line") / 225.0) * np.log((g("t_pace_ew10") + g("o_pace_ew10")) / 200.0)]),
        "P4 with_without": (["with_without"], [g(f"ww_{mk}")]),
        "P5 home": (["home"], [g("home")]),
        "P6 alt_travel": (["alt_away", "travel_kkm", "b2b_travel"],
                          [g("alt_away"), g("travel_km") / 1000.0, b2b * (g("travel_km") > 500)]),
        "P7 rest_load": (["b2b", "b2b_x_load", "busy", "busy_x_load", "rest3"],
                         [b2b, b2b * load, busy, busy * load, (g("days_rest") >= 3).astype(float)]),
        "P9 role_change": (["starter_change", "log_l3_vs_ew", "log_last_vs_ew", "team_changed", "missed"],
                           [st - g("starter_r10"), np.log((g("min_l3") + 1) / (ew10 + 1)),
                            np.log((g("min_last") + 1) / (ew10 + 1)), g("team_changed"),
                            (g("missed_since_last") > 0).astype(float)]),
        "P10 garbage": (["abs_spread", "abs_spread_x_starter", "team_spread", "team_spread_x_starter"],
                        [g("abs_spread") / 10, g("abs_spread") / 10 * st, g("team_spread") / 10,
                         g("team_spread") / 10 * st]),
    }
    if mk in ("points", "threes"):
        with np.errstate(divide="ignore", invalid="ignore"):
            c3 = g("r_fg3a_ew40") / np.where(g("r_fga_ew40") > 0, g("r_fga_ew40"), np.nan) - 0.40
            d3 = np.log(np.where(g("allow_fg3a_pm") > 0, g("allow_fg3a_pm"), np.nan))
            dp = np.log(np.where(g("allow_pts_pm") > 0, g("allow_pts_pm"), np.nan))
        arms["P8 shot_profile"] = (["three_share_x_opp3", "opp_3pa_allowed", "opp_pts_allowed"], [c3 * d3, d3, dp])
    names, cols = [], []
    for nm, (n, c) in arms.items():
        names += [f"{nm.split()[0]}:{x}" for x in n]
        cols += c
    arms["P11 all"] = (names, cols)
    out = {k: (n, np.column_stack(c)) for k, (n, c) in arms.items()}
    # POST-HOC reference (added after the first scoring run, not a candidate): the constant alone, to separate the
    # level shift every arm carries from the factor itself
    out["P0 const-only (post-hoc reference)"] = ([], np.zeros((len(d), 0)))
    return out


def standardise(X, tr):
    mu, sd = np.nanmean(X[tr], 0), np.nanstd(X[tr], 0)
    sd = np.where(sd > 1e-9, sd, 1.0)
    # P8 centres log(allowed) on its training mean through this step (frozen: "/ its training mean")
    return np.nan_to_num((X - mu) / sd, nan=0.0)


def load_wf(dev_dir, final_dir, mk):
    parts = []
    for p in sorted(Path(dev_dir).glob(f"sm1_{mk}__dev_*.npz")) + sorted(Path(final_dir).glob(f"sm1frozen_{mk}__final_*.npz")):
        z = np.load(p)
        d = pd.DataFrame({"key": z["keys"], "y": z["y"], "mean": z["mean"], "elig": z["elig"],
                          "ew_min": z["f_ew_min"], "window": p.stem.split("__")[1]})
        d["_pmf"] = list(z["pmf"])
        lo = int(z["lo"])
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    P = np.stack(d.pop("_pmf").to_numpy()).astype(np.float64)
    P /= P.sum(1, keepdims=True)
    return d, P, lo


def wstart(w):
    return w.split("_", 1)[1]


def run(dev_dir, final_dir, frame_pkl, db, lines_pkl, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    f = extra_features(pd.read_pickle(frame_pkl), db)
    f = f.drop_duplicates("key").set_index("key")
    print("features", round(time.time() - t0), flush=True)
    lines = pd.read_pickle(lines_pkl)
    lines = lines[lines["result"].isin(["over", "under"])].reset_index(drop=True)
    lines["key"] = lines["game_id"] + "|" + lines["player_id"]
    lines["over"] = (lines["result"] == "over").astype(float)
    assert not lines.duplicated(["game_id", "player_id", "stat", "book", "line"]).any()
    arm_po = {}                                             # arm -> per-line-row offline P(Over) (NaN = no PMF)
    base_po = np.full(len(lines), np.nan)
    rep = {"markets": {}, "arms": {}}
    Xmin_all = min_inputs(f)
    for mk in MARKETS:
        d, P, lo = load_wf(dev_dir, final_dir, mk)
        d = d.join(f.rename(columns={"min": "min_fr"}), on="key", rsuffix="_fr")
        ts = d["ts"].to_numpy(float)
        wins = sorted(d["window"].unique(), key=wstart)
        y, mu = d["y"].to_numpy(float), np.maximum(d["mean"].to_numpy(float), 0.05)
        games = d["key"].str.split("|").str[0].to_numpy()
        el = d["elig"].to_numpy(bool)
        ks = lo + np.arange(P.shape[1], dtype=float)
        wsv = d["window"].map(wstart).to_numpy()
        test_w = [w for w in wins if w.startswith("final_") or wstart(w) >= EVAL_DEV_FROM]
        # P1 stage 1: minutes model per test window, fitted on frame rows that started before the window
        d["z_min2"] = np.nan
        fts, fmin, fpn = f["ts"].to_numpy(float), f["min"].to_numpy(float), f["prior_n"].to_numpy(float)
        for w in wins:                                       # every window: model fitted on games before it starts
            te = (d["window"] == w).to_numpy()
            t_start = pd.Timestamp(wstart(w), tz="UTC").timestamp()
            tr = (fts < t_start) & (fpn >= 3) & np.isfinite(fmin)
            m = fit_minutes(Xmin_all[tr], fmin[tr])
            mh = pred_minutes(min_inputs(d.loc[te]), m)
            d.loc[te, "z_min2"] = np.log((mh + 1) / (np.maximum(np.nan_to_num(d.loc[te, "ew_min"].to_numpy(float), nan=10.0), 0) + 1))
            if w == wins[-1]:
                rep.setdefault("minutes_model", {})[mk] = {
                    "n_train": int(tr.sum()), "beta_std": dict(zip(["const"] + MIN_INPUTS, np.round(m[2], 3).tolist())),
                    "test_mae_model": round(float(np.nanmean(np.abs(mh - d.loc[te, "min_fr"].to_numpy(float)))), 3) if "min_fr" in d else None,
                    "test_mae_ew": round(float(np.nanmean(np.abs(d.loc[te, "ew_min"].to_numpy(float) - d.loc[te, "min_fr"].to_numpy(float)))), 3) if "min_fr" in d else None}
        arms = raw_columns(d, mk)
        moved = {a: P.copy() for a in arms}
        gam = {a: {} for a in arms}
        for w in test_w:
            te = (d["window"] == w).to_numpy()
            tr = wsv < wstart(w)                             # every earlier window (dev and earlier final)
            for a, (names, X) in arms.items():
                Z = np.column_stack([np.ones(len(d)), standardise(X, tr)])
                g = SC.fit_mean(y[tr], mu[tr], Z[tr])
                moved[a][te] = SC.remap_rows(P[te], lo, np.exp(np.clip(Z[te] @ g, -2, 2)))
                gam[a][w] = dict(zip(["const"] + names, np.round(g, 4).tolist()))
        # all-outcome information: log score per eligible player-game on the test windows (dev 2026 and final)
        yi = np.clip(np.rint(y).astype(int) - lo, 0, P.shape[1] - 1)
        ls0 = np.log(np.maximum(P[np.arange(len(P)), yi], 1e-6))
        info = {}
        for scope, m in (("dev_2026", np.array([w.startswith("dev_") and wstart(w) >= EVAL_DEV_FROM for w in d["window"]]) & el),
                         ("final", d["window"].str.startswith("final_").to_numpy() & el)):
            bt = SC.Boot(games[m])
            info[scope] = {"n": int(m.sum())}
            for a in arms:
                dl = np.log(np.maximum(moved[a][np.arange(len(P)), yi], 1e-6)) - ls0
                info[scope][a] = {"d_logscore": round(float(dl[m].mean()), 5), "ci": bt.ci(dl[m]),
                                  "d_mae": round(float((np.abs(moved[a][m] @ ks - y[m]) - np.abs(P[m] @ ks - y[m])).mean()), 4)}
        rep["markets"][mk] = {"all_outcome": info, "gamma_last_window": {a: gam[a][test_w[-1]] for a in arms},
                              "n_train_last": int((wsv < wstart(test_w[-1])).sum())}
        # line rows of this market
        fin = d["window"].str.startswith("final_").to_numpy()
        idx = pd.Series(np.flatnonzero(fin), index=d.loc[fin, "key"].to_numpy())
        idx = idx[~idx.index.duplicated()]
        lm = np.flatnonzero((lines["stat"] == mk).to_numpy())
        have = lines["key"].iloc[lm].isin(idx.index).to_numpy()
        li = lm[have]
        ii = idx.loc[lines["key"].iloc[li]].to_numpy()
        L = lines["line"].to_numpy(float)[li][:, None]

        def po_of(Q):
            q = Q[ii]
            po, pu = (q * (ks[None, :] > L)).sum(1), (q * (ks[None, :] < L)).sum(1)
            return po / np.maximum(po + pu, 1e-12)
        base_po[li] = po_of(P)
        for a in arms:
            arm_po.setdefault(a, np.full(len(lines), np.nan))[li] = po_of(moved[a])
        rep["markets"][mk]["line_rows"], rep["markets"][mk]["line_rows_with_pmf"] = int(len(lm)), int(len(li))
        print(mk, "done", round(time.time() - t0), flush=True)
    # ------------------------------------------------------------------------------------------------ line scoring
    live = lines["p_over"].to_numpy(float)
    over = lines["over"].to_numpy(float)
    fresh = (lines["start_at"] >= FRESH_FROM).to_numpy()
    new = (lines["start_at"] >= "2026-11-09T12:00:00Z").to_numpy()
    games = lines["game_id"].to_numpy()
    bt_all = C.Boot(games)
    lines["po_base_off"] = base_po
    for a, po in arm_po.items():
        ok = np.isfinite(po) & np.isfinite(base_po)
        xl = np.where(ok, C.expit(C.logit(live) + C.logit(np.where(ok, po, 0.5)) - C.logit(np.where(ok, base_po, 0.5))), live)
        lines[f"po_{a.split()[0]}"] = xl
        r = {"pooled": C.side_arm(bt_all, over, live, xl, "nba", fresh=fresh), "by_market": {}, "by_period": {}}
        for mk in MARKETS:
            m = (lines["stat"] == mk).to_numpy()
            if np.isfinite(po[m]).any():
                r["by_market"][mk] = C.side_arm(C.Boot(games[m]), over[m], live[m], xl[m], "nba", fresh=fresh[m])
        for lab, m in (("diagnosed_oct24_nov5", ~fresh), ("fresh_nov6_on", fresh), ("new_nov9_10", new)):
            if m.any():
                r["by_period"][lab] = C.side_arm(C.Boot(games[m]), over[m], live[m], xl[m], "nba")
        rep["arms"][a] = r
        p = r["pooled"]
        print(f"{a:16s} flips {p['flips']:5d} all {p['all']['base']['hit']}->{p['all']['arm']['hit']} d {p['all']['diff']} "
              f"{p['all']['ci']} | graded n {p['graded']['base']['n']}->{p['graded']['arm']['n']} hit "
              f"{p['graded']['base']['hit']}->{p['graded']['arm']['hit']} d {p['graded']['diff']} {p['graded']['ci']} "
              f"O {p['graded']['arm']['over_hit']} U {p['graded']['arm']['under_hit']} osh "
              f"{p['graded']['base']['over_share']}->{p['graded']['arm']['over_share']} fresh {p.get('fresh_graded_diff')} "
              f"| {'PASS' if p['pass'] else 'FAIL ' + ','.join(p['failed'])}", flush=True)
    rep["rows"], rep["games"] = int(len(lines)), int(lines["game_id"].nunique())
    rep["rows_with_offline_pmf"] = int(np.isfinite(base_po).sum())
    rep["fresh_rows"], rep["new_rows"] = int(fresh.sum()), int(new.sum())
    (out_dir / "nba_factors.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    lines.to_pickle(out_dir / "nba_factor_lines.pkl")


if __name__ == "__main__":
    run(*sys.argv[1:7])
