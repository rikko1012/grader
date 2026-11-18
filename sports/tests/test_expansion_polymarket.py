"""Polymarket tennis reference prices: exact identity against Scout's own fixtures, or nothing.

Events are trimmed from the real answers read 2026-11-18 (fixtures/polymarket_tennis.json, raw Gamma shape).
Fixtures are in the stored catalogue shape (current-matches.json)."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

import pytest

from sports import expansion_polymarket as xp
from sports import expansion_sources, net

DOC = json.loads((Path(__file__).parent / 'fixtures' / 'polymarket_tennis.json').read_text(encoding='utf-8'))
NOW = '2026-11-18T21:28:00Z'


def raw(part, tour='atp'):
    return deepcopy(next(e for e in DOC[tour] if part in e['slug']))


def event(part, tour='atp'):
    return xp.trim(raw(part, tour))


def player(ident, name):
    return {'id': f'atp:player:espn:{ident}', 'name': name, 'abbreviation': None, 'short_name': None}


def fixture(a, b, start='2026-11-19T13:00:00Z', competition='atp', key='9', status='scheduled', name='Nitto ATP Finals'):
    return {'event_id': f'{competition}:espn:{competition}:339-2026:{key}', 'competition': competition,
            'name': name, 'start': start, 'status': status, 'participants': [a, b]}


SHELTON, JODAR = player(9250, 'Ben Shelton'), player(12657, 'Rafael Jodar')


def parse(events, fixtures, tour='atp', now=NOW):
    return xp.parse(events, tour, fixtures, now)


def test_trim_keeps_only_what_the_parser_reads():
    kept = event('shelton-jodar')
    assert sorted(kept) == ['ended', 'league', 'live', 'moneyline', 'slug', 'startTime', 'teams', 'title', 'types']
    assert kept['teams'] == ['Ben Shelton', 'Rafael Jodar'] and len(kept['moneyline']) == 1
    assert sorted(kept['moneyline'][0]) == sorted(xp._KEEP) and kept['league'].startswith('ATP World Tour Finals')
    assert set(kept['types']) == xp.SEEN_TYPES
    assert len(json.dumps(kept)) < 1000 < len(json.dumps(raw('shelton-jodar')))


def test_match_winner_row_in_the_fixture_order_with_the_fixture_start():
    rows, stats = parse([event('shelton-jodar')], [fixture(JODAR, SHELTON, '2026-11-19T12:40:00Z')])
    row = rows[0]
    # Polymarket lists Shelton first at 66c / 67c; the fixture lists Jodar first, so his side is the complement.
    assert [o['label'] for o in row['outcomes']] == ['Rafael Jodar', 'Ben Shelton']
    assert [(o['bid'], o['ask'], o['mid']) for o in row['outcomes']] == [(.33, .34, .335), (.66, .67, .665)]
    assert [o['participant_id'] for o in row['outcomes']] == [JODAR['id'], SHELTON['id']]
    assert [o['selection_id'] for o in row['outcomes']] == ['atp-shelton-jodar-2026-11-17:1', 'atp-shelton-jodar-2026-11-17:0']
    assert not any('american' in o or 'decimal' in o for o in row['outcomes'])
    assert row['start'] == '2026-11-19T12:40:00Z' and row['source_start'] == '2026-11-19T13:00:00Z'
    assert row['event_id'] == 'atp:espn:atp:339-2026:9' and row['offer_id'] == row['source_event'] == 'atp-shelton-jodar-2026-11-17'
    assert (row['sport'], row['platform'], row['source'], row['scope'], row['market_type'], row['main']) == (
        'tennis', 'polymarket', 'gamma', 'full_game', 'Exchange match', True)
    assert row['reference_only'] is True and row['identity_status'] == 'scout_fixture_exact'
    assert 'Not tradable from the US' in row['jurisdiction'] and row['settlement_rules'].startswith('polymarket_rules')
    assert row['match'] == 'Rafael Jodar vs Ben Shelton' and row['competition'] == 'atp' and row['liquidity'] > 500
    assert stats['matched'] == 1 == stats['events'] and stats['unmatched'] == [] and stats['unparsed_types'] == []


def test_revision_follows_the_price_only():
    ev, fixtures = event('shelton-jodar'), [fixture(SHELTON, JODAR)]
    first = parse([ev], fixtures)[0][0]
    later = parse(deepcopy([ev]), fixtures, now='2026-11-18T21:31:00Z')[0][0]
    assert later['revision'] == first['revision'] and later['observed_at'] != first['observed_at']
    busy = deepcopy(ev)
    busy['moneyline'][0]['liquidityNum'] = 99999.5
    assert parse([busy], fixtures)[0][0]['revision'] == first['revision']      # depth alone is not a new quote
    moved = deepcopy(ev)
    moved['moneyline'][0]['bestAsk'] = .68
    assert parse([moved], fixtures)[0][0]['revision'] != first['revision']


@pytest.mark.parametrize('defect,count', [
    ('wide', 'dropped_wide'), ('empty_book', 'dropped_wide'), ('thin', 'dropped_thin'), ('no_depth', 'dropped_thin'),
    ('crossed', 'dropped_invalid'), ('no_bid', 'dropped_invalid'), ('closed', 'dropped_invalid'),
    ('inactive', 'dropped_invalid'), ('not_accepting', 'dropped_invalid'), ('outcomes_differ', 'dropped_invalid'),
    ('bad_outcomes', 'dropped_invalid'), ('three_teams', 'dropped_invalid'), ('slash_name', 'dropped_invalid'),
    ('doubles', 'dropped_invalid'), ('two_moneylines', 'dropped_invalid'), ('no_moneyline', 'dropped_invalid'),
    ('start_differs', 'dropped_invalid'), ('no_start', 'dropped_invalid'),
    ('started', 'started'), ('live', 'started'), ('ended', 'started')])
def test_refusals(defect, count):
    ev, fixtures = event('shelton-jodar'), [fixture(SHELTON, JODAR)]
    m = ev['moneyline'][0]
    if defect == 'wide': m.update(bestBid=.60)
    if defect == 'empty_book': m.update(bestBid=.01, bestAsk=.99)
    if defect == 'thin': m['liquidityNum'] = 499.9
    if defect == 'no_depth': m['liquidityNum'] = None
    if defect == 'crossed': m.update(bestBid=.67)
    if defect == 'no_bid': m['bestBid'] = None
    if defect == 'closed': m['closed'] = True
    if defect == 'inactive': m['active'] = False
    if defect == 'not_accepting': m['acceptingOrders'] = False
    if defect == 'outcomes_differ': m['outcomes'] = '["Ben Shelton", "R. Jodar"]'
    if defect == 'bad_outcomes': m['outcomes'] = 'not json'
    if defect == 'three_teams': ev['teams'].append('Carlos Alcaraz')
    if defect == 'slash_name':
        ev['teams'][0] = 'Ben Shelton / Taylor Fritz'
        m['outcomes'] = '["Ben Shelton / Taylor Fritz", "Rafael Jodar"]'
        fixtures = [fixture(player(1, 'Ben Shelton / Taylor Fritz'), JODAR)]
    if defect == 'doubles': ev['league'] = 'ATP Finals Doubles'
    if defect == 'two_moneylines': ev['moneyline'].append(dict(m, slug='second'))
    if defect == 'no_moneyline': ev['moneyline'] = []
    if defect == 'start_differs': m['gameStartTime'] = '2026-11-19 14:00:00+00'
    if defect == 'no_start': ev['startTime'] = None
    if defect == 'started': ev['startTime'], m['gameStartTime'] = '2026-11-18T21:28:00Z', '2026-11-18 21:28:00+00'
    if defect == 'live': ev['live'] = True
    if defect == 'ended': ev['ended'] = True
    rows, stats = parse([ev], fixtures)
    assert rows == [] and stats['matched'] == 0 and stats[count] == 1


def test_the_recorded_live_and_finished_matches_are_never_rows():
    a, b = player(1, 'Mikael Arseneault'), player(2, 'Clement Chidekh')
    c, d = player(3, 'Flavio Cobolli'), player(4, 'Frances Tiafoe')
    fixtures = [fixture(a, b, '2026-11-18T22:00:00Z', 'atp_challenger', '1'), fixture(c, d, '2026-11-18T22:00:00Z', key='2')]
    rows, stats = parse([event('arsene-chidekh'), event('cobolli-tiafoe')], fixtures)
    assert rows == [] and stats['started'] == 2


@pytest.mark.parametrize('case,linked', [
    ('same', True), ('accents_case_punctuation', True), ('two_hours', True), ('challenger_level', True),
    ('surnames', False), ('initial', False), ('one_name_differs', False), ('over_two_hours', False),
    ('wta_level', False), ('wta125_level', False), ('in_progress', False), ('final', False),
    ('fixture_started', False), ('same_player_twice', False), ('two_fixtures', False)])
def test_matching(case, linked):
    a, b, kw = SHELTON, JODAR, {}
    if case == 'accents_case_punctuation': a, b = player(1, 'BEN  Shélton'), player(2, 'Rafael Jódar.')
    if case == 'two_hours': kw['start'] = '2026-11-19T15:00:00Z'
    if case == 'challenger_level': kw['competition'] = 'atp_challenger'
    if case == 'surnames': a, b = player(1, 'Shelton'), player(2, 'Jodar')
    if case == 'initial': a = player(1, 'B. Shelton')
    if case == 'one_name_differs': b = player(3, 'Rafael Jodar Jr')
    if case == 'over_two_hours': kw['start'] = '2026-11-19T15:00:01Z'
    if case == 'wta_level': kw['competition'] = 'wta'
    if case == 'wta125_level': kw['competition'] = 'wta125'
    if case in ('in_progress', 'final'): kw['status'] = case
    if case == 'fixture_started': kw['start'] = '2026-11-18T21:00:00Z'
    if case == 'same_player_twice': b = dict(JODAR, id=SHELTON['id'])
    fixtures = [fixture(a, b, **kw)]
    if case == 'two_fixtures': fixtures.append(fixture(JODAR, SHELTON, '2026-11-19T14:00:00Z', key='10'))
    rows, stats = parse([event('shelton-jodar')], fixtures)
    assert len(rows) == (1 if linked else 0)
    if case == 'two_fixtures':
        assert stats['ambiguous'] == 1
    elif not linked:
        assert stats['unmatched'] == ['Ben Shelton vs Rafael Jodar']


def test_two_events_for_one_fixture_drop_both():
    ev = event('shelton-jodar')
    twin = deepcopy(ev)
    twin['slug'] = twin['moneyline'][0]['slug'] = 'atp-shelton-jodar-2026-11-19'
    rows, stats = parse([ev, twin], [fixture(SHELTON, JODAR)])
    assert rows == [] and stats['ambiguous'] == 2


def test_a_match_catalogued_under_two_levels_links_by_the_tournament_name_only():
    # Real 2026-11-18: the Colina WTA 125 match was in the catalogue as wta125 "Colina" and as wta "LP Open by IND".
    a, b = player(1, 'Anastasia Zolotareva'), player(2, 'Irene Burillo Escorihuela')
    twice = [fixture(a, b, '2026-11-19T14:30:00Z', 'wta', '1', name='LP Open by IND'),
             fixture(a, b, '2026-11-19T15:00:00Z', 'wta125', '2', name='Colina')]
    rows, stats = parse([event('zolota-escorih', 'wta')], twice, tour='wta')
    assert [r['event_id'] for r in rows] == ['wta125:espn:wta125:339-2026:2'] and rows[0]['tournament'] == 'Colina'
    assert [(o['label'], o['mid']) for o in rows[0]['outcomes']] == [('Anastasia Zolotareva', .275), ('Irene Burillo Escorihuela', .725)]
    for names in (('LP Open by IND', 'Santiago'), ('Colina', 'Colina')):       # no name decides, or both do
        unclear = [dict(f, name=n) for f, n in zip(twice, names)]
        rows, stats = parse([event('zolota-escorih', 'wta')], unclear, tour='wta')
        assert rows == [] and stats['ambiguous'] == 1
    # The ATP series never links a WTA-level fixture.
    assert parse([event('zolota-escorih', 'wta')], twice, tour='atp')[0] == []


def test_recorded_answer_against_a_catalogue_counts_every_drop():
    pairs = [('Alexander Zverev', 'Arthur Fils', '2026-11-19T19:30:00Z'), ('Ben Shelton', 'Rafael Jodar', '2026-11-19T13:00:00Z'),
             ('Daniil Medvedev', 'Frances Tiafoe', '2026-11-20T13:00:00Z')]                    # 67c / 75c: wide
    evs = [xp.trim(e) for e in deepcopy(DOC['atp'])]
    thin = next(e for e in evs if 'rocha-justo' in e['slug'])                                  # $446 resting
    wide = next(e for e in evs if 'donald-kwon' in e['slug'])                                  # 21c / 28c
    fixtures = [fixture(player(i * 2, a), player(i * 2 + 1, b), start, key=str(i)) for i, (a, b, start) in enumerate(pairs)]
    for n, e in enumerate((thin, wide)):
        fixtures.append(fixture(player(50 + n, e['teams'][0]), player(60 + n, e['teams'][1]), e['startTime'], 'atp_challenger', f'c{n}'))
    rows, stats = parse(evs, fixtures)
    assert sorted(r['offer_id'] for r in rows) == ['atp-shelton-jodar-2026-11-17', 'atp-zverev-fils-2026-11-17']
    unmatched = next(e for e in evs if 'schoolk-ayeni' in e['slug'])['teams']                  # no fixture given
    assert stats == {'events': 8, 'matched': 2, 'started': 2, 'dropped_invalid': 0, 'dropped_wide': 2, 'dropped_thin': 1,
                     'ambiguous': 0, 'unmatched': [' vs '.join(unmatched)], 'unparsed_types': []}


def test_unknown_market_type_is_reported_and_never_parsed():
    ev = raw('shelton-jodar')
    ev['markets'].append({'sportsMarketType': 'tennis_completed_match', 'question': 'Will the match be completed?',
                          'outcomes': '["Yes", "No"]', 'bestBid': .95, 'bestAsk': .96, 'active': True, 'closed': False,
                          'liquidityNum': 5000, 'slug': 'completed'})
    rows, stats = parse([xp.trim(ev)], [fixture(SHELTON, JODAR)])
    assert stats['unparsed_types'] == ['tennis_completed_match']
    assert [r['offer_id'] for r in rows] == ['atp-shelton-jodar-2026-11-17'] and len(rows[0]['outcomes']) == 2


def test_parse_needs_a_known_tour_and_a_time():
    for tour, now in (('itf', NOW), ('atp', 'yesterday')):
        with pytest.raises(ValueError):
            xp.parse([], tour, [], now)


# ---- collect: budget, paging, holds ----------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(net, '_HOLD', {})
    return tmp_path


def _text(moment):
    return moment.isoformat().replace('+00:00', 'Z')


def _live(part='shelton-jodar', tour='atp', hours=24, slug=None):
    """A recorded event moved to start `hours` from now."""
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=hours)
    ev = raw(part, tour)
    ev['startTime'] = _text(start)
    for m in ev['markets']:
        m['gameStartTime'] = start.strftime('%Y-%m-%d %H:%M:%S+00')
    if slug:
        ev['slug'] = slug
        for m in ev['markets']:
            m['slug'] = m['slug'].replace(raw(part, tour)['slug'], slug)
    return ev, _text(start)


def _catalogue(store, fixtures):
    directory = store / 'tennis'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'current-matches.json').write_text(json.dumps(fixtures), encoding='utf-8')
    return directory


class Source:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_collect_one_request_one_matched_row(store):
    ev, start = _live()
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start)])
    get = Source([ev])
    rows, status = xp.collect('tennis', get, directory)
    assert len(rows) == 1 and rows[0]['platform'] == 'polymarket' and rows[0]['start'] == start
    assert status['ok'] and status['reference'] and status['markets'] == 1 == status['matched'] == status['requests']
    assert 'truncated' not in status and status['events'] == 1
    url, kw = get.calls[0]
    params = kw['params']
    assert url == 'https://gamma-api.polymarket.com/events' and kw['tries'] == 1 and kw['timeout'] <= 12 and 'headers' not in kw
    assert {k: params[k] for k in ('series_id', 'closed', 'order', 'ascending', 'limit', 'offset')} == {
        'series_id': '10365', 'closed': 'false', 'order': 'endDate', 'ascending': 'true', 'limit': 25, 'offset': 0}
    low, high = (datetime.fromisoformat(params[k].replace('Z', '+00:00')) for k in ('end_date_min', 'end_date_max'))
    now = datetime.now(timezone.utc)
    assert abs((low - now).total_seconds() - 2 * 86400) < 60 and abs((high - now).total_seconds() - 9 * 86400) < 60


def test_only_the_tours_with_a_scheduled_fixture_are_requested(store):
    ev, start = _live('zolota-escorih', 'wta')
    a, b = player(1, 'Anastasia Zolotareva'), player(2, 'Irene Burillo Escorihuela')
    directory = _catalogue(store, [fixture(a, b, start, 'wta125', name='Colina')])
    get = Source([ev])
    rows, status = xp.collect('tennis', get, directory)
    assert [c[1]['params']['series_id'] for c in get.calls] == ['10366'] and len(rows) == 1
    assert rows[0]['competition'] == 'wta125'


@pytest.mark.parametrize('case', ['no_catalogue', 'beyond_horizon', 'not_scheduled', 'started', 'oversize_catalogue', 'soccer'])
def test_collect_sends_nothing_without_a_relevant_fixture(store, case):
    start = _text(datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
        days=9 if case == 'beyond_horizon' else -1 if case == 'started' else 1))
    directory = store / 'tennis'
    if case != 'no_catalogue':
        directory = _catalogue(store, [fixture(SHELTON, JODAR, start, status='final' if case == 'not_scheduled' else 'scheduled')])
    if case == 'oversize_catalogue':
        (directory / 'current-matches.json').write_text('[' + ' ' * 3_000_001 + ']', encoding='utf-8')
    get = Source()
    rows, status = xp.collect('soccer' if case == 'soccer' else 'tennis', get, directory)
    assert rows == [] and get.calls == [] and status['ok'] and status['skipped'] == 'no_fixture'


def test_paging_stops_on_a_short_page_and_reads_a_repeated_event_once(store, monkeypatch):
    monkeypatch.setattr(xp, 'PAGE', 2)
    ev, start = _live()
    other, _ = _live('zverev-fils')
    third, _ = _live('schoolk-ayeni')
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start)])
    get = Source([ev, other], [ev, third], [])            # the list moved between requests: one event twice
    rows, status = xp.collect('tennis', get, directory)
    assert [c[1]['params']['offset'] for c in get.calls] == [0, 2, 4] and all(c[1]['params']['limit'] == 2 for c in get.calls)
    assert status['events'] == 3 and len(rows) == 1 and status['requests'] == 3 and 'truncated' not in status


def test_request_budget_stops_the_paging_and_says_so(store, monkeypatch):
    monkeypatch.setattr(xp, 'PAGE', 1)
    ev, start = _live()
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start)])
    get = Source(*[[_live(slug=f'atp-copy-{i}')[0]] for i in range(10)])
    rows, status = xp.collect('tennis', get, directory)
    assert len(get.calls) == xp.MAX_REQUESTS == 6 == status['requests'] and status['truncated'] is True and status['ok']


def test_wall_clock_stops_further_requests(store, monkeypatch):
    ev, start = _live()
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start)])
    monkeypatch.setattr(xp, 'WALL_S', 0)
    get = Source([ev])
    rows, status = xp.collect('tennis', get, directory)
    assert rows == [] and get.calls == [] and status['truncated'] is True


@pytest.mark.parametrize('retry_after,expected', [(None, 15 * 60), (3600, 3600), (5, 15 * 60)])
def test_429_persists_a_hold_and_the_next_run_sends_nothing(store, retry_after, expected):
    ev, start = _live()
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start)])
    get = Source(net.HttpError(429, xp.API + '/events', '', not_before=time.time() + retry_after if retry_after else None))
    rows, status = xp.collect('tennis', get, directory)
    assert rows == [] and status['ok'] is False and status['error'] == 'http_429' and len(get.calls) == 1
    assert abs(expansion_sources.held_until('tennis', xp.HOST) - time.time() - expected) < 5 and status['held_until']
    net._HOLD.clear()                                     # a new process: the stored hold still applies
    again = Source([ev])
    rows, status = xp.collect('tennis', again, directory)
    assert rows == [] and again.calls == [] and status['error'] == 'held' and status['held_until']


def test_403_is_a_six_hour_hold(store):
    ev, start = _live()
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start)])
    get = Source(net.HttpError(403, xp.API + '/events', 'forbidden'))
    rows, status = xp.collect('tennis', get, directory)
    assert rows == [] and status['error'] == 'http_403' and len(get.calls) == 1
    assert abs(expansion_sources.held_until('tennis', xp.HOST) - time.time() - 6 * 3600) < 5


@pytest.mark.parametrize('failure', [net.HttpError(None, xp.API, 'timeout'), net.HttpError(500, xp.API, ''),
                                     ValueError('oversize response refused'), {'events': []}, 'text'])
def test_other_failures_end_the_run_without_a_second_request_or_a_hold(store, failure):
    ev, start = _live()
    wta, _ = _live('zolota-escorih', 'wta')
    a, b = player(1, 'Anastasia Zolotareva'), player(2, 'Irene Burillo Escorihuela')
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start), fixture(a, b, start, 'wta125', '2')])
    get = Source(failure, [wta])
    rows, status = xp.collect('tennis', get, directory)
    assert rows == [] and status['ok'] is False and len(get.calls) == 1 and 'held_until' not in status
    assert expansion_sources.held_until('tennis', xp.HOST) - time.time() <= 2


def test_a_failed_later_page_keeps_the_rows_already_read(store, monkeypatch):
    monkeypatch.setattr(xp, 'PAGE', 1)
    ev, start = _live()
    directory = _catalogue(store, [fixture(SHELTON, JODAR, start)])
    rows, status = xp.collect('tennis', Source([ev], net.HttpError(500, xp.API, '')), directory)
    assert len(rows) == 1 and status['ok'] is False and status['error'] == 'http_500' and status['requests'] == 2


def test_row_cap_keeps_the_nearest_starts(store, monkeypatch):
    monkeypatch.setattr(xp, 'MAX_ROWS', 2)
    fixtures, evs = [], []
    for i in range(4):
        a, b = player(100 + i, f'Alpha Player{i}'), player(200 + i, f'Beta Player{i}')
        ev, start = _live(hours=30 - i, slug=f'atp-copy-{i}')
        ev['teams'] = [{'name': a['name']}, {'name': b['name']}]
        next(m for m in ev['markets'] if m['sportsMarketType'] == 'moneyline')['outcomes'] = json.dumps([a['name'], b['name']])
        fixtures.append(fixture(a, b, start, key=str(i)))
        evs.append(ev)
    rows, status = xp.collect('tennis', Source(evs), _catalogue(store, fixtures))
    assert [r['teams'][0] for r in rows] == ['Alpha Player3', 'Alpha Player2'] and status['markets'] == 2 and status['matched'] == 4


def test_switch_is_tennis_only_and_on_unless_zero(monkeypatch):
    monkeypatch.delenv('SCOUT_TENNIS_POLYMARKET', raising=False)
    assert xp.switched_on('tennis') and not xp.switched_on('soccer')
    monkeypatch.setenv('SCOUT_TENNIS_POLYMARKET', '0')
    assert not xp.switched_on('tennis')
    monkeypatch.setenv('SCOUT_TENNIS_POLYMARKET', '1')
    monkeypatch.setenv('SCOUT_SOCCER_POLYMARKET', '1')
    assert xp.switched_on('tennis') and not xp.switched_on('soccer')
    assert xp.MAX_BYTES == 2_000_000 and net._GAPS[xp.HOST] >= 1.0
