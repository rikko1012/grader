"""gu-1111 before / after preview of one projection arm on the settled real-line rows (sides and letters changed,
Over / Under split, examples). Research only - the arm is not implemented in the forward job.

  python -m research.sports_lab.grade_up_1111.preview <nba_factor_lines.pkl> <frame.pkl> <arm col, e.g. po_P7> <from start_at> <out.json>
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

from research.sports_lab.grade_up_1111 import common as C


def run(lines_pkl, frame_pkl, col, start_from, out_json, league="nba"):
    d = pd.read_pickle(lines_pkl)
    d = d[d["start_at"] >= start_from].reset_index(drop=True)
    f = pd.read_pickle(frame_pkl)
    f["key"] = f["game_id"] + "|" + f["player_id"]
    f = f.drop_duplicates("key").set_index("key")
    d = d.join(f[["name", "team", "opp", "b2b", "games_last_7d", "days_rest", "min_ew10"]], on="key")
    ov0, pc0 = C.side_of(d["p_over"])
    ov1, pc1 = C.side_of(d[col])
    q0, q1 = C.printed(pc0, league), C.printed(pc1, league)
    L0, L1 = C.band_idx(q0), C.band_idx(q1)
    h0, h1 = np.where(ov0, d["over"], 1 - d["over"]), np.where(ov1, d["over"], 1 - d["over"])
    flip, up, down = ov0 != ov1, (L1 > L0) & (ov0 == ov1), (L1 < L0) & (ov0 == ov1)

    def dist(L, ov):
        return {C.LET[k]: {"over": int(((L == k) & ov).sum()), "under": int(((L == k) & ~ov).sum())} for k in (3, 2, 1, 0)}

    def hit(h, m):
        return None if not m.any() else round(100 * float(h[m].mean()), 1)
    out = {"arm": col, "from": start_from, "rows": int(len(d)), "games": int(d["game_id"].nunique()),
           "sides_flipped": int(flip.sum()), "to_over": int((flip & ov1).sum()), "to_under": int((flip & ~ov1).sum()),
           "flipped_hit_before_after": [hit(h0, flip), hit(h1, flip)],
           "letters_up_same_side": int(up.sum()), "letters_down_same_side": int(down.sum()),
           "unchanged": int((~flip & (L0 == L1)).sum()),
           "before": {"letters": dist(L0, ov0), "over_share_all": round(100 * float(ov0.mean()), 1),
                      "graded": int((L0 >= 1).sum()), "graded_over_share": round(100 * float(ov0[L0 >= 1].mean()), 1),
                      "hit_all": hit(h0, np.ones(len(d), bool)), "hit_graded": hit(h0, L0 >= 1),
                      "hit_over": hit(h0, ov0), "hit_under": hit(h0, ~ov0)},
           "after": {"letters": dist(L1, ov1), "over_share_all": round(100 * float(ov1.mean()), 1),
                     "graded": int((L1 >= 1).sum()), "graded_over_share": round(100 * float(ov1[L1 >= 1].mean()), 1),
                     "hit_all": hit(h1, np.ones(len(d), bool)), "hit_graded": hit(h1, L1 >= 1),
                     "hit_over": hit(h1, ov1), "hit_under": hit(h1, ~ov1)},
           "newly_graded_hit": hit(h1, (L1 >= 1) & (L0 == 0)), "newly_graded_n": int(((L1 >= 1) & (L0 == 0)).sum()),
           "lost_grade_hit_before": hit(h0, (L1 == 0) & (L0 >= 1)), "lost_grade_n": int(((L1 == 0) & (L0 >= 1)).sum())}
    ch = np.flatnonzero(flip | (L0 != L1))
    order = ch[np.argsort(-np.abs(d[col].to_numpy(float)[ch] - d["p_over"].to_numpy(float)[ch]))]
    ex = []
    seen = set()
    for i in order:
        r = d.iloc[i]
        k = (r["player_id"], r["stat"])
        if k in seen:
            continue
        seen.add(k)
        ex.append({"player": r["name"], "team": r["team"], "opp": r["opp"], "date": r["start_at"][:10], "stat": r["stat"],
                   "book": r["book"], "line": float(r["line"]),
                   "before": f"{C.LET[L0[i]]} {'Over' if ov0[i] else 'Under'} {100 * q0[i]:.1f}%",
                   "after": f"{C.LET[L1[i]]} {'Over' if ov1[i] else 'Under'} {100 * q1[i]:.1f}%",
                   "b2b": int(r["b2b"]) if r["b2b"] == r["b2b"] else None,
                   "games_last_7d": int(r["games_last_7d"]) if r["games_last_7d"] == r["games_last_7d"] else None,
                   "ew_min": round(float(r["min_ew10"]), 1) if r["min_ew10"] == r["min_ew10"] else None,
                   "result": "Over" if r["over"] else "Under",
                   "before_hit": bool(h0[i]), "after_hit": bool(h1[i])})
        if len(ex) >= 12:
            break
    out["examples_largest_changes"] = ex
    json.dump(out, open(out_json, "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k != "examples_largest_changes"}, indent=1))
    for e in ex:
        print(e)


if __name__ == "__main__":
    run(*sys.argv[1:6])
