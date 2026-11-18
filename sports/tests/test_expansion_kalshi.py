"""Kalshi soccer / tennis reference prices: exact identity against Scout's own ESPN fixtures, or nothing.

Kalshi events are trimmed from real answers read 2026-11-18 (fixtures/kalshi_*_events.json). ESPN fixtures are in
the stored catalogue shape (current-matches.json)."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

import pytest

from sports import expansion_kalshi as xk
from sports import expansion_sources, net

HERE = Path(__file__).parent / 'fixtures'
NOW = '2026-11-18T12:00:00Z'


def events(name):
    return deepcopy(json.loads((HERE / f'kalshi_{name}_events.json').read_text(encoding='utf-8'))['events'])


def event(name, suffix):
    return next(e for e in events(name) if e['event_ticker'].endswith(suffix))


def team(ident, name, short=None, abbreviation=None):
    return {'id': f'soccer:team:espn:{ident}', 'name': name, 'abbreviation': abbreviation, 'short_name': short or name}


def match(home, away, start='2026-11-21T15:00:00Z', competition='eng.1', key='1', status='scheduled'):
    # ESPN order as stored: home first, name "<away> at <home>".
    return {'event_id': f'soccer:espn:{competition}:{key}:{key}', 'competition': competition,
            'name': f"{away['name']} at {home['name']}", 'start': start, 'status': status,
            'participants': [home, away], 'best_of': None, 'surface': None}


def player(ident, name):
    return {'id': f'atp:player:espn:{ident}', 'name': name, 'abbreviation': None, 'short_name': None}


def tennis_match(a, b, start='2026-11-20T13:00:00Z', competition='atp', key='9', status='scheduled'):
    return {'event_id': f'{competition}:espn:{competition}:339-2026:{key}', 'competition': competition,
            'name': 'Nitto ATP Finals', 'start': start, 'status': status, 'participants': [a, b]}


CITY, FULHAM = team(382, 'Manchester City', 'Man City', 'MNC'), team(370, 'Fulham', 'Fulham', 'FUL')
SHELTON, JODAR = player(1, 'Ben Shelton'), player(2, 'Rafael Jodar')
ALIAS = {'tottenham': 'tottenham hotspur'}


def soccer(evs, fixtures, alias=None):
    return xk.parse_soccer(evs, 'eng.1', fixtures, ALIAS if alias is None else alias, NOW)


def test_three_way_is_ordered_home_draw_away_with_the_espn_start():
    ev = event('epl', 'MCIFUL')
    # ESPN has Fulham at home here: the order follows ESPN's fixture, not Kalshi's title.
    rows, stats = soccer([ev], [match(FULHAM, CITY)])
    row = rows[0]
    assert [o['label'] for o in row['outcomes']] == ['Fulham', 'Draw', 'Manchester City']
    assert [o['participant_id'] for o in row['outcomes']] == [FULHAM['id'], None, CITY['id']]
    assert [(o['bid'], o['ask'], o['mid']) for o in row['outcomes']] == [(.12, .13, .125), (.17, .18, .175), (.69, .70, .695)]
    assert [o['selection_id'][-3:] for o in row['outcomes']] == ['FUL', 'TIE', 'MCI']
    assert row['start'] == '2026-11-21T15:00:00Z' != ev['markets'][0]['occurrence_datetime']
    assert row['event_id'] == 'soccer:espn:eng.1:1:1' and row['offer_id'] == 'KXEPLGAME-26NOV21MCIFUL'
    assert row['teams'] == ['Fulham', 'Manchester City'] and row['match'] == 'Fulham vs Manchester City'
    assert (row['platform'], row['scope'], row['market_type'], row['reference_only']) == ('kalshi', 'regulation', 'Exchange match', True)
    assert row['identity_status'] == 'scout_fixture_exact' and row['settlement_rules'] == 'exchange_native_reference_only'
    assert row['outcomes'][0]['volume'] > 0 and row['outcomes'][0]['source_updated_at'].startswith('2026-11-')
    assert stats['matched'] == 1 and stats['events'] == 1 and not stats['unmatched']
    assert not any('american' in o or 'decimal' in o for o in row['outcomes'])


def test_revision_follows_the_price_only():
    ev, fixtures = event('epl', 'MCIFUL'), [match(CITY, FULHAM)]
    first = soccer([ev], fixtures)[0][0]
    later = xk.parse_soccer(deepcopy([ev]), 'eng.1', fixtures, ALIAS, '2026-11-18T12:03:00Z')[0][0]
    assert later['revision'] == first['revision'] and later['observed_at'] != first['observed_at']
    busy = deepcopy(ev)
    busy['markets'][0].update(volume_fp='999999.00', updated_time='2026-11-18T11:59:00Z')
    assert soccer([busy], fixtures)[0][0]['revision'] == first['revision']     # volume alone is not a new quote
    moved = deepcopy(ev)
    moved['markets'][0]['yes_ask_dollars'] = '0.7100'
    assert soccer([moved], fixtures)[0][0]['revision'] != first['revision']


@pytest.mark.parametrize('suffix,home,away', [
    ('BOUNFO', team(349, 'AFC Bournemouth', 'Bournemouth'), team(393, 'Nottingham Forest')),
    ('HULBRI', team(306, 'Hull City'), team(331, 'Brighton & Hove Albion', 'Brighton')),
    ('NEWARS', team(361, 'Newcastle United', 'Newcastle'), team(359, 'Arsenal'))])
def test_short_name_match(suffix, home, away):
    ev = event('epl', suffix)
    start = '2026-11-22T14:00:00Z' if suffix == 'HULBRI' else '2026-11-21T15:00:00Z'
    rows, _ = soccer([ev], [match(home, away, start)])
    assert [o['label'] for o in rows[0]['outcomes']] == [home['name'], 'Draw', away['name']]


def test_alias_match_and_the_shipped_alias_file():
    spurs, ipswich = team(367, 'Tottenham Hotspur', 'Spurs', 'TOT'), team(373, 'Ipswich Town', 'Ipswich')
    ev = event('epl', 'TOTIPS')
    assert soccer([ev], [match(spurs, ipswich)], alias={})[0] == []
    assert xk.aliases('soccer', 'eng.1') == ALIAS and xk.aliases('tennis', 'atp') == {}
    assert soccer([ev], [match(spurs, ipswich)], alias=xk.aliases('soccer', 'eng.1'))[0][0]['teams'] == ['Tottenham Hotspur', 'Ipswich Town']


def test_unknown_team_is_dropped_and_listed():
    rows, stats = soccer([event('epl', 'MCIFUL')], [match(CITY, team(1, 'Fulham Ladies', 'Fulham LFC'))])
    assert rows == [] and stats['unmatched'] == ['Manchester City vs Fulham'] and stats['matched'] == 0


def test_abbreviation_never_matches():
    # Kalshi's CFC is Chelsea; ESPN's CFC is Coritiba.
    ev = event('epl', 'CFCLEE')
    coritiba, leeds = team(3456, 'Coritiba', 'Coritiba', 'CFC'), team(357, 'Leeds United', 'Leeds', 'LEE')
    assert soccer([ev], [match(coritiba, leeds)])[0] == []
    by_abbreviation = [match(team(1, 'Chelsea Example', 'Example', 'Chelsea'), leeds)]
    assert soccer([ev], by_abbreviation)[0] == []
    assert len(soccer([ev], [match(team(363, 'Chelsea', 'Chelsea', 'CHE'), leeds)])[0]) == 1


def _refusal(defect):
    ev, fixtures = event('epl', 'MCIFUL'), [match(CITY, FULHAM)]
    m = ev['markets']
    if defect == 'two_markets': del m[1]
    if defect == 'four_markets': m.append(dict(m[1], ticker=m[1]['ticker'] + 'X'))
    if defect == 'no_tie': m[2].update(yes_sub_title='Draw')
    if defect == 'tie_without_ticker': m[2].update(ticker='KXEPLGAME-26NOV21MCIFUL-DRW')
    if defect == 'two_ties': m[1].update(yes_sub_title='Tie', ticker='KXEPLGAME-26NOV21MCIFUL-TIE')
    if defect == 'title_mismatch': ev['title'] = 'Manchester City vs Everton'
    if defect == 'title_not_a_pair': ev['title'] = 'Manchester City v Fulham'
    if defect == 'extra_time_rules': m[0]['rules_primary'] = m[0]['rules_primary'].replace('after 90 minutes plus stoppage time', 'including extra time')
    if defect == 'inactive': m[1]['status'] = 'closed'
    if defect == 'scalar_market': m[0]['market_type'] = 'scalar'
    if defect == 'crossed': m[0].update(yes_bid_dollars='0.7000', yes_ask_dollars='0.7000')
    if defect == 'no_bid': m[2]['yes_bid_dollars'] = '0.0000'
    if defect == 'missing_ask': m[2]['yes_ask_dollars'] = None
    if defect == 'wide_one_leg': m[1].update(yes_bid_dollars='0.1000', yes_ask_dollars='0.1600')
    if defect == 'bad_ticker_date': ev['event_ticker'] = 'KXEPLGAME-MCIFUL'
    if defect == 'bad_month': ev['event_ticker'] = 'KXEPLGAME-26XXX21MCIFUL'
    if defect == 'two_days_late': fixtures = [match(CITY, FULHAM, '2026-11-23T15:00:00Z')]
    if defect == 'two_days_early': fixtures = [match(CITY, FULHAM, '2026-11-19T15:00:00Z')]
    if defect == 'started': fixtures = [match(CITY, FULHAM, '2026-11-18T11:00:00Z')]
    if defect == 'in_progress': fixtures = [match(CITY, FULHAM, status='in_progress')]
    if defect == 'final': fixtures = [match(CITY, FULHAM, status='final')]
    if defect == 'wrong_competition': fixtures = [match(CITY, FULHAM, competition='eng.fa')]
    if defect == 'same_team_twice': fixtures = [match(CITY, dict(CITY, name='Fulham', short_name='Fulham'))]
    if defect == 'two_fixtures': fixtures = [match(CITY, FULHAM), match(FULHAM, CITY, '2026-11-22T15:00:00Z', key='2')]
    if defect == 'naive_start': fixtures = [match(CITY, FULHAM, '2026-11-21T15:00')]
    return ev, fixtures


@pytest.mark.parametrize('defect', ['two_markets', 'four_markets', 'no_tie', 'tie_without_ticker', 'two_ties',
    'title_mismatch', 'title_not_a_pair', 'extra_time_rules', 'inactive', 'scalar_market', 'crossed', 'no_bid',
    'missing_ask', 'wide_one_leg', 'bad_ticker_date', 'bad_month', 'two_days_late', 'two_days_early', 'started',
    'in_progress', 'final', 'wrong_competition', 'same_team_twice', 'two_fixtures', 'naive_start'])
def test_soccer_refusals(defect):
    ev, fixtures = _refusal(defect)
    rows, stats = soccer([ev], fixtures)
    assert rows == [] and stats['matched'] == 0
    if defect == 'wide_one_leg':
        assert stats['dropped_wide'] == 1
    if defect == 'two_fixtures':
        assert stats['ambiguous'] == 1


def test_two_events_for_one_fixture_drop_both():
    ev = event('epl', 'MCIFUL')
    twin = deepcopy(ev)
    twin['event_ticker'] = 'KXEPLGAME-26NOV22MCIFUL'
    rows, stats = soccer([ev, twin], [match(CITY, FULHAM)])
    assert rows == [] and stats['ambiguous'] == 2


def test_an_empty_december_book_is_counted_wide_not_matched():
    coventry, ipswich = team(388, 'Coventry City', 'Coventry'), team(373, 'Ipswich Town', 'Ipswich')
    rows, stats = soccer([event('epl', 'COVIPS')], [match(coventry, ipswich, '2026-12-01T19:45:00Z')])
    assert rows == [] and stats['dropped_wide'] == 1


def test_whole_recorded_answer_against_one_fixture():
    rows, stats = soccer(events('epl'), [match(CITY, FULHAM)])
    assert [r['offer_id'] for r in rows] == ['KXEPLGAME-26NOV21MCIFUL']
    assert stats['events'] == 7 and stats['matched'] == 1 and len(stats['unmatched']) == 6


def tennis(evs, fixtures, competition='atp'):
    return xk.parse_tennis(evs, competition, fixtures, NOW)


def test_tennis_full_name_pair_three_days_after_the_ticker_date():
    ev = event('atp', 'SHEJOD')                      # ticker date Nov 17; ESPN schedules the match Nov 20
    rows, stats = tennis([ev], [tennis_match(JODAR, SHELTON)])
    row = rows[0]
    assert [o['label'] for o in row['outcomes']] == ['Rafael Jodar', 'Ben Shelton']       # ESPN participant order
    assert [(o['bid'], o['ask'], o['mid']) for o in row['outcomes']] == [(.33, .34, .335), (.66, .67, .665)]
    assert [o['participant_id'] for o in row['outcomes']] == [JODAR['id'], SHELTON['id']]
    assert row['start'] == '2026-11-20T13:00:00Z' and row['scope'] == 'full_game' and row['sport'] == 'tennis'
    assert row['event_id'] == 'atp:espn:atp:339-2026:9' and stats['matched'] == 1


@pytest.mark.parametrize('defect', ['last_names_only', 'one_name_differs', 'doubles_title', 'doubles_competition',
    'doubles_rules', 'slash_name', 'wrong_tour', 'five_days_late', 'two_days_early', 'started', 'final',
    'three_markets', 'same_name_twice', 'inactive', 'wide'])
def test_tennis_refusals(defect):
    ev, fixtures = event('atp', 'SHEJOD'), [tennis_match(SHELTON, JODAR)]
    m = ev['markets']
    if defect == 'last_names_only': fixtures = [tennis_match(player(1, 'Shelton'), player(2, 'Jodar'))]
    if defect == 'one_name_differs': fixtures = [tennis_match(SHELTON, player(3, 'Rafael Jodar Jr'))]
    if defect == 'doubles_title': ev['title'] += ' Doubles'
    if defect == 'doubles_competition': ev['product_metadata']['competition'] = 'ATP World Tour Finals Men Doubles'
    if defect == 'doubles_rules': m[0]['rules_primary'] = m[0]['rules_primary'].replace('Men Singles', 'Men Doubles')
    if defect == 'slash_name':
        m[0]['yes_sub_title'] = 'Ben Shelton / Taylor Fritz'
        fixtures = [tennis_match(player(1, 'Ben Shelton / Taylor Fritz'), JODAR)]
    if defect == 'wrong_tour': fixtures = [tennis_match(SHELTON, JODAR, competition='atp_challenger')]
    if defect == 'five_days_late': fixtures = [tennis_match(SHELTON, JODAR, '2026-11-22T13:00:00Z')]
    if defect == 'two_days_early': ev['event_ticker'] = 'KXATPMATCH-26NOV22SHEJOD'
    if defect == 'started': fixtures = [tennis_match(SHELTON, JODAR, '2026-11-18T12:00:00Z')]
    if defect == 'final': fixtures = [tennis_match(SHELTON, JODAR, status='final')]
    if defect == 'three_markets': m.append(dict(m[0], ticker='X', yes_sub_title='Carlos Alcaraz'))
    if defect == 'same_name_twice': m[1]['yes_sub_title'] = 'Ben Shelton'
    if defect == 'inactive': m[0]['status'] = 'finalized'
    if defect == 'wide': m[0].update(yes_bid_dollars='0.6000')
    assert tennis([ev], fixtures)[0] == []


def test_tennis_duplicate_open_events_for_a_pair_drop_both():
    ev = event('atp', 'SHEJOD')
    final = deepcopy(ev)
    final['event_ticker'] = 'KXATPMATCH-26NOV19SHEJOD'       # e.g. group match and final both open
    rows, stats = tennis([ev, final], [tennis_match(SHELTON, JODAR)])
    assert rows == [] and stats['ambiguous'] == 2
    # ... and one event that fits two scheduled fixtures of the pair is not guessed either.
    two = [tennis_match(SHELTON, JODAR), tennis_match(JODAR, SHELTON, '2026-11-21T13:00:00Z', key='10')]
    assert tennis([ev], two)[0] == []


def test_recorded_atp_answer_keeps_only_tight_books():
    names = ['Carlos Alcaraz', 'Flavio Cobolli', 'Daniil Medvedev', 'Frances Tiafoe', 'Ben Shelton', 'Rafael Jodar',
             'Alexander Zverev', 'Arthur Fils']
    fixtures = [tennis_match(player(i, names[i]), player(i + 1, names[i + 1]), key=str(i)) for i in range(0, 8, 2)]
    rows, stats = tennis(events('atp'), fixtures)
    assert sorted(r['offer_id'][-6:] for r in rows) == ['SHEJOD', 'ZVEFIL']
    assert stats['dropped_wide'] == 2 and stats['matched'] == 2 and stats['unmatched'] == []


# ---- collect: budget, switch-free core, holds ------------------------------------------------------------------

def _code(day):
    return day.strftime('%y') + day.strftime('%b').upper() + day.strftime('%d')


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path))
    monkeypatch.setattr(net, '_HOLD', {})
    return tmp_path


def _catalogue(store, sport='soccer', days=1, **kw):
    start = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=days)
    text = start.isoformat().replace('+00:00', 'Z')
    fixture = match(CITY, FULHAM, text, **kw) if sport == 'soccer' else tennis_match(SHELTON, JODAR, text, **kw)
    directory = store / sport
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'current-matches.json').write_text(json.dumps([fixture]), encoding='utf-8')
    name, suffix = ('epl', 'MCIFUL') if sport == 'soccer' else ('atp', 'SHEJOD')
    ev = event(name, suffix)
    old = ev['event_ticker'].split('-')[1][:7]
    text = json.dumps(ev).replace(old, _code(start.date()))
    return directory, json.loads(text)


class Source:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, **kw):
        self.calls.append((url, kw))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.parametrize('sport', ['soccer', 'tennis'])
def test_collect_one_request_one_matched_row(store, sport):
    directory, ev = _catalogue(store, sport)
    get = Source({'events': [ev], 'cursor': ''})
    rows, status = xk.collect(sport, get, directory)
    assert len(rows) == 1 and rows[0]['sport'] == sport and len(rows[0]['outcomes']) == (3 if sport == 'soccer' else 2)
    assert status['ok'] and status['reference'] and status['markets'] == 1 and status['requests'] == 1
    assert status['matched'] == 1 and 'truncated' not in status
    url, kw = get.calls[0]
    assert url == 'https://api.elections.kalshi.com/trade-api/v2/events' and kw['tries'] == 1 and kw['timeout'] <= 10
    assert kw['params'] == {'series_ticker': 'KXEPLGAME' if sport == 'soccer' else 'KXATPMATCH', 'status': 'open',
                            'with_nested_markets': 'true', 'limit': 100}
    assert 'headers' not in kw
    # The pacing mark is shared with the other sport's job through request-holds.db.
    assert 0 < expansion_sources.held_until('tennis', xk.HOST) - time.time() <= 2


@pytest.mark.parametrize('case', ['no_catalogue', 'other_competition', 'beyond_horizon', 'not_scheduled', 'started', 'oversize_catalogue'])
def test_collect_sends_nothing_without_a_relevant_fixture(store, case):
    directory = store / 'soccer'
    if case == 'other_competition': directory, _ = _catalogue(store, competition='esp.1')
    if case == 'beyond_horizon': directory, _ = _catalogue(store, days=9)
    if case == 'not_scheduled': directory, _ = _catalogue(store, status='in_progress')
    if case == 'started': directory, _ = _catalogue(store, days=-1)
    if case == 'oversize_catalogue':
        directory, _ = _catalogue(store)
        (directory / 'current-matches.json').write_text('[' + ' ' * 3_000_001 + ']', encoding='utf-8')
    get = Source()
    rows, status = xk.collect('soccer', get, directory)
    assert rows == [] and get.calls == [] and status['ok'] and status['skipped'] == 'no_fixture'


def test_unverified_series_are_never_requested(store, monkeypatch):
    start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    directory = store / 'tennis'
    directory.mkdir()
    (directory / 'current-matches.json').write_text(json.dumps(
        [tennis_match(SHELTON, JODAR, start, competition=c, key=c) for c in ('wta', 'atp_challenger', 'wta125')]), encoding='utf-8')
    get = Source()
    assert xk.collect('tennis', get, directory)[1]['skipped'] == 'no_fixture' and get.calls == []
    assert [k for k, v in xk.SERIES['tennis'].items() if v['verified']] == ['KXATPMATCH']
    assert [k for k, v in xk.SERIES['soccer'].items() if v['verified']] == ['KXEPLGAME']


def test_at_most_three_requests_and_truncation_is_reported(store):
    directory, ev = _catalogue(store)
    get = Source(*[{'events': [], 'cursor': 'more'} for _ in range(5)])
    rows, status = xk.collect('soccer', get, directory)
    assert len(get.calls) == 3 == status['requests'] and status['truncated'] is True and rows == []
    assert get.calls[1][1]['params']['cursor'] == 'more'


def test_wall_clock_stops_further_pages(store, monkeypatch):
    directory, ev = _catalogue(store)
    monkeypatch.setattr(xk, 'WALL_S', 0)
    get = Source({'events': [ev], 'cursor': ''})
    assert xk.collect('soccer', get, directory)[0] == [] and get.calls == []


@pytest.mark.parametrize('retry_after,least,most', [(None, 15 * 60, 15 * 60), (3600, 3600, 3600), (5, 15 * 60, 15 * 60)])
def test_429_persists_a_hold_and_the_next_run_sends_nothing(store, retry_after, least, most):
    directory, ev = _catalogue(store)
    url = xk.API + '/events'
    refusal = net.HttpError(429, url, '', not_before=time.time() + retry_after if retry_after else None)
    get = Source(refusal)
    rows, status = xk.collect('soccer', get, directory)
    assert rows == [] and status['ok'] is False and status['error'] == 'http_429' and len(get.calls) == 1
    remaining = expansion_sources.held_until('soccer', xk.HOST) - time.time()
    assert least - 5 <= remaining <= most + 5 and status['held_until']
    # A new process (empty in-memory holds), either sport: nothing is sent and nothing is carried forward.
    net._HOLD.clear()
    for sport in ('soccer', 'tennis'):
        again = Source({'events': [ev], 'cursor': ''})
        directory, _ = _catalogue(store, sport)
        rows, status = xk.collect(sport, again, directory)
        assert rows == [] and again.calls == [] and status['ok'] is False and status['error'] == 'held'
        assert status['held_until']


def test_403_is_a_six_hour_hold(store):
    directory, ev = _catalogue(store)
    get = Source(net.HttpError(403, xk.API + '/events', 'forbidden'))
    rows, status = xk.collect('soccer', get, directory)
    assert rows == [] and status['error'] == 'http_403' and len(get.calls) == 1
    assert abs(expansion_sources.held_until('soccer', xk.HOST) - time.time() - 6 * 3600) < 5


@pytest.mark.parametrize('failure', [net.HttpError(None, xk.API, 'timeout'), net.HttpError(500, xk.API, ''),
                                     ValueError('oversize response refused'), 'not a dict', {'events': 'x'}])
def test_other_failures_return_no_rows_and_no_long_hold(store, failure):
    directory, ev = _catalogue(store)
    get = Source(failure)
    rows, status = xk.collect('soccer', get, directory)
    assert rows == [] and status['ok'] is False and len(get.calls) == 1 and 'held_until' not in status
    assert expansion_sources.held_until('soccer', xk.HOST) - time.time() <= 2


class Streamed:
    """A requests answer as net sees it with stream=True: the body is produced only as it is read."""
    def __init__(self, total, declared=None, status=200, chunk=65536):
        self.status_code, self.total, self.chunk = status, total, chunk
        self.headers = {'Content-Length': str(declared)} if declared is not None else {}
        self.read, self.closed = 0, False

    def iter_content(self, size):
        while self.read < self.total:
            n = min(self.chunk, self.total - self.read)
            self.read += n
            yield b' ' * n

    @property
    def content(self):
        raise AssertionError('the whole body must never be buffered')

    text = content

    def close(self):
        self.closed = True


def _streamed(monkeypatch, *replies):
    replies, seen = list(replies), []

    class Session:
        def get(self, url, **kw):
            seen.append(kw)
            return replies.pop(0)
    monkeypatch.setattr(net, 'session', lambda: Session())
    return seen


def test_oversize_answer_stops_the_download_at_the_limit(store, monkeypatch):
    # 26 MB without a Content-Length (chunked / compressed): reading stops one chunk past the 2 MB limit.
    big = Streamed(26_000_000)
    seen = _streamed(monkeypatch, big)
    with pytest.raises(ValueError, match='oversize'):
        expansion_sources.get_limited('soccer', 'https://example.test/x', xk.MAX_BYTES, tries=1)
    assert seen[0]['stream'] is True and big.closed
    assert xk.MAX_BYTES < big.read <= xk.MAX_BYTES + 65536 and xk.MAX_BYTES == 2_000_000


def test_declared_oversize_answer_is_refused_before_any_body_byte(store, monkeypatch):
    big = Streamed(5_000_000, declared=5_000_000)
    _streamed(monkeypatch, big)
    with pytest.raises(net.Oversize):
        expansion_sources.get_limited('soccer', 'https://example.test/x', 100, tries=1)
    assert big.read == 0 and big.closed


def test_answer_within_the_limit_is_parsed_and_closed(store, monkeypatch):
    class Small(Streamed):
        def iter_content(self, size):
            yield b'{"ok":'
            yield b'1}'
    small = Small(8, declared=8)
    _streamed(monkeypatch, small)
    assert expansion_sources.get_limited('soccer', 'https://example.test/x', 100) == {'ok': 1}
    assert small.closed


def test_streamed_refusal_reads_only_the_start_of_the_error_body(store, monkeypatch):
    class Raw:
        def __init__(self): self.asked = []
        def read(self, n, decode_content=False):
            self.asked.append(n)
            return b'forbidden'
    refusal = Streamed(50_000_000, status=403)
    refusal.raw = Raw()
    _streamed(monkeypatch, refusal)
    with pytest.raises(net.HttpError) as e:
        expansion_sources.get_limited('soccer', 'https://example.test/x', 100, tries=1)
    assert e.value.status == 403 and e.value.body == 'forbidden' and refusal.raw.asked == [300]
    assert refusal.read == 0 and refusal.closed


def test_slow_body_ends_at_the_timeout(store, monkeypatch):
    clock = [1000.0]
    class Drip(Streamed):
        def iter_content(self, size):
            for _ in range(100):
                clock[0] += 10
                yield b' '
    drip = Drip(100)
    _streamed(monkeypatch, drip)
    monkeypatch.setattr(net.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(net.time, 'sleep', lambda s: None)
    with pytest.raises(net.HttpError) as e:
        net.get_json_limited('https://example.test/x', 1000, timeout=25, tries=1)
    assert e.value.status is None and drip.closed and clock[0] <= 1040


def test_row_cap_keeps_the_nearest_starts(store, monkeypatch):
    directory, ev = _catalogue(store, 'tennis')
    monkeypatch.setattr(xk, 'MAX_ROWS', 2)
    base = datetime.now(timezone.utc).replace(microsecond=0)
    fixtures, evs = [], []
    for i in range(4):
        a, b = player(100 + i, f'Alpha Player{i}'), player(200 + i, f'Beta Player{i}')
        start = (base + timedelta(hours=30 - i)).isoformat().replace('+00:00', 'Z')
        fixtures.append(tennis_match(a, b, start, key=str(i)))
        e = deepcopy(ev)
        e['event_ticker'] = f"{ev['event_ticker'][:-6]}AA{i:04d}"
        for m, p in zip(e['markets'], (a, b)):
            m.update(yes_sub_title=p['name'], ticker=f"{e['event_ticker']}-{p['id'][-3:]}")
        evs.append(e)
    (directory / 'current-matches.json').write_text(json.dumps(fixtures), encoding='utf-8')
    rows, status = xk.collect('tennis', Source({'events': evs, 'cursor': None}), directory)
    assert [r['teams'][0] for r in rows] == ['Alpha Player3', 'Alpha Player2'] and status['markets'] == 2


def test_hold_never_shortens_an_existing_hold(store):
    expansion_sources.hold('soccer', 'example.test', time.time() + 500)
    expansion_sources.hold('tennis', 'example.test', time.time() + 5)
    net._HOLD.clear()
    assert expansion_sources.held_until('soccer', 'example.test') - time.time() > 490
    assert expansion_sources.held_until('soccer', 'other.test') == 0.0


def test_switch_reads_only_its_own_sport(monkeypatch):
    monkeypatch.delenv('SCOUT_SOCCER_KALSHI', raising=False)
    monkeypatch.setenv('SCOUT_TENNIS_KALSHI', '0')
    assert xk.switched_on('soccer') and not xk.switched_on('tennis')
