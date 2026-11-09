"""sacc-1109 walk-forward runner: live-equivalent sm-1 (frozen config, refit at every refit date on games known then)
predicting EVERY played player-game with history (prior_n >= 1, eligible flag saved), plus the per-row pre-game inputs the
candidate layers read. In the final window the frozen live artifact sm1_nba.json is applied too.

Read-only on the data. Reuses the pnf-1 harness (sports/research/proj_nba_full/walkforward.py: sm1_frames, sm1_fit).

  python -m research.sports_lab.sacc_1109.wf run <history_ext.db> <outdir> [dev|final|all]
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

from sports.model import dists, model as smodel, study
from research.sports_lab.proj_nba_full import walkforward as W

MARKETS = ("points", "rebounds", "assists", "threes")
FEATS = ("ew_min", "l10_min", "l5_min", "w_min_n", "prior_n", "starter_lag", "starter_rate10", "absent_share",
         "absent_share_lag", "rest_days", "spread_abs", "team_spread", "pace_l10", "opp_pace_l10", "min", "ts", "home")


def run(hist_db, outdir, which="all"):
    os.makedirs(outdir, exist_ok=True)
    t0 = time.time()
    cfg, frames = W.sm1_frames(hist_db)
    print("frames", round(time.time() - t0), flush=True)
    frozen = json.loads((W.ART / "sm1_nba.json").read_text(encoding="utf-8"))
    for wtype, r0, r1 in W.windows(which):
        a, b = W._ep(r0), W._ep(r1)
        wname = f"{wtype}_{r0[:10]}"
        for mk in MARKETS:
            hl = tuple(cfg["markets"][mk]["half_lives"])
            live_df, pt_df = frames[hl]
            win = (pt_df["ts"] >= a) & (pt_df["ts"] < b) & (pt_df["prior_n"] >= 1)
            te = pt_df[win]
            if len(te) == 0:
                continue
            el = smodel.eligible(pt_df, mk)[win]
            keys = np.char.add(np.char.add(te["game_id"].astype(str), "|"), te["player_id"].astype(str))
            y = np.asarray(te[f"y_{mk}"], float)
            lo, _ = dists.grid(mk)
            feats = {f"f_{c}": np.asarray(te[c], float) for c in FEATS if c in te}
            fit = W.sm1_fit(cfg["markets"][mk], live_df, mk, a)
            p, mu = study.pmf_for(fit, te, mk, "nba")
            np.savez_compressed(os.path.join(outdir, f"sm1_{mk}__{wname}.npz"), keys=keys, y=y,
                                pmf=p.astype(np.float32), mean=np.asarray(mu, float), lo=lo, elig=el, **feats)
            if wtype == "final":
                fz = study.from_config(frozen["markets"][mk])
                p, mu = study.pmf_for(fz, te, mk, "nba")
                np.savez_compressed(os.path.join(outdir, f"sm1frozen_{mk}__{wname}.npz"), keys=keys, y=y,
                                    pmf=p.astype(np.float32), mean=np.asarray(mu, float), lo=lo, elig=el, **feats)
        print(wname, "done", round(time.time() - t0), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "all")
