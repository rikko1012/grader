"""Model worker jobs - reached ONLY through `python -m sports.worker train <job> ...` (SPORTS_TRAIN=1), i.e. the sports
worker process. Never the web process, never at import.

  train dataset nba [--budget 270] [--chunks N]    ESPN NBA backfill (resumable crawl, bounded chunks)
  train dataset nfl                                 nflverse NFL files -> tables
  train coverage                                    dataset coverage JSON
  train study [--sport nba|nfl]                     REGISTER.md study: train -> tune -> LOCKED test (once)
  train fit                                         fit the forward artifact (frozen config, all data to date)
  train forecast [nba nfl]                          sm-1 / mi-1 research forecasts for stored offers (writes store)
  train learn                                       settlement -> scorecard + diagnostic queue; retrain trigger check
  train snapshot                                    write the read-only API snapshot files (api_model.py reads them)
  train books [nba nfl]                             extra market sources (Polymarket, Odds API,
                                                    OddsPapi capture) -> offers (SPORTS_INGEST too; credit ledger)

Every job runs inside the foundation's Jobs ledger (idempotent job_key + time bucket, resumable cursor, timeout,
jittered retries) and the worker's memory cap.
"""
from __future__ import annotations

import argparse
import json
import sys

from .. import flags, store
from ..contracts import now_utc


def _print(x):
    print(json.dumps(x, indent=1, default=str), flush=True)


def _jobs(con):
    from ..worker import Jobs
    return Jobs(con)


def cmd_dataset(a):
    from . import datasets
    from ..worker import bucket, rss_mb
    hcon = datasets.connect()
    out = {}
    if a.league == "nba":
        datasets.nba_seed_days(hcon)
        with store.opened() as scon:
            jobs = _jobs(scon)
            jobs.timeout_s = max(jobs.timeout_s, a.budget + 60)
            for i in range(a.chunks):
                key = f"model:dataset:nba:{now_utc()}:{i}"   # the crawl table is the resume state
                r = jobs.run(key, "model-dataset", lambda ctx: datasets.nba_crawl(hcon, budget_s=a.budget))
                out[key] = r
                res = r.get("result") or {}
                print(json.dumps({"chunk": i, **res, "rss_mb": rss_mb()}), flush=True)
                if r["state"] == "done" and res.get("pending", 1) == 0:
                    break
    else:
        out = datasets.nfl_build(hcon, log=lambda x: print(json.dumps(x), flush=True))
    out["coverage"] = datasets.coverage(hcon)
    hcon.close()
    return out


def cmd_coverage(a):
    from . import datasets
    con = datasets.connect()
    try:
        return datasets.coverage(con)
    finally:
        con.close()


def main(argv=None):
    flags.require("SPORTS_TRAIN")
    p = argparse.ArgumentParser(prog="python -m sports.worker train")
    sub = p.add_subparsers(dest="job", required=True)
    s = sub.add_parser("dataset")
    s.add_argument("league", choices=("nba", "nfl"))
    s.add_argument("--budget", type=int, default=270)
    s.add_argument("--chunks", type=int, default=1)
    sub.add_parser("coverage")
    s = sub.add_parser("study")
    s.add_argument("--sport", choices=("nba", "nfl"))
    s.add_argument("--phase", choices=("tune", "test", "all"), default="all")
    sub.add_parser("fit")
    s = sub.add_parser("forecast")
    s.add_argument("leagues", nargs="*")
    sub.add_parser("learn")
    sub.add_parser("snapshot")
    s = sub.add_parser("books")
    s.add_argument("leagues", nargs="*")
    a = p.parse_args(argv)
    if a.job == "dataset":
        res = cmd_dataset(a)
    elif a.job == "coverage":
        res = cmd_coverage(a)
    else:
        from . import pipeline
        res = pipeline.run(a)
    _print(res)
    return res


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except flags.FlagOff as e:
        raise SystemExit(str(e))
