"""Worker-side job scheduler for the sports service (Render Starter: 512 MB RAM, 0.5 CPU - owner 2026-10-24).

Every background job is a CHILD process of the service (never a request thread). One supervisor thread admits jobs
through a MEMORY-RESERVATION QUEUE:

  * each job declares a cap (MB, measured peak x margin - sports/RUNBOOK.md "Starter memory budget");
  * a job starts only when  sum(caps of running children) + its cap <= SPORTS_CHILD_BUDGET_MB  (default 290);
  * at most ONE heavy job runs at a time ("heavy" = anything but the light slip / assistant jobs);
  * an "exclusive" job (model fit / forecast, lab training) waits until NOTHING else runs and blocks every other start
    while it runs - so ingestion is paused during training;
  * a member-facing job overdue past urgent_after_s goes first and later jobs may not take its slot (2026-10-31);
  * priority with AGING (effective priority = prio - overdue / SPORTS_JOB_AGING_S, default 120 s per level), so a
    job that keeps losing the slot moves up; a job blocked only by the heavy slot / budget lets later jobs that fit
    start (backfill), but a waiting EXCLUSIVE job holds every later start back until it gets the whole budget;
  * bounded waits (stall 2026-11-09): LIGHT jobs run beside a waiting or running exclusive job whenever the
    reservation total still holds the exclusive job (an exclusive job never waits for them); an exclusive job that has
    waited SPORTS_JOB_EXCL_DRAIN_S (600) stops every other non-light start, urgent ones included, so the system drains
    and it starts - except a heavy job whose recent runs all ended within the time the running ones still need (a
    board build beside a collector), which finishes first and so never delays it; any other job overdue past
    SPORTS_JOB_STARVE_S (3600) counts as urgent, so a saturated pair of heavy slots cannot starve settle / history /
    player-context - the archive after its own starve_after_s (1,800 s, heavy-1118); a job may yield to another
    (settle-post -> settle);
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
RECENT_RUNS = 3                                     # run times kept per job for the drain fill-in (review 2026-11-09)
_LOCK = threading.Lock()
JOBS = {}                     # name -> state (ops / alerts / tests)
STATS = {"started": 0, "over_cap_kills": 0, "timeouts": 0, "deferred_ticks": 0, "peak_reserved_mb": 0,
         "peak_children_rss_mb": 0.0, "recent_timeouts": []}


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


# heavy-1118 (live 2026-11-18 22:03-22:40 UTC, c7e7114): 36 min after a deploy the archive, settle-post, history,
# player-context, Kalshi and archive-verify had 0 starts. All of them share a store lock with settle or the collectors
# (canonical / model-daily / games); on live run times that lane is oversubscribed (NBA collector 145 s per 180 s,
# history ~800 s per 900 s, settle ~330 s, plus a 12-min exclusive model-forward every 30 min), so a non-urgent job there
# starts only by winning tie-breaks. The archive (prio 6) lost every tie to history (prio 6, same boot tick, defined
# first) and so waited for the 1-h starve rule (replay: first start 76-92 min); deploys came sooner, its first pass never
# ran and nothing was archived after 2026-11-16. It counts as starved after 30 min overdue (replays: first start 34-39
# min, sports/tests/test_sports_heavy_1118.py), then takes its turn between the member-facing jobs like any starved job;
# the lane is full, so history / player-context / Kalshi start later after a deploy (still inside the 1-h rule + turn).
# SPORTS_JOB_STARVE_S=0 still turns the rule off for every job.
ARCHIVE_STARVE_S = 1800.0

BOOKS_CAP_MB = 60                   # Starter cap of the books job (x cap_scale); see the books row in sports/RUNBOOK.md


def books_job_on(env=None):
    """SPORTS_BOOKS_JOB (default on): the books' own feeds run as the light "books" job, not inside ingest-<league>."""
    v = ((os.environ if env is None else env).get("SPORTS_BOOKS_JOB") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _py(*args):
    return [sys.executable, *args]


def default_jobs():
    """name -> spec. cap_mb from the 2026-10-24 measurements (sports/research/results/starter_2026-10-24) with margin.
    every_s None + trigger = on demand. Starter cadences (members see the freshness in RUNBOOK 'Starter')."""
    lg = os.environ.get("SPORTS_LEAGUES", "nba nfl").split()
    books_job = books_job_on()
    std = standard()                    # Standard plan cadences (2026-10-25); Starter values after "else"
    J = {
        # on demand, member-facing first
        "assistant": dict(argv=_py("-m", "sports.assistant.runner", "--once"), every_s=None, trigger="assistant_queued",
                          timeout_s=300, flag="SPORTS_ASSISTANT", cap_mb=120, cls=LIGHT, prio=0),
        # what members read
        # heavy-1118: every 600 s (was 45 / 90). After the c7e7114 memory fix a run takes 362-419 s live (112-117 MB), so
        # at 45 s it was due again 1 s after each exit (live it still started only twice in the first 36 min: the drain,
        # boards, collectors and settle held the slots - the archive fix below is what moves); its only reader is the
        # /sports slips page (web/sports/slips.js, SPORTS_UI, off), served while <= 1,200 s old (web.SNAPSHOTS) - under
        # the 25-min line rule (sports/picks.py STALE_S). Not always met: the replays' longest gap is 20-40 min, set by
        # the model-forward drain + run (every heavy start stops), not by this cadence; a stale pool answers 503
        "prebuilt": dict(argv=_py("-m", "sports.service", "snapshots"), every_s=600, timeout_s=600 if std else 120, flag="SPORTS_API",
                         urgent_after_s=300, cap_mb=160, cls=HEAVY, prio=1, locks=("prebuilt",)),  # 105-117 MB (slim slip pool)
        # the board build, ONE PROCESS PER LEAGUE (fresh 2026-10-24): both leagues in one process were killed at
        # 251-265 MB in production; an NFL week alone measured 137 MB on one live read of every board (17k offers),
        # NBA 90 MB. Cap 300 = the envelope the exclusive model jobs already run in (340): with it reserved no other
        # heavy child and no light child fits the 310 MB budget, so service (~70) + build stays near 370 of 512 MB.
        **{f"ui-snapshots-{x}": dict(argv=_py("-m", "sports.snapshots", "build", x, "--no-results"),
                                     every_s=60 if std else 180,
                                     # 2026-10-31: NBA hit 240 s on the grown store and each timeout threw the whole
                                     # build away (board 64 min old); urgent_after_s = what members read goes first
                                     timeout_s=420 if std else 240, urgent_after_s=300,
                                     flag="SPORTS_INGEST", cap_mb=300, cls=HEAVY, prio=2,
                                     league=x, locks=(f"snapshots:{x}",)) for x in lg},
        # locks (2026-10-31): a heavy job without them runs alone, so the other slot sat empty and sheets / settle / history
        # waited 50 min. sheets reads the store read-only and writes only its own files; 420 s because a partner slows it
        "sheets": dict(argv=_py("-m", "sports.sheets", "build", *lg, "--skip-results"), every_s=300,
                       timeout_s=600 if std else 300,
                       flag="SPORTS_INGEST", cap_mb=200, cls=HEAVY, prio=3, locks=("sheets",)),   # 388 s on 2026-11-02, killed twice at 420; 67 / 126 / 26 MB measured (synthetic 400-player league)
        # public Results (sports/results_public.py) as its OWN job (2026-11-08): inside sheets its ~230 s full-season
        # build was killed over the sheets cap (361 > 360 MB) every 5 minutes and took sheets / the heavy slot with it.
        # Every 30 min (results_public.REFRESH_S); read-only store, writes only results_public.json
        # "season-results" (review 2026-11-08): never beside settle-post, the other full-season build, so the two
        # never hold both heavy slots at once
        "results-public": dict(argv=_py("-m", "sports.sheets", "build", *lg, "--only", "results", "--force"),
                               every_s=1800, timeout_s=900, flag="SPORTS_INGEST", cap_mb=200, cls=HEAVY, prio=4,
                               locks=("results-public", "season-results")),
        # locks (review 2026-11-08): a heavy job without locks starts only when NO heavy job runs, and the other slot is
        # always backfilled first - mapping-audit sat 4 h unstarted. It reads the store and writes only its own
        # snapshots/<lg>/mapping_audit.json, so it pairs like sheets
        "mapping-audit": dict(argv=_py("-m", "sports.mapping_audit", *lg), every_s=3600, timeout_s=240,
                              flag="SPORTS_INGEST", cap_mb=250, cls=HEAVY, prio=3,
                              locks=("mapping-audit",)),  # one offer scan per league / hour
        "slips-ingest": dict(argv=_py("-m", "sports.slips", "ingest"), every_s=300, timeout_s=300,
                             flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=2),    # 23 MB
        # collection: the one collector, one job PER LEAGUE on a GAME-DAY cadence (fresh 2026-10-24, owner audit bug 1):
        # every SPORTS_INGEST_HOT_S (300) from SPORTS_INGEST_HOT_H (2) hours before that league's earliest upcoming
        # tip, SPORTS_INGEST_GAMEDAY_S (600) on a game day, SPORTS_INGEST_IDLE_S (900) otherwise; each successful
        # cycle starts the board build at once ("then"). --bucket-min 1: a 5-minute cycle is never skipped as 'done'.
        **{f"ingest-{x}": dict(argv=_py("-m", "sports.worker", "ingest", x, "--bucket-min", "1", "--sources",
                                        "espn,pp,ud,injuries,oddsapi,polymarket," + ("" if books_job else "books,")
                                        + "games"),
                               every_s=600 if std else 900, cadence=("gameday", (x,)), then=(f"ui-snapshots-{x}",), collector=True,
                               # 2026-10-31: a board shows only collected lines; 15 min overdue goes first (replay: 5 min
                               # stopped settle / forecasts, none left line gaps of 55 min)
                               urgent_after_s=900 if std else None,
                               timeout_s=900, flag="SPORTS_INGEST", cap_mb=300, cls=EXCLUSIVE, prio=3,
                               league=x, locks=("canonical", "books", "games", "cache"),
                               # 2026-11-01: the worker's own 300 s limit restarted any 300+ s cycle up to 3 times
                               # beside the old thread until this 900 s kill (ingest-nfl took 180-290 s, twice killed)
                               env={"SPORTS_WORKER_CONCURRENCY": "1", "SPORTS_JOB_TIMEOUT_S": "870"})
                            for x in lg},   # 191-197 MB both leagues
        # the books' own feeds (FanDuel / BetRivers, + OddsPapi when its guard allows) as their OWN light job
        # (owner 2026-11-06, book-cadence): inside a ~4-minute ingest cycle that waited behind settle (~20 min) the
        # quotes were read every 36-60 min and failed every consumer's freshness rule (market chance 15 min, Tools +EV
        # 30 min). Every minute on a game day it checks books.db (no request) and reads only the games the direct
        # ladder says are due (books/schedule.py: 3 / 5 / 8 / 8 min inside 2 / 8 / 24 / 72 h). Light: no heavy slot, so settle
        # / board builds / sheets never hold it back; its writes are short per-game transactions (books.db under the
        # books worker's lease, sc-1 offers in BEGIN IMMEDIATE with a 30 s busy timeout). SPORTS_BOOKS_JOB=0 puts the
        # books source back into the ingest cycles (rollback).
        **({"books": dict(argv=_py("-m", "sports.worker", "books", *lg), every_s=60, cadence=("books", tuple(lg)),
                          timeout_s=600, flag="SPORTS_INGEST", cap_mb=BOOKS_CAP_MB, cls=LIGHT, prio=2,
                          locks=("books",), env={"SPORTS_WORKER_CONCURRENCY": "1"},
                          # live 2026-11-06 22:19-22:25: a WAITING exclusive model-forward held every later start for
                          # 6+ min (and a running one blocks all starts for up to 15 min) - so the books job runs
                          # beside exclusive jobs; network-bound, 108 MB of the 1,200 MB budget on Standard
                          beside_exclusive=True)} if books_job else {}),
        "ingest-kalshi": dict(argv=_py("-m", "sports.worker", "ingest", *lg, "--bucket-min", "1", "--sources",
                                       "kalshi"),
                              every_s=1800 if std else 3600, cadence=("kalshi", tuple(lg)), then=tuple(f"ui-snapshots-{x}" for x in lg),
                              timeout_s=900, flag="SPORTS_INGEST", cap_mb=240, cls=HEAVY, prio=3,
                              locks=("canonical", "cache"), env={"SPORTS_WORKER_CONCURRENCY": "1", "SPORTS_JOB_TIMEOUT_S": "870"}),   # 70-84 MB (production 2026-10-25)
        # --no-post (2026-11-08): settling only (~64 s + ESPN reads); the results ledger + model / shadow scorecards
        # (~1,000 s and growing) made every run hit the 1,500 s kill - they run as "settle-post", started right after
        "settle": dict(argv=_py("-m", "sports.worker", "settle", *lg, "--no-post"), every_s=900 if std else 3600,
                       timeout_s=1500 if std else 900,      # 764 s beside a partner on 2026-11-01, still rising
                       then=("settle-post",),
                       # stall 2026-11-09: 0 starts in 72 min - a non-urgent heavy job never got a slot from the
                       # board builds / collectors (always urgent on Standard). Settlement feeds members' picks
                       urgent_after_s=900 if std else None,
                       flag="SPORTS_INGEST", cap_mb=240 if std else 120, cls=HEAVY, prio=4,
                       locks=("canonical", "books", "games", "cache", "model-daily"),      # never beside a collector
                       # Settlement also imports the model scorecard's native libraries. The old collector VM
                       # limit (320 MB) lets settlement finish but breaks that import. RSS remains watchdog-bound.
                       # the worker's own job limit just below this kill (bug sweep 2026-11-06 #12): the default 300 s
                       # timed out every long settle and restarted it beside the still-running first attempt
                       env={"SPORTS_WORKER_MAX_VM_MB": str(_env_num("SPORTS_JOB_SETTLE_VM_MB", 1280, int)),
                            "SPORTS_JOB_TIMEOUT_S": "1440"}
                       if std else {"SPORTS_JOB_TIMEOUT_S": "840"}),
        # after each settle run (then=) and on its own cadence: the model + shadow scorecards (learning.db / shadow.db),
        # then results.json per league (streamed through a spill file: memory flat with the season). Reads the store
        # only; never writes a settlement. Each event's scoring commits with its progress mark, so a killed run resumes
        # where it stopped; the scorecards go first so a killed ledger never starves them. model-daily: never beside
        # settle (whose settlements it reads) or the daily fit (which reads learning.db)
        # cap 150 (x1.8 = 270 MB on Standard, review 2026-11-08): measured peaks scorecards 56 MB, ledger 69-76 MB
        # (spill, flat with the season). The former 340 (612 MB) left no room for a board build (540) beside a light
        # job while settle-post ran - and it runs most of the time
        # stall 2026-11-09: its first production run held model-daily (and so settle) for the whole 2,400 s kill. Now
        # the scorecards stop between events after --budget-s (their per-event marks keep the progress; the next run
        # resumes), the ledger follows, and the job never starts while settle is due (yields_to) - settle waits at most
        # one bounded settle-post run (~budget + ledger, kill at timeout_s)
        # settle-post 2026-11-20: the run went 1,085 -> 1,214 s -> its 1,500 s kill (learning 943 s, 343 s past a budget
        # only checked between events, then the ledger's 430-650 s). Now the scorecards alone, the budget checked per
        # forecast (+ a 60 s statement backstop; the shadow scorecard keeps the last 15% as its own slice), so a run ends
        # near --budget-s at worst; the ledger is "results-ledger" below
        "settle-post": dict(argv=_py("-m", "sports.worker", "settle-post", *lg, "--budget-s",
                                     str(_env_num("SPORTS_SETTLE_POST_BUDGET_S", 600, int))),
                            every_s=900 if std else 3600,
                            timeout_s=1500 if std else 1800, flag="SPORTS_INGEST", cap_mb=150,
                            cls=HEAVY, prio=5, locks=("model-daily", "settle-post"),
                            yields_to=("settle",),
                            env={"SPORTS_WORKER_MAX_VM_MB": str(_env_num("SPORTS_JOB_SETTLE_VM_MB", 1280, int))}
                            if std else {}),
        # the results ledger (<league>/results.json: admin Results + the board's settled-by-model counts) as its OWN job
        # (2026-11-20, out of settle-post): a full-season rebuild (430-650 s live, growing with the season) that cannot
        # resume. Reads the store only, so no model-daily / canonical lock - it never holds settle back; "season-results"
        # keeps it off results-public (the other full-season build). Oldest league first, so a kill never starves one.
        # Admin-only file: hourly (the stall replays - test_sports_stall_1108 / test_sports_heavy_1118 - keep every
        # board-build / collector bound at prio 6 + 3,600 s; at 1,800 s one of them slides past its bound)
        "results-ledger": dict(argv=_py("-m", "sports.worker", "results-ledger", *lg), every_s=3600,
                               timeout_s=1500 if std else 1800, flag="SPORTS_INGEST", cap_mb=150, cls=HEAVY, prio=6,
                               locks=("results-ledger", "season-results"),
                               env={"SPORTS_WORKER_MAX_VM_MB": str(_env_num("SPORTS_JOB_SETTLE_VM_MB", 1280, int))}
                               if std else {}),
        # pre-game news archive (pgn-1, pregame_news/): injury report + depth charts through the shared caches and the
        # game summary of games starting within 2 h -> pregame_news.db, append-only, change-only. Research input only.
        "pregame-news": dict(argv=_py("-m", "sports.pregame_news", "run", *lg), every_s=900, timeout_s=300,
                             flag="SPORTS_INGEST", cap_mb=80, cls=LIGHT, prio=4, locks=("pregame-news",)),
        "picks-ingest": dict(argv=_py("-m", "sports.picks", "ingest"), every_s=60, timeout_s=120,
                             flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=2),    # member picks: spool -> picks.db
        "picks-settle": dict(argv=_py("-m", "sports.picks", "settle"), every_s=900 if std else 3600, timeout_s=600,
                             flag="SPORTS_INGEST", cap_mb=80, cls=LIGHT, prio=4),
        # game store backfill (current + previous season, resumable, time-bounded) so the factor engine behind the
        # player page (sports/player_context.py) has whole seasons, not only the games since launch
        # the engine's player-page context, out of the 3-minute ui-snapshots job (319 MB inline with whole seasons vs
        # its 250 MB cap); ui-snapshots embeds the latest result one player file at a time (199 MB)
        "player-context": dict(argv=_py("-m", "sports.player_context", "build", *lg), every_s=1800, timeout_s=600,
                               flag="SPORTS_INGEST", cap_mb=260, cls=HEAVY, prio=5, locks=("games",)),   # 170 MB, 13 s (1,724 games)
        "games-backfill": dict(argv=_py("-m", "sports.games.backfill_job", "--budget", "120"), every_s=900,
                               timeout_s=300, flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=6),   # 43-47 MB
        "slips-settle": dict(argv=_py("-m", "sports.slips", "settle"), every_s=900 if std else 3600, timeout_s=600,
                             flag="SPORTS_INGEST", cap_mb=60, cls=LIGHT, prio=4),    # 22 MB
        # REGISTER A9a: daily cal-mi fit (hysteresis) -> gradecheck -> containment. The child checks itself and exits
        # at once unless due: once per game-day, after 10:00 UTC (overnight settlement), only when something new
        # settled. Heavy slot (numpy fit + read-only gradecheck); SPORTS_CAL_AUTOFIT / SPORTS_GRADECHECK_DAILY = 0 skip
        # its steps. Reads learning.db / forecasts.db / canonical.db read-only; writes only model/*.json.
        # measured (synthetic, both leagues): 3.5k settled props 6.5 s / 72 MB, 10k props over 12 days 131 s / 96 MB;
        # the walk-forward refits once per game-day, so time grows ~ days^2 - one hour of headroom, once a day
        "grades-daily": dict(argv=_py("-m", "sports.model.daily", "run"), every_s=1800, timeout_s=3600,
                             flag="SPORTS_INGEST", cap_mb=300, cls=HEAVY, prio=6, locks=("model-daily",)),
        # forward forecasts, INFERENCE ONLY (sports/model/fwd.py): frozen hash-verified artifacts, no fit / learn /
        # retrain. SPORTS_FORECAST - independent of SPORTS_TRAIN (owner 2026-10-24). Exclusive: never beside ingestion.
        "model-forward": dict(argv=_py("-m", "sports.model.fwd", "run", *lg), every_s=1800, timeout_s=900,
                              # 2026-11-16: the run peaked past its 720 MB (400 x1.8) on the grown offer set and was killed 9 times
                              # in a row - 40 h with no new forecast, so the whole NBA slate showed no grade. 472 x1.8 = 850 MB: the
                              # most the stall replays allow (test_sports_stall_1108: at 900 MB nothing may run beside the waiting
                              # exclusive job inside the 1,200 MB child budget and the collectors back up).
                              flag="SPORTS_FORECAST", cap_mb=472 if std else 340, cls=EXCLUSIVE, prio=5),
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
                        timeout_s=900, flag="SPORTS_INGEST", cap_mb=200, cls=HEAVY, prio=6,
                        locks=("canonical", "cache")),
        # live 2026-11-22/23: peak ~308 MB at 60 players (the service sets a higher cap than 200 by env); five runs in a row hit
        # the 900 s timeout, so the job now stops starting players at a 720 s budget (snapshots.HISTORY_BUDGET_S) and exits 0
        # locks (review 2026-11-08): lockless, both waited for an empty heavy pair that backfill never leaves (a starved
        # archive lets the hot records table grow). They write canonical.db (archive moves cold offers in one
        # transaction, verify stamps verified_at): "canonical" keeps them off every store writer (collectors, settle,
        # history, kalshi), "archive" off each other; beside the read-only heavies (board builds, sheets, results)
        # 2026-11-17: the pass holds the "canonical" lock the NBA / NFL collectors need. A 20-min parts budget plus setup ran it 24-28 min
        # on the 23 GB store, past the 25-min freshness rule (sports/picks.py STALE_S): every pick'em line went stale and every Auto-build
        # slip with an NBA / NFL leg failed its recheck. A 10-min parts budget keeps the lock well under 25 min (same 6-h cadence: the
        # stall replays allow no more runs); the rest waits for the next pass.
        "archive": dict(argv=_py("-m", "sports.archive", "pass", "--time-budget-s", "600"), every_s=6 * 3600, timeout_s=1800,
                        flag="SPORTS_INGEST", cap_mb=200, cls=HEAVY, prio=6,
                        starve_after_s=ARCHIVE_STARVE_S,      # heavy-1118: not the global hour
                        locks=("canonical", "archive")),      # 140 MB (80k rows)
        "archive-verify": dict(argv=_py("-m", "sports.archive", "verify"), every_s=86400, timeout_s=1800,
                               flag="SPORTS_INGEST", cap_mb=120, cls=HEAVY, prio=8, locks=("canonical", "archive")),
    }
    # Separate opt-in collectors and stores; never feed an existing sport's model.
    for sport in ("soccer", "tennis"):
        if os.environ.get(f"SCOUT_{sport.upper()}_ENABLED") != "1":
            continue
        # ppsoccer-1119: soccer reads the PrizePicks soccer tabs in rotation, an adaptive number of requests a run
        # (AIMD on PrizePicks 429s, expansion_board._soccer_prizepicks) -> short runs every 45 s; 180 s with the
        # per-league tabs off (SCOUT_SOCCER_PP_LEAGUES=0)
        every = 45 if sport == "soccer" and os.environ.get("SCOUT_SOCCER_PP_LEAGUES", "1") != "0" else 180
        J[f"{sport}-quotes"] = dict(argv=_py("-m", "sports.expansion_board", sport), every_s=every,
            # Independent quote stores, measured 58/42 MB and ~4 s per live run.
            # Like books, keep fresh during long exclusive model jobs. Admission
            # still reserves the cap against the existing child memory budget.
            timeout_s=90, flag=f"SCOUT_{sport.upper()}_ENABLED", cap_mb=80, cls=LIGHT, prio=2,
            locks=(f"{sport}-quotes",))
        # Independent, bounded history stores (measured <60 MB including member
        # research publication). Do not starve cards behind long NBA/NFL fits.
        # LIGHT still reserves its full cap against the common child budget.
        J[f"{sport}-history"] = dict(argv=_py("-m", "sports.expansion_history", sport), every_s=300,
            timeout_s=180, flag=f"SCOUT_{sport.upper()}_ENABLED", cap_mb=120, cls=LIGHT, prio=6,
            locks=(f"{sport}-history",), serial_group=f"expansion:{sport}")
        # sg-stability 2026-11-20: LIGHT (was HEAVY prio 8). This job alone publishes the sport's grades
        # (forecasts.json); as a heavy prio-8 job it ran 2 / 1 times in 85 min live (start-to-start gaps 47-57 min in the
        # replays). Peaks 53 / 43 MB, runs 3-21 s. LIGHT keeps its full cap reserved against the child budget and its
        # serial group with history; 8-seed replay on the 2026-11-20 live run times (cs2_scout_ops/logs/
        # soccer_grades_1120/stability/sched_grid2.txt): learning gap 53 -> 6.6 min worst, soccer-quotes <= 3.3 min,
        # settle-post -8.3 min, prebuilt -6 min; player-context's worst seed +9.8 min (median 76.3 -> 75.9)
        J[f"{sport}-learning"] = dict(argv=_py("-m", "sports.expansion_learning", sport), every_s=180,
            timeout_s=45, flag=f"SCOUT_{sport.upper()}_ENABLED", cap_mb=100, cls=LIGHT, prio=8,
            locks=(f"{sport}-history",f"{sport}-learning"), serial_group=f"expansion:{sport}")
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
            if name in ("soccer-quotes", "tennis-quotes"):
                spec["every_s"] = max(30.0, spec["every_s"])   # never a PrizePicks run more often than every 30 s
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
            "books_hot_s": _env_num("SPORTS_BOOKS_HOT_S", 60.0),
            "books_gameday_s": _env_num("SPORTS_BOOKS_GAMEDAY_S", 60.0),
            "books_idle_s": _env_num("SPORTS_BOOKS_IDLE_S", 180.0),
            # site audit 2026-11-06 N1: the books job checks at its game-day pace while a game is within this many hours (the
            # books ladder reads every game inside 72 h every 3-8 min, so a quote stays under the 10-min Price check rule)
            "books_gameday_h": _env_num("SPORTS_BOOKS_GAMEDAY_H", 72.0),
            "kalshi_hot_s": _env_num("SPORTS_KALSHI_HOT_S", 600.0 if std else 1200.0),
            "kalshi_idle_s": _env_num("SPORTS_KALSHI_IDLE_S", 1800.0 if std else 3600.0)}


def _iso_ts(x):
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def league_phase(league, now_ts, snap_root=None, gameday_h=None):
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
    if live or (nxt is not None and nxt - now_ts <= (c["gameday_h"] if gameday_h is None else gameday_h) * 3600):
        return "gameday"
    return "idle"


def cadence_every(cadence, now_ts, snap_root=None):
    """Seconds between runs for a ('gameday' | 'kalshi' | 'books', leagues) cadence: the fastest phase over its
    leagues."""
    kind, leagues = cadence
    c = cadence_settings()
    phases = {league_phase(x, now_ts, snap_root) for x in leagues}
    if kind == "kalshi":
        return c["kalshi_hot_s"] if "hot" in phases else c["kalshi_idle_s"]
    if kind == "books":                 # the check is a books.db read; requests follow the direct ladder, not this
        phases = {league_phase(x, now_ts, snap_root, gameday_h=c["books_gameday_h"]) for x in leagues}
        if "hot" in phases:
            return c["books_hot_s"]
        return c["books_gameday_s"] if ("gameday" in phases or "unknown" in phases) else c["books_idle_s"]
    if "hot" in phases:
        return c["hot_s"]
    if "gameday" in phases or "unknown" in phases:
        return c["gameday_s"]
    return c["idle_s"]


# ---------------------------------------------------------------------------------------------------- scheduler
class Scheduler:
    def __init__(self, jobs=None, budget=None, now=time.time, tick_s=TICK_S, triggers=None, aging_s=None,
                 snap_root=None, max_heavy_n=None, runs_path=None):
        self.specs = jobs if jobs is not None else default_jobs()
        # recent run times survive a restart (the fill-in rule needs them right after a deploy): a small JSON file
        # the service passes (DATA_DIR/scheduler_runs.json); None keeps them in memory only (tests, the replay)
        self.runs_path = runs_path
        self.snap_root = snap_root
        self._cad = {}                                  # cadence -> (computed_at, every_s): re-read every 30 s
        self.budget = budget if budget is not None else budget_mb()
        self.max_heavy = max_heavy_n if max_heavy_n is not None else max_heavy()
        self.now = now
        self.tick_s = tick_s
        self.aging_s = aging_s if aging_s is not None else _env_num("SPORTS_JOB_AGING_S", 120.0)
        # stall 2026-11-09 (sports/RUNBOOK.md "Bounded waits"): 0 disables either rule
        self.drain_s = _env_num("SPORTS_JOB_EXCL_DRAIN_S", 600.0)
        self.starve_s = _env_num("SPORTS_JOB_STARVE_S", 3600.0)
        self.triggers = triggers or TRIGGERS
        self.running = {}                               # name -> (Popen, start_t)
        self.stop_ev = threading.Event()
        self.history = []                               # (t, event, name, detail) - bounded, for tests / ops
        t = self.now()
        for name, spec in self.specs.items():
            enabled = _job_on(spec["flag"])
            JOBS[name] = {"enabled": enabled, "flag": spec["flag"], "cls": spec["cls"], "cap_mb": spec["cap_mb"],
                          "every_s": spec["every_s"], "timeout_s": spec.get("timeout_s"),
                          "prio": spec["prio"], "pid": None, "starts": 0,
                          "last_start": None, "last_exit": None, "failures_in_a_row": 0, "over_cap": 0,
                          "rss_mb": None, "peak_rss_mb": 0.0, "last_peak_rss_mb": None, "last_ok_at": None,
                          # first run soon after start, staggered by priority so jobs do not pile up at boot
                          "next_start": (t + spec.get("first_s", 5 + 3 * spec["prio"])) if spec["every_s"] is not None
                          else None,
                          "queued_since": None,
                          "recent_ran_s": []}           # last RECENT_RUNS run times (s), any exit: the fill-in rule
        self._load_runs()

    # -- recent run times (the fill-in rule, review 2026-11-09) ----------------------------------------------------
    def _load_runs(self):
        if not self.runs_path:
            return
        try:
            import json
            with open(self.runs_path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return                                      # no file yet / unreadable: learn from the next runs
        if not isinstance(saved, dict):
            return
        for name, rec in saved.items():
            if name in JOBS and name in self.specs and isinstance(rec, list):
                ok = [float(x) for x in rec if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
                JOBS[name]["recent_ran_s"] = ok[-RECENT_RUNS:]

    def _save_runs(self):
        if not self.runs_path:
            return
        try:
            import json
            data = {n: JOBS[n]["recent_ran_s"] for n in self.specs
                    if n in JOBS and JOBS[n].get("recent_ran_s") and self.specs[n]["cls"] != LIGHT}
            tmp = f"{self.runs_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.runs_path)
        except OSError:                                 # a full / read-only disk never stops the scheduler
            pass

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

    @staticmethod
    def _beside(spec):
        """May run beside an exclusive job, which does not wait for it (the reservation total still holds both):
        beside_exclusive (the books job, 2026-11-06) and every LIGHT job (stall 2026-11-09: picks-ingest sat 65 min
        behind a waiting model-forward while member picks went unrecorded; lights are small, short and touch only
        their own stores)."""
        return bool(spec.get("beside_exclusive")) or spec["cls"] == LIGHT

    def _fits(self, name):
        spec = self.specs[name]
        # Explicit cross-class serialization for isolated history writers and their
        # learning readers. Existing jobs have no serial_group and are unchanged.
        group = spec.get("serial_group")
        if group and any(self.specs[n].get("serial_group") == group for n in self.running):
            return False
        others = [n for n in self.running if not self._beside(self.specs[n])]
        if any(self.specs[n]["cls"] == EXCLUSIVE for n in self.running) and not self._beside(spec):
            return False
        if spec["cls"] == EXCLUSIVE:
            return not others and (not self.running or self.reserved() + spec["cap_mb"] <= self.budget)
        if spec["cls"] == HEAVY:
            heavy = [self.specs[n] for n in self.running if self.specs[n]["cls"] == HEAVY]
            if len(heavy) >= self.max_heavy or not all(self._pairable(spec, h) for h in heavy):
                return False
        return self.reserved() + spec["cap_mb"] <= self.budget

    def _urgent(self, name, t):
        """A member-facing job overdue past its urgent_after_s: ahead of everything, however aged the others are.
        Any other non-exclusive job counts as urgent once overdue past starve_s (stall 2026-11-09: on Standard the
        board builds / collectors are always urgent, so settle, history, player-context ... never got a slot)."""
        return self._urgency(name, t) is not None

    def _urgency(self, name, t):
        """'member' (overdue past urgent_after_s), 'starved' (overdue past starve_s, not an exclusive job) or None."""
        spec, ns = self.specs[name], JOBS[name]["next_start"]
        if ns is None:
            return None
        after = spec.get("urgent_after_s")
        if after and t - ns >= after:
            return "member"
        starve = self._starve_after(name)
        if starve and spec["cls"] != EXCLUSIVE and t - ns >= starve:
            return "starved"
        return None

    def _starve_after(self, name):
        """Seconds overdue after which a non-exclusive job counts as starved: SPORTS_JOB_STARVE_S (0 = the rule is off),
        or the job's own shorter starve_after_s (heavy-1118, ARCHIVE_STARVE_S: the archive shares the canonical lock with
        the collectors / settle / history, which keep that lane busy; at the global hour a deploy every hour or two
        reset it before it ever ran)."""
        if not self.starve_s:
            return 0.0
        own = self.specs[name].get("starve_after_s")
        return min(self.starve_s, float(own)) if own else self.starve_s

    def _draining(self, due, t):
        """The exclusive job that has waited drain_s or longer (the first in due order), else None. While one drains
        no other job starts unless it may run beside it, so the running ones finish and it gets the budget: before
        2026-11-09 urgent jobs passed a waiting exclusive job forever and model-forward waited 72+ min."""
        if not self.drain_s:
            return None
        for n in due:
            qs = JOBS[n]["queued_since"]
            if self.specs[n]["cls"] == EXCLUSIVE and qs is not None and t - qs >= self.drain_s:
                return n
        return None

    def _left_s(self, names):
        """Seconds until the last of the running jobs `names` is expected to end: per job the SHORTEST of its recent
        runs minus the time it has run so far, so the estimate errs early. A job with no recorded run (its first since
        the run-times file began) counts its timeout - the scheduler kills it there - so a short job may still use the
        idle slot; if the job ends sooner, the waiting job waits at most for that one short run."""
        t, left = self.now(), 0.0
        for n in names:
            if n not in self.running:
                continue
            rec = JOBS[n].get("recent_ran_s")
            est = min(rec) if rec else float(self.specs[n].get("timeout_s") or 0.0)
            left = max(left, est - (t - self.running[n][1]))
        return left

    def _ends_first(self, name, names):
        """Is the heavy job `name` expected to end before the running jobs `names` that another job waits for (review
        2026-11-09)? The LONGEST of its recent runs, plus a tick, against _left_s: it finishes first, so the waiting job
        starts no later - a board build (60-220 s) uses the idle heavy slot beside a collector (290-360 s) or the
        archive instead of the slot sitting empty. Never before the job's first recorded run."""
        rec = JOBS[name].get("recent_ran_s")
        return (self.specs[name]["cls"] == HEAVY and bool(rec) and bool(names)
                and max(rec) + self.tick_s < self._left_s(names))

    def _blocking(self, name):
        """The running jobs `name` cannot start beside (an exclusive job: every non-beside one; a heavy job: a shared
        store lock, the same league's collector / board build, or a running exclusive job)."""
        spec = self.specs[name]
        out = []
        for n in self.running:
            other = self.specs[n]
            if self._beside(other):
                continue
            if spec["cls"] == EXCLUSIVE or other["cls"] == EXCLUSIVE or not self._pairable(spec, other):
                out.append(n)
        return out

    def _may_pass(self, name, held):
        """May `name` start while the urgent job `held` waits? Only if it leaves that job its slot and memory - or
        (review 2026-11-09) `held` waits on a store lock / an exclusive job that runs longer than `name` will."""
        spec, want = self.specs[name], self.specs[held]
        if spec["cls"] == LIGHT:
            return True
        if spec["cls"] == EXCLUSIVE:
            return False
        if self._pairable(spec, want):
            heavy = sum(1 for n in self.running if self.specs[n]["cls"] == HEAVY)
            if heavy + 1 < self.max_heavy and self.reserved() + spec["cap_mb"] + want["cap_mb"] <= self.budget:
                return True
        return self._ends_first(name, self._blocking(held))

    def _yield_blocked(self, name, waiting):
        """`name` gives way to a due job in its yields_to (settle-post -> settle: they share the model-daily lock)."""
        return any(y in waiting for y in self.specs[name].get("yields_to") or ())

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
            over = max(0.0, t - ns)
            kind = self._urgency(n, t)
            if kind == "starved":
                # among urgent jobs a starved one ages from the moment it became urgent: it takes its turn between
                # the member-facing jobs (each slot it waits for is held), it does not jump all of them at once
                over -= self._starve_after(n)
            return (kind is None, self.specs[n]["prio"] - over / self.aging_s, ns)
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
        if ran > 0:                                     # a spawn failure (0 s) says nothing about the run time
            st["recent_ran_s"] = (list(st.get("recent_ran_s") or []) + [round(ran, 1)])[-RECENT_RUNS:]
            if spec["cls"] != LIGHT:                    # lights never block anyone: not worth a write
                self._save_runs()
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
                        STATS["recent_timeouts"] = (STATS["recent_timeouts"] + [
                            {"job": name, "at": t, "ran_s": round(t - t0, 1),
                             "peak_rss_mb": st.get("last_peak_rss_mb")}])[-10:]   # last_exit is gone after the next run
                    del self.running[name]
                    self._finish(name, "timeout", t - t0)
                continue
            del self.running[name]
            self._finish(name, rc, t - t0)
        with _LOCK:
            STATS["peak_children_rss_mb"] = round(max(STATS["peak_children_rss_mb"], total), 1)
        held = None                                     # an urgent job waiting for a slot
        due = self.due()
        waiting = set(due)
        # an exclusive job waiting for everything else to finish; one that has waited drain_s holds every start from
        # the top of the queue (urgent ones too), not only the starts after it
        excl = self._draining(due, t)
        for name in due:
            spec = self.specs[name]
            if self._yield_blocked(name, waiting):
                # e.g. settle-post (shares model-daily with settle) never takes the lock while settle is due
                if JOBS[name]["queued_since"] is None:
                    JOBS[name]["queued_since"] = t
                continue
            if excl is not None and name != excl:
                # behind a waiting exclusive job only a job that may run beside it (books, the light jobs) may start,
                # and only if the exclusive job still fits beside every such job once the others (which it waits for
                # anyway) are done - so neither ever starves the other
                beside = sum(self.specs[n]["cap_mb"] for n in self.running if self._beside(self.specs[n]))
                if self._beside(spec) and self._fits(name) and \
                        beside + spec["cap_mb"] + self.specs[excl]["cap_mb"] <= self.budget:
                    self.start(name)
                    continue
                # a heavy job that ends before the running ones may take the idle heavy slot: the exclusive job
                # waits for those anyway (review 2026-11-09: the drain had stopped every board build for ~5 min with
                # one heavy slot empty while a collector finished)
                if (held is None or self._may_pass(name, held)) and self._fits(name) and \
                        self._ends_first(name, self._blocking(excl)):
                    self.start(name)
                    continue
                if JOBS[name]["queued_since"] is None:
                    JOBS[name]["queued_since"] = t
                continue
            if (held is None or self._may_pass(name, held)) and self._fits(name):
                self.start(name)
                continue
            if JOBS[name]["queued_since"] is None:
                JOBS[name]["queued_since"] = t
            with _LOCK:
                STATS["deferred_ticks"] += 1
            if self.specs[name]["cls"] == EXCLUSIVE:
                if excl is None:
                    excl = name                         # hold everything behind a waiting exclusive job (no starvation)
                continue
            if held is None and self._urgent(name, t):
                held = name                             # nothing may take the slot it is waiting for

    def run(self):
        while not self.stop_ev.is_set():
            try:
                self.tick()
            except Exception as e:                      # noqa: BLE001 - the scheduler never dies
                sys.stderr.write(f"  sports scheduler tick failed: {type(e).__name__}: {str(e)[:200]}\n")
            self.stop_ev.wait(self.tick_s)
        self.terminate_all()

    def terminate_all(self, grace_s=None):
        """sg-stability 2026-11-20: on a stop (deploy SIGTERM) every running child first gets SIGTERM and up to grace_s
        (SPORTS_CHILD_TERM_GRACE_S, 8 s) to checkpoint and exit - the soccer / tennis history job saves its catalog and
        cursor on it - and only then the kill. Before, children were killed at once and a history run lost its
        in-memory catalog marks and cursor (the next runs re-read those summaries). -> {name: exit}."""
        grace = _env_num("SPORTS_CHILD_TERM_GRACE_S", 8.0) if grace_s is None else grace_s
        out = {}
        procs = list(self.running.items())
        for _name, (p, _t0) in procs:
            try:
                p.terminate()
            except Exception:                           # noqa: BLE001 - already gone
                pass
        deadline = time.monotonic() + max(0.0, grace)
        for name, (p, t0) in procs:
            rc = None
            try:
                rc = p.wait(max(0.0, deadline - time.monotonic()))
            except Exception:                           # noqa: BLE001 - TimeoutExpired: killed below
                _kill(p)
            self.running.pop(name, None)
            out[name] = "stopped" if rc is None else f"stopped (exit {rc})"
            self._finish(name, out[name], 0.0)          # 0.0: a cut run never enters recent_ran_s (fill-in rule)
        return out

    def stop(self):
        self.stop_ev.set()


def state():
    with _LOCK:
        s = dict(STATS)
    mem = memory_mb()
    return {"budget_mb": budget_mb(mem), "memory_mb": mem, "memory_source": memory_source(),
            "cap_scale": round(cap_scale(mem), 3), "max_heavy": max_heavy(mem), "stats": s, "jobs": {k: dict(v) for k, v in JOBS.items()}}
