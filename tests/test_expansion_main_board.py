"""Real Grader Handler + shared UI: new sports reuse the existing board/card.
Only source data are fixtures; authentication, adapter, filters and UI are real.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

expect = pytest.importorskip('playwright.sync_api').expect

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


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_saved_forecast_uses_existing_readable_board_and_card(ctx, env, expansion, size):
    doc=expansion['soccer'];offer=doc['offers'][0];facts=doc['research'][adapter._key(offer)]
    forecast=forecast_fixture(offer,facts)
    doc['forecasts']={forecast_key(offer):forecast}
    p=open_page(ctx,env,'/#scout/board?sport=soccer',size)
    expect(p.locator('#hub-rows')).to_contain_text('Verified Player',timeout=30000)
    p.locator('#hub-rows .h-player').first.click()
    expect(p.locator('#hub-detail')).to_contain_text('Model estimate',timeout=20000)
    expect(p.locator('#hub-detail')).to_contain_text(forecast['grade'])
    expect(p.locator('#hub-detail')).to_contain_text('Opponent 0')
    p.locator('#hub-detail [data-hub="card-tab"][data-id="stats"]').click()
    expect(p.locator('#hub-detail')).to_contain_text('20 completed games')
    expect(p.locator('#hub-detail')).to_contain_text('Minutes')
    no_h_scroll(p)


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
    assert 'await' not in p.locator('#hub-health').inner_text()          # ST-4 2026-11-23: no member status note
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-panel', timeout=20000)
    expect(p.locator('#hub-detail')).to_contain_text('Opponent 0', timeout=20000)
    assert 'Regulation time' in p.locator('#hub-detail').inner_text() or 'regulation time' in p.locator('#hub-detail').inner_text()
    assert p.locator('#hub-detail [data-hub="detail-add"]').count() == 0
    p.locator('#hub-detail [data-hub="card-tab"][data-id="stats"]').click()
    expect(p.locator('#hub-detail')).to_contain_text('Minutes', timeout=15000)
    expect(p.locator('#hub-detail')).to_contain_text('82')
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
    expect(p.locator('#hub-detail')).to_contain_text('Hard', timeout=15000)
    expect(p.locator('#hub-detail')).to_contain_text('Double Faults')
    no_h_scroll(p)
    assert not p.errors


@pytest.mark.parametrize('size',[DESK,PHONE])
def test_main_moneylines_three_way_soccer_and_tennis(ctx,env,expansion,size):
 p=open_page(ctx,env,'/?view=classic#moneylines',size)
 p.locator('[data-mlsport=soccer]').click()
 p.wait_for_function("document.querySelector('#boardout').textContent.includes('Draw')")
 text=p.locator('#boardout').inner_text()
 assert '+220' in text and 'BetRivers' in text
 assert '-1000' not in text
 no_h_scroll(p)
 p.locator('[data-mlsport=tennis]').click()
 p.wait_for_function("document.querySelector('#boardout').textContent.includes('-170')")
 assert 'Draw' not in p.locator('#boardout').inner_text()
 no_h_scroll(p)
 assert not p.errors


def forecast_key(o):
    import hashlib
    return hashlib.sha256(json.dumps([o[k] for k in ('sport','platform','offer_id','revision')],separators=(',',':')).encode()).hexdigest()


def forecast_fixture(offer,facts):
    return {'id':'saved-fixture-forecast','identity':facts['identity'],'sport':offer['sport'],
            'stat':offer['stat'],'scope':offer['scope'],'line':offer['line'],'platform':offer['platform'],
            'offer_id':offer['offer_id'],'offer_revision':offer['revision'],'start':offer['start'],
            'made_at':(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),'p_over':.7,'p_under':.3,'p_push':0,
            'p_side':.7,'side':'over','projection':3.7,'range80':[1,6],'grade':'A',
            'sample':20,'model_version':'fixture-model','grade_reason':[],
            'stake_reason':'No stake assigned.','context_used':{}}


def test_adapter_refuses_forecast_from_another_identity_or_revision():
    import copy
    doc=source('soccer');offer=doc['offers'][0];facts=doc['research'][adapter._key(offer)]
    f=forecast_fixture(offer,facts);key=forecast_key(offer)
    assert adapter._forecast(offer,facts,{key:f})==f
    for field,value in [('scope','first_half'),('stat','goals'),('offer_revision','other'),('line',1.5),('sport','tennis')]:
        bad=copy.deepcopy(f);bad[field]=value
        assert adapter._forecast(offer,facts,{key:bad}) is None
    bad=copy.deepcopy(f);bad['identity']['event_id']='other'
    assert adapter._forecast(offer,facts,{key:bad}) is None


@pytest.mark.parametrize('sport',adapter.SPORTS)
def test_board_and_direct_cards_exclude_nonstandard_lines(expansion,sport):
    import server
    doc=expansion[sport]
    standard=doc['offers'][0]
    for variant in ('demon','goblin','alternate','unknown',None):
        doc['offers'].append(dict(standard,offer_id='alt-'+str(variant),variant=variant))
    snap=adapter.snapshot(server,sport)
    records=json.loads(snap['body'])['records']
    assert len(records)==2
    assert all(r['market']['line_type']=='standard' for r in records)
    assert all(not x['offer']['offer_id'].startswith('alt-') for x in snap['index'].values())
    # A previously opened card also closes if its source becomes an alternate.
    rid=records[0]['offer']['offer_id']
    standard['variant']='demon'
    assert adapter.line_doc(server,sport,'prizepicks',rid)[0]==404


@pytest.mark.parametrize('sport',adapter.SPORTS)
@pytest.mark.parametrize('size',[DESK,PHONE])
def test_unoffered_model_direction_is_clear_without_reversing_the_pick(ctx,env,expansion,sport,size):
    doc=expansion[sport];offer=doc['offers'][0];offer['sides']={'over':{}}
    facts=doc['research'][adapter._key(offer)]
    f=forecast_fixture(offer,facts)
    f.update(side='under',p_under=.7,p_over=.3,projection=1.3,grade=None,
             grade_reason=['Model side not offered on this line.'])
    doc['forecasts']={forecast_key(offer):f}
    assert adapter._forecast(offer,facts,doc['forecasts'])==f
    p=open_page(ctx,env,'/#scout/board?sport='+sport,size)
    expect(p.locator('#hub-rows')).to_contain_text('Not offered',timeout=30000)
    p.locator('#hub-rows .h-player').first.click()
    expect(p.locator('#hub-detail')).to_contain_text('1.3',timeout=20000)
    no_h_scroll(p)
    # decision 1 (2026-11-20): the offered side with its own (low) chance is the honest grade of a one-sided line
    offered=dict(f,side='over',p_side=.3,grade='Lean')
    assert adapter._forecast(offer,facts,{forecast_key(offer):offered})==offered
    both=dict(offer,sides={'over':{},'under':{}})
    assert adapter._forecast(both,facts,{forecast_key(both):offered}) is None     # two sides: the higher one only


def _zero_game_forecast(doc):
    """soccer-grades-1120: a thin-history (population prior) forecast for the 'empty' offer, whose card has no saved game."""
    offer = next(o for o in doc['offers'] if o['offer_id'] == 'empty')
    research = {'identity': {'event_id': 'soccer:event:2', 'player_id': 'soccer:player:2', 'competition': 'eng.1'},
                'scope': 'regulation', 'games': [], 'stats': [], 'context': {}}
    doc['research'][adapter._key(offer)] = research
    f = forecast_fixture(offer, research)
    f.update(id='thin-fixture', side='under', p_over=.3, p_under=.7, p_side=.7, grade='A', sample=0, projection=1.4,
             range80=[0, 3], units=0.0, thin_model={'version': 'thin-1', 'games': 0, 'tier': 'passed'},
             stake_reason='0U: fewer than 5 past games - population-prior model passed its walk-forward test.')
    doc.setdefault('forecasts', {})[forecast_key(offer)] = f
    return offer, f


def test_zero_game_lettered_card_and_drawer_doc(expansion):
    import server
    adapter.reset()
    offer, f = _zero_game_forecast(expansion['soccer'])
    snap = adapter.snapshot(server, 'soccer')
    recs = {r['player']['name']: r for r in json.loads(snap['body'])['records']}
    r = recs['Unsupported Player']
    assert r['scout']['grade'] == 'A' and r['scout']['units'] == 0.0
    assert r['scout']['stake_reason'] == 'Stake 0U \u00b7 under 5 past games' and r['history']['avg'] is None
    assert r['history']['l10'] == {'n': 0, 'over': 0, 'under': 0, 'push': 0}
    st, card = adapter.line_doc(server, 'soccer', 'prizepicks', r['offer']['offer_id'])
    assert st == 200 and card['history']['series'] == [] and card['history']['avg'] is None
    assert card['forecast']['grade'] == 'A'


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_zero_game_lettered_line_renders_row_and_drawer(ctx, env, expansion, size):
    adapter.reset()
    offer, f = _zero_game_forecast(expansion['soccer'])
    p = open_page(ctx, env, '/#scout/board?sport=soccer', size)
    expect(p.locator('#hub-rows')).to_contain_text('Unsupported Player', timeout=30000)
    row = p.locator('#hub-rows .h-player', has_text='Unsupported Player').first
    row.click()
    expect(p.locator('#hub-detail')).to_contain_text('Model estimate', timeout=20000)
    expect(p.locator('#hub-detail')).to_contain_text('Under')
    text = p.locator('#hub-detail').inner_text()
    assert 'NaN' not in text and 'undefined' not in text
    no_h_scroll(p)


@pytest.mark.parametrize('sport', adapter.SPORTS)
def test_card_forecast_sample_unit_matches_the_board_record(expansion, sport):
    """sweep 2026-11-23 ST-3: the tennis card said 'N completed games' while the board record says 'N completed matches'
    (the sample counts matches). Soccer keeps its exact wording."""
    import hashlib
    import server
    adapter.reset()
    doc = expansion[sport]; offer = doc['offers'][0]; facts = doc['research'][adapter._key(offer)]
    doc['forecasts'] = {forecast_key(offer): forecast_fixture(offer, facts)}
    snap = adapter.snapshot(server, sport)
    rid = 'e-' + hashlib.sha256(offer['offer_id'].encode()).hexdigest()[:24]
    r = next(x for x in json.loads(snap['body'])['records'] if x['offer']['offer_id'] == rid)
    unit = 'matches' if sport == 'tennis' else 'games'
    assert f'20 completed {unit}' in r['scout']['validation']['detail']
    st, card = adapter.line_doc(server, sport, 'prizepicks', rid)
    rows = {x['label']: x['value'] for x in card['context']['rows'] if x['group'] == 'Used in forecast'}
    assert st == 200 and rows['Forecast sample'] == f'20 completed {unit}'
    assert rows['Prediction method'] == ('Match-to-match' if sport == 'tennis' else 'Game-to-game') + ' variation and historical scoring rate'


def test_st1_saved_forecast_older_than_a_day_keeps_its_letter_before_kickoff():
    """sweep 2026-11-23 ST-1: a frozen forecast keeps its first-issue made_at for as long as its offer revision holds, so the
    old 24 h made_at limit dropped live letters ~17 h before kickoff. Freshness is the publication time (forecasts_at)."""
    now = datetime.now(timezone.utc)
    doc = source('tennis'); offer = doc['offers'][0]; facts = doc['research'][adapter._key(offer)]
    offer['start'] = (now + timedelta(hours=17)).isoformat()
    f = forecast_fixture(offer, facts); f['made_at'] = (now - timedelta(hours=30)).isoformat()
    key = forecast_key(offer)
    assert adapter._forecast(offer, facts, {key: f}) == f                                   # older sports service: no forecasts_at
    assert adapter._forecast(offer, facts, {key: f}, (now - timedelta(minutes=3)).isoformat()) == f
    assert adapter._forecast(offer, facts, {key: f}, (now - timedelta(hours=25)).isoformat()) is None   # publisher stopped
    assert adapter._forecast(offer, facts, {key: f}, 'not a time') is None
    future = dict(f, made_at=(now + timedelta(minutes=5)).isoformat())
    assert adapter._forecast(offer, facts, {key: future}) is None                           # never a future issue time
    late = dict(f, made_at=(now + timedelta(hours=18)).isoformat())
    assert adapter._forecast(offer, facts, {key: late}) is None


def test_st1_board_reads_the_publication_time(expansion):
    import hashlib
    import server
    adapter.reset()
    now = datetime.now(timezone.utc)
    doc = expansion['tennis']; offer = doc['offers'][0]; facts = doc['research'][adapter._key(offer)]
    f = forecast_fixture(offer, facts); f['made_at'] = (now - timedelta(hours=30)).isoformat()
    doc['forecasts'] = {forecast_key(offer): f}
    doc['forecasts_at'] = (now - timedelta(minutes=2)).isoformat()
    rid = 'e-' + hashlib.sha256(offer['offer_id'].encode()).hexdigest()[:24]
    def rec():
        adapter._build(server, 'tennis')         # not srv.shared's 20 s cache: one fresh build per case
        return next(r for r in json.loads(adapter._SNAP['tennis']['body'])['records'] if r['offer']['offer_id'] == rid)
    assert rec()['scout']['grade'] == 'A'
    doc['forecasts_at'] = (now - timedelta(hours=26)).isoformat()
    rec = rec()
    assert rec['scout']['grade'] is None
