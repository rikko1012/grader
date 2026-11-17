"""Isolated UI component tests: synthetic offers, no provider or production calls."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

pw = pytest.importorskip('playwright.sync_api')
ROOT = Path(__file__).resolve().parents[1]


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
