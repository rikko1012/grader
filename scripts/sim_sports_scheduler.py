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
  long      ok, with longer heavy jobs (settle-post 900 s, grades-daily 1,200 s, archive 1,500 s; review 2026-11-09)
  warm      ok, with each job's recent run time known at boot (the service persists them; cold = the first deploy)
Each scenario also prints the minutes a heavy slot sat empty while a due board build would have finished before the
running heavy jobs (idle board slot; the drain fill-in rule, review 2026-11-09). --seeds N repeats each over N jitter
seeds and prints the median and worst longest gap of the member-facing jobs instead.
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


LONG_RUN_S = {"settle-post": 900, "grades-daily": 1200, "archive": 1500}
MEMBER = ("ui-snapshots-nba", "ui-snapshots-nfl", "ingest-nba", "ingest-nfl", "prebuilt", "model-forward")


# store-slow 2026-11-09 (ops at uptime 51 min after the Novig/Gemini revert e5e2ce0): the run times live ops showed,
# model-forward killed at its 900 s timeout every run. "store-fix" = the same table with the jobs the migration-8 reads
# speed up back at their pre-2026-11-09 times (RUN_S; model-forward 300 s - it ran 190 s on 2026-11-05).
LIVE_1109_S = {"ingest-nba": 424, "ingest-nfl": 585, "model-forward": 900, "settle": 199, "ui-snapshots-nba": 292,
               "ui-snapshots-nfl": 143, "settle-post": 418, "results-public": 119, "sheets": 76, "books": 25}
STORE_FIX_S = dict(LIVE_1109_S, **{k: sim.RUN_S[k] for k in ("ingest-nba", "ingest-nfl", "model-forward")})


def scenarios():
    return {"ok": dict(), "failing": dict(rc={"model-forward": 1}), "no-fwd": dict(env={"SPORTS_FORECAST": ""}),
            "first": dict(hours=1.2), "long": dict(run_s=LONG_RUN_S), "warm": dict(warm=True),
            "live-1109": dict(run_s=LIVE_1109_S, warm=True), "store-fix": dict(run_s=STORE_FIX_S, warm=True)}


def seeds_table(mod, kw, hours, n):
    import statistics
    rs = [sim.simulate(mod, hours=hours, seed=s, probe=True, **kw) for s in range(1, n + 1)]
    rows = []
    for j in MEMBER:
        if j not in rs[0]["waits"]:
            continue
        g = [r["max_gap"][j] / 60 for r in rs]
        w = [r["waits"][j] / 60 for r in rs]
        st = [len(r["starts"].get(j, [])) for r in rs]
        rows.append(f"{j:18} starts={statistics.mean(st):5.1f}  longest_gap median/worst={statistics.median(g):5.1f}/"
                    f"{max(g):5.1f} min  longest_wait median/worst={statistics.median(w):5.1f}/{max(w):5.1f} min")
    idle = [r["idle_board_s"] / 60 for r in rs if r["idle_board_s"] is not None]
    if idle:
        rows.append(f"idle board slot    median/worst={statistics.median(idle):5.1f}/{max(idle):5.1f} min")
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", action="append", default=[], help="git ref of an older sports/scheduler.py")
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--seeds", type=int, default=0, help="repeat each scenario over N jitter seeds (median / worst)")
    ap.add_argument("--only", action="append", default=[], help="run only these scenarios")
    a = ap.parse_args(argv)
    mods = [("this checkout", current)]
    with tempfile.TemporaryDirectory() as tmp:
        for r in a.ref:
            mods.append((r, load_ref(r, tmp)))
        for label, mod in mods:
            for sc, kw in scenarios().items():
                if a.only and sc not in a.only:
                    continue
                kw = dict(kw)
                hours = kw.pop("hours", a.hours)
                if kw.get("warm") and not hasattr(mod, "RECENT_RUNS"):
                    continue                            # older copies keep no run times
                if a.seeds:
                    print(f"\n=== {label} / {sc} ({hours:g} h, {a.seeds} seeds)")
                    print(seeds_table(mod, kw, hours, a.seeds))
                    continue
                res = sim.simulate(mod, hours=hours, probe=True, **kw)
                names = [n for n in FOCUS if n in res["waits"]]
                print(f"\n=== {label} / {sc} ({hours:g} h): deferred_ticks {res['stats']['deferred_ticks']}, "
                      f"budget ok {res['reserved_ok']}, heavy slots ok {res['heavy_ok']}, "
                      f"idle board slot {res['idle_board_s'] / 60:.1f} min")
                print(sim.summary(res, names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
