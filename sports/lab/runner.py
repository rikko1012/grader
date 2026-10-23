"""The lab training process - SEPARATE from the web / service process:

  SPORTS_TRAIN=1 python -m sports.lab.runner          loop: train queued experiments, run queued replays
  SPORTS_TRAIN=1 python -m sports.lab.runner --once   drain the queue, then exit

One item at a time (training is CPU-bound; numpy); the worker memory cap applies when sports.worker.apply_memory_cap is
available. A crashed run is picked up again after jobs.STALE_S and resumes from its saved dataset; at most
jobs.MAX_ATTEMPTS attempts.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .. import flags
from . import jobs


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m sports.lab.runner")
    p.add_argument("--once", action="store_true")
    p.add_argument("--poll", type=float, default=5.0)
    a = p.parse_args(argv)
    flags.require("SPORTS_TRAIN")
    try:
        from ..worker import apply_memory_cap
        print(f"memory cap: {apply_memory_cap()}", file=sys.stderr)
    except Exception:                                   # noqa: BLE001
        pass
    while True:
        con = jobs.connect()
        try:
            out = jobs.work_once(con)
        finally:
            con.close()
        if out is not None:
            print(json.dumps({"kind": out[0], "ref": out[1], "result": out[2]}), flush=True)
            continue
        if a.once:
            return
        time.sleep(a.poll)


if __name__ == "__main__":
    try:
        main()
    except flags.FlagOff as e:
        raise SystemExit(str(e))
