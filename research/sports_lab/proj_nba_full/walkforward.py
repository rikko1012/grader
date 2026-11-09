"""Walk-forward of the NBA projection DISTRIBUTION on EVERY historical played player-game (no book lines needed):
live-equivalent sm-1 vs pnf-1 (research/sports_lab/proj_nba_full_model.py). Read-only on the data; writes prediction files + JSON.

Baseline ("sm1"): the frozen sm-1 configuration (model_config_nba.json: half-lives, feature set, family, cal-1 - the
live code path sports.model.study) REFIT at every refit date on the rows known then, exactly as forward.fit does. Its
training frame is the live one (featurize.nba_frame: same-game teammate absence from the box, as sm-1 was trained);
its TEST rows use the pre-game-known absence list (injury/rest/suspension/personal DNPs + rotation players missing from
the box) - the offline stand-in for the injury report the live forward path reads. In the final window the baseline is
additionally the frozen live artifact sm1_nba.json itself ("sm1_frozen", fit 2026-10-23T23:00Z).

Protocol:
  * refit dates: the 1st of every month with games (2024-10 .. 2026-06 = DEVELOPMENT window) and 2026-10-24 12:00Z,
    2026-11-01 12:00Z (FINAL window, games 2026-10-24 .. 2026-11-05 - untouched while iterating; also the window of
    the settled book-line props). A refit uses only games with start + 4 h <= the refit time; it predicts the games
    from the refit time to the next refit.
  * evaluated rows: played player-games eligible under sm-1 (>= 5 prior played games, last-10 minutes >= 15), both
    models on exactly the same rows. Distributions are conditional on playing (props on DNPs are void).
  * clustered CIs: whole-game bootstrap (B draws) of per-game sums.

  python -m research.sports_lab.proj_nba_full.walkforward run   <hist.db> <frame.pkl> <outdir> [dev|final|all]
  python -m research.sports_lab.proj_nba_full.walkforward report <outdir> <out.json>
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from sports.model import featurize, model as smodel, study
from research.sports_lab import proj_nba_full_model as pnf
from research.sports_lab.proj_nba_full import data as D

ART = Path(__file__).resolve().parents[3] / "sports" / "model" / "artifacts"
KNOWN_H = 4
DEV_REFITS = [f"{y}-{m:02d}-01T00:00:00Z" for y, m in
              [(2024, 10), (2024, 11), (2024, 12), (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2025, 5), (2025, 6),
               (2025, 10), (2025, 11), (2025, 12), (2026, 1), (2026, 2), (2026, 3), (2026, 4), (2026, 5), (2026, 6)]]
DEV_END = "2026-07-01T00:00:00Z"
FINAL_REFITS = ["2026-10-24T00:00:00Z", "2026-11-01T00:00:00Z"]
# end of the FINAL window; the 2026-11-06 fresh-export rerun sets PNF_FINAL_END=2026-11-07T00:00:00Z so the one newly
# settled Nov 6 UTC game is scored (the default keeps every earlier result reproducible)
FINAL_END = os.environ.get("PNF_FINAL_END", "2026-11-06T00:00:00Z")
B = 500


def _ep(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def windows(which):
    out = []
    if which in ("dev", "all"):
        r = DEV_REFITS + [DEV_END]
        out += [("dev", r[i], r[i + 1]) for i in range(len(r) - 1)]
    if which in ("final", "all"):
        r = FINAL_REFITS + [FINAL_END]
        out += [("final", r[i], r[i + 1]) for i in range(len(r) - 1)]
    return out


# --------------------------------------------------------------------------------------------------------- sm-1
def _pt_absence(orig):
    """featurize._team_absence with coach's-decision DNPs counted as NOT known absent (pre-game-known list)."""
    def fn(pg, tg, tgs, absences=None, up_games=()):
        pg2 = pg.copy()
        reason = np.asarray(pg2["dnp_reason"], dtype=object)
        coach = (np.asarray(pg2["played"]) == 0) & np.isin(reason, ["COACH'S DECISION", "DNP"])
        pg2["played"] = np.where(coach, 1.0, np.asarray(pg2["played"], float))
        return orig(pg2, tg, tgs, absences, up_games)
    return fn


def sm1_frames(hist_db):
    con = sqlite3.connect(f"file:{hist_db}?mode=ro", uri=True)
    cfg = json.loads((ART / "model_config_nba.json").read_text(encoding="utf-8"))
    out = {}
    orig = featurize._team_absence
    for hl in sorted({tuple(c["half_lives"]) for c in cfg["markets"].values()}):
        live = featurize.nba_frame(con, h_min=hl[0], h_rate=hl[1], with_lags=False)
        featurize._team_absence = _pt_absence(orig)
        try:
            pt = featurize.nba_frame(con, h_min=hl[0], h_rate=hl[1], with_lags=False)
        finally:
            featurize._team_absence = orig
        assert np.array_equal(live["game_id"], pt["game_id"]) and np.array_equal(live["player_id"], pt["player_id"])
        out[hl] = (live, pt)
    con.close()
    return cfg, out


def sm1_fit(cfg_m, df, mk, cut):
    el = smodel.eligible(df, mk)
    tr = df[el & np.isfinite(df.f(f"y_{mk}")) & (df["ts"] + KNOWN_H * 3600 <= cut)]
    f = study.Fitted(mk, tuple(cfg_m["half_lives"]))
    study.mean_model(f, tr, tr, mk, "nba", list(cfg_m["features"]))
    study.fit_family_params(f, tr, mk, "nba")
    f.family = cfg_m["family"]
    f.cal = tuple(cfg_m["cal"])
    return f


def _save(outdir, name, wname, keys, y, pmf, mean, lo, extra=None):
    np.savez_compressed(Path(outdir) / f"{name}__{wname}.npz", keys=np.asarray(keys, dtype=str), y=y,
                        pmf=pmf.astype(np.float32), mean=mean, lo=lo, **(extra or {}))


def run(hist_db, frame_pkl, outdir, which="all", models=("sm1", "pnf")):
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    cfg, frames = sm1_frames(hist_db)
    print("sm-1 frames", round(time.time() - t0), flush=True)
    any_pt = next(iter(frames.values()))[1]
    elig = {mk: smodel.eligible(frames[tuple(cfg["markets"][mk]["half_lives"])][1], mk) for mk in pnf.STATS}
    f = D.add_pos(pd.read_pickle(frame_pkl))
    f["key"] = f["game_id"] + "|" + f["player_id"]
    frozen = json.loads((ART / "sm1_nba.json").read_text(encoding="utf-8"))
    for wtype, r0, r1 in windows(which):
        a, b = _ep(r0), _ep(r1)
        wname = f"{wtype}_{r0[:10]}"
        # ---------------------------------------------------------------- sm-1 (refit) and the frozen artifact
        for mk in pnf.STATS:
            hl = tuple(cfg["markets"][mk]["half_lives"])
            live_df, pt_df = frames[hl]
            win = (pt_df["ts"] >= a) & (pt_df["ts"] < b)
            # final window: every played row with history (the book-line props include non-eligible players);
            # the all-outcome comparison keeps the eligible ones (flag saved)
            te_m = win & ((pt_df["prior_n"] >= 1) if wtype == "final" else elig[mk])
            te = pt_df[te_m]
            el_te = elig[mk][te_m]
            if len(te) == 0:
                continue
            keys = np.char.add(np.char.add(te["game_id"].astype(str), "|"), te["player_id"].astype(str))
            y = np.asarray(te[f"y_{mk}"], float)
            lo, _ = pnf.SUPPORT[mk]
            if "sm1" in models:
                fit = sm1_fit(cfg["markets"][mk], live_df, mk, a)
                p, mu = study.pmf_for(fit, te, mk, "nba")
                _save(outdir, f"sm1_{mk}", wname, keys, y, p, np.asarray(mu, float), lo, {"elig": el_te})
                if wtype == "final":
                    fz = study.from_config(frozen["markets"][mk])
                    p, mu = study.pmf_for(fz, te, mk, "nba")
                    _save(outdir, f"sm1frozen_{mk}", wname, keys, y, p, np.asarray(mu, float), lo, {"elig": el_te})
        print(wname, "sm1 done", round(time.time() - t0), flush=True)
        # ---------------------------------------------------------------- pnf-1
        if "pnf" in models:
            tr = f[(f["prior_n"] >= 1) & (f["ts"] + KNOWN_H * 3600 <= a)]
            te = f[(f["prior_n"] >= 1) & (f["ts"] >= a) & (f["ts"] < b)]
            if len(te):
                m = pnf.Model(D.FEATURES).fit(tr)
                pr = m.predict(te)
                for mk, c in pnf.STATS.items():
                    p, mu, lo = pr[mk]
                    _save(outdir, f"pnf_{mk}", wname, te["key"].to_numpy(), te[c].to_numpy(float), p, mu, lo,
                          {"m_hat": pr["_minutes"], "min": te["min"].to_numpy(float)})
                print(wname, "pnf done", len(tr), len(te), m.best_iter, round(time.time() - t0), flush=True)
                # a book-line prop is scored by the latest refit made BEFORE its forecast_at: props for later games
                # forecast before the next refit need this refit's prediction too ("xtra", never in the all-outcome
                # comparison, which uses each game once)
                if wtype == "final" and r1 != FINAL_END:
                    tx = f[(f["prior_n"] >= 1) & (f["ts"] >= b) & (f["ts"] < _ep(FINAL_END))]
                    if len(tx):
                        px = m.predict(tx)
                        for mk, c in pnf.STATS.items():
                            p, mu, lo = px[mk]
                            _save(outdir, f"pnf_{mk}", f"xtra_{r0[:10]}", tx["key"].to_numpy(),
                                  tx[c].to_numpy(float), p, mu, lo)
    del any_pt


# ---------------------------------------------------------------------------------------------------------- report
class Boot:
    def __init__(self, games, seed=11, b=B):
        self.ug, self.g = np.unique(games, return_inverse=True)
        rng = np.random.default_rng(seed)
        G = len(self.ug)
        self.C = np.stack([np.bincount(rng.integers(0, G, G), minlength=G) for _ in range(b)]).astype(float)

    def mean_ci(self, x, mask=None):
        x = np.asarray(x, float)
        w = np.ones(len(x)) if mask is None else mask.astype(float)
        s = self.C @ np.bincount(self.g, weights=x * w, minlength=len(self.ug))
        n = self.C @ np.bincount(self.g, weights=w, minlength=len(self.ug))
        v = s / np.maximum(n, 1e-9)
        return [round(float(np.percentile(v, 2.5)), 5), round(float(np.percentile(v, 97.5)), 5)]


THRESH_STEP = {"points": (-3, -2, -1, 0, 1, 2, 3), "rebounds": (-2, -1, 0, 1, 2), "assists": (-2, -1, 0, 1, 2),
               "threes": (-1, 0, 1)}


def _load(outdir, name):
    parts = sorted(Path(outdir).glob(f"{name}__*.npz"))
    out = {}
    for p in parts:
        z = np.load(p)
        out[p.stem.split("__")[1]] = {k: z[k] for k in z.files}
    return out


def threshold_calibration(pmf, lo, y, centre, steps):
    """P(X > t) vs outcome at half-integer thresholds t = round(centre) + 0.5 + step (centre = a model-neutral typical
    line: the player's prior EW-10 per-game average). Rows x thresholds pooled; side = the more likely side."""
    stated, hit, over_side, rows = [], [], [], []
    base = np.floor(np.asarray(centre, float)) + 0.5
    for s in steps:
        t = np.maximum(base + s, 0.5)
        po = pnf.p_over(pmf, lo, t)
        ov = (y > t).astype(float)
        side_o = po >= 0.5
        stated.append(np.where(side_o, po, 1 - po))
        hit.append(np.where(side_o, ov, 1 - ov))
        over_side.append(side_o)
        rows.append(np.arange(len(y)))
    return np.concatenate(stated), np.concatenate(hit), np.concatenate(over_side), np.concatenate(rows)


def _cal_summary(st, hi, ov, bt_rows, bt):
    out = {}
    for name, m in (("all", np.ones(len(st), bool)), ("over", ov), ("under", ~ov)):
        out[name] = {"n": int(m.sum()), "stated": round(float(st[m].mean()), 4), "hit": round(float(hi[m].mean()), 4),
                     "gap": round(float(st[m].mean() - hi[m].mean()), 4)}
    bins = []
    for side, m0 in (("over", ov), ("under", ~ov)):
        for lo in np.arange(0.5, 1.0, 0.05):
            m = m0 & (st >= lo) & (st < lo + 0.05 + (1e-9 if lo >= 0.95 else 0))
            if m.sum() >= 50:
                bins.append({"side": side, "bin": f"{lo:.2f}", "n": int(m.sum()), "stated": round(float(st[m].mean()), 4),
                             "hit": round(float(hi[m].mean()), 4)})
    out["bins"] = bins
    out["max_bin_gap"] = round(max(abs(b["stated"] - b["hit"]) for b in bins), 4) if bins else None
    return out


def compare_window(outdir, wtype, base_name="sm1", cand="pnf", seed=11):
    """All-outcome comparison on the rows both models scored in windows of type wtype."""
    rep = {}
    F = pd.read_pickle(os.environ["PNF_FRAME"]) if os.environ.get("PNF_FRAME") else None
    centre_map = None
    if F is not None:
        F["key"] = F["game_id"] + "|" + F["player_id"]
        centre_map = F.set_index("key")
    pooled = {"base": [], "cand": [], "games": []}
    for mk, c in pnf.STATS.items():
        A = _load(outdir, f"{base_name}_{mk}")
        C = _load(outdir, f"{cand}_{mk}")
        ks, ya, pa, pc, ma, mc, lo = [], [], [], [], [], [], None
        for w in sorted(A):
            if not w.startswith(wtype) or w not in C:
                continue
            a, cc = A[w], C[w]
            ci = {k: i for i, k in enumerate(cc["keys"])}
            idx = np.array([ci.get(k, -1) for k in a["keys"]])
            ok = idx >= 0
            if "elig" in a:                                  # sm-1-eligible rows only
                ok &= a["elig"].astype(bool)
            ks.append(a["keys"][ok]); ya.append(a["y"][ok]); pa.append(a["pmf"][ok]); pc.append(cc["pmf"][idx[ok]])
            ma.append(a["mean"][ok]); mc.append(cc["mean"][idx[ok]]); lo = int(a["lo"])
            assert np.allclose(a["y"][ok], cc["y"][idx[ok]])
        if not ks:
            continue
        keys = np.concatenate(ks); y = np.concatenate(ya); P_a = np.concatenate(pa); P_c = np.concatenate(pc)
        M_a = np.concatenate(ma); M_c = np.concatenate(mc)
        games = np.array([k.split("|")[0] for k in keys])
        bt = Boot(games, seed)
        la, lc = pnf.logscore(P_a, lo, y), pnf.logscore(P_c, lo, y)
        ea, ec = np.abs(M_a - y), np.abs(M_c - y)
        rng = np.random.default_rng(seed)
        u = rng.random(len(y))
        pits = {}
        for nm, P in (("base", P_a), ("cand", P_c)):
            l, h = pnf.pit_bounds(P, lo, y)
            pit = l + u * (h - l)
            pits[nm] = np.round(np.histogram(pit, bins=10, range=(0, 1))[0] / len(pit), 4).tolist()
        r = {"n": int(len(y)), "games": int(len(np.unique(games))),
             "logscore_base": round(float(la.mean()), 5), "logscore_cand": round(float(lc.mean()), 5),
             "d_logscore": round(float((lc - la).mean()), 5), "d_logscore_ci": bt.mean_ci(lc - la),
             "mae_base": round(float(ea.mean()), 4), "mae_cand": round(float(ec.mean()), 4),
             "d_mae": round(float((ec - ea).mean()), 4), "d_mae_ci": bt.mean_ci(ec - ea),
             "bias_base": round(float((M_a - y).mean()), 4), "bias_cand": round(float((M_c - y).mean()), 4),
             "pit_deciles_base": pits["base"], "pit_deciles_cand": pits["cand"]}
        if centre_map is not None:
            centre = centre_map.loc[keys, f"{c}_pg_ew10"].to_numpy(float)
            centre = np.where(np.isfinite(centre), centre, M_a)
            for nm, P in (("base", P_a), ("cand", P_c)):
                st, hi, ov, _ = threshold_calibration(P, lo, y, centre, THRESH_STEP[mk])
                r[f"thr_cal_{nm}"] = _cal_summary(st, hi, ov, None, bt)
        rep[mk] = r
        pooled["base"].append(la); pooled["cand"].append(lc); pooled["games"].append(games)
        print(wtype, mk, r["n"], "dLS", r["d_logscore"], r["d_logscore_ci"], "dMAE", r["d_mae"], r["d_mae_ci"], flush=True)
    if pooled["base"]:
        la = np.concatenate(pooled["base"]); lc = np.concatenate(pooled["cand"]); g = np.concatenate(pooled["games"])
        bt = Boot(g, seed)
        rep["pooled"] = {"n": int(len(la)), "d_logscore": round(float((lc - la).mean()), 5),
                         "d_logscore_ci": bt.mean_ci(lc - la)}
    return rep


def report(outdir, out_json):
    rep = {"generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "protocol": __doc__.split("Protocol:")[1].split("python -m")[0].strip(),
           "dev_sm1_refit_vs_pnf": compare_window(outdir, "dev"),
           "final_sm1_refit_vs_pnf": compare_window(outdir, "final"),
           "final_sm1_frozen_vs_pnf": compare_window(outdir, "final", base_name="sm1frozen"),
           "final_sm1_frozen_vs_sm1_refit": compare_window(outdir, "final", base_name="sm1frozen", cand="sm1")}
    Path(out_json).write_text(json.dumps(rep, indent=1, default=float), encoding="utf-8")
    print("wrote", out_json)


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "all")
    else:
        report(sys.argv[2], sys.argv[3])
