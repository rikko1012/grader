"""PrizePicks soccer tabs, adaptive coverage (ppsoccer-1119 review blockers): the number of soccer requests a run grows
by one after 10 clean minutes and halves at once on a 429 from ANY PrizePicks consumer (soccer, tennis, NBA / NFL);
rotation is fair (tab 82 every 180 s, then least-recently-attempted); a failed /leagues read backs off."""
import json
import sqlite3
import time
from datetime import timedelta

import pytest

from sports import expansion_board as board, expansion_sources as sources, net
from sports.tests.test_pp_soccer_leagues_1119 import (env, run, status, snap_ids, visible_late, pp_tab,  # noqa: F401
                                                      LISTED)


def state(env):
    return json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))


def set_level(env, level):
    st = state(env)
    st['aimd']['level'] = level
    (env['dir'] / board.PP_STATE).write_text(json.dumps(st), encoding='utf-8')


def all_tabs(env):
    """Every mapped tab listed with standard lines (9 sources -> max level 5 at 90 s)."""
    names = {531: 'La Liga', 533: 'Bundesliga', 535: 'Ligue 1', 79: 'UCL', 14: 'EPL'}
    for lid, name in names.items():
        env['listed'][lid] = (name, 20, 'soccer', None)
        env['tabs'][lid] = pp_tab(lid, name, [(f'{lid}a', f'p{lid}', 'Shots', 1.5, 'standard')])


def test_max_level_and_league_backoff_schedule(monkeypatch):
    monkeypatch.delenv('SCOUT_SOCCER_PP_MAX_LEVEL', raising=False)
    assert board.max_level(9) == 5 and board.max_level(4) == 2 and board.max_level(1) == 1   # requests per 90 s
    monkeypatch.setenv('SCOUT_SOCCER_PP_MAX_LEVEL', '3')
    assert board.max_level(9) == 3
    monkeypatch.setenv('SCOUT_SOCCER_PP_MAX_LEVEL', '1')
    assert board.max_level(9) == 1                           # the rollback lever: one request per 90 s
    assert [board.leagues_wait_s(n) for n in range(6)] == [0, 600, 1200, 2400, 3600, 3600]


def test_level_one_rotation_is_fair_and_tab_82_is_read_every_180_s(env):
    runs = run(env, 13)                                      # 45 s runs; level 1 = one request per 90 s
    calls = [c for c, _ in runs]
    assert calls == [[82, 'leagues'], [], [14], [], [82], [], [6], [], [82], [], [536], [], [82]]
    assert all(len([x for x in c if x != 'leagues']) <= 1 for c in calls)        # least-recently-attempted first
    for _, snap in runs:
        assert visible_late(snap, env)['SOCCER'] == 2                           # tab 82 never dark
    # a tab with an EARLIER kick-off never takes another tab's turn
    env['tabs'][536] = pp_tab(536, 'Liga MX', [('536a', 'p4', 'Passes Attempted', 65.5, 'standard')],
                              start='2026-11-19T18:00:00Z')
    st = status(env)
    assert st['coverage']['max_level'] == 2                                    # 82 + MLS, Liga MX, Serie A
    assert st['source']['ok'] is True


def test_grows_without_429s_to_full_coverage_and_then_every_source_stays_on_the_board(env):
    runs = run(env, 15)
    assert status(env)['coverage']['level'] == 2              # +1 after 600 s with no 429 anywhere
    run(env, 4)                                               # one 180 s round at the new level
    for calls, snap in run(env, 16):
        assert len([x for x in calls if x != 'leagues']) <= 2
        seen = visible_late(snap, env)
        assert (seen['SOCCER'], seen['MLS'], seen['Liga MX'], seen['Serie A']) == (2, 1, 1, 1)
    st = status(env)
    assert st['coverage']['full'] and st['coverage']['level'] == st['coverage']['max_level'] == 2
    # every 180 s (a fake run here lasts 45 s + its own request time, so up to ~200 s)
    assert all(lg['refresh_age_s'] is not None and lg['refresh_age_s'] <= 240 for lg in st['leagues']
               if lg['id'] in (82, 6, 536, 534))


def test_nine_sources_reach_level_five_in_about_forty_minutes_and_never_more(env):
    all_tabs(env)
    levels = []
    for calls, snap in run(env, 70):
        levels.append(status(env)['coverage']['level'])
        assert len([x for x in calls if x != 'leagues']) <= 3     # level 5 = 2-3 requests a 45 s run
    assert max(levels) == 5 and levels.index(5) <= 60           # 4 steps x 600 s (+ a run each)
    for calls, snap in run(env, 8):
        seen = visible_late(snap, env)
        assert len(seen) == 9 and all(seen.values())             # every tab continuously on the board
    st = status(env)
    assert st['coverage']['level'] == 5 and st['coverage']['cap'] == 5
    assert len(json.dumps(state(env))) < 8_000_000


@pytest.mark.parametrize('consumer', ['tennis', 'nba_nfl'])
def test_a_429_from_another_consumer_halves_coverage_at_once_and_skips_this_run(env, consumer):
    all_tabs(env)
    run(env, 2)
    set_level(env, 4)
    assert sources.record_429(consumer) is True
    calls, snap = run(env, 1)[0]
    st = status(env)
    assert calls == [] and st['stopped'] == 'recent_429_other_consumer'      # cool-off: no request this run
    assert st['coverage']['level'] == 2 and st['coverage']['ceiling'] == 4
    assert st['coverage']['last_halving']['why'] == '429:' + consumer
    assert st['pp_429'][consumer]['last_1h'] == 1
    assert snap['offers']                                                   # last good rows carried
    calls, _ = run(env, 1)[0]                                               # the same event is never counted twice
    assert status(env)['coverage']['level'] == 2 and len(calls) == 1


def test_own_429_halves_and_a_second_backs_off_to_tab_82(env):
    all_tabs(env)
    run(env, 2)
    set_level(env, 4)
    env['fail'] = {lid: 429 for lid in list(env['tabs']) + ['leagues']}
    calls, _ = run(env, 1)[0]
    st = status(env)
    assert len(calls) == 1 and st['stopped'] == 'http_429' and st['coverage']['level'] == 2
    calls, _ = run(env, 1)[0]
    st = status(env)
    assert len(calls) == 1 and st['coverage']['level'] == 1 and st['backoff_until']
    env['fail'] = {}
    backoff = run(env, 12)                                                  # 12 x 45 s < 600 s
    assert all(c in ([82], []) for c, _ in backoff) and [c for c, _ in backoff].count([82]) == 3   # every 180 s


def test_recovery_after_the_ceiling_expires_and_never_above_the_ceiling_meanwhile(env, monkeypatch):
    all_tabs(env)
    monkeypatch.setenv('SCOUT_SOCCER_PP_CEILING_S', '1800')
    run(env, 2)
    set_level(env, 4)
    sources.record_429('nba_nfl')
    run(env, 1)
    until = board.parse_utc(state(env)['aimd']['ceiling_until'])
    levels = []
    for _ in range(90):                                                     # > 60 min
        run(env, 1)
        levels.append((env['clock'].t, status(env)['coverage']['level']))
    assert levels[0][1] == 2
    early = [lv for t, lv in levels if t < until]                           # ceiling 4 on for 30 min: at most 3
    assert max(early) == 3
    assert max(lv for _, lv in levels) >= 4                                 # then it may probe 4 again


def test_a_shared_hold_never_counts_as_clean_time(env):
    run(env, 2)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sources, 'held_until', lambda sport, url: 10 ** 12)
        for calls, _ in run(env, 24):                                       # 18 min held
            assert calls == []
    assert status(env)['coverage']['level'] == 1
    st = state(env)
    assert board._age_s(st['aimd']['clean_since'], env['clock'].iso()) <= 45  # the clean clock restarted


def test_nba_nfl_hold_is_copied_to_the_shared_store_and_soccer_honours_it(env, monkeypatch):
    run(env, 2)
    monkeypatch.setattr(net, '_HOLD', {sources.PP_HOST: time.time() + 120})
    assert sources.record_429('nba_nfl')
    assert sources.held_until('soccer', board.PP_URL) > time.time() + 100
    monkeypatch.setattr(net, '_HOLD', {})
    calls, _ = run(env, 1)[0]
    assert calls == []


def test_failed_league_list_backs_off_instead_of_retrying_every_run(env):
    env['fail'] = {'leagues': 500}

    def failing(sport, url, params=None, **kw):
        lid = (params or {}).get('league_id', 'leagues')
        env['calls'].append(lid)
        env['clock'].t += timedelta(seconds=2)
        if lid == 'leagues':
            raise net.HttpError(500, url, '')
        return env['tabs'].get(lid, {'data': [], 'included': []})
    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(sources, 'get_json', failing)
    at = []
    try:
        for _ in range(85):                                                 # > 60 min
            t = env['clock'].t
            calls, _ = run(env, 1)[0]
            if 'leagues' in calls:
                at.append(t)
    finally:
        mp.undo()
    gaps = [(b - a).total_seconds() for a, b in zip(at, at[1:])]
    assert len(at) == 3 and 600 <= gaps[0] < 660 and 1200 <= gaps[1] < 1260   # 600 s, then 1200 s, then 2400 s
    st = status(env)
    assert st['league_list']['fails'] == 3 and st['league_list']['next_try_after_error_s'] == 2400


@pytest.mark.parametrize('bad', [[], 'oops', {'data': 'x'}])
def test_bad_league_list_or_tab_body_is_a_failed_read_not_a_crash(env, bad):
    env['tabs'][82] = bad
    real_listed = env['listed']

    def get_json(sport, url, params=None, **kw):
        lid = (params or {}).get('league_id', 'leagues')
        env['calls'].append(lid)
        return bad if lid in ('leagues', 82) else env['tabs'].get(lid, {'data': [], 'included': []})
    mp = pytest.MonkeyPatch()
    mp.setattr(sources, 'get_json', get_json)
    try:
        runs = run(env, 3)
    finally:
        mp.undo()
    st = status(env)
    assert st['league_list']['fails'] == 1 and st['source']['ok'] is False
    assert sum(c.count('leagues') for c, _ in runs) == 1
    assert real_listed


def test_a_failing_league_tab_never_flips_the_prizepicks_source(env):
    run(env, 20)
    env['fail'] = {534: 'empty', 536: 429}
    for calls, snap in run(env, 12):
        assert visible_late(snap, env)['SOCCER'] == 2
    env['fail'] = {534: 'empty'}
    for calls, snap in run(env, 12):
        assert snap['sources']['prizepicks']['ok'] is True                 # only tab 82 decides the member strip
    st = status(env)
    serie = next(lg for lg in st['leagues'] if lg['id'] == 534)
    assert serie['ok'] is False and serie['last_error'] == 'empty_listed_tab'
    env['fail'] = {82: 'empty'}
    for calls, snap in run(env, 8):
        pass
    assert snap['sources']['prizepicks']['ok'] is False


def test_checked_at_is_never_fresher_than_a_carried_row(env):
    for _, snap in run(env, 30):
        src = snap['sources']['prizepicks']
        assert all(r['observed_at'] >= src['checked_at'] for r in snap['offers'] if r['platform'] == 'prizepicks')


def test_lines_fetch_reports_429s_without_changing_the_answer_or_the_exception(tmp_path, monkeypatch):
    from sports import lines

    class R:
        def __init__(self, code, body=None, headers=None):
            self.status_code, self._b, self.headers, self.text = code, body, headers or {}, ''

        def json(self):
            return self._b
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    monkeypatch.delenv('SCOUT_SOCCER_PP_LEAGUES', raising=False)
    monkeypatch.setattr(net, 'backoff', lambda attempt, **k: 0)
    monkeypatch.setattr(net, '_GAPS', {}, raising=False)
    monkeypatch.setattr(net, 'DEFAULT_GAP', 0)
    answers = [R(429), R(200, {'data': [1]})]

    class S:
        def get(self, url, **kw):
            return answers.pop(0)
    monkeypatch.setattr(net, 'session', lambda: S())
    monkeypatch.setattr(net, 'get', lambda url, params=None, headers=None, timeout=30, tries=3, ok=(200,): net._request(
        url, params, headers, timeout, tries, ok, lambda: net.session().get(url)))     # the real get (conftest: offline)
    lg = {'pp': 7, 'key': 'nba'}
    assert lines._fetch_pp(lg) == {'data': [1]}                            # retried and answered exactly as before
    assert [e['consumer'] for e in sources.pp_events_since(0)] == ['nba_nfl']
    answers[:] = [R(429), R(429), R(429)]
    with pytest.raises(net.HttpError) as e:
        lines._fetch_pp(lg)
    assert e.value.status == 429
    assert sum(x['n'] for x in sources.pp_events_since(0)) == 4
    monkeypatch.delenv('SCOUT_SOCCER_ENABLED')                             # soccer off: nothing written, no file
    other = tmp_path / 'off'
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(other))
    answers[:] = [R(429), R(200, {'ok': 1})]
    assert lines._fetch_pp(lg) == {'ok': 1}
    assert not other.exists()
    monkeypatch.setattr(sources, 'record_429', lambda *a, **k: 1 / 0)       # a broken reporter changes nothing
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    answers[:] = [R(429), R(200, {'ok': 2})]
    assert lines._fetch_pp(lg) == {'ok': 2}


def test_held_records_tennis_and_soccer_429s_but_not_held_skips(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    monkeypatch.setenv('SCOUT_PP_GAP_S', '0')

    def drew_429():
        with net._LOCK:
            net._N429[sources.PP_HOST] = net._N429.get(sources.PP_HOST, 0) + 1
        raise net.HttpError(429, board.PP_URL, '')
    with pytest.raises(net.HttpError):
        sources._held('tennis', board.PP_URL, drew_429)

    def held():
        raise net.HttpError(429, board.PP_URL, '', not_before=time.time() + 60, held=True)
    with pytest.raises(net.HttpError):
        sources._held('tennis', board.PP_URL, held)                        # no request sent: not a 429 answer
    assert [(e['consumer'], e['n']) for e in sources.pp_events_since(0)] == [('tennis', 1)]
    with sqlite3.connect(tmp_path / 'request-holds.db') as con:
        assert con.execute('SELECT COUNT(*) FROM pp_events').fetchone()[0] == 1


def test_scheduler_clamps_the_quotes_override_to_30_s(monkeypatch):
    from sports.scheduler import default_jobs
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    monkeypatch.setenv('SPORTS_JOB_SOCCER_QUOTES_EVERY_S', '5')
    monkeypatch.setenv('SPORTS_JOB_TENNIS_QUOTES_EVERY_S', '0')
    jobs = default_jobs()
    assert jobs['soccer-quotes']['every_s'] == 30 and jobs['tennis-quotes']['every_s'] == 30
    monkeypatch.setenv('SPORTS_JOB_SOCCER_QUOTES_EVERY_S', '120')
    assert default_jobs()['soccer-quotes']['every_s'] == 120


def test_ops_lists_coverage_level_per_tab_refresh_age_and_429s_per_consumer(env):
    run(env, 3)
    sources.record_429('tennis', 2)
    run(env, 1)
    st = status(env)
    assert {'level', 'max_level', 'cap', 'grow_in_s', 'halvings', 'last_halving'} <= set(st['coverage'])
    assert st['pp_429']['tennis']['last_1h'] == 2 and st['pp_429']['tennis']['last_24h'] == 2
    assert all('refresh_age_s' in lg and 'ok' in lg for lg in st['leagues'])


def test_run_budget_stops_new_requests_inside_the_job_timeout(env, monkeypatch):
    all_tabs(env)
    run(env, 2)
    set_level(env, 5)
    monkeypatch.setenv('SCOUT_SOCCER_PP_SPACE_S', '20')                    # 20 s apart: only 2 fit in 30 s
    calls, _ = run(env, 1)[0]
    st = status(env)
    assert len(calls) == 2 and st['coverage']['constrained'] == 'run_budget'
