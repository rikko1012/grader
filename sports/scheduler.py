"""Worker-side job scheduler for the sports service (Render Starter: 512 MB RAM, 0.5 CPU - owner 2026-10-24).

Every background job is a CHILD process of the service (never a request thread). One supervisor thread admits jobs
through a MEMORY-RESERVATION QUEUE:

  * each job declares a cap (MB, measured peak x margin - sports/RUNBOOK.md "Starter memory budget");
  * a job starts only when  sum(caps of running children) + its cap <= SPORTS_CHILD_BUDGET_MB  (default 290);
  * at most ONE heavy job runs at a time ("heavy" = anything but the light slip / assistant jobs);
  * an "exclusive" job (model fit / forecast, lab training) waits until NOTHING else runs and blocks every other start
    while it runs - so ingestion is paused during training;
  * priority with AGING (effective priority = prio - overdue / SPORTS_JOB_AGING_S, default 120 s per level), so a
    job that keeps losing the slot moves up; a job blocked only by the heavy slot / budget lets later jobs that fit
    start (backfill), but a waiting EXCLUSIVE job holds every later start back until it gets the whole budget;
  * a watchdog samples each child's RSS every 0.25 s; above its cap the child is killed (SIGKILL), counted
    (`over_cap`) and retried later with backoff - the service process itself is never the one that runs out;
  * children run at low OS priority (nice 10 / BELOW_NORMAL) so member requests keep the CPU, and on Linux mark
    themselves first in line for the kernel OOM killer (oom_score_adj 900) - the service keeps its default;
  * single-threaded native math (OPENBLAS / OMP / MKL = 1) and MALLOC_ARENA_MAX=2 in every child.

The assistant runner is ON DEMAND: `python -m sports.assistant.runner --once` starts when a question is queued in
assistant.db (checked every 2 s) and exits when the queue is empty - no idle resident process.

Cadence / cap / budget of every job are environment-overridable: SPORTS_JOB_<NAME>_EVERY_S, SPORTS_JOB_<NAME>_CAP_MB
(NAME = job name upper-cased, '-' -> '_'), SPORTS_CHILD_BUDGET_MB. Flags are read at service start.
"""
from __future__ import annotations

import os
import random
import sqlite3
import subprocess
import sys
import threading
import time

from . import flags

LIGHT, HEAVY, EXCLUSIVE = "light", "heavy", "exclusive"
TICK_S = 0.25
_LOCK = threading.Lock()
JOBS = {}                     # name -> state (ops / alerts / tests)
STATS = {"started": 0, "over_cap_kills": 0, "timeouts": 0, "deferred_ticks": 0, "peak_reserved_mb": 0,
         "peak_children_rss_mb": 0.0}


def _env_num(name, default, cast=float):
    try:
        return cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return cast(default)


# ------------------------------------------------------------------------------------------------ plan-aware memory
# The plan (fresh 2026-10-25): Starter 512 MB / 0.5 CPU, Standard 2 GB / 1 CPU. The caps below were measured on
# Starter; on a larger plan they scale by cap_scale() (at most x CAP_SCALE_MAX, so a board build is capped at 700 MB on
# 2 GB), the children's reservation total is ~75% of the plan minus the service's own working set, and two heavy
# jobs may run at once when they share no store write lock (max_heavy).
BASE_MEMORY_MB = 512
SERVICE_MB = 70                     # the service process itself (prod rss 69 MB at 700-member load, 2026-10-24)
CAP_SCALE_MAX = 7.0 / 3.0           # 300 MB board build -> 700 MB at 2 GB


def _cgroup_limit_mb():
    """The container's memory limit (cgroup v2 memory.max / v1 memory.limit_in_bytes) in MB, None when unreadable or
    unlimited."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as f:
                raw = f.read().strip()
        except OSError:
            continue
        if not raw.isdigit():
            continue                                    # "max" = no limit
        mb = int(raw) / (1024 * 1024)
        if 128 <= mb < 1024 * 1024:                     # v1 reports ~8 EB when unlimited
            return int(mb)
    return None


def memory_mb():
    """SPORTS_MEMORY_MB, else the cgroup limit, else 512 (Starter)."""
    v = os.environ.get("SPORTS_MEMORY_MB", "").strip()
    if v:
        try:
            return max(256, int(float(v)))
        except ValueError:
            pass
    return _cgroup_limit_mb() or BASE_MEMORY_MB


def memory_source():
    if os.environ.get("SPORTS_MEMORY_MB", "").strip():
        return "SPORTS_MEMORY_MB"
    return "cgroup" if _cgroup_limit_mb() else "default 512"


LIGHT_RESERVE_MB = 120              # light children (slips / picks ingest, assistant) kept free beside a heavy pair
PAIR_BASE_MB = 600                  # Starter caps of the pair that may co-run: board build 300 + collector 300


def standard(mem=None):
    """True on a plan of 1.5 GB or more (Render Standard 2 GB, 2026-10-25): faster cadences, two heavy slots."""
    return (memory_mb() if mem is None else mem) >= 1536


def max_heavy(mem=None):
    """Heavy children at once: 2 from 1.5 GB (Standard), else 1. SPORTS_MAX_HEAVY overrides (SPORTS_HEAVY_SLOTS, the
    cloud runbook's name, is read too)."""
    mem = memory_mb() if mem is None else mem
    dflt = 2 if standard(mem) else 1
    v = os.environ.get("SPORTS_MAX_HEAVY") or os.environ.get("SPORTS_HEAVY_SLOTS")
    try:
        return max(1, int(v)) if v else dflt
    except ValueError:
        return dflt


def cap_scale(mem=None, budget=None, heavy=None):
    """Starter cap x this = the cap on this plan: min(7/3, plan / 512) and - so the reservation total really holds
    the pair it allows - never more than (budget - light reserve) / the pair's Starter caps when two heavy jobs may
    run. Honours SPORTS_CHILD_BUDGET_MB: at 2 GB with the owner's 1200 MB -> x1.8 (board build / collector 540 MB);
    with the default 1466 MB -> x2.243 (673 MB). Never below x1."""
    mem = memory_mb() if mem is None else mem
    budget = budget_mb(mem) if budget is None else budget
    heavy = max_heavy(mem) if heavy is None else heavy
    s = min(CAP_SCALE_MAX, mem / BASE_MEMORY_MB)
    if heavy >= 2:
        s = min(s, (budget - LIGHT_RESERVE_MB) / PAIR_BASE_MB)
    return max(1.0, s)


def budget_mb(mem=None):
    """Reservation total of all children: ~75% of the plan minus the service (512 -> 314 ~ the former 310;
    2048 -> 1466). SPORTS_CHILD_BUDGET_MB overrides."""
    mem = memory_mb() if mem is None else mem
    return _env_num("SPORTS_CHILD_BUDGET_MB", int(mem * 0.75) - SERVICE_MB, int)


def _py(*args):
    return [sys.executable, *args]


def default_jobs():
    """name -> spec. cap_mb from the 2026-10-24 measurements (sports/research/results/starter_2026-10-24) with margin.
    every_s None + trigger = on demand. Starter cadences (members see the freshness in RUNBOOK 'Starter')."""
    lg = os.environ.get("SPORTS_LEAGUES", "nba nfl").split()
    std = standard()                    # Standard plan cadences (2026-10-25); Starter values after "else"
    J = {
        # on demand, member-facing first
        "assistant": dict(argv=_py("-m", "sports.assistant.runner", "--once"), every_s=None, trigger="assistant_queued",
                          timeout_s=300, flag="SPORTS_ASSISTANT", cap_mb=120, cls=LIGHT, prio=0),
        # what members read
        "prebuilt": dict(argv=_py("-m", "sports.service", "snapshots"), every_s=45 if std else 90, timeout_s=120, flag="SPORTS_API",
                         cap_mb=160, cls=HEAVY, prio=1, locks=("prebuilt",)),  # 105-109 MB (slim slip pool)
        # the board build, ONE PROCESS PER LEAGUE (fresh 2026-10-24): both leagues in one process were killed at
        # 251-265 MB in production; an NFL week alone measured 137 MB on one live read of every board (17k offers),
        # NBA 90 MB. Cap 300 = the envelope the exclusive model jobs already run in (340): with it reserved no other
        # heavy child and no light child fits the 310 MB budget, so service (~70) + build stays near 370 of 512 MB.
        **{f"ui-snapshots-{x}": dict(argv=_py("-m", "sports.snapshots", "build", x, "--no-results"),
                                     every_s=60 if std else 180,
                                     timeout_s=240, flag="SPORTS_INGEST", cap_mb=300, cls=HEAVY, prio=2,
                                     league=x, locks=(f"snapshots:{x}",)) for x in lg},
        "sheets": dict(argv=_py("-m", "sports.sheets", "build", *lg), every_s=300, timeout_s=300,
                       flag="SPORTS_INGEST", cap_mb=200, cls=HEAVY, prio=3),   # 67 / 126 / 26 MB measured (synthetic 400-player league)
        "mapping-audit": dict(argv=_py("-m", "sports.mapping_audit", *lg), every_s=3600, timeout_s=240,
                              flag="SPORTS_INGEST", cap_mb=250, cls=HEAVY, prio=3),  # one offer scan per league / hour
        "slips-ingest": dict(argv=_py("-m", "sports.slips", "ingest"), every_s=300, timeout_s=300,
                             flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=2),    # 23 MB
        # collection: the one collector, one job PER LEAGUE on a GAME-DAY cadence (fresh 2026-10-24, owner audit bug 1):
        # every SPORTS_INGEST_HOT_S (300) from SPORTS_INGEST_HOT_H (2) hours before that league's earliest upcoming
        # tip, SPORTS_INGEST_GAMEDAY_S (600) on a game day, SPORTS_INGEST_IDLE_S (900) otherwise; each successful
        # cycle starts the board build at once ("then"). --bucket-min 1: a 5-minute cycle is never skipped as 'done'.
        **{f"ingest-{x}": dict(argv=_py("-m", "sports.worker", "ingest", x, "--bucket-min", "1", "--sources",
                                        "espn,pp,ud,injuries,oddsapi,polymarket,books,games"),
                               every_s=600 if std else 900, cadence=("gameday", (x,)), then=(f"ui-snapshots-{x}",), collector=True,
                               timeout_s=900, flag="SPORTS_INGEST", cap_mb=300, cls=EXCLUSIVE, prio=3,
                               league=x, locks=("canonical", "books", "games", "cache"),
                               env={"SPORTS_WORKER_CONCURRENCY": "1"}) for x in lg},   # 191-197 MB both leagues
        "ingest-kalshi": dict(argv=_py("-m", "sports.worker", "ingest", *lg, "--bucket-min", "1", "--sources",
                                       "kalshi"),
                              every_s=1800 if std else 3600, cadence=("kalshi", tuple(lg)), then=tuple(f"ui-snapshots-{x}" for x in lg),
                              timeout_s=900, flag="SPORTS_INGEST", cap_mb=240, cls=HEAVY, prio=3,
                              locks=("canonical", "cache"), env={"SPORTS_WORKER_CONCURRENCY": "1"}),         # 70-84 MB (production 2026-10-25)
        "settle": dict(argv=_py("-m", "sports.worker", "settle", *lg), every_s=900 if std else 3600, timeout_s=900,
                       flag="SPORTS_INGEST", cap_mb=120, cls=HEAVY, prio=4),   # 58 MB
        "picks-ingest": dict(argv=_py("-m", "sports.picks", "ingest"), every_s=60, timeout_s=120,
                             flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=2),    # member picks: spool -> picks.db
        "picks-settle": dict(argv=_py("-m", "sports.picks", "settle"), every_s=900 if std else 3600, timeout_s=600,
                             flag="SPORTS_INGEST", cap_mb=80, cls=LIGHT, prio=4),
        # game store backfill (current + previous season, resumable, time-bounded) so the factor engine behind the
        # player page (sports/player_context.py) has whole seasons, not only the games since launch
        # the engine's player-page context, out of the 3-minute ui-snapshots job (319 MB inline with whole seasons vs
        # its 250 MB cap); ui-snapshots embeds the latest result one player file at a time (199 MB)
        "player-context": dict(argv=_py("-m", "sports.player_context", "build", *lg), every_s=1800, timeout_s=600,
                               flag="SPORTS_INGEST", cap_mb=260, cls=HEAVY, prio=5),   # 170 MB, 13 s (1,724 games)
        "games-backfill": dict(argv=_py("-m", "sports.games.backfill_job", "--budget", "120"), every_s=900,
                               timeout_s=300, flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=6),   # 43-47 MB
        "slips-settle": dict(argv=_py("-m", "sports.slips", "settle"), every_s=900 if std else 3600, timeout_s=600,
                             flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=4),    # 22 MB
        # forward forecasts, INFERENCE ONLY (sports/model/fwd.py): frozen hash-verified artifacts, no fit / learn /
        # retrain. SPORTS_FORECAST - independent of SPORTS_TRAIN (owner 2026-10-24). Exclusive: never beside ingestion.
        "model-forward": dict(argv=_py("-m", "sports.model.fwd", "run", *lg), every_s=1800, timeout_s=900,
                              flag="SPORTS_FORECAST", cap_mb=340, cls=EXCLUSIVE, prio=5),
        # model (worker `train` commands need SPORTS_TRAIN - foundation rule)
        "model-forecast": dict(argv=_py("-m", "sports.worker", "train", "forecast", *lg), every_s=3600, timeout_s=900,
                               flag="SPORTS_TRAIN", cap_mb=340, cls=EXCLUSIVE, prio=5),   # 272-311 MB
        "model-learn": dict(argv=_py("-m", "sports.worker", "train", "learn"), every_s=3600, timeout_s=600,
                            flag="SPORTS_TRAIN", cap_mb=250, cls=HEAVY, prio=5),   # 78-218 MB (reads every forecast)
        "model-snapshot": dict(argv=_py("-m", "sports.worker", "train", "snapshot"), every_s=1800, timeout_s=600,
                               flag="SPORTS_TRAIN", cap_mb=250, cls=HEAVY, prio=5),   # 77-217 MB (reads every forecast)
        "model-fit": dict(argv=_py("-m", "sports.worker", "train", "fit"), every_s=7 * 86400, timeout_s=3600,
                          flag="SPORTS_TRAIN", cap_mb=340, cls=EXCLUSIVE, prio=7),    # 306-309 MB
        "lab": dict(argv=_py("-m", "sports.lab.runner", "--once"), every_s=900, timeout_s=3600, flag="SPORTS_TRAIN",
                    cap_mb=340, cls=EXCLUSIVE, prio=7),                        # idle 34 MB; training up to fit size
        # slow / maintenance
        # player game logs: small rotating batches (least recently checked first) every 15 min, so every board player
        # is re-read within ~3 h and a finished game reaches the player page the same night
        "history": dict(argv=_py("-m", "sports.snapshots", "history", *lg, "--max-players", "60"), every_s=900,
                        timeout_s=900, flag="SPORTS_INGEST", cap_mb=200, cls=HEAVY, prio=6),   # 107 MB @ 40 players
        "archive": dict(argv=_py("-m", "sports.archive", "pass"), every_s=6 * 3600, timeout_s=1800,
                        flag="SPORTS_INGEST", cap_mb=200, cls=HEAVY, prio=6),      # 140 MB (80k rows)
        "archive-verify": dict(argv=_py("-m", "sports.archive", "verify"), every_s=86400, timeout_s=1800,
                               flag="SPORTS_INGEST", cap_mb=120, cls=HEAVY, prio=8),
    }
    scale, two = cap_scale(), max_heavy() >= 2
    for name, spec in J.items():
        key = "SPORTS_JOB_" + name.upper().replace("-", "_")
        spec["cap_mb"] = int(round(spec["cap_mb"] * scale))
        if two and spec.get("collector"):               # a collector may run beside the OTHER league's board build
            spec["cls"] = HEAVY
        if spec["every_s"] is not None:
            spec["every_s"] = _env_num(key + "_EVERY_S", spec["every_s"])
            if os.environ.get(key + "_EVERY_S"):        # an explicit override pins the interval (no game-day cadence)
                spec.pop("cadence", None)
        spec["cap_mb"] = _env_num(key + "_CAP_MB", spec["cap_mb"], int)
        if spec["cls"] == EXCLUSIVE:
            spec["cap_mb"] = max(spec["cap_mb"], 1)
    return J


# ------------------------------------------------------------------------------------------------ process helpers
def rss_mb(pid):
    """Resident set of one process (Linux /proc; psutil elsewhere when installed). None when unknown."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    try:
        import psutil
        return psutil.Process(pid).memory_info().rss / 1e6
    except Exception:                                   # noqa: BLE001
        return None


def _child_env(spec):
    e = dict(os.environ)
    e.update({"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "MALLOC_ARENA_MAX": "2",
              "PYTHONUNBUFFERED": "1"})
    e.update(spec.get("env") or {})
    return e


def _posix_child_setup():                               # runs in the child before exec (Linux)
    try:
        os.nice(10)
    except OSError:
        pass
    try:
        with open("/proc/self/oom_score_adj", "w") as f:
            f.write("900")
    except OSError:
        pass


def spawn(spec):
    kw = dict(stdin=subprocess.DEVNULL, env=_child_env(spec))
    if os.name == "posix":
        kw["preexec_fn"] = _posix_child_setup
    else:
        kw["creationflags"] = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    return subprocess.Popen(spec["argv"], **kw)


def _kill(p):
    try:
        p.kill()
        p.wait(10)
    except Exception:                                   # noqa: BLE001
        pass


# ------------------------------------------------------------------------------------------------------ triggers
def assistant_queued():
    """True when assistant.db holds a queued run (read-only, cheap; False when the file does not exist)."""
    from . import config
    p = config.DATA_DIR / "assistant.db"
    if not p.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=1)
        try:
            return con.execute("SELECT 1 FROM runs WHERE state='queued' LIMIT 1").fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


TRIGGERS = {"assistant_queued": assistant_queued}


def _job_on(flag):
    if flag in flags.FLAGS:
        return flags.on(flag)
    return os.environ.get(flag, "").strip().lower() in ("1", "true", "yes", "on")


# ------------------------------------------------------------------------------------------------ game-day cadence
def cadence_settings():
    std = standard()                    # Standard 2 GB / 1 CPU: 3 / 5 / 10 min, Kalshi 10 / 30 (Starter 5/10/15, 20/60)
    return {"hot_s": _env_num("SPORTS_INGEST_HOT_S", 180.0 if std else 300.0),
            "gameday_s": _env_num("SPORTS_INGEST_GAMEDAY_S", 300.0 if std else 600.0),
            "idle_s": _env_num("SPORTS_INGEST_IDLE_S", 600.0 if std else 900.0),
            "hot_h": _env_num("SPORTS_INGEST_HOT_H", 2.0),
            "gameday_h": _env_num("SPORTS_INGEST_GAMEDAY_H", 18.0),
            "kalshi_hot_s": _env_num("SPORTS_KALSHI_HOT_S", 600.0 if std else 1200.0),
            "kalshi_idle_s": _env_num("SPORTS_KALSHI_IDLE_S", 1800.0 if std else 3600.0)}


def _iso_ts(x):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def league_phase(league, now_ts, snap_root=None):
    """'hot' (earliest upcoming tip within hot_h hours), 'gameday' (a tip within gameday_h hours or a game under way),
    'idle', or 'unknown' (no slate file yet). From the league's slate snapshot (<snapshots>/<league>/slate.json - a
    small file the board build writes), never the store."""
    import json
    from pathlib import Path
    if snap_root is None:
        from . import config
        snap_root = config.DATA_DIR / "snapshots"
    try:
        evs = json.loads((Path(snap_root) / league / "slate.json").read_text(encoding="utf-8")).get("events") or []
    except (OSError, ValueError):
        return "unknown"
    c = cadence_settings()
    nxt, live = None, False
    for e in evs:
        t = _iso_ts(e.get("start_utc"))
        if t is None or e.get("status") in ("final", "cancelled", "postponed"):
            continue
        if t > now_ts:
            nxt = t if nxt is None else min(nxt, t)
        elif now_ts - t < 4 * 3600:
            live = True
    if nxt is not None and nxt - now_ts <= c["hot_h"] * 3600:
        return "hot"
    if live or (nxt is not None and nxt - now_ts <= c["gameday_h"] * 3600):
        return "gameday"
    return "idle"


def cadence_every(cadence, now_ts, snap_root=None):
    """Seconds between runs for a ('gameday' | 'kalshi', leagues) cadence: the fastest phase over its leagues."""
    kind, leagues = cadence
    c = cadence_settings()
    phases = {league_phase(x, now_ts, snap_root) for x in leagues}
    if kind == "kalshi":
        return c["kalshi_hot_s"] if "hot" in phases else c["kalshi_idle_s"]
    if "hot" in phases:
        return c["hot_s"]
    if "gameday" in phases or "unknown" in phases:
        return c["gameday_s"]
    return c["idle_s"]


# ---------------------------------------------------------------------------------------------------- scheduler
class Scheduler:
    def __init__(self, jobs=None, budget=None, now=time.time, tick_s=TICK_S, triggers=None, aging_s=None,
                 snap_root=None, max_heavy_n=None):
        self.specs = jobs if jobs is not None else default_jobs()
        self.snap_root = snap_root
        self._cad = {}                                  # cadence -> (computed_at, every_s): re-read every 30 s
        self.budget = budget if budget is not None else budget_mb()
        self.max_heavy = max_heavy_n if max_heavy_n is not None else max_heavy()
        self.now = now
        self.tick_s = tick_s
        self.aging_s = aging_s if aging_s is not None else _env_num("SPORTS_JOB_AGING_S", 120.0)
        self.triggers = triggers or TRIGGERS
        self.running = {}                               # name -> (Popen, start_t)
        self.stop_ev = threading.Event()
        self.history = []                               # (t, event, name, detail) - bounded, for tests / ops
        t = self.now()
        for name, spec in self.specs.items():
            enabled = _job_on(spec["flag"])
            JOBS[name] = {"enabled": enabled, "flag": spec["flag"], "cls": spec["cls"], "cap_mb": spec["cap_mb"],
                          "every_s": spec["every_s"], "prio": spec["prio"], "pid": None, "starts": 0,
                          "last_start": None, "last_exit": None, "failures_in_a_row": 0, "over_cap": 0,
                          "rss_mb": None, "peak_rss_mb": 0.0, "last_peak_rss_mb": None, "last_ok_at": None,
                          # first run soon after start, staggered by priority so jobs do not pile up at boot
                          "next_start": (t + spec.get("first_s", 5 + 3 * spec["prio"])) if spec["every_s"] is not None
                          else None,
                          "queued_since": None}

    # -- admission ----------------------------------------------------------------------------------------------
    def reserved(self):
        return sum(self.specs[n]["cap_mb"] for n in self.running)

    @staticmethod
    def _pairable(a, b):
        """Two heavy specs may run together: both declare their store write locks, share none, and are not one
        league's collector beside that SAME league's board build (the build follows the cycle - 'then')."""
        la, lb = a.get("locks"), b.get("locks")
        if not la or not lb or set(la) & set(lb):
            return False
        return not (a.get("league") and a.get("league") == b.get("league"))

    def _fits(self, name):
        spec = self.specs[name]
        classes = [self.specs[n]["cls"] for n in self.running]
        if EXCLUSIVE in classes:
            return False
        if spec["cls"] == EXCLUSIVE:
            return not self.running
        if spec["cls"] == HEAVY:
            heavy = [self.specs[n] for n in self.running if self.specs[n]["cls"] == HEAVY]
            if len(heavy) >= self.max_heavy or not all(self._pairable(spec, h) for h in heavy):
                return False
        return self.reserved() + spec["cap_mb"] <= self.budget

    def every(self, name):
        """The job's current interval: its game-day cadence when it has one (cached 30 s), else every_s."""
        spec = self.specs[name]
        cad = spec.get("cadence")
        if not cad:
            return spec["every_s"]
        t = self.now()
        hit = self._cad.get(cad)
        if hit is None or t - hit[0] >= 30:
            try:
                hit = (t, float(cadence_every(cad, t, self.snap_root)))
            except Exception:                           # noqa: BLE001 - the cadence never stops a job
                hit = (t, float(spec["every_s"]))
            self._cad[cad] = hit
        return hit[1]

    def due(self):
        t = self.now()
        out = []
        for name, spec in self.specs.items():
            st = JOBS[name]
            if not st["enabled"] or name in self.running:
                continue
            if spec.get("cadence") and spec["every_s"] is not None:
                e = self.every(name)
                st["every_s"] = e
                le = st.get("last_exit") or {}
                if le.get("code") == 0 and st["last_start"] is not None and st["next_start"] > st["last_start"] + e:
                    st["next_start"] = st["last_start"] + e     # a phase change (2 h before tip) pulls the next run in
            if spec["every_s"] is None:
                trig = self.triggers.get(spec.get("trigger"))
                ns = st["next_start"]
                if trig and (ns is None or t >= ns) and trig():
                    out.append(name)
            elif t >= st["next_start"]:
                out.append(name)
        def eff(n):
            ns = JOBS[n]["next_start"] or t
            return (self.specs[n]["prio"] - max(0.0, t - ns) / self.aging_s, ns)
        out.sort(key=eff)
        return out

    def _event(self, ev, name, detail=None):
        self.history.append((round(self.now(), 3), ev, name, detail))
        if len(self.history) > 2000:
            del self.history[:500]

    def start(self, name):
        spec = self.specs[name]
        st = JOBS[name]
        try:
            p = spawn(spec)
        except OSError as e:
            self._finish(name, f"spawn failed: {e}", 0.0)
            return
        t = self.now()
        self.running[name] = (p, t)
        st.update(pid=p.pid, starts=st["starts"] + 1, last_start=t, queued_since=None, rss_mb=None,
                  last_peak_rss_mb=0.0)
        with _LOCK:
            STATS["started"] += 1
            STATS["peak_reserved_mb"] = max(STATS["peak_reserved_mb"], self.reserved())
        self._event("start", name, {"reserved_mb": self.reserved()})

    def _finish(self, name, rc, ran):
        spec, st = self.specs[name], JOBS[name]
        st.update(pid=None, last_exit={"code": rc, "at": self.now(), "ran_s": round(ran, 1),
                                       "peak_rss_mb": st.get("last_peak_rss_mb")})
        st["failures_in_a_row"] = 0 if rc == 0 else st["failures_in_a_row"] + 1
        if rc == 0:
            st["last_ok_at"] = self.now()
        if spec["every_s"] is None:                     # on demand: re-check the trigger after a short pause
            wait = 2.0 if rc == 0 else min(900.0, 30.0 * (2 ** min(st["failures_in_a_row"], 5)))
        else:
            every = self.every(name)
            if spec.get("cadence"):                     # a cadence is start to start and never jitters later
                wait = max(1.0, every - ran) * (0.95 + random.random() * 0.05)
            else:
                wait = max(1.0, every - ran) * (0.9 + random.random() * 0.2)
            if rc != 0:
                wait = max(wait, min(6 * 3600.0, every * (2 ** min(st["failures_in_a_row"], 4))))
        st["next_start"] = self.now() + wait
        if rc == 0:
            for nxt in spec.get("then") or ():          # e.g. the board build right after each collector cycle
                ns = JOBS.get(nxt) or {}
                if ns.get("enabled") and nxt not in self.running and ns.get("next_start") is not None:
                    ns["next_start"] = min(ns["next_start"], self.now())
                    self._event("then", nxt, {"after": name})
        self._event("exit", name, {"code": rc, "ran_s": round(ran, 1), "peak_rss_mb": st.get("last_peak_rss_mb")})

    # -- one tick -------------------------------------------------------------------------------------------------
    def tick(self):
        t = self.now()
        total = 0.0
        for name, (p, t0) in list(self.running.items()):
            spec, st = self.specs[name], JOBS[name]
            rc = p.poll()
            if rc is None:
                mb = rss_mb(p.pid)
                if mb is not None:
                    total += mb
                    st["rss_mb"] = round(mb, 1)
                    st["last_peak_rss_mb"] = round(max(st["last_peak_rss_mb"] or 0.0, mb), 1)
                    st["peak_rss_mb"] = round(max(st["peak_rss_mb"], mb), 1)
                    if mb > spec["cap_mb"]:
                        _kill(p)
                        st["over_cap"] += 1
                        with _LOCK:
                            STATS["over_cap_kills"] += 1
                        del self.running[name]
                        self._finish(name, f"killed_over_cap ({mb:.0f} > {spec['cap_mb']} MB)", t - t0)
                        continue
                if spec.get("timeout_s") and t - t0 > spec["timeout_s"]:
                    _kill(p)
                    with _LOCK:
                        STATS["timeouts"] += 1
                    del self.running[name]
                    self._finish(name, "timeout", t - t0)
                continue
            del self.running[name]
            self._finish(name, rc, t - t0)
        with _LOCK:
            STATS["peak_children_rss_mb"] = round(max(STATS["peak_children_rss_mb"], total), 1)
        for name in self.due():
            if self._fits(name):
                self.start(name)
                continue
            if JOBS[name]["queued_since"] is None:
                JOBS[name]["queued_since"] = t
            with _LOCK:
                STATS["deferred_ticks"] += 1
            if self.specs[name]["cls"] == EXCLUSIVE:
                break                                   # hold everything behind a waiting exclusive job (no starvation)

    def run(self):
        while not self.stop_ev.is_set():
            try:
                self.tick()
            except Exception as e:                      # noqa: BLE001 - the scheduler never dies
                sys.stderr.write(f"  sports scheduler tick failed: {type(e).__name__}: {str(e)[:200]}\n")
            self.stop_ev.wait(self.tick_s)
        for name, (p, _t0) in list(self.running.items()):
            _kill(p)
            del self.running[name]
            self._finish(name, "stopped", 0.0)

    def stop(self):
        self.stop_ev.set()


def state():
    with _LOCK:
        s = dict(STATS)
    mem = memory_mb()
    return {"budget_mb": budget_mb(mem), "memory_mb": mem, "memory_source": memory_source(),
            "cap_scale": round(cap_scale(mem), 3), "max_heavy": max_heavy(mem), "stats": s, "jobs": {k: dict(v) for k, v in JOBS.items()}}
