"""settle-post 2026-11-20 (live: runs 1,085 -> 1,214 s -> the 1,500 s kill; model_learning 942.6 s against a 600 s budget
checked only between events, then the results ledger's 430-650 s; grading_job_stale page).

  * the results ledger is its own job (worker `results-ledger`, scheduler "results-ledger") - the same results.json;
  * the scorecard budget is strict: checked per forecast, rows so far committed, the unfinished event keeps no mark,
    the next run resumes at the event it stopped at; a statement past deadline + grace is interrupted; the shadow
    scorecard has its own slice; retrain_due (a report) is skipped once the budget is spent;
  * a stopped run keeps its window start until a run completes (a backlog never slides out of the 30-day window);
  * settle_post_stages.json carries the scorecard counters; model/learning_state.json says partial / last complete;
  * grades-daily holds (no fit, no switch) while the scorecard is behind.

Every budgeted / killed / resumed sequence must end with exactly the tables of one unbounded run, in the same row
order (the scorecard report and the cal-mi rows read them in rowid order)."""
import argparse
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from sports import config, scheduler, snapshots, store, worker
from sports.model import daily, learning, shadow
from sports.tests import season_world
from sports.tests.test_sports_jobs_1108 import _add_shadow_rows, _correct_one

NOW = "2026-11-08T21:00:00Z"


class Clock:
    """time.time() that advances one 'second' per call - a deterministic stand-in for work done."""

    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        self.t += 1.0
        return self.t


def _world(d, monkeypatch, events=5):
    monkeypatch.setattr(config, "DATA_DIR", d)
    with store.opened() as con:
        season_world.build(con, events=events, players=4, stats=2, revisions=2, extra_settled=3)
        _add_shadow_rows(con)


def _tables():
    """Every learning output in its stored ORDER (rowid), wall-clock columns left out, plus the derived reports."""
    from sports.model import cal
    lcon = learning.db()
    sc = lcon.execute("SELECT * FROM scorecard ORDER BY rowid").fetchall()
    pr = lcon.execute("SELECT * FROM projection_reviews ORDER BY rowid").fetchall()
    dg = lcon.execute("SELECT forecast_id, settlement_id, kind, detail, reviewed FROM diagnostics ORDER BY rowid").fetchall()
    ev = sorted(lcon.execute("SELECT event_id, sig FROM learn_events").fetchall())
    meta = lcon.execute("SELECT * FROM learn_meta").fetchall()
    report = json.dumps(learning.scorecard(lcon), sort_keys=True, default=str)
    bands = json.dumps(learning.grade_band_report(lcon), sort_keys=True, default=str)
    calrows = json.dumps(cal.rows_from_learning(lcon), sort_keys=True, default=str)
    lcon.close()
    s = shadow.db()
    sh = s.execute("SELECT * FROM shadow_scorecard ORDER BY rowid").fetchall()
    s.close()
    return {"scorecard": sc, "projection_reviews": pr, "diagnostics": dg, "learn_events": ev, "learn_meta": meta,
            "report": report, "bands": bands, "cal_rows": calrows, "shadow": sh}


def _one_run(tmp_path, monkeypatch, name="one-run", events=5, after=None):
    d = tmp_path / name
    d.mkdir()
    _world(d, monkeypatch, events)
    with store.opened() as con:
        out = learning.on_settled(con, now=NOW)
        if after:
            after(con)
            learning.on_settled(con, now=NOW)
    assert not out.get("partial") and out["scorecard_rows_added"] > 0
    return _tables()


def _done(out):
    """Main AND shadow scorecards both finished."""
    return not out.get("partial") and not (out.get("shadow") or {}).get("partial")


def _budget_runs(con, monkeypatch, budget, now=NOW, max_runs=400):
    clock = Clock()
    monkeypatch.setattr(learning.time, "time", clock)
    outs = []
    for _ in range(max_runs):
        outs.append(learning.on_settled(con, now=now, deadline=clock.t + budget))
        if _done(outs[-1]):
            break
    monkeypatch.undo()
    return outs


# ------------------------------------------------------------------------------------------ strict budget + resume
def test_budget_stops_inside_an_event_and_the_resumed_runs_equal_one_run(monkeypatch, tmp_path):
    full = _one_run(tmp_path, monkeypatch)
    d = tmp_path / "budget"
    d.mkdir()
    _world(d, monkeypatch)
    stopped_inside = False
    with store.opened() as con:
        clock = Clock()
        monkeypatch.setattr(learning.time, "time", clock)
        outs = []
        for _ in range(400):
            outs.append(learning.on_settled(con, now=NOW, deadline=clock.t + 60))
            lc = learning.db()
            scored = {r[0] for r in lc.execute("SELECT DISTINCT event_id FROM scorecard")}
            marked = {r[0] for r in lc.execute("SELECT event_id FROM learn_events")}
            lc.close()
            stopped_inside |= bool(scored - marked)        # rows of an event committed without its mark
            if _done(outs[-1]):
                break
        monkeypatch.undo()
    monkeypatch.setattr(config, "DATA_DIR", d)
    assert len(outs) > 3 and stopped_inside               # many runs, at least one stopped mid-event
    assert not any(_done(o) for o in outs[:-1]) and _done(outs[-1])
    assert any(o.get("partial") and o["shadow"]["shadow_scored"] > 0 for o in outs)    # the shadow's own slice
    for o in outs[:-1]:
        assert o["retrain"]["nba"].get("skipped") and o["retrain"]["nba"]["due"] is None   # report skipped, a dict
    assert sum(o["scorecard_rows_added"] for o in outs) == len(full["scorecard"])          # nothing scored twice
    got = _tables()
    assert got == full


def test_shadow_scorecard_has_its_own_slice(monkeypatch, tmp_path):
    """A main pass that uses its whole share still leaves the shadow scorecard time: it scores rows in that run."""
    d = tmp_path / "w"
    d.mkdir()
    _world(d, monkeypatch, events=6)
    with store.opened() as con:
        clock = Clock()
        monkeypatch.setattr(learning.time, "time", clock)
        first = learning.on_settled(con, now=NOW, deadline=clock.t + 200)
        monkeypatch.undo()
    assert first.get("partial")                           # the main pass ran out of its share ...
    assert first["shadow"]["shadow_scored"] > 0           # ... and the shadow still scored in the same run


def test_kill_mid_event_then_resume_equals_one_run(monkeypatch, tmp_path):
    """A SIGKILL / timeout half way through an event: its uncommitted rows are gone, the next run rescored them in the
    same order - the same tables as one run, nothing doubled."""
    full = _one_run(tmp_path, monkeypatch)
    d = tmp_path / "kill"
    d.mkdir()
    _world(d, monkeypatch)
    real, real_db, conns = learning._score_one, learning.db, []
    calls = []

    def opened():
        c = real_db()
        conns.append(c)
        return c

    def dying(*a, **k):
        calls.append(1)
        if len(calls) == 7:                               # mid-way through the first event with work
            raise KeyboardInterrupt("killed")
        return real(*a, **k)
    with store.opened() as con:
        monkeypatch.setattr(learning, "_score_one", dying)
        monkeypatch.setattr(learning, "db", opened)
        with pytest.raises(KeyboardInterrupt):
            learning.on_settled(con, now=NOW)
        for c in conns:                                   # the process died: its open transaction is gone
            c.close()
        monkeypatch.setattr(learning, "_score_one", real)
        monkeypatch.setattr(learning, "db", real_db)
        lc = learning.db()
        assert lc.execute("SELECT COUNT(*) FROM scorecard").fetchone()[0] < 7
        lc.close()
        out = learning.on_settled(con, now=NOW)
    assert not out.get("partial")
    assert _tables() == full


def test_a_correction_between_budgeted_runs_matches_one_run_with_the_correction(monkeypatch, tmp_path):
    """Settlement revisions arriving while a backlog drains (the suspected live cause): already-scored forecasts are
    recognised without an offer read, the corrected one is rescored and its old row superseded - as one run would."""
    full = _one_run(tmp_path, monkeypatch, after=_correct_one)
    d = tmp_path / "budget"
    d.mkdir()
    _world(d, monkeypatch)
    with store.opened() as con:
        first = _budget_runs(con, monkeypatch, 60)
        monkeypatch.setattr(config, "DATA_DIR", d)
        _correct_one(con)
        again = learning.on_settled(con, now=NOW)
    monkeypatch.setattr(config, "DATA_DIR", d)
    assert len(first) > 1 and again["offers_read"] < again["forecasts_read"]
    assert again["forecasts_already_scored"] > 0 and again["scorecard_rows_added"] >= 1
    assert _tables()["scorecard"] == full["scorecard"]
    assert {k: v for k, v in _tables().items() if k != "learn_events"} == \
        {k: v for k, v in full.items() if k != "learn_events"}


def test_hard_backstop_interrupts_a_statement_past_the_deadline(monkeypatch, tmp_path):
    full = _one_run(tmp_path, monkeypatch)
    d = tmp_path / "hard"
    d.mkdir()
    _world(d, monkeypatch)
    monkeypatch.setattr(learning, "HARD_GRACE_S", -5.0)
    monkeypatch.setattr(learning, "PROGRESS_OPS", 1)
    import time
    with store.opened() as con:
        out = learning.on_settled(con, now=NOW, deadline=time.time())
        assert out.get("interrupted") and out.get("partial") and out["scorecard_rows_added"] == 0
        assert "error" in out["shadow"] or out["shadow"].get("partial") or out["shadow"]["shadow_scored"] == 0
        monkeypatch.setattr(learning, "HARD_GRACE_S", 60.0)
        con.execute("SELECT 1").fetchone()                # the handler is gone: the connection works
        out2 = learning.on_settled(con, now=NOW)
    assert not out2.get("partial")
    assert _tables() == full
    st = learning.read_state()
    assert st["partial"] is False and st["complete_at"]


# ------------------------------------------------------------------------------------------ backlog / growth
def test_a_backlog_older_than_the_window_is_never_dropped(monkeypatch, tmp_path):
    """Run 1 stops part way; the next run is 60 days later (every settlement now outside the 30-day window). The kept
    window start makes it finish the backlog - the same tables as one run at the time."""
    full = _one_run(tmp_path, monkeypatch)
    d = tmp_path / "late"
    d.mkdir()
    _world(d, monkeypatch)
    later = (datetime.strptime(NOW, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with store.opened() as con:
        clock = Clock()
        monkeypatch.setattr(learning.time, "time", clock)
        first = learning.on_settled(con, now=NOW, deadline=clock.t + 80)
        monkeypatch.undo()
        monkeypatch.setattr(config, "DATA_DIR", d)
        assert first.get("partial")
        rest = learning.on_settled(con, now=later)
    assert not rest.get("partial") and rest["window_from"] == first["window_from"] and rest["events_read"] > 0
    assert {k: v for k, v in _tables().items() if k != "learn_events"} == \
        {k: v for k, v in full.items() if k != "learn_events"}
    with store.opened() as con:                           # completed: the window is the normal one again
        assert learning.on_settled(con, now=later)["events_read"] == 0


def test_run_length_stays_bounded_as_the_store_grows_and_the_backlog_drains(monkeypatch, tmp_path):
    """Each budgeted run stops within its budget (+ a few clock reads) whatever the store size; a backlog 3x as big
    takes more runs, never a longer run; each drained result equals one run on the same store."""
    budget, runs = 120, {}
    for events in (3, 9):
        full = _one_run(tmp_path, monkeypatch, name=f"one{events}", events=events)
        d = tmp_path / f"grow{events}"
        d.mkdir()
        _world(d, monkeypatch, events)
        with store.opened() as con:
            clock = Clock()
            monkeypatch.setattr(learning.time, "time", clock)
            outs, spans = [], []
            for _ in range(500):
                t0 = clock.t
                outs.append(learning.on_settled(con, now=NOW, deadline=t0 + budget))
                spans.append(clock.t - t0)
                if _done(outs[-1]):
                    break
            monkeypatch.undo()
        monkeypatch.setattr(config, "DATA_DIR", d)
        assert _done(outs[-1])
        assert max(spans) <= budget + 10, (events, max(spans))
        assert _tables() == full
        runs[events] = len(outs)
    assert runs[9] > runs[3] > 1, runs


def test_settle_post_real_clock_ends_near_its_budget(monkeypatch, tmp_path):
    """Real time: a 1 s budget on a world whose full pass takes longer ends within a few seconds, partial, and the
    stages file says so (with the counters ops reads)."""
    d = tmp_path / "real"
    d.mkdir()
    _world(d, monkeypatch, events=8)
    monkeypatch.setenv("SPORTS_INGEST", "1")
    import time
    with store.opened() as con:
        t0 = time.time()
        out = worker.cmd_settle_post(argparse.Namespace(leagues=["nba", "nfl"], budget_s=0.3), con)
        took = time.time() - t0
    ml = out["model_learning"]
    assert took < 0.3 + 5.0, took
    ps = json.loads((config.DATA_DIR / "settle_post_stages.json").read_text())
    assert [x[0] for x in ps["stage_s"]] == ["model_learning"] and ps["finished"]
    c = ps["model_learning"]
    for k in ("events_read", "events_unchanged", "events_scored", "forecasts_read", "offers_read", "main_s"):
        assert k in c, k
    assert c.get("partial") == ml.get("partial")
    assert "results" not in out                          # the ledger is its own job now


# ------------------------------------------------------------------------------------------ results-ledger job
def test_results_ledger_job_writes_the_same_file_as_the_old_settle_post(monkeypatch, tmp_path):
    d = tmp_path / "ledger"
    d.mkdir()
    _world(d, monkeypatch, events=4)
    monkeypatch.setenv("SPORTS_INGEST", "1")
    monkeypatch.setattr(snapshots, "now_utc", lambda: NOW)
    with store.opened() as con:
        old = {lg: snapshots.write_results(con, lg) for lg in ("nba", "nfl")}
        before = {lg: (snapshots.snap_root() / lg / "results.json").read_bytes() for lg in ("nba", "nfl")}
        out = worker.cmd_results_ledger(argparse.Namespace(leagues=["nba", "nfl"]), con)
    after = {lg: (snapshots.snap_root() / lg / "results.json").read_bytes() for lg in ("nba", "nfl")}
    assert out["results"] == old and after == before and before["nba"]
    st = json.loads((config.DATA_DIR / "results_ledger_stages.json").read_text())
    assert st["finished"] and [x[0] for x in st["stage_s"]] == ["results:nba", "results:nfl"]


def test_results_ledger_builds_the_oldest_league_first(monkeypatch, tmp_path):
    import os
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    for lg, age in (("nba", 100), ("nfl", 5000)):
        p = snapshots.snap_root() / lg / "results.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}")
        os.utime(p, (p.stat().st_mtime - age,) * 2)
    assert worker._ledger_order(["nba", "nfl"]) == ["nfl", "nba"]
    (snapshots.snap_root() / "nba" / "results.json").unlink()
    assert worker._ledger_order(["nba", "nfl"]) == ["nba", "nfl"]      # never built = oldest


def test_results_ledger_cli_is_wired():
    with pytest.raises(SystemExit):
        worker.main(["results-ledger", "--help"])


def test_job_table(monkeypatch):
    monkeypatch.setenv("SPORTS_MEMORY_MB", "2048")
    monkeypatch.setenv("SPORTS_CHILD_BUDGET_MB", "1200")
    J = scheduler.default_jobs()
    sp, rl, pair = J["settle-post"], J["results-ledger"], scheduler.Scheduler._pairable
    assert "season-results" not in sp["locks"] and "model-daily" in sp["locks"]
    assert rl["argv"][2:4] == ["sports.worker", "results-ledger"] and rl["cls"] == scheduler.HEAVY
    assert rl["locks"] == ("results-ledger", "season-results") and rl["flag"] == "SPORTS_INGEST"
    assert not pair(rl, J["results-public"])               # the two full-season builds never fill both slots
    assert pair(rl, J["settle"]) and pair(rl, J["settle-post"])   # it never holds settle (or the scorecards) back
    budget = float(sp["argv"][sp["argv"].index("--budget-s") + 1])
    assert budget + learning.HARD_GRACE_S + 300 <= sp["timeout_s"]
    assert scheduler._child_env(rl)["SPORTS_WORKER_MAX_VM_MB"] == scheduler._child_env(sp)["SPORTS_WORKER_MAX_VM_MB"]


# ------------------------------------------------------------------------------------------ grades-daily hold
def _daily_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("SPORTS_CAL_AUTOFIT", "0")
    monkeypatch.setenv("SPORTS_GRADECHECK_DAILY", "0")
    monkeypatch.setattr(daily, "latest_settled", lambda p: "2026-11-20T05:00:00Z")
    monkeypatch.setattr(daily, "publish", lambda summary: None)
    (tmp_path / "model").mkdir(exist_ok=True)


def _state(tmp_path, **kw):
    (tmp_path / "model" / "learning_state.json").write_text(json.dumps(kw))


AT = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("state,held", [
    (None, False),                                                                    # no file: older code, no hold
    ({"at": "2026-11-20T11:50:00Z", "partial": False, "complete_at": "2026-11-20T11:50:00Z"}, False),
    ({"at": "2026-11-20T11:50:00Z", "partial": True, "events_left": 40, "complete_at": "2026-11-20T11:00:00Z"}, True),
    ({"at": "2026-11-20T06:00:00Z", "partial": False, "complete_at": "2026-11-20T06:00:00Z"}, True),  # 6 h old
    ({"at": "2026-11-20T11:50:00Z", "partial": True, "events_left": 3}, True),        # never complete
])
def test_grades_daily_holds_while_the_scorecard_is_behind(monkeypatch, tmp_path, state, held):
    _daily_env(monkeypatch, tmp_path)
    if state is not None:
        _state(tmp_path, **state)
    out = daily.run(now=AT)
    st = json.loads((tmp_path / "model" / "daily_state.json").read_text())
    if held:
        assert out["ran"] is False and out.get("held") and out["why"].startswith("held: model scorecard behind")
        assert st["last_skip"]["why"] == out["why"] and "last_day" not in st     # retried by the next check
    else:
        assert out["ran"] is True and st["last_day"] == daily.game_day(AT)


def test_grades_daily_force_and_switch_off_bypass_the_hold(monkeypatch, tmp_path):
    _daily_env(monkeypatch, tmp_path)
    _state(tmp_path, at="2026-11-20T11:50:00Z", partial=True, events_left=5)
    assert daily.run(now=AT, force=True)["ran"] is True
    (tmp_path / "model" / "daily_state.json").unlink()
    monkeypatch.setenv("SPORTS_DAILY_SCORECARD_MAX_AGE_S", "0")
    assert daily.run(now=AT)["ran"] is True


def test_learning_state_file_tracks_partial_and_complete(monkeypatch, tmp_path):
    d = tmp_path / "st"
    d.mkdir()
    _world(d, monkeypatch)
    with store.opened() as con:
        clock = Clock()
        monkeypatch.setattr(learning.time, "time", clock)
        learning.on_settled(con, now=NOW, deadline=clock.t + 40)
        monkeypatch.undo()
        monkeypatch.setattr(config, "DATA_DIR", d)
        st = learning.read_state()
        assert st["partial"] and st["events_left"] > 0 and st["complete_at"] is None
        assert daily.scorecard_hold(AT) is not None
        learning.on_settled(con, now=NOW)
    st = learning.read_state()
    assert not st["partial"] and st["complete_at"] == st["at"]


# ------------------------------------------------------------------------------------------ changes behind a cursor
def _correct_in(con, ev):
    """A settlement revision for the FIRST forecast of event `ev` (before any saved cursor) and a late forecast on the
    same event - deterministic, so the one-run world gets the identical change."""
    from sports.tests import helpers as h
    fr = con.execute("SELECT payload FROM records WHERE kind='forecast' AND event_id=? ORDER BY rid LIMIT 1",
                     (ev,)).fetchone()
    fc = store._rec(fr)
    o = store.get(con, fc.offer_snapshot_ids[0])
    s = next(s for _, s in store.settlements(con) if s.platform == o.platform and s.offer_id == o.offer_id
             and s.side == fc.chosen_side)
    store.put(con, replace(s, settlement_id=s.settlement_id + "-c", revision=s.revision + 1, supersedes=s.settlement_id,
                           source_value=(s.source_value or 0) + 7, outcome="over",
                           status="win" if s.side == "over" else "loss",
                           known_at="2026-11-01T00:00:00Z", observed_at="2026-11-01T00:00:00Z"))
    store.put(con, h.forecast(forecast_at="2026-10-30T20:00:00Z", league=o.league, event_id=o.event_id,
                              player_id=o.player_id, player_key=o.player_key, stat=o.stat, line=o.line,
                              offer_snapshot_ids=(fc.offer_snapshot_ids[0],),
                              input_snapshot_ids=(fc.offer_snapshot_ids[0], "late-c"), model_version="sm-1",
                              projection_mean=o.line + 2, p_over=0.6, p_under=0.4, p_push=0.0,
                              p_over_given_no_push=0.6, p_under_given_no_push=0.4,
                              sides_available=tuple(o.offered_sides()), chosen_side=o.offered_sides()[0]))


def _sorted_tables():
    t = _tables()
    return {k: (sorted(v) if isinstance(v, list) else v) for k, v in t.items()
            if k in ("scorecard", "projection_reviews", "diagnostics", "shadow")}


def test_a_change_inside_the_stopped_event_is_not_skipped_by_the_cursor(monkeypatch, tmp_path):
    """Stopped mid-event with a forecast cursor saved; a correction for a forecast BEFORE the cursor and a late forecast
    land in that event; the resumed runs rescore both (the cursor is dropped because the event's records changed) -
    the same rows and superseded flags as one run that saw the same change."""
    d = tmp_path / "budget"
    d.mkdir()
    _world(d, monkeypatch)
    ev = None
    with store.opened() as con:
        clock = Clock()
        monkeypatch.setattr(learning.time, "time", clock)
        for budget in range(20, 400, 7):
            lc = learning.db()
            lc.execute("DELETE FROM scorecard"), lc.execute("DELETE FROM projection_reviews")
            lc.execute("DELETE FROM learn_events"), lc.execute("DELETE FROM learn_meta"), lc.commit()
            meta = {}
            learning.on_settled(con, now=NOW, deadline=clock.t + budget)
            meta = dict(lc.execute("SELECT key, value FROM learn_meta"))
            lc.close()
            if meta.get("resume_rid"):
                ev = meta["resume_event"]
                first = con.execute("SELECT MIN(rid) FROM records WHERE kind='forecast' AND event_id=?", (ev,)).fetchone()[0]
                if int(meta["resume_rid"]) > first:
                    break
                ev = None
        assert ev is not None
        monkeypatch.setattr(config, "DATA_DIR", d)
        _correct_in(con, ev)
        outs = _budget_runs(con, monkeypatch, 60)
    monkeypatch.setattr(config, "DATA_DIR", d)
    assert _done(outs[-1])
    got = _sorted_tables()
    one = tmp_path / "one"
    one.mkdir()
    _world(one, monkeypatch)
    with store.opened() as con:
        learning.on_settled(con, now=NOW)
        _correct_in(con, ev)
        learning.on_settled(con, now=NOW)
    want = _sorted_tables()
    assert any(r[-1] == 1 for r in want["scorecard"])      # the correction superseded a row
    assert got["scorecard"] == want["scorecard"] and got["projection_reviews"] == want["projection_reviews"]
    assert got["shadow"] == want["shadow"]


def test_a_swallowed_interrupt_never_marks_the_event_or_moves_the_cursor(monkeypatch, tmp_path):
    """The archive's table probe answers None on ANY sqlite error, so an interrupted offer read can look like a missing
    offer. Once the backstop has fired, no event is marked and no cursor is saved: everything is re-read."""
    full = _one_run(tmp_path, monkeypatch)
    d = tmp_path / "swallow"
    d.mkdir()
    _world(d, monkeypatch)
    real_get = store.get
    state = {"n": 0}

    def get(con, rid):                                   # the 5th offer read is 'interrupted' and swallowed
        state["n"] += 1
        if state["n"] == 5:
            try:
                con.execute("SELECT 1").fetchone()       # the handler fires on this statement ...
            except sqlite3.OperationalError:
                pass
            return None                                  # ... and the reader answers None, as _has_tables does
        return real_get(con, rid)
    import time
    with store.opened() as con:
        monkeypatch.setattr(store, "get", get)
        monkeypatch.setattr(learning, "PROGRESS_OPS", 1)
        real_time = time.time
        base = real_time()
        monkeypatch.setattr(learning.time, "time", lambda: real_time() + (10 ** 6 if state["n"] >= 5 else 0))
        out = learning.on_settled(con, now=NOW, deadline=base + 300)
        monkeypatch.undo()
        monkeypatch.setattr(config, "DATA_DIR", d)
        lc = learning.db()
        meta = dict(lc.execute("SELECT key, value FROM learn_meta"))
        marked = lc.execute("SELECT COUNT(*) FROM learn_events").fetchone()[0]
        lc.close()
        assert out.get("partial") and "resume_rid" not in meta and marked == 0, (out, meta, marked)
        out2 = learning.on_settled(con, now=NOW)
    assert not out2.get("partial")
    assert _tables() == full
