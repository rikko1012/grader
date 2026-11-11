"""gu-1111 NFL projection arms N1-N12 (FROZEN_RULES.md section 2): one multiplier on the sm-1 mean per arm, gamma by
Poisson deviance on box scores of EARLIER windows only, scored at the real offered lines (weeks 8-9 + the Nov 5 TNF
game - the only NFL rows with a local offline PMF) by the owner hit-rate rule.

  python -m research.sports_lab.grade_up_1111.nfl_factors <nflwf_dir> <history.db> <nfl_lines.pkl> <out_dir>

Research only: reads local files, writes <out_dir>.
"""
from __future__ import annotations

import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from research.sports_lab.grade_up_1111 import common as C
from research.sports_lab.sacc_1109 import cand as SC
from sports.research import proj_nfl_full as H

MARKETS = ("rec", "rec_yds", "rush_att", "rush_yds", "pass_yds")
OPP = {"rec": "targets", "rec_yds": "targets", "rush_att": "carries", "rush_yds": "carries", "pass_yds": "pass_att"}
OPPDEF = {"rec": ["allow_targets", "allow_rec", "oa_targets", "oa_catch"],
          "rec_yds": ["allow_rec_yds", "allow_targets", "oa_targets", "oa_ypr"],
          "rush_att": ["allow_carries", "oa_carries"],
          "rush_yds": ["allow_rush_yds", "allow_carries", "oa_ypc"],
          "pass_yds": ["allow_pass_yds", "allow_pass_att", "oa_pass_att", "oa_ypcmp"]}
MATES = {"rec": "mates_out_tshare", "rec_yds": "mates_out_tshare", "pass_yds": "mates_out_tshare",
         "rush_att": "mates_out_carries", "rush_yds": "mates_out_carries"}
EVAL_FROM = "2026-09-01"


def frame_table(history):
    warnings.filterwarnings("ignore")
    frames, cf, meta = H.load(history)
    cols = {}
    dropped = []
    want = ["l3_snap", "ew_offense_pct", "lag1_offense_pct", "qb_change_lag", "team_spread", "implied_total",
            "spread_home", "wind_x", "temp_x", "dome", "wind", "ew_rz_targets", "ew_rz_carries", "e2_rz_targets",
            "e8_rz_targets", "e2_rz_carries", "e8_rz_carries", "plays_l6", "opp_plays_l6", "npass_l6",
            "back_from_absence", "team_change", "n_season"]
    for o in set(OPP.values()):
        want += [f"e2_{o}", f"e8_{o}", f"lag1_{o}", f"ew_{o}"]
    for v in OPPDEF.values():
        want += v
    for c in dict.fromkeys(want):
        if c in cf:
            cols[c] = np.asarray(cf.f(c), float)
        else:
            dropped.append(c)
    # N2: registry columns (sports.features.compute on the featurize frame)
    from sports import features as F
    for c in ("mates_out_tshare", "mates_out_carries"):
        try:
            cols[c] = np.asarray(F.compute(frames[4], "nfl", "rec" if c.endswith("tshare") else "rush_att", c), float)
        except Exception as e:                                        # noqa: BLE001 - reported, column dropped
            dropped.append(f"{c} ({type(e).__name__}: {str(e)[:80]})")
    t = pd.DataFrame(cols)
    t["key"] = np.char.add(np.char.add(cf["game_id"].astype(str), "|"), cf["player_id"].astype(str))
    t["position"] = np.asarray(cf["position"], dtype=object)
    return t.drop_duplicates("key").set_index("key"), dropped


def raw_columns(d, mk):
    def g(c):
        return d[c].to_numpy(float) if c in d else np.full(len(d), np.nan)
    o = OPP[mk]
    pos = d["position"].to_numpy(dtype=object)
    snap = g("ew_offense_pct")
    arms = {
        "N1 snap_route": (["l3_snap_trend", "lag1_snap_trend", "log_e2_vs_e8_opp", "log_lag1_vs_ew_opp"],
                          [g("l3_snap") - snap, g("lag1_offense_pct") - snap,
                           np.log((g(f"e2_{o}") + 1) / (g(f"e8_{o}") + 1)), np.log((g(f"lag1_{o}") + 1) / (g(f"ew_{o}") + 1))]),
        "N3 qb_change": (["qb_change_lag"], [g("qb_change_lag")]),
        "N4 opp_def": (list(OPPDEF[mk]), [g(c) for c in OPPDEF[mk]]),
        "N5 script": (["team_spread", "implied_total", "abs_spread", "team_spread_x_RB", "team_spread_x_QB"],
                      [g("team_spread"), g("implied_total"), np.abs(g("spread_home")), g("team_spread") * (pos == "RB"),
                       g("team_spread") * (pos == "QB")]),
        "N6 weather": (["wind_x", "temp_x", "dome", "wind15_outdoor"],
                       [g("wind_x"), g("temp_x"), g("dome"), ((g("wind") >= 15) & (g("dome") != 1)).astype(float)]),
        "N8 redzone": (["ew_rz_targets", "ew_rz_carries", "rz_targets_trend", "rz_carries_trend"],
                       [g("ew_rz_targets"), g("ew_rz_carries"), g("e2_rz_targets") - g("e8_rz_targets"),
                        g("e2_rz_carries") - g("e8_rz_carries")]),
        "N9 pace": (["plays_l6", "opp_plays_l6", "npass_l6"], [g("plays_l6"), g("opp_plays_l6"), g("npass_l6")]),
        "N10 promoted": (["snap_jump_25", "back_from_absence"],
                         [(g("lag1_offense_pct") - snap >= 0.25).astype(float), g("back_from_absence")]),
        "N11 new_team": (["team_change", "early_season", "team_change_x_early"],
                         [g("team_change"), (g("n_season") <= 2).astype(float), g("team_change") * (g("n_season") <= 2)]),
    }
    if MATES[mk] in d:
        arms["N2 mates_out"] = ([MATES[mk]], [g(MATES[mk])])
    names, cols = [], []
    for nm, (n, c) in arms.items():
        names += [f"{nm.split()[0]}:{x}" for x in n]
        cols += c
    arms["N12 all"] = (names, cols)
    out = {k: (n, np.column_stack(c)) for k, (n, c) in arms.items()}
    # POST-HOC reference (added after the first scoring run, not a candidate): the constant alone, to separate the
    # level shift every arm carries from the factor itself
    out["N0 const-only (post-hoc reference)"] = ([], np.zeros((len(d), 0)))
    return out


def standardise(X, tr):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mu, sd = np.nanmean(X[tr], 0), np.nanstd(X[tr], 0)
    mu = np.nan_to_num(mu, nan=0.0)
    sd = np.where(np.isfinite(sd) & (sd > 1e-9), sd, 1.0)
    return np.nan_to_num((X - mu) / sd, nan=0.0, posinf=0.0, neginf=0.0)


def load_wf(wf_dir, mk):
    parts = []
    for p in sorted(Path(wf_dir).glob(f"sm1_{mk}__dev_*.npz")) + sorted(Path(wf_dir).glob(f"sm1frozen_{mk}__final_*.npz")):
        z = np.load(p)
        d = pd.DataFrame({"key": z["keys"], "y": z["y"], "mean": z["mean"], "elig": z["elig"],
                          "window": p.stem.split("__")[1]})
        d["_pmf"] = list(z["pmf"])
        lo = int(z["lo"])
        parts.append(d)
    d = pd.concat(parts, ignore_index=True)
    P = np.stack(d.pop("_pmf").to_numpy()).astype(np.float64)
    P /= P.sum(1, keepdims=True)
    return d, P, lo


def wstart(w):
    return w.split("_", 1)[1]


def run(wf_dir, history, lines_pkl, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    ft, dropped = frame_table(history)
    print("frame", len(ft), "dropped", dropped, round(time.time() - t0), flush=True)
    lines = pd.read_pickle(lines_pkl).reset_index(drop=True)
    lines = lines[lines["book"].isin(["prizepicks", "underdog"])].reset_index(drop=True)
    assert not lines.duplicated(["game_id", "player_id", "market", "book", "line"]).any()
    arm_po, base_po = {}, np.full(len(lines), np.nan)
    rep = {"dropped_columns": dropped, "markets": {}, "arms": {}}
    for mk in MARKETS:
        d, P, lo = load_wf(wf_dir, mk)
        d = d.join(ft, on="key")
        wsv = d["window"].map(wstart).to_numpy()
        wins = sorted(d["window"].unique(), key=wstart)
        test_w = [w for w in wins if w.startswith("final_") and wstart(w) >= EVAL_FROM]
        y, mu = d["y"].to_numpy(float), np.maximum(d["mean"].to_numpy(float), 0.05)
        games = d["key"].str.split("|").str[0].to_numpy()
        el = d["elig"].to_numpy(bool)
        step = float(H.STEP.get(mk, 1.0)) if False else 1.0
        ks = lo + np.arange(P.shape[1], dtype=float)
        arms = raw_columns(d, mk)
        moved = {a: P.copy() for a in arms}
        gam = {a: {} for a in arms}
        for w in test_w:
            te = (d["window"] == w).to_numpy()
            tr = wsv < wstart(w)
            for a, (names, X) in arms.items():
                Z = np.column_stack([np.ones(len(d)), standardise(X, tr)])
                g = SC.fit_mean(y[tr], mu[tr], Z[tr])
                moved[a][te] = SC.remap_rows(P[te], lo, np.exp(np.clip(Z[te] @ g, -2, 2)))
                gam[a][w] = dict(zip(["const"] + names, np.round(g, 4).tolist()))
        yi = np.clip(np.rint(y).astype(int) - lo, 0, P.shape[1] - 1)
        ls0 = np.log(np.maximum(P[np.arange(len(P)), yi], 1e-6))
        m = d["window"].isin(test_w).to_numpy() & el
        bt = SC.Boot(games[m])
        info = {"n": int(m.sum())}
        for a in arms:
            dl = np.log(np.maximum(moved[a][np.arange(len(P)), yi], 1e-6)) - ls0
            info[a] = {"d_logscore": round(float(dl[m].mean()), 5), "ci": bt.ci(dl[m])}
        rep["markets"][mk] = {"all_outcome_2026_weeks": info, "gamma_last_window": {a: gam[a][test_w[-1]] for a in arms}}
        fin = d["window"].str.startswith("final_").to_numpy()
        idx = pd.Series(np.flatnonzero(fin), index=d.loc[fin, "key"].to_numpy())
        idx = idx[~idx.index.duplicated()]
        lm = np.flatnonzero((lines["market"] == mk).to_numpy())
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
    live, over, games = lines["po_live"].to_numpy(float), lines["over"].to_numpy(float), lines["game_id"].to_numpy()
    bt_all = C.Boot(games)
    lines["po_base_off"] = base_po
    for a, po in arm_po.items():
        ok = np.isfinite(po) & np.isfinite(base_po)
        xl = np.where(ok, C.expit(C.logit(live) + C.logit(np.where(ok, po, 0.5)) - C.logit(np.where(ok, base_po, 0.5))), live)
        lines[f"po_{a.split()[0]}"] = xl
        r = {"pooled": C.side_arm(bt_all, over, live, xl, "nfl"), "by_market": {}}
        for mk in MARKETS:
            m = (lines["market"] == mk).to_numpy()
            if np.isfinite(po[m]).any():
                r["by_market"][mk] = C.side_arm(C.Boot(games[m]), over[m], live[m], xl[m], "nfl")
        rep["arms"][a] = r
        p = r["pooled"]
        print(f"{a:16s} flips {p['flips']:5d} all {p['all']['base']['hit']}->{p['all']['arm']['hit']} d {p['all']['diff']} "
              f"{p['all']['ci']} | graded n {p['graded']['base']['n']}->{p['graded']['arm']['n']} hit "
              f"{p['graded']['base']['hit']}->{p['graded']['arm']['hit']} d {p['graded']['diff']} {p['graded']['ci']} "
              f"O {p['graded']['arm']['over_hit']} U {p['graded']['arm']['under_hit']} osh "
              f"{p['graded']['base']['over_share']}->{p['graded']['arm']['over_share']} "
              f"| {'PASS' if p['pass'] else 'FAIL ' + ','.join(p['failed'])}", flush=True)
    rep["rows"], rep["games"] = int(len(lines)), int(lines["game_id"].nunique())
    rep["rows_with_offline_pmf"] = int(np.isfinite(base_po).sum())
    (out_dir / "nfl_factors.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    lines.to_pickle(out_dir / "nfl_factor_lines.pkl")


if __name__ == "__main__":
    run(*sys.argv[1:5])
