"""Real Handler + isolated four-sport fixtures. No production account writes."""
import json
import time
import pytest
from tests.test_upgrade_browser import browser, env, ctx, context, open_page, no_h_scroll, DESK, PHONE


def saved_state(page, predicate):
    deadline = time.monotonic() + 15
    last = None
    while time.monotonic() < deadline:
        last = page.evaluate("fetch('/api/upgrade/state').then(r=>r.json())")
        if predicate(last):
            return last
        page.wait_for_timeout(200)
    pytest.fail('Account state did not persist: ' + repr(last))


def hub(ctx, env, size=None):
    p = open_page(ctx, env, '/#all-sports', size)
    p.wait_for_selector('#hub-rows tr', timeout=30000)
    p.wait_for_function("document.querySelector('#hub-sports').textContent.includes('NFL')")
    return p


def test_all_four_real_boards_filters_research_and_original_navigation(ctx, env):
    p = hub(ctx, env)
    p.wait_for_function("document.querySelectorAll('#hub-rows tr[data-record^=nba]').length > 0")
    assert all(p.locator('#hub-rows tr[data-record^='+s+']').count() for s in ('cs2','lol','nba','nfl'))
    assert 'SAMPLE' not in p.locator('#u-hub').inner_text()
    p.locator('#hub-q').fill('vargo')
    assert p.locator('#hub-rows tr').count() >= 2
    assert 'orlo' not in p.locator('#hub-rows').inner_text()
    p.locator('#hub-rows .h-player').first.click()
    p.wait_for_selector('#hub-detail .h-history-chart svg', timeout=15000)
    p.locator('#hub-detail [data-hub=research]').click()
    p.wait_for_selector('#u-drawer .u-chart svg', timeout=15000)
    assert 'past results at this line' in p.locator('#u-drawer').inner_text().lower()
    p.locator('[data-u=pclose]').click()
    p.locator('.h-classic').click()
    assert p.locator('#u-hub').is_hidden()
    assert not p.locator('#board').get_attribute('inert')
    assert not p.errors


def test_four_sport_prizepicks_slip_syncs_to_second_browser_context(ctx, env, browser):
    p = hub(ctx, env)
    p.locator('#hub-book').select_option('prizepicks')
    for i, sport in enumerate(('cs2','lol','nba','nfl')):
        p.locator('#hub-sports [data-id='+sport+']').click()
        assert p.locator('#hub-rows tr').first.get_attribute('data-record').startswith(sport+'|'), p.locator('#hub-sports').inner_text()
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
        if p.locator('#u-drawer').is_visible():
            # A research-only offer can have no Scout side. The member must explicitly choose.
            p.locator('#u-drawer [data-u=pside][data-id=over]').click()
            p.locator('#u-drawer [data-u=padd]').click()
            p.locator('#u-drawer [data-u=pclose]').click()
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
    p.locator('[data-hub=save-view]').click()
    p.locator('#hub-save-name').fill('My CS2 research')
    p.locator('#hub-save-confirm').click()
    saved_state(p, lambda d: len((d.get('prefs') or {}).get('researchHub', {}).get('views', [])) == 1)
    p.locator('[data-hub=save-alert]').click()
    p.locator('#hub-save-name').fill('Vargo line updates')
    p.locator('#hub-save-confirm').click()
    p.locator('#hub-rows [data-hub=watch]').first.click()
    saved_state(p, lambda d: len((d.get('prefs') or {}).get('researchHub', {}).get('alerts', [])) == 1 and len((d.get('prefs') or {}).get('researchHub', {}).get('watch', [])) == 1)
    p.reload()
    p.wait_for_selector('#hub-rows tr')
    p.locator('#hub-views').select_option('0')
    assert p.locator('#hub-q').input_value()=='vargo'
    p.locator('.h-sidebar [data-id=alerts]').click()
    assert 'Vargo line updates' in p.locator('#hub-alerts-body').inner_text()
    assert 'No email or push' in p.locator('#hub-alerts-body').inner_text()
    assert not p.errors


@pytest.mark.parametrize('size',[DESK, PHONE])
def test_responsive_record_loading_keyboard_and_no_overflow(ctx, env, size):
    p=hub(ctx, env, size)
    no_h_scroll(p)
    p.locator(('.h-nav-mobile' if size==PHONE else '.h-sidebar')+' [data-id=record]').click()
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


def test_quote_refresh_failure_is_explicit_and_cannot_add(ctx, env):
    p=hub(ctx, env)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.route('**/api/upgrade/board',lambda r:r.fulfill(status=503,content_type='application/json',body='{"error":"board_unavailable"}'))
    p.locator('.h-tools [data-hub=refresh]').click()
    p.wait_for_selector('#hub-health .h-warning')
    assert 'could not refresh' in p.locator('#hub-health').inner_text()
    assert p.locator('#hub-rows .h-add:not([disabled])').count()==0
    assert not p.errors


def test_unoffered_scout_side_opens_side_choice_instead_of_dead_add(ctx, env):
    p = hub(ctx, env)

    def one_sided(route):
        response = route.fetch()
        d = response.json()
        for record in d['records']:
            side = record['scout'].get('side')
            if side and record['offer']['platform'] == 'prizepicks':
                record['offer'][side]['offered'] = False
        route.fulfill(response=response, json=d)

    p.route('**/api/upgrade/board', one_sided)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-book').select_option('prizepicks')
    p.locator('.h-tools [data-hub=refresh]').click()
    p.wait_for_selector('#hub-rows .h-add[aria-label^="Choose a side"]', timeout=10000)
    p.locator('#hub-rows .h-add').first.click()
    p.wait_for_selector('#u-drawer[open]', timeout=10000)
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
    p.locator('#hub-views').select_option('0')
    assert p.locator('#hub-q').input_value()==''
    assert p.locator('#hub-rows tr').count()>0
    assert not p.errors


def test_successful_response_with_stale_source_is_not_presented_as_current(ctx, env):
    def stale(route):
        response=route.fetch()
        d=response.json()
        for f in d['feeds'].values():
            f['state']='stale'
        route.fulfill(response=response,json=d)
    p=hub(ctx, env)
    p.route('**/api/upgrade/board',stale)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('.h-tools [data-hub=refresh]').click()
    p.wait_for_selector('#hub-health details.h-warning')
    assert p.locator('#hub-rows .h-add:not([disabled])').count()==0


def test_phone_keeps_filters_available_without_covering_first_prop(ctx, env):
    p=hub(ctx, env, PHONE)
    assert not p.locator('#hub-book').is_visible()
    assert p.locator('#hub-rows tr').first.bounding_box()['y'] < 600
    p.locator('#hub-filters-toggle').click()
    p.locator('#hub-book').select_option('prizepicks')
    p.locator('#hub-filters-toggle').click()
    assert all(x.startswith(('cs2|prizepicks|','lol|prizepicks|','nba|prizepicks|','nfl|prizepicks|')) for x in p.locator('#hub-rows tr').evaluate_all('(rows)=>rows.map(r=>r.dataset.record)'))
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
    p.locator('#hub-detail [data-hub=detail-line]').filter(has_text='34.5').click()
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
    p.locator('#hub-games [data-hub=game]').filter(has_text='Kestrel').click()
    assert 'vargo' not in p.locator('#hub-rows').inner_text()
    p.locator('#hub-stat').select_option(label='Headshots')
    p.locator('#hub-sports [data-id=nba]').click()
    assert p.locator('#hub-stat').input_value()=='all'
    assert p.locator('#hub-games [data-id=all]').get_attribute('aria-pressed')=='true'
    assert p.locator('#hub-rows tr[data-record]').count()>0
    assert not p.errors


def test_tracked_picks_have_a_page_with_sport_status_filters_and_receipts(ctx,env):
    p=hub(ctx,env)
    p.locator('#hub-sports [data-id=cs2]').click()
    p.locator('#hub-book').select_option('underdog')
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


def test_phone_keeps_specialist_tools_in_a_real_menu(ctx,env):
    p=hub(ctx,env,PHONE)
    p.locator('.h-tools-menu summary').click()
    assert p.locator('.h-tools-menu a').filter(has_text='Moneylines').is_visible()
    assert p.locator('.h-tools-menu a').filter(has_text='Single prop grader').is_visible()
    no_h_scroll(p)


@pytest.mark.parametrize('path',['/#slips','/sports#/slips?lg=nfl','/lol#scout/studio'])
def test_slip_studio_uses_the_shared_account_draft_on_every_sport_route(ctx,env,path):
    p=hub(ctx,env)
    p.locator('#hub-book').select_option('prizepicks')
    for sport in ('cs2','lol','nba','nfl'):
        p.locator('#hub-sports [data-id='+sport+']').click()
        p.locator('#hub-rows .h-add:not([disabled])').first.click()
        if p.locator('#u-drawer').is_visible():
            p.locator('#u-drawer [data-u=pside][data-id=over]').click()
            p.locator('#u-drawer [data-u=padd]').click()
            p.locator('#u-drawer [data-u=pclose]').click()
    saved_state(p,lambda d:len((d.get('slip')or{}).get('legs',[]))==4)
    p.goto(env.url+path)
    p.wait_for_selector('#hub-studio-body .u-entry',timeout=20000)
    assert p.locator('#hub-title').inner_text()=='Slip Studio'
    assert p.locator('#hub-studio-body .u-leg').count()==4
    p.locator('#hub-studio-body [data-u=check]').click()
    p.wait_for_selector('#hub-studio-body .u-status b:has-text("ready")',timeout=20000)
    assert not p.errors


def test_workspace_navigation_back_reload_and_platform_bookmarks(ctx,env):
    p=open_page(ctx,env,'/#board/prizepicks')
    p.wait_for_selector('#hub-rows tr[data-record]')
    assert p.locator('#hub-book').input_value()=='prizepicks'
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

def test_generator_checks_and_saves_current_mixed_sport_picks(ctx, env, browser):
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.locator('#hub-generate').click()
    p.wait_for_function("document.querySelector('#hub-generation-status').textContent.length > 0", timeout=30000)
    message = p.locator('#hub-generation-status').inner_text()
    assert 'generated and rechecked' in message, message
    state = saved_state(p, lambda d: len((d.get('slip') or {}).get('legs', [])) == 2)
    legs = state['slip']['legs']
    assert len({(l['sport'], l['event_id']) for l in legs}) == 2
    assert all(l['book'] == 'prizepicks' and l['read_rev'] for l in legs)
    assert p.locator('#hub-studio-body .u-leg').count() == 2
    # Repeat at the same total must preserve the existing draft, not duplicate/replace it.
    p.locator('#hub-generate').click()
    p.wait_for_function("document.querySelector('#hub-generation-status').textContent.includes('already has')")
    assert saved_state(p, lambda d: bool(d.get('slip')))['slip']['legs'] == legs
    c = context(browser, env)
    try:
        q = open_page(c, env, '/#slips')
        q.wait_for_selector('#hub-studio-body .u-leg', timeout=20000)
        assert q.locator('#hub-studio-body .u-leg').count() == 2
    finally:
        c.close()
    assert not p.errors


def test_generator_refuses_server_failure_without_overwriting_draft(ctx, env):
    p = hub(ctx, env)
    p.locator('.h-sidebar [data-id=studio]').click()
    p.route('**/api/upgrade/recheck', lambda route: route.fulfill(status=503, content_type='application/json', body='{"error":"busy"}'))
    p.locator('#hub-generate').click()
    p.wait_for_function("document.querySelector('#hub-generation-status').textContent.includes('draft was kept')", timeout=20000)
    assert p.locator('#hub-studio-body .u-leg').count() == 0
    assert not p.errors


@pytest.mark.parametrize('width', [360, 390, 768, 1024, 1440, 1920])
def test_studio_controls_and_entry_contents_fit_their_boxes(ctx, env, width):
    p = hub(ctx, env, {'width':width,'height':900})
    p.screenshot(path=f'.fit-board-{width}.png')
    p.goto(env.url + '/#slips')
    p.wait_for_selector('#hub-generate')
    p.locator('#hub-generate').click()
    p.wait_for_function("document.querySelector('#hub-generation-status').textContent.length > 0",timeout=30000)
    assert 'generated and rechecked' in p.locator('#hub-generation-status').inner_text()
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
    p.wait_for_selector('.h-stat-library dl',timeout=15000)
    facts = p.locator('.h-stat-library').inner_text()
    assert 'Points, L5' in facts and '21.4' in facts
    assert 'Points allowed per game to C' in facts and '24.1' in facts
    assert 'Days since last game' in facts
