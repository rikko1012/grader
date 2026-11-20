"""PrizePicks soccer per-league tabs (ppsoccer-1119): the member report "grader is not picking up these soccer games
... they do them by league" (Liga MX, MLS, Serie A tabs). Tab 82 stays byte-identical; the per-league tabs are read by
numeric league id, pinned to one competition, bounded and rate-limit safe; a failed read never invents or moves a line."""
import json
import sqlite3
from collections import Counter
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
              'SPORTS_JOB_SOCCER_QUOTES_EVERY_S', 'SCOUT_SOCCER_PP_BACKOFF_S'):
        monkeypatch.delenv(k, raising=False)
    clock = Clock()
    monkeypatch.setattr(board, 'now_utc', clock.iso)
    monkeypatch.setattr(expansion_odds, 'collect', lambda sport, get, directory: ([], {}))
    state = {'calls': [], 'fail': {}, 'tabs': tabs(), 'listed': dict(LISTED)}

    def get_json(sport, url, params=None, **kw):
        lid = (params or {}).get('league_id', 'leagues')
        state['calls'].append(lid)
        clock.t += timedelta(seconds=2)
        f = state['fail'].get(lid)
        if f == 429:
            raise net.HttpError(429, url, '', not_before=None)
        if f == 'empty':
            return {'data': [], 'included': []}
        if lid == 'leagues':
            return leagues_body(state['listed'])
        return state['tabs'].get(lid, {'data': [], 'included': []})
    monkeypatch.setattr(sources, 'get_json', get_json)
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


def run(env, n=1, step=90):
    """n quotes runs `step` seconds apart -> [(calls, snapshot)]."""
    out = []
    for _ in range(n):
        env['calls'].clear()
        snap = board.collect('soccer')
        out.append((list(env['calls']), snap))
        env['clock'].t += timedelta(seconds=step)
    return out


def status(env):
    return json.loads((env['dir'] / board.PP_STATUS).read_text(encoding='utf-8'))


def visible_late(snap, env, before_next=85):
    """Per-source counts a member still sees just before the next run (the 300 s rule at read time)."""
    late = datetime.fromisoformat(snap['built_at'].replace('Z', '+00:00')) + timedelta(seconds=before_next)
    seen = Counter()
    for r in snap['offers']:
        if (late - datetime.fromisoformat(r['observed_at'].replace('Z', '+00:00'))).total_seconds() <= 300:
            seen[r.get('pp_league') or 'SOCCER'] += 1
    return seen


def test_one_request_a_run_learns_the_tabs_then_covers_82_and_the_best_tab(env):
    runs = run(env, 6)
    assert [c for c, _ in runs] == [[82, 'leagues'],        # tab 82, then the hourly league list (housekeeping)
                                    [6, 14],                # a free coverage slot learns a tab; discovery reads EPL
                                    [82, 534],              # discovery: Serie A
                                    [536],                  # Serie A (gradeable, ita.1) holds the slot; both fresh
                                    [82],                   # then tab 82 and Serie A alternate, each every 180 s
                                    [534]]
    assert all(len(c) <= 2 for c, _ in runs)                # never a burst of three
    st = status(env)
    assert st['covered'] == [82, 534] and st['slots'] == 2 and st['every_s'] == 90
    assert sorted(st['not_covered']) == [6, 536]            # standard lines outside coverage: ops says so
    snap = runs[-1][1]
    assert snap_ids(snap) == ['534a', '82a', '82b']         # covered tabs only; per-league rows standard only
    assert next(r for r in snap['offers'] if r['offer_id'] == '82a').get('pp_league') is None   # tab 82 wins the id
    assert set(snap['sources']) == {'prizepicks'} and snap['sources']['prizepicks']['ok'] is True


def test_steady_state_tab_82_never_dark_and_covered_tabs_always_visible(env):
    runs = run(env, 4)
    for calls, snap in run(env, 12):
        assert len(calls) <= 2
        seen = visible_late(snap, env)
        assert seen['SOCCER'] == 2 and seen['Serie A'] == 1   # just before the next run: still on the board
        assert snap['sources']['prizepicks']['ok'] is True


def test_faster_cadence_widens_coverage(env, monkeypatch):
    monkeypatch.setenv('SPORTS_JOB_SOCCER_QUOTES_EVERY_S', '60')
    assert board.coverage_slots(board.quotes_every_s()) == 4
    run(env, 8, step=60)
    for calls, snap in run(env, 8, step=60):
        seen = visible_late(snap, env, before_next=55)
        assert (seen['SOCCER'], seen['Serie A'], seen['MLS'], seen['Liga MX']) == (2, 1, 1, 1)
    assert status(env)['not_covered'] == []


def test_single_429_honours_retry_after_and_a_second_backs_off_to_tab_82_at_the_old_rate(env, monkeypatch):
    run(env, 5)
    before = {r['offer_id']: r for r in json.loads((env['dir'] / 'board.json').read_text(encoding='utf-8'))['offers']}
    env['fail'] = {534: 429, 82: 429}
    calls, snap = run(env, 1)[0]
    assert len(calls) == 1 and status(env)['stopped'] == 'http_429' and not status(env)['backoff_until']
    rows = {r['offer_id']: r for r in snap['offers']}
    assert all(rows[k] == before[k] for k in rows)           # carried, observed_at never re-stamped
    calls, _ = run(env, 1)[0]
    assert status(env)['backoff_until']                      # a second 429 inside 15 min: back-off
    env['fail'] = {}
    backoff = run(env, 6)                                    # 6 x 90 s < 600 s
    assert all(c in ([82], []) for c, _ in backoff) and [c for c, _ in backoff].count([82]) == 3   # 82 only, every 180 s
    assert snap_ids(backoff[-1][1]) == ['82a', '82b']        # Serie A lines aged out honestly (300 s)
    after = run(env, 2)
    assert any(534 in c for c, _ in after) and '534a' in snap_ids(after[-1][1])   # coverage again after 600 s


def test_shared_hold_means_no_request_at_all(env, monkeypatch):
    run(env, 3)
    monkeypatch.setattr(sources, 'held_until', lambda sport, url: 10 ** 12)
    calls, snap = run(env, 1)[0]
    assert calls == [] and snap['offers'] and status(env)['stopped'] == 'retry_after_hold'


def test_empty_answer_from_a_listed_tab_is_a_failed_read_and_rereads_the_list_at_most_every_ten_minutes(env):
    run(env, 6)
    env['fail'] = {534: 'empty'}
    runs = run(env, 8)
    st = json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))
    assert st['per']['534']['error'] == 'empty_listed_tab'
    lists = sum(c.count('leagues') for c, _ in runs)
    assert 1 <= lists <= 2                                    # 8 runs = 12 min: one or two forced list reads


def test_no_league_list_tries_mapped_tabs_and_retries_the_list(env):
    env['fail'] = {'leagues': 429}
    calls, _ = run(env, 1)[0]
    assert calls == [82, 'leagues']
    st = json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))
    assert 'listing' not in st['leagues'] and st['leagues']['error'] == 'HttpError'
    env['fail'] = {}
    calls, _ = run(env, 1)[0]
    assert calls[0] in offers.PP_SOCCER_LEAGUES and calls[1:] == ['leagues']


def test_books_keep_their_180_s_cadence_and_carry_between_reads(env, monkeypatch):
    from sports import expansion_odds
    n = {'reads': 0}
    def odds(sport, get, directory):
        n['reads'] += 1
        return ([{'sport': 'soccer', 'platform': 'betrivers', 'offer_id': f'o{n["reads"]}', 'revision': 'r',
                  'start': START, 'observed_at': env['clock'].iso()}],
                {'betrivers': {'ok': True, 'checked_at': env['clock'].iso(), 'markets': 1}})
    monkeypatch.setattr(expansion_odds, 'collect', odds)
    runs = run(env, 6)
    assert n['reads'] == 3                                    # 6 runs x 90 s: books every second run
    assert [s['game_odds'][0]['offer_id'] for _, s in runs] == ['o1', 'o1', 'o2', 'o2', 'o3', 'o3']
    assert runs[1][1]['sources']['betrivers'] == runs[0][1]['sources']['betrivers']   # carried, not re-stamped


def test_kill_switch_is_the_old_single_request_every_180_s(env, monkeypatch):
    from sports.scheduler import default_jobs
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    assert default_jobs()['soccer-quotes']['every_s'] == 90 and default_jobs()['tennis-quotes']['every_s'] == 180
    assert default_jobs()['soccer-quotes']['timeout_s'] == 90
    monkeypatch.setenv('SCOUT_SOCCER_PP_LEAGUES', '0')
    assert default_jobs()['soccer-quotes']['every_s'] == 180
    snap = board.collect('soccer')
    assert env['calls'] == [82] and set(snap['sources']) == {'prizepicks'}
    assert snap_ids(snap) == ['82a', '82b']
    assert not (env['dir'] / board.PP_STATE).exists() and not (env['dir'] / 'odds-carry.json').exists()
    assert sources.pace_gap_s(sources.PP_HOST) == 0
    env['calls'].clear(); monkeypatch.setenv('SCOUT_SOCCER_PP_MLS', '1')
    snap = board.collect('soccer')
    assert env['calls'] == [82, 6] and set(snap['sources']) == {'prizepicks', 'prizepicks_mls'}


def test_shared_pace_reserves_slots_across_processes(tmp_path, monkeypatch):
    t = {'now': 1000.0, 'slept': []}
    monkeypatch.setattr(sources.time, 'time', lambda: t['now'])
    monkeypatch.setattr(sources.time, 'sleep', lambda s: t['slept'].append(round(s, 3)))
    monkeypatch.delenv('SCOUT_SOCCER_PP_LEAGUES', raising=False)
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


def test_a_broken_holds_db_never_stops_a_request(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('SCOUT_PP_GAP_S', '3')
    def broken(con, host):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(sources, '_pace', broken)
    assert sources._held('soccer', board.PP_URL, lambda: 'sent') == 'sent'


def test_held_until_reads_the_shared_hold(tmp_path, monkeypatch):
    import time
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    assert sources.held_until('soccer', board.PP_URL) is None
    with sqlite3.connect(tmp_path / 'request-holds.db') as con:
        con.execute('INSERT INTO holds VALUES (?, ?)', (sources.PP_HOST, time.time() + 50))
    assert sources.held_until('soccer', board.PP_URL) > time.time()
    assert sources.held_until('tennis', board.PP_URL) > time.time()   # one file for both new-sport jobs


def test_ops_reads_the_status_file_alerts_and_the_kill_switch_silences_them(env, monkeypatch):
    from sports import service
    from sports.tests.test_service import _ops
    run(env, 3)
    st = service.expansion_pp_soccer()
    assert st['mode'] == 'leagues' and {x['id'] for x in st['leagues']} == set(offers.PP_SOCCER_LEAGUES)
    assert st['kill_switch_off'] is False
    for lg in st['leagues']:
        if lg['id'] == 82:
            lg['last_ok_at'] = '2000-01-01T00:00:00Z'
    codes = [a['code'] for a in service.alerts(_ops(expansion_pp_soccer=st))]
    assert 'pp_soccer_tab_stale' in codes and 'pp_soccer_tab_unmapped' in codes
    st['kill_switch_off'] = True
    assert not [c for c in (a['code'] for a in service.alerts(_ops(expansion_pp_soccer=st))) if c.startswith('pp_soccer_tab')]
    monkeypatch.delenv('SCOUT_SOCCER_ENABLED')
    assert service.expansion_pp_soccer() is None


def test_a_covered_tab_failing_without_429_never_starves_tab_82(env):
    run(env, 6)
    env['fail'] = {534: 'empty'}                             # 200 with no data from a listed tab: a failed read
    for calls, snap in run(env, 8):
        assert calls.count(534) <= 1 and len(calls) <= 2
        assert visible_late(snap, env)['SOCCER'] == 2         # tab 82 lines on the board before every next run
    env['fail'] = {}


def test_tab_82_failing_keeps_the_covered_tab_visible(env):
    run(env, 6)
    env['fail'] = {82: 'empty'}
    for calls, snap in run(env, 8):
        assert visible_late(snap, env)['Serie A'] >= 1          # (its own copy of 82a joins once tab 82's is gone)


def test_a_run_killed_mid_read_still_moves_the_rotation_on(env, monkeypatch):
    run(env, 6)
    nxt = sorted(board._read_json(env['dir'] / board.PP_STATE, {})['per'].items(),
                 key=lambda kv: kv[1].get('attempt_at') or '')
    real = sources.get_json
    def killed(sport, url, params=None, **kw):
        raise SystemExit('killed over cap')                   # the scheduler's SIGKILL, as close as a test gets
    monkeypatch.setattr(sources, 'get_json', killed)
    with pytest.raises(SystemExit):
        board.collect('soccer')
    first = json.loads((env['dir'] / board.PP_STATE).read_text(encoding='utf-8'))
    monkeypatch.setattr(sources, 'get_json', real)
    env['clock'].t += timedelta(seconds=90)
    calls, _ = run(env, 1)[0]
    picked_before = max(first['per'].items(), key=lambda kv: kv[1].get('attempt_at') or '')[0]
    assert calls and str(calls[0]) != picked_before            # the next run asks the OTHER covered source


def test_tab_82_alone_in_coverage_keeps_its_old_180_s_rate(env):
    env['tabs'] = {82: env['tabs'][82]}
    env['listed'] = {82: LISTED[82]}
    runs = run(env, 9)
    assert sum(c.count(82) for c, _ in runs) == 5            # 9 runs x 90 s: every second run, not every run
    for _, snap in runs:
        assert visible_late(snap, env)['SOCCER'] == 2

