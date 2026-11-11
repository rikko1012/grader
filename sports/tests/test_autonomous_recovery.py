"""Failure/restart recovery without repeated model decisions or rewritten picks."""
import json
from datetime import datetime, timezone

import pytest

from sports.model import daily, gradecheck


@pytest.fixture
def world(tmp_path, monkeypatch):
    paths = {k: tmp_path / k for k in ("state", "learning", "inputs", "store", "history", "games")}
    monkeypatch.setattr(daily, "paths", lambda: paths)
    monkeypatch.setattr(daily, "latest_settled", lambda p: "2026-11-11T10:00:00Z")
    monkeypatch.setattr(daily, "admin_view", lambda: json.loads(paths['state'].read_text()))
    monkeypatch.setattr(daily, "publish", lambda obj: None)
    monkeypatch.setattr(daily, "autofit_on", lambda: True)
    monkeypatch.setattr(daily, "gradecheck_on", lambda: True)
    monkeypatch.setattr(daily, "write_gradecheck", lambda *a: "report.json")
    monkeypatch.setattr(daily, "headline", lambda r: {})
    from sports import containment
    monkeypatch.setattr(containment, "apply_verdicts", lambda *a: {})
    calls = []
    monkeypatch.setattr(daily, "autofit", lambda *a: calls.append(1) or {"sha": "same-weights", "fails": {"x": 1}})
    monkeypatch.setattr(gradecheck, "run", lambda *a: {"generated_at": "now"})
    return paths, calls


NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)


@pytest.mark.parametrize("raises", [False, True])
def test_failed_check_retries_without_a_second_fit(world, monkeypatch, raises):
    paths, calls = world
    def broken(*args):
        if raises:
            raise OSError("temporary read error")
        return {"error": "temporary read error"}
    monkeypatch.setattr(gradecheck, "run", broken)
    first = daily.run(now=NOW)
    assert first['error']['gradecheck']
    state = json.loads(paths['state'].read_text())
    assert 'last_day' not in state and state['cal_fails'] == {'x': 1}
    monkeypatch.setattr(gradecheck, "run", lambda *a: {"generated_at": "now"})
    second = daily.run(now=NOW.replace(hour=14))
    assert 'error' not in second and len(calls) == 1
    assert json.loads(paths['state'].read_text())['last_day'] == '2026-11-11'
    assert daily.run(now=NOW.replace(hour=16))['ran'] is False


def test_interrupted_fit_is_not_silently_repeated(world):
    paths, calls = world
    paths['state'].write_text(json.dumps({'attempt_day':'2026-11-11', 'cal_attempt':{'status':'running'}}))
    result = daily.run(now=NOW)
    assert 'interrupted' in result['error']['cal'] and not calls
    assert result['gradecheck']['skipped'].startswith('calibration attempt incomplete')


def test_failed_fit_stays_visible_without_counting_another_daily_failure(world, monkeypatch):
    paths, calls = world
    def fail(*args):
        calls.append(1)
        raise OSError('fit interrupted')
    monkeypatch.setattr(daily, 'autofit', fail)
    assert daily.run(now=NOW)['error']['cal']
    assert daily.run(now=NOW.replace(hour=14))['error']['cal']
    assert len(calls) == 1
    daily.run(now=NOW.replace(day=12))
    assert len(calls) == 2


def test_failed_publish_retries_only_publication(world, monkeypatch):
    paths, calls = world
    monkeypatch.setattr(daily, 'publish', lambda obj: 'temporarily unavailable')
    assert daily.run(now=NOW)['publish_error']
    monkeypatch.setattr(daily, 'publish', lambda obj: None)
    monkeypatch.setattr(gradecheck, 'run', lambda *a: pytest.fail('must not recheck completed day'))
    assert daily.run(now=NOW.replace(hour=14))['ran'] is False
    assert not json.loads(paths['state'].read_text())['publish_pending']
    assert len(calls) == 1


@pytest.mark.parametrize('result,code', [({'error':{'gradecheck':'bad'}},1), ({'publish_error':'bad'},1), ({'ran':False},0)])
def test_scheduler_receives_failure_exit_status(monkeypatch,result,code):
    monkeypatch.setattr(daily, 'run', lambda **kw: result)
    assert daily.main(['run']) == code


@pytest.mark.parametrize('name', ['model-forward','books','settle','settle-post','picks-settle','slips-settle','grades-daily'])
def test_stale_jobs_alert_even_before_three_failures(name,monkeypatch):
    from sports import service
    from sports.tests.test_service import _ops
    monkeypatch.setattr(service.time,'time',lambda: 100000.)
    job={'enabled':True,'every_s':1800,'timeout_s':900,'last_ok_at':90000.,'failures_in_a_row':1}
    data=_ops(scheduler={'uptime_s':10000,'jobs':{name:job}})
    stale=lambda: [a for a in service.alerts(data) if a['code']=='grading_job_stale']
    assert stale()[0]['detail']['job']==name
    job['last_ok_at']=99999.
    assert not stale()
    job['last_ok_at']=None
    assert stale()
    data['scheduler']['uptime_s']=100
    assert not stale()
    data['scheduler']['uptime_s']=10000
    job['enabled']=False
    assert not stale()


def test_long_job_timeout_is_included_in_alert_grace(monkeypatch):
    from sports import service
    from sports.tests.test_service import _ops
    monkeypatch.setattr(service.time, 'time', lambda: 100000.)
    job = {'enabled':True, 'every_s':300, 'timeout_s':3600, 'last_ok_at':None}
    data = _ops(scheduler={'uptime_s':3000,'jobs':{'settle':job}})
    assert not any(a['code']=='grading_job_stale' for a in service.alerts(data))
    data['scheduler']['uptime_s']=4000
    assert any(a['code']=='grading_job_stale' for a in service.alerts(data))


def test_daily_failure_remains_visible_after_a_not_due_success():
    from sports import service
    from sports.tests.test_service import _ops
    data = _ops(scheduler={'jobs':{'grades-daily':{'enabled':True,'last_exit':{'code':0}}}},
                grades_daily={'last_error':{'stages':{'gradecheck':'failed'}},'publish_pending':True})
    codes = lambda: {a['code'] for a in service.alerts(data)}
    assert {'daily_grading_incomplete','daily_report_unpublished'} <= codes()
    data['grades_daily']={'last_error':None,'publish_pending':False}
    assert not codes()
