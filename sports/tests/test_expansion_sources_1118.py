"""Research sources as budgeted phases of the existing history job (2026-11-18): own caps, the main phases keep a
reserve for them, a failing source never stops the others, and FotMob keys leagues on verified ids."""
import json
import time

import pytest

from sports import expansion_collect as phases, expansion_history as history, net
from sports import expansion_soccer_detail as fotmob
from sports.tests.test_expansion_soccer_detail import fixture as fotmob_fixture


def test_each_phase_stops_at_its_own_request_cap(monkeypatch, tmp_path):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    calls = []

    def fake(name, sport, catalogs, persist, directory, get, text):
        for i in range(100):
            get(f'https://example.test/{name}/{i}', {})
    monkeypatch.setattr(phases, '_phase', fake)
    monkeypatch.setattr('sports.expansion_sources.get_json', lambda sport, url, **kw: calls.append(url) or {})
    monkeypatch.setattr(phases, 'PHASES', {'soccer': (('a', 3, 30), ('b', 2, 30))})
    out = phases.run('soccer', {}, lambda *a: True, tmp_path, time.monotonic() + 60)
    assert out['a']['requests'] == 3 and out['a']['state'] == 'budget'
    assert out['b']['requests'] == 2 and len(calls) == 5


def test_a_failing_source_never_stops_the_next_one(monkeypatch, tmp_path):
    def fake(name, *a):
        if name == 'a':
            raise net.HttpError(503, 'https://example.test', '')
        return {'errors': [], 'file': 'E0'}
    monkeypatch.setattr(phases, '_phase', fake)
    monkeypatch.setattr(phases, 'PHASES', {'soccer': (('a', 3, 30), ('b', 2, 30))})
    out = phases.run('soccer', {}, lambda *a: True, tmp_path, time.monotonic() + 60)
    assert out['a']['state'] == 'failed' and out['a']['errors'] == ['HttpError']
    assert out['b']['state'] == 'ok' and out['b']['detail'] == {'file': 'E0'}


def test_past_the_job_deadline_no_source_requests(monkeypatch, tmp_path):
    monkeypatch.setattr(phases, '_phase', lambda *a: pytest.fail('ran past the deadline'))
    out = phases.run('tennis', {}, lambda *a: True, tmp_path, time.monotonic() - 1)
    assert all(v['state'] == 'skipped_deadline' and v['requests'] == 0 for v in out.values())


def test_main_phases_leave_the_reserve_and_sources_still_run(monkeypatch, tmp_path):
    """A slow ESPN day: the scoreboards stop RESERVE_S before the deadline, the sources still get their turn."""
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    clock = [1000.0]
    monkeypatch.setattr(history.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(phases.time, 'monotonic', lambda: clock[0])
    seen = []

    def slow(sport, url, **kw):
        seen.append(url)
        clock[0] += 30                                  # every ESPN request takes 30 s (timeout is 10-20 s)
        return {'events': []}
    monkeypatch.setattr('sports.expansion_sources.get_json', slow)
    ran = []
    monkeypatch.setattr(phases, '_phase', lambda name, *a: ran.append((name, clock[0])) or {'errors': []})
    monkeypatch.setattr('sports.expansion_research.publish', lambda *a, **k: {})
    monkeypatch.setattr('sports.expansion_challenger.collect', lambda catalogs, get, *a: [get(f'u{i}', {}) for i in range(9)])
    history.collect('tennis', max_requests=16, max_seconds=150)
    # 4 scoreboards sent at 1000 / 1030 / 1060 / 1090; at 1120 (>= 1000 + 150 - 45) the main phases stop requesting
    assert len(seen) == 4 and ran == [('thescore', 1120), ('exchanges', 1120)]   # sources run in the reserve


# --------------------------------------------------------------------------------------------- FotMob league ids
def test_league_ids_resolve_listing_and_detail_including_group_names():
    assert fotmob.league_of_listing({'primaryId': 61, 'name': 'Liga Portugal', 'ccode': 'POR'}) == 'por.1'
    assert fotmob.league_of_listing({'primaryId': 130, 'name': 'Major League Soccer'}) == 'usa.1'   # renamed listing
    assert fotmob.league_of_listing({'primaryId': 230, 'name': 'Liga MX Apertura Playoff', 'id': 957972}) == 'mex.1'
    assert fotmob.league_of_listing({'primaryId': 522, 'name': 'Premier League', 'ccode': 'GHA'}) is None
    assert fotmob.league_of_detail({'leagueId': 47, 'parentLeagueId': 47}) == 'eng.1'
    assert fotmob.league_of_detail({'leagueId': 920741, 'parentLeagueId': 9806}) is None   # national teams: not mapped
    assert fotmob.league_of_detail({'leagueId': 'x'}) is None
    from sports.expansion import COMPETITIONS
    assert all(COMPETITIONS[s] == ('soccer', 'soccer') for s in fotmob.LEAGUES.values())
    assert fotmob.ORIGINAL <= set(fotmob.LEAGUES.values())


def test_a_mapped_new_league_is_detailed_only_while_offered(tmp_path):
    m, b = fotmob_fixture()
    m.update(competition='por.1', event_id='soccer:espn:por.1:1:1')
    b['general'].update(leagueId=61, parentLeagueId=61)
    dates = []

    def get(url, params):
        dates.append(params.get('date'))
        return {'leagues': []}
    fotmob.collect_details({'por.1': {'m': m}}, get, lambda *a: None, tmp_path, recent={})
    assert dates == []                                   # not offered recently: no request
    fotmob.collect_details({'por.1': {'m': m}}, get, lambda *a: None, tmp_path, recent={'por.1': 'x'})
    assert dates == ['20261102']
    rows = fotmob.parse_detail(b, 'por.1', [m], '2026-11-17T12:00:00Z').records
    assert rows and {r.source for r in rows} == {'fotmob'}


def test_a_group_stage_listing_resolves_by_its_parent_id(tmp_path):
    m, b = fotmob_fixture()
    listing = {'leagues': [{'ccode': 'BRA', 'name': 'Série A Final Group A', 'primaryId': 268, 'id': 999999,
                            'matches': [{'id': 44, 'home': b['header']['teams'][0], 'away': b['header']['teams'][1],
                                         'status': b['header']['status']}]}]}
    stored = []

    def get(url, params):
        return b if 'matchDetails' in url else listing
    fotmob.collect_details({'bra.1': {'x': m}}, get, lambda feed, comp: stored.extend(feed.records), tmp_path)
    assert stored and all(r.event_id == m['event_id'] for r in stored)


def test_a_503_keeps_the_detail_state(tmp_path):
    m, _b = fotmob_fixture()
    (tmp_path / 'detail-history-state.json').write_text(json.dumps({'older': '2026-11-17T00:00:00Z'}))

    def get(url, params):
        raise net.HttpError(503, url, '')
    errors = fotmob.collect_details({'bra.1': {'x': m}}, get, lambda *a: None, tmp_path)
    assert errors == ['HttpError']
    assert 'older' in json.loads((tmp_path / 'detail-history-state.json').read_text())


def test_kill_switches_stop_one_phase_or_all(monkeypatch, tmp_path):
    ran = []
    monkeypatch.setattr(phases, '_phase', lambda name, *a: ran.append(name) or {'errors': []})
    monkeypatch.setenv('SCOUT_EXPANSION_PHASE_EXCHANGES', '0')
    out = phases.run('soccer', {}, lambda *a: True, tmp_path, time.monotonic() + 60)
    assert ran == ['fotmob', 'football_data'] and out['exchanges']['state'] == 'off'
    monkeypatch.setenv('SCOUT_EXPANSION_SOURCES', '0')
    out = phases.run('tennis', {}, lambda *a: True, tmp_path, time.monotonic() + 60)
    # research off never stops a graded input: theScore still runs for the finals counts (graded_only)
    assert ran == ['fotmob', 'football_data', 'thescore'] and out['thescore']['state'] == 'graded_only'
    assert out['exchanges']['state'] == 'off'
    monkeypatch.setenv('SCOUT_EXPANSION_GRADED_SOURCES', '0')
    out = phases.run('tennis', {}, lambda *a: True, tmp_path, time.monotonic() + 60)
    assert ran == ['fotmob', 'football_data', 'thescore'] and all(v['state'] == 'off' for v in out.values())
