"""hit-1109 task 2: NBA points teammates-out train/serve mismatch.

Live sm-1 points uses nba.teammate_absent_sameday. It is TRAINED on the realised share of rotation minutes that did not
play (every DNP reason, known only after the game) but SERVED from the ESPN injury report's Out list (or unknown) -
so live points sit below the inputs the model was trained on (live P(Over) 4.3 pts below the offline re-run; live Over
share 33.5% vs 47%).

Arms (same refit recipe as the sacc-1109 walk-forward: refit at 2026-10-24 and 2026-11-01 on games known then; the Nov 1
fit carries to Nov 9, so Nov 6-8 is the untouched fresh window):
  ref   current config, trained on the realised absences, predicted on the harness's pre-game approximation (coach's
        decisions counted as played) - the offline re-run the earlier study used
  lag   nba.teammate_absent_sameday -> nba.teammate_absent_lag (rotation teammates who missed the team's PREVIOUS game):
        trained and served on the SAME box-score definition - exact train/serve parity, no injury report needed
  drop  the feature removed (exact parity, nothing same-day)
  pers  trained AND scored on "missed this game AND the previous one" (persisting absences) - what an Out-list-only
        definition looks like when the report is complete; served live it would need the report (research only)

  python -m sports.research.hit_1109.nba_abs <history.db> <lines.pkl> <out.pkl>
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

os.environ.setdefault("PNF_FINAL_END", "2026-11-09T12:00:00Z")

from sports.model import dists, featurize, study  # noqa: E402
from sports.research.proj_nba_full import walkforward as W  # noqa: E402

MK = "points"
REFITS = ["2026-10-24T00:00:00Z", "2026-11-01T00:00:00Z", os.environ["PNF_FINAL_END"]]


def _persisting(orig):
    """Absent this game AND the team's previous game (the previous-game miss is pre-game knowledge)."""
    def fn(pg, tg, tgs, absences=None, up_games=()):
        a = orig(pg, tg, tgs, absences, up_games)
        # share computed per team-game; the persisting part is bounded by both today's and yesterday's share
        lag = featurize.gshift(a, tgs, 1)
        return np.where(np.isfinite(lag), np.minimum(a, lag), 0.0)
    return fn


def frames(hist_db, hl):
    con = sqlite3.connect(f"file:{hist_db}?mode=ro", uri=True)
    orig = featurize._team_absence
    out = {"live": featurize.nba_frame(con, h_min=hl[0], h_rate=hl[1], with_lags=False)}
    for nm, fn in (("pt", W._pt_absence(orig)), ("pers", _persisting(orig))):
        featurize._team_absence = fn
        try:
            out[nm] = featurize.nba_frame(con, h_min=hl[0], h_rate=hl[1], with_lags=False)
        finally:
            featurize._team_absence = orig
    con.close()
    return out


def run(hist_db, lines_pkl, out_pkl):
    cfg = json.loads((W.ART / "model_config_nba.json").read_text(encoding="utf-8"))["markets"][MK]
    hl = tuple(cfg["half_lives"])
    fr = frames(hist_db, hl)
    feats = list(cfg["features"])
    arms = {"ref": (feats, "live", "pt"),
            "lag": ([("nba.teammate_absent_lag" if f == "nba.teammate_absent_sameday" else f) for f in feats], "live", "live"),
            "drop": ([f for f in feats if f != "nba.teammate_absent_sameday"], "live", "live"),
            "pers": (feats, "pers", "pers")}
    lines = pd.read_pickle(lines_pkl)
    lines = lines[lines["market"] == MK].reset_index(drop=True)
    lo, ks = dists.grid(MK)
    keys = {nm: np.char.add(np.char.add(f["game_id"].astype(str), "|"), f["player_id"].astype(str)) for nm, f in fr.items()}
    info = {}
    for arm, (fl, trn, tst) in arms.items():
        po = np.full(len(lines), np.nan)
        mean = np.full(len(lines), np.nan)
        for a_s, b_s in zip(REFITS[:-1], REFITS[1:]):
            a, b = W._ep(a_s), W._ep(b_s)
            c = dict(cfg, features=fl)
            fit = W.sm1_fit(c, fr[trn], MK, a)
            te_df = fr[tst]
            win = (te_df["ts"] >= a) & (te_df["ts"] < b) & (te_df["prior_n"] >= 1)
            te = te_df[win]
            p, mu = study.pmf_for(fit, te, MK, "nba")
            idx = pd.Series(np.arange(len(te)), index=keys[tst][np.asarray(win)])
            idx = idx[~idx.index.duplicated()]
            m = lines["key"].isin(idx.index).to_numpy() & np.isnan(po)
            ii = idx.loc[lines.loc[m, "key"]].to_numpy()
            q = p[ii]
            L = lines.loc[m, "line"].to_numpy(float)[:, None]
            o, u = (q * (ks[None, :] > L)).sum(1), (q * (ks[None, :] < L)).sum(1)
            po[m] = o / np.maximum(o + u, 1e-12)
            mean[m] = np.asarray(mu, float)[ii]
            info.setdefault(arm, {})[a_s[:10]] = {"rows": int(m.sum()), "beta": [round(float(x), 4) for x in fit.beta]}
        lines[f"po_{arm}"], lines[f"mean_{arm}"] = po, mean
    lines = lines[np.isfinite(lines[[f"po_{a}" for a in arms]]).all(1)].reset_index(drop=True)
    lines.to_pickle(out_pkl)
    print(json.dumps(info)[:3000])
    print(len(lines), lines.groupby("period").size().to_dict())
    print(lines[["po_live", "po_base"] + [f"po_{a}" for a in arms]].mean().round(4).to_dict())


if __name__ == "__main__":
    run(*sys.argv[1:4])
