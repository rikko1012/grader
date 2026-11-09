"""Does the display-only exchanges step (Novig, Gemini) inside the light books job change when board builds,
model-forward, settle or the collectors start? Paired over jitter seeds with the production job table
(sports/tests/sched_sim.py), books run time 25 s as in production vs:
  noise floor      every books run 1 s longer (what any timing change does to this chaotic schedule)
  realistic        +10 s every 10th books run (one pass every 10 min; measured 4.3 s for both leagues, x2 margin)
  budget           +30 s every 10th books run (the step's hard SPORTS_EXCHANGES_BUDGET_S)
Prints the paired difference of each job's longest due-but-not-started wait (minutes, mean +- 2 standard errors).

    python scripts/sim_exchanges_step.py [seeds=40]

Nothing is spawned; no store / network access. 2026-11-09 result (40 seeds): realistic = within the noise floor for
every job; a separate light "exchanges" job (own reservation) made ingest-nba / ingest-nfl wait ~3 min longer."""
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sports import scheduler as cur          # noqa: E402
from sports.tests import sched_sim as sim    # noqa: E402

KEY = ("prebuilt", "ui-snapshots-nba", "ui-snapshots-nfl", "model-forward", "settle", "settle-post", "sheets",
       "ingest-nba", "ingest-nfl")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    b0 = sim.RUN_S["books"]
    variants = {"noise floor (+1 s every books run)": lambda k: b0 + 1,
                "realistic (+10 s every 10th books run)": lambda k: b0 + (10 if k % 10 == 0 else 0),
                "budget (+30 s every 10th books run)": lambda k: b0 + (30 if k % 10 == 0 else 0)}
    for scen, kw in (("ok", {}), ("warm", {"warm": True})):
        base = [sim.simulate(cur, seed=sd, **kw) for sd in range(n)]
        for name, fn in variants.items():
            d = {k: [] for k in KEY}
            for sd in range(n):
                a = sim.simulate(cur, seed=sd, run_s={"books": fn}, **kw)
                for k in KEY:
                    d[k].append((a["waits"].get(k, 0) - base[sd]["waits"].get(k, 0)) / 60)
            print(f"== {scen} / {name}, {n} seeds: paired diff of longest wait, min (mean +- 2se)")
            print("  " + "  ".join(f"{k}:{statistics.mean(v):+.2f}+-{2 * statistics.stdev(v) / n ** 0.5:.2f}"
                                   for k, v in d.items()), flush=True)


if __name__ == "__main__":
    main()
