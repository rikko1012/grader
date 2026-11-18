"""Heavy slots 2026-11-18 (live c7e7114, Render Standard 1 CPU / 2 GB, budget 1,200 MB, two heavy slots).

ops 22:40 UTC, uptime 36 min: ingest-kalshi, settle-post, player-context, history, archive, archive-verify had 0
starts since the 22:03 deploy (model-learn / model-snapshot are SPORTS_TRAIN jobs, off); deferred_ticks 94,763.

Why (sports/tests/sched_sim.py with LIVE_1118_RUN_S, and the live state rebuilt below):
  * every one of them shares a store lock with settle or the collectors (canonical / model-daily / games). On live run
    times that lane is oversubscribed (NBA collector 145 s per 180 s, history ~800 s per 900 s, settle ~330 s) and a
    12-min exclusive model-forward stops every heavy start for its drain + run each 30 min: replayed, both heavy slots
    or the exclusive job are busy ~94% of the time. A non-urgent job there starts only by winning tie-breaks;
  * the archive (prio 6) lost every tie to history (prio 6, due at the same boot tick, defined first), so it waited
    for the 1-h starve rule - 76-92 min after boot in the replays - and the deploys came sooner: its first pass never
    ran (nothing archived after 2026-11-16);
  * settle-post follows settle (then= / yields_to): it is not starved by a rule, it waits for settle, which waited for
    model-forward (exclusive 873-1595 s after boot) and then held canonical / model-daily / games;
  * at the snapshot one heavy slot was EMPTY: settle (600 MB - a Render override, the table gives 432) + books (108)
    left 492 MB, an urgent board build (540) did not fit and was held, and nothing passes a held urgent job unless it
    ends first - which the 1.0 s in settle's recent runs rules out (test below; reported, no code change);
  * prebuilt (every 45 s, 362-419 s a run after the memory fix) was due again 1 s after each exit.

Fix (scheduling only - no grade / chance / stake / projection / settlement change, no cap change):
  * prebuilt every 600 s, slips_pool served while <= 1,200 s old (< the 25-min line rule, sports/picks.py STALE_S);
  * the archive counts as starved after 30 min overdue (starve_after_s, scheduler.ARCHIVE_STARVE_S), not the global
    hour. The lane stays full: history / player-context / Kalshi start later after a deploy instead."""
import pytest

from sports import picks, scheduler, web
from sports.tests import sched_sim as sim

BOUND_MIN = 45                         # archive / settle-post first start after a deploy (replays: 30-41 min)
NAMED = ("archive", "settle-post", "history", "player-context", "ingest-kalshi", "archive-verify")
LONG_RUN_S = {"settle-post": 900, "grades-daily": 1200, "archive": 1500}


def _live(hours=4.0, seed=1108, **kw):
    return sim.simulate(scheduler, hours=hours, run_s=sim.LIVE_1118_RUN_S, env=sim.LIVE_1118_ENV,
                        cadence=sim.LIVE_1118_CADENCE, warm=True, first_settle_post_s=860, seed=seed, **kw)


def _first_min(res, name):
    s = res["starts"].get(name) or []
    return s[0] / 60 if s else None


@pytest.fixture(scope="module")
def live():
    res = _live()
    print("\n" + sim.summary(res, list(NAMED) + ["prebuilt", "ui-snapshots-nba", "ui-snapshots-nfl", "settle"]))
    return res


@pytest.fixture(scope="module", params=["ok", "long"])
def nov9(request):
    rs = dict(sim.NOV9_RUN_S, **(LONG_RUN_S if request.param == "long" else {}))
    return request.param, sim.simulate(scheduler, hours=4.0, run_s=rs)


# ------------------------------------------------------------------------------------------- the bound after boot
def test_archive_and_settle_post_start_within_the_bound_after_boot_on_live_run_times(live):
    for n in ("archive", "settle-post"):
        f = _first_min(live, n)
        assert f is not None and f <= BOUND_MIN, (n, f)                  # c7e7114: archive 84 min, settle-post 39
    for n in NAMED:                                                      # the rest still bounded (1-h rule + turn)
        f = _first_min(live, n)
        assert f is not None and f <= 120, (n, f)
    assert live["reserved_ok"] and live["heavy_ok"]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_bound_holds_over_jitter_seeds(seed):
    r = _live(hours=1.5, seed=seed)
    for n in ("archive", "settle-post"):
        f = _first_min(r, n)
        assert f is not None and f <= BOUND_MIN, (seed, n, f)


def test_the_bound_holds_on_the_nov9_replays(nov9):
    sc, r = nov9
    for n in ("archive", "settle-post"):
        f = _first_min(r, n)
        assert f is not None and f <= BOUND_MIN, (sc, n, f)              # c7e7114 (prebuilt 59 s): archive 76-81
    assert r["reserved_ok"] and r["heavy_ok"]


def test_member_facing_jobs_keep_running_on_live_run_times(live):
    """The lane is full, so the archive's turn is paid by history / player-context / Kalshi (later first start), not
    by the boards or collectors. c7e7114 on this replay (4 h, seed 1108): board builds 12 / 16 starts, longest gap
    37 / 31 min; collectors 45 / 55, settle 55, model-forward 48 min. Head: 12 / 15, 37 / 27; 43 / 38, 48, 45."""
    r = live
    for n, starts, gap in (("ui-snapshots-nba", 11, 38), ("ui-snapshots-nfl", 14, 32)):
        assert len(r["starts"][n]) >= starts, n
        assert r["max_gap"][n] <= gap * 60, (n, r["max_gap"][n] / 60)
    for n, gap in (("ingest-nba", 46), ("ingest-nfl", 46), ("settle", 50), ("model-forward", 50)):
        assert r["max_gap"][n] <= gap * 60, (n, r["max_gap"][n] / 60)
    for n in ("picks-ingest", "slips-ingest", "picks-settle", "slips-settle", "pregame-news", "books"):
        assert r["waits"][n] <= 6 * 60, (n, r["waits"][n])


# --------------------------------------------------------------------------------------------------- prebuilt
def test_prebuilt_cadence_and_the_slip_pool_age_fit_the_line_rule(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    J = scheduler.default_jobs()
    _fn, refresh_s, max_age_s = web.SNAPSHOTS["slips_pool"]
    assert J["prebuilt"]["every_s"] == refresh_s == 600
    live_run = max(sim.LIVE_1118_RUN_S["prebuilt"], 419.0)               # ops: 362.6 / 419.0 s
    assert J["prebuilt"]["every_s"] + live_run <= max_age_s < picks.STALE_S
    assert J["prebuilt"]["urgent_after_s"] == 300 and J["prebuilt"]["cap_mb"] == 288      # unchanged


def test_prebuilt_no_longer_holds_a_heavy_slot_most_of_the_time(live):
    starts = live["starts"]["prebuilt"]
    busy = len(starts) * sim.LIVE_1118_RUN_S["prebuilt"]
    assert busy <= 0.5 * live["end"], (len(starts), busy / live["end"])  # at most every 600 s + 400 s run


# ---------------------------------------------------------------------------------------------- the rule itself
def _sched(monkeypatch, jobs, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SPORTS_API", "1")
    monkeypatch.setattr(scheduler, "JOBS", {})
    monkeypatch.setattr(scheduler, "rss_mb", lambda pid: 1.0)
    clock = [1_795_039_420.0]
    sch = scheduler.Scheduler(jobs=jobs, budget=1200, now=lambda: clock[0], max_heavy_n=2)
    return sch, clock


def _j(cls, cap, prio, **kw):
    return dict(argv=["x"], every_s=60, timeout_s=10_000, flag="SPORTS_API", cap_mb=cap, cls=cls, prio=prio, **kw)


def test_a_job_with_its_own_starve_after_is_starved_sooner(monkeypatch):
    jobs = {"archive": _j("heavy", 360, 6, starve_after_s=1800, locks=("canonical",)),
            "history": _j("heavy", 360, 6, locks=("canonical",)),
            "x": _j("heavy", 100, 1, starve_after_s=7200, locks=("x",))}      # longer than the global: the global wins
    sch, clock = _sched(monkeypatch, jobs, SPORTS_JOB_STARVE_S="3600")
    for n in jobs:
        scheduler.JOBS[n]["next_start"] = clock[0] - 1900
    t = clock[0]
    assert sch._urgency("archive", t) == "starved" and sch._urgency("history", t) is None
    assert sch._starve_after("x") == 3600
    assert sch.due()[0] == "archive"                                       # ahead of history on the same tick
    clock[0] += 1800
    assert sch._urgency("history", clock[0]) == "starved"
    # both starved: each ages from the moment it became urgent, so the archive (urgent 30 min longer) stays first
    assert sch.due().index("archive") < sch.due().index("history")


def test_starve_s_zero_still_turns_the_rule_off_for_every_job(monkeypatch):
    jobs = {"archive": _j("heavy", 360, 6, starve_after_s=1800, locks=("canonical",))}
    sch, clock = _sched(monkeypatch, jobs, SPORTS_JOB_STARVE_S="0")
    scheduler.JOBS["archive"]["next_start"] = clock[0] - 10 * 3600
    assert sch._urgency("archive", clock[0]) is None


def test_the_table_sets_it_on_the_archive_only(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    J = scheduler.default_jobs()
    assert {n for n, s in J.items() if s.get("starve_after_s")} == {"archive"}
    assert J["archive"]["starve_after_s"] == scheduler.ARCHIVE_STARVE_S == 1800
    assert J["archive"]["prio"] == 6 and J["archive"]["cap_mb"] == 360                   # unchanged


# ------------------------------------------------------------------- the live picture at 22:40 UTC, one tick
OPS_2240 = {      # ops_live3.json: next_start / queued_since / recent_ran_s / last_start of the jobs that matter
    "settle": dict(pid=1, last_start=1795041157.4, next_start=1795039437.2, recent=[260.4, 387.2, 1.0]),
    "books": dict(pid=1, last_start=1795041155.9, next_start=1795040833.4, recent=[5.0, 425.0, 382.4]),
    "ui-snapshots-nfl": dict(next_start=1795039736.9, recent=[53.9, 57.8, 81.7]),
    "ui-snapshots-nba": dict(next_start=1795040208.8, recent=[131.3, 222.9, 174.8]),
    "ingest-nba": dict(next_start=1795040087.0, recent=[155.1, 151.8, 124.8]),
    "prebuilt": dict(next_start=1795041521.1, recent=[218.5, 419.0, 362.6]),
    "archive": dict(next_start=1795039443.2, recent=[1611.6, 1671.2, 1800.2]),
    "settle-post": dict(next_start=1795039440.2, recent=[785.2, 868.9, 864.0]),
    "history": dict(next_start=1795039443.2, recent=[712.1, 819.5, 829.3]),
}
NOW_2240 = 1795041581.0


class _Running:
    pid, rc = 1, None

    def poll(self):
        return self.rc


def _live_tick(monkeypatch, settle_cap):
    for k, v in dict(sim.prod_env(sim.LIVE_1118_ENV), SPORTS_JOB_SETTLE_CAP_MB=settle_cap).items():
        if v == "":
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    monkeypatch.setattr(scheduler, "JOBS", {})
    monkeypatch.setattr(scheduler, "rss_mb", lambda pid: 10.0)
    monkeypatch.setattr(scheduler, "cadence_every", lambda cad, t, root=None: 600.0)
    started = []
    monkeypatch.setattr(scheduler, "spawn", lambda spec: started.append(spec) or _Running())
    sch = scheduler.Scheduler(now=lambda: NOW_2240)
    for n, st in scheduler.JOBS.items():
        st["next_start"] = NOW_2240 + 3600 if st["next_start"] is not None else None   # only the listed jobs are due
    for n, o in OPS_2240.items():
        scheduler.JOBS[n].update(next_start=o["next_start"], recent_ran_s=list(o["recent"]))
        if o.get("pid"):
            sch.running[n] = (_Running(), o["last_start"])
            scheduler.JOBS[n]["last_start"] = o["last_start"]
    names = {id(s): n for n, s in sch.specs.items()}
    sch.tick()
    return sch, [names[id(s)] for s in started]


def test_the_live_tick_reproduces_the_empty_heavy_slot_with_the_600_mb_settle_override(monkeypatch):
    """Not changed here (an environment value): with settle at 600 MB the urgent board build (540) does not fit beside
    settle + books and is held, so the second heavy slot stays empty; at the table's 432 MB it starts. The lane jobs
    (archive, history, settle-post) wait on settle's locks either way."""
    sch, started = _live_tick(monkeypatch, "600")
    assert started == [] and set(sch.running) == {"settle", "books"}
    assert sch.reserved() == 708 and sch.specs["ui-snapshots-nfl"]["cap_mb"] == 540
    for n in ("archive", "history", "settle-post"):
        assert sch._blocking(n) == ["settle"], n
    sch, started = _live_tick(monkeypatch, "")                          # the table's settle cap (240 x 1.8)
    assert sch.specs["settle"]["cap_mb"] == 432
    assert started == ["ui-snapshots-nfl"]
