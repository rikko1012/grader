"""Real HTTP draft generation and rechecks at desktop/mobile; no production writes."""
import json
import os
from pathlib import Path
import pytest
import workspace_ev as we
import workspace_markets as wm
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE
from tests.test_hub_shell_browser import market_doc, mt_page


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_market_generate_pair_single_and_changed_price(browser,env,monkeypatch,size):
    monkeypatch.setenv('SCOUT_MARKET_TOOLS','1')
    monkeypatch.setenv('UI_NEXT','1')
    doc=market_doc()
    for sp,d in doc['sports'].items():
        for i,r in enumerate(d['ev']+d['arbitrage']):
            r.update(leg_id=f'{sp}|event|player{i}|points|full_game|24.5',event_id='event')
    monkeypatch.setattr(wm,'shared',lambda *a:(200,json.dumps(doc).encode()))
    c=context(browser,env,size)
    try:
        p=mt_page(c,env,doc,path='/#/arb')
        p.locator('[data-hub="price-draft"]').filter(has_text='Generate paired slips').click()
        p.wait_for_selector('#hub-price-draft li')
        assert p.locator('#hub-price-draft li').count()==2
        assert 'two separate matched slips' in p.locator('#hub-price-draft').inner_text().lower()
        assert 'No bets have been placed' in p.locator('#hub-price-draft').inner_text()
        if os.environ.get('PRICE_SLIP_SHOTS'):
            p.screenshot(path=str(Path(os.environ['PRICE_SLIP_SHOTS'])/f'arb-{size["width"]}.png'))
        assert p.locator('#hub-price-draft').evaluate('(d)=>d.scrollWidth<=d.clientWidth+1')
        # The copy action rechecks the server, not just the cached modal contents.
        doc['sports']['nba']['arbitrage'][0]['legs'][0]['odds']=2.07
        p.locator('[data-hub="copy-price-draft"]').click()
        p.wait_for_selector('#hub-price-draft [role="alert"]')
        assert 'price changed' in p.locator('#hub-price-draft').inner_text()
        assert p.locator('#hub-price-draft li').count()==0
        p.locator('#hub-price-draft form button').click()
        assert p.url.endswith('/#/arb')
        p.goto(env.url+'/#/ev')
        p.locator('[data-hub="price-draft"]').filter(has_text='Generate bet slip').first.click()
        p.wait_for_selector('#hub-price-draft li')
        assert p.locator('#hub-price-draft li').count()==1
        assert 'One straight bet' in p.locator('#hub-price-draft').inner_text()
        no_h_scroll(p)
        assert not p.errors
    finally:c.close()


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_ev_generate_stays_on_ev_and_keeps_studio_untouched(browser,env,monkeypatch,size):
    monkeypatch.setenv('SCOUT_PICKEM_EV','1')
    monkeypatch.setenv('UI_NEXT','1')
    c=context(browser,env,size)
    records=c.request.get(env.url+'/api/upgrade/board?sport=cs2').json()['records']
    board={r['key']:r for r in records}
    original=we.shared
    def prices(srv,q,allowed):
        st,body=original(srv,q,allowed);d=json.loads(body)
        d['sports']=['cs2'];d['rows']=[];d['model_only']=[]
        for r in board.values():
            if r['offer']['platform']!=d['app'] or not r['scout'].get('side'):continue
            d['rows'].append(dict(key='|'.join((r['sport'],d['app'],r['offer']['offer_id'])),sport='cs2',
                player=r['player']['name'],team=r['player']['team'],event=r['event']['label'],
                stat_label=r['market']['stat_label'],period_label=r['market']['period_label'],start_utc=r['event']['start_utc'],
                side=r['scout']['side'],line=r['offer']['line'],basis='market',ev=.12,p_market=.65,
                books=2,sources=[dict(kind='sportsbook',age_s=30)],american=-150,be=.58,p_model=r['scout']['p_side']))
        d['rows_total']=len(d['rows'])
        return st,json.dumps(d).encode()
    monkeypatch.setattr(we,'shared',prices)
    try:
        p=open_page(c,env,'/#scout/ev',size)
        p.wait_for_selector('#ev-entry')
        p.locator('#ev-entry').select_option('power-2')
        p.wait_for_function("document.querySelector('#ev-entry').value==='power-2' && !document.querySelector('[data-ev=generate]').disabled")
        p.locator('#ev-slip-count').select_option('1')
        p.locator('[data-ev="generate"]').click()
        p.wait_for_selector('.ev-generated-card',timeout=30000)
        assert p.url.endswith('/#scout/ev')
        assert p.locator('.ev-generated-card').count()==1
        assert p.locator('.ev-generated-card li').count()==2
        assert not p.locator('#hub-studio-section').is_visible()
        state=c.request.get(env.url+'/api/upgrade/state').json()
        assert state.get('studio') is None
        assert p.locator('[data-ev="copy-generated"]').is_visible()
        c.grant_permissions(['clipboard-read','clipboard-write'])
        p.locator('[data-ev="copy-generated"]').click()
        p.wait_for_function("document.querySelector('.ev-generated [role=status]').textContent.includes('Rechecked and copied')")
        copied=p.evaluate('navigator.clipboard.readText()')
        assert 'PrizePicks power' in copied
        # Recheck failure must stay on EV and refuse copying the old offer.
        p.route('**/api/upgrade/recheck',lambda r:r.fulfill(status=409,json={'error':'A line changed. Generate again.'}))
        p.locator('[data-ev="copy-generated"]').click()
        p.wait_for_function("document.querySelector('.ev-generated [role=status]').textContent.includes('line changed')")
        assert p.url.endswith('/#scout/ev')
        assert p.evaluate('navigator.clipboard.readText()')==copied
        if os.environ.get('PRICE_SLIP_SHOTS'):
            p.screenshot(path=str(Path(os.environ['PRICE_SLIP_SHOTS'])/f'ev-dedicated-{size["width"]}.png'))
        no_h_scroll(p)
        assert not p.errors
    finally:c.close()
