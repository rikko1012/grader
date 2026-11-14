"""Real HTTP draft generation and rechecks at desktop/mobile; no production writes."""
import json
import os
from pathlib import Path
import pytest
import workspace_ev as we
import workspace_markets as wm
from tests.test_upgrade_browser import browser, env, context, open_page, no_h_scroll, DESK, PHONE
from tests.test_hub_shell_browser import market_doc, mt_page


@pytest.mark.parametrize('design', ['0', '1'])
@pytest.mark.parametrize('size', [DESK, PHONE])
def test_market_generate_pair_single_and_changed_price(browser,env,monkeypatch,size,design):
    monkeypatch.setenv('SCOUT_MARKET_TOOLS','1')
    monkeypatch.setenv('UI_NEXT',design)
    doc=market_doc()
    for sp,d in doc['sports'].items():
        for i,r in enumerate(d['ev']+d['arbitrage']):
            r.update(leg_id=f'{sp}|event|player{i}|points|full_game|24.5',event_id='event')
    monkeypatch.setattr(wm,'shared',lambda *a:(200,json.dumps(doc).encode()))
    c=context(browser,env,size)
    try:
        p=mt_page(c,env,doc,path='/#/arb')
        p.get_by_role('button',name='Generate best paired slips',exact=True).click()
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
        assert p.locator('[data-hub="price-draft"]').filter(has_text='Generate bet slip').count()==sum(not r.get('flags') for d in doc['sports'].values() for r in d['ev'])
        p.locator('[data-hub="price-draft"]').filter(has_text='Generate bet slip').first.click()
        p.wait_for_selector('#hub-price-draft li')
        assert p.locator('#hub-price-draft li').count()==1
        assert 'One straight bet' in p.locator('#hub-price-draft').inner_text()
        no_h_scroll(p)
        assert not p.errors
    finally:c.close()


@pytest.mark.parametrize('design', ['0', '1'])
@pytest.mark.parametrize('size', [DESK, PHONE])
def test_ev_generate_stays_on_ev_and_keeps_studio_untouched(browser,env,monkeypatch,size,design):
    monkeypatch.setenv('SCOUT_PICKEM_EV','1')
    monkeypatch.setenv('UI_NEXT',design)
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


@pytest.mark.parametrize('design', ['0', '1'])
@pytest.mark.parametrize('size', [DESK, PHONE])
def test_generators_remain_visible_without_prices(browser,env,monkeypatch,size,design):
    monkeypatch.setenv('SCOUT_PICKEM_EV','1')
    monkeypatch.setenv('SCOUT_MARKET_TOOLS','1')
    monkeypatch.setenv('UI_NEXT',design)
    c=context(browser,env,size)
    try:
        c.route('**/api/upgrade/pickem-ev?*',lambda r:r.fulfill(status=502,json={'error':'unavailable'}))
        p=open_page(c,env,'/#/ev',size)
        p.wait_for_function("document.querySelector('#hub-ev-body')?.textContent.includes('Prices could not be loaded')")
        assert p.get_by_role('button',name='Generate EV slips',exact=True).is_visible()
        assert p.get_by_role('button',name='Generate EV slips',exact=True).is_disabled()
        no_h_scroll(p)
        doc=market_doc()
        for d in doc['sports'].values():
            d['arbitrage']=[];d['ev']=[];d['closest']=[]
        p=mt_page(c,env,doc,path='/#/arb')
        button=p.get_by_role('button',name='Generate best paired slips',exact=True)
        button.wait_for()
        assert button.is_disabled()
        assert 'No qualifying pair' in p.locator('[aria-label="Arbitrage slip generator"]').inner_text()
        assert not p.locator('#hub-studio-section').is_visible()
        no_h_scroll(p)
    finally:c.close()


@pytest.mark.parametrize('design', ['0', '1'])
@pytest.mark.parametrize('size', [DESK, PHONE])
def test_reference_books_visible_without_opportunities(browser,env,monkeypatch,size,design):
    monkeypatch.setenv('SCOUT_MARKET_TOOLS','1')
    monkeypatch.setenv('UI_NEXT',design)
    doc=market_doc(empty=True)
    template=market_doc()['sports']['nba']['ev'][0]
    ref={k:v for k,v in template.items() if k not in ('p_cal','ev','side','per_100','odds','american')}
    ref.update(book='reference',book_label='Reference Book',reference_only=True,rules_confirmed=False,
               prices={'over':{'odds':1.91,'american':'-110'},'under':{'odds':1.95,'american':'-105'}})
    doc['sports']['nba']['comparisons']=[ref]
    pair=market_doc()['sports']['nba']['arbitrage'][0]
    pair.update(margin_pct=-4.2,reference_only=True)
    doc['sports']['nba']['closest_pairs']=[pair]
    c=context(browser,env,size)
    try:
        p=mt_page(c,env,doc,path='/#/arb')
        section=p.get_by_role('region',name='Current book comparisons',exact=True)
        assert section.is_visible()
        assert 'Over -110' in section.inner_text() and 'Under -105' in section.inner_text()
        assert 'Settlement rules unverified' in section.inner_text()
        assert p.get_by_role('region',name='Closest matched prices').is_visible()
        assert p.get_by_role('button',name='Generate best paired slips',exact=True).is_disabled()
        assert section.locator('[data-hub="price-draft"]').count()==0
        p.locator('#hub-mt-book').select_option('reference')
        assert section.is_visible()
        assert p.get_by_role('region',name='Closest matched prices').count()==0
        p.locator('.h-mt-more > summary').click()
        p.locator('#hub-mt-side').select_option('over')
        assert 'Hidden by side filter' in section.inner_text()
        assert 'Not listed' not in section.inner_text()
        p.locator('#hub-mt-side').select_option('all')
        p.locator('#hub-mt-q').fill('no such player')
        assert p.get_by_role('region',name='Current book comparisons').count()==0
        p.locator('#hub-mt-q').fill('')
        no_h_scroll(p)
        if os.environ.get('PRICE_SLIP_SHOTS'):
            section.scroll_into_view_if_needed()
            p.screenshot(path=str(Path(os.environ['PRICE_SLIP_SHOTS'])/f'reference-{design}-{size["width"]}.png'))
        p.goto(env.url+'/#/ev')
        p.get_by_role('region',name='Current book comparisons').wait_for()
        assert p.get_by_role('button',name='Generate best bet slip',exact=True).is_disabled()
        no_h_scroll(p)
        assert not p.errors
    finally:c.close()


def test_exact_line_price_comparison_and_started_game(browser,env,monkeypatch):
    monkeypatch.setenv('SCOUT_MARKET_TOOLS','1')
    doc=market_doc()
    nba=doc['sports']['nba']
    base=dict(nba['ev'][0],leg_id='nba|event|player|points|game|24.5',side=None,
              rules_confirmed=True,prices={'over':{'odds':2.1,'american':'+110'}})
    nba['comparisons']=[base,dict(base,book='beta',book_label='Beta',prices={'under':{'odds':1.9,'american':'-111'}}),
                        dict(base,leg_id='nba|event|player|points|q1|24.5',period_label='First quarter')]
    nba['ev'][0]['start_utc']='2000-01-01T00:00:00Z'
    c=context(browser,env,PHONE)
    try:
        page=mt_page(c,env,doc,path='/#/ev')
        group=page.locator('.h-compare-group')
        group.locator('summary').click()
        assert 'Over +110' in group.inner_text() and 'Under -111' in group.inner_text()
        assert 'First quarter' not in group.inner_text()
        assert 'First quarter' in page.locator('.h-compare-single').inner_text()
        assert page.get_by_text('Game started',exact=True).is_visible()
        no_h_scroll(page)
    finally:
        c.close()
