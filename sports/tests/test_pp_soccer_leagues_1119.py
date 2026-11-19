"""PrizePicks soccer per-league tabs (ppsoccer-1119): the member report "grader is not picking up these soccer games
... they do them by league" (Liga MX, MLS, Serie A tabs). Tab 82 stays byte-identical; the per-league tabs are read by
numeric league id, pinned to one competition, bounded and rate-limit safe; a failed read never invents or moves a line."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sports import expansion_board as board, expansion_offers as offers, expansion_history as history
from sports import expansion_sources as sources, net

NOW = '2026-11-19T16:00:00Z'
START = '2030-01-01T20:00:00Z'


def pp_tab(lid, name, rows, teams=('San Luis', 'Puebla'), full=(('Atlético San Luis', 'Atlético de San Luis'),
                                                                     ('Puebla', 'Club Puebla')), start=START):
    """A PrizePicks /projections answer for one tab. rows: [(projection id, player id, stat, line, odds_type)]."""
    data, inc, players = [], [], set()
    inc += [{'type': 'duration', 'id': 'd1', 'attributes': {'name': 'Full'}},
            {'type': 'game', 'id': f'g{lid}', 'attributes': {'metadata': {'game_info': {'teams': {
                'away': {'abbreviation': teams[0]}, 'home': {'abbreviation': teams[1]}}}}},
             'relationships': {'away_team_data': {'data': {'type': 'team', 'id': f't{lid}a'}},
                               'home_team_data': {'data': {'type': 'team', 'id': f't{lid}h'}}}},
            {'type': 'team', 'id': f't{lid}a', 'attributes': {'abbreviation': teams[0], 'name': full[0][0], 'market': full[0][1]}},
            {'type': 'team', 'id': f't{lid}h', 'attributes': {'abbreviation': teams[1], 'name': full[1][0], 'market': full[1][1]}}]
    for pid, player, stat, line, odds in rows:
        data.append({'type': 'projection', 'id': pid, 'attributes': {
            'stat_type': stat, 'line_score': line, 'start_time': start, 'status': 'pre_game',
            'projection_type': 'Single Stat', 'odds_type': odds,
            'allowed_wager_types': 'under_or_over' if odds == 'standard' else 'over'},
            'relationships': {'new_player': {'data': {'type': 'new_player', 'id': player}},
                              'game': {'data': {'type': 'game', 'id': f'g{lid}'}},
                              'duration': {'data': {'type': 'duration', 'id': 'd1'}},
                              'league': {'data': {'type': 'league', 'id': str(lid)}}}})
        if player not in players:
            players.add(player)
            inc.append({'type': 'new_player', 'id': player, 'attributes': {
                'league': name, 'league_id': lid, 'name': f'Player {player}', 'team': teams[0], 'position': 'Midfielder'}})
    return {'data': data, 'included': inc}


def leagues_body(counts):
    rows = []
    for lid, (name, count, icon, parent) in counts.items():
        rows.append({'type': 'league', 'id': str(lid), 'attributes': {
            'name': name, 'display_name': name, 'projections_count': count, 'icon': icon, 'parent_id': parent,
            'active': True}})
    return {'data': rows}


LISTED = {82: ('SOCCER', 10, 'soccer', None), 14: ('EPL', 50, 'soccer', None), 6: ('MLS', 9, 'soccer', None),
          536: ('Liga MX', 30, 'soccer', None), 534: ('Serie A', 20, 'soccer', None), 531: ('La Liga', 0, 'soccer', None),
          545: ('Liga MX 1H', 4, 'soccer', 536), 262: ('SOCCERSZN', 7, 'soccer', None),
          284: ('HANDBALL', 12, 'soccer', None), 7: ('NBA', 400, 'basketball', None)}


def tabs():
    return {82: pp_tab(82, 'SOCCER', [('82a', 'p1', 'Shots', 2.5, 'standard'), ('82b', 'p1', 'Shots', 4.5, 'demon')],
                       teams=('Sao Paulo', 'Vitoria'), full=(('São Paulo', 'São Paulo FC'), ('Vitória', 'EC Vitória'))),
            14: pp_tab(14, 'EPL', [('14a', 'p2', 'Shots', 1.5, 'demon')], teams=('Arsenal', 'Spurs'),
                       full=(('Arsenal', 'Arsenal FC'), ('Tottenham', 'Tottenham Hotspur'))),
            6: pp_tab(6, 'MLS', [('6a', 'p3', 'Passes Attempted', 42.5, 'standard')], teams=('Cincinnati', 'Orlando'),
                      full=(('Cincinnati', 'FC Cincinnati'), ('Orlando City', 'Orlando City SC'))),
            536: pp_tab(536, 'Liga MX', [('536a', 'p4', 'Passes Attempted', 65.5, 'standard'),
                                         ('536b', 'p4', 'Passes Attempted', 70.5, 'goblin')]),
            534: pp_tab(534, 'Serie A', [('534a', 'p5', 'Passes Attempted', 69.5, 'standard'),
                                         ('82a', 'p5', 'Shots', 2.5, 'standard')],    # also listed on tab 82
                        teams=('Roma', 'Parma'), full=(('Roma', 'AS Roma'), ('Parma', 'Parma Calcio 1913')))}


class Clock:
    def __init__(self, at=NOW):
        self.t = datetime.fromisoformat(at.replace('Z', '+00:00'))

    def iso(self):
        return self.t.strftime('%Y-%m-%dT%H:%M:%SZ')

    def mono(self):
        return self.t.timestamp()


@pytest.fixture
def env(monkeypatch, tmp_path):
    from sports import expansion_odds
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    for k in ('SCOUT_SOCCER_PP_LEAGUES', 'SCOUT_SOCCER_PP_MLS', 'SCOUT_SOCCER_UNDERDOG', 'SCOUT_SOCCER_PP_MAX_CALLS',
              'SCOUT_SOCCER_PP_BUDGET_S'):
        monkeypatch.delenv(k, raising=False)
    clock = Clock()
    monkeypatch.setattr(board, 'now_utc', clock.iso)
    monkeypatch.setattr(expansion_odds, 'collect', lambda sport, get, directory: ([], {}))
    state = {'calls': [], 'fail': {}, 'tabs': tabs(), 'listed': dict(LISTED)}

    def get_json(sport, url, params=None, **kw):
        lid = (params or {}).get('league_id', 'leagues')
        state['calls'].append(lid)
        clock.t += timedelta(seconds=3)
        f = state['fail'].get(lid)
        if f == 429:
            raise net.HttpError(429, url, '', not_before=None)
        if f == 'empty':
            return {'data': [], 'included': []}
        if lid == 'leagues':
            return leagues_body(state['listed'])
        return state['tabs'].get(lid, {'data': [], 'included': []})
    monkeypatch.setattr(sources, 'get_json', get_json)
    monkeypatch.setattr(board.time if hasattr(board, 'time') else __import__('time'), 'monotonic', clock.mono)
    state['clock'], state['dir'] = clock, tmp_path / 'soccer'
    return state


def snap_ids(snap):
    return sorted(r['offer_id'] for r in snap['offers'])


def test_tab_82_reader_is_unchanged_and_per_league_reader_goes_by_numeric_id():
    body = tabs()[536]
    assert offers.prizepicks(body, 'soccer', NOW) == []                # "Liga MX" was never in the name filter
    rows = offers.prizepicks(body, 'soccer', NOW, 536)
    assert [r['offer_id'] for r in rows] == ['536a', '536b']
    r = rows[0]
    assert (r['pp_league'], r['competition_code']) == ('Liga MX', 'mex.1')
    assert r['team_aliases'] == [['Atlético San Luis', 'Atlético de San Luis'], ['Puebla', 'Club Puebla']]
    legacy = offers.prizepicks(tabs()[82], 'soccer', NOW)
    assert all(k not in legacy[0] for k in ('pp_league', 'competition_code', 'team_aliases'))
    other = json.loads(json.dumps(body))
    for x in other['included']:
        if x['type'] == 'new_player':
            x['attributes']['league_id'] = 545                         # a 1H child tab's player: not this tab
    assert offers.prizepicks(other, 'soccer', NOW, 536) == []
    for bad in (82, 545, 284, 9999):
        with pytest.raises(ValueError):
            offers.prizepicks(body, 'soccer', NOW, bad)
    with pytest.raises(ValueError):
        offers.prizepicks(pp_tab(5, 'TENNIS', []), 'tennis', NOW, 536)


def test_full_team_names_only_when_the_team_objects_are_the_same_two_teams():
    body = tabs()[536]
    for x in body['included']:
        if x['type'] == 'team' and x['id'].endswith('a'):
            x['attributes']['abbreviation'] = 'Someone else'
    assert offers.prizepicks(body, 'soccer', NOW, 536)[0]['team_aliases'] == [[], []]


def match(competition, event, start=START):
    return {'event_id': f'soccer:espn:{competition}:{event}:{event}', 'competition': competition, 'start': start,
            'participants': [{'id': 'soccer:team:espn:1', 'name': 'Atlético de San Luis', 'abbreviation': 'ASL',
                              'short_name': 'Atl. San Luis'},
                             {'id': 'soccer:team:espn:2', 'name': 'Puebla', 'abbreviation': 'PUE', 'short_name': 'Puebla'}],
            'players': [{'id': 'soccer:player:espn:9', 'name': 'Player p4', 'team_id': 'soccer:team:espn:1'}]}


def test_competition_pin_and_exact_full_names_resolve_san_luis_and_nothing_else():
    offer = offers.prizepicks(tabs()[536], 'soccer', NOW, 536)[0]
    assert history.resolve(offer, [match('mex.1', 1)])['competition'] == 'mex.1'      # "San Luis" alone never matched
    assert history.resolve(offer, [match('mex.2', 1)]) is None                         # same names, other competition
    assert history.soccer_teams(offer, match('mex.2', 1)) == []
    assert history.resolve(offer, [match('mex.1', 1), match('mex.2', 2)])['competition'] == 'mex.1'
    plain = {k: v for k, v in offer.items() if k not in ('competition_code', 'team_aliases', 'pp_league')}
    assert history.resolve(plain, [match('mex.1', 1)]) is None                         # tab-82 style: unchanged rule


def test_plan_reads_only_exact_mapped_tabs_with_lines():
    listing = board.league_list(leagues_body(LISTED))
    use, unmapped, renamed = board.plan_leagues(listing)
    assert use == [6, 14, 534, 536]                                    # La Liga lists 0; 1H child, SOCCERSZN out
    assert unmapped == [{'id': 284, 'name': 'HANDBALL', 'count': 12}]  # "soccer" icon, never read, listed for ops
    assert renamed == []
    listing['536']['name'] = 'Liga BBVA MX'
    use, _, renamed = board.plan_leagues(listing)
    assert 536 not in use and renamed == [{'id': 536, 'expected': 'Liga MX', 'listed': 'Liga BBVA MX'}]
    assert board.plan_leagues(None)[0] == sorted(k for k in offers.PP_SOCCER_LEAGUES if k != 82)


def test_default_run_reads_every_soccer_tab_bounded_and_standard_only(env):
    snap = board.collect('soccer')
    # 82 first, the league list, then never-read mapped tabs in table order up to 5 PrizePicks requests a run
    assert env['calls'] == [82, 'leagues', 14, 6, 536]
    assert set(snap['sources']) == {'prizepicks'}                     # one member-facing key, as before
    assert snap_ids(snap) == ['536a', '6a', '82a', '82b']             # per-league tabs: standard only; 82 unchanged
    status = json.loads((env['dir'] / board.PP_STATUS).read_text(encoding='utf-8'))
    assert status['stopped'] == 'call_cap' and status['calls'] == 5
    by = {x['id']: x for x in status['leagues']}
    assert by[536]['on_board'] == 1 and by[536]['parsed'] == 2 and by[536]['kept'] == 1
    assert by[14]['poll'] == 'slow' and by[534]['read_this_run'] is False
    assert status['unmapped_with_lines'] == [{'id': 284, 'name': 'HANDBALL', 'count': 12}]
    assert snap['sources']['prizepicks']['ok'] is False                # Serie A not read yet: honest "as of"
    env['calls'].clear()
    snap = board.collect('soccer')
    # EPL had no standard line: slow poll; Serie A (never read) goes first; no /leagues inside its hour
    assert env['calls'] == [82, 534, 6, 536]
    assert snap_ids(snap) == ['534a', '536a', '6a', '82a', '82b']      # 82a listed on 534 too: tab 82's row kept
    assert next(r for r in snap['offers'] if r['offer_id'] == '82a').get('pp_league') is None
    assert snap['sources']['prizepicks']['ok'] is True


def test_kill_switch_is_exactly_the_old_single_request(env, monkeypatch):
    monkeypatch.setenv('SCOUT_SOCCER_PP_LEAGUES', '0')
    snap = board.collect('soccer')
    assert env['calls'] == [82] and set(snap['sources']) == {'prizepicks'}
    assert snap_ids(snap) == ['82a', '82b']
    assert not (env['dir'] / board.PP_STATE).exists()
    env['calls'].clear(); monkeypatch.setenv('SCOUT_SOCCER_PP_MLS', '1')
    snap = board.collect('soccer')
    assert env['calls'] == [82, 6] and set(snap['sources']) == {'prizepicks', 'prizepicks_mls'}


def test_429_stops_every_further_prizepicks_request_and_keeps_last_good_rows_until_they_age_out(env):
    board.collect('soccer'); board.collect('soccer')
    before = {r['offer_id']: r for r in json.loads((env['dir'] / 'board.json').read_text(encoding='utf-8'))['offers']}
    env['calls'].clear(); env['fail'][534] = 429
    env['clock'].t += timedelta(seconds=120)
    snap = board.collect('soccer')
    assert env['calls'] == [82, 534]                                   # stopped at the 429: no storm, no 6 / 536 read
    rows = {r['offer_id']: r for r in snap['offers']}
    assert set(rows) == set(before)                                    # carried, never dropped on a blip
    for k in ('534a', '6a', '536a'):
        assert rows[k] == before[k]                                    # observed_at / revision never re-stamped
    assert snap['sources']['prizepicks']['ok'] is False
    status = json.loads((env['dir'] / board.PP_STATUS).read_text(encoding='utf-8'))
    assert status['stopped'] == 'http_429'
    env['calls'].clear(); env['fail'] = {82: 429}
    env['clock'].t += timedelta(seconds=200)                           # > 300 s since 534 / 6 / 536 were read
    snap = board.collect('soccer')
    assert env['calls'] == [82]
    assert snap_ids(snap) == ['82a', '82b']                            # 82 read < 300 s ago: still carried
    env['calls'].clear()
    env['clock'].t += timedelta(seconds=200)
    snap = board.collect('soccer')
    assert snap['offers'] == []                                        # nothing older than the 300 s rule survives


def test_steady_state_no_tab_flickers_and_shared_hold_skips_without_waiting(env, monkeypatch):
    seen = []
    for _ in range(8):
        snap = board.collect('soccer')
        env['clock'].t += timedelta(seconds=165)
        seen.append(snap_ids(snap))
    assert all(ids == ['534a', '536a', '6a', '82a', '82b'] for ids in seen[1:])
    monkeypatch.setattr(sources, 'held_until', lambda sport, url: 10 ** 12)
    env['calls'].clear()
    snap = board.collect('soccer')
    assert env['calls'] == [] and snap['offers']                       # held: no request at all, rows still carried
    assert json.loads((env['dir'] / board.PP_STATUS).read_text(encoding='utf-8'))['stopped'] == 'retry_after_hold'


def test_empty_answer_from_a_listed_tab_is_a_failed_read_and_rereads_the_list(env):
    board.collect('soccer'); board.collect('soccer')
    env['calls'].clear(); env['fail'][536] = 'empty'
    snap = board.collect('soccer')
    assert '536a' in snap_ids(snap)                                    # carried, not emptied by a 200 with data: []
    state = json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))
    assert state['per']['536']['error'] == 'empty_listed_tab' and state['leagues']['refresh'] is True
    env['calls'].clear()
    board.collect('soccer')
    assert env['calls'][:2] == [82, 'leagues']


def test_time_budget_and_call_cap_env(env, monkeypatch):
    monkeypatch.setenv('SCOUT_SOCCER_PP_MAX_CALLS', '2')
    board.collect('soccer')
    assert env['calls'] == [82, 'leagues']
    env['calls'].clear(); monkeypatch.setenv('SCOUT_SOCCER_PP_MAX_CALLS', '9')
    monkeypatch.setenv('SCOUT_SOCCER_PP_BUDGET_S', '7')
    board.collect('soccer')
    assert env['calls'] == [82, 14, 6]                                 # 3 s a request: none started at/after 7 s


def test_no_league_list_tries_every_mapped_tab_within_the_cap(env):
    env['fail']['leagues'] = 429
    board.collect('soccer')
    assert env['calls'] == [82, 'leagues']                             # the 429 on /leagues stops the run
    st = json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))
    assert 'listing' not in st['leagues'] and st['leagues']['error'] == 'HttpError'
    env['calls'].clear(); env['fail'] = {'leagues': 429}
    env['clock'].t += timedelta(seconds=70)
    snap = board.collect('soccer')
    assert env['calls'] == [82]                                        # 429 back-off: tab 82 only, as at 5036ce4
    env['clock'].t += timedelta(seconds=600)
    env['calls'].clear()
    snap = board.collect('soccer')
    assert env['calls'] == [82, 'leagues']                             # retried after the back-off while it has no list
    env['clock'].t += timedelta(seconds=600)
    env['calls'].clear(); env['fail'] = {}
    board.collect('soccer')
    assert env['calls'] == [82, 'leagues', 14, 6, 536]


def test_untrusted_old_list_falls_back_to_every_mapped_tab(env, monkeypatch):
    board.collect('soccer')
    st_path = env['dir'] / board.PP_STATE
    st = json.loads(st_path.read_text(encoding='utf-8'))
    st['leagues']['fetched_at'] = '2026-11-01T00:00:00Z'               # older than a day
    st_path.write_text(json.dumps(st), encoding='utf-8')
    env['calls'].clear(); env['fail'] = {'leagues': 429}
    board.collect('soccer')
    assert env['calls'] == [82, 'leagues']
    env['calls'].clear(); env['fail'] = {}
    env['clock'].t += timedelta(seconds=601)                           # past the 429 back-off
    monkeypatch.setattr(board, 'LEAGUES_TTL_S', 10 ** 9)
    monkeypatch.setattr(board, 'LEAGUES_MAX_AGE_S', 1)
    board.collect('soccer')
    # no trusted list: mapped tabs never read OK first (Serie A, then the empty-today La Liga / Bundesliga ...)
    assert env['calls'][0] == 82 and env['calls'][1:] == [534, 531, 533, 535]


def test_shared_pace_reserves_slots_across_processes(tmp_path, monkeypatch):
    t = {'now': 1000.0, 'slept': []}
    monkeypatch.setattr(sources.time, 'time', lambda: t['now'])
    monkeypatch.setattr(sources.time, 'sleep', lambda s: t['slept'].append(round(s, 3)))
    monkeypatch.setenv('SCOUT_PP_GAP_S', '8')
    db = tmp_path / 'h.db'
    for _ in range(3):
        con = sqlite3.connect(db, isolation_level=None)
        sources._pace(con, sources.PP_HOST)
        con.close()
    assert t['slept'] == [8.0, 16.0]                                   # queued reservations: +8 s, +16 s
    with sqlite3.connect(db) as c:
        c.execute('UPDATE pace SET at=?', (t['now'] + 10 ** 6,))       # a slot far in the future (clock jump)
    con = sqlite3.connect(db, isolation_level=None)
    sources._pace(con, sources.PP_HOST)
    sources._pace(con, 'api.underdogfantasy.com')                      # other hosts: no shared pace
    con.close()
    assert t['slept'] == [8.0, 16.0]                                   # never parked behind a bogus slot
    monkeypatch.setenv('SCOUT_PP_GAP_S', '0')
    assert sources.pace_gap_s(sources.PP_HOST) == 0


def test_held_until_reads_the_shared_hold(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    assert sources.held_until('soccer', board.PP_URL) is None
    with sqlite3.connect(tmp_path / 'request-holds.db') as con:
        con.execute('INSERT INTO holds VALUES (?, ?)', (sources.PP_HOST, time.time() + 50))
    assert sources.held_until('soccer', board.PP_URL) > time.time()
    assert sources.held_until('tennis', board.PP_URL) > time.time()   # one file for both new-sport jobs


def test_ops_reads_the_status_file_and_alerts(env, monkeypatch):
    from sports import service
    board.collect('soccer')
    st = service.expansion_pp_soccer()
    assert st['mode'] == 'leagues' and {x['id'] for x in st['leagues']} == set(offers.PP_SOCCER_LEAGUES)
    assert st['kill_switch_off'] is False
    for lg in st['leagues']:
        if lg['id'] == 6:
            lg['last_ok_at'] = '2000-01-01T00:00:00Z'
    from sports.tests.test_service import _ops
    o = _ops(expansion_pp_soccer=st)
    codes = [a['code'] for a in service.alerts(o)]
    assert 'pp_soccer_tab_stale' in codes and 'pp_soccer_tab_unmapped' in codes
    monkeypatch.delenv('SCOUT_SOCCER_ENABLED')
    assert service.expansion_pp_soccer() is None


def test_429_backs_the_extra_tabs_off_for_ten_minutes_but_tab_82_keeps_its_one_request(env):
    board.collect('soccer')
    env['calls'].clear(); env['fail'] = {6: 429}
    env['clock'].t += timedelta(seconds=180)
    board.collect('soccer')
    assert env['calls'] == [82, 534, 6]                                # stopped at the 429
    st = json.loads((env['dir'] / board.PP_STATUS).read_text(encoding='utf-8'))
    assert st['backoff_reason'] == 'http_429' and st['backoff_until']
    env['fail'] = {}
    for _ in range(3):                                                 # 3 x 180 s < 600 s: tab 82 only
        env['calls'].clear(); env['clock'].t += timedelta(seconds=180)
        snap = board.collect('soccer')
        assert env['calls'] == [82]
    assert json.loads((env['dir'] / board.PP_STATUS).read_text(encoding='utf-8'))['stopped'] == 'backoff'
    assert snap_ids(snap) == ['82a', '82b']                            # the tabs' rows aged out honestly (300 s)
    env['calls'].clear(); env['clock'].t += timedelta(seconds=180)
    snap = board.collect('soccer')
    assert env['calls'][0] == 82 and len(env['calls']) > 1             # back after 600 s
    assert '536a' in snap_ids(snap)

