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
    p.locator('#hub-rows [data-hub=research]').first.click()
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
        q.locator('#hub-slip-content [data-u=check]').click()
        q.wait_for_selector('#hub-slip-content .u-status b:has-text("ready")', timeout=20000)
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
