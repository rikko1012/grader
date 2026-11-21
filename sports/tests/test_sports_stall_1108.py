"""Scheduler stall 2026-11-09 (deploy c31637d, uptime ~72 min on Render Standard, budget 1,200 MB, two heavy slots):
model-forward (exclusive) queued 71 min with 0 starts, settle 0 starts, picks-ingest / slips-ingest / picks-settle /
pregame-news not run for ~60 min, while the board builds, collectors, prebuilt and books kept running.

Cause (sports/tests/sched_sim.py, scripts/sim_sports_scheduler.py): the heavy slots are oversubscribed by member-facing
jobs that are always urgent on Standard (board builds every 60 s at 150-220 s, prebuilt every 45 s, collectors every
3-5 min at 290-360 s). Urgent jobs passed a waiting exclusive job, so the system never drained and model-forward never
started; meanwhile the waiting exclusive job held every later start - every LIGHT job included - and a non-urgent heavy
job (settle) never got a slot. The rule predates c31637d (ecfa81a replays the same first hour); there model-forward
failed with exit 1 and sat in its 1-8 h backoff, so most of the time nothing waited and the lights ran.

Fix (scheduling only - no grade / chance / stake / projection / settlement change):
  * LIGHT jobs run beside a waiting or running exclusive job when the budget holds both (like books);
  * an exclusive job that waited SPORTS_JOB_EXCL_DRAIN_S (600 s) stops every other non-light start -> it starts;
  * settle is urgent after 900 s overdue; any other job overdue past SPORTS_JOB_STARVE_S (3600 s) counts as urgent;
  * settle-post never starts while settle is due and stops its scorecards after --budget-s (resumed next run).
Review 2026-11-09: the drain stopped the board builds even with a heavy slot idle (one collector finishing) and the
board gaps grew past c31637d - now a heavy job whose recent runs end before the running jobs fills the slot (it never
delays the waiting job), also past an urgent job held on a store lock; recent run times persist across restarts."""
import pytest

from sports import config, scheduler, store
from sports.tests import sched_sim as sim

LIGHTS = ("picks-ingest", "slips-ingest", "picks-settle", "slips-settle", "pregame-news", "games-backfill", "books")


def _intervals(res):
    """[(job, start, end)] in seconds since the simulation start, from the scheduler's own events."""
    open_, out = {}, []
    for t, ev, name, _d in res["history"]:
        if ev == "start":
            open_[name] = t - sim.T0
        elif ev == "exit" and name in open_:
            out.append((name, open_.pop(name), t - sim.T0))
    out += [(n, s, res["end"]) for n, s in open_.items()]
    return out


def _overlap(iv, a, b):
    return [(x, y) for x, s1, e1 in iv for y, s2, e2 in iv if x == a and y == b and s1 < e2 and s2 < e1]


@pytest.fixture(scope="module")
def six_hours():
    res = sim.simulate(scheduler, hours=6.0, run_s=sim.NOV9_RUN_S)        # the incident's run times (prebuilt 59 s)
    print("\n" + sim.summary(res))
    return res


# ---------------------------------------------------------------------------------------------- the incident itself
def test_the_old_rules_reproduce_the_stall(monkeypatch):
    """Lights blocked by a waiting exclusive job (the pre-fix _beside) and no drain: the live picture after 72 min."""
    monkeypatch.setattr(scheduler.Scheduler, "_beside", staticmethod(lambda spec: bool(spec.get("beside_exclusive"))))

    def c31637d(J):                                         # the job table as deployed
        J["settle"].pop("urgent_after_s")
        sp = J["settle-post"]
        sp.pop("yields_to")
        sp["argv"] = sp["argv"][:sp["argv"].index("--budget-s")]
        sp["timeout_s"] = 2400
        J["prebuilt"]["every_s"] = 45.0                     # heavy-1118 changed these two; c31637d had neither
        J["archive"].pop("starve_after_s", None)
    res = sim.simulate(scheduler, hours=1.2, run_s=sim.NOV9_RUN_S,
                       env={"SPORTS_JOB_EXCL_DRAIN_S": "0", "SPORTS_JOB_STARVE_S": "0"},
                       jobs_hook=c31637d)
    assert len(res["starts"]["settle-post"]) == 1                          # one long first run (live: 2,400 s kill)
    assert res["starts"].get("model-forward", []) == []                     # queued from boot, never started
    for n in ("picks-ingest", "slips-ingest", "picks-settle", "pregame-news"):
        assert res["waits"][n] >= 45 * 60, (n, res["waits"][n])            # live: 57-65 min overdue
    for n in ("history", "grades-daily", "archive", "games-backfill"):
        assert res["starts"].get(n, []) == [], n                            # live: 0 starts
    assert len(res["starts"]["books"]) >= 60                                # books (beside_exclusive) kept running
    for n in ("ui-snapshots-nba", "ui-snapshots-nfl", "prebuilt", "ingest-nba"):
        assert len(res["starts"][n]) >= 3, n                                # ... and the urgent member-facing jobs


# ------------------------------------------------------------------------------------------------- bounded waits
def test_picks_ingest_and_every_light_job_wait_minutes_not_an_hour(six_hours):
    r = six_hours
    for n in LIGHTS:
        assert r["waits"][n] <= 5 * 60, (n, r["waits"][n])
    assert r["max_gap"]["picks-ingest"] <= 6 * 60                          # every 60 s; never > 6 min apart
    assert len(r["starts"]["picks-ingest"]) >= 300


def test_model_forward_starts_within_its_drain_bound(six_hours):
    r = six_hours
    drain, longest_heavy = 600, max(sim.RUN_S["ingest-nfl"], 600 + sim.LEDGER_S)   # + the slowest job to drain
    assert r["waits"]["model-forward"] <= drain + longest_heavy + 120, r["waits"]["model-forward"]
    assert len(r["starts"]["model-forward"]) >= 7                           # every_s 1800: ~every 42 min
    iv = _intervals(r)
    for other in r["specs"]:                                                # still alone among the non-light jobs
        if other != "model-forward" and not scheduler.Scheduler._beside(r["specs"][other]):
            assert _overlap(iv, "model-forward", other) == [], other


def test_settle_gets_its_turn_and_settle_post_never_holds_it_long(six_hours):
    r = six_hours
    assert r["waits"]["settle"] <= 45 * 60, r["waits"]["settle"]           # c31637d: 0 starts in 72 min
    assert len(r["starts"]["settle"]) >= 7
    assert all(code == 0 for _t, code in r["ends"]["settle"])
    iv = _intervals(r)
    assert _overlap(iv, "settle", "settle-post") == []                      # model-daily: never together
    assert _overlap(iv, "results-ledger", "results-public") == []           # season-results (review 2026-11-08)
    for n, s, e in iv:
        if n == "settle-post":
            assert e - s <= r["specs"]["settle-post"]["timeout_s"]          # budget-bounded, never killed
    assert all(code == 0 for _t, code in r["ends"]["settle-post"])


def test_nothing_starves_and_the_earlier_fixes_hold(six_hours):
    r = six_hours
    assert r["reserved_ok"] and r["heavy_ok"]                               # budget 1,200 MB, two heavy slots
    for n in r["waits"]:
        assert r["starts"].get(n), n                                        # every enabled job ran at least once
    for n in ("sheets", "results-public", "mapping-audit", "archive", "history", "player-context", "grades-daily"):
        assert r["waits"][n] <= 100 * 60, (n, r["waits"][n])               # bounded (live: 0 starts / 4 h)
    # heavy-1118: prebuilt every 600 s (was 45 s): ~18-36 runs in 6 h; at 45 s this replay gave 25+
    assert len(r["starts"]["prebuilt"]) >= 18
    assert "--skip-results" in r["specs"]["sheets"]["argv"]
    assert r["specs"]["settle"]["then"] == ("settle-post",) and "--no-post" in r["specs"]["settle"]["argv"]


# --------------------------------------------------------------- review 2026-11-09: what the drain costs the boards
# c31637d on the same replay (seed 1108, 6 h): longest start-to-start gap in minutes. scripts/sim_sports_scheduler.py
# --ref c31637d --seeds 8 prints the medians over 8 jitter seeds (RUNBOOK "Bounded waits").
C31637D_GAP = {"ok": {"ui-snapshots-nba": 25.8, "ui-snapshots-nfl": 21.8},
               "long": {"ui-snapshots-nba": 35.8, "ui-snapshots-nfl": 30.4}}
# heavy-1118: prebuilt every 600 s (was 45 s: 17.2 / 30.4 here against a 270 s max age; now a 1,200 s max age):
# measured 21.8 / 29.8 on this replay (seed 1108). The longest gap is set by model-forward (its drain + run stop every
# heavy start), not by the cadence. Bounded at the measured value + ~1 min so a further slide fails.
PREBUILT_GAP = {"ok": 23.0, "long": 31.0}
# the collectors (canonical lock) pay for settle / history / archive now running and one model-forward drain per run:
# bounded here at the measured value + ~2 min so a further slide fails (c31637d: 25.8 ok / 35.8 long)
COLLECTOR_GAP = {"ok": 34.0, "long": 54.0}
LONG_RUN_S = {"settle-post": 900, "grades-daily": 1200, "archive": 1500}


@pytest.fixture(scope="module", params=["ok", "long"])
def replay(request):
    kw = {"run_s": dict(sim.NOV9_RUN_S, **(LONG_RUN_S if request.param == "long" else {}))}
    return request.param, sim.simulate(scheduler, hours=6.0, probe=True, **kw)


def test_the_drain_never_leaves_a_heavy_slot_idle_that_a_board_build_could_use(replay):
    """By the MODELLED run times: no tick with a heavy slot empty while a due board build fitted and would have ended
    before the running heavy jobs (eeb392a: ~28 min of such ticks in 6 h; c31637d ~23 min, its urgent-slot hold)."""
    _sc, r = replay
    assert r["idle_board_s"] == 0, r["idle_board_s"]


def test_board_builds_and_prebuilt_keep_c31637d_gaps_and_collectors_are_bounded(replay):
    sc, r = replay
    for n, base in C31637D_GAP[sc].items():
        assert r["max_gap"][n] <= (base + 1.0) * 60, (sc, n, r["max_gap"][n] / 60, base)
    assert r["max_gap"]["prebuilt"] <= PREBUILT_GAP[sc] * 60, (sc, r["max_gap"]["prebuilt"] / 60)
    for n in ("ui-snapshots-nba", "ui-snapshots-nfl"):
        assert len(r["starts"][n]) >= (25 if sc == "ok" else 20), (sc, n)
    for n in ("ingest-nba", "ingest-nfl"):
        assert r["max_gap"][n] <= COLLECTOR_GAP[sc] * 60, (sc, n, r["max_gap"][n] / 60)
    assert len(r["starts"]["model-forward"]) >= 7                           # c31637d: 4 in 6 h
    assert r["waits"]["model-forward"] <= (20 if sc == "ok" else 32) * 60, r["waits"]["model-forward"]
    assert r["reserved_ok"] and r["heavy_ok"]


# ---------------------------------------------------------------------------------------------- the rules, one by one
class _P:
    n = 0

    def __init__(self):
        _P.n += 1
        self.pid, self.rc = 80000 + _P.n, None

    def poll(self):
        return self.rc


def _sched(monkeypatch, jobs, budget=1200, runs_path=None, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(scheduler, "JOBS", {})
    monkeypatch.setattr(scheduler, "STATS", {k: 0 if isinstance(v, (int, float)) else v
                                             for k, v in scheduler.STATS.items()})
    monkeypatch.setattr(scheduler, "rss_mb", lambda pid: 1.0)
    monkeypatch.setattr(scheduler, "spawn", lambda spec: _P())
    clock = [1_794_190_000.0]
    sch = scheduler.Scheduler(jobs=jobs, budget=budget, now=lambda: clock[0], max_heavy_n=2, runs_path=runs_path)
    for n in jobs:
        scheduler.JOBS[n]["next_start"] = clock[0]
    return sch, clock


def _j(cls, cap, prio, every=60, **kw):
    return dict(argv=["x"], every_s=every, timeout_s=10_000, flag="SPORTS_API", cap_mb=cap, cls=cls, prio=prio, **kw)


def test_a_draining_exclusive_job_stops_urgent_heavies_but_not_the_lights(monkeypatch):
    """Two urgent board builds take turns so one always runs (production): before the drain they pass the waiting
    exclusive job; once it has waited 600 s neither restarts, the running one ends and the exclusive job starts.
    The light job runs throughout."""
    jobs = {"b1": _j("heavy", 400, 1, every=1, urgent_after_s=1, locks=("1",)),     # due again 1 s after it ends
            "b2": _j("heavy", 400, 1, every=1, urgent_after_s=1, locks=("2",)),
            "excl": _j("exclusive", 720, 5),
            "picks": _j("light", 108, 2)}
    sch, clock = _sched(monkeypatch, jobs, SPORTS_JOB_EXCL_DRAIN_S="600")
    t0 = clock[0]
    sch.tick()
    assert set(sch.running) == {"b1", "b2", "picks"} and scheduler.JOBS["excl"]["queued_since"] == t0
    excl_at, picks_runs, build_starts_late, early = None, 0, [], 0
    for _ in range(60):
        clock[0] += 30
        builds = sorted((t, n) for n, (_p, t) in sch.running.items() if n in ("b1", "b2"))
        if builds:
            sch.running[builds[0][1]][0].rc = 0                     # the older build ends; the other still runs
        if "picks" in sch.running:
            sch.running["picks"][0].rc = 0
        before = set(sch.running)
        sch.tick()
        new = set(sch.running) - before
        picks_runs += "picks" in new
        if clock[0] - t0 >= 600 + 30:
            build_starts_late += [n for n in new if n in ("b1", "b2")]
        if "excl" in sch.running:
            excl_at = clock[0] - t0
            break
        if clock[0] - t0 < 600:
            early += len(new & {"b1", "b2"})                        # urgent builds pass before the drain
            assert {"b1", "b2"} & set(sch.running)                  # one of them always runs
    assert excl_at is not None and 600 <= excl_at <= 600 + 2 * 30, excl_at
    assert early >= 8 and build_starts_late == []                   # nothing heavy restarted while draining
    assert picks_runs >= 5                                          # the light job kept running
    clock[0] += 61
    scheduler.JOBS["picks"]["next_start"] = clock[0]
    if "picks" in sch.running:
        sch.running["picks"][0].rc = 0
        sch.tick()
        clock[0] += 61
    sch.tick()
    assert {"picks", "excl"} <= set(sch.running)                    # a light job beside the running exclusive job
    assert not ({"b1", "b2"} & set(sch.running))


def _end(sch, name):
    sch.running[name][0].rc = 0


def _drain_setup(monkeypatch, board_runs=(150.0,), collector_runs=(360.0,), **env):
    """A collector (360 s) runs, the exclusive job has waited past the drain, a board build is due."""
    jobs = {"collector": _j("heavy", 540, 3, every=10_000, locks=("canonical",)),
            "board": _j("heavy", 540, 2, every=1, urgent_after_s=1, locks=("snapshots",)),
            "excl": _j("exclusive", 720, 5, every=10_000)}
    sch, clock = _sched(monkeypatch, jobs, SPORTS_JOB_EXCL_DRAIN_S="600", **env)
    scheduler.JOBS["board"]["next_start"] = clock[0] + 10_000                # not due yet
    scheduler.JOBS["excl"]["next_start"] = clock[0] + 10_000
    sch.tick()
    assert set(sch.running) == {"collector"}
    scheduler.JOBS["collector"]["recent_ran_s"] = list(collector_runs)
    scheduler.JOBS["board"]["recent_ran_s"] = list(board_runs)
    scheduler.JOBS["excl"]["next_start"] = clock[0]
    scheduler.JOBS["excl"]["queued_since"] = clock[0] - 700                  # drained
    scheduler.JOBS["board"]["next_start"] = clock[0]
    return sch, clock


def test_a_board_build_fills_the_idle_slot_while_draining_and_the_exclusive_job_starts_on_time(monkeypatch):
    """review 2026-11-09: during the drain one heavy slot sat empty for ~5 min while a collector finished and every
    board build was held. Now a build whose runs end before the collector takes it; the exclusive job still starts
    the moment the collector ends."""
    sch, clock = _drain_setup(monkeypatch)
    t0 = clock[0]
    sch.tick()
    assert set(sch.running) == {"collector", "board"}                       # 150 s < 360 s left
    clock[0] = t0 + 150
    _end(sch, "board")
    sch.tick()                                                              # reaped (its next run is due in ~1 s)
    clock[0] += 2
    sch.tick()                                                              # 150 s again < 208 s left: once more
    assert set(sch.running) == {"collector", "board"}
    clock[0] = t0 + 301
    _end(sch, "board")
    sch.tick()
    clock[0] += 2
    sch.tick()                                                              # 150 s > 57 s left: held for the drain
    assert set(sch.running) == {"collector"} and scheduler.JOBS["board"]["next_start"] <= clock[0]
    clock[0] = t0 + 360
    _end(sch, "collector")
    sch.tick()
    assert set(sch.running) == {"excl"}                                     # not a second later than without builds


def test_no_fill_in_when_the_build_would_outlast_the_running_job_or_has_no_recorded_run(monkeypatch):
    sch, clock = _drain_setup(monkeypatch, board_runs=(150.0, 380.0))       # its LONGEST recent run counts
    sch.tick()
    assert set(sch.running) == {"collector"}
    sch, clock = _drain_setup(monkeypatch, board_runs=())                   # never before its first run
    sch.tick()
    assert set(sch.running) == {"collector"}
    sch, clock = _drain_setup(monkeypatch, collector_runs=(140.0, 360.0))   # the collector's SHORTEST run counts
    sch.tick()
    assert set(sch.running) == {"collector"}


def test_a_running_job_with_no_recorded_run_counts_its_timeout(monkeypatch):
    sch, clock = _drain_setup(monkeypatch, collector_runs=())               # timeout 10,000 s: a build fits
    assert sch._left_s(["collector"]) == pytest.approx(10_000)
    sch.tick()
    assert set(sch.running) == {"collector", "board"}


def test_a_short_job_passes_an_urgent_job_held_on_a_store_lock(monkeypatch):
    """c31637d too: an urgent collector waiting on the archive's canonical lock held the free slot for the archive's
    whole run, so no board build started (prod replay ~23 min of idle slot in 6 h). A build that ends before the
    archive does not delay the collector."""
    jobs = {"archive": _j("heavy", 360, 6, every=10_000, locks=("canonical", "archive")),
            "collector": _j("heavy", 540, 3, every=1, urgent_after_s=1, locks=("canonical",)),
            "board": _j("heavy", 540, 4, every=1, locks=("snapshots",))}
    sch, clock = _sched(monkeypatch, jobs)
    scheduler.JOBS["collector"]["next_start"] = clock[0] + 10_000
    scheduler.JOBS["board"]["next_start"] = clock[0] + 10_000
    sch.tick()
    assert set(sch.running) == {"archive"}
    scheduler.JOBS["archive"]["recent_ran_s"] = [600.0]
    scheduler.JOBS["board"]["recent_ran_s"] = [150.0]
    scheduler.JOBS["collector"]["next_start"] = clock[0] - 5                # urgent, blocked by canonical
    scheduler.JOBS["board"]["next_start"] = clock[0]
    sch.tick()
    assert set(sch.running) == {"archive", "board"}
    clock[0] += 500                                                          # 100 s left on the archive
    _end(sch, "board")
    scheduler.JOBS["board"]["next_start"] = clock[0]
    sch.tick()
    assert set(sch.running) == {"archive"}                                   # 150 s > 100 s: the slot stays held
    clock[0] += 100
    _end(sch, "archive")
    sch.tick()
    assert "collector" in sch.running


def test_recent_run_times_persist_across_a_restart(monkeypatch, tmp_path):
    import json
    f = tmp_path / "scheduler_runs.json"
    jobs = {"h": _j("heavy", 300, 1, locks=("h",)), "l": _j("light", 50, 2)}
    sch, clock = _sched(monkeypatch, jobs, runs_path=f)
    sch.tick()
    assert set(sch.running) == {"h", "l"}
    clock[0] += 42
    _end(sch, "h")
    _end(sch, "l")
    sch.tick()
    assert json.loads(f.read_text(encoding="utf-8")) == {"h": [42.0]}        # lights are not written
    _sched(monkeypatch, jobs, runs_path=f)
    assert scheduler.JOBS["h"]["recent_ran_s"] == [42.0] and scheduler.JOBS["l"]["recent_ran_s"] == []
    f.write_text("{not json", encoding="utf-8")
    _sched(monkeypatch, jobs, runs_path=f)
    assert scheduler.JOBS["h"]["recent_ran_s"] == []                         # unreadable: learn again
    f.write_text(json.dumps({"h": [1, "x", -3, True, 2, 3, 4], "gone": [5]}), encoding="utf-8")
    _sched(monkeypatch, jobs, runs_path=f)
    assert scheduler.JOBS["h"]["recent_ran_s"] == [2.0, 3.0, 4.0]            # numbers > 0 only, last 3
    assert "gone" not in scheduler.JOBS
    sch, clock = _sched(monkeypatch, jobs, runs_path=tmp_path / "missing" / "x.json")   # unwritable: no error
    sch.tick()
    clock[0] += 7
    _end(sch, "h")
    sch.tick()
    assert scheduler.JOBS["h"]["recent_ran_s"] == [7.0] and not (tmp_path / "missing").exists()


def test_the_service_persists_run_times_in_the_data_dir(monkeypatch, tmp_path):
    from sports import service
    seen = {}

    class FakeScheduler:
        def __init__(self, jobs=None, runs_path=None):
            seen["runs_path"] = runs_path
            self.stop_ev = None

        def run(self):
            pass
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "Scheduler", FakeScheduler)
    monkeypatch.setattr(service, "_SCHED", dict(service._SCHED))
    service.supervise()
    assert seen["runs_path"] == tmp_path / "scheduler_runs.json"


def test_a_light_job_never_takes_the_exclusive_jobs_memory(monkeypatch):
    jobs = {"excl": _j("exclusive", 1000, 5), "heavy": _j("heavy", 300, 1, locks=("h",)),
            "light": _j("light", 250, 6)}                                   # queued behind the exclusive job
    sch, clock = _sched(monkeypatch, jobs)
    sch.tick()
    assert set(sch.running) == {"heavy"}                                   # 1000 + 250 > 1200: the light waits
    clock[0] += 5
    sch.running["heavy"][0].rc = 0
    sch.tick()
    assert set(sch.running) == {"excl"}


def test_settle_post_yields_to_a_due_settle(monkeypatch):
    jobs = {"collector": _j("heavy", 540, 1, locks=("canonical",)),
            "settle": _j("heavy", 432, 4, locks=("canonical", "model-daily")),
            "settle-post": _j("heavy", 270, 3, locks=("model-daily", "settle-post"), yields_to=("settle",))}
    sch, clock = _sched(monkeypatch, jobs)
    sch.tick()
    # settle waits for the collector (canonical); settle-post would fit beside it but must not take model-daily
    assert set(sch.running) == {"collector"}
    clock[0] += 5
    sch.running["collector"][0].rc = 0
    sch.tick()
    assert "settle" in sch.running and "settle-post" not in sch.running
    clock[0] += 5
    sch.running["settle"][0].rc = 0
    sch.tick()
    assert "settle-post" in sch.running


def test_a_starved_job_becomes_urgent_and_takes_the_next_slot(monkeypatch):
    jobs = {"b1": _j("heavy", 500, 1, urgent_after_s=1, locks=("1",)),
            "b2": _j("heavy", 500, 1, urgent_after_s=1, locks=("2",)),
            "history": _j("heavy", 200, 6, locks=("h",))}
    sch, clock = _sched(monkeypatch, jobs, SPORTS_JOB_STARVE_S="3600")
    starts = []
    for _ in range(200):                                                    # the builds always end due + urgent
        clock[0] += 30
        for n, (p, _t) in list(sch.running.items()):
            p.rc = 0
            scheduler.JOBS[n]["next_start"] = clock[0] - 5
        before = set(sch.running)
        sch.tick()
        starts += [n for n in sch.running if n not in before]
        if "history" in starts:
            break
    assert "history" in starts
    assert clock[0] - 1_794_190_000.0 <= 3600 + 6 * 120 + 120               # starve_s + its aging turn


def test_settle_is_urgent_after_15_minutes_on_standard(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    J = scheduler.default_jobs()
    assert J["settle"]["urgent_after_s"] == 900
    assert J["settle-post"]["yields_to"] == ("settle",) and "--budget-s" in J["settle-post"]["argv"]
    monkeypatch.setenv("SPORTS_MEMORY_MB", "512")
    monkeypatch.delenv("SPORTS_CHILD_BUDGET_MB")
    assert scheduler.default_jobs()["settle"].get("urgent_after_s") is None  # Starter unchanged


# ------------------------------------------------------------------------------------------ settle-post time budget
def test_settle_post_budget_stops_between_events_and_the_next_run_finishes_identically(monkeypatch, tmp_path):
    """The scorecards stop once the budget is spent; the events done keep their marks, shadow's closing mark is not
    advanced, and the resumed run ends with exactly the tables of one unbounded run (no settlement touched)."""
    from sports.model import learning, shadow
    from sports.tests import season_world
    from sports.tests.test_sports_jobs_1108 import NOW, _add_shadow_rows, _scorecard_state
    states, outs = {}, {}
    for mode in ("one-run", "budget"):
        d = tmp_path / mode
        d.mkdir()
        with monkeypatch.context() as m:
            m.setattr(config, "DATA_DIR", d)
            with store.opened() as con:
                season_world.build(con, events=4, players=4, stats=2, revisions=2, extra_settled=3)
                _add_shadow_rows(con)
                settlements = sorted(r[0] for r in con.execute("SELECT record_id FROM records WHERE kind='settlement'"))
                if mode == "one-run":
                    outs[mode] = [learning.on_settled(con, now=NOW)]
                else:
                    ticks = iter(range(10 ** 6))                    # each time.time() call = one second
                    m.setattr(learning.time, "time", lambda: float(next(ticks)))
                    first = learning.on_settled(con, now=NOW, deadline=2.0)
                    m.undo()
                    m.setattr(config, "DATA_DIR", d)
                    mark = shadow.db().execute("SELECT value FROM shadow_meta WHERE key='settled_known_at'").fetchone()
                    outs[mode] = [first, learning.on_settled(con, now=NOW)]
                    assert first.get("partial") and first["events_left"] > 0
                    assert first["shadow"].get("partial") and mark is None           # closing mark not advanced
                    assert outs[mode][1].get("partial") is None
                assert sorted(r[0] for r in con.execute(
                    "SELECT record_id FROM records WHERE kind='settlement'")) == settlements
            states[mode] = _scorecard_state()
    assert states["budget"] == states["one-run"] and states["one-run"][0] and states["one-run"][2]


def test_settle_post_cli_passes_its_budget(monkeypatch):
    from sports import worker
    seen = {}
    monkeypatch.setattr(worker, "_post_settle", lambda lg, con, st, t0, name, budget_s=None, ledger=True:
                        seen.update(lg=lg, budget=budget_s, name=name, ledger=ledger) or {})
    monkeypatch.setenv("SPORTS_INGEST", "1")
    import argparse
    worker.cmd_settle_post(argparse.Namespace(leagues=["nba", "nfl"], budget_s=600.0), None)
    assert seen == {"lg": ["nba", "nfl"], "budget": 600.0, "name": "settle_post_stages.json",
                    "ledger": False}                    # settlepost-1120: the ledger is the results-ledger job
