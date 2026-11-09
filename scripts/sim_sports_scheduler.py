"""Replay of the 2026-11-09 sports scheduler stall (deploy c31637d, uptime ~72 min): deterministic tick simulation of
sports/scheduler.py with the production job table (Render Standard: cgroup 2 GB -> std plan, two heavy slots; owner
budget SPORTS_CHILD_BUDGET_MB=1200 -> cap_scale 1.8) and live run times (sports/tests/sched_sim.py).

    python scripts/sim_sports_scheduler.py            # this checkout
    python scripts/sim_sports_scheduler.py --ref c31637d --ref ecfa81a     # + older scheduler.py copies from git

For each scheduler it runs four scenarios over 6 simulated hours and prints, per job, the starts and the longest
due-but-not-started wait:
  ok        model-forward succeeds (c31637d fixed its SQLITE_BUSY_SNAPSHOT exit 1)
  failing   model-forward exits 1 every run (ecfa81a: 4 x exit 1 -> backoff 1 h, 2 h, 4 h, 8 h)
  no-fwd    SPORTS_FORECAST off (no exclusive job at all)
  first     only the first hour (the incident window)
Nothing is spawned; no store / network access."""
from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sports import scheduler as current          # noqa: E402
from sports.tests import sched_sim as sim        # noqa: E402

FOCUS = ("picks-ingest", "slips-ingest", "picks-settle", "pregame-news", "games-backfill", "books", "model-forward",
         "settle", "settle-post", "player-context", "history", "grades-daily", "archive", "mapping-audit", "sheets",
         "results-public", "prebuilt", "ui-snapshots-nba", "ui-snapshots-nfl", "ingest-nba", "ingest-nfl")


def load_ref(ref, tmp):
    src = subprocess.run(["git", "show", f"{ref}:sports/scheduler.py"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    p = Path(tmp) / f"sched_{ref}.py"
    p.write_text(src, encoding="utf-8")
    name = f"sports._sched_{ref}"
    spec = importlib.util.spec_from_file_location(name, p, submodule_search_locations=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "sports"
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def scenarios():
    return {"ok": dict(), "failing": dict(rc={"model-forward": 1}), "no-fwd": dict(env={"SPORTS_FORECAST": ""}),
            "first": dict(hours=1.2)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", action="append", default=[], help="git ref of an older sports/scheduler.py")
    ap.add_argument("--hours", type=float, default=6.0)
    a = ap.parse_args(argv)
    mods = [("this checkout", current)]
    with tempfile.TemporaryDirectory() as tmp:
        for r in a.ref:
            mods.append((r, load_ref(r, tmp)))
        for label, mod in mods:
            for sc, kw in scenarios().items():
                kw = dict(kw)
                hours = kw.pop("hours", a.hours)
                res = sim.simulate(mod, hours=hours, **kw)
                names = [n for n in FOCUS if n in res["waits"]]
                print(f"\n=== {label} / {sc} ({hours:g} h): deferred_ticks {res['stats']['deferred_ticks']}, "
                      f"budget ok {res['reserved_ok']}, heavy slots ok {res['heavy_ok']}")
                print(sim.summary(res, names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
