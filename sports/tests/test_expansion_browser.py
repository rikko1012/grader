"""Isolated UI component tests: synthetic offers, no provider or production calls."""

from datetime import datetime, timedelta, timezone

import json

import os

from pathlib import Path

from urllib.parse import urlparse

import pytest

pw = pytest.importorskip('playwright.sync_api')

ROOT = Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('width,theme',[(1440,'dark'),(390,'dark'),(390,'light')])

def test_matching_theme_filters_and_player_periods(width,theme):

    now=datetime.now(timezone.utc)

    row={'sport':'soccer','platform':'prizepicks','offer_id':'1','event_id':'match1','player_id':'p1',

         'player':'Test Forward','role':'Forward','team':'Example FC','match':'Example FC · Test United',

         'label':'Shots','stat':'shots','scope':'full_game','line':2.5,'variant':'standard',

         'start':(now+timedelta(hours=3)).isoformat(),'observed_at':now.isoformat(),

         'sides':{'over':{'multiplier':None}},'forecast':None}

    with pw.sync_playwright() as p:

        browser=p.chromium.launch()

        ctx=browser.new_context(viewport={'width':width,'height':900})

        ctx.add_init_script(f"localStorage.setItem('scout-theme', {json.dumps(theme)})")

        def route(r):

            u=urlparse(r.request.url)

            if u.netloc!='scout.test':

                return r.abort()

            assets={'/sports/expansion.html':'web/sports/expansion.html',

                    '/sports/expansion.js':'web/sports/expansion.js','/sports/expansion.css':'web/sports/expansion.css',

                    '/upgrade/next.css':'web/upgrade-next.css'}

            if u.path in assets:

                path=ROOT/assets[u.path]

                return r.fulfill(body=path.read_text(encoding='utf-8'),content_type={'.css':'text/css','.js':'application/javascript','.html':'text/html'}[path.suffix])

            if u.path=='/api/sports/token':

                return r.fulfill(json={'token':'local-fixture','api_base':''})

            if u.path=='/api/sports/expansion/board':

                return r.fulfill(json={'offers': [row,dict(row,scope='first_half',line=1.5)] if 'soccer' in u.query else []})

            r.fulfill(status=404)

        ctx.route('**/*',route)

        page=ctx.new_page();page.set_default_timeout(5000);errors=[]

        page.on('pageerror',lambda e:errors.append(str(e)))

        page.goto('http://scout.test/sports/expansion.html')

        page.locator('.player').wait_for()

        assert page.locator('.player').count()==1

        assert page.locator('.sides').inner_text()=='Over 2.5'

        page.get_by_label('Test Forward: market and period',exact=True).select_option('0')

        page.get_by_text('More filters',exact=True).click()

        page.get_by_label('Period',exact=True).select_option('first_half')

        assert page.locator('.line').inner_text()=='1.5 Shots'

        assert page.locator('.scope').inner_text()=='First half'

        page.get_by_text('More filters',exact=True).click()

        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')

        assert page.evaluate('document.documentElement.dataset.theme')==theme

        assert page.locator('body').evaluate('(e)=>getComputedStyle(e).fontFamily').startswith('"IBM Plex Sans"')

        target=os.environ.get('EXPANSION_SHOTS')

        if target:

            Path(target).mkdir(parents=True,exist_ok=True)

            page.screenshot(path=str(Path(target)/f'expansion-{width}-{theme}.png'),full_page=True)

        page.get_by_text('More filters',exact=True).click()

        page.get_by_label('Side',exact=True).select_option('under')

        assert page.locator('.player').count()==0

        assert 'No current lines' in page.locator('.empty').inner_text()

        page.get_by_role('button',name='Tennis',exact=True).click()

        page.locator('.empty').wait_for()

        assert page.locator('.player').count()==0

        assert not errors

        browser.close()

# These fixtures start the real esports Handler. The new Sports HTTP process
# below uses the actual service and route/auth table; only test login is faked.
from tests.test_upgrade_browser import browser, env, ctx

@pytest.mark.parametrize('width', [1440, 390])
def test_real_servers_player_history_and_navigation(ctx, env, monkeypatch, tmp_path, width):
    import threading
    from sports import service, store, expansion_board, expansion_history
    from sports.contracts import Event, StatObservation, now_utc, utc
    from sports.expansion import isolated_database
    monkeypatch.setenv('SPORTS_API', '1')
    monkeypatch.setenv('SPORTS_API_MODE', 'service')
    monkeypatch.setenv('SPORTS_TOKEN_SECRET', 'local-expansion-test-secret-' * 3)
    monkeypatch.setenv('SCOUT_TENNIS_ENABLED', '1')
    monkeypatch.setenv('SCOUT_EXPANSION_DATA_DIR', str(tmp_path / 'expansion'))
    monkeypatch.setenv('SPORTS_ALLOWED_ORIGINS', env.url)
    monkeypatch.setattr(service, '_STATE', {'draining': False})
    now = datetime.now(timezone.utc)
    start = utc(now + timedelta(hours=3))
    known = utc(now - timedelta(hours=1))
    row = {'sport':'tennis','tour':'atp','platform':'underdog','offer_id':'line-1',
           'player_id':'tennis:underdog:p1','event_id':'tennis:underdog:e1',
           'player':'Test Server','opponent':'Test Returner','role':None,'team':None,
           'match':'Test Server vs Test Returner','competition':'ATP Singles',
           'label':'Games Won','stat':'games_won','scope':'full_game','line':12.5,
           'variant':'balanced','start':start,'observed_at':now_utc(),
           'sides':{'over':{'multiplier':1.0},'under':{'multiplier':1.0}},'revision':'fixture-rev'}
    expansion_board.save('tennis',[row],{'underdog':{'ok':True,'checked_at':now_utc()}},now_utc())
    match = {'event_id':'atp:espn:atp:t:upcoming','competition':'atp','start':start,
             'participants':[{'id':'atp:player:espn:1','name':'Test Server'},
                             {'id':'atp:player:espn:2','name':'Test Returner'}]}
    expansion_history.atomic_json(tmp_path/'expansion/tennis/current-matches.json',[match])
    con = store.connect(isolated_database(tmp_path/'expansion','atp'))
    old = 'atp:espn:atp:t:old'
    store.put_many(con,[Event(old,'atp',2026,'unknown',None,None,utc(now-timedelta(days=2)),'espn',
        status='final',observed_at=known,known_at=known),
        StatObservation(old,'atp','atp:player:espn:1','player','full_game','no','games_won','count',14,
                        'played','espn','old',True,known,known)])
    con.close()
    srv=service.Server(('127.0.0.1',0),threads=4)
    monkeypatch.setenv('SPORTS_API_BASE', f'http://127.0.0.1:{srv.server_address[1]}')
    threading.Thread(target=srv.serve_forever,daemon=True).start()
    try:
        page=ctx.new_page();page.set_viewport_size({'width':width,'height':900});errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(env.url+'/sports/expansion.html?sport=tennis')
        page.get_by_role('heading',name='Test Server',exact=True).wait_for(timeout=15000)
        page.get_by_text('Player history',exact=True).click()
        page.get_by_text('L5: 1 Over · 0 Under · 0 Push (1 games)',exact=True).wait_for(timeout=15000)
        assert page.locator('.research tbody tr').count()==1
        assert page.locator('.research tbody tr').inner_text().endswith('14\tOver')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        assert not errors
        target=os.environ.get('EXPANSION_SHOTS')
        if target: page.screenshot(path=str(Path(target)/f'history-real-servers-{width}.png'),full_page=True)
        page.goto(env.url+'/#all-sports')
        page.locator('#hub-sports [data-hub=expansion][data-id=tennis]').wait_for(timeout=30000)
        assert page.locator('#hub-sports [data-hub=sport][data-id=cs2]').count()==1
        page.locator('#hub-sports [data-hub=expansion][data-id=tennis]').click()
        page.get_by_role('heading',name='Test Server',exact=True).wait_for(timeout=15000)
    finally:
        srv.shutdown();srv.server_close()
