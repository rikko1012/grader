"""Kalshi + Polymarket match prices (2026-11-18): weight-0 baseline, kalshi.py quote rule, exact identity, never in
board.json, 500 KB guard, last good prices kept, 15-minute freshness by each price's own check time."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from sports import expansion_exchanges as ex, net

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
CHECK = '2026-11-19T12:00:00Z'


def _iso(dt):
    return dt.isoformat().replace('+00:00', 'Z')


def _espn(status='scheduled', start='2026-11-21T12:30:00Z', comp='eng.1'):
    return {'event_id': f'soccer:espn:{comp}:7:7', 'competition': comp, 'start': start, 'status': status,
            'participants': [{'id': 'soccer:team:espn:382', 'name': 'Manchester City', 'short_name': 'Man City'},
                             {'id': 'soccer:team:espn:370', 'name': 'Fulham'}]}


def _kalshi(bids=((0.69, 0.70), (0.17, 0.18), (0.13, 0.14)), names=('Manchester City', 'Tie', 'Fulham')):
    return {'event_ticker': 'KXEPLGAME-26NOV21MCIFUL', 'markets': [
        {'ticker': f'KXEPLGAME-26NOV21MCIFUL-{i}', 'status': 'active', 'yes_sub_title': n,
         'yes_bid_dollars': f'{b:.4f}', 'yes_ask_dollars': f'{a:.4f}', 'occurrence_datetime': '2026-11-21T15:30:00Z'}
        for i, (n, (b, a)) in enumerate(zip(names, bids))]}


def test_quote_rule_matches_kalshi_py():
    assert ex.quote('0.69', '0.70') == {'bid': 0.69, 'ask': 0.70, 'mid': 0.695}
    assert ex.quote('0.60', '0.66') is None                         # spread over 5 cents
    assert ex.quote(None, '0.66') is None and ex.quote('0', '0.03') is None   # one-sided
    assert ex.quote('0.70', '0.69') is None


def test_kalshi_soccer_joins_on_ticker_date_and_takes_the_start_from_espn():
    eid, entry = ex.kalshi_soccer(_kalshi(), 'eng.1', [_espn()], CHECK)
    assert eid == 'soccer:espn:eng.1:7:7' and entry['start'] == '2026-11-21T12:30:00Z'   # not Kalshi's 15:30
    assert [(o['label'], o['participant_id'], o['mid']) for o in entry['outcomes']] == [
        ('Manchester City', 'soccer:team:espn:382', 0.695), ('Draw', None, 0.175), ('Fulham', 'soccer:team:espn:370', 0.135)]


def test_a_wide_quote_is_left_out_and_no_quotable_side_means_no_entry():
    _eid, entry = ex.kalshi_soccer(_kalshi(bids=((0.60, 0.70), (0.17, 0.18), (0.13, 0.14))), 'eng.1', [_espn()], CHECK)
    assert [o['label'] for o in entry['outcomes']] == ['Draw', 'Fulham']
    assert ex.kalshi_soccer(_kalshi(bids=((0.1, 0.9),) * 3), 'eng.1', [_espn()], CHECK) is None


@pytest.mark.parametrize('case', ['played', 'alias_miss', 'two_days_off', 'two_events', 'other_league'])
def test_kalshi_soccer_abstains(case):
    espn = [_espn()]
    names = ('Manchester City', 'Tie', 'Fulham')
    if case == 'played':
        espn = [_espn(status='final')]                  # Kalshi "open" still listed a match played the day before
    if case == 'alias_miss':
        names = ('Man. City FC', 'Tie', 'Fulham')
    if case == 'two_days_off':
        espn = [_espn(start='2026-11-23T12:30:00Z')]
    if case == 'two_events':
        espn = [_espn(), dict(_espn(start='2026-11-22T12:30:00Z'), event_id='other')]
    if case == 'other_league':
        espn = [_espn(comp='eng.2')]
    assert ex.kalshi_soccer(_kalshi(names=names), 'eng.1', espn, CHECK) is None


def test_a_verified_alias_joins(monkeypatch):
    monkeypatch.setitem(ex.KALSHI_ALIASES, ('eng.1', 'Man. City FC'), 'soccer:team:espn:382')
    assert ex.kalshi_soccer(_kalshi(names=('Man. City FC', 'Tie', 'Fulham')), 'eng.1', [_espn()], CHECK)


def _poly(question, bid, ask, start='2026-11-21 12:30:00+00', mid='1'):
    return {'id': mid, 'question': question, 'bestBid': bid, 'bestAsk': ask, 'gameStartTime': start,
            'closed': False, 'acceptingOrders': True, 'outcomes': '["Yes", "No"]'}


def test_polymarket_soccer_needs_every_team_by_name_or_alias_and_filters_start_itself(monkeypatch):
    rows = [_poly('Will Manchester City FC win on 2026-11-21?', 0.68, 0.70, mid='a'),
            _poly('Will Manchester City FC vs. Fulham FC end in a draw?', 0.17, 0.19, mid='b'),
            _poly('Will Fulham FC win on 2026-11-21?', 0.12, 0.14, mid='c'),
            _poly('Will Old FC win on 2026-10-10?', 0.5, 0.52, start='2026-10-10 12:00:00+00', mid='d')]
    assert ex.poly_soccer(rows, 'eng.1', [_espn()], CHECK, NOW) == {}            # "... FC" names: no alias, abstain
    monkeypatch.setitem(ex.POLY_ALIASES, ('eng.1', 'Manchester City FC'), 'soccer:team:espn:382')
    monkeypatch.setitem(ex.POLY_ALIASES, ('eng.1', 'Fulham FC'), 'soccer:team:espn:370')
    out = ex.poly_soccer(rows, 'eng.1', [_espn()], CHECK, NOW)
    assert [(o['label'], o['mid']) for o in out['soccer:espn:eng.1:7:7']['outcomes']] == [
        ('Manchester City FC', 0.69), ('Draw', 0.18), ('Fulham FC', 0.13)]


def test_tennis_pairs_are_exact_and_unique():
    m = {'event_id': 'atp:365scores:atp_challenger:1:2', 'competition': 'atp_challenger', 'status': 'scheduled',
         'start': '2026-11-19T20:45:00Z', 'participants': [{'id': 'p1', 'name': 'Mikael Arseneault'},
                                                            {'id': 'p2', 'name': 'Clement Chidekh'}]}
    ev = [{'markets': [{'id': 9, 'sportsMarketType': 'moneyline', 'closed': False, 'bestBid': 0.016, 'bestAsk': 0.018,
                        'outcomes': '["Mikael Arseneault", "Clement Chidekh"]', 'gameStartTime': '2026-11-19 20:45:00+00'}]}]
    out = ex.poly_tennis(ev, ('atp', 'atp_challenger'), [m], CHECK, NOW)
    o = out[m['event_id']]['outcomes']
    assert (o[0]['participant_id'], o[0]['mid'], o[1]['participant_id'], o[1]['mid']) == ('p1', 0.017, 'p2', 0.983)
    assert ex.poly_tennis(ev, ('atp', 'atp_challenger'), [m, dict(m, event_id='dup')], CHECK, NOW) == {}
    k = {'event_ticker': 'KXATPCHALLENGERMATCH-26NOV19ARSCHI', 'markets': [
        {'ticker': 'a', 'status': 'active', 'yes_sub_title': 'Mikael Arseneault', 'yes_bid_dollars': '0.02', 'yes_ask_dollars': '0.03'},
        {'ticker': 'b', 'status': 'active', 'yes_sub_title': 'Clement Chidekh', 'yes_bid_dollars': '0.97', 'yes_ask_dollars': '0.98'}]}
    eid, entry = ex.kalshi_tennis(k, ('atp_challenger',), [m], CHECK)
    assert eid == m['event_id'] and [x['mid'] for x in entry['outcomes']] == [0.025, 0.975]
    assert ex.kalshi_tennis(k, ('wta125',), [m], CHECK) is None


def test_freshness_is_by_each_prices_own_check_time():
    assert ex.fresh({'checked_at': _iso(NOW - timedelta(minutes=14))}, NOW)
    assert not ex.fresh({'checked_at': _iso(NOW - timedelta(minutes=16))}, NOW)
    assert not ex.fresh({'checked_at': _iso(NOW + timedelta(minutes=1))}, NOW)
    assert not ex.fresh({}, NOW)


def test_a_429_keeps_the_last_prices_and_rotation_moves_on(tmp_path):
    saved = {'soccer:espn:eng.1:7:7': {'kalshi': {'venue': 'kalshi', 'checked_at': CHECK, 'start': '2099-01-01T00:00:00Z',
                                                  'outcomes': [{'label': 'Fulham', 'mid': 0.13}]}}}
    (tmp_path / 'market-prices.json').write_text(json.dumps(saved))
    asked = []

    def get(url, params):
        asked.append(params.get('series_ticker') or params.get('tag_id'))
        if len(asked) > 3:
            raise StopIteration
        raise net.HttpError(429, url, '')
    with pytest.raises(StopIteration):
        ex.collect('soccer', get, tmp_path, {}, {})
    assert ex.load(tmp_path) == saved
    state = json.loads((tmp_path / 'exchanges-state.json').read_text())
    assert len(state) == 3 and len(set(asked[:3])) == 3
    again = []
    with pytest.raises(StopIteration):
        ex.collect('soccer', lambda url, p: again.append(p.get('series_ticker') or p.get('tag_id')) or (_ for _ in ()).throw(StopIteration), tmp_path, {}, {})
    assert again[0] not in asked[:3]                     # the failed ones moved to the back of the rotation


def test_offered_competitions_are_asked_first(tmp_path):
    asked = []

    def get(url, params):
        asked.append(params.get('series_ticker') or params.get('tag_id'))
        if len(asked) >= 2:
            raise StopIteration
        return {'events': []} if 'series_ticker' in params else []
    with pytest.raises(StopIteration):
        ex.collect('soccer', get, tmp_path, {}, {'ita.2': CHECK})
    assert asked[0] == 'KXSERIEBGAME' and len(asked) == 2


def test_the_price_file_stays_under_500_kb_and_drops_started_events(tmp_path):
    big = {f'e{i}': {'kalshi': {'checked_at': CHECK, 'start': _iso(NOW + timedelta(hours=i)), 'outcomes':
                                [{'label': 'x' * 900, 'mid': 0.5}]}} for i in range(800)}
    big['old'] = {'kalshi': {'checked_at': CHECK, 'start': _iso(NOW - timedelta(hours=1)), 'outcomes': []}}
    kept = ex.save(tmp_path, big, NOW)
    assert 'old' not in kept and (tmp_path / 'market-prices.json').stat().st_size <= ex.PRICES_BYTES
    assert 'e0' in kept and 'e799' not in kept                 # latest-starting events are left out first


def test_prices_never_touch_the_board_version(tmp_path, monkeypatch):
    """board.json is written by the quotes job only; this module never writes it."""
    import inspect
    src = inspect.getsource(ex)
    assert 'board.json' not in src.replace('never in board.json', '') and 'expansion_board' not in src


def test_changes_are_logged_once(tmp_path):
    found = {'e': {'venue': 'kalshi', 'checked_at': CHECK,
                   'outcomes': [{'label': 'A', 'market_id': 'm', 'bid': 0.4, 'ask': 0.42, 'mid': 0.41}]}}
    assert ex.log(tmp_path, found) == 1 and ex.log(tmp_path, found) == 0
    found['e']['outcomes'][0].update(bid=0.41, mid=0.415)
    assert ex.log(tmp_path, found) == 1
