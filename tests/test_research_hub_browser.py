"""Real Handler + isolated four-sport fixtures. No production account writes."""
import json
import os
import time
from pathlib import Path
import pytest
import server
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE

SHOTS_DIR = Path(os.environ['PHASE4_SHOTS']) if os.environ.get('PHASE4_SHOTS') else None


def saved_state(page, predicate):
    deadline = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        last = page.evaluate("fetch('/api/upgrade/state').then(r=>r.json())")
        if predicate(last):
            return last
        page.wait_for_timeout(200)
    pytest.fail('Account state did not persist: ' + repr(last))


def book(p, b):
    """The platform toggle on the toolbar (All | PP | UD)."""
    p.locator(f'#hub-platform [data-id={b}]').click()


def open_filters(p):
    if p.locator('#hub-filter-pop').is_hidden():
        p.locator('#hub-filters-toggle').click()


def add_first(p):
    """+ Add on the first row not on the slip; a prop whose Scout side is not offered opens the ONE card, where the member picks a side."""
    p.locator('#hub-rows .h-add:not([disabled]):not(.is-on)').first.click()
    if p.locator('#hub-card').is_visible():
        p.locator('#hub-detail [data-hub=history-side][data-id=over]').click()
        p.locator('#hub-detail [data-hub=detail-add]:not([disabled])').click()
        p.locator('#hub-detail [data-hub=card-close]').click()


def hub(ctx, env, size=None):
    p = open_page(ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    return p


@pytest.mark.parametrize('size',[DESK,PHONE])
def test_owner_grade_comparison_four_sports_and_paging(ctx,env,monkeypatch,size):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    monkeypatch.setattr(server.Handler, '_sports_owner', lambda self: True)
    p = ctx.new_page()
    p.set_viewport_size(size)
    seen=[]
    def report(route):
        from urllib.parse import urlsplit,parse_qs
        q=parse_qs(urlsplit(route.request.url).query)
        sport=q['sport'][0];seen.append((sport,q.get('before')))
        route.fulfill(json={'sport':sport,'started_at':'2026-11-03T12:00:00Z','next_before':None if q.get('before') else 8,
          'rows':[{'player':'<unsafe>','event':'fixture-event','stat':'kills','line':20.5,'period':'full','book':'prizepicks',
                   'old_letter':'B','old_p':.62,'at':'2026-11-03T12:01:00Z','policy':'gp-1',
                   'sides':{'over':{'raw':.63,'blended':.61,'p':.55,'market':.59,'blend_weight':.5,'calibration':'isotonic',
                         'grade':{'band':'B','break_even':.5345,'reasons':['<unsafe> gate']}},'under':{'p':.45,'grade':{'band':None}}}}]})
    p.route('**/api/upgrade/grade-review?*',report)
    p.goto(env.url+'/#/tools')
    p.wait_for_selector('[data-hub="grade-review"]')
    for sport in ('cs2','lol','nba','nfl'):
        p.locator('[data-hub="grade-review"][data-id="'+sport+'"]').first.click()
        p.wait_for_selector('#hub-grade-review table')
        assert '<unsafe>' in p.locator('#hub-grade-review').inner_text()
        assert p.locator('#hub-grade-review unsafe').count()==0
        p.locator('#hub-grade-review summary').first.click()
        assert 'Raw model 63.0%' in p.locator('#hub-grade-review').inner_text()
        assert 'isotonic' in p.locator('#hub-grade-review').inner_text()
        assert '<unsafe> gate' in p.locator('#hub-grade-review').inner_text()
        assert p.locator('#hub-grade-review unsafe').count()==0
        p.locator('#hub-grade-review [data-before]').click()
        p.wait_for_function("!document.querySelector('#hub-grade-review [data-before]')")
    assert len(seen)==8
    assert seen[-1]==('nfl',['8'])
    no_h_scroll(p)


@pytest.mark.parametrize('size',[DESK,PHONE])
def test_four_sport_calendars_show_pushes_and_empty_days(ctx,env,size):
    from gradecore.calendar import daily_record
    p=ctx.new_page();p.set_viewport_size(size)
    doc={'calendar':daily_record([{'at':'2026-11-02T12:00:00Z','status':'win'},
                                  {'at':'2026-11-02T12:00:00Z','status':'push'}],
                                 '2026-11-03T12:00:00Z','Fixture date · UTC')}
    p.route('**/api/upgrade/stated',lambda r:r.fulfill(json=doc))
    p.route('**/api/upgrade/results?*',lambda r:r.fulfill(json=doc))
    p.goto(env.url+'/#scout/record')
    p.wait_for_selector('.h-record-calendar')
    assert p.locator('.h-record-calendar').count()==4
    # a run of days with no decided pick folds into one tile: 28 days before the result, the day itself, the day after
    assert p.locator('.h-calendar-grid summary').count()==4
    assert p.locator('.h-cal-gap').count()==8 and '28 days' in p.locator('.h-cal-gap').first.inner_text()
    p.locator('.h-calendar-grid summary[aria-label^="2026-11-02"]').first.click()
    assert '1 wins · 0 losses · 1 pushes' in p.locator('.h-calendar-grid details[open]').inner_text()
    assert 'Accuracy only, not profit.' in p.locator('#hub-record-body').inner_text()
    no_h_scroll(p)


@pytest.mark.parametrize('size',[DESK,PHONE])
def test_public_record_four_sports_retry_and_safe_text(ctx,env,monkeypatch,size):
    monkeypatch.setenv('SCOUT_PUBLIC_RECORD','1')
    p=ctx.new_page();p.set_viewport_size(size)
    from gradecore.calendar import daily_record
    failures=[True]
    def report(route):
        if failures[0]:
            failures[0]=False
            return route.fulfill(status=503,json={'error':'unavailable'})
        route.fulfill(json={'totals':{'wins':2,'losses':1,'pushes':1,'observed':2/3,'printed':.6},
            'population':'<unsafe> fixture population','rule':'Only saved pregame forecasts',
            'bands':[{'name':'B','wins':2,'losses':1,'observed':2/3,'printed':.6}],
            'calendar':daily_record([{'at':'2026-11-02T12:00:00Z','status':'push'}],
                '2026-11-03T12:00:00Z','Fixture date · UTC')})
    p.route('**/api/record?*',report)
    p.goto(env.url+'/record')
    p.wait_for_selector('#retry');p.locator('#retry').click()
    for sport in ('cs2','lol','nba','nfl'):
        p.locator('[data-sport='+sport+']').click()
        p.wait_for_function('(s)=>document.querySelector("#record h2")?.textContent.toLowerCase()===s',arg=sport)
        assert '66.7%' in p.locator('#record').inner_text()
        assert p.locator('#record unsafe').count()==0
        assert p.locator('#record .counts').count()==0 and p.locator('#record .rel-chart').count()==0   # no reliability answer: as before
        assert p.locator('.calendar summary').count()==30
        no_h_scroll(p)
    p.screenshot(path=f'.record-{size["width"]}.png',full_page=True)


@pytest.mark.parametrize('size',[DESK,PHONE])
def test_public_record_bins_letters_fact_and_slips_tab(ctx,env,monkeypatch,size):
    import re
    for k in ('SCOUT_PUBLIC_RECORD','SCOUT_RECORD_STATUS','SCOUT_PUBLIC_RECORD_SLIPS'):monkeypatch.setenv(k,'1')
    p=ctx.new_page();p.set_viewport_size(size)
    rec={'sport':'nba','population':'Fixture population','rule':'Fixture rule','calendar':None,'slips_tab':True,
         'totals':{'wins':5,'losses':3,'pushes':1,'void':2,'matches':4,'match_word':'games','observed':.625,'printed':.6,'ci':[.4,.8]},
         'bands':[{'name':'60–65%','wins':5,'losses':3,'observed':.625,'printed':.62,'ci':[.4,.8]}],
         'reliability':[{'lo':.55,'hi':.6,'n':4,'stated':.58,'hit':.5,'ci':[.25,.75]},{'lo':.6,'hi':.65,'n':4,'stated':.62,'hit':.75,'ci':[.5,1]}],
         'status':{'letters':'held','text':'NBA letters are held. NBA figures are research estimates.'}}
    slips={'sport':'nba','tracked':7,'mixed':2,'pending':2,'settled':5,'all_hit':2,'missed':2,'push_or_void':1,'all_hit_rate':.5,
           'legs':{'hit':9,'miss':2,'push':1,'void':0,'pending':2},
           'by_platform':[{'name':'Underdog','settled':5,'all_hit':2,'missed':2,'push_or_void':1}],
           'by_size':[{'name':'2 picks','settled':3,'all_hit':1,'missed':1,'push_or_void':1},{'name':'3 picks','settled':2,'all_hit':1,'missed':1,'push_or_void':0}]}
    p.route(re.compile(r'.*/api/record/slips\?.*'),lambda r:r.fulfill(json=dict(slips,sport=r.request.url.split('sport=')[1])))
    p.route(re.compile(r'.*/api/record\?.*'),lambda r:r.fulfill(json=rec))
    p.goto(env.url+'/record#nba')
    p.wait_for_selector('#record .rel-chart')
    text=p.locator('#record').inner_text()
    for want in ('NBA letters are held','8 decided (wins plus losses)','1 pushes, counted apart','2 voids, left out','4 games','40.0% to 80.0%','55–60%'):
        assert want in text, want
    assert p.locator('.rel-chart circle').count()==2
    no_h_scroll(p)
    small=p.evaluate("[...document.querySelectorAll('button,a')].filter(e=>e.offsetParent).map(e=>[e.textContent,e.getBoundingClientRect().height]).filter(x=>x[1]<40)")
    assert not small, small
    p.locator('[data-view=slips]').click()
    p.wait_for_function("location.hash==='#nba/slips' && document.querySelector('#record').textContent.includes('Settled slips')")
    text=p.locator('#record').inner_text()
    for want in ('Every leg hit','7 slips tracked','1 settled with a push or void leg, counted apart','2 mixed-sport slips included','A push or void leg is never a hit','3 picks'):
        assert want in text, want
    assert p.locator('[data-view=slips]').get_attribute('aria-pressed')=='true'
    no_h_scroll(p)
    p.locator('[data-sport=cs2]').click()
    p.wait_for_function("location.hash==='#cs2/slips' && document.querySelector('#record h2').textContent==='CS2'")
    p.locator('a.skip').focus()
    assert p.evaluate("document.querySelector('a.skip').getBoundingClientRect().left")>=0
    p.keyboard.press('Enter')
    assert p.evaluate("document.activeElement.id")=='record' and p.evaluate('location.hash')=='#cs2/slips'
    body=p.locator('body').inner_text().lower()
    assert not re.search(r'profit|\broi\b|not a bet|betting advice|financial advice|hypothetical|unsure|provisional',body)


def test_all_four_real_boards_filters_research_and_original_navigation(ctx, env):
    p = hub(ctx, env)
    p.wait_for_function("document.querySelectorAll('#hub-rows tr[data-record^=nba]').length > 0")
    assert all(p.locator('#hub-rows tr[data-record^='+s+']').count() for s in ('cs2','lol','nba','nfl'))
    assert 'SAMPLE' not in p.locator('#u-hub').inner_text()
    p.locator('#hub-q').fill('vargo')
    assert p.locator('#hub-rows tr[data-record]').count() == 1
    p.locator('#hub-rows .h-prop-toggle').click()
    assert p.locator('#hub-rows tr[data-record]').count() >= 2
    assert 'orlo' not in p.locator('#hub-rows').inner_text()
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg', timeout=15000)
    # ONE card: books and the Genie are tabs of the same card; the old drawer is not used inside the hub
    assert p.locator('#hub-detail [role=tab]').all_inner_texts() == ['Overview', 'Lines', 'Stats', 'Ask']
    p.locator('#hub-detail [role=tab][data-id=lines]').click()
    p.wait_for_selector('#hub-detail .h-line-shop table')
    assert 'Seen' in p.locator('#hub-detail .h-line-shop thead').text_content()
    p.locator('#hub-detail [role=tab][data-id=ask]').click()
    assert 'Scout evidence guide' in p.locator('#hub-detail').inner_text()
    assert not p.locator('#u-drawer').is_visible() and not p.locator('#u-tray').is_visible()
    p.locator('#hub-detail [data-hub=card-close]').click()
    assert p.locator('#hub-card').is_hidden()
    p.locator('.h-sidebar [data-hub=close]').click()
    assert p.locator('#u-hub').is_hidden()
    assert not p.locator('#board').get_attribute('inert')
    assert not p.errors


def test_four_sport_prizepicks_slip_syncs_to_second_browser_context(ctx, env, browser):
    p = hub(ctx, env)
    book(p, 'prizepicks')
    for i, sport in enumerate(('cs2','lol','nba','nfl')):
        p.locator('#hub-sports [data-id='+sport+']').click()
        assert p.locator('#hub-rows tr[data-record]').first.get_attribute('data-record').startswith(sport+'|'), p.locator('#hub-sports').inner_text()
        add_first(p)            # a research-only offer can have no Scout side: the member explicitly chooses in the card
        assert p.locator('#hub-slip-count').inner_text() == str(i+1), (sport,p.locator('#hub-rows').inner_text(),p.locator('#u-toast').inner_text(),p.errors)
    p.wait_for_function("document.querySelector('#hub-slip-count').textContent === '4'")
    saved = saved_state(p, lambda d: len((d.get('slip') or {}).get('legs', [])) == 4)
    assert {l['sport'] for l in saved['slip']['legs']} == {'cs2','lol','nba','nfl'}
    c = context(browser, env)
    try:
        q = hub(c, env)
        q.wait_for_function("document.querySelector('#hub-slip-count').textContent === '4'")
        assert 'NBA' in q.locator('#hub-slip-content').inner_text()
        q.locator('.h-sidebar [data-id=studio]').click()
        q.locator('#hub-studio-body [data-u=check]').click()
        q.wait_for_selector('#hub-studio-body .u-status b:has-text("ready")', timeout=20000)
        assert not q.errors
    finally:
        c.close()
    assert not p.errors


def test_saved_view_alert_and_watch_are_account_preferences(ctx, env):
    p=hub(ctx, env)
    p.locator('#hub-q').fill('vargo')
    open_filters(p)
    p.locator('#hub-minGrade').select_option('B-')
    p.locator('#hub-views-toggle').click()
    p.locator('[data-hub=save-view]').click()
    p.locator('#hub-save-name').fill('My CS2 research')
    p.locator('#hub-save-confirm').click()
    saved_state(p, lambda d: len((d.get('prefs') or {}).get('researchHub', {}).get('views', [])) == 1)
    p.locator('#hub-views-toggle').click()
    p.locator('[data-hub=save-alert]').click()
    p.locator('#hub-save-name').fill('Vargo line updates')
    p.locator('#hub-save-confirm').click()
    p.locator('#hub-rows [data-hub=watch]').first.click()
    saved_state(p, lambda d: len((d.get('prefs') or {}).get('researchHub', {}).get('alerts', [])) == 1 and len((d.get('prefs') or {}).get('researchHub', {}).get('watch', [])) == 1)
    p.reload()
    p.wait_for_selector('#hub-rows tr')
    p.locator('#hub-views-toggle').click()
    p.locator('#hub-views').select_option('0')
    assert p.locator('#hub-q').input_value()=='vargo'
    assert p.locator('#hub-minGrade').input_value()=='B-'
    assert p.locator('#hub-filter-n').inner_text()=='1'                # Filters (n) counts the grade filter
    p.locator('.h-sidebar [data-id=tools]').click()                      # Tools is the one index; alerts are managed right there
    assert 'Vargo line updates' in p.locator('#hub-tools-alerts').inner_text()
    p.locator('#hub-tools-alerts [data-id=alerts]').click()
    assert 'Vargo line updates' in p.locator('#hub-alerts-body').inner_text()
    assert 'Nothing is sent when it is closed' in p.locator('#hub-alerts-body').inner_text()          # Live highlight: never claims delivery
    assert not p.errors


@pytest.mark.parametrize('size',[DESK, PHONE])
def test_responsive_record_loading_keyboard_and_no_overflow(ctx, env, size):
    p=hub(ctx, env, size)
    no_h_scroll(p)
    p.locator(('.h-tabbar' if size==PHONE else '.h-sidebar')+' [data-id=record]').click()
    p.wait_for_selector('.h-record-card',timeout=30000)
    assert 'Actual hit rate' in p.locator('#hub-record-body').inner_text()
    assert p.locator('.h-record-card').count()==4
    assert '112 decided picks' in p.locator('#hub-record-body').inner_text()
    assert '116 decided picks' not in p.locator('#hub-record-body').inner_text()
    assert 'Different sport cohorts stay separate' in p.locator('#hub-record-section').inner_text()
    no_h_scroll(p)
    p.locator('#hub-record-sport').select_option('cs2')
    p.wait_for_function("document.querySelectorAll('.h-record-card').length===1")
    assert not p.errors


def test_quote_refresh_failure_hides_the_lines_and_cannot_add(ctx, env):
    # owner rules (audit 2026-11-04): a failed refresh hides the lines, never labels them
    p=hub(ctx, env)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.route('**/api/upgrade/board',lambda r:r.fulfill(status=503,content_type='application/json',body='{"error":"board_unavailable"}'))
    p.locator('.h-toolbar [data-hub=refresh]').click()
    p.wait_for_function("!document.querySelector('#hub-rows .h-add')")
    assert 'could not refresh' not in p.locator('#hub-health').inner_text()
    assert p.locator('#hub-rows .h-add:not([disabled])').count()==0
    assert not p.errors


def test_unoffered_scout_side_opens_side_choice_instead_of_dead_add(ctx, env):
    p = hub(ctx, env)

    def one_sided(route):
        h = dict(route.request.headers); h.pop('if-none-match', None)          # always a full board, never a 304, for the rewrite
        response = route.fetch(headers=h)
        d = response.json()
        for record in d['records']:
            side = record['scout'].get('side')
            if side and record['offer']['platform'] == 'prizepicks':
                record['offer'][side]['offered'] = False
        route.fulfill(response=response, json=d)

    p.route('**/api/upgrade/board', one_sided)
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'prizepicks')
    p.locator('.h-toolbar [data-hub=refresh]').click()
    p.wait_for_selector('#hub-rows .h-add[aria-label^="Choose a side"]', timeout=10000)
    p.locator('#hub-rows .h-add').first.click()
    p.wait_for_selector('#hub-card:not([hidden]) #hub-detail .h-decision', timeout=10000)      # the same card, side chooser pinned
    assert not p.locator('#u-drawer').is_visible()
    assert p.locator('#hub-slip-count').inner_text() == '0'
    assert not p.errors


def test_malformed_saved_filters_cannot_break_the_board(ctx, env):
    p=hub(ctx, env)
    p.evaluate("""async () => {
      const s=await (await fetch('/api/upgrade/state')).json();
      const bad={name:'Recovered view',filters:{q:{bad:true},sport:123,side:[],book:null,watched:'false'}};
      const r=await fetch('/api/upgrade/state',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rev:s.rev,prefs:{researchHub:{views:[bad],alerts:[bad],watch:[null,{}]}}})});
      if(!r.ok)throw Error('Fixture preference write failed');
    }""")
    p.reload()
    p.wait_for_selector('#hub-rows tr')
    p.locator('#hub-views-toggle').click()
    p.locator('#hub-views').select_option('0')
    assert p.locator('#hub-q').input_value()==''
    assert p.locator('#hub-rows tr[data-record]').count()>0
    assert not p.errors


def test_successful_response_with_stale_source_is_not_presented_as_current(ctx, env):
    def stale(route):
        h=dict(route.request.headers);h.pop('if-none-match',None)
        response=route.fetch(headers=h)
        d=response.json()
        for f in d['feeds'].values():
            f['state']='stale'
        route.fulfill(response=response,json=d)
    p=hub(ctx, env)
    p.route('**/api/upgrade/board',stale)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('.h-toolbar [data-hub=refresh]').click()
    p.wait_for_function("!document.querySelector('#hub-rows .h-add')")      # stale lines are left off, never labelled
    assert p.locator('#hub-rows .h-add:not([disabled])').count()==0 and 'stale' not in p.locator('#hub-health').inner_text().lower()


def test_phone_keeps_filters_available_without_covering_first_prop(ctx, env):
    p=hub(ctx, env, PHONE)
    assert not p.locator('#hub-stat').is_visible()                     # the other filters wait in the Filters pop-over
    assert p.locator('#hub-rows tr[data-record]').first.bounding_box()['y'] < 300
    p.locator('#hub-filters-toggle').click()
    assert p.locator('#hub-stat').is_visible()
    p.keyboard.press('Escape')
    assert not p.locator('#hub-stat').is_visible()
    book(p, 'prizepicks')
    assert all(x.startswith(('cs2|prizepicks|','lol|prizepicks|','nba|prizepicks|','nfl|prizepicks|')) for x in p.locator('#hub-rows tr[data-record]').evaluate_all('(rows)=>rows.map(r=>r.dataset.record)'))
    no_h_scroll(p)


@pytest.mark.parametrize('path,sport',[('/', 'cs2'),('/#board','cs2'),('/lol','lol'),('/sports#/board?lg=nba','nba'),('/sports#/board?lg=nfl','nfl')])
def test_new_workspace_is_the_default_for_existing_member_board_urls(ctx,env,path,sport):
    p=open_page(ctx,env,path)
    p.wait_for_selector('#hub-rows tr[data-record]',timeout=20000)
    assert p.locator('#u-hub').is_visible()
    assert p.locator('#hub-sports [data-id='+sport+']').get_attribute('aria-pressed')=='true', (p.url,p.locator('#hub-title').inner_text(),p.locator('#hub-sports').inner_text(),p.errors)
    assert all(x.startswith(sport+'|') for x in p.locator('#hub-rows tr[data-record]').evaluate_all('(rows)=>rows.map(r=>r.dataset.record)'))
    assert not p.errors


@pytest.mark.parametrize('size',[DESK,PHONE])
def test_inline_history_line_selection_side_and_keyboard(ctx,env,size):
    p=hub(ctx,env,size)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-q').fill('vargo')
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg')
    initial=p.locator('#hub-detail .h-history-summary').inner_text()
    assert '6/10' in initial and '38.5' in initial
    win=p.locator('#hub-detail [data-hub=history-window][data-id="5"]')
    win.focus();p.keyboard.press('Enter')
    assert p.evaluate('document.activeElement.dataset.id')=='5'
    assert '3/5' in p.locator('#hub-detail .h-history-summary').inner_text()
    p.locator('#hub-detail [data-hub=history-side][data-id=under]').click()
    assert '2/5' in p.locator('#hub-detail .h-history-summary').inner_text()
    p.locator('#hub-detail [role=tab][data-id=lines]').click()
    p.locator('#hub-detail [data-hub=detail-line]').filter(has_text='34.5').click()
    p.wait_for_function("document.querySelector('#hub-card-line')?.selectedOptions[0]?.textContent.includes('34.5')")
    p.locator('#hub-detail [role=tab][data-id=overview]').click()
    p.wait_for_function("document.querySelector('#hub-detail .h-history-chart')?.getAttribute('aria-label').includes('34.5')")
    assert 'Add Under 34.5' in p.locator('#hub-detail [data-hub=detail-add]').inner_text()
    assert not p.locator('#hub-detail [data-hub=detail-add]').is_disabled()
    p.locator('#hub-detail .h-history-table summary').click()
    assert p.locator('#hub-detail .h-history-table tbody tr').count()==5
    no_h_scroll(p)
    assert not p.errors


def test_inline_research_refuses_a_changed_revision(ctx,env):
    p=hub(ctx,env)
    def moved(route):
        response=route.fetch();d=response.json()
        d['record']['offer']['revision']='a-new-revision'
        route.fulfill(response=response,json=d)
    p.route('**/api/upgrade/line?*',moved)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-warning')
    assert p.locator('#hub-detail [data-hub=detail-add]').is_disabled()
    assert p.locator('#hub-detail .h-history-chart').count()==0
    assert not p.errors


def test_matchup_and_market_filters_reset_cleanly_when_switching_sport(ctx,env):
    p=hub(ctx,env)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-rows [data-hub=game]').filter(has_text='Kestrel').click()          # a game header row filters to that game
    assert 'vargo' not in p.locator('#hub-rows').inner_text()
    assert p.locator('#hub-filter-n').inner_text()=='1'
    open_filters(p)
    p.locator('#hub-stat').select_option(label='Headshots')
    p.keyboard.press('Escape')
    p.locator('#hub-sports [data-id=nba]').click()
    assert p.locator('#hub-stat').input_value()=='all'
    assert p.locator('#hub-rows [data-hub=game][aria-pressed=true]').count()==0 and p.locator('#hub-filter-n').inner_text()==''
    assert p.locator('#hub-rows tr[data-record]').count()>0
    assert not p.errors


def test_tracked_picks_have_a_page_with_sport_status_filters_and_receipts(ctx,env):
    p=hub(ctx,env)
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'underdog')
    for name in ('vargo','orlo'):
        p.locator('#hub-q').fill(name)
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
    p.locator('.h-sidebar [data-id=studio]').click()
    p.wait_for_selector('#hub-studio-body [data-u=track]:not([disabled])',timeout=20000)
    p.locator('#hub-studio-body [data-u=track]').click()
    p.wait_for_selector('#hub-studio-body .u-note.good')
    p.locator('.h-sidebar [data-id=picks]').click()
    p.wait_for_selector('#hub-picks-body .u-pick')
    assert p.locator('#hub-picks-body .u-pick').count()==2
    p.locator('#hub-picks-body .u-pick button').first.click()
    assert 'Chance you saw' in p.locator('#hub-picks-body').inner_text()
    p.locator('#hub-pick-status').select_option('hit')
    assert 'No picks in this view' in p.locator('#hub-picks-body').inner_text()
    p.locator('#hub-pick-status').select_option('all')
    p.locator('#hub-pick-sport').select_option('nba')
    assert p.locator('#hub-picks-body .u-pick').count()==0
    p.locator('#hub-pick-sport').select_option('cs2')
    assert p.locator('#hub-picks-body .u-pick').count()==2
    assert not p.errors


@pytest.mark.parametrize('flag,size',[('0',DESK),('1',DESK),('1',PHONE)])
def test_studio_slip_header_only_with_its_flag(ctx,env,monkeypatch,flag,size):
    from tests.test_upgrade_browser import _calibrated_export
    monkeypatch.setenv('SCOUT_SLIP_HEADER',flag)
    monkeypatch.setattr(server,'_p1_export',_calibrated_export)
    p=hub(ctx,env)
    p.locator('#hub-sports [data-id=cs2]').click()
    book(p, 'underdog')
    for name in ('vargo','orlo'):
        p.locator('#hub-q').fill(name)
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
    saved_state(p,lambda d:len((d.get('slip')or{}).get('legs',[]))==2)
    p.set_viewport_size(size)                       # the draft is built on desktop; the phone reads the same account draft
    p.goto(env.url+'/#slips')
    p.wait_for_selector('#hub-studio-body [data-u=track]:not([disabled])',timeout=20000)
    body=p.locator('#hub-studio-body')
    if flag=='1':
        assert body.locator('.u-slip-metrics.u-slip-head>div').count()==3 and body.locator('.u-leg-ev').count()==2
        assert 'Kelly' not in body.inner_text()
    else:
        assert body.locator('.u-slip-head').count()==0 and body.locator('.u-leg-ev').count()==0 and 'Quarter-Kelly' not in body.inner_text()
    no_h_scroll(p)
    assert not p.errors


def test_phone_keeps_specialist_tools_in_a_real_menu(ctx,env):
    p=hub(ctx,env,PHONE)
    p.locator('.h-tabbar [data-hub=more]').click()                      # the bottom bar's More holds the specialist tools
    assert p.locator('#hub-more a').filter(has_text='Moneylines').is_visible()
    assert p.locator('#hub-more a').filter(has_text='Single prop grader').is_visible()
    assert p.locator('#hub-more [data-hub=close]').is_visible()
    no_h_scroll(p)


@pytest.mark.parametrize('path',['/#slips','/sports#/slips?lg=nfl','/lol#scout/studio'])
def test_slip_studio_uses_the_shared_account_draft_on_every_sport_route(ctx,env,path):
    p=hub(ctx,env)
    book(p, 'prizepicks')
    for sport in ('cs2','lol','nba','nfl'):
        p.locator('#hub-sports [data-id='+sport+']').click()
        add_first(p)
    saved_state(p,lambda d:len((d.get('slip')or{}).get('legs',[]))==4)
    p.goto(env.url+path)
    p.wait_for_selector('#hub-studio-body .u-entry',timeout=20000)
    assert p.locator('#hub-title').inner_text()=='Slips'
    assert p.locator('[data-hub=studio-tab][aria-selected=true]').inner_text().startswith('My slip')
    assert p.locator('#hub-studio-body .u-leg').count()==4
    p.locator('#hub-studio-body [data-u=check]').click()
    p.wait_for_selector('#hub-studio-body .u-status b:has-text("ready")',timeout=20000)
    # owner sign-off 2026-11-05: the PrizePicks help-center table is verified, so the entry shows its payout (4-pick Power 10x)
    assert 'All-hit payout' in p.locator('#hub-studio-body').inner_text() and 'only in its app' not in p.locator('#hub-studio-body').inner_text()
    assert 'Published payout tiers' not in p.locator('#hub-studio-body').inner_text()
    assert not p.errors


def test_workspace_navigation_back_reload_and_platform_bookmarks(ctx,env):
    p=open_page(ctx,env,'/#board/prizepicks')
    p.wait_for_selector('#hub-rows tr[data-record]')
    assert p.locator('#hub-platform [data-id=prizepicks]').get_attribute('aria-pressed')=='true'
    p.locator('#hub-sports [data-id=nfl]').click()
    p.locator('.h-sidebar [data-id=studio]').click()
    p.go_back()
    p.wait_for_selector('#hub-board-section:not([hidden])')
    assert p.locator('#hub-sports [data-id=nfl]').get_attribute('aria-pressed')=='true'
    p.reload()
    p.wait_for_selector('#hub-rows tr[data-record]')
    assert p.locator('#hub-sports [data-id=nfl]').get_attribute('aria-pressed')=='true'
    p.locator('.h-sidebar [data-id=studio]').click()
    p.keyboard.press('/')
    assert p.locator('#hub-q').evaluate('(el)=>el===document.activeElement')
    p.locator('#hub-q').fill('A/B')
    assert p.locator('#hub-q').input_value()=='A/B'
    assert not p.errors


def test_single_prop_and_shared_legacy_filter_links_do_not_get_hidden(ctx,env):
    p=open_page(ctx,env,'/?bv=1&b=underdog#board/underdog')
    p.wait_for_selector('#boardout .brow2',timeout=20000)
    assert p.locator('#u-hub').is_hidden()
    assert 'view=classic' in p.url
    p.goto(env.url+'/?p=vargo&l=38.5&t=kills')
    p.wait_for_selector('#f',timeout=20000)
    assert p.locator('#u-hub').is_hidden()
    assert p.url.endswith('#one')

def test_generator_saves_eight_distinct_ranked_slips_without_replacing_draft(ctx, env, browser):
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    # without its goblin line the fixture pool (6 legs, 3 matches) builds 7 distinct pairs; eight need Any lines. The No goblins
    # default is covered by test_goblin_rule_defaults_to_no_goblins_is_saved_and_obeyed.
    p.locator('#hub-generation-lines').select_option('all')
    p.locator('#hub-generate').click()
    p.wait_for_function("document.querySelector('#hub-generation-status').textContent.includes('slips generated')", timeout=30000)
    state = saved_state(p, lambda d: len((d.get('studio') or {}).get('slips', [])) == 8)
    slips = state['studio']['slips']
    assert not (state.get('slip') or {}).get('legs')
    assert len({tuple(sorted(l['id'] for l in s['legs'])) for s in slips}) == 8
    assert all(len(s['legs']) == 2 for s in slips)
    assert state['studio']['generator'] == 'sq-1'
    assert all(s['why'] and all(l['gen']['record'] for l in s['legs']) for s in slips)          # one line per slip, a record per leg
    assert not [l for s in slips for l in s['legs'] if l['sport'] in ('nba', 'nfl')]           # research estimates stay out unless asked
    assert all(len({l['player_key'] for l in s['legs']}) == 2 for s in slips)
    assert p.locator('.h-generated-card .h-batch-why').count() == 8
    assert all(len({(l['sport'], l['event_id']) for l in s['legs']}) == 2 for s in slips)
    assert p.locator('.h-generated-card').count() == 8
    p.locator('[data-hub=use-generated]').first.click()
    saved_state(p, lambda d: len((d.get('slip') or {}).get('legs', [])) == 2)
    before = saved_state(p, lambda d: bool(d.get('slip')))['slip']
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    p.locator('#hub-generate').click()
    p.wait_for_function("!document.querySelector('#hub-generate').disabled", timeout=30000)
    assert saved_state(p, lambda d: bool(d.get('studio')))['slip'] == before
    c = context(browser, env)
    try:
        q = open_page(c, env, '/#slips?tab=build')
        q.wait_for_selector('.h-generated-card', timeout=20000)
        assert q.locator('.h-generated-card').count() == 8
        assert q.locator('#hub-studio-body .u-leg').count() == 2
    finally:
        c.close()
    assert not p.errors


def test_generator_refuses_server_failure_without_overwriting_draft(ctx, env):
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    p.route('**/api/upgrade/recheck', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"busy"}'))
    p.locator('#hub-generate').click()
    p.wait_for_function("document.querySelector('#hub-generation-status').textContent.includes('draft was kept')", timeout=20000)
    assert p.locator('#hub-studio-body .u-leg').count() == 0
    assert not p.errors


@pytest.mark.parametrize('width', [360, 390, 768, 1024, 1440, 1920])
def test_studio_controls_and_entry_contents_fit_their_boxes(ctx, env, width):
    p = hub(ctx, env, {'width':width,'height':900})
    p.screenshot(path=f'.fit-board-{width}.png')
    p.goto(env.url + '/#slips?tab=build')
    p.wait_for_selector('#hub-generate')
    p.locator('#hub-generate').click()
    p.wait_for_function("!document.querySelector('#hub-generate').disabled",timeout=30000)
    assert 'slips generated and rechecked' in p.locator('#hub-generation-status').inner_text()
    p.locator('[data-hub=use-generated]').first.click()
    p.wait_for_selector('#hub-studio-body .u-leg')
    failures = p.evaluate('''() => [...document.querySelectorAll('#hub-studio-section .h-generator-controls, #hub-studio-section .u-entry-h, #hub-studio-section .u-leg, #hub-studio-section .u-slip-metrics')].flatMap(parent => {
      const b=parent.getBoundingClientRect();
      return [...parent.children].filter(el=>{const r=el.getBoundingClientRect();return r.width>0&&(r.left<b.left-1||r.right>b.right+1)}).map(el=>el.className||el.tagName);
    })''')
    assert failures == [], failures
    no_h_scroll(p)
    p.screenshot(path=f'.fit-studio-{width}.png')
    assert not p.errors


def test_inline_prop_displays_available_player_stats(ctx, env):
    p = hub(ctx, env)
    p.locator('#hub-sports [data-id=nba]').click()
    p.locator('#hub-q').fill('Karl Anders')
    p.locator('#hub-rows .h-player').first.click()
    p.locator('#hub-detail [role=tab][data-id=stats]').click()
    p.wait_for_selector('.h-stat-library dl',timeout=15000)
    facts = p.locator('.h-stat-library').inner_text()
    assert 'Points, L5' in facts and '21.4' in facts
    assert 'Points allowed per game to C' in facts and '24.1' in facts
    assert 'Days since last game' in facts

@pytest.mark.parametrize('size', [DESK, PHONE])
def test_board_rows_show_real_counts_placeholders_chips_and_compare(ctx, env, size):
    """L5/L20 use the newest results (n below the window shows its real count), wait quietly for the shared pass,
    letters are chips, the compare column summarises the real alternatives, the card groups its stats."""
    first = [True]

    def hits(route):
        response = route.fetch()
        d = response.json()
        rows = d['rows']
        if first[0]:
            first[0] = False
            d = {'rows': {}, 'complete': False}           # the shared pass has not reached any row yet
        else:
            for k, v in rows.items():
                v['l20'] = {'over': 7, 'under': 2, 'push': 0, 'n': 9}
            d['complete'] = True
        route.fulfill(response=response, json=d)
    p = ctx.new_page()
    p.errors = []
    p.on('pageerror', lambda e: p.errors.append(str(e)))
    p.set_viewport_size(size)
    p.route('**/api/upgrade/hits', hits)
    p.goto(env.url + '/#all-sports')
    p.wait_for_selector('#hub-rows tr[data-record]', timeout=30000)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-q').fill('vargo')
    row = p.locator('#hub-rows tr[data-record]').first
    p.wait_for_selector('#hub-rows .h-s-l5 .h-hit-wait')
    assert row.locator('.h-s-l5').inner_text().strip() == ''          # a placeholder, never a dash
    # ONE hit strip: the rate on the cell, the exact counts in its tooltip
    l10 = row.locator('.h-s-l10 .h-hit')
    assert l10.inner_text() == '60%' and l10.get_attribute('title').startswith('6 Over of the last 10 at this line')
    p.wait_for_function("!document.querySelector('#hub-rows .h-hit-wait')", timeout=20000)
    l20 = row.locator('.h-s-l20 .h-hit')
    assert l20.text_content() == '78%' and '7 Over of the last 9' in l20.get_attribute('title') and '(9 games on file)' in l20.get_attribute('title')
    assert 'h-few' in l20.get_attribute('class')
    assert '—' not in row.locator('.h-s-l5').inner_text()
    assert row.locator('.h-c-scout .h-grade.h-grade-b').inner_text() == 'B'                 # side, chance and letter in one Scout cell
    assert 'Over' in row.locator('.h-c-scout').inner_text()
    assert row.locator('.h-chevron').count() == 0
    assert row.locator('.h-prop-toggle').inner_text() == '+3'
    compare = row.locator('.h-compare')
    assert '3 lines' in compare.inner_text() and '34.5–42.5' in compare.inner_text()
    assert 'PrizePicks 34.5' in compare.get_attribute('title')
    p.locator('#hub-sports [data-id=nba]').click()
    p.locator('#hub-q').fill('Karl Anders')
    nba = p.locator('#hub-rows tr[data-record]').first
    assert nba.locator('.h-grade').count() == 0 and 'Research estimate' in nba.locator('.h-c-scout').inner_text()
    nba.locator('.h-player').click()
    p.locator('#hub-detail [role=tab][data-id=stats]').click()
    p.wait_for_selector('#hub-detail .h-stat-group h5', timeout=15000)
    assert p.locator('#hub-detail .h-full-research a').get_attribute('href').startswith('/sports?view=classic#/player/')
    heads = p.locator('#hub-detail .h-stat-group h5').all_inner_texts()
    assert {'MATCHUP', 'AVERAGES', 'MODEL INPUTS'} <= {h.upper() for h in heads}, heads
    no_h_scroll(p)
    assert not p.errors


def test_underdog_generation_uses_one_sport_with_enough_games(ctx, env):
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    p.locator('#hub-generation-book').select_option('underdog')
    p.locator('#hub-generate').click()
    p.wait_for_function("!document.querySelector('#hub-generate').disabled", timeout=30000)
    assert 'slips generated and rechecked' in p.locator('#hub-generation-status').inner_text()
    state = saved_state(p, lambda d: bool((d.get('studio') or {}).get('slips')))
    legs = state['studio']['slips'][0]['legs']
    assert len({l['sport'] for l in legs}) == 1
    assert len({l['event_id'] for l in legs}) == 2
    assert all(l['book'] == 'underdog' for l in legs)

def test_batch_mixed_sports_and_stale_candidate_refusal(ctx, env):
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    p.locator('#hub-generation-slips').select_option('4')
    p.locator('#hub-generation-mix').select_option('mixed')
    p.locator('#hub-generate').click()
    p.wait_for_function("!document.querySelector('#hub-generate').disabled", timeout=30000)
    state = saved_state(p, lambda d: bool(d.get('studio')))
    assert len(state['studio']['slips']) == 4
    assert all(len({l['sport'] for l in s['legs']}) >= 2 for s in state['studio']['slips'])
    # A line may move after generation; opening must recheck and preserve the draft on failure.
    p.route('**/api/upgrade/recheck', lambda r: r.fulfill(status=200, content_type='application/json', body=json.dumps({'legs':{},'entries':{}})))
    p.locator('[data-hub=use-generated]').first.click()
    p.wait_for_function("document.querySelector('#hub-batch-message-0').textContent.length > 0")
    assert p.locator('#hub-studio-body .u-leg').count() == 0
    assert not p.errors


def test_batch_shortfall_is_explicit_and_controls_match_platform(ctx, env):
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    for sport in ('lol', 'nba', 'nfl'):
        p.locator(f'#hub-generation-sports input[value={sport}]').uncheck()
    p.locator('#hub-generation-count').select_option('6')
    p.locator('#hub-generate').click()
    p.wait_for_function("!document.querySelector('#hub-generate').disabled", timeout=30000)
    assert 'eligible legs on PrizePicks' in p.locator('#hub-generation-status').inner_text()   # the fixture's CS2 legs cannot make a 6-pick slip
    assert p.locator('.h-generated-card').count() == 0
    assert p.locator('#hub-generation-games option[value="stacks"]').get_attribute('disabled') is not None   # PrizePicks: one leg per match
    p.locator('#hub-generation-count').select_option('2')
    p.locator('#hub-generation-book').select_option('underdog')
    assert p.locator('#hub-generation-games option[value="stacks"]').get_attribute('disabled') is None
    assert p.locator('#hub-generation-mix option[value="mixed"]').get_attribute('disabled') is not None
    p.locator('#hub-generation-type').select_option('flex')
    assert p.locator('#hub-generation-count').input_value() == '3'
    assert p.locator('#hub-generation-count option[value="2"]').get_attribute('disabled') is not None
    p.locator('#hub-generation-count').select_option('6')
    p.locator('#hub-generation-book').select_option('prizepicks')
    assert p.locator('#hub-generation-count').input_value() == '6'
    assert p.locator('#hub-generation-count option').all_text_contents() == ['2 picks', '3 picks', '4 picks', '5 picks', '6 picks']
    assert not p.errors

@pytest.mark.parametrize('size', [2,3,4,5,6])
def test_eight_lineups_each_have_requested_number_of_picks(ctx, env, size):
    from tests.workspace_fixture import row
    start = env.rows[0]['start']
    env.rows[:] = [row(f'pp:{9000+i}', 'prizepicks', f'BatchPlayer{i}', f'Team{i}', f'Other{i}', 20.5, 'More', .60+i/10000, 2000+i, start) for i in range(16)]
    env.seed()
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    for sport in ('lol','nba','nfl'):
        p.locator(f'#hub-generation-sports input[value={sport}]').uncheck()
    p.locator('#hub-generation-count').select_option(str(size))
    p.locator('#hub-generate').click()
    p.wait_for_function("!document.querySelector('#hub-generate').disabled", timeout=30000)
    assert 'slips generated' in p.locator('#hub-generation-status').inner_text(), p.locator('#hub-generation-status').inner_text()
    state = saved_state(p, lambda d: bool(d.get('studio')))
    slips = state['studio']['slips']
    assert len(slips) == 8, p.locator('#hub-generation-status').inner_text()
    assert all(len(s['legs']) == size for s in slips)
    assert len({tuple(sorted(l['id'] for l in s['legs'])) for s in slips}) == 8
    assert not p.errors


@pytest.mark.parametrize('size,available', [(2,16),(6,48),(2,6)])
def test_no_repeat_props_across_generated_batch(ctx,env,size,available):
    from tests.workspace_fixture import row
    start=env.rows[0]['start']
    env.rows[:]=[row(f'pp:{9800+i}', 'prizepicks', f'UniquePlayer{i}', f'Team{i}', f'Other{i}',20.5,'More',.60+i/10000,3000+i,start) for i in range(available)]
    # Another offer/line for the same prop must not evade the batch restriction.
    env.rows.append(row('pp:9999','prizepicks','UniquePlayer0','Team0','Other0',21.5,'More',.60,3000,start))
    env.seed()
    p=hub(ctx,env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click()
    for sport in ('lol','nba','nfl'):
        p.locator(f'#hub-generation-sports input[value={sport}]').uncheck()
    p.locator('#hub-generation-count').select_option(str(size))
    p.locator('#hub-generation-repeats').select_option('none')
    p.locator('#hub-generate').click()
    p.wait_for_function("!document.querySelector('#hub-generate').disabled",timeout=30000)
    state=saved_state(p,lambda d:bool(d.get('studio')))
    batch=state['studio']; legs=[l for s in batch['slips'] for l in s['legs']]
    identities=[(l['sport'],l['event_id'],l['player_key'],l['stat'],l['period']) for l in legs]
    assert len(identities)==len(set(identities))
    assert len(batch['slips'])==min(8,available//size)
    assert all(len(s['legs'])==size for s in batch['slips'])
    assert batch['options']['noRepeatProps'] is True
    if available<size*8:
        assert 'No props were repeated' in p.locator('#hub-generation-status').inner_text()
    p.reload();p.locator('[role=tab][data-hub=studio-tab][data-id=build]').click();p.wait_for_selector('.h-generated-card')
    assert 'No repeat props' in p.locator('#hub-generated').inner_text()
    assert p.locator('#hub-generation-repeats').input_value()=='none'
    no_h_scroll(p)
    assert not p.errors


@pytest.mark.parametrize('path', ['/', '/lol', '/sports#/board'])
def test_workspace_boot_hides_classic_while_account_loads(ctx, env, monkeypatch, path):
    monkeypatch.setenv('SCOUT_WORKSPACE_V2', '1')
    p = ctx.new_page()
    pending = []
    p.route('**/api/upgrade/me', lambda route: pending.append(route))
    p.goto(env.url + path)
    p.wait_for_function("document.documentElement.classList.contains('scout-boot')")
    assert p.locator('body').evaluate("el=>getComputedStyle(el).visibility") == 'hidden'
    assert pending
    pending[0].continue_()
    p.wait_for_selector('#u-hub:not([hidden])')
    p.wait_for_function("!document.documentElement.classList.contains('scout-boot')")
    assert p.locator('body').evaluate("el=>getComputedStyle(el).visibility") == 'visible'
    no_h_scroll(p)


@pytest.mark.parametrize('path', ['/?view=classic#board', '/lol?view=classic', '/sports?view=classic#/board'])
@pytest.mark.parametrize('size', [DESK, PHONE])
def test_classic_keeps_tools_and_does_not_reopen_workspace(ctx, env, path, size):
    p = open_page(ctx, env, path, size)
    p.wait_for_selector('#u-tray-btn')
    assert p.locator('#u-hub').is_hidden()
    if path.startswith('/?'):
        p.wait_for_selector('#boardout .u-decorated .u-hits', timeout=20000)
        assert p.locator('#boardout .u-decorated').first.evaluate('(el)=>el.scrollWidth<=el.clientWidth+2')
    assert not p.locator('html').evaluate("el=>el.classList.contains('scout-boot')")
    assert p.locator('body').evaluate("el=>getComputedStyle(el).visibility") == 'visible'
    assert p.locator('[data-hub-inert]').count() == 0
    no_h_scroll(p)


def test_failed_workspace_script_restores_classic(ctx, env):
    p = ctx.new_page()
    p.route('**/upgrade/upgrade.js', lambda r: r.abort())
    p.goto(env.url + '/')
    p.wait_for_function("!document.documentElement.classList.contains('scout-boot')", timeout=12000)
    assert p.locator('body').evaluate("el=>getComputedStyle(el).visibility") == 'visible'


def test_adder_warns_at_once_refuses_a_second_leg_for_one_player_and_swaps_in_place(ctx, env):
    """sq-1 adder: a same-match note on the slip at once, a refusal naming the leg already there, and the server's best replacement
    applied only on Swap in, at the same position."""
    from tests.workspace_fixture import row
    start = env.rows[0]['start']
    extra = [row('pp:7400', 'prizepicks', 'vargo', 'Halcyon', 'Norrland', 15.5, 'More', .62, 900, start, prop='hs'),
             row('pp:7401', 'prizepicks', 'zenith', 'Orbit', 'Comet', 25.5, 'More', .72, 902, start)]
    env.rows.extend(extra)
    env.seed(extra)
    p = hub(ctx, env)
    book(p, 'prizepicks')
    p.locator('#hub-sports [data-id=cs2]').click()
    def add(name, n=0):
        p.locator('#hub-q').fill(name)
        p.wait_for_function("(n)=>document.querySelectorAll('#hub-rows .h-add:not([disabled]):not(.is-on)').length>n", arg=n)
        p.locator('#hub-rows .h-add:not([disabled]):not(.is-on)').nth(n).click()
    add('brandr')
    add('vargo')
    p.wait_for_function("document.querySelector('#hub-slip-count').textContent === '2'")
    assert 'Same match as' in p.locator('#u-toast').inner_text()
    p.locator('.h-sidebar [data-id=studio]').click()
    p.wait_for_selector('#hub-studio-body .u-leg-notes')
    assert 'Same match as' in p.locator('#hub-studio-body .u-leg-notes').first.inner_text()
    p.locator('.h-sidebar [data-id=board]').first.click()
    p.locator('#hub-q').fill('vargo')
    p.wait_for_function("document.querySelectorAll('#hub-rows .h-add').length>=1")
    if p.locator('#hub-rows .h-prop-toggle[aria-expanded=false]').count():
        p.locator('#hub-rows .h-prop-toggle').first.click()                    # the player's other props open under his row
    other = p.locator('#hub-rows tr[data-record]').filter(has_text='Headshots').locator('.h-add:not(.is-on)')
    assert other.count() == 1
    other.first.click()
    assert 'One leg per player' in p.locator('#u-toast').inner_text()
    assert p.locator('#hub-slip-count').inner_text() == '2'
    p.locator('.h-sidebar [data-id=studio]').click()
    names = "[...document.querySelectorAll('#hub-studio-body .u-leg-name')].map(e=>e.firstChild.textContent)"
    before = p.evaluate(names)
    p.wait_for_selector('#hub-studio-body [data-u=replace]', timeout=15000)
    assert 'zenith' in p.locator('#hub-studio-body .u-leg-advice').filter(has=p.locator('[data-u=replace]')).first.inner_text()
    p.locator('#hub-studio-body [data-u=replace]').first.click()
    p.wait_for_function("[...document.querySelectorAll('#hub-studio-body .u-leg-name')].some(e=>e.textContent.startsWith('zenith'))")
    after = p.evaluate(names)
    assert len(after) == len(before) == 2 and 'zenith' in after
    kept = [n for n in before if n in after]
    assert len(kept) == 1 and after.index(kept[0]) == before.index(kept[0])        # the other leg keeps its place
    assert not p.errors


# ------------------------------------------------------------------------------------------------ goblin rule (owner 2026-11-05)

def _studio_build(p):
    p.goto(p.url.split('#')[0] + '#slips?tab=build')
    p.wait_for_selector('#hub-generation-lines')


@pytest.mark.parametrize('size', [DESK, PHONE])
def test_goblin_rule_defaults_to_no_goblins_is_saved_and_obeyed(ctx, env, browser, size):
    """Slip Studio's Goblins rule: No goblins by default, explained in one plain line, saved to the account (a reload and a second
    browser keep it), and every generated slip and every adder suggestion follows it."""
    p = hub(ctx, env, size)
    _studio_build(p)
    sel = p.locator('#hub-generation-lines')
    assert sel.input_value() == 'no_goblins'
    assert sel.locator('option').all_text_contents() == ['No goblins', 'At most 1 goblin per slip', 'No goblins or demons', 'Any lines']
    assert p.locator('#hub-generation-lines-help').inner_text() == 'Goblins are easier lines that pay less. Scout leaves them out unless you allow them.'
    assert 'Goblins (PrizePicks lines set below the standard line' in p.locator('.h-generator details').text_content()
    if SHOTS_DIR:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        p.locator('.h-generator').screenshot(path=str(SHOTS_DIR / f"studio_goblin_rule_{size['width']}.png"))
    with p.expect_request(lambda r: '/api/upgrade/generate?' in r.url) as req:
        p.locator('#hub-generate').click()
    assert 'lines=no_goblins' in req.value.url
    p.wait_for_function("document.querySelector('#hub-generation-status').textContent.includes('slips generated')", timeout=30000)
    state = saved_state(p, lambda d: (d.get('studio') or {}).get('options', {}).get('lines') == 'no_goblins')
    legs = [l for s in state['studio']['slips'] for l in s['legs']]
    assert legs and not [l for l in legs if (l.get('gen') or {}).get('line_type') == 'goblin']
    assert '· No goblins' in p.locator('.h-batch-head').inner_text()
    sel.select_option('one_goblin')
    saved_state(p, lambda d: ((d.get('prefs') or {}).get('researchHub') or {}).get('slipLines') == 'one_goblin')
    p.reload()
    p.wait_for_selector('#hub-generation-lines')
    p.wait_for_function("document.getElementById('hub-generation-lines').value==='one_goblin'", timeout=15000)
    c = context(browser, env, size)
    try:
        q = open_page(c, env, '/#slips?tab=build')
        q.wait_for_function("document.getElementById('hub-generation-lines')?.value==='one_goblin'", timeout=20000)
    finally:
        c.close()
    p.locator('#hub-generation-lines').select_option('no_goblins')
    saved_state(p, lambda d: ((d.get('prefs') or {}).get('researchHub') or {}).get('slipLines') == 'no_goblins')
    no_h_scroll(p)
    assert not p.errors


@pytest.mark.parametrize('old,shown', [('all', 'no_goblins'), ('standard', 'standard')])
def test_a_batch_saved_before_the_goblin_rule_does_not_turn_goblins_back_on(ctx, env, old, shown):
    """Review 2026-11-05: members who generated before the Goblins control have studio.options.lines == 'all' saved (the old Lines
    select's first option and default) and no prefs slipLines. That is not a choice to allow goblins: the control shows No goblins
    and Generate asks for lines=no_goblins. The old 'Standard lines only' was a deliberate opt-out and carries over."""
    p = hub(ctx, env)
    p.evaluate("""async (old) => {
      const s=await (await fetch('/api/upgrade/state')).json();
      const r=await fetch('/api/upgrade/state',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({rev:s.rev,studio:{version:1,created_at:new Date().toISOString(),options:{lines:old,noRepeatProps:false},slips:[]}})});
      if(!r.ok)throw Error('Fixture studio write failed');
    }""", old)
    st = saved_state(p, lambda d: ((d.get('studio') or {}).get('options') or {}).get('lines') == old)
    assert not ((st.get('prefs') or {}).get('researchHub') or {}).get('slipLines')
    with p.expect_response(lambda r: r.url.endswith('/api/upgrade/state') and r.request.method == 'GET', timeout=30000):
        p.reload()
    _studio_build(p)
    p.wait_for_timeout(800)                                   # the saved state has been read and applied to the control
    p.wait_for_function("document.getElementById('hub-generation-lines').value===%r" % shown, timeout=15000)
    with p.expect_request(lambda r: '/api/upgrade/generate?' in r.url) as req:
        p.locator('#hub-generate').click()
    assert f'lines={shown}' in req.value.url
    assert not p.errors


def test_adder_asks_with_the_goblin_rule(ctx, env):
    p = hub(ctx, env)
    book(p, 'prizepicks')
    with p.expect_request(lambda r: r.url.endswith('/api/upgrade/slip-advice'), timeout=20000) as req:
        add_first(p)
    assert req.value.post_data_json['lines'] == 'no_goblins'
    assert not p.errors
