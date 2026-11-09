"""Write the lpf-1 live artifact (lol/lpf1_model.json.gz) - OFFLINE ONLY (needs lightgbm + sklearn + scipy from
requirements-test.txt; the server never imports this module).

  python -m lol.lpf1_train --db <read-only copy of production's LoL tables, lpf_data.Store shape> --cutoff 2026-11-09 \
      --out lol/lpf1_model.json.gz [--cache <dir>] [--rounds 600]

Exactly the research refit of lol/lpf_eval (variant W, live offset, decider model; the configuration frozen on DEV on
2026-11-05 and accepted on the forward lines 2026-11-09), done once at --cutoff: boosters, win / decider model and NB sizes
from targets on days strictly before the cutoff. No book line is read. Production-only history (no --extra), the version
accepted 2026-11-09.
"""
import argparse
import datetime as dt
import gzip
import json
import sys
import time

from . import lpf, lpf_data as D, lpf_eval as E

STATS_OUT = ("kills", "assists")
KEEP = ("split_feature", "threshold", "decision_type", "default_left", "missing_type", "leaf_value")


def compact(node):
    """Only what lol/lpf1_live.Trees reads (the dump also carries gains / counts / internal values)."""
    out = {k: node[k] for k in KEEP if k in node}
    if "split_feature" in node:
        out["left_child"], out["right_child"] = compact(node["left_child"]), compact(node["right_child"])
    return out


def dump(booster):
    d = booster.dump_model()
    return {"feature_names": d["feature_names"], "tree_info": [{"tree_structure": compact(t["tree_structure"])} for t in d["tree_info"]]}


def build_artifact(db, cutoff, cache=None, rounds=lpf.N_ROUNDS, log=print, stats=STATS_OUT):
    b = E.build(db, cache, log)
    fr = E.Frame(b)
    fr.gw = E.wins_per_game(fr, db, cache, log)
    cand = E.Candidate(fr, "W", rounds=rounds, log=log, offset=True)
    d = D.day_of(cutoff + "T00:00:00+00:00")
    wm = E.win_model_fit(fr, d)
    sizes = cand.fit_sizes(d, wm)
    boosters = cand.train(d)
    art = {"version": "lpf-1", "variant": "W", "offset_live": True, "rounds": rounds, "cutoff": cutoff,
           "written_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "data": {"player_games": b["db_rows"], "targets_before_cutoff": int((fr.day < d).sum()), "last_day": E.iso(fr.day.max())},
           "features": list(cand.feat), "booster_inputs": cand.names(),
           "win": {"coef": [float(c) for c in wm.coef], "p3coef": None if wm.p3coef is None else [float(c) for c in wm.p3coef]},
           "sizes": {f"{s}|{sc}|{role}": float(v) for (s, sc, role), v in sizes.items()},
           "boosters": {s: dump(boosters[s]) for s in stats}}
    return art, boosters, fr, cand


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--cutoff", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", default=None)
    ap.add_argument("--rounds", type=int, default=lpf.N_ROUNDS)
    a = ap.parse_args(argv)
    log = lambda *x: print(time.strftime("%H:%M:%S"), *x, file=sys.stderr, flush=True)  # noqa: E731
    art, *_ = build_artifact(a.db, a.cutoff, a.cache, a.rounds, log)
    raw = json.dumps(art, separators=(",", ":")).encode("utf-8")
    with open(a.out, "wb") as fh:
        fh.write(gzip.compress(raw, 9, mtime=0) if a.out.endswith(".gz") else raw)
    log(f"wrote {a.out}: {len(raw)} bytes json")


if __name__ == "__main__":
    main()
