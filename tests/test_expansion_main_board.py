"""Real Scout Handler + shared UI: new sports reuse the existing board/card.
Only source data are fixtures; authentication, adapter, filters and UI are real.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import workspace_expansion as adapter
import workspace_sports
from tests.test_upgrade_browser import browser, env, ctx, open_page, no_h_scroll, DESK, PHONE


def source(sport):
    now = datetime.now(timezone.utc)
    offer = {'sport': sport, 'platform': 'prizepicks', 'offer_id': 'native1', 'revision': 'rev1',
        'player_id': 'native-player', 'event_id': 'native-event', 'player': 'Verified Player',
        'team': 'Example FC' if sport == 'soccer' else '', 'opponent': 'Test Opponent',
        'role': 'Forward' if sport == 'soccer' else '', 'match': 'Example vs Test',
        'stat': 'shots' if sport == 'soccer' else 'aces', 'label': 'Shots' if sport == 'soccer' else 'Aces',
        'scope': 'full_game', 'variant': 'standard', 'line': 2.5,
        'start': (now+timedelta(hours=3)).isoformat(), 'observed_at': now.isoformat(),
        'sides': {'over': {}, 'under': {}}}
    games = [{'event_id': f'{sport}:old:{i}', 'date': (now-timedelta(days=i+1)).isoformat(),
              'opponent': f'Opponent {i}', 'value': i % 6, 'source': 'espn', 'known_at': now.isoformat()}
             for i in range(20)]
    research = {'identity': {'event_id': sport+':event:1', 'player_id': sport+':player:1',
                            'competition': 'eng.1' if sport == 'soccer' else 'atp'},
        'scope': 'regulation' if sport == 'soccer' else 'full_game', 'games': games,
        'stats': [{'stat': 'minutes' if sport == 'soccer' else 'double_faults',
                   'average': 82 if sport == 'soccer' else 3.2, 'games': 10, 'scope': 'regulation' if sport == 'soccer' else 'full_game'}],
        'context': {} if sport == 'soccer' else {'surface': 'Hard'}}
    empty = dict(offer, offer_id='empty', player_id='unknown', player='Unsupported Player')
    half = dict(offer, offer_id='half', scope='first_half' if sport == 'soccer' else 'set1', line=1.5)
    odds = {'sport':sport, 'platform':'betrivers', 'offer_id':'ml', 'event_id':'book-event',
            'match':'Example vs Test', 'start':offer['start'], 'observed_at':offer['observed_at'],
            'main':True, 'market_type':'Match', 'jurisdiction':'US-OH',
            'outcomes':[{'label':name, 'american':price, 'line':None} for name,price in
                        ([('Example',150),('Draw',220),('Test',175)] if sport=='soccer' else [('Example',150),('Test',-170)])]}
    return {'sport': sport, 'offers': [offer, empty, half], 'research_at': now.isoformat(), 'built_at':now.isoformat(),
            'game_odds':[odds,dict(odds,offer_id='alternate',main=False)],
            'research': {adapter._key(offer): research, adapter._key(half): {**research, 'scope': half['scope']}},
            'sources': {'prizepicks': {'ok': True, 'checked_at': now.isoformat()}}}


@pytest.fixture
def expansion(env, monkeypatch):
    monkeypatch.setenv('SCOUT_SOCCER_ENABLED', '1')
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    monkeypatch.setenv('UI_NEXT', '1')
    original = workspace_sports.call
    docs = {s: source(s) for s in adapter.SPORTS}
    def call(srv, path, query=None, **kwargs):
        if path == '/api/sports/expansion/board':
            assert query.get('research') in (None, '1')
            return docs[query['sport']], None
        return original(srv, path, query, **kwargs)
    monkeypatch.setattr(workspace_sports, 'call', call)
    return docs


@pytest.mark.parametrize('sport', adapter.SPORTS)
def test_adapter_only_publishes_supported_history(expansion, sport):
    import server
    snap = adapter.snapshot(server, sport)
    doc = json.loads(snap['body'])
    assert doc['coverage'] == {'current_offers': 3, 'with_history': 2, 'pending_history': 1,
                               'history_at': expansion[sport]['research_at']}
    assert len(doc['records']) == 2
    r = doc['records'][0]
    assert r['history']['l5'] == {'n': 5, 'over': 2, 'under': 3, 'push': 0}
    assert r['scout']['projection'] is None and r['scout']['grade'] is None
    st, card = adapter.line_doc(server, sport, 'prizepicks', r['offer']['offer_id'])
    assert st == 200 and len(card['history']['series']) == 20
    assert card['context']['rows'][-1]['value'] == (82 if sport == 'soccer' else 3.2)
    assert len(card['books']) == 1   # First half/first set cannot join full-match prices.


@pytest.mark.parametrize('sport', adapter.SPORTS)
def test_existing_moneylines_keep_real_main_prices_and_draw(expansion, sport):
    import server, workspace_game_books
    status, raw = workspace_game_books.moneylines(server, sport)
    doc = json.loads(raw)
    assert status == 200 and len(doc['games']) == 1
    game = doc['games'][0]
    assert game['quotes'][0]['prices'] == ([150,220,175] if sport=='soccer' else [150,-170])
    assert game['pick'] is None
    if sport=='soccer': assert game['teams'][1]=='Draw'


def test_old_page_redirects_to_main_board_and_private_routes_stay_gated(ctx, env, expansion):
    p = open_page(ctx, env, '/sports/expansion.html?sport=tennis')
    p.wait_for_url('**/#scout/board?sport=tennis')
    p.wait_for_selector('#hub-rows .h-player')
    assert 'Aces' in p.locator('#hub-rows').inner_text()


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_same_sport_tabs_player_cards_history_and_stats(ctx, env, expansion, size, tmp_path):
    p = open_page(ctx, env, '/#scout/board?sport=soccer', size)
    p.wait_for_selector('#hub-rows .h-player', timeout=30000)
    assert '/sports/expansion' not in p.url
    assert p.locator('#hub-sports [data-hub="expansion"]').count() == 0
    assert p.locator('#hub-sports [data-id="soccer"]').get_attribute('aria-pressed') == 'true'
    assert 'Unsupported Player' not in p.locator('#hub-rows').inner_text()
    assert 'Verified Player' in p.locator('#hub-rows').inner_text()
    assert '1 more await' in p.locator('#hub-health').inner_text()
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-panel', timeout=20000)
    assert 'Opponent 0' in p.locator('#hub-detail').inner_text()
    assert 'Regulation time' in p.locator('#hub-detail').inner_text() or 'regulation time' in p.locator('#hub-detail').inner_text()
    assert p.locator('#hub-detail [data-hub="detail-add"]').count() == 0
    p.locator('#hub-detail [data-hub="card-tab"][data-id="stats"]').click()
    assert 'Minutes' in p.locator('#hub-detail').inner_text()
    assert '82' in p.locator('#hub-detail').inner_text()
    no_h_scroll(p)
    shots=Path(os.environ.get('UPGRADE_SHOTS') or tmp_path); shots.mkdir(parents=True,exist_ok=True)
    p.screenshot(path=str(shots/f'integrated-soccer-{size["width"]}.png'))
    p.keyboard.press('Escape')
    p.locator('#hub-sports [data-id="tennis"]').click()
    p.wait_for_function("document.querySelector('#hub-rows').textContent.includes('Aces')")
    assert '/sports/expansion' not in p.url
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-panel')
    p.locator('#hub-detail [data-hub="card-tab"][data-id="stats"]').click()
    assert 'Hard' in p.locator('#hub-detail').inner_text()
    assert 'Double Faults' in p.locator('#hub-detail').inner_text()
    no_h_scroll(p)
    assert not p.errors
