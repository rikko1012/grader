"""Review fixes 2026-11-18: research kill switches never stop a graded input, the all-off job is 53dad0b's, and newer
competitions' roster refreshes only use the budget the 53dad0b competitions leave.

build_world / fake_transport / normalized_plan are stdlib-only on purpose: the ops differential harness
(cs2_scout_ops/logs/sources_1118/review2/killswitch_diff.py) loads this file by path and runs the SAME world against
the real 53dad0b tree. EXPECTED_53DAD0B_* below are that harness's 53dad0b plans (limit normalised, see below)."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path
import re

ALPHA, BETA, GAMMA, DELTA = ('soccer:team:espn:101', 'soccer:team:espn:102', 'soccer:team:espn:201',
                             'soccer:team:espn:202')


def _iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def _final(comp, n, start, teams, players=True):
    m = {'event_id': f'soccer:espn:{comp}:{n}:{n}', 'competition': comp, 'start': _iso(start), 'status': 'final',
         'scores': {teams[0][0]: 2.0, teams[1][0]: 1.0},
         'participants': [{'id': t, 'name': name, 'short_name': name.split()[0]} for t, name in teams]}
    if players:
        m['players'] = [{'id': f'soccer:player:espn:{n}1', 'name': 'Test Player', 'team_id': teams[0][0]}]
    return m


def build_world(data, now=None):
    """A soccer + tennis store: due base summaries, offered base and newer-competition fixtures (rosters due), a
    FotMob-detailable final in a base league and one in a newer league, a theScore final with research stats and a
    pre-match listing. Returns the world description the fake transport serves."""
    now = now or datetime.now(timezone.utc)
    data = Path(data)
    base_teams = [(ALPHA, 'Alpha FC'), (BETA, 'Beta FC')]
    new_teams = [(GAMMA, 'Gamma FC'), (DELTA, 'Delta FC')]
    fot = now - timedelta(days=3)
    bra = {'fot': {**_final('bra.1', 1, fot, [('a', 'Santos'), ('b', 'Example')]),
                   'players': [{'id': 'player1', 'name': 'Test Player', 'team_id': 'a'}],
                   'summary_checked_at': _iso(now - timedelta(hours=1))},
           's1': {'event_id': 'soccer:espn:bra.1:900:900', 'competition': 'bra.1', 'start': _iso(now + timedelta(days=1)),
                  'status': 'scheduled', 'participants': [{'id': t, 'name': n, 'short_name': n.split()[0]}
                                                          for t, n in base_teams]}}
    for i in range(3):
        bra[f'f{i}'] = _final('bra.1', 10 + i, now - timedelta(days=1 + i), base_teams, players=False)
    eng2 = {'s2': {'event_id': 'soccer:espn:eng.2:950:950', 'competition': 'eng.2', 'start': _iso(now + timedelta(days=1)),
                   'status': 'scheduled', 'participants': [{'id': t, 'name': n, 'short_name': n.split()[0]}
                                                           for t, n in new_teams]},
            'fot2': {**_final('eng.2', 2, fot, [('c', 'Leeds'), ('d', 'Other')]),
                     'players': [{'id': 'player2', 'name': 'Second Player', 'team_id': 'c'}],
                     'summary_checked_at': _iso(now - timedelta(hours=1))}}
    for i in range(2):
        eng2[f'f{i}'] = _final('eng.2', 20 + i, now - timedelta(days=1 + i), new_teams, players=False)
    _write(data / 'soccer' / 'bra.1' / 'matches.json', bra)
    _write(data / 'soccer' / 'eng.2' / 'matches.json', eng2)
    offers = [{'sport': 'soccer', 'teams': ['Alpha', 'Beta'], 'team': 'Alpha', 'player': 'Someone',
               'start': bra['s1']['start']},
              {'sport': 'soccer', 'teams': ['Gamma', 'Delta'], 'team': 'Gamma', 'player': 'Someone Else',
               'start': eng2['s2']['start']}]
    _write(data / 'soccer' / 'board.json', {'offers': offers})
    _write(data / 'soccer' / 'offered-competitions.json', {'eng.2': _iso(now)})
    # tennis: one ESPN final theScore can verify (names + start + set scores)
    t_start = now - timedelta(days=1)
    atp = {'t1': {'event_id': 'atp:espn:atp:5:6', 'competition': 'atp', 'start': _iso(t_start), 'status': 'final',
                  'participants': [{'id': 'tennis:player:espn:1', 'name': 'Player One'},
                                   {'id': 'tennis:player:espn:2', 'name': 'Player Two'}],
                  'set_scores': {'tennis:player:espn:1': [6, 6], 'tennis:player:espn:2': [3, 4]}}}
    _write(data / 'tennis' / 'atp' / 'matches.json', atp)
    _write(data / 'tennis' / 'board.json', {'offers': []})
    return {'now': now, 't_start': t_start, 'fot': bra['fot'], 'fot2': eng2['fot2']}


def _fotmob_detail(match, league_id, home, away, player, team_id):
    status = {'utcTime': match['start'], 'finished': True, 'reason': {'longKey': 'finished'}}
    values = {'minutes_played': {'value': 79}, 'accurate_passes': {'value': 41, 'total': 46},
              'expected_goals': {'value': .58}}
    return {'general': {'matchId': 44 if league_id == 268 else 45, 'leagueId': league_id, 'parentLeagueId': league_id,
                        'finished': True, 'countryCode': 'BRA' if league_id == 268 else 'ENG',
                        'leagueName': 'Série A' if league_id == 268 else 'Championship'},
            'header': {'status': status, 'teams': [{'id': team_id, 'name': home, 'score': 2},
                                                   {'id': team_id + 1, 'name': away, 'score': 1}]},
            'content': {'playerStats': {'1': {'name': player, 'teamId': team_id, 'stats': [
                {'stats': {k: {'key': k, 'stat': v} for k, v in values.items()}}]}}}}


def fake_transport(world, calls):
    """get_json(sport, url, params=..., **kw) for both trees; records (url, params) in call order."""
    now, t_start = world['now'], world['t_start']
    d1 = _fotmob_detail(world['fot'], 268, 'Santos', 'Example', 'Test Player', 11)
    d2 = _fotmob_detail(world['fot2'], 48, 'Leeds', 'Other', 'Second Player', 21)

    def player(first, last):
        return {'player1': {'first_name': first, 'last_name': last}}
    final = {'id': 555, 'status': 'final', 'league': {'slug': 'atp'}, 'match_date': format_datetime(t_start),
             'team_1': player('Player', 'One'), 'team_2': player('Player', 'Two'),
             'team_1_scores': [6, 6], 'team_2_scores': [3, 4],
             'team_1_stats': {'aces': 7, 'double_faults': 2, 'points_won': 60, 'total_breakpoints': 5,
                              'first_serve_successful': 64},
             'team_2_stats': {'aces': 3, 'double_faults': 4, 'points_won': 41, 'total_breakpoints': 2}}
    upcoming = {'id': 556, 'status': 'pre_game', 'league': {'slug': 'atp'},
                'match_date': format_datetime(now + timedelta(days=1)),
                'team_1': player('Player', 'Three'), 'team_2': player('Player', 'Four')}

    def get_json(sport, url, params=None, **kw):
        calls.append((url, dict(params or {})))
        if url.endswith('/scoreboard'):
            return {'events': []}
        tid = re.search(r'/teams/(\d+)/', url)
        if url.endswith('/roster'):
            return {'team': {'id': tid[1]}, 'athletes': [{'id': tid[1] + '9', 'displayName': 'Someone'}]}
        if url.endswith('/schedule'):
            return {'team': {'id': tid[1]}, 'events': []}
        if url.endswith('/summary'):
            comp = re.search(r'/soccer/([^/]+)/summary', url)[1]
            return {'header': {'league': {'slug': comp}, 'id': params['event']}}
        if 'fotmob.com/api/data/matches' in url:
            day = params['date']
            leagues = []
            for d, m, lid, cc, nm, mid in ((d1, world['fot'], 268, 'BRA', 'Série A', 44),
                                           (d2, world['fot2'], 48, 'ENG', 'Championship', 45)):
                if m['start'][:10].replace('-', '') == day:
                    leagues.append({'ccode': cc, 'name': nm, 'primaryId': lid, 'matches': [
                        {'id': mid, 'home': d['header']['teams'][0], 'away': d['header']['teams'][1],
                         'status': d['header']['status']}]})
            return {'leagues': leagues}
        if 'matchDetails' in url:
            return d1 if str(params.get('matchId')) == '44' else d2
        if url == 'https://api.thescore.com/atp/events':
            return [{'id': 77, 'league': {'slug': 'atp'}, 'match_type': 'Singles',
                     'start_date': format_datetime(now - timedelta(days=3)),
                     'end_date': format_datetime(now + timedelta(days=3))}]
        if url == 'https://api.thescore.com/wta/events':
            return []
        if url.endswith('/events/77/matches'):
            return [final, upcoming]
        if url.endswith('/matches/555'):
            return final
        if url.endswith('/matches/556'):
            return upcoming
        return {}
    return get_json


def normalized_plan(calls, now):
    """Dates -> day offsets; ESPN scoreboard 'limit' dropped (the one known difference: 1000 since 70ac109, 53dad0b
    sent 200 - a truncation fix on the same request, not an extra one)."""
    today = now.date()

    def day(s):
        return 'D%+d' % (datetime.strptime(s, '%Y%m%d').date() - today).days

    out = []
    for url, params in calls:
        params = {k: v for k, v in params.items() if not (k == 'limit' and url.endswith('/scoreboard'))}
        params = {k: re.sub(r'\d{8}', lambda m: day(m[0]), str(v)) for k, v in sorted(params.items())}
        out.append([url, params])
    return out


# Generated by review2/killswitch_diff.py from the real 53dad0b tree on build_world (the 365Scores challenger
# requests carry calendar dates and are compared there, not here).
EXPECTED_53DAD0B_SOCCER = [
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", {"dates": "D-1"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", {"dates": "D+0"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", {"dates": "D+1"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", {"dates": "D+2"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/all/scoreboard", {"dates": "D-1"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/teams/101/roster", {}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/teams/101/schedule", {}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/summary", {"event": "10"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/summary", {"event": "11"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/summary", {"event": "12"}],
    ["https://site.api.espn.com/apis/site/v2/sports/soccer/bra.1/summary", {"event": "900"}],
    ["https://www.fotmob.com/api/data/matches", {"date": "D-3"}],
    ["https://www.fotmob.com/api/data/matchDetails", {"matchId": "44"}],
]
EXPECTED_53DAD0B_TENNIS = [
    ["https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard", {"dates": "D-1-D+2"}],
    ["https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard", {"dates": "D-1-D+2"}],
    ["https://site.api.espn.com/apis/site/v2/sports/tennis/atp/scoreboard", {"dates": "D-7-D-1"}],
    ["https://site.api.espn.com/apis/site/v2/sports/tennis/wta/scoreboard", {"dates": "D-7-D-1"}],
    ["https://api.thescore.com/atp/events", {}],
    ["https://api.thescore.com/wta/events", {}],
    ["https://api.thescore.com/atp/events/77/matches", {}],
    ["https://api.thescore.com/atp/matches/555", {}],
]


# ------------------------------------------------------------------------------------------------- helpers
def _run(monkeypatch, tmp_path, sport, env=None):
    from sports import expansion_history as history
    for k in ('SCOUT_EXPANSION_SOURCES', 'SCOUT_EXPANSION_GRADED_SOURCES', 'SCOUT_EXPANSION_PHASE_FOTMOB',
              'SCOUT_EXPANSION_PHASE_THESCORE', 'SCOUT_EXPANSION_PHASE_FOOTBALL_DATA', 'SCOUT_EXPANSION_PHASE_EXCHANGES'):
        monkeypatch.delenv(k, raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    world = build_world(tmp_path)
    calls, stored = [], []
    monkeypatch.setattr('sports.expansion_sources.get_json', fake_transport(world, calls))
    monkeypatch.setattr('sports.expansion_sources.get_text', lambda *a, **k: (_ for _ in ()).throw(ValueError('no csv')))
    monkeypatch.setattr('sports.expansion_research.publish', lambda *a, **k: {})
    real = history.store.put_many
    monkeypatch.setattr(history.store, 'put_many', lambda con, recs: stored.extend(recs) or real(con, recs))
    out = history.collect(sport)
    return world, calls, stored, out


def _graded(stored):
    return sorted((r.event_id, r.participant_id, r.stat, r.value, r.source) for r in stored
                  if getattr(r, 'source', None) in ('fotmob', 'thescore'))


# ------------------------------------------------------------------------------------------------- 1. kill switch
def test_all_research_off_soccer_is_the_53dad0b_plan(monkeypatch, tmp_path):
    world, calls, stored, out = _run(monkeypatch, tmp_path, 'soccer', {'SCOUT_EXPANSION_SOURCES': '0'})
    assert normalized_plan(calls, world['now']) == EXPECTED_53DAD0B_SOCCER
    assert out['sources'] == {}
    assert {r[0] for r in _graded(stored)} == {world['fot']['event_id']}      # FotMob base league still collected
    assert not (tmp_path / 'soccer' / 'match-context.json').exists()


def test_every_phase_switch_off_equals_the_global_switch(monkeypatch, tmp_path):
    env = {f'SCOUT_EXPANSION_PHASE_{n}': '0' for n in ('FOTMOB', 'FOOTBALL_DATA', 'EXCHANGES')}
    world, calls, stored, out = _run(monkeypatch, tmp_path, 'soccer', env)
    assert normalized_plan(calls, world['now']) == EXPECTED_53DAD0B_SOCCER and out['sources'] == {}


def test_all_research_off_tennis_collects_the_53dad0b_finals_counts_only(monkeypatch, tmp_path):
    world, calls, stored, out = _run(monkeypatch, tmp_path, 'tennis', {'SCOUT_EXPANSION_SOURCES': '0'})
    urls = [u for u, _ in calls]
    assert [x for x in normalized_plan(calls, world['now']) if '365scores' not in x[0]] == EXPECTED_53DAD0B_TENNIS
    assert {r[2] for r in _graded(stored)} == {'aces', 'double_faults', 'points_won'}   # no research stat names
    assert 'matches/556' not in ' '.join(urls) and not (tmp_path / 'tennis' / 'match-context.json').exists()


def test_research_switch_off_keeps_graded_inputs_in_the_phase(monkeypatch, tmp_path):
    """PHASE_FOTMOB=0 alone: the newer league stops, the six graded leagues keep coming (graded_only)."""
    world, calls, stored, out = _run(monkeypatch, tmp_path, 'soccer', {'SCOUT_EXPANSION_PHASE_FOTMOB': '0'})
    assert out['sources']['fotmob']['state'] == 'graded_only'
    assert {r[0] for r in _graded(stored)} == {world['fot']['event_id']}
    world, calls, stored, out = _run(monkeypatch, tmp_path / 'b', 'tennis', {'SCOUT_EXPANSION_PHASE_THESCORE': '0'})
    assert out['sources']['thescore']['state'] == 'graded_only'
    assert {r[2] for r in _graded(stored)} == {'aces', 'double_faults', 'points_won'}


def test_default_collects_graded_and_research(monkeypatch, tmp_path):
    world, calls, stored, out = _run(monkeypatch, tmp_path, 'soccer')
    assert out['sources']['fotmob']['state'] in ('ok', 'budget')
    assert world['fot']['event_id'] in {r[0] for r in _graded(stored)}
    world, calls, stored, out = _run(monkeypatch, tmp_path / 'b', 'tennis')
    assert {'aces', 'bp_chances', 'first_serve_in_pct'} <= {r[2] for r in _graded(stored)}


def test_only_the_graded_switch_stops_graded_collection(monkeypatch, tmp_path):
    world, calls, stored, out = _run(monkeypatch, tmp_path, 'soccer', {'SCOUT_EXPANSION_GRADED_SOURCES': '0'})
    assert out['sources']['fotmob']['state'] == 'off' and not _graded(stored)
    assert not any('fotmob' in u for u, _ in calls)
    world, calls, stored, out = _run(monkeypatch, tmp_path / 'b', 'soccer',
                                     {'SCOUT_EXPANSION_GRADED_SOURCES': '0', 'SCOUT_EXPANSION_SOURCES': '0'})
    assert not any('fotmob' in u for u, _ in calls) and not _graded(stored)


def test_all_off_gives_espn_the_whole_window(monkeypatch, tmp_path):
    """No reserve when nothing would use it: a slow ESPN day keeps requesting until the 150 s deadline."""
    from sports import expansion_collect as phases, expansion_history as history
    for k in ('SCOUT_EXPANSION_PHASE_FOTMOB', 'SCOUT_EXPANSION_GRADED_SOURCES'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('SCOUT_EXPANSION_SOURCES', '0')
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    clock = [1000.0]
    monkeypatch.setattr(history.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(phases.time, 'monotonic', lambda: clock[0])
    seen = []

    def slow(sport, url, **kw):
        seen.append(url)
        clock[0] += 30
        return {'events': []}
    monkeypatch.setattr('sports.expansion_sources.get_json', slow)
    monkeypatch.setattr('sports.expansion_research.publish', lambda *a, **k: {})
    monkeypatch.setattr('sports.expansion_challenger.collect', lambda catalogs, get, *a: [get(f'u{i}', {}) for i in range(9)])
    monkeypatch.setattr('sports.expansion_tennis.collect_details', lambda *a, **k: [])
    history.collect('tennis', max_requests=16, max_seconds=150)
    assert len(seen) == 5                       # 1000..1120 all < 1150: one more than with the 45 s reserve (4)


# ------------------------------------------------------------------------------------------------- 2. rosters
def _slate(tmp_path, base_teams, new_teams, checked=None):
    from sports import expansion_history as history
    now = datetime.now(timezone.utc)
    catalogs, offers = {}, []
    for comp, teams in (('bra.1', base_teams), ('eng.2', new_teams)):
        rows = {}
        for i in range(0, len(teams), 2):
            pair = teams[i:i + 2]
            m = {'event_id': f'soccer:espn:{comp}:{i}:{i}', 'competition': comp, 'status': 'scheduled',
                 'start': _iso(now + timedelta(hours=5)),
                 'participants': [{'id': f'soccer:team:espn:{t}', 'name': f'Team{t} FC', 'short_name': f'Team{t}'}
                                  for t in pair]}
            rows[str(i)] = m
            offers.append({'sport': 'soccer', 'teams': [f'Team{t}' for t in pair], 'team': f'Team{pair[0]}',
                           'player': 'X', 'start': m['start']})
            offers.append({'sport': 'soccer', 'teams': [f'Team{t}' for t in pair], 'team': f'Team{pair[1]}',
                           'player': 'X', 'start': m['start']})
        catalogs[comp] = rows
    history.atomic_json(tmp_path / 'board.json', {'offers': offers})
    if checked:
        history.atomic_json(tmp_path / 'team-history-state.json', checked)
    return catalogs


def _getter(calls, cap=99):
    def get(url, params):
        if len(calls) >= cap:
            raise StopIteration
        calls.append(url)
        tid = re.search(r'/teams/(\d+)/', url)[1]
        return {'team': {'id': tid}, 'athletes': []} if url.endswith('/roster') else {'team': {'id': tid}, 'events': []}
    return get


def test_new_teams_never_take_a_due_base_roster_slot(tmp_path):
    """Never-checked newer-league teams sort first by checked_at - they still cannot displace two due base teams."""
    from sports import expansion_history as history
    old = _iso(datetime.now(timezone.utc) - timedelta(hours=7))
    state = {f'bra.1:soccer:team:espn:{t}': {'checked_at': old, 'schedule_checked_at': old} for t in (1, 2)}
    catalogs = _slate(tmp_path, [1, 2], [3, 4], state)
    calls = []
    history.collect_soccer_teams(catalogs, _getter(calls), lambda *a: True, tmp_path, 'base')
    assert [re.search(r'/soccer/([^/]+)/teams/(\d+)', u).groups() for u in calls[::2]] == [('bra.1', '1'), ('bra.1', '2')]


def test_new_teams_get_two_extra_slots_from_what_is_left(tmp_path):
    from sports import expansion_history as history
    catalogs = _slate(tmp_path, [1, 2], [3, 4, 5, 6])
    calls = []
    history.collect_soccer_teams(catalogs, _getter(calls), lambda *a: True, tmp_path)   # base pass, then new pass
    teams = [re.search(r'/soccer/([^/]+)/teams/(\d+)', u).groups() for u in calls[::2]]
    assert teams[:2] == [('bra.1', '1'), ('bra.1', '2')] and len(teams) == 4
    assert all(c == 'eng.2' for c, _ in teams[2:])


def test_no_budget_left_means_no_new_team_request(tmp_path):
    from sports import expansion_history as history
    import pytest
    catalogs = _slate(tmp_path, [1, 2], [3, 4])
    calls = []
    with pytest.raises(StopIteration):
        history.collect_soccer_teams(catalogs, _getter(calls, cap=4), lambda *a: True, tmp_path)
    assert len(calls) == 4 and all('/bra.1/' in u for u in calls)


def test_history_job_runs_new_rosters_after_the_base_summaries(monkeypatch, tmp_path):
    """Default job order inside the shared 16-request cap: base rosters, base summaries, then newer competitions."""
    world, calls, stored, out = _run(monkeypatch, tmp_path, 'soccer')
    urls = [u for u, _ in calls]
    base_summaries = [i for i, u in enumerate(urls) if u.endswith('/bra.1/summary')]
    new_requests = [i for i, u in enumerate(urls) if '/eng.2/' in u]
    assert len(base_summaries) == 4 and new_requests and min(new_requests) > max(base_summaries)
    assert urls[:11] == [u for u, _ in EXPECTED_53DAD0B_SOCCER[:11]]      # 53dad0b prefix untouched


# ------------------------------------------------------------------------------------------------- 4. catalog memory
def test_shared_leaf_catalog_is_byte_identical_and_shares_players():
    from sports.expansion_history import load_catalog
    player = {'id': 'soccer:player:espn:1', 'name': 'Test Player', 'team_id': 'soccer:team:espn:1'}
    rows = {f'e{i}': {'event_id': f'e{i}', 'scores': {'a': 1.0, 'b': float(i)}, 'players': [dict(player)],
                      'participants': [{'id': 'a', 'name': 'A FC', 'short_name': 'A'}, {'id': 'b', 'name': 'B'}]}
            for i in range(5)}
    text = json.dumps(rows, separators=(',', ':'))
    shared = load_catalog(text, {})
    assert json.dumps(shared, separators=(',', ':')) == text == json.dumps(json.loads(text), separators=(',', ':'))
    assert shared['e0']['players'][0] is shared['e4']['players'][0]           # one object for one player
    assert shared['e0']['scores'] is not shared['e1']['scores']               # non-leaf / non-identity dicts never shared
    assert shared['e0']['players'] is not shared['e1']['players']             # lists never shared


def test_shared_leaf_catalog_keeps_number_types_apart():
    from sports.expansion_history import load_catalog
    text = json.dumps({'a': {'p': [{'id': 'x', 'name': 'n', 'v': 1}]}, 'b': {'p': [{'id': 'x', 'name': 'n', 'v': 1.0}]},
                       'c': {'p': [{'id': 'x', 'name': 'n', 'v': True}]}}, separators=(',', ':'))
    assert json.dumps(load_catalog(text, {}), separators=(',', ':')) == text
