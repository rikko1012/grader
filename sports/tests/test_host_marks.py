"""Kalshi and Polymarket are asked by several scheduler children (ingest-kalshi, the NBA / NFL ingest, the soccer and
tennis quotes jobs): one pacing mark and one rate-limit hold per host for all of them (sports/host_marks.py)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from sports import config, expansion_sources, host_marks, kalshi, net
from sports import expansion_kalshi as xk

KALSHI = 'api.elections.kalshi.com'
POLY = 'gamma-api.polymarket.com'
URL = f'https://{KALSHI}/trade-api/v2/events'


class Reply:
    def __init__(self, status=200, headers=None):
        self.status_code, self.headers, self.text = status, headers or {}, ''

    def json(self):
        return {'events': [], 'cursor': ''}


def _other_process(monkeypatch):
    """What a separate scheduler child starts with: no in-memory hold and no in-memory pacing slot."""
    monkeypatch.setattr(net, '_HOLD', {})
    monkeypatch.setattr(net, '_LAST', {})


def _ask(replies, tries=1, url=URL):
    sent = []
    def send():
        sent.append(time.time())
        return replies.pop(0)
    try:
        return net._request(url, None, None, 5, tries, (200,), send), sent
    except net.HttpError as e:
        return e, sent


def test_only_the_exchange_hosts_are_shared_and_they_keep_their_gap():
    assert host_marks.HOSTS == {KALSHI, POLY}
    assert net._GAPS[KALSHI] == 2.0 and net._GAPS[POLY] == 1.0


def test_three_processes_take_turns_one_gap_apart():
    waits = [host_marks.reserve(KALSHI, 2.0) for _ in range(3)]
    assert waits[0] == 0 and 1.9 < waits[1] <= 2.0 and 3.9 < waits[2] <= 4.0
    assert host_marks.reserve(POLY, 1.0) == 0                 # each host has its own mark
    assert (config.DATA_DIR / 'request-marks.db').exists()


def test_a_request_waits_for_the_slot_another_process_took(monkeypatch):
    slept = []
    monkeypatch.setattr(net.time, 'sleep', slept.append)
    _ask([Reply()])
    _other_process(monkeypatch)                               # e.g. tennis-quotes right after ingest-kalshi sent
    reply, sent = _ask([Reply()])
    assert reply.status_code == 200 and len(sent) == 1 and len(slept) == 1 and 1.9 < slept[0] <= 2.0
    # A host that is not shared is paced per process only, as before.
    slept.clear()
    _ask([Reply()], url='https://site.api.espn.com/x')
    _other_process(monkeypatch)
    _ask([Reply()], url='https://site.api.espn.com/x')
    assert slept == [] and host_marks.held_until('site.api.espn.com') == 0


def test_a_stored_time_far_ahead_is_a_clock_change_not_a_queue():
    host_marks.reserve(KALSHI, 2.0, delay=3600)
    assert host_marks.reserve(KALSHI, 2.0) == 0


def test_retry_after_seen_by_one_job_stops_every_other_job_without_a_request(monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path / 'expansion'))
    monkeypatch.setattr(net.time, 'sleep', lambda s: None)
    error, sent = _ask([Reply(429, {'Retry-After': '600'})])  # ingest-kalshi is told to wait 10 minutes
    assert error.status == 429 and len(sent) == 1
    for sport in ('soccer', 'tennis'):                        # the quotes jobs: separate processes, their own store
        _other_process(monkeypatch)
        assert 590 < expansion_sources.held_until(sport, KALSHI) - time.time() <= 600
        error, sent = _ask([Reply()])
        assert error.held and sent == [] and 590 < error.not_before - time.time() <= 600
    assert expansion_sources.held_until('tennis', POLY) - time.time() <= 0      # the other exchange is not held


def test_expansion_kalshi_step_sends_nothing_inside_ingest_kalshis_hold(monkeypatch, tmp_path):
    from sports.tests.test_expansion_kalshi import Source, _catalogue
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path / 'expansion'))
    (tmp_path / 'expansion').mkdir()
    net.hold(KALSHI, time.time() + 900)                       # published by the NBA / NFL job
    _other_process(monkeypatch)
    directory, ev = _catalogue(tmp_path / 'expansion')
    get = Source({'events': [ev], 'cursor': ''})
    rows, status = xk.collect('soccer', get, directory)
    assert rows == [] and get.calls == [] and status['error'] == 'held' and status['held_until']


def test_ingest_kalshi_sends_nothing_inside_a_quotes_jobs_rate_limit_hold(monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path / 'expansion'))
    expansion_sources.hold('tennis', KALSHI, time.time() + xk.HOLD_429_S)       # what collect() does on a 429
    _other_process(monkeypatch)
    sent = []
    class Session:
        def get(self, url, **kw):
            sent.append(url)
            return Reply()
    monkeypatch.setattr(net, 'session', lambda: Session())
    monkeypatch.setattr(net, 'get_json', lambda url, **kw: net._request(
        url, kw.get('params'), None, 5, 3, (200,), lambda: net.session().get(url)).json())
    slept = []
    monkeypatch.setattr(net.time, 'sleep', slept.append)
    rows, status = kalshi.load({'key': 'nba', 'name': 'NBA'}, refresh=True)
    assert sent == [] and rows == [] and status['ok'] is False and 'wait until' in status['error']
    assert slept == []                                        # no pacing slot is taken for a request that is not sent


def test_a_403_hold_stays_with_the_soccer_and_tennis_jobs(monkeypatch, tmp_path):
    """A 403 is not a rate signal and may concern one series only: it never pauses the NBA / NFL collectors."""
    from sports.tests.test_expansion_kalshi import Source, _catalogue
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path / 'expansion'))
    (tmp_path / 'expansion').mkdir()
    directory, ev = _catalogue(tmp_path / 'expansion')
    rows, status = xk.collect('soccer', Source(net.HttpError(403, URL, 'forbidden')), directory)
    assert status['error'] == 'http_403'
    _other_process(monkeypatch)
    assert host_marks.held_until(KALSHI) - time.time() <= 2                     # only the 2 s pacing mark
    assert abs(expansion_sources.held_until('tennis', KALSHI) - time.time() - 6 * 3600) < 5
    # Reading the stored 403 hold back in a later quotes run does not publish it either.
    monkeypatch.setattr(net, 'get_json', lambda url, **kw: {})
    expansion_sources.get_json('tennis', URL)
    assert host_marks.held_until(KALSHI) - time.time() <= 2


def test_a_429_without_retry_after_holds_the_shared_host_for_fifteen_minutes(monkeypatch):
    monkeypatch.setattr(net.time, 'sleep', lambda s: None)
    error, sent = _ask([Reply(429), Reply(429), Reply(429)], tries=3)
    assert error.status == 429 and len(sent) == 3             # the call's own retries are unchanged
    _other_process(monkeypatch)
    assert abs(net.held_until(KALSHI) - time.time() - 900) < 5
    error, sent = _ask([Reply()])
    assert error.held and sent == []
    # A host only one job asks keeps its old behaviour: no hold without a Retry-After.
    error, sent = _ask([Reply(429)], url='https://site.api.espn.com/x')
    assert error.status == 429 and net.held_until('site.api.espn.com') == 0
    # A 500 is not a rate limit.
    _other_process(monkeypatch)
    _ask([Reply(500)], url=f'https://{POLY}/events')
    assert net.held_until(POLY) == 0


def test_a_hold_is_never_shortened():
    far = time.time() + 3600
    host_marks.hold(KALSHI, far)
    host_marks.hold(KALSHI, time.time() + 5)
    assert host_marks.held_until(KALSHI) == far


def test_switched_off_nothing_is_written_and_pacing_is_per_process(monkeypatch):
    monkeypatch.setenv('SPORTS_SHARED_HOLDS', '0')
    net.hold(KALSHI, time.time() + 600)
    assert host_marks.reserve(KALSHI, 2.0, delay=0.5) == 0.5 and host_marks.held_until(KALSHI) == 0
    assert not (config.DATA_DIR / 'request-marks.db').exists()
    assert net.held_until(KALSHI) > time.time()               # this process still honours its own hold


def test_an_unusable_file_never_fails_a_request(monkeypatch, tmp_path):
    (tmp_path / 'request-marks.db').write_bytes(b'not a database' * 100)
    assert host_marks.reserve(KALSHI, 2.0, delay=0.25) == 0.25 and host_marks.held_until(KALSHI) == 0
    host_marks.hold(KALSHI, time.time() + 60)
    monkeypatch.setattr(net.time, 'sleep', lambda s: None)
    reply, sent = _ask([Reply()])
    assert reply.status_code == 200 and len(sent) == 1
    blocked = tmp_path / 'a-file'
    blocked.write_text('x')
    monkeypatch.setattr(config, 'DATA_DIR', blocked / 'sports')               # the folder cannot be created
    assert host_marks.reserve(KALSHI, 2.0) == 0 and host_marks.held_until(KALSHI) == 0


def test_real_processes_never_send_closer_than_the_gap(tmp_path):
    code = ('import sys, time\n'
            'from sports import host_marks\n'
            'for _ in range(4):\n'
            '    time.sleep(host_marks.reserve("api.elections.kalshi.com", 0.15))\n'
            '    print(repr(time.time()), flush=True)\n')
    env = dict(os.environ, SPORTS_DATA_DIR=str(tmp_path), PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    env.pop('SPORTS_SHARED_HOLDS', None)
    children = [subprocess.Popen([sys.executable, '-c', code], env=env, stdout=subprocess.PIPE, text=True)
                for _ in range(3)]
    times = sorted(float(line) for child in children for line in child.communicate(timeout=60)[0].split())
    assert all(child.returncode == 0 for child in children) and len(times) == 12
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) > 0.12, gaps                             # 0.15 s apart, less the sleep's own jitter
