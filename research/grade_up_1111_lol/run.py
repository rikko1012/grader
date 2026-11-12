"""gu-lol-1: walk-forward refits of lpf-1 (+ one frozen feature group) on the read-only local copy. Research only.

  python research/grade_up_1111_lol/run.py <db> <lpf cache dir> <out dir> <group>[,<group>...]

group: base (lpf-1's 93 features), P1..P8, P11, ALL (= P1..P8), NOISE<seed> (placebo control: 5 noise columns). Refits 2026-07-01 .. 2026-11-01 (each on earlier days only);
writes <out>/preds_<group>.pkl = {target index: {stat: {scope: (W, M, S)}}} + the (series, player) -> index map.
"""
import os
import pickle
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
from lol import lpf, lpf_eval as E  # noqa: E402
import feats  # noqa: E402

REFITS = ["2026-07-01", "2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01"]
END = "2026-11-10"


def main():
    db, cache, out, groups = sys.argv[1:5]
    log = lambda *x: print(time.strftime("%H:%M:%S"), *x, file=sys.stderr, flush=True)
    os.makedirs(out, exist_ok=True)
    b = E.build(db, cache, log)
    xp = os.path.join(out, "extra.pkl")
    if os.path.exists(xp):
        xn, XX = pickle.load(open(xp, "rb"))
    else:
        xn, XX = feats.build(db, b, log)
        pickle.dump((xn, XX), open(xp, "wb"))
    for group in groups.split(","):
        if group == "extra":
            continue
        op = os.path.join(out, f"preds_{group}.pkl")
        if os.path.exists(op):
            continue
        if group == "base":
            keep = []
        elif group == "ALL":
            keep = [j for j, n in enumerate(xn) if not n.startswith("P11_")]
        else:
            keep = [j for j, n in enumerate(xn) if n.startswith(group + "_")]
            assert keep or group.startswith("NOISE"), group
        if group.startswith("NOISE"):
            # placebo control (added after the freeze, diagnostic only): 5 columns of pure noise, to measure how much the
            # hit rate moves when the booster is merely refitted with extra columns that carry no information
            keep = []
            NZ = np.random.default_rng(int(group[5:])).normal(size=(len(b["T"]), 5))
            bb = dict(b, X=np.hstack([b["X"], NZ]), names=list(b["names"]) + [f"NZ_{k}" for k in range(5)])
        else:
            bb = dict(b, X=np.hstack([b["X"], XX[:, keep]]), names=list(b["names"]) + [xn[j] for j in keep])
        fr = E.Frame(bb)
        fr.gw = E.wins_per_game(fr, db, cache, log)
        cand = E.Candidate(fr, "W", rounds=lpf.N_ROUNDS, log=log, offset=True)
        preds = E.walk_forward(fr, cand, REFITS, END, log)
        meta = preds.pop("_meta")
        pickle.dump({"preds": preds, "meta": meta, "features": [xn[j] for j in keep],
                     "keys": {(t["match"], t["pid"]): i for i, t in enumerate(fr.T)}}, open(op, "wb"))
        log("done", group, len(preds))


if __name__ == "__main__":
    main()
