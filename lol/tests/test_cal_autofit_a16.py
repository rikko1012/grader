"""A16 part 1 (lol/EVALUATION.md): the daily server-side lc-1 decision - hysteresis (ON on a pass, OFF after 2 consecutive daily
fails), the decision log, skip when nothing new settled, the worker's once-a-day child-process step, its peak memory, and the
admin view. Every team, player and number is a TEST FIXTURE."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lol import cal
from lol.center import store

REPO = Path(__file__).resolve().parents[2]
BOTH = {"higher": {"status": "active"}, "lower": {"status": "active"}}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for k in ("LOL_CAL", "LOL_CAL_AUTOFIT"):
        monkeypatch.delenv(k, raising=False)
    cal.reset_cache()
    yield
    cal.reset_cache()


def _make_db(path, days=6, per_day=200, series_per_day=10, w=0.4, seed=3, start_day=18):
    """A lol.db whose live chance is far too confident (hit ~ sigmoid(w logit p)): lc-1 passes kills:underdog."""
    import numpy as np
    rng = np.random.default_rng(seed)
    con = store.connect(path)
    ins = ("INSERT INTO lol_forecasts (forecast_at, platform, market_key, stat, scope, odds_type, line, sides, sid, pid, "
           "series_start, model_version, lambda, nb_size, p_over, p_under, p_push, model_side, side_offered) "
           "VALUES (?, 'underdog', ?, 'kills', '12', 'balanced', 6.5, ?, ?, ?, ?, 'lol-1f', ?, 8.0, ?, ?, 0, ?, 1)")
    sett = "INSERT INTO lol_settlements (unit_key, version, settled_at, status, over_hit, push) VALUES (?, 1, ?, 'graded', ?, 0)"
    for d in range(days):
        day = f"2026-10-{start_day + d:02d}"
        for i in range(per_day):
            p = float(rng.uniform(0.15, 0.85))
            lam = float(rng.uniform(4, 9))
            mk = f"m{day}-{i}"
            con.execute(ins, (f"{day}T{8 + i % 12:02d}:00:00Z", mk, json.dumps(BOTH), f"s{day}-{i % series_per_day}", f"p{i}",
                              f"{day}T21:00:00Z", lam, p, 1 - p, "over" if p >= 0.5 else "under"))
            hit = int(rng.uniform() < cal._sig(w * cal._logit(p)))
            con.execute(sett, (f"underdog|{mk}|6.5", f"{day}T23:30:00Z", hit))
    con.commit()
    con.close()
    return path


def _w(on, theta=(0.1, 0.5, 0.2, 0.0, 0.0, 0.0, 0.0, 0.0), verdict=None, key="kills:underdog"):
    params = {key: {"on": on, "theta": list(theta), "verdict": verdict or ("PASS (i)" if on else "FAIL")}}
    return {"version": cal.VERSION, "params": params, "sha": cal._sha(params)}


# ------------------------------------------------------------------------------------------------- hysteresis
def test_hysteresis_on_on_pass_off_after_two_consecutive_fails():
    rep_pass = {"kills:underdog": {"scored": 300, "series": 20, "days": 5, "verdict": "PASS (i)", "why": "ok", "on": True}}
    rep_fail = {"kills:underdog": {"scored": 300, "series": 20, "days": 5, "verdict": "FAIL", "why": "balance", "on": False}}
    old = (0.3, 0.4, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0)
    new = (0.0, 0.9, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    # OFF + pass -> ON with today's parameters
    p, f, d = cal.decide(rep_pass, _w(True, new), _w(False, old), {})
    assert p["kills:underdog"]["on"] and p["kills:underdog"]["theta"] == list(new) and f["kills:underdog"] == 0
    assert d[0]["action"] == "ON (pass)" and d[0]["on_before"] is False and d[0]["on_after"] is True
    # ON + first fail -> stays ON with the LAST PASSING parameters
    p, f, d = cal.decide(rep_fail, _w(False, new), _w(True, old), {"kills:underdog": 0})
    assert p["kills:underdog"]["on"] is True and p["kills:underdog"]["theta"] == list(old) and f["kills:underdog"] == 1
    assert "1 of 2" in d[0]["action"] and d[0]["verdict"] == "FAIL" and d[0]["why"] == "balance"
    # ON + second consecutive fail -> OFF
    p, f, d = cal.decide(rep_fail, _w(False, new), _w(True, old), {"kills:underdog": 1})
    assert p["kills:underdog"]["on"] is False and f["kills:underdog"] == 2 and d[0]["action"].startswith("OFF")
    # a pass in between resets the counter
    p, f, _ = cal.decide(rep_pass, _w(True, new), _w(True, old), {"kills:underdog": 1})
    assert p["kills:underdog"]["on"] and f["kills:underdog"] == 0
    # OFF + fail stays OFF
    p, f, d = cal.decide(rep_fail, _w(False, new), _w(False, old), {"kills:underdog": 5})
    assert p["kills:underdog"]["on"] is False and d[0]["action"] == "OFF"


# ------------------------------------------------------------------------------------------------- autofit end to end
def test_autofit_writes_weights_log_state_and_skips_without_new_settlements(tmp_path):
    db = _make_db(tmp_path / "lol.db")
    out = tmp_path / "model" / cal.FILE_NAME
    before = db.read_bytes()
    r = cal.autofit(db, out=out, base=out.parent, today="2026-10-25")
    assert db.read_bytes() == before                                         # read-only
    assert r["skipped"] is False and "kills:underdog" in r["on"]
    w = cal.validate(json.loads(out.read_text(encoding="utf-8")))
    assert w["params"]["kills:underdog"]["on"] is True and w["sha"] == r["sha"]
    log = cal.read_log(cal.log_path(out.parent))
    assert log and log[-1]["date"] == "2026-10-25" and log[-1]["market"] == "kills:underdog"
    assert {"n", "series", "verdict", "why", "action", "on_after", "sha"} <= set(log[-1])
    st = json.loads(cal.state_path(out.parent).read_text(encoding="utf-8"))
    assert st["signature"][0] == 1200 and st["table"]["kills:underdog"]["verdict"].startswith("PASS")
    # same settlements -> one skip line, the weights untouched
    mt = out.stat().st_mtime_ns
    r2 = cal.autofit(db, out=out, base=out.parent, today="2026-10-26")
    assert r2["skipped"] is True and out.stat().st_mtime_ns == mt
    assert cal.read_log(cal.log_path(out.parent))[-1]["action"] == "skip"


def test_autofit_on_market_survives_one_fail_then_switches_off(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "lol.db")
    out = tmp_path / "model" / cal.FILE_NAME
    cal.autofit(db, out=out, base=out.parent, today="2026-10-25")
    theta_pass = json.loads(out.read_text())["params"]["kills:underdog"]["theta"]
    real = cal.run_eval

    def failing(rows):
        rep = real(rows)
        for k in rep:
            rep[k].update(on=False, verdict="FAIL", why="test: forced fail")
        return rep
    monkeypatch.setattr(cal, "run_eval", failing)
    cal.autofit(db, out=out, base=out.parent, today="2026-10-26", force=True)
    w = json.loads(out.read_text())["params"]["kills:underdog"]
    assert w["on"] is True and w["theta"] == theta_pass                     # one fail: stays ON, last passing parameters
    cal.autofit(db, out=out, base=out.parent, today="2026-10-27", force=True)
    assert json.loads(out.read_text())["params"]["kills:underdog"]["on"] is False   # 2 consecutive fails: OFF
    acts = [d["action"] for d in cal.read_log(cal.log_path(out.parent)) if d["market"] == "kills:underdog"]
    assert acts[0] == "ON (pass)" and "1 of 2" in acts[1] and acts[2].startswith("OFF")


def test_manual_fit_still_works(tmp_path, capsys):
    db = _make_db(tmp_path / "lol.db", days=3, per_day=40)
    cal.main(["fit", "--db", str(db), "--out", str(tmp_path / "w.json")])
    assert cal.validate(json.loads((tmp_path / "w.json").read_text()))
    assert json.loads(capsys.readouterr().out)["written"]["path"].endswith("w.json")


def test_autofit_child_process_peak_memory_is_bounded(tmp_path):
    """The worker runs the fit as a child: measure the child's own peak resident memory on a record ~4x production's."""
    db = _make_db(tmp_path / "lol.db", days=8, per_day=600, series_per_day=12)
    out = tmp_path / "model" / cal.FILE_NAME
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    r = subprocess.run([sys.executable, "-m", "lol.cal", "autofit", "--db", str(db), "--out", str(out)], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-800:]
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["skipped"] is False and res["rows"] == 4800
    assert res["peak_rss_mb"] is not None and res["peak_rss_mb"] < 300, res["peak_rss_mb"]
    print("autofit child peak RSS MB:", res["peak_rss_mb"])


# ------------------------------------------------------------------------------------------------- the worker step
class _Run:
    def __init__(self, stdout='{"skipped": false, "rows": 5, "sha": "x", "on": [], "peak_rss_mb": 80.0}', code=0):
        self.calls, self.stdout, self.code = [], stdout, code

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, self.code, self.stdout, "boom" if self.code else "")


@pytest.fixture
def wcon(tmp_path, monkeypatch):
    monkeypatch.setenv("LOL_CENTER_DB", str(tmp_path / "center.db"))
    c = store.connect()
    yield c
    c.close()


def _at(h, day=28):
    return datetime(2026, 10, day, h, 5, tzinfo=timezone.utc)


def test_worker_step_once_a_day_after_six_utc_in_a_child(wcon):
    from lol.center import worker
    run = _Run()
    assert worker.lc1_autofit(wcon, now=_at(5), run=run) is None and not run.calls          # before 06:00 UTC
    r = worker.lc1_autofit(wcon, now=_at(6), run=run)
    assert r["code"] == 0 and r["peak_rss_mb"] == 80.0 and len(run.calls) == 1
    cmd, kw = run.calls[0]
    assert cmd[1:4] == ["-m", "lol.cal", "autofit"] and kw["timeout"] == worker.AUTOFIT_TIMEOUT_S
    assert kw["env"]["OPENBLAS_NUM_THREADS"] == "1" and Path(kw["cwd"]) == REPO
    assert worker.lc1_autofit(wcon, now=_at(18), run=run) is None and len(run.calls) == 1   # once per day
    assert worker.lc1_autofit(wcon, now=_at(7, day=29), run=run)["code"] == 0 and len(run.calls) == 2


def test_worker_step_skips_without_a_child_when_nothing_new_settled(wcon):
    from lol.center import worker
    cal._write_json(cal.state_path(), {"signature": cal.settle_signature(wcon)})
    run = _Run()
    r = worker.lc1_autofit(wcon, now=_at(9), run=run)
    assert r["skipped"] is True and not run.calls
    assert cal.read_log(cal.log_path())[-1]["action"] == "skip"


def test_worker_step_switch_and_error_is_logged(wcon, monkeypatch):
    from lol.center import worker
    monkeypatch.setenv("LOL_CAL_AUTOFIT", "0")
    run = _Run()
    assert worker.lc1_autofit(wcon, now=_at(9), run=run) is None and not run.calls
    monkeypatch.delenv("LOL_CAL_AUTOFIT")
    bad = _Run(stdout="", code=1)
    r = worker.lc1_autofit(wcon, now=_at(9), run=bad)
    assert r["code"] == 1 and "boom" in r["error"]
    assert cal.read_log(cal.log_path())[-1]["action"] == "error"
    assert worker.lc1_autofit(wcon, now=_at(10), run=bad) is None                     # one attempt per day


def test_worker_calls_the_step_right_after_settlement():
    src = (REPO / "lol" / "center" / "worker.py").read_text(encoding="utf-8")
    i_settle, i_fit = src.index('"settle", lambda'), src.index('"lc1_autofit", lambda')
    i_board = src.index('_step(con, "board"')
    assert i_settle < i_fit < i_board


# ------------------------------------------------------------------------------------------------- admin
def test_admin_view_and_learning_dashboard_carry_lc1(tmp_path, wcon):
    from lol.center import learning
    db = _make_db(tmp_path / "lol.db", days=3, per_day=60)
    for i, day in enumerate(("2026-10-21", "2026-10-22")):
        cal.autofit(db, today=day, force=True)
    for i in range(12):
        cal.append_log(cal.log_path(), [{"date": "2026-10-23", "market": "*", "action": "skip", "why": f"x{i}"}])
    v = cal.admin_view()
    assert v["version"] == "lc-1" and v["autofit"] is True and v["source"] == "file"
    assert len(v["decisions"]) == 10 and v["decisions"][-1]["why"] == "x11"
    assert "kills:underdog" in v["table"] and {"live_stated", "live_hit", "lc1_stated", "lc1_hit", "verdict"} <= set(v["table"]["kills:underdog"])
    d = learning.dashboard(wcon)
    assert d["lc1"]["version"] == "lc-1" and len(d["lc1"]["decisions"]) == 10


def test_admin_page_has_the_calibration_view():
    html = (REPO / "web" / "lol_admin.html").read_text(encoding="utf-8")
    assert 'data-v="cal"' in html and "/admin/lol/learning.json" in html and "Last 10 decisions" in html
