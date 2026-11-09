"""sacc-1109: NFL book-line population joined to the frozen live sm-1 distribution (sm1_nfl.json applied offline) and
pre-game features. Read-only; reuses the proj_nfl_full harness loader.

  python -m research.sports_lab.sacc_1109.nfl_lines <history.db> <export.json> <out.pkl>
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from sports.model import dists, study
from sports.research import proj_nfl_full as H

FEATS = ("prior_n", "position", "home", "spread_home", "total_line", "snap_share", "ew_snap_share", "ew_targets",
         "ew_carries", "ew_pass_att", "rest_days", "team")


def build(history, export):
    frames, cf, meta = H.load(history)
    exp, rows, miss = H.book_rows(export, cf, meta)
    art = json.loads((H.ART / "sm1_nfl.json").read_text(encoding="utf-8"))
    recs = []
    for mk in H.LIVE_MK:
        sel = [r for r in rows if r["market"] == mk and r["status"] in ("win", "loss")]
        if not sel:
            continue
        idx = np.array([r["i"] for r in sel])
        f = study.from_config(art["markets"][mk])
        df = frames[f.hl[0]][idx]
        p, mu = study.pmf_for(f, df, mk, "nfl")
        lo, ks = dists.grid(mk)
        y = np.asarray(df[f"y_{mk}"], float)
        for j, r in enumerate(sel):
            o, u, pu = dists.line_probs(p[j], lo, r["line"])
            m = float(mu[j])
            sd = float(np.sqrt((p[j] * (ks - (p[j] * ks).sum()) ** 2).sum()))
            rec = {"game_id": r["match_key"], "player_id": r["player_id"], "market": mk, "line": float(r["line"]),
                   "book": r["book"], "forecast_at": r["forecast_at"], "start_at": r["start_at"],
                   "po_live": r["live_po"], "po_off": o / max(o + u, 1e-12), "mean": m, "sd": sd, "y": y[j],
                   "over": float(y[j] > r["line"]) if np.isfinite(y[j]) else np.nan,
                   "status_over": float((r["status"] == "win") == (r["side"] == "over")),
                   "key": str(cf["game_id"][r["i"]]) + "|" + str(cf["player_id"][r["i"]])}
            for c in FEATS:
                if c in cf:
                    v = cf[c][r["i"]]
                    rec[c] = v
            recs.append(rec)
    d = pd.DataFrame(recs)
    return d, miss


if __name__ == "__main__":
    d, miss = build(sys.argv[1], sys.argv[2])
    d.to_pickle(sys.argv[3])
    ok = d["over"].notna()
    print(len(d), d["game_id"].nunique(), miss, "box==service", float((d.loc[ok, "over"] == d.loc[ok, "status_over"]).mean()),
          "corr live/off", float(np.corrcoef(d["po_live"], d["po_off"])[0, 1]),
          "mad", float(np.abs(d["po_live"] - d["po_off"]).mean()))
    print([c for c in d.columns])
