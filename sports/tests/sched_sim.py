"""Deterministic simulation of the sports scheduler (sports/scheduler.py) with the PRODUCTION job table and fake
children whose run times come from live ops (2026-11-09 01:15 UTC, uptime 72 min on c31637d). Used by
test_sports_stall_1108.py and scripts/sim_sports_scheduler.py (incident replay).

Nothing here spawns a process: spawn() returns a fake child that exits after its modelled run time, the clock is a
counter, the game-day cadence is fixed (NBA hot 180 s, NFL game day 300 s, Kalshi 3,600 s, books 60 s - the intervals the
live ops showed) and random jitter is seeded."""
from __future__ import annotations

import copy
import os
import random

# run time (s) of each job in production. ops 2026-11-09 01:15 UTC (last_exit.ran_s) where the job ran; the rest from
# the RUNBOOK / earlier stages files. settle-post's first production run hit its 2,400 s kill (scorecards' first pass
# with no skip marks); later runs skip unchanged events.
RUN_S = {
    "prebuilt": 59, "ui-snapshots-nba": 150, "ui-snapshots-nfl": 220, "sheets": 90, "results-public": 179,
    "mapping-audit": 170, "slips-ingest": 1, "ingest-nba": 290, "ingest-nfl": 360, "books": 25,
    "ingest-kalshi": 310, "settle": 120, "settle-post": 300, "pregame-news": 4, "picks-ingest": 1,
    "picks-settle": 1, "player-context": 60, "games-backfill": 120, "slips-settle": 3, "grades-daily": 15,
    "model-forward": 300, "history": 300, "archive": 600, "archive-verify": 300, "assistant": 30,
}
FIRST_SETTLE_POST_S = 2400.5          # the first run (no marks) - longer than its timeout
LEDGER_S = 200                        # results ledger, both leagues (settle_stages 2026-11-07: 263.5 - 64.3 s)
FIXED_CADENCE = {"gameday:nba": 180.0, "gameday:nfl": 300.0, "kalshi": 3600.0, "books": 60.0}
T0 = 1_794_182_000.0                  # 2026-11-09 ~00:00 UTC (the deploy)


class FakeChild:
    n = 0

    def __init__(self, clock, run_s, rc=0):
        FakeChild.n += 1
        self.pid = 90000 + FakeChild.n
        self.clock, self.end, self.rc = clock, clock[0] + run_s, rc
        self.killed = False

    def poll(self):
        return self.rc if self.clock[0] >= self.end else None

    def kill(self):
        self.killed = True
        self.end = self.clock[0]

    def wait(self, *_a):
        return self.rc


def prod_env(extra=None):
    """The production environment: Render Standard (cgroup 2 GB -> std / two heavy slots), owner budget 1,200 MB."""
    env = {"SPORTS_MEMORY_MB": "2048", "SPORTS_CHILD_BUDGET_MB": "1200", "SPORTS_LEAGUES": "nba nfl",
           "SPORTS_API": "1", "SPORTS_INGEST": "1", "SPORTS_FORECAST": "1", "SPORTS_TRAIN": "", "SPORTS_ASSISTANT": "",
           "SPORTS_MAX_HEAVY": "", "SPORTS_HEAVY_SLOTS": "", "SPORTS_BOOKS_JOB": "1"}
    env.update(extra or {})
    return env


class _Patched:
    """Patch a scheduler module (the current one or an older copy) for one simulation and restore it afterwards."""

    def __init__(self, mod, env, seed=1108):
        self.mod, self.env, self.seed = mod, env, seed

    def __enter__(self):
        self.saved_env = {k: os.environ.get(k) for k in self.env}
        for k, v in self.env.items():
            if v == "":
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.saved = {k: getattr(self.mod, k) for k in ("JOBS", "STATS", "spawn", "rss_mb", "cadence_every")}
        self.mod.JOBS = {}
        self.mod.STATS = copy.deepcopy(self.saved["STATS"])
        for k in self.mod.STATS:
            if isinstance(self.mod.STATS[k], (int, float)):
                self.mod.STATS[k] = 0
        self.mod.rss_mb = lambda pid: 10.0
        self.mod.cadence_every = lambda cad, t, root=None: FIXED_CADENCE[
            cad[0] if cad[0] != "gameday" else f"gameday:{cad[1][0]}"]
        self.rstate = random.getstate()
        random.seed(self.seed)
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            setattr(self.mod, k, v)
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        random.setstate(self.rstate)
        return False


def simulate(mod, hours=6.0, tick_s=1.0, run_s=None, rc=None, env=None, jobs_hook=None, first_settle_post_s=None,
             warm=False, seed=1108, probe=False):
    """Run mod.Scheduler over `hours` of simulated time with the production table. warm=True starts with each job's
    recent run time known (the service persists them in DATA_DIR/scheduler_runs.json, so a deploy starts warm once the
    file exists); the default (cold) is the first deploy with the file, the worst case. Returns a dict:
    starts {job: [t...]} (seconds since T0), ends {job: [(t, code)]}, waits {job: longest due-but-not-started span},
    max_gap {job: longest start-to-start gap incl. the tail}, stats, jobs (final JOBS), reserved_ok, heavy_ok;
    probe=True adds idle_board_s: seconds a heavy slot sat empty (no exclusive job running) while a due board build
    fitted and, by its MODELLED run time, would have ended before every running heavy job (review 2026-11-09)."""
    run_s = dict(RUN_S, **(run_s or {}))
    rc = rc or {}
    fsp = FIRST_SETTLE_POST_S if first_settle_post_s is None else first_settle_post_s
    clock = [T0]
    starts, ends = {}, {}
    with _Patched(mod, prod_env(env), seed):
        J = mod.default_jobs()
        if jobs_hook:
            J = jobs_hook(J) or J

        def spawn(spec):
            name = next(n for n, s in J.items() if s is spec)
            k = len(starts.get(name, ()))
            d = run_s.get(name, 30)
            if name == "settle-post" and k == 0:
                d = fsp
                argv = list(spec["argv"])
                if "--budget-s" in argv:        # the scorecards stop at the budget; the ledger (~200 s) follows
                    d = min(fsp, float(argv[argv.index("--budget-s") + 1]) + LEDGER_S)
            code = rc.get(name, 0)
            code = code(k) if callable(code) else code
            return FakeChild(clock, d, code)
        mod.spawn = spawn
        sch = mod.Scheduler(jobs=J, now=lambda: clock[0], tick_s=tick_s)
        if warm:
            for n, st in mod.JOBS.items():
                if "recent_ran_s" in st:                # older scheduler copies have no fill-in rule
                    st["recent_ran_s"] = [float(run_s.get(n, 30))]
        enabled = sorted(n for n in J if mod.JOBS[n]["enabled"])
        due_since = {}
        waits = {n: 0.0 for n in enabled}
        reserved_ok = heavy_ok = True
        idle_board = 0.0
        steps = int(hours * 3600 / tick_s)
        for _ in range(steps):
            before = set(sch.running)
            sch.tick()
            t = clock[0] - T0
            for n in set(sch.running) - before:
                starts.setdefault(n, []).append(t)
            for n in before - set(sch.running):
                ends.setdefault(n, []).append((t, (mod.JOBS[n].get("last_exit") or {}).get("code")))
            # a job that ended and restarted inside one tick is not seen above: count from the job's own state
            for n in enabled:
                st = mod.JOBS[n]
                if st["last_start"] is not None and (not starts.get(n) or starts[n][-1] < st["last_start"] - T0 - 1e-6):
                    starts.setdefault(n, []).append(st["last_start"] - T0)
                ns = st["next_start"]
                is_due = n not in sch.running and ns is not None and clock[0] >= ns
                if is_due:
                    due_since.setdefault(n, clock[0])
                    waits[n] = max(waits[n], clock[0] - due_since[n])
                else:
                    due_since.pop(n, None)
            if probe:
                idle_board += tick_s * _board_slot_idle(mod, sch, clock[0], run_s)
            if sch.reserved() > sch.budget:
                reserved_ok = False
            if sum(1 for n in sch.running if J[n]["cls"] == mod.HEAVY) > sch.max_heavy:
                heavy_ok = False
            clock[0] += tick_s
        end = clock[0] - T0
        max_gap = {}
        for n in enabled:
            s = starts.get(n, [])
            pts = s + [end]
            max_gap[n] = max(b - a for a, b in zip([0.0] + pts[:-1], pts)) if pts else end
        return {"starts": starts, "ends": ends, "waits": waits, "max_gap": max_gap, "end": end,
                "stats": dict(mod.STATS), "jobs": {k: dict(v) for k, v in mod.JOBS.items()}, "specs": J,
                "reserved_ok": reserved_ok, "heavy_ok": heavy_ok, "history": list(sch.history),
                "idle_board_s": idle_board if probe else None}


def _board_slot_idle(mod, sch, t, run_s):
    """1 when a heavy slot is empty, no exclusive job runs, and a due board build fits now and would end (by its
    modelled run time, two ticks of margin) before the running heavy jobs - a slot the drain wasted - else 0."""
    if any(sch.specs[n]["cls"] == mod.EXCLUSIVE for n in sch.running):
        return 0
    heavy = [n for n in sch.running if sch.specs[n]["cls"] == mod.HEAVY]
    if len(heavy) >= sch.max_heavy or not heavy:
        return 0
    left = max(run_s.get(n, 30) - (t - sch.running[n][1]) for n in heavy)
    for n in sch.due():
        if n.startswith("ui-snapshots-") and run_s.get(n, 30) + 2 < left and sch._fits(n):
            return 1
    return 0


def summary(res, names=None):
    names = names or sorted(res["waits"])
    rows = []
    for n in names:
        rows.append(f"{n:18} starts={len(res['starts'].get(n, [])):4d}  longest_wait={res['waits'].get(n, 0) / 60:6.1f} min"
                    f"  longest_gap={res['max_gap'].get(n, 0) / 60:6.1f} min")
    return "\n".join(rows)
