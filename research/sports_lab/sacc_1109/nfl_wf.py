"""sacc-1109 NFL walk-forward runner: live-equivalent sm-1 (model_config_nfl.json, refit at every cutoff on rows known
then - the proj_nfl_full harness's fit_baseline) on every player-game with history in the five live markets, plus the
pre-game role inputs the candidate layers read. Final window (2026 season, weekly) also applies the frozen live
artifact sm1_nfl.json.

  python -m research.sports_lab.sacc_1109.nfl_wf <history.db> <outdir>
"""
from __future__ import annotations

import json
import os
import sys
import time
import warnings

import numpy as np

from sports.model import dists, study
from sports.research import proj_nfl_full as H

FEATS = ("prior_n", "n_season", "home", "spread_home", "team_spread", "total_line", "implied_total", "ew_offense_pct",
         "l3_snap", "lag1_offense_pct", "ew_targets", "lag1_targets", "e2_targets", "e8_targets", "ew_carries",
         "lag1_carries", "e2_carries", "e8_carries", "ew_pass_att", "lag1_pass_att", "ew_target_share",
         "back_from_absence", "team_change", "qb_change_lag", "rest_days", "gap_days", "ts")
POS = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}


def run(history, outdir):
    warnings.filterwarnings("ignore")
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    frames, cf, meta = H.load(history)
    ts = cf.f("ts")
    art = json.loads((H.ART / "sm1_nfl.json").read_text(encoding="utf-8"))
    pos = np.array([POS.get(p, 4) for p in cf["position"]], float)
    keys_all = np.char.add(np.char.add(cf["game_id"].astype(str), "|"), cf["player_id"].astype(str))
    for stage, a, b in H.cutoffs("all"):
        ea, eb = a.timestamp(), b.timestamp()
        known = ts + H.KNOWN_H * 3600 <= ea
        fits = H.fit_baseline(frames, known)
        wname = f"{stage}_{a.strftime('%Y-%m-%d')}"
        for mk in H.LIVE_MK:
            f = fits[mk]
            df_all = frames[f.hl[0]]
            win = (ts >= ea) & (ts < eb) & (cf.f("prior_n") >= 1) & np.isfinite(df_all.f(f"y_{mk}"))
            idx = np.flatnonzero(win)
            if len(idx) == 0:
                continue
            pop = H.population(cf, mk)[idx]
            df = df_all[idx]
            p, mu = study.pmf_for(f, df, mk, "nfl")
            lo, _ = dists.grid(mk)
            feats = {f"f_{c}": np.asarray(cf.f(c)[idx], float) for c in FEATS if c in cf}
            feats["f_pos"] = pos[idx]
            y = np.asarray(df.f(f"y_{mk}"), float)
            np.savez_compressed(os.path.join(outdir, f"sm1_{mk}__{wname}.npz"), keys=keys_all[idx], y=y,
                                pmf=p.astype(np.float32), mean=np.asarray(mu, float), lo=lo, elig=pop, **feats)
            if stage == "final":
                fz = study.from_config(art["markets"][mk])
                p, mu = study.pmf_for(fz, df, mk, "nfl")
                np.savez_compressed(os.path.join(outdir, f"sm1frozen_{mk}__{wname}.npz"), keys=keys_all[idx], y=y,
                                    pmf=p.astype(np.float32), mean=np.asarray(mu, float), lo=lo, elig=pop, **feats)
        print(wname, "done", round(time.time() - t0), flush=True)
    # map export ids (espn) -> history keys for the book-line join
    json.dump({"games": meta["games"], "espn_ids": meta["espn_ids"]}, open(os.path.join(outdir, "idmap.json"), "w"))


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
