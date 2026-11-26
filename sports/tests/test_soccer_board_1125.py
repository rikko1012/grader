"""soccer-board-1125 (owner 2026-11-25: "fix soccer it keeps removing props off the board").

PrizePicks' SOCCER tab grew to 15,491 projections (190 standard, the rest demon / goblin). The capped quotes job was
killed on every read of it, the failure back-off spaced runs past the rows' 300 s life, and every soccer line left the
board. These tests pin the structure of the fix, never a byte or time threshold:
  - an answer is reduced while it is read (every standard line, a bounded set of demon / goblin lines);
  - under the bound nothing changes;
  - the state file can always be read back;
  - a tab whose read ends the run does not take the other tabs or the board with it;
  - one failed tab-82 read, or tab 82 failing while league tabs are fine, no longer hides lines that are still fresh;
  - a failing quotes job is started again inside its rows' 300 s.
"""
import json
import random
import sqlite3
from datetime import datetime, timedelta

import pytest

from sports import expansion_board as board, expansion_offers as offers, expansion_sources as sources, net
from sports import scheduler
from sports.tests.test_pp_soccer_leagues_1119 import (env, run, status, snap_ids, visible_late, pp_tab,  # noqa: F401
                                                      NOW)


def state(env):
    return json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))


def flood(games=4, standard=2, alt=30, first_start='2030-01-01T12:00:00Z', lid=82, name='SOCCER', seed=1):
    """A PrizePicks answer like the flooded tab: `games` matches an hour apart, each with `standard` standard lines and
    `alt` demon / goblin lines (projection ids rise with the start), in a shuffled order."""
    t0 = datetime.fromisoformat(first_start.replace('Z', '+00:00'))
    data, inc, players = [], [{'type': 'duration', 'id': 'd1', 'attributes': {'name': 'Full'}}], set()
    pid = 1000
    for g in range(games):
        start = (t0 + timedelta(hours=g)).strftime('%Y-%m-%dT%H:%M:%SZ')
        inc.append({'type': 'game', 'id': f'g{g}', 'attributes': {'metadata': {'game_info': {'teams': {
            'away': {'abbreviation': f'A{g}'}, 'home': {'abbreviation': f'H{g}'}}}}}, 'relationships': {}})
        for k in range(standard + alt):
            pid += 1
            player = f'p{g}_{k % 7}'
            odds = 'standard' if k < standard else ('goblin' if k % 5 == 0 else 'demon')
            data.append({'type': 'projection', 'id': str(pid), 'attributes': {
                'stat_type': 'Shots', 'line_score': 0.5 + k, 'start_time': start, 'status': 'pre_game',
                'projection_type': 'Single Stat', 'odds_type': odds,
                'allowed_wager_types': 'under_or_over' if odds == 'standard' else 'over'},
                'relationships': {'new_player': {'data': {'type': 'new_player', 'id': player}},
                                  'game': {'data': {'type': 'game', 'id': f'g{g}'}},
                                  'duration': {'data': {'type': 'duration', 'id': 'd1'}},
                                  'league': {'data': {'type': 'league', 'id': str(lid)}}}})
            if player not in players:
                players.add(player)
                inc.append({'type': 'new_player', 'id': player, 'attributes': {
                    'league': name, 'league_id': lid, 'name': f'Player {player}', 'team': f'A{g}',
                    'position': 'Midfielder'}})
    random.Random(seed).shuffle(data)
    return {'data': data, 'included': inc, 'meta': {'total_pages': 1}}


def chunked(body, n=7):
    raw = json.dumps(body).encode('utf-8')
    return (raw[i:i + n] for i in range(0, len(raw), n))


def is_alt(item):
    return item['attributes']['odds_type'] in board.PP_ALT


def game_of(item):
    return item['relationships']['game']['data']['id']


def select(body, alt_max):
    s = board.PPSelect(alt_max, NOW)
    for item in body['data']:
        s.add(item)
    return s


# ------------------------------------------------------------------------------------------------ the selection
def test_every_standard_line_is_kept_and_alt_lines_are_bounded():
    body = flood(games=4, standard=3, alt=40)
    s = select(body, 10)
    kept = s.result()
    assert sum(not is_alt(x) for x in kept) == 12                    # every standard line, whatever the bound
    assert s.info() == {'seen': 172, 'alt_seen': 160, 'alt_kept': 14, 'alt_games': 4}
    assert [x['id'] for x in kept] == [x['id'] for x in body['data'] if x in kept]      # the answer's own order
    by_game = {}
    for x in kept:
        if is_alt(x):
            by_game.setdefault(game_of(x), []).append(int(x['id']))
    lowest = {g: min(int(x['id']) for x in body['data'] if is_alt(x) and game_of(x) == g) for g in by_game}
    assert set(by_game) == {'g0', 'g1', 'g2', 'g3'}                  # every match keeps a line of its own ...
    assert all(lowest[g] in ids for g, ids in by_game.items())       # ... its lowest projection id
    assert {g: len(ids) for g, ids in by_game.items()} == {'g0': 11, 'g1': 1, 'g2': 1, 'g3': 1}   # then the soonest


def test_the_choice_does_not_depend_on_the_answers_order():
    picks = {tuple(sorted(x['id'] for x in select(flood(games=5, standard=2, alt=25, seed=seed), 17).result()))
             for seed in range(6)}
    assert len(picks) == 1


def test_under_the_bound_nothing_changes_at_all():
    body = flood(games=3, standard=2, alt=9)
    before = offers.prizepicks(json.loads(json.dumps(body)), 'soccer', NOW)
    whole = json.loads(json.dumps(body))
    info = board.pp_reduce(whole, 3000, NOW)
    assert info == {'seen': 33, 'alt_seen': 27, 'alt_kept': 27, 'alt_games': 3}
    assert json.dumps(offers.prizepicks(whole, 'soccer', NOW)) == json.dumps(before)     # rows, order, revisions
    streamed = board.pp_reader(3000, NOW)(chunked(body))
    assert streamed.pop(board.PP_INFO) == info
    assert streamed == body


def test_started_and_startless_alt_lines_never_take_a_place():
    body = flood(games=2, standard=1, alt=8, first_start='2026-11-19T15:30:00Z')     # g0 started before NOW (16:00)
    next(x for x in body['data'] if is_alt(x) and game_of(x) == 'g1')['attributes']['start_time'] = None
    kept = [x for x in select(body, 4).result() if is_alt(x)]
    assert len(kept) == 5 and all(game_of(x) == 'g1' and x['attributes']['start_time'] for x in kept)
    # and the rows built from what is kept are exactly the rows the adapter built before for those projections
    full = {r['offer_id']: r for r in offers.prizepicks(json.loads(json.dumps(body)), 'soccer', NOW)}
    cut = json.loads(json.dumps(body))
    board.pp_reduce(cut, 4, NOW)
    rows = offers.prizepicks(cut, 'soccer', NOW)
    assert len(rows) == 6 and all(r == full[r['offer_id']] for r in rows)


def test_the_stream_reader_and_the_whole_body_path_agree_when_the_bound_bites():
    body = flood(games=6, standard=3, alt=50, seed=4)
    whole = json.loads(json.dumps(body))
    info = board.pp_reduce(whole, 23, NOW)
    assert info == {'seen': 318, 'alt_seen': 300, 'alt_kept': 29, 'alt_games': 6}
    for n in (1, 5, 64, 100_000):
        streamed = board.pp_reader(23, NOW)(chunked(body, n))
        assert streamed[board.PP_INFO] == info and streamed['data'] == whole['data']
        assert streamed['included'] == body['included'] and streamed['meta'] == body['meta']
    assert board.pp_reduce(whole, 23, NOW) is info and len(whole['data']) == 18 + 29     # never reduced twice


def test_alt_max_zero_is_standard_lines_only_and_bad_shapes_fail_as_before():
    body = flood(games=2, standard=2, alt=5)
    out = board.pp_reader(0, NOW)(chunked(body))
    assert [is_alt(x) for x in out['data']] == [False] * 4
    assert out[board.PP_INFO] == {'seen': 14, 'alt_seen': 10, 'alt_kept': 0, 'alt_games': 0}
    for bad in ({'data': 'x'}, {'data': None}, {'included': []}):
        got = board.pp_reader(5, NOW)(chunked(bad))
        assert got == bad and board.pp_reduce(got, 5, NOW)['seen'] == 0
    with pytest.raises(ValueError):
        board.pp_reader(5, NOW)(iter([b'[]']))
    with pytest.raises(ValueError):
        board.pp_reader(5, NOW)(iter([json.dumps(body).encode()[:-9]]))      # a cut answer is never used


# ---------------------------------------------------------------------------------------------- the collector
def test_a_flooded_tab_82_gives_a_board_with_every_standard_line(env, monkeypatch):
    monkeypatch.setenv('SCOUT_SOCCER_PP_ALT_MAX', '25')
    env['tabs'][82] = flood(games=5, standard=4, alt=60)
    env['listed'][82] = ('SOCCER', 320, 'soccer', None)
    calls, snap = run(env, 1)[0]
    mine = [r for r in snap['offers'] if 'pp_league' not in r]
    assert sum(r['variant'] == 'standard' for r in mine) == 20 and len(mine) == 50
    st = status(env)
    t82 = next(lg for lg in st['leagues'] if lg['id'] == 82)
    assert (t82['seen'], t82['alt_seen'], t82['alt_kept'], t82['kept'], t82['on_board']) == (320, 300, 30, 50, 50)
    assert st['alt_max'] == 25 and st['state_rows_dropped'] == 0 and st['state_lost'] is None
    assert snap['sources']['prizepicks']['ok'] is True and st['tab82']['fresh'] is True
    assert len(state(env)['per']['82']['rows']) == 50                # the state holds what the board carries


def test_the_getter_is_asked_to_stream_and_a_whole_body_is_reduced_the_same(env, monkeypatch):
    monkeypatch.setenv('SCOUT_SOCCER_PP_ALT_MAX', '9')
    env['tabs'][82] = flood(games=3, standard=2, alt=30)
    first = run(env, 1)[0][1]                                        # the fixture's getter answers whole bodies
    asked, plain = [], sources.get_json

    def streaming(sport, url, params=None, reader=None, **kw):
        whole = plain(sport, url, params=params, **kw)               # the fixture's fake: failures, clock, call log
        if reader is None or not isinstance(whole, dict):
            return whole
        asked.append((params or {}).get('league_id'))
        return reader(chunked(whole, 11))
    env['dir'].joinpath(board.PP_STATE).unlink()
    env['clock'].t += timedelta(seconds=400)
    monkeypatch.setattr(sources, 'get_json', streaming)
    second = run(env, 1)[0][1]
    assert asked == [82] and snap_ids(second) == snap_ids(first)
    assert [r['revision'] for r in second['offers']] == [r['revision'] for r in first['offers']]
    assert sum(r['variant'] != 'standard' for r in second['offers'] if 'pp_league' not in r) == 12


def test_a_listed_league_tab_with_only_alt_lines_is_not_a_failed_read(env):
    env['listed'][534] = ('Serie A', 20, 'soccer', None)
    env['tabs'][534] = pp_tab(534, 'Serie A', [('534x', 'p5', 'Shots', 3.5, 'demon'), ('534y', 'p5', 'Shots', 0.5, 'goblin')],
                              teams=('Roma', 'Parma'), full=(('Roma', 'AS Roma'), ('Parma', 'Parma Calcio 1913')))
    run(env, 40)
    serie = next(lg for lg in status(env)['leagues'] if lg['id'] == 534)
    assert serie['last_ok_at'] and serie['last_error'] is None
    assert (serie['kept'], serie['seen'], serie['alt_seen'], serie['alt_kept']) == (0, 2, 2, 0)
    assert state(env)['leagues'].get('refresh') is not True          # no forced /leagues re-read


def test_the_state_file_can_always_be_read_back(env, monkeypatch):
    env['tabs'][82] = flood(games=4, standard=3, alt=40)
    monkeypatch.setattr(board, 'STATE_ROWS_BYTES', 9000)             # far under what one read keeps
    run(env, 1)
    st = status(env)
    assert st['state_rows_dropped'] > 0 and st['state_lost'] is None
    rows = state(env)['per']['82']['rows']
    assert sum(r['variant'] == 'standard' for r in rows) == 12       # alt rows give way first
    assert sum(len(json.dumps(r, separators=(',', ':'))) + 1 for r in rows) <= 9000
    calls, _ = run(env, 1)[0]
    assert 'leagues' not in calls and 82 not in calls                # the next run knew what the last one did


@pytest.mark.parametrize('why', ['too_large', 'unreadable'])
def test_a_state_file_that_cannot_be_read_back_is_reported_not_silent(env, why):
    run(env, 2)
    content = 'x' * 8_000_001 if why == 'too_large' else '{not json'
    (env['dir'] / board.PP_STATE).write_text(content, encoding='utf-8')
    run(env, 1)
    assert status(env)['state_lost'] == why
    run(env, 1)
    assert status(env)['state_lost'] is None


class Killed(BaseException):
    """What a memory-cap kill or a job timeout looks like from inside: nothing after it runs."""


def test_a_tab_whose_read_ends_the_run_does_not_take_the_board_with_it(env):
    run(env, 6)
    plain, dying = sources.get_json, {'on': True}

    def get_json(sport, url, params=None, **kw):
        if dying['on'] and (params or {}).get('league_id') == 82:
            raise Killed()
        return plain(sport, url, params=params, **kw)
    mp = pytest.MonkeyPatch()
    mp.setattr(sources, 'get_json', get_json)
    try:
        deaths, boards, league_boards = [], 0, 0
        for _ in range(60):                                          # 45 min of 45 s runs
            try:
                snap = board.collect('soccer')
                boards += 1
                league_boards += any(r.get('pp_league') for r in snap['offers'])
            except Killed:
                deaths.append(env['clock'].t.timestamp())
            env['clock'].t += timedelta(seconds=45)
        gaps = [b - a for a, b in zip(deaths, deaths[1:])]
        assert len(deaths) >= 4 and boards == 60 - len(deaths)       # every other run wrote a board
        assert gaps[0] <= 270                                        # the first time: its normal pace ...
        assert gaps[1] >= 2 * board.REREAD_MIN_S and gaps[2] >= 4 * board.REREAD_MIN_S     # ... then 2x, 4x
        assert max(gaps) <= board.DIED_WAIT_MAX_S + 135
        assert league_boards > boards // 2                           # the league tabs kept their turns
        st = state(env)['per']['82']
        assert st['died'] >= len(deaths) - 1 and st['error'] == 'run_ended_mid_read'
        t82 = next(lg for lg in status(env)['leagues'] if lg['id'] == 82)
        assert t82['died'] == st['died'] and t82['died_at']
        dying['on'] = False
        env['clock'].t += timedelta(seconds=board.DIED_WAIT_MAX_S)
        run(env, 3)
        t82 = next(lg for lg in status(env)['leagues'] if lg['id'] == 82)
        assert t82['ok'] is True and t82['died'] == 0 and 'died' not in state(env)['per']['82']
    finally:
        mp.undo()


def test_a_run_that_ends_after_its_reads_is_not_counted_against_a_tab(env, monkeypatch):
    run(env, 4)
    real = board.save

    def dies(*a, **k):
        raise Killed()
    monkeypatch.setattr(board, 'save', dies)
    env['clock'].t += timedelta(seconds=200)
    with pytest.raises(Killed):
        board.collect('soccer')
    monkeypatch.setattr(board, 'save', real)
    st = state(env)
    assert 'reading' not in st and st['per']['82']['rows']           # the read was saved before the run ended
    env['clock'].t += timedelta(seconds=45)
    snap = run(env, 1)[0][1]
    assert not any(e.get('died') for e in state(env)['per'].values())
    assert any('pp_league' not in r and r['platform'] == 'prizepicks' for r in snap['offers'])   # carried, own stamp


def timeline(env, n):
    """n runs -> [(epoch s of the run, tab-82 rows a member still sees just before the next run, calls, source ok)]."""
    out = []
    for calls, snap in run(env, n):
        at = datetime.fromisoformat(snap['built_at'].replace('Z', '+00:00')).timestamp()
        out.append((at, visible_late(snap, env)['SOCCER'], calls, snap['sources']['prizepicks']['ok']))
    return out


def gaps_of(seen):
    reads = [at for at, _, calls, _ in seen if 82 in calls]
    return [b - a for a, b in zip(reads, reads[1:])]


def test_one_failed_tab_82_read_no_longer_takes_its_lines_off_the_board(env):
    run(env, 8)
    plain, fail = sources.get_json, {'left': 1}

    def get_json(sport, url, params=None, **kw):
        if (params or {}).get('league_id') == 82 and fail['left']:
            fail['left'] -= 1
            env['calls'].append(82)
            raise net.HttpError(None, url, 'read timed out')
        return plain(sport, url, params=params, **kw)
    mp = pytest.MonkeyPatch()
    mp.setattr(sources, 'get_json', get_json)
    try:
        seen = timeline(env, 20)
    finally:
        mp.undo()
    gaps = gaps_of(seen)
    assert gaps[0] <= 100                                            # asked again on the next request slot ...
    assert all(g >= 165 for g in gaps[1:])                           # ... once; then its normal pace
    assert all(n == 2 for _, n, _, _ in seen)                        # its two lines never left the board
    assert all(ok for _, _, _, ok in seen)
    assert state(env)['per']['82']['fails'] == 0


def test_a_rate_limited_tab_82_read_is_never_retried_early(env):
    run(env, 8)
    env['fail'] = {82: 429}
    seen = []
    while not any(82 in calls for _, _, calls, _ in seen):           # until the run that asks tab 82 and draws the 429
        seen += timeline(env, 1)
    assert state(env)['per']['82']['fails'] == 1 and not state(env)['per']['82'].get('retry_soon')
    env['fail'] = {}
    seen += timeline(env, 14)
    gaps = gaps_of(seen)
    assert gaps and all(g >= 165 for g in gaps)


def test_the_quick_retry_never_fires_while_a_rate_limit_is_on(env, monkeypatch):
    """Review (advisor, before ship): the cool-off and back-off branches ask tab 82 without spending the level's credit,
    so the quick retry must not apply there - no PrizePicks request 45 s after the last one during rate limiting."""
    import time as _t
    import types
    shim = types.SimpleNamespace(**{k: getattr(_t, k) for k in dir(_t) if not k.startswith('__')})
    shim.time = env['clock'].mono
    monkeypatch.setattr(board, 'time', shim)
    monkeypatch.setattr(sources, 'time', shim)
    run(env, 8)
    for mode in ('cool_off', 'backoff'):
        env['fail'] = {82: 'empty'}
        seen = []
        while not any(82 in calls for _, _, calls, _ in seen):       # the failed read that arms the quick retry
            seen += timeline(env, 1)
        assert state(env)['per']['82'].get('retry_soon') is True
        env['fail'] = {}
        for _ in range(8):                                           # 6 min of rate limiting right after it
            if mode == 'cool_off':
                sources.record_429('nba_nfl')                        # another consumer's 429 before every run
            else:
                st = state(env)
                st['backoff_until'] = (env['clock'].t + timedelta(seconds=600)).strftime('%Y-%m-%dT%H:%M:%SZ')
                (env['dir'] / board.PP_STATE).write_text(json.dumps(st), encoding='utf-8')
            seen += timeline(env, 1)
            assert status(env)['coverage']['constrained'] == mode
        gaps = gaps_of(seen)
        assert gaps and all(g >= 165 for g in gaps), (mode, gaps)
        run(env, 12)                                                 # settle before the next mode


def test_two_failures_in_a_row_go_back_to_the_normal_pace(env):
    run(env, 8)
    env['fail'] = {82: 'empty'}
    gaps = gaps_of(timeline(env, 20))
    assert gaps[0] <= 100 and len(gaps) >= 3 and all(g >= 165 for g in gaps[1:])
    assert state(env)['per']['82']['fails'] >= 3


def test_fresh_league_lines_stay_shown_while_tab_82_is_failing(env):
    run(env, 20)
    env['fail'] = {82: 'empty'}
    league_only = dark = 0
    for calls, snap in run(env, 40):                                 # 30 min of tab 82 failing
        mine = [r for r in snap['offers'] if r['platform'] == 'prizepicks']
        league = [r for r in mine if r.get('pp_league')]
        src = snap['sources']['prizepicks']
        assert src['ok'] is bool(mine)                               # lines on the board <=> the source is ok
        assert all(r['observed_at'] >= src['checked_at'] for r in mine)
        if league and len(league) == len(mine):
            league_only += 1
        if not mine:
            dark += 1
            assert src['error'] == 'empty_listed_tab'
    assert league_only > 5                                           # before: every one of these runs was hidden
    st = status(env)
    assert st['tab82'] == {'fresh': False, 'error': 'empty_listed_tab', 'retry_soon': False,
                           'fails': st['tab82']['fails']} and st['tab82']['fails'] >= 3
    env['fail'] = {82: 'empty', 536: 'empty', 6: 'empty', 534: 'empty', 14: 'empty'}
    env['clock'].t += timedelta(seconds=400)
    snap = run(env, 1)[0][1]
    assert snap['sources']['prizepicks']['ok'] is False and not snap['offers']


def test_tab_82_counts_as_fresh_for_its_rows_own_300_s(env):
    run(env, 1)
    env['fail'] = {82: 429, 536: 429, 6: 429, 534: 429, 14: 429, 'leagues': 429}
    flags = []
    for calls, snap in run(env, 9):
        age = (datetime.fromisoformat(snap['built_at'].replace('Z', '+00:00')) - datetime.fromisoformat(
            state(env)['per']['82']['ok_at'].replace('Z', '+00:00'))).total_seconds()
        flags.append((age, snap['sources']['prizepicks']['ok'], len(snap['offers'])))
    assert all(ok and n for age, ok, n in flags if age <= 300)       # rows on the board -> the source is ok
    assert all(not ok and not n for age, ok, n in flags if age > 300)
    assert any(255 < age <= 300 for age, ok, n in flags)             # the 45 s that used to be hidden
    assert any(age > 300 for age, ok, n in flags)


# ------------------------------------------------------------------------------------------ the quote history
def test_near_its_cap_the_quote_history_keeps_standard_lines_and_leaves_alt_lines_out(tmp_path, monkeypatch):
    from sports import expansion_archive as xa
    rows = offers.prizepicks(flood(games=2, standard=3, alt=8), 'soccer', NOW)
    standard = [r for r in rows if r['variant'] == 'standard']
    alt = [r for r in rows if r['variant'] != 'standard']
    assert (len(standard), len(alt)) == (6, 16)
    board.save('soccer', standard[:1], {}, NOW, tmp_path)            # creates the store
    monkeypatch.setattr(xa, 'live_bytes', lambda db: int(0.9 * board.QUOTES_CAP_MB * 1048576))
    out = board.record_quote_history(tmp_path, rows, NOW, budget_s=0)
    assert out['state'] == 'ok' and out['alt_skipped'] == 16 and out['written'] == 5

    def stored():
        con = sqlite3.connect(tmp_path / 'quotes.db')
        try:
            return {r[0] for r in con.execute('SELECT offer_id FROM revisions')}
        finally:
            con.close()
    assert stored() == {r['offer_id'] for r in standard}
    monkeypatch.setattr(xa, 'live_bytes', lambda db: int(0.5 * board.QUOTES_CAP_MB * 1048576))
    out = board.record_quote_history(tmp_path, rows, NOW, budget_s=0)
    assert 'alt_skipped' not in out and out['written'] == 16         # room again: they are recorded
    assert stored() == {r['offer_id'] for r in rows}


# ------------------------------------------------------------------------------------- the single-request path
def test_kill_switch_path_reads_tab_82_through_the_same_bound(env, monkeypatch):
    monkeypatch.setenv('SCOUT_SOCCER_PP_LEAGUES', '0')
    monkeypatch.setenv('SCOUT_SOCCER_PP_ALT_MAX', '7')
    env['tabs'][82] = flood(games=3, standard=2, alt=20)
    snap = board.collect('soccer')
    mine = [r for r in snap['offers'] if r['platform'] == 'prizepicks']
    assert sum(r['variant'] == 'standard' for r in mine) == 6 and len(mine) == 16


# ------------------------------------------------------------------------------------------------ the getter
class _Resp:
    status_code, headers = 200, {}

    def __init__(self, chunks, fail=None):
        self.chunks, self.fail, self.closed = chunks, fail, False

    def iter_content(self, n):
        for c in self.chunks:
            yield c
        if self.fail:
            raise self.fail

    def close(self):
        self.closed = True


def test_get_json_with_a_reader_streams_through_the_shared_holds(tmp_path, monkeypatch):
    import requests
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    body = flood(games=2, standard=1, alt=4)
    raw = json.dumps(body).encode()
    made = []

    def get_stream(url, **kw):
        made.append(_Resp([raw[i:i + 50] for i in range(0, len(raw), 50)]))
        return made[-1]
    monkeypatch.setattr(net, 'get_stream', get_stream)
    url = 'https://partner-api.prizepicks.com/projections'
    got = sources.get_json('soccer', url, params={'league_id': 82}, reader=board.pp_reader(2, NOW))
    assert got[board.PP_INFO] == {'seen': 10, 'alt_seen': 8, 'alt_kept': 4, 'alt_games': 2} and made[0].closed
    with pytest.raises(ValueError, match='too large'):
        sources.get_json_stream('soccer', url, board.pp_reader(2, NOW), max_bytes=100)
    assert made[1].closed
    monkeypatch.setattr(net, 'get_stream', lambda url, **kw: _Resp([raw[:200]], requests.ConnectionError('reset')))
    with pytest.raises(net.HttpError):
        sources.get_json('soccer', url, reader=board.pp_reader(2, NOW))
    monkeypatch.setattr(net, 'get_stream', lambda url, **kw: _Resp([raw[:200]]))
    with pytest.raises(ValueError):                                  # a body that simply ends: never a partial answer
        sources.get_json('soccer', url, reader=board.pp_reader(2, NOW))


# --------------------------------------------------------------------------------------------- the scheduler
def test_a_failing_quotes_job_is_started_again_inside_its_rows_300_s(monkeypatch):
    monkeypatch.setenv('SPORTS_API', '1')
    monkeypatch.setattr(scheduler, 'JOBS', {})
    clock = {'t': 1000.0}
    spec = dict(argv=['x'], every_s=45, timeout_s=90, flag='SPORTS_API', cap_mb=80, cls=scheduler.LIGHT, prio=2)
    sch = scheduler.Scheduler(jobs={'quotes': dict(spec, fail_wait_max_s=120), 'plain': dict(spec)}, budget=400,
                              now=lambda: clock['t'])
    waits = {'quotes': [], 'plain': []}
    for _ in range(6):
        for name in waits:
            sch._finish(name, 'killed_over_cap (146 > 144 MB)', 3.0)
            waits[name].append(scheduler.JOBS[name]['next_start'] - clock['t'])
    assert waits['plain'][3] == 720 and waits['plain'][5] == 720     # the plain back-off: 12 min from the 4th failure
    assert waits['quotes'][0] == 90 and all(w <= 120 for w in waits['quotes'])
    sch._finish('quotes', 0, 3.0)
    assert scheduler.JOBS['quotes']['failures_in_a_row'] == 0 and scheduler.JOBS['quotes']['next_start'] - clock['t'] < 50
    for sport in ('soccer', 'tennis'):
        monkeypatch.setenv(f'SCOUT_{sport.upper()}_ENABLED', '1')
    jobs = scheduler.default_jobs()
    assert {k for k, v in jobs.items() if v.get('fail_wait_max_s')} == {'soccer-quotes'}     # tennis: plain back-off
    # soccer (every 45 s): a failed run + the longest wait still lands inside the rows' 300 s
    assert jobs['soccer-quotes']['every_s'] + jobs['soccer-quotes']['fail_wait_max_s'] < board.MAX_AGE
