"""hit-1109: freeze the nrm-1 shadow artifact (REGISTER A12) and check train/serve parity.

fit    gamma per market (rush_att, rec) by the sacc-1109 rm-1 recipe (Poisson deviance, offset = the out-of-sample
       sm-1 mean, ridge 1e-2) on EVERY walk-forward window through 2026 week 9 (dev 2023-09 .. 2026-02 monthly refits,
       2026 weekly refits with the frozen live artifact). Box scores only; no book line is read.
parity the production inputs (sports.model.nrm1.role_inputs on the LIVE sm-1 frame at the market's own half-life) vs
       the research inputs (nfl_cand.mean_Z on proj_nfl_full.augment of the h=4 frame), row for row.

  python -m research.sports_lab.hit_1109.nrm1_fit fit <nflwf_dir> <out_artifact.json>
  python -m research.sports_lab.hit_1109.nrm1_fit parity <history.db> <nflwf_dir>
"""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np

from sports.model import featurize, nrm1, study
from research.sports_lab.sacc_1109 import cand as C, nfl_cand as N


def fit(wf_dir, out_path):
    from sports.model import forward
    sm1 = forward.load_artifact("nfl")
    markets = {}
    for mk in nrm1.MARKETS:
        d, P, lo = N.load(wf_dir, mk)
        Z = N.mean_Z(d, mk)
        y, mu = d["y"].to_numpy(float), d["mean"].to_numpy(float)
        ok = np.isfinite(y) & np.isfinite(mu)
        g = C.fit_mean(np.maximum(y[ok], 0), np.maximum(np.abs(mu[ok]), 0.05), Z[ok])
        markets[mk] = {"gamma": [round(float(x), 6) for x in g], "fit_rows": int(ok.sum()),
                       "windows": sorted(d["window"].unique().tolist())[-1],
                       "gamma_named": dict(zip(nrm1.Z_NAMES, [round(float(x), 4) for x in g]))}
        print(mk, markets[mk]["fit_rows"], markets[mk]["gamma_named"])
    art = {"model_version": nrm1.VERSION, "sport": "nfl", "pairs_with_sm1": sm1["sha"], "z_names": list(nrm1.Z_NAMES),
           "fitted_on": "sacc-1109 walk-forward out-of-sample sm-1 predictions, every window through 2026-11-03 "
                        "(NFL 2026 week 9); box scores only, no book line",
           "markets": markets, "role": "challenger (shadow) - REGISTER.md A12; not displayed, not graded"}
    art["sha"] = nrm1._sha(art["markets"])[:16]
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(art, fh, indent=1, sort_keys=True)
    print("sha", art["sha"])


def parity(hist_db, wf_dir):
    con = sqlite3.connect("file:" + hist_db.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    import json as _j
    from sports.model.forward import ART_DIR  # noqa: F401
    cfg = _j.load(open(nrm1.ART_DIR / "model_config_nfl.json"))
    for mk in nrm1.MARKETS:
        hl = cfg["markets"][mk]["half_lives"][0]
        df = study._nfl_fill(featurize.nfl_frame(con, h_opp=hl, with_lags=False))
        Zp = nrm1.role_inputs(df, mk)
        keys = np.char.add(np.char.add(df["game_id"].astype(str), "|"), df["player_id"].astype(str))
        pos = {k: i for i, k in enumerate(keys)}
        d, _, _ = N.load(wf_dir, mk)
        Zr = N.mean_Z(d, mk)
        ii = np.array([pos.get(k, -1) for k in d["key"]])
        m = ii >= 0
        diff = np.abs(Zp[ii[m]] - Zr[m])
        print(mk, "rows", int(m.sum()), "of", len(d), "max |diff| per column", np.round(diff.max(0), 6).tolist(),
              "rows with any diff > 1e-6:", int((diff.max(1) > 1e-6).sum()))


if __name__ == "__main__":
    {"fit": fit, "parity": parity}[sys.argv[1]](*sys.argv[2:4])
